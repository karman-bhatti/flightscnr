"""
Tomorrow.io weather API wrapper with built-in rate limiting.

Free tier: 25 requests/hour (one every ~2.4 minutes).
We enforce a minimum 3-minute gap between ALL API calls to stay safe.
"""
from datetime import date, datetime, timedelta
import time
import logging
import socket
import threading

from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

# Attempt to load config data
try:
    from config import TOMORROW_API_KEY
    from config import FORECAST_DAYS
    from config import TEMPERATURE_LOCATION as _BOOT_TEMPERATURE_LOCATION
except (ModuleNotFoundError, NameError, ImportError):
    TOMORROW_API_KEY = None
    FORECAST_DAYS = 3
    _BOOT_TEMPERATURE_LOCATION = ""

logger = logging.getLogger(__name__)


def _temperature_location() -> str:
    """Live radar-center weather query point (do not freeze boot-time import)."""
    try:
        import config

        loc = (getattr(config, "TEMPERATURE_LOCATION", None) or "").strip()
        if loc:
            return loc
        home = getattr(config, "LOCATION_HOME", None)
        if home and len(home) >= 2:
            return f"{home[0]},{home[1]}"
    except Exception:
        pass
    return (_BOOT_TEMPERATURE_LOCATION or "").strip()


def _weather_enabled() -> bool:
    try:
        from secrets_store import api_enabled

        return api_enabled("TOMORROW_API_KEY")
    except Exception:
        return True


def _temperature_units() -> str:
    """Units requested from Tomorrow.io — always metric; convert on display."""
    return "metric"


def _convert_temperature(value, from_units: str):
    try:
        from weather_prefs import convert_temperature

        return convert_temperature(value, from_units)
    except ImportError:
        if from_units == "imperial":
            return value
        return value


def invalidate_caches() -> None:
    """Clear in-memory and file weather caches after portal unit change."""
    global _cached_temp, _cached_temp_ts, _cached_forecast, _cached_forecast_ts
    global _cached_temp_units, _cached_forecast_units, _cached_weather_code
    _cached_temp = None
    _cached_temp_ts = 0.0
    _cached_forecast = None
    _cached_forecast_ts = 0.0
    _cached_temp_units = None
    _cached_forecast_units = None
    _cached_weather_code = None
    for path in (_TEMP_CACHE_FILE, _FORECAST_CACHE_FILE):
        try:
            _os.remove(path)
        except OSError:
            pass


def reset_for_location_change() -> None:
    """Clear caches and allow an immediate fetch for a new radar center."""
    global _temp_last_call_ts, _fc_last_call_ts, _in_backoff
    invalidate_caches()
    with _rate_lock:
        _temp_last_call_ts = 0.0
        _fc_last_call_ts = 0.0
        _in_backoff = False

# ─── Rate Limiter ────────────────────────────────────────────────────────────
# Separate rate limiters for temperature and forecast so they don't block each other.
# Normal mode: 1 API call per 30 minutes per endpoint.
# Backoff mode (after 429): 1 call every 10 minutes until success.
# Backoff auto-clears after 2 hours regardless.
_NORMAL_INTERVAL_S = 1800   # 30 min between calls per endpoint
_BACKOFF_INTERVAL_S = 600   # 10 minutes when rate-limited
_BACKOFF_AUTO_CLEAR_S = 7200  # Auto-clear backoff after 2 hours

_temp_last_call_ts = 0.0
_fc_last_call_ts = 0.0
_in_backoff = False
_backoff_entered_ts = 0.0
_rate_lock = threading.Lock()


