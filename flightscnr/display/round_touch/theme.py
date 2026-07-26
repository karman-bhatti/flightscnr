"""FlightScnr visual theme — support both round and rectangular display configurations."""

REF_SIZE = 390

try:
    from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_ROUND
except ImportError:
    DISPLAY_WIDTH = 720
    DISPLAY_HEIGHT = 720
    DISPLAY_ROUND = True

WIDTH = DISPLAY_WIDTH
HEIGHT = DISPLAY_HEIGHT
IS_ROUND = DISPLAY_ROUND
SIZE = min(WIDTH, HEIGHT)
SCALE = SIZE / REF_SIZE
CENTER_X = WIDTH // 2
CENTER_Y = HEIGHT // 2
BEZEL_INSET = 2
VISIBLE_RADIUS = SIZE // 2 - BEZEL_INSET
GRID_OUTER_RADIUS = VISIBLE_RADIUS - 2
CARDINAL_NORTH_OFFSET_Y = 10
CARDINAL_SOUTH_OFFSET_Y = 10
CARDINAL_DIAGONAL_INSET = 14
SCALE_GAP_FROM_OUTER_RING = 12
SCALE_GAP_OUTER_RING_KM = 20
GRID_DASH_LEN = 7
GRID_DASH_GAP = 15
AIRCRAFT_ICON_RADIUS = 15
AIRCRAFT_LABEL_GAP = 3
BEYOND_RING_MARGIN = 3
SWEEP_RADIUS = VISIBLE_RADIUS - BEYOND_RING_MARGIN
TAP_PICK_RADIUS = 36
FONT_TITLE = 28
FONT_BODY = 22
FONT_DETAIL = 18
FONT_CLOCK = 64
FONT_CLOCK_AMPM = 36
FONT_CARDINAL = 15
FONT_CARDINAL_DIAG = 15
FONT_TAG = 12
FONT_TAG_SUB = 11


def s(value: float) -> int:
    return max(1, int(round(value * SCALE)))


def _apply_framebuffer_dimensions(width: int, height: int) -> None:
    """Recompute layout constants for a draw buffer."""
    global SIZE, WIDTH, HEIGHT, IS_ROUND, SCALE, CENTER_X, CENTER_Y, BEZEL_INSET, VISIBLE_RADIUS
    global GRID_OUTER_RADIUS, CARDINAL_NORTH_OFFSET_Y, CARDINAL_SOUTH_OFFSET_Y
    global CARDINAL_DIAGONAL_INSET, SCALE_GAP_FROM_OUTER_RING, SCALE_GAP_OUTER_RING_KM
    global GRID_DASH_LEN, GRID_DASH_GAP, AIRCRAFT_ICON_RADIUS, AIRCRAFT_LABEL_GAP
    global BEYOND_RING_MARGIN, SWEEP_RADIUS, TAP_PICK_RADIUS
    global FONT_TITLE, FONT_BODY, FONT_DETAIL, FONT_CLOCK, FONT_CLOCK_AMPM
    global FONT_CARDINAL, FONT_CARDINAL_DIAG, FONT_TAG, FONT_TAG_SUB

    WIDTH = width
    HEIGHT = height
    
    try:
        from config import DISPLAY_ROUND
        IS_ROUND = DISPLAY_ROUND
    except ImportError:
        IS_ROUND = (WIDTH == HEIGHT)

    SIZE = min(WIDTH, HEIGHT)
    SCALE = SIZE / REF_SIZE
    CENTER_X = WIDTH // 2
    CENTER_Y = HEIGHT // 2
    # Thin rim so sweep/tags are not clipped by the physical round bezel.
    BEZEL_INSET = max(2, s(3))
    VISIBLE_RADIUS = SIZE // 2 - BEZEL_INSET
    GRID_OUTER_RADIUS = VISIBLE_RADIUS - 2
    CARDINAL_NORTH_OFFSET_Y = s(10)
    CARDINAL_SOUTH_OFFSET_Y = s(10)
    CARDINAL_DIAGONAL_INSET = s(14)
    SCALE_GAP_FROM_OUTER_RING = s(12)
    SCALE_GAP_OUTER_RING_KM = s(20)
    GRID_DASH_LEN = s(7)
    GRID_DASH_GAP = s(15)
    AIRCRAFT_ICON_RADIUS = s(15)
    AIRCRAFT_LABEL_GAP = s(3)
    BEYOND_RING_MARGIN = s(3)
    SWEEP_RADIUS = VISIBLE_RADIUS - BEYOND_RING_MARGIN
    TAP_PICK_RADIUS = s(36)
    FONT_TITLE = s(28)
    FONT_BODY = s(22)
    FONT_DETAIL = s(18)
    FONT_CLOCK = s(64)
    FONT_CLOCK_AMPM = s(36)
    FONT_CARDINAL = s(15)
    FONT_CARDINAL_DIAG = s(15)
    FONT_TAG = s(12)
    FONT_TAG_SUB = s(11)


