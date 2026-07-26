"""Vessel radar declutter helpers (config.h flags)."""

from __future__ import annotations

import os


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def short_tags_enabled() -> bool:
    try:
        from config import VESSEL_SHORT_TAGS

        return bool(VESSEL_SHORT_TAGS)
    except ImportError:
        return _bool_env("VESSEL_SHORT_TAGS", True)


def hide_parked_enabled() -> bool:
    try:
        from config import VESSEL_HIDE_PARKED

        return bool(VESSEL_HIDE_PARKED)
    except ImportError:
        return _bool_env("VESSEL_HIDE_PARKED", True)


def hierarchy_enabled() -> bool:
    try:
        from config import VESSEL_HIERARCHY

        return bool(VESSEL_HIERARCHY)
    except ImportError:
        return _bool_env("VESSEL_HIERARCHY", True)


def density_mode() -> str:
    """all_labels | moving_only | icons_only"""
    try:
        from config import VESSEL_DENSITY_MODE

        mode = str(VESSEL_DENSITY_MODE or "").strip().lower()
    except ImportError:
        mode = str(os.environ.get("VESSEL_DENSITY_MODE", "moving_only")).strip().lower()
    if mode in ("all", "all_labels", "labels"):
        return "all_labels"
    if mode in ("icons", "icons_only", "icon"):
        return "icons_only"
    return "moving_only"


def parked_sog_kt() -> float:
    try:
        from config import VESSEL_PARKED_SOG_KT

        return float(VESSEL_PARKED_SOG_KT)
    except (ImportError, TypeError, ValueError):
        try:
            return float(os.environ.get("VESSEL_PARKED_SOG_KT", "0.5"))
        except ValueError:
            return 0.5


def is_vessel(flight: dict | None) -> bool:
    return bool(flight and flight.get("kind") == "vessel")


def is_parked(flight: dict | None) -> bool:
    """True for anchored/moored or near-zero SOG vessels."""
    if not is_vessel(flight):
        return False
    if flight.get("stationary"):
        return True
    nav = (flight.get("nav_status_name") or "").strip().lower()
    if nav in ("at anchor", "moored", "aground"):
        return True
    sog = flight.get("sog_kt")
    try:
        if sog is not None and float(sog) < parked_sog_kt():
            return True
    except (TypeError, ValueError):
        pass
    return False


def should_show_on_radar(flight: dict | None) -> bool:
    if not is_vessel(flight):
        return True
    if hide_parked_enabled() and is_parked(flight):
        return False
    return True


def should_label(flight: dict | None) -> bool:
    """Whether to draw a text tag next to a vessel icon."""
    if not is_vessel(flight):
        return True
    mode = density_mode()
    if mode == "icons_only":
        return False
    if mode == "moving_only" and is_parked(flight):
        return False
    return bool(display_name(flight))


def display_name(flight: dict | None) -> str:
    """Radar label name — never MMSI."""
    if not flight:
        return ""
    for key in ("name", "callsign"):
        raw = (flight.get(key) or "").strip()
        if not raw:
            continue
        if raw.upper().startswith("MMSI"):
            continue
        return raw
    return ""


def truncate_name(name: str, max_chars: int = 14) -> str:
    text = (name or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"