def _rate_limited(endpoint: str = "temp") -> bool:
    """Return True if we should skip this API call due to rate limiting."""
    global _in_backoff, _backoff_entered_ts
    with _rate_lock:
        # Auto-clear backoff after 2 hours
        if _in_backoff and (time.time() - _backoff_entered_ts) > _BACKOFF_AUTO_CLEAR_S:
            _in_backoff = False
            logger.info("Tomorrow.io: backoff auto-cleared after 2 hours")

        last_ts = _temp_last_call_ts if endpoint == "temp" else _fc_last_call_ts
        elapsed = time.time() - last_ts
        interval = _BACKOFF_INTERVAL_S if _in_backoff else _NORMAL_INTERVAL_S
        if elapsed < interval:
            return True
        return False


def _record_call(endpoint: str = "temp"):
    """Record that an API call was just made."""
    global _temp_last_call_ts, _fc_last_call_ts
    with _rate_lock:
        if endpoint == "temp":
            _temp_last_call_ts = time.time()
        else:
            _fc_last_call_ts = time.time()


def _enter_backoff():
    """Enter backoff mode after receiving 429."""
    global _in_backoff, _backoff_entered_ts
    with _rate_lock:
        _in_backoff = True
        _backoff_entered_ts = time.time()
    logger.warning("Tomorrow.io: entering backoff mode (retry every 10 min)")


def _exit_backoff():
    """Exit backoff mode after a successful response."""
    global _in_backoff
    with _rate_lock:
        if _in_backoff:
            _in_backoff = False
            logger.info("Tomorrow.io: backoff cleared, resuming normal interval")


# ─── DNS helper ──────────────────────────────────────────────────────────────

def is_dns_error(exc: Exception) -> bool:
    cause = exc
    while cause:
        if isinstance(cause, socket.gaierror):
            return True
        cause = cause.__cause__
    return False


# ─── HTTP Session (shared, with retries on server errors only) ───────────────
_session = None


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()

        retries = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=3,
            allowed_methods=["GET", "POST"],
            # Do NOT retry on 429 — that makes rate limiting worse
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=2,
            pool_maxsize=2,
        )

        _session.mount("https://", adapter)
        _session.mount("http://", adapter)

    return _session


# ─── API URL ─────────────────────────────────────────────────────────────────
TOMORROW_API_URL = "https://api.tomorrow.io/v4"


# ─── Persistent File Cache ───────────────────────────────────────────────────
# Survives reboots — prevents death-spiral when Tomorrow.io 429s on startup.
import os as _os
import json as _json

_CACHE_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), ".cache")
_os.makedirs(_CACHE_DIR, exist_ok=True)
_TEMP_CACHE_FILE = _os.path.join(_CACHE_DIR, "temperature.json")
_FORECAST_CACHE_FILE = _os.path.join(_CACHE_DIR, "forecast.json")


def _load_file_cache(path):
    """Load cached data from file. Returns (data, timestamp, units) or (None, 0, None)."""
    try:
        with open(path, "r") as f:
            obj = _json.load(f)
            return obj.get("data"), obj.get("ts", 0), obj.get("units")
    except (FileNotFoundError, _json.JSONDecodeError, KeyError):
        return None, 0, None


def _save_file_cache(path, data, units: str | None = None):
    """Save data + timestamp + units to file cache."""
    try:
        with open(path, "w") as f:
            _json.dump({"data": data, "ts": time.time(), "units": units}, f)
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot write cache {path}: {e}")


# ─── Temperature & Humidity ──────────────────────────────────────────────────
_cached_temp = None
_cached_temp_ts = 0.0
_cached_temp_units: str | None = None
_cached_weather_code = None
_TEMP_CACHE_TTL = 3600  # 1 hour

# Load persistent cache on startup
_startup_temp, _startup_temp_ts, _startup_temp_units = _load_file_cache(_TEMP_CACHE_FILE)
if _startup_temp and (time.time() - _startup_temp_ts) < _TEMP_CACHE_TTL * 2:
    if isinstance(_startup_temp, dict):
        _cached_temp = (_startup_temp.get("temperature"), _startup_temp.get("humidity"))
        _cached_weather_code = _startup_temp.get("weather_code")
    else:
        _cached_temp = tuple(_startup_temp) if isinstance(_startup_temp, list) else _startup_temp
    _cached_temp_ts = _startup_temp_ts
    _cached_temp_units = _startup_temp_units or "metric"
    logger.info(f"Loaded cached temperature from file: {_cached_temp}")


