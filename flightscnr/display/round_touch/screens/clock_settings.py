"""Clock settings — time format and auto timezone (FlightScnr clock settings)."""

import time

import pygame

from display.round_touch import draw, nav, settings, theme

FOOTER_BUTTONS = ("radar",)


def tap_footer_action(x: int, y: int) -> str | None:
    idx = nav.tap_footer_button(x, y, len(FOOTER_BUTTONS))
    if idx is None:
        return None
    return FOOTER_BUTTONS[idx]


def _rows() -> list[tuple[str, str]]:
    tz = time.tzname[0] if time.tzname else "Local"
    auto = "on" if settings.auto_timezone_enabled() else "off"
    fmt = "12 hr" if settings.use_12hr_clock() else "24 hr"
    return [
        ("Time format", fmt),
        ("Auto timezone", auto),
        ("Timezone", tz),
    ]


def row_at(x: int, y: int) -> int | None:
    body_font = draw.load_font(theme.FONT_BODY)
    top = nav.content_top_y() + theme.s(4)
    row_h = body_font.get_height() + theme.s(10)
    for i in range(len(_rows())):
        ry = top + i * row_h
        half = draw.circle_half_width_at_row(int(ry), body_font.get_height())
        rect = pygame.Rect(theme.CENTER_X - half, ry - theme.s(2), half * 2, row_h)
        if rect.collidepoint(x, y):
            return i
    return None


def apply_row(row: int) -> None:
    if row == 0:
        settings.toggle_clock_format()
    elif row == 1:
        settings.toggle_auto_timezone()
        if settings.auto_timezone_enabled():
            try:
                from config import LOCATION_HOME
                from utilities.tz_lookup import invalidate_cache, maybe_apply_auto_timezone

                invalidate_cache()
                maybe_apply_auto_timezone(LOCATION_HOME[0], LOCATION_HOME[1])
            except ImportError:
                pass


def draw_clock_settings(surface):
    draw.fill_background(surface)
    nav.draw_breadcrumb(surface, ["Radar", "Clock", "Settings"])
    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))

    title_font = draw.load_font(theme.FONT_TITLE, bold=True)
    body_font = draw.load_font(theme.FONT_BODY)
    y = nav.content_top_y() + theme.s(4)
    y = draw.draw_center_line(surface, "Clock", y, title_font, theme.SWEEP)

    row_h = body_font.get_height() + theme.s(10)
    for label, value in _rows():
        line = f"{label}: {value}"
        draw.draw_center_line(surface, line, y, body_font, theme.MUTED)
        y += row_h
