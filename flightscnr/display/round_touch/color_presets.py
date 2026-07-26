"""Radar accent themes — from FlightScnr include/ui/radar_accent.cpp."""

from __future__ import annotations

# Preset accents (Orange removed so Theme + RGB sliders fit the round screen).
THEME_NAMES = ("Red", "Yellow", "Green", "White")

# grid, sweep, sweep_trail, label — background & aircraft stay fixed (radar_theme.h).
THEMES: tuple[dict[str, tuple[int, int, int]], ...] = (
    {
        "grid": (200, 24, 24),
        "sweep": (255, 64, 64),
        "sweep_trail": (72, 8, 8),
        "label": (255, 80, 80),
    },
    {
        "grid": (180, 180, 0),
        "sweep": (255, 255, 64),
        "sweep_trail": (72, 72, 0),
        "label": (255, 255, 128),
    },
    {
        # "grid": (48, 220, 80),
        # "crosshair": (48, 220, 80),
        # "sweep": (80, 255, 112),
        # "sweep_trail": (20, 90, 40),
        # "label": (160, 255, 180),
        "grid": (0, 255, 0),
        "crosshair": (0, 255, 0),
        "sweep": (0, 255, 0),
        "sweep_trail": (0, 255, 0),
        "label": (0, 255, 0),
    },
    {
        "grid": (160, 160, 160),
        "sweep": (255, 255, 255),
        "sweep_trail": (80, 80, 80),
        "label": (255, 255, 255),
    },
)

DEFAULT_THEME_INDEX = 2  # Green
DEFAULT_CUSTOM_RGB = THEMES[DEFAULT_THEME_INDEX]["sweep"]

# Bump when THEME_NAMES / index order changes so saved indices can remapped.
THEME_PALETTE_V = 2
# Former Orange accent (removed); kept for migration of saved theme_index == 2.
_LEGACY_ORANGE_RGB = (255, 180, 48)

THEME_COUNT = len(THEME_NAMES)


def _clamp_byte(value) -> int:
    try:
        return max(0, min(255, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def normalize_rgb(rgb) -> tuple[int, int, int]:
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        return (_clamp_byte(rgb[0]), _clamp_byte(rgb[1]), _clamp_byte(rgb[2]))
    return tuple(DEFAULT_CUSTOM_RGB)


def palette_from_rgb(r: int, g: int, b: int) -> dict[str, tuple[int, int, int]]:
    """Build grid/sweep/trail/label from a single accent RGB."""
    base = normalize_rgb((r, g, b))

    def scale(factor: float) -> tuple[int, int, int]:
        return tuple(max(0, min(255, int(round(c * factor)))) for c in base)

    # Slight lift toward white so labels stay readable on the dark radar bg.
    label = tuple(min(255, int(round(c + (255 - c) * 0.18))) for c in base)
    return {
        "grid": scale(0.72),
        "crosshair": scale(0.72),
        "sweep": base,
        "sweep_trail": scale(0.28),
        "label": label,
    }


def migrate_theme_index(state: dict) -> bool:
    """Remap theme_index after Orange was dropped. Returns True if state changed."""
    try:
        version = int(state.get("theme_palette_v", 1))
    except (TypeError, ValueError):
        version = 1
    if version >= THEME_PALETTE_V:
        # Still clamp in case of hand-edited settings.
        try:
            idx = int(state.get("theme_index", DEFAULT_THEME_INDEX))
        except (TypeError, ValueError):
            idx = DEFAULT_THEME_INDEX
        clamped = max(0, min(idx, THEME_COUNT - 1))
        if clamped != idx:
            state["theme_index"] = clamped
            return True
        return False

    try:
        old = int(state.get("theme_index", DEFAULT_THEME_INDEX))
    except (TypeError, ValueError):
        old = DEFAULT_THEME_INDEX

    # Old list: Red, Yellow, Orange, Green, White
    if old == 2:
        # Keep the saved Orange as a custom color.
        state["theme_custom"] = True
        state["custom_theme_rgb"] = list(_LEGACY_ORANGE_RGB)
        state["theme_index"] = DEFAULT_THEME_INDEX
    elif old == 3:
        state["theme_index"] = 2  # Green
    elif old == 4:
        state["theme_index"] = 3  # White
    elif old < 0 or old >= THEME_COUNT:
        state["theme_index"] = DEFAULT_THEME_INDEX
    # old 0/1 unchanged

    state["theme_palette_v"] = THEME_PALETTE_V
    return True
