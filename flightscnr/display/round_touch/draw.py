"""Drawing helpers for round FlightScnr-style screens."""

import math
import pygame

from display.round_touch import theme


_font_cache = {}
_text_cache: dict = {}
_TEXT_CACHE_MAX = 512
_sweep_overlay: pygame.Surface | None = None
_sweep_overlay_size = (0, 0)


def render_text_cached(font: "pygame.font.Font", text: str, color) -> pygame.Surface:
    """font.render with memoization — radar tags re-render the same strings
    ~10x/s during layer rebuilds, which costs real milliseconds on a Pi."""
    key = (id(font), text, tuple(color))
    surf = _text_cache.get(key)
    if surf is None:
        if len(_text_cache) >= _TEXT_CACHE_MAX:
            _text_cache.clear()
        surf = font.render(text, True, color)
        _text_cache[key] = surf
    return surf


def load_font(size: int, bold=False) -> pygame.font.Font:
    from display.round_touch.ui_fonts import resolve_font_path

    key = (size, bold)
    if key not in _font_cache:
        path = resolve_font_path(bold=bold)
        if path:
            font = pygame.font.Font(path, size)
        else:
            fallback = pygame.font.match_font("dejavusans", bold=bold)
            if fallback:
                font = pygame.font.Font(fallback, size)
            else:
                font = pygame.font.SysFont(None, size, bold=bold)
        _font_cache[key] = font
    return _font_cache[key]


def circle_half_width_at_row(row_y: int, row_h: int) -> int:
    if not theme.IS_ROUND:
        return max(0, (theme.WIDTH - theme.s(16)) // 2)
    r = theme.VISIBLE_RADIUS
    if r <= 0 or row_h <= 0:
        return 0
    row_center = row_y + row_h // 2
    dy = row_center - theme.CENTER_Y
    if abs(dy) >= r:
        return 0
    half = math.sqrt(r * r - dy * dy)
    usable = int(half) - theme.s(6)
    return max(0, usable)


def fit_text(text: str, font: pygame.font.Font, max_width: int) -> str:
    if max_width <= 0 or not text:
        return text
    if font.size(text)[0] <= max_width:
        return text
    for n in range(len(text), 0, -1):
        trial = text[:n] + "…"
        if font.size(trial)[0] <= max_width:
            return trial
    return "…"


def draw_center_line(
    surface: pygame.Surface,
    text: str,
    y: int,
    font: pygame.font.Font,
    color,
    bg=None,
) -> int:
    h = font.get_height()
    max_w = circle_half_width_at_row(y, h) * 2
    line = fit_text(text, font, max_w)
    rendered = font.render(line, True, color, bg)
    rect = rendered.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(rendered, rect)
    return y + h + theme.s(4)


def draw_dashed_circle(surface, center, radius, color, width=2):
    """Draw a smooth dashed ring by sampling the arc every ~2 px."""
    if radius <= 0:
        return

    dash = max(1.0, float(theme.GRID_DASH_LEN))
    gap = max(1.0, float(theme.GRID_DASH_GAP))
    pattern = dash + gap
    cx, cy = center

    # Fine angular steps keep the ring circular instead of polygonal.
    steps = max(360, int(math.ceil(2 * math.pi * radius / 2.0)))
    angle_step = (2 * math.pi) / steps
    arc_step = angle_step * radius

    run = []
    arc_pos = 0.0

    def flush():
        if len(run) >= 2:
            pygame.draw.lines(surface, color, False, run, width)
        run.clear()

    for i in range(steps + 1):
        angle = i * angle_step
        in_dash = (arc_pos % pattern) < dash
        pt = (int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle)))
        if in_dash:
            run.append(pt)
        elif run:
            flush()
        arc_pos += arc_step

    flush()


def draw_dashed_line(surface, start, end, color, width=2):
    """Draw a dashed line between two points using the grid dash pattern."""
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return

    dash = max(1.0, float(theme.GRID_DASH_LEN))
    gap = max(1.0, float(theme.GRID_DASH_GAP))
    pattern = dash + gap
    dx = (x1 - x0) / length
    dy = (y1 - y0) / length

    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        if seg_end > pos:
            pygame.draw.line(
                surface,
                color,
                (int(x0 + dx * pos), int(y0 + dy * pos)),
                (int(x0 + dx * seg_end), int(y0 + dy * seg_end)),
                width,
            )
        pos += pattern


def _sweep_overlay_surface(width: int, height: int) -> pygame.Surface:
    """Reusable SRCALPHA buffer; grows as needed, cleared by the caller."""
    global _sweep_overlay, _sweep_overlay_size
    width = max(8, int(width))
    height = max(8, int(height))
    ow, oh = _sweep_overlay_size
    if _sweep_overlay is None or width > ow or height > oh:
        # Pad a bit so we don't realloc every other frame as the AABB breathes.
        nw = max(width, ow) + 16
        nh = max(height, oh) + 16
        _sweep_overlay = pygame.Surface((nw, nh), pygame.SRCALPHA)
        _sweep_overlay_size = (nw, nh)
    return _sweep_overlay


