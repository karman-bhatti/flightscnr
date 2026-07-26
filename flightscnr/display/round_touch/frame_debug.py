"""Shared frame-timing collector (FLIGHTSCNR_FRAME_DEBUG=1).

Two views of the same samples:
- 2s aggregates ("stages") for the periodic [frame] summary line.
- per-frame-gap marks so a single slow frame can be attributed to the loop
  section / render stage that actually consumed the time.
"""

import os

ENABLED = os.environ.get("FLIGHTSCNR_FRAME_DEBUG", "").lower() in ("1", "true", "yes")

_stages: dict[str, list] = {}
_gap: dict[str, float] = {}


def stage(name: str, seconds: float) -> None:
    slot = _stages.get(name)
    if slot is None:
        _stages[name] = [seconds, 1]
    else:
        slot[0] += seconds
        slot[1] += 1
    _gap[name] = _gap.get(name, 0.0) + seconds


def drain_gap() -> dict[str, float]:
    """Marks accumulated since the previous presented frame."""
    out = dict(_gap)
    _gap.clear()
    return out


def drain_stages() -> dict[str, list]:
    """2s aggregates: name -> [total_seconds, count]."""
    out = dict(_stages)
    _stages.clear()
    return out
