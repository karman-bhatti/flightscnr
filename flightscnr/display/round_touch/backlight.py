"""Apply display brightness on Raspberry Pi (backlight sysfs)."""

import logging
import os

logger = logging.getLogger("flightscnr.display")

_last_pct: int | None = None


def _backlight_paths() -> list[str]:
    base = "/sys/class/backlight"
    if not os.path.isdir(base):
        return []
    paths = []
    for name in sorted(os.listdir(base)):
        bpath = os.path.join(base, name, "brightness")
        maxpath = os.path.join(base, name, "max_brightness")
        if os.path.isfile(bpath) and os.path.isfile(maxpath):
            paths.append(bpath)
    return paths


def apply_percent(percent: int) -> bool:
    global _last_pct
    pct = max(0, min(100, int(percent)))
    if _last_pct == pct:
        return True
    ok = False
    for bpath in _backlight_paths():
        try:
            maxpath = os.path.join(os.path.dirname(bpath), "max_brightness")
            with open(maxpath, encoding="utf-8") as f:
                max_val = int(f.read().strip())
            value = max(1, int(round(max_val * pct / 100)))
            with open(bpath, "w", encoding="utf-8") as f:
                f.write(str(value))
            ok = True
        except OSError as exc:
            logger.debug("Backlight write failed %s: %s", bpath, exc)
    if ok:
        _last_pct = pct
    return ok