def current_weather_code():
    """Latest weather code from realtime (may be None)."""
    return _cached_weather_code


def _return_temperature():
    if not _cached_temp:
        return None, None
    temp, humidity = _cached_temp
    return _convert_temperature(temp, "metric"), humidity


def grab_temperature_and_humidity():
    """
    Fetch current temperature and humidity.
    Returns cached data if called within the cache TTL or rate limit window.
    """
    global _cached_temp, _cached_temp_ts, _cached_temp_units, _cached_weather_code

    if not _weather_enabled():
        logger.info("Tomorrow.io weather disabled in web portal settings")
        return None, None
    if not TOMORROW_API_KEY:
        logger.warning("TOMORROW_API_KEY not set — skipping temperature fetch")
        return None, None

    units = _temperature_units()

    # Return cache if still fresh (convert if display units changed)
    if _cached_temp and (time.time() - _cached_temp_ts) < _TEMP_CACHE_TTL:
        return _return_temperature()

    # Rate limit check
    if _rate_limited("temp"):
        logger.debug("Rate limit: skipping temperature API call, using cache")
        if _cached_temp:
            return _return_temperature()
        return None, None

    try:
        s = get_session()
        request = s.get(
            f"{TOMORROW_API_URL}/weather/realtime",
            params={
                "location": _temperature_location(),
                "units": _temperature_units(),
                "apikey": TOMORROW_API_KEY
            },
            timeout=(5, 20)
        )

        if request.status_code == 429:
            _record_call("temp")
            _enter_backoff()
            if _cached_temp:
                return _return_temperature()
            return None, None

        request.raise_for_status()
        _record_call("temp")
        _exit_backoff()

        data = request.json().get("data", {}).get("values", {})
        temperature = data.get("temperature")
        humidity = data.get("humidity")
        code = data.get("weatherCode")

        if temperature is None:
            logger.error("Incomplete data from Tomorrow.io API")
            if _cached_temp:
                return _return_temperature()
            return None, None

        try:
            _cached_weather_code = int(code) if code is not None else None
        except (TypeError, ValueError):
            _cached_weather_code = None
        _cached_temp = (temperature, humidity)
        _cached_temp_ts = time.time()
        _cached_temp_units = "metric"
        _save_file_cache(
            _TEMP_CACHE_FILE,
            {
                "temperature": temperature,
                "humidity": humidity,
                "weather_code": _cached_weather_code,
            },
            "metric",
        )
        return _convert_temperature(temperature, "metric"), humidity

    except (RequestException, ValueError) as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        # Don't clear backoff on network errors — let auto-clear (2hr) handle it
        if is_dns_error(e):
            logger.error(
                f"[{timestamp}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logger.error(
                f"[{timestamp}] Temperature request failed: {e}"
            )

        if _cached_temp:
            return _return_temperature()
        return None, None


# ─── Forecast ────────────────────────────────────────────────────────────────
_cached_forecast = None
_cached_forecast_ts = 0.0
_cached_forecast_units: str | None = None
_FORECAST_CACHE_TTL = 3600  # 1 hour


def _interval_local_date(start: str) -> date | None:
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone().date()
        return dt.date()
    except ValueError:
        return None


def _forecast_from_today(intervals: list) -> list:
    """Drop completed daily intervals so midnight rollover does not show yesterday."""
    if not intervals:
        return []
    today = datetime.now().date()
    out = []
    for item in intervals:
        day = _interval_local_date(item.get("startTime") or "")
        if day is not None and day < today:
            continue
        out.append(item)
    return out