def draw_sweep_line(
    surface,
    angle_deg: float,
    color,
    width=2,
    *,
    trail_color=None,
    trail_deg: float = 30.0,
    trail_steps: int = 40,
    origin: tuple[float, float] | None = None,
    radius: float | None = None,
):
    """Soft translucent afterglow wedge — same hue, alpha falloff only.

    Matches the common “feathered radar sweep” look: bright leading edge with a
    long translucent trail that fades out, not a dark opaque pie slice.
    Clipped to the wedge AABB so the alpha blit stays cheap on the Pi.
    """
    if origin is None:
        cx, cy = float(theme.CENTER_X), float(theme.CENTER_Y)
    else:
        cx, cy = float(origin[0]), float(origin[1])
    if radius is None:
        radius = float(theme.SWEEP_RADIUS)
    else:
        radius = float(radius)
    width = max(1, int(width))
    accent = tuple(max(0, min(255, int(c))) for c in color[:3])

    def _endpoint(deg: float) -> tuple[float, float]:
        rad = math.radians(deg - 90.0)
        return (
            cx + radius * math.cos(rad),
            cy + radius * math.sin(rad),
        )

    # Axis-aligned bounds of the tip + trail arc (continuous angles, no wrap).
    edge_span = max(0.9, min(2.4, trail_deg * 0.06))
    pts = [(cx, cy), _endpoint(angle_deg + edge_span * 0.25)]
    sample_n = 10
    for i in range(sample_n + 1):
        pts.append(_endpoint(angle_deg - trail_deg * (i / sample_n)))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = 5
    x0 = int(math.floor(min(xs))) - pad
    y0 = int(math.floor(min(ys))) - pad
    x1 = int(math.ceil(max(xs))) + pad
    y1 = int(math.ceil(max(ys))) + pad
    # Clip to the destination surface so display-space draws stay in bounds.
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(surface.get_width(), x1)
    y1 = min(surface.get_height(), y1)
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)

    overlay = _sweep_overlay_surface(box_w, box_h)
    overlay.fill((0, 0, 0, 0), rect=pygame.Rect(0, 0, box_w, box_h))
    lcx = cx - x0
    lcy = cy - y0

    def _local(deg: float) -> tuple[float, float]:
        x, y = _endpoint(deg)
        return (x - x0, y - y0)

    # Wide, very faint wash — gives the soft “glow over the map” of the reference.
    wash_steps = max(10, trail_steps // 3)
    for i in range(wash_steps):
        t0 = i / wash_steps
        t1 = (i + 1) / wash_steps
        fade = (1.0 - t0) ** 1.6
        alpha = max(0, min(70, int(round(55 * fade))))
        if alpha < 3:
            continue
        pygame.draw.polygon(
            overlay,
            (*accent, alpha),
            [(lcx, lcy), _local(angle_deg - trail_deg * t0), _local(angle_deg - trail_deg * t1)],
        )

    # Denser body — fine radial slices so the trail reads as feathered rays, not bands.
    steps = max(24, int(trail_steps))
    for i in range(steps):
        t0 = i / steps
        t1 = (i + 1) / steps
        # Stay on the accent hue; only alpha dies out (reference look).
        fade = (1.0 - t0) ** 2.25
        alpha = max(0, min(200, int(round(185 * fade))))
        if alpha < 4:
            continue
        pygame.draw.polygon(
            overlay,
            (*accent, alpha),
            [(lcx, lcy), _local(angle_deg - trail_deg * t0), _local(angle_deg - trail_deg * t1)],
        )

    # Bright leading edge (narrow wedge + AA spine).
    tip_rgb = tuple(min(255, int(c) + 40) for c in accent)
    pygame.draw.polygon(
        overlay,
        (*tip_rgb, 245),
        [(lcx, lcy), _local(angle_deg + edge_span * 0.2), _local(angle_deg - edge_span * 0.85)],
    )
    tip = _local(angle_deg)
    pygame.draw.aaline(overlay, tip_rgb, (lcx, lcy), tip)
    if width >= 2:
        rad = math.radians(angle_deg - 90.0)
        nx, ny = -math.sin(rad), math.cos(rad)
        for off in (0.55, 1.05):
            pygame.draw.aaline(
                overlay,
                tip_rgb,
                (lcx + nx * off, lcy + ny * off),
                (tip[0] + nx * off, tip[1] + ny * off),
            )

    surface.blit(overlay, (x0, y0), area=pygame.Rect(0, 0, box_w, box_h))
    return pygame.Rect(x0, y0, box_w, box_h)


def draw_error(surface: pygame.Surface, message: str):
    """Show a persistent error screen instead of closing the display."""
    fill_background(surface)
    title = load_font(theme.FONT_TITLE, bold=True)
    body = load_font(theme.FONT_BODY)
    detail = load_font(theme.FONT_DETAIL)
    y = theme.CENTER_Y - theme.s(100)
    y = draw_center_line(surface, "Display Error", y, title, theme.TAG_ALT_DESCEND)
    y += theme.s(12)
    for line in _wrap_message(message, 40):
        y = draw_center_line(surface, line, y, body, theme.LABEL)
    y += theme.s(12)
    draw_center_line(surface, "Tap or swipe to return to radar", y, detail, theme.HINT)
    y += theme.s(8)
    draw_center_line(surface, "Check: journalctl -u flightscnr -f", y, detail, theme.MUTED)


def _wrap_message(text: str, width: int):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text[:width]]


