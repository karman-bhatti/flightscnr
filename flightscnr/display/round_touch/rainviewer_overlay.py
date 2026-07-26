"""RainViewer precipitation overlay for the circular radar map.

Uses the free public Weather Maps API (personal/educational use):
https://www.rainviewer.com/api.html
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import threading
import time

import pygame
import requests

from display.round_touch import scale, theme

logger = logging.getLogger("flightscnr.display")

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
CACHE_DIR = os.path.join(DATA_DIR, "maps", "rainviewer")

METADATA_URL = "https://api.rainviewer.com/public/weather-maps.json"
USER_AGENT = "FlightScnrPi/1.0 (RainViewer overlay; https://www.rainviewer.com/)"

# Free personal API: max zoom 7, Universal Blue (2), PNG.
MAX_ZOOM = 7
TILE_SIZE = 512
COLOR_SCHEME = 2
OPTIONS = "0_0"

EARTH_RADIUS_M = 6378137.0
METADATA_TTL_S = 5 * 60
OVERLAY_ALPHA = 180  # keep map/roads readable under precip
FETCH_TIMEOUT_S = 20

_lock = threading.Lock()
_surfaces: dict[tuple, pygame.Surface] = {}
_fetch_threads: dict[tuple, threading.Thread] = {}
_meta: dict | None = None
_meta_ts = 0.0
_meta_lock = threading.Lock()


def _enabled() -> bool:
    try:
        from display.round_touch import settings

        return bool(settings.show_precipitation())
    except Exception:
        return True


def _meters_per_pixel(lat_deg: float, zoom: int) -> float:
    return math.cos(math.radians(lat_deg)) * 2 * math.pi * EARTH_RADIUS_M / (
        TILE_SIZE * (2 ** zoom)
    )


def _cache_key_for_scale(scale_index: int, frame_time: int) -> tuple | None:
    try:
        from config import LOCATION_HOME, location_configured
    except ImportError:
        return None
    if not location_configured():
        return None
    return (
        round(LOCATION_HOME[0], 5),
        round(LOCATION_HOME[1], 5),
        int(scale_index),
        int(frame_time),
    )


def _store_surface(key: tuple, surface: pygame.Surface) -> None:
    """Cache a frame surface and evict stale ones.

    Keys embed the RainViewer frame timestamp, so without eviction every
    ~10min frame pinned another ~2MB surface forever (~280MB/day leak).
    Only the newest frame is ever drawn; older frames and other centers
    are dead weight.
    """
    with _lock:
        stale = [
            k for k in _surfaces if k[3] != key[3] or k[0] != key[0] or k[1] != key[1]
        ]
        for k in stale:
            del _surfaces[k]
        _surfaces[key] = surface


def _cache_path_for_key(key: tuple) -> str:
    lat, lon, scale_idx, frame_time = key
    return os.path.join(
        CACHE_DIR, f"precip_{lat}_{lon}_{scale_idx}_{frame_time}.png"
    )


def _cached_metadata() -> dict | None:
    with _meta_lock:
        return _meta


def _metadata_stale() -> bool:
    with _meta_lock:
        if _meta is None:
            return True
        return (time.time() - _meta_ts) >= METADATA_TTL_S


def _fetch_metadata(force: bool = False) -> dict | None:
    """Blocking metadata refresh — call only from background threads."""
    global _meta, _meta_ts
    if not force and not _metadata_stale():
        return _cached_metadata()

    try:
        resp = requests.get(
            METADATA_URL,
            timeout=FETCH_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (OSError, requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        logger.warning("RainViewer metadata fetch failed: %s", exc)
        return _cached_metadata()

    with _meta_lock:
        _meta = data
        _meta_ts = time.time()
    return data


def _latest_frame(meta: dict | None) -> tuple[str, str, int] | None:
    """Return (host, path, unix_time) for the newest past radar frame."""
    if not meta:
        return None
    host = str(meta.get("host") or "").rstrip("/")
    past = (meta.get("radar") or {}).get("past") or []
    if not host or not past:
        return None
    frame = past[-1]
    path = str(frame.get("path") or "")
    try:
        frame_time = int(frame.get("time") or 0)
    except (TypeError, ValueError):
        frame_time = 0
    if not path or not frame_time:
        return None
    return host, path, frame_time


def _apply_circle_mask(surface: pygame.Surface) -> pygame.Surface:
    w, h = surface.get_size()
    cx = cy = w // 2
    radius = min(cx, cy)
    masked = pygame.Surface((w, h), pygame.SRCALPHA)
    masked.blit(surface, (0, 0))
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (cx, cy), radius)
    masked.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return masked


def _with_alpha(surface: pygame.Surface, alpha: int) -> pygame.Surface:
    """Multiply existing per-pixel alpha by overlay opacity."""
    out = surface.copy()
    alpha = max(0, min(255, int(alpha)))
    if alpha >= 255:
        return out
    # Scale only the A channel so clear (no-rain) pixels stay transparent.
    arr_a = pygame.surfarray.pixels_alpha(out)
    arr_a[:] = (arr_a.astype("uint16") * alpha // 255).astype("uint8")
    del arr_a
    return out


def _build_overlay(scale_index: int, host: str, path: str) -> pygame.Surface | None:
    try:
        from config import LOCATION_HOME, location_configured
    except ImportError:
        return None
    if not location_configured():
        return None
    if scale_index < 0 or scale_index >= len(scale.SCALE_BANDS):
        return None

    home_lat, home_lon = float(LOCATION_HOME[0]), float(LOCATION_HOME[1])
    outer_km = scale.SCALE_BANDS[scale_index]["label_km"]
    px_per_km = theme.GRID_OUTER_RADIUS / outer_km
    zoom = MAX_ZOOM

    # Centered widget tile: one PNG covering a Mercator window around home.
    url = (
        f"{host}{path}/{TILE_SIZE}/{zoom}/"
        f"{home_lat:.5f}/{home_lon:.5f}/{COLOR_SCHEME}/{OPTIONS}.png"
    )
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        tile = pygame.image.load(io.BytesIO(resp.content)).convert_alpha()
    except (OSError, requests.RequestException, pygame.error) as exc:
        logger.warning("RainViewer tile fetch failed %s: %s", url, exc)
        return None

    tile_km = TILE_SIZE * _meters_per_pixel(home_lat, zoom) / 1000.0
    if tile_km <= 0:
        return None

    diameter_px = theme.VISIBLE_RADIUS * 2
    diameter_km = diameter_px / px_per_km
    # Crop the geographic window that maps to the radar circle, then scale.
    crop_px = max(8, int(round(TILE_SIZE * (diameter_km / tile_km))))
    crop_px = min(crop_px, TILE_SIZE)
    tw, th = tile.get_size()
    cx, cy = tw // 2, th // 2
    half = crop_px // 2
    rect = pygame.Rect(cx - half, cy - half, crop_px, crop_px)
    rect = rect.clip(tile.get_rect())
    cropped = tile.subsurface(rect).copy()

    scaled = pygame.transform.smoothscale(cropped, (diameter_px, diameter_px))
    scaled = _apply_circle_mask(scaled)
    scaled = _with_alpha(scaled, OVERLAY_ALPHA)

    logger.info(
        "Built RainViewer precip overlay (scale %d, z%d, crop %d→%d px, ~%.1f km)",
        scale_index,
        zoom,
        crop_px,
        diameter_px,
        diameter_km / 2.0,
    )
    return scaled


def _load_disk(key: tuple) -> pygame.Surface | None:
    path = _cache_path_for_key(key)
    if not os.path.isfile(path):
        return None
    try:
        return pygame.image.load(path).convert_alpha()
    except pygame.error:
        return None


def _save_disk(surface: pygame.Surface, key: tuple) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        pygame.image.save(surface, _cache_path_for_key(key))
    except (OSError, pygame.error) as exc:
        logger.warning("Could not cache RainViewer overlay: %s", exc)


def _prune_old_cache(keep_frame: int) -> None:
    """Drop overlay PNGs older than the current frame to limit disk use."""
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return
    for name in names:
        if not name.startswith("precip_") or not name.endswith(".png"):
            continue
        # precip_{lat}_{lon}_{scale}_{frame}.png — frame is last underscore group.
        stem = name[:-4]
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        try:
            frame = int(parts[1])
        except ValueError:
            continue
        if frame != keep_frame:
            try:
                os.remove(os.path.join(CACHE_DIR, name))
            except OSError:
                pass


def _fetch_running(key: tuple) -> bool:
    with _lock:
        t = _fetch_threads.get(key)
        return bool(t and t.is_alive())


_refresh_thread: threading.Thread | None = None


def _start_fetch(key: tuple, host: str, path: str) -> None:
    if _fetch_running(key):
        return

    def _worker():
        try:
            surface = _build_overlay(key[2], host, path)
            if surface is None:
                return
            _save_disk(surface, key)
            _store_surface(key, surface)
            _prune_old_cache(key[3])
        finally:
            with _lock:
                _fetch_threads.pop(key, None)

    thread = threading.Thread(
        target=_worker,
        name=f"rainviewer-{key[2]}",
        daemon=True,
    )
    with _lock:
        _fetch_threads[key] = thread
    thread.start()


def _ensure_current_frame_async() -> None:
    """Refresh metadata (if stale) and load/fetch the active-scale overlay."""
    global _refresh_thread

    def _worker():
        try:
            meta = _fetch_metadata(force=_metadata_stale())
            frame = _latest_frame(meta)
            if frame is None:
                return
            host, path, frame_time = frame
            key = _cache_key_for_scale(scale.active_index(), frame_time)
            if key is None:
                return
            with _lock:
                if key in _surfaces:
                    return
            disk = _load_disk(key)
            if disk is not None:
                _store_surface(key, disk)
                return
            _start_fetch(key, host, path)
        finally:
            global _refresh_thread
            with _lock:
                _refresh_thread = None

    with _lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_thread = threading.Thread(
            target=_worker,
            name="rainviewer-refresh",
            daemon=True,
        )
        _refresh_thread.start()


def request_overlay() -> None:
    """Kick an async refresh so draw stays off the network."""
    if not _enabled():
        return
    # Fast path: serve a disk/memory hit for the last known frame.
    frame = _latest_frame(_cached_metadata())
    if frame is not None:
        _, _, frame_time = frame
        key = _cache_key_for_scale(scale.active_index(), frame_time)
        if key is not None:
            with _lock:
                cached = key in _surfaces
            if not cached:
                disk = _load_disk(key)
                if disk is not None:
                    _store_surface(key, disk)
    if _metadata_stale() or get_overlay() is None:
        _ensure_current_frame_async()


def invalidate() -> None:
    with _lock:
        _surfaces.clear()
        _fetch_threads.clear()
    with _meta_lock:
        global _meta, _meta_ts
        _meta = None
        _meta_ts = 0.0


def cache_token() -> object:
    """Stable token for radar backdrop invalidation (changes with precip frame)."""
    if not _enabled():
        return None
    frame = _latest_frame(_cached_metadata())
    if frame is None:
        return ("none",)
    _, _, frame_time = frame
    key = _cache_key_for_scale(scale.active_index(), frame_time)
    if key is None:
        return None
    with _lock:
        return (key, id(_surfaces.get(key)) if key in _surfaces else 0)


def get_overlay() -> pygame.Surface | None:
    if not _enabled():
        return None
    frame = _latest_frame(_cached_metadata())
    if frame is None:
        return None
    _, _, frame_time = frame
    key = _cache_key_for_scale(scale.active_index(), frame_time)
    if key is None:
        return None
    with _lock:
        return _surfaces.get(key)


def draw_overlay(surface: pygame.Surface, pan_offset: tuple[int, int] | None = None) -> None:
    overlay = get_overlay()
    if overlay is None:
        return
    facing = 0.0
    try:
        from display.round_touch import settings

        facing = float(settings.effective_facing_deg() or 0.0)
    except Exception:
        facing = 0.0
    ox = int(pan_offset[0]) if pan_offset else 0
    oy = int(pan_offset[1]) if pan_offset else 0
    if abs(facing) < 0.05:
        rect = overlay.get_rect(center=(theme.CENTER_X + ox, theme.CENTER_Y + oy))
        surface.blit(overlay, rect)
        return
    rotated = pygame.transform.rotate(overlay, facing)
    rect = rotated.get_rect(center=(theme.CENTER_X + ox, theme.CENTER_Y + oy))
    surface.blit(rotated, rect)


def attribution_text() -> str | None:
    if not _enabled() or get_overlay() is None:
        return None
    return "© RainViewer"