def _convert_forecast_intervals(intervals: list, from_units: str) -> list:
    out = []
    for item in intervals:
        values = dict(item.get("values") or {})
        if "temperatureMin" in values:
            values["temperatureMin"] = _convert_temperature(values.get("temperatureMin"), from_units)
        if "temperatureMax" in values:
            values["temperatureMax"] = _convert_temperature(values.get("temperatureMax"), from_units)
        out.append({**item, "values": values})
    return out


def _return_forecast():
    if not _cached_forecast:
        return []
    filtered = _forecast_from_today(_cached_forecast)
    return _convert_forecast_intervals(filtered, "metric")


# Load persistent forecast cache on startup
_startup_fc, _startup_fc_ts, _startup_fc_units = _load_file_cache(_FORECAST_CACHE_FILE)
if _startup_fc and (time.time() - _startup_fc_ts) < _FORECAST_CACHE_TTL * 2:
    _cached_forecast = _startup_fc
    _cached_forecast_ts = _startup_fc_ts
    _cached_forecast_units = _startup_fc_units or "metric"
    logger.info(f"Loaded cached forecast from file ({len(_startup_fc)} intervals)")


def _normalize_forecast_values(values: dict) -> dict:
    """Map Tomorrow.io field aliases into the keys our UI expects."""
    out = dict(values or {})
    if out.get("weatherCodeFullDay") is None:
        for key in ("weatherCodeMax", "weatherCode", "weatherCodeDay"):
            if out.get(key) is not None:
                out["weatherCodeFullDay"] = out.get(key)
                break
    return out


def _intervals_from_timelines_payload(payload: dict) -> list:
    data = payload.get("data") or {}
    timelines = data.get("timelines") or []
    if not timelines:
        return []
    raw = timelines[0].get("intervals") or []
    return [
        {
            "startTime": item.get("startTime") or item.get("time") or "",
            "values": _normalize_forecast_values(item.get("values") or {}),
        }
        for item in raw
    ]


def _intervals_from_weather_forecast_payload(payload: dict) -> list:
    """Parse GET /weather/forecast daily timeline into interval dicts."""
    timelines = payload.get("timelines") or {}
    daily = timelines.get("daily") if isinstance(timelines, dict) else None
    if not daily:
        return []
    return [
        {
            "startTime": item.get("time") or item.get("startTime") or "",
            "values": _normalize_forecast_values(item.get("values") or {}),
        }
        for item in daily
    ]


def _store_forecast_intervals(intervals: list) -> list:
    global _cached_forecast, _cached_forecast_ts, _cached_forecast_units
    intervals = _forecast_from_today(intervals)
    if not intervals:
        return []
    _cached_forecast = intervals
    _cached_forecast_ts = time.time()
    _cached_forecast_units = "metric"
    _save_file_cache(_FORECAST_CACHE_FILE, intervals, "metric")
    return _return_forecast()


def _fetch_forecast_via_weather_endpoint(tag: str) -> list:
    """Fallback when /timelines is forbidden on the current API key/plan."""
    s = get_session()
    resp = s.get(
        f"{TOMORROW_API_URL}/weather/forecast",
        params={
            "location": _temperature_location(),
            "units": _temperature_units(),
            "timesteps": "1d",
            "apikey": TOMORROW_API_KEY,
        },
        timeout=(5, 20),
    )
    if resp.status_code == 429:
        _record_call("forecast")
        _enter_backoff()
        return []
    resp.raise_for_status()
    _record_call("forecast")
    _exit_backoff()
    intervals = _intervals_from_weather_forecast_payload(resp.json() or {})
    if not intervals:
        logger.error("[Forecast:%s] weather/forecast returned no daily rows", tag)
        return []
    logger.info("[Forecast:%s] Using weather/forecast fallback (%d days)", tag, len(intervals))
    return _store_forecast_intervals(intervals)