def fill_background(surface: pygame.Surface):
    surface.fill(theme.BG)


def draw_timeout_ring(surface: pygame.Surface, remaining_fraction: float) -> None:
    """Countdown ring on the visible perimeter. 1.0 = full time left, 0.0 = expired."""
    remaining_fraction = max(0.0, min(1.0, remaining_fraction))
    if remaining_fraction <= 0:
        return

    cx, cy = float(theme.CENTER_X), float(theme.CENTER_Y)
    width = max(2, theme.s(3))
    r = float(theme.VISIBLE_RADIUS - width // 2 - theme.s(2))

    if remaining_fraction >= 0.999:
        pygame.draw.circle(surface, theme.SWEEP, (int(cx), int(cy)), int(round(r)), width)
        return

    start = -math.pi / 2
    sweep = 2 * math.pi * remaining_fraction
    # ~1 px along the arc so the tip crawls instead of stepping facet edges.
    steps = max(48, int(math.ceil(r * sweep)))
    points = [
        (
            cx + r * math.cos(start + sweep * i / steps),
            cy + r * math.sin(start + sweep * i / steps),
        )
        for i in range(steps + 1)
    ]
    if len(points) >= 2:
        pygame.draw.lines(surface, theme.SWEEP, False, points, width)


_bezel_overlay = None
_bezel_key = None
_bezel_rects: list[pygame.Rect] = []


def invalidate_bezel_cache() -> None:
    global _bezel_overlay, _bezel_key, _bezel_rects
    _bezel_overlay = None
    _bezel_key = None
    _bezel_rects = []


def _bezel_band_rects(size, cx: int, cy: int, radius: int) -> list[pygame.Rect]:
    """Horizontal bands covering every pixel outside the visible circle.

    Each band uses the row furthest from the centre, so the rects always reach
    at least as far in as the circle does anywhere in that band.
    """
    width, height = size
    bands = 12
    # Overlap inwards to absorb rounding between sqrt() here and pygame's
    # rasterized circle. Overlapping is free: the overlay is transparent there.
    pad = 3
    rects: list[pygame.Rect] = []
    for i in range(bands):
        y0 = height * i // bands
        y1 = height * (i + 1) // bands
        dy = max(abs(y0 - cy), abs(y1 - 1 - cy))
        inside = radius * radius - dy * dy
        if inside <= 0:
            rects.append(pygame.Rect(0, y0, width, y1 - y0))
            continue
        half = math.sqrt(inside)
        left = max(0, int(math.floor(cx - half)) + pad)
        right = min(width, int(math.ceil(cx + half)) - pad)
        if left > 0:
            rects.append(pygame.Rect(0, y0, left, y1 - y0))
        if right < width:
            rects.append(pygame.Rect(right, y0, width - right, y1 - y0))
    return rects


def apply_round_bezel(surface: pygame.Surface):
    """Mask everything outside the round visible area.

    Blits only the border band. A full-screen alpha blit is ~4.3 ms/frame on
    the Pi, which alone eats a quarter of the sweep's 16 ms frame budget.
    """
    global _bezel_overlay, _bezel_key, _bezel_rects
    size = surface.get_size()
    key = (size, theme.CENTER_X, theme.CENTER_Y, theme.VISIBLE_RADIUS, theme.BG)
    if _bezel_overlay is None or _bezel_key != key:
        _bezel_overlay = pygame.Surface(size, pygame.SRCALPHA)
        _bezel_overlay.fill((*theme.BG, 255))
        pygame.draw.circle(
            _bezel_overlay,
            (0, 0, 0, 0),
            (theme.CENTER_X, theme.CENTER_Y),
            theme.VISIBLE_RADIUS,
        )
        _bezel_rects = _bezel_band_rects(
            size, theme.CENTER_X, theme.CENTER_Y, theme.VISIBLE_RADIUS
        )
        _bezel_key = key
    for rect in _bezel_rects:
        surface.blit(_bezel_overlay, rect.topleft, area=rect)
