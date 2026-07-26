"""
Live vessel positions from aisstream.io (free WebSocket AIS feed).

Opens one persistent WSS connection, sends a bounding-box subscription, then
merges PositionReport + ShipStaticData messages by MMSI into a shared table.

Protocol and merge strategy adapted from capsule-radar-ais (MIT):
  https://github.com/socquique/capsule-radar-ais
API docs: https://aisstream.io/documentation
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

AIS_WSS_URL = "wss://stream.aisstream.io/v0/stream"
AIS_BOX_MARGIN = 1.15  # slightly larger than display range so edge vessels stay in feed
SHIP_STALE_S = 12 * 60  # ships report less often than aircraft
AIS_MAX_SHIPS = 200
RECONNECT_MIN_S = 2.0
RECONNECT_MAX_S = 60.0
FILTER_MESSAGE_TYPES = ("PositionReport", "ShipStaticData")

# AIS navigation status (ITU-R M.1371) — common codes only
NAV_UNDERWAY_ENGINE = 0
NAV_AT_ANCHOR = 1
NAV_MOORED = 5
NAV_FISHING = 7
NAV_UNDERWAY_SAILING = 8
NAV_UNDEFINED = 15


@dataclass
class Ship:
    """Vessel track merged from aisstream PositionReport + ShipStaticData."""

    mmsi: int = 0
    name: str = ""
    dest: str = ""
    lat: float = 0.0
    lon: float = 0.0
    sog_kt: float = float("nan")
    cog_deg: float = float("nan")
    heading_deg: float = float("nan")
    nav_status: int = NAV_UNDEFINED
    ship_type: int = 0
    length_m: int = 0
    beam_m: int = 0
    draught_m: float = float("nan")
    last_seen: float = 0.0  # time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mmsi": self.mmsi,
            "name": self.name,
            "destination": self.dest,
            "lat": self.lat,
            "lon": self.lon,
            "sog_kt": None if math.isnan(self.sog_kt) else self.sog_kt,
            "cog_deg": None if math.isnan(self.cog_deg) else self.cog_deg,
            "heading_deg": None if math.isnan(self.heading_deg) else self.heading_deg,
            "nav_status": self.nav_status,
            "ship_type": self.ship_type,
            "length_m": self.length_m,
            "beam_m": self.beam_m,
            "draught_m": None if math.isnan(self.draught_m) else self.draught_m,
            "last_seen": self.last_seen,
            "data_source": "aisstream",
        }


def _trim_ais(value: str) -> str:
    """Trim AIS string padding: trailing spaces and '@' fill characters."""
    return (value or "").rstrip(" @\0")


def _as_float(value, default: float = float("nan")) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def bounding_box(lat: float, lon: float, range_nm: float) -> list[list[float]]:
    """[[SW lat, SW lon], [NE lat, NE lon]] for aisstream BoundingBoxes."""
    nm = max(0.5, float(range_nm)) * AIS_BOX_MARGIN
    d_lat = nm / 60.0
    cos_lat = math.cos(math.radians(lat))
    d_lon = nm / (60.0 * (cos_lat if abs(cos_lat) > 0.01 else 0.01))
    return [
        [lat - d_lat, lon - d_lon],
        [lat + d_lat, lon + d_lon],
    ]


def _api_key() -> str:
    try:
        from secrets_store import api_enabled

        if not api_enabled("AISSTREAM_API_KEY"):
            return ""
    except Exception:
        pass
    try:
        from config import AISSTREAM_API_KEY

        return (AISSTREAM_API_KEY or "").strip()
    except ImportError:
        import os

        return os.environ.get("AISSTREAM_API_KEY", "").strip()


def ais_data_enabled() -> bool:
    """True when the on-device / portal AIS data toggle is on."""
    try:
        from display.round_touch import settings

        return settings.ais_enabled()
    except Exception:
        return False


class AisClient:
    """
    Background aisstream.io client.

    Call start()/stop()/configure(). Read vessels with snapshot() (thread-safe).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ships: dict[int, Ship] = {}
        self._api_key = ""
        self._lat = 0.0
        self._lon = 0.0
        self._range_nm = 15.0
        self._connected = False
        self._last_msg_ts = 0.0
        self._last_connect_ts = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._config_epoch = 0
        self._started = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_msg_ts(self) -> float:
        return self._last_msg_ts

    def configure(self, api_key: str, lat: float, lon: float, range_nm: float) -> None:
        """Update credentials / home box. Re-subscribes if already connected."""
        with self._lock:
            changed = (
                api_key != self._api_key
                or abs(lat - self._lat) > 1e-7
                or abs(lon - self._lon) > 1e-7
                or abs(range_nm - self._range_nm) > 1e-3
            )
            self._api_key = (api_key or "").strip()
            self._lat = float(lat)
            self._lon = float(lon)
            self._range_nm = max(0.5, float(range_nm))
            if changed:
                self._config_epoch += 1
        if changed and self._loop and self._ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._send_subscription(), self._loop)
            except Exception:
                logger.debug("AIS re-subscribe schedule failed", exc_info=True)

    def start(self) -> None:
        if self._started and self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started = True
        self._thread = threading.Thread(target=self._thread_main, name="aisstream", daemon=True)
        self._thread.start()
        logger.info("AIS client thread started")

    def stop(self) -> None:
        self._stop.set()
        self._started = False
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        self._connected = False
        logger.info("AIS client stopped")

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._ships)

    def snapshot(self, include_stale: bool = False) -> list[Ship]:
        """Copy current vessels; expire quiet tracks unless include_stale."""
        now = time.time()
        with self._lock:
            out: list[Ship] = []
            dead: list[int] = []
            for mmsi, ship in self._ships.items():
                if not include_stale and now - ship.last_seen > SHIP_STALE_S:
                    dead.append(mmsi)
                    continue
                if ship.lat == 0.0 and ship.lon == 0.0:
                    continue
                out.append(
                    Ship(
                        mmsi=ship.mmsi,
                        name=ship.name,
                        dest=ship.dest,
                        lat=ship.lat,
                        lon=ship.lon,
                        sog_kt=ship.sog_kt,
                        cog_deg=ship.cog_deg,
                        heading_deg=ship.heading_deg,
                        nav_status=ship.nav_status,
                        ship_type=ship.ship_type,
                        length_m=ship.length_m,
                        beam_m=ship.beam_m,
                        draught_m=ship.draught_m,
                        last_seen=ship.last_seen,
                    )
                )
            for mmsi in dead:
                self._ships.pop(mmsi, None)
            return out

    def snapshot_dicts(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.snapshot()]

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception:
            logger.exception("AIS client loop crashed")
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._connected = False

    async def _run(self) -> None:
        backoff = RECONNECT_MIN_S
        while not self._stop.is_set():
            key = self._api_key
            if not key:
                self._connected = False
                await asyncio.sleep(1.0)
                continue
            try:
                import websockets

                async with websockets.connect(
                    AIS_WSS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._last_connect_ts = time.time()
                    backoff = RECONNECT_MIN_S
                    logger.info("[ais] WebSocket connected → %s", AIS_WSS_URL)
                    await self._send_subscription()
                    epoch = self._config_epoch
                    while not self._stop.is_set():
                        if self._config_epoch != epoch:
                            await self._send_subscription()
                            epoch = self._config_epoch
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        self._ingest(raw)
            except ImportError:
                logger.error("[ais] websockets package not installed — pip install websockets")
                await asyncio.sleep(30.0)
            except Exception as exc:
                self._connected = False
                self._ws = None
                if self._stop.is_set():
                    break
                logger.warning("[ais] WebSocket error: %s — reconnect in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(RECONNECT_MAX_S, backoff * 1.8)
            finally:
                self._ws = None
                self._connected = False

    async def _send_subscription(self) -> None:
        ws = self._ws
        if ws is None:
            return
        with self._lock:
            key = self._api_key
            lat, lon, range_nm = self._lat, self._lon, self._range_nm
        if not key:
            return
        box = bounding_box(lat, lon, range_nm)
        msg = {
            "APIKey": key,
            "BoundingBoxes": [box],
            "FilterMessageTypes": list(FILTER_MESSAGE_TYPES),
        }
        await ws.send(json.dumps(msg))
        logger.info(
            "[ais] subscribed box [%.4f,%.4f]..[%.4f,%.4f]",
            box[0][0],
            box[0][1],
            box[1][0],
            box[1][1],
        )

    def _ingest(self, raw: str) -> None:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug("AIS JSON parse error: %s", exc)
            return

        mtype = doc.get("MessageType")
        if not mtype:
            err = doc.get("error") or doc.get("Error")
            if err:
                logger.warning("[ais] server error: %s", err)
            return

        meta = doc.get("MetaData") or {}
        mmsi = _as_int(meta.get("MMSI"))
        if mmsi <= 0:
            message = doc.get("Message") or {}
            body = message.get(mtype) or {}
            mmsi = _as_int(body.get("UserID"))
        if mmsi <= 0:
            return

        now = time.time()
        with self._lock:
            ship = self._ships.get(mmsi)
            is_new = ship is None
            if ship is None:
                if len(self._ships) >= AIS_MAX_SHIPS:
                    return
                ship = Ship(mmsi=mmsi)
                self._ships[mmsi] = ship
            ship.mmsi = mmsi
            ship.last_seen = now

            if not ship.name:
                ship.name = _trim_ais(str(meta.get("ShipName") or ""))

            # MetaData lat/lon present on most messages (lowercase keys in feed)
            meta_lat = meta.get("latitude", meta.get("Latitude"))
            meta_lon = meta.get("longitude", meta.get("Longitude"))
            if meta_lat is not None and meta_lon is not None:
                ship.lat = _as_float(meta_lat, ship.lat)
                ship.lon = _as_float(meta_lon, ship.lon)

            message = doc.get("Message") or {}
            if mtype == "PositionReport":
                pr = message.get("PositionReport") or {}
                if "Latitude" in pr:
                    ship.lat = _as_float(pr.get("Latitude"), ship.lat)
                if "Longitude" in pr:
                    ship.lon = _as_float(pr.get("Longitude"), ship.lon)
                ship.cog_deg = _as_float(pr.get("Cog"), ship.cog_deg)
                ship.sog_kt = _as_float(pr.get("Sog"), ship.sog_kt)
                heading = _as_float(pr.get("TrueHeading"), float("nan"))
                # 511 = not available in AIS
                ship.heading_deg = heading if 0.0 <= heading < 360.0 else float("nan")
                ship.nav_status = _as_int(pr.get("NavigationalStatus"), NAV_UNDEFINED)
            elif mtype == "ShipStaticData":
                sd = message.get("ShipStaticData") or {}
                name = sd.get("Name")
                if name:
                    ship.name = _trim_ais(str(name))
                ship.ship_type = _as_int(sd.get("Type"), ship.ship_type)
                dest = sd.get("Destination")
                if dest:
                    ship.dest = _trim_ais(str(dest))
                dim = sd.get("Dimension") or {}
                if dim:
                    ship.length_m = _as_int(dim.get("A")) + _as_int(dim.get("B"))
                    ship.beam_m = _as_int(dim.get("C")) + _as_int(dim.get("D"))
                if "MaximumStaticDraught" in sd:
                    ship.draught_m = _as_float(sd.get("MaximumStaticDraught"))

            self._last_msg_ts = now
            if is_new:
                logger.info(
                    "[ais] new vessel MMSI=%s name=%r type=%s at %.4f,%.4f (tracked=%d)",
                    mmsi,
                    ship.name or "?",
                    mtype,
                    ship.lat,
                    ship.lon,
                    len(self._ships),
                )


_client: AisClient | None = None
_client_lock = threading.Lock()


def get_client() -> AisClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = AisClient()
        return _client


def fetch_ais_vessels(
    lat: float | None = None,
    lon: float | None = None,
    range_nm: float | None = None,
) -> list[dict[str, Any]]:
    """
    Ensure the AIS stream is configured for the given area and return vessels.

    Starts the background client when AIS data is enabled and an API key is set.
    Returns [] when disabled, unconfigured, or not yet connected.
    """
    if not ais_data_enabled():
        client = get_client()
        if client._started:
            client.stop()
        return []

    key = _api_key()
    if not key:
        return []

    if lat is None or lon is None:
        try:
            from config import LOCATION_HOME

            lat = float(LOCATION_HOME[0])
            lon = float(LOCATION_HOME[1])
        except Exception:
            return []

    if range_nm is None:
        try:
            from display.round_touch import scale, settings

            range_nm = float(scale.search_radius_nm(settings.scale_index()))
        except Exception:
            try:
                from config import SEARCH_RADIUS_NM

                range_nm = float(SEARCH_RADIUS_NM)
            except Exception:
                range_nm = 15.0

    client = get_client()
    client.configure(key, float(lat), float(lon), float(range_nm))
    if not client._started:
        client.start()
    return client.snapshot_dicts()


def sync_ais_client() -> None:
    """Start or stop the background client to match current settings / key."""
    client = get_client()
    if not ais_data_enabled() or not _api_key():
        if client._started:
            logger.info("[ais] stopping client (disabled or no API key)")
            client.stop()
        return
    try:
        from config import LOCATION_HOME
        from display.round_touch import scale, settings

        lat = float(LOCATION_HOME[0])
        lon = float(LOCATION_HOME[1])
        range_nm = float(scale.search_radius_nm(settings.scale_index()))
    except Exception:
        return
    client.configure(_api_key(), lat, lon, range_nm)
    if not client._started:
        client.start()
    logger.info(
        "[ais] sync: key=%s home=%.4f,%.4f range=%.1fnm connected=%s tracked=%d",
        "yes" if _api_key() else "no",
        lat,
        lon,
        range_nm,
        client.connected,
        client.tracked_count(),
    )


# ---- presentation helpers (shared with radar / detail UI) ----

_NAV_NAMES = {
    0: "Under way",
    1: "At anchor",
    2: "Not under cmd",
    3: "Restricted",
    4: "Constrained",
    5: "Moored",
    6: "Aground",
    7: "Fishing",
    8: "Sailing",
    14: "SART",
    15: "Unknown",
}


def nav_status_name(status: int) -> str:
    return _NAV_NAMES.get(int(status), "Unknown")


def ship_category_name(ship_type: int) -> str:
    t = int(ship_type or 0)
    if 70 <= t <= 79:
        return "Cargo"
    if 80 <= t <= 89:
        return "Tanker"
    if 60 <= t <= 69:
        return "Passenger"
    if 40 <= t <= 49:
        return "High-speed"
    if t == 30:
        return "Fishing"
    if t in (36, 37):
        return "Sailing"
    if 50 <= t <= 59:
        return "Service"
    return "Vessel"


def ship_is_stationary(nav_status: int, sog_kt) -> bool:
    if nav_status in (NAV_AT_ANCHOR, NAV_MOORED):
        return True
    try:
        if sog_kt is not None and float(sog_kt) < 0.5:
            return True
    except (TypeError, ValueError):
        pass
    return False


def vessel_to_radar_entry(vessel: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an AIS vessel dict into the radar/detail entry shape."""
    lat = vessel.get("lat")
    lon = vessel.get("lon")
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if abs(lat_f) < 0.01 and abs(lon_f) < 0.01:
        return None

    mmsi = int(vessel.get("mmsi") or 0)
    name = (vessel.get("name") or "").strip()
    dest = (vessel.get("destination") or "").strip()
    sog = vessel.get("sog_kt")
    cog = vessel.get("cog_deg")
    heading = vessel.get("heading_deg")
    nav = int(vessel.get("nav_status") or NAV_UNDEFINED)
    stype = int(vessel.get("ship_type") or 0)

    try:
        from utilities.mmsi_mid import country_name_for_mmsi, flag_iso2_for_mmsi

        flag_iso2 = flag_iso2_for_mmsi(mmsi)
        flag_country = country_name_for_mmsi(mmsi)
    except Exception:
        flag_iso2 = ""
        flag_country = ""

    heading_out = 0
    for candidate in (heading, cog):
        try:
            h = float(candidate)
            if 0.0 <= h < 360.0:
                heading_out = int(round(h))
                break
        except (TypeError, ValueError):
            continue

    try:
        gs = int(round(float(sog))) if sog is not None else 0
    except (TypeError, ValueError):
        gs = 0

    label = name or f"MMSI {mmsi}"
    category = ship_category_name(stype)

    return {
        "kind": "vessel",
        "callsign": label,
        "mmsi": mmsi,
        "name": name,
        "airline": flag_country or "Flag unknown",
        "plane": category,
        "origin": "",
        "destination": dest,
        "plane_latitude": lat_f,
        "plane_longitude": lon_f,
        "altitude": None,
        "ground_speed": gs,
        "heading": heading_out,
        "vertical_speed": 0,
        "nav_status": nav,
        "nav_status_name": nav_status_name(nav),
        "ship_type": stype,
        "length_m": int(vessel.get("length_m") or 0),
        "beam_m": int(vessel.get("beam_m") or 0),
        "draught_m": vessel.get("draught_m"),
        "flag_iso2": flag_iso2,
        "flag_country": flag_country,
        "stationary": ship_is_stationary(nav, sog),
        "data_source": "aisstream",
        "sog_kt": sog,
        "cog_deg": cog,
    }


_last_snapshot_log = 0.0


def fetch_ais_radar_entries(
    lat: float | None = None,
    lon: float | None = None,
    range_nm: float | None = None,
) -> list[dict[str, Any]]:
    """Vessels as radar-compatible dicts (empty when AIS is off)."""
    global _last_snapshot_log
    raw = fetch_ais_vessels(lat, lon, range_nm)
    out: list[dict[str, Any]] = []
    for v in raw:
        entry = vessel_to_radar_entry(v)
        if entry:
            out.append(entry)
    if ais_data_enabled():
        now = time.time()
        if now - _last_snapshot_log >= 10.0:
            _last_snapshot_log = now
            client = get_client()
            logger.info(
                "[ais] snapshot: %d vessels (connected=%s last_msg=%.0fs ago)",
                len(out),
                client.connected,
                (now - client.last_msg_ts) if client.last_msg_ts else -1,
            )
    return out