def set_framebuffer_side(side: int) -> None:
    set_framebuffer_dimensions(side, side)


def set_framebuffer_dimensions(width: int, height: int) -> None:
    """Match layout to the physical display (call after pygame set_mode)."""
    width = int(width)
    height = int(height)
    if width < 100 or height < 100:
        raise ValueError(f"framebuffer dimensions too small: {width}x{height}")
    global WIDTH, HEIGHT
    if width == WIDTH and height == HEIGHT:
        return
    _apply_framebuffer_dimensions(width, height)
    try:
        from display.round_touch import draw

        draw.invalidate_bezel_cache()
    except ImportError:
        pass


_apply_framebuffer_dimensions(DISPLAY_WIDTH, DISPLAY_HEIGHT)

# Colors (FlightScnr radar_theme.h)
BG = (2, 15, 3)
GRID = (16, 100, 32)
PAGE_DOT_INACTIVE = (8, 42, 14)
CROSSHAIR = GRID
SWEEP = (48, 255, 96)
SWEEP_TRAIL = (12, 72, 28)
LABEL = (255, 255, 255)
AIRCRAFT = (255, 180, 40)
# Unmapped ICAO type / blank type — darker so known traffic stays punchy.
AIRCRAFT_UNKNOWN = (150, 100, 28)
TAG_TYPE = (255, 200, 0)
TAG_ALT_ASCEND = (0, 255, 255)
TAG_ALT_DESCEND = (255, 0, 255)
HINT = (120, 140, 160)
MUTED = (180, 200, 220)
ROUTE = (100, 220, 255)
LIVE = (56, 168, 255)
LIVE_DIM = (28, 84, 128)
# Parked / slow AIS vessels (dimmer than AIRCRAFT when hierarchy is on).
VESSEL_PARKED = (120, 90, 40)
VESSEL_MOVING = AIRCRAFT
ALERT_MILITARY = (255, 40, 40)   # red — military tracks
ALERT_OTHER = (56, 160, 255)     # blue — emergency squawk / watch list
ALERT_EMERGENCY = ALERT_OTHER
ALERT_FLASH = (255, 80, 80)      # bright red pulse (military rim / icons)
ALERT_FLASH_OTHER = (120, 200, 255)  # bright blue pulse
ALERT_WATCH = ALERT_OTHER

SCALE_LABEL_BEARING_DEG = 245.5
RING_COUNT = 3
SWEEP_PERIOD_MS = 6000
# Target ~60fps for the sweep; achieved FPS may be lower on Pi full redraws.
SWEEP_FRAME_MS = 16


def in_visible_circle(x: float, y: float, margin: float = 0) -> bool:
    dx = x - CENTER_X
    dy = y - CENTER_Y
    limit = VISIBLE_RADIUS - margin
    return dx * dx + dy * dy <= limit * limit