def grab_forecast(tag="unknown"):
    """
    Fetch daily forecast data.
    Returns cached data if called within the cache TTL or rate limit window.
    """
    global _cached_forecast, _cached_forecast_ts, _cached_forecast_units

    if not _weather_enabled():
        logger.info("Tomorrow.io weather disabled in web portal settings")
        return []
    if not TOMORROW_API_KEY:
        logger.warning("TOMORROW_API_KEY not set — skipping forecast fetch")
        return []

    # Return cache if still fresh (convert if display units changed)
    if _cached_forecast and (time.time() - _cached_forecast_ts) < _FORECAST_CACHE_TTL:
        if _forecast_from_today(_cached_forecast):
            return _return_forecast()

    # Rate limit check
    if _rate_limited("forecast"):
        logger.debug(f"[Forecast:{tag}] Rate limit: skipping API call, using cache")
        if _cached_forecast:
            return _return_forecast()
        return []

    dt = datetime.now()
    today_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        s = get_session()
        resp = s.post(
            f"{TOMORROW_API_URL}/timelines",
            headers={
                "Accept-Encoding": "gzip",
                "accept": "application/json",
                "content-type": "application/json"
            },
            params={"apikey": TOMORROW_API_KEY},
            json={
                "location": _temperature_location(),
                "units": _temperature_units(),
                "timezone": "auto",
                "startTime": today_start.isoformat(),
                "dailyStartHour": 6,
                "fields": [
                    "temperatureMin",
                    "temperatureMax",
                    "weatherCodeFullDay",
                    "sunriseTime",
                    "sunsetTime",
                    "moonPhase",
                    "precipitationProbabilityAvg",
                ],
                "timesteps": ["1d"],
                "endTime": (today_start + timedelta(days=int(FORECAST_DAYS))).isoformat(),
            },
            timeout=(5, 20)
        )

        if resp.status_code == 429:
            _record_call("forecast")
            _enter_backoff()
            if _cached_forecast:
                return _return_forecast()
            return []

        # Free / restricted keys often 403 the timelines endpoint — fall back.
        if resp.status_code in (400, 401, 403):
            logger.warning(
                "[Forecast:%s] timelines HTTP %s — trying weather/forecast",
                tag,
                resp.status_code,
            )
            try:
                result = _fetch_forecast_via_weather_endpoint(tag)
                if result:
                    return result
            except RequestException as fallback_exc:
                logger.error(
                    "[Forecast:%s] weather/forecast fallback failed: %s",
                    tag,
                    fallback_exc,
                )
            if _cached_forecast:
                return _return_forecast()
            return []

        resp.raise_for_status()
        _record_call("forecast")
        _exit_backoff()

        intervals = _intervals_from_timelines_payload(resp.json() or {})
        if not intervals:
            logger.error(f"[Forecast:{tag}] No timelines returned from API")
            try:
                result = _fetch_forecast_via_weather_endpoint(tag)
                if result:
                    return result
            except RequestException:
                pass
            if _cached_forecast:
                return _return_forecast()
            return []

        stored = _store_forecast_intervals(intervals)
        if stored:
            return stored
        logger.error(f"[Forecast:{tag}] No current-or-future intervals after date filter")
        if _cached_forecast:
            return _return_forecast()
        return []

    except RequestException as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        # Don't clear backoff on network errors — let auto-clear (2hr) handle it
        if is_dns_error(e):
            logger.error(
                f"[{timestamp}] [Forecast:{tag}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logger.error(
                f"[{timestamp}] [Forecast:{tag}] API request failed: {e}"
            )
            try:
                result = _fetch_forecast_via_weather_endpoint(tag)
                if result:
                    return result
            except RequestException as fallback_exc:
                logger.error(
                    "[Forecast:%s] weather/forecast fallback failed: %s",
                    tag,
                    fallback_exc,
                )
        if _cached_forecast:
            return _return_forecast()
        return []

    except KeyError as e:
        logger.error(f"[Forecast:{tag}] Unexpected data format: {e}")
        if _cached_forecast:
            return _return_forecast()
        return []
