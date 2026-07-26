"""About / boot splash — 70% splash plate with live firmware version."""

from __future__ import annotations

import json
import os

import pygame

from display.round_touch import draw, nav, theme
from version import APP_VERSION

FOOTER_BUTTONS = ("radar",)
# Compact radar control tucked toward the rim so it clears the version line.
_FOOTER_Y_OFFSET = theme.s(28)
_FOOTER_BUTTON_SIZE = theme.s(28)

_BOOT_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "..",
        "assets",
        "boot",
    )
)
_ABOUT_PATH = os.path.join(_BOOT_DIR, "about.png")
_PLATE_PATH = os.path.join(_BOOT_DIR, "brand_plate.png")
_LAYOUT_PATH = os.path.join(_BOOT_DIR, "brand_layout.json")
_SPLASH_PATH = os.path.join(_BOOT_DIR, "splash.png")

_plate_surf: pygame.Surface | None = None
_about_surf: pygame.Surface | None = None
_layout: dict | None = None


def tap_footer_action(x: int, y: int) -> str | None:
    idx = nav.tap_footer_button(
        x,
        y,
        len(FOOTER_BUTTONS),
        y_offset=_FOOTER_Y_OFFSET,
        button_size=_FOOTER_BUTTON_SIZE,
    )
    if idx is None:
        return None
    return FOOTER_BUTTONS[idx]


def _load_layout() -> dict:
    global _layout
    if _layout is not None:
        return _layout
    try:
        with open(_LAYOUT_PATH, encoding="utf-8") as fh:
            _layout = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        _layout = {"version_top": 560, "version_height": 28, "size": 720}
    return _layout


def _load_png(path: str) -> pygame.Surface | None:
    if not os.path.isfile(path):
        return None
    try:
        return pygame.image.load(path).convert()
    except pygame.error:
        return None


def _brand_surface() -> pygame.Surface | None:
    """Prefer plate (version drawn live); fall back to about.png / full splash."""
    global _plate_surf, _about_surf
    if _plate_surf is None:
        _plate_surf = _load_png(_PLATE_PATH)
    if _plate_surf is not None:
        return _plate_surf
    if _about_surf is None:
        _about_surf = _load_png(_ABOUT_PATH) or _load_png(_SPLASH_PATH)
    return _about_surf


def _blit_brand(surface, brand: pygame.Surface) -> None:
    if not theme.IS_ROUND:
        bw, bh = brand.get_size()
        scale_factor = min(theme.WIDTH / bw, theme.HEIGHT / bh)
        new_w = int(bw * scale_factor)
        new_h = int(bh * scale_factor)
        brand = pygame.transform.smoothscale(brand, (new_w, new_h))
        rect = brand.get_rect(center=(theme.WIDTH // 2, theme.HEIGHT // 2))
        surface.blit(brand, rect)
    else:
        if brand.get_size() != (theme.SIZE, theme.SIZE):
            brand = pygame.transform.smoothscale(brand, (theme.SIZE, theme.SIZE))
        surface.blit(brand, (0, 0))


def _draw_version_overlay(surface) -> None:
    layout = _load_layout()
    if not theme.IS_ROUND:
        top = theme.HEIGHT - theme.s(60)
        scale_val = theme.SIZE / float(layout.get("size") or 720)
    else:
        src_size = float(layout.get("size") or 720)
        scale_val = theme.SIZE / src_size
        top = int(layout.get("version_top", 560) * scale_val)

    font = draw.load_font(max(12, theme.s(14)), bold=True)
    label = f"v{APP_VERSION}"
    band_h = max(
        font.get_height() + theme.s(8),
        int(layout.get("version_height", 28) * scale_val) + theme.s(10) if theme.IS_ROUND else theme.s(30),
    )
    clear = pygame.Surface((theme.WIDTH if not theme.IS_ROUND else theme.SIZE, band_h))
    clear.fill((0, 0, 0))
    surface.blit(clear, (0, max(0, top - theme.s(2))))
    
    if not theme.IS_ROUND:
        rect = font.render(label, True, theme.SWEEP).get_rect(midtop=(theme.WIDTH // 2, top))
        surface.blit(font.render(label, True, theme.SWEEP), rect)
    else:
        draw.draw_center_line(surface, label, top, font, theme.SWEEP)


def draw_details(surface, boot_splash=False, scroll_offset: int = 0) -> int:
    del scroll_offset
    surface.fill((0, 0, 0))

    brand = _brand_surface()
    if brand is None:
        font = draw.load_font(theme.FONT_BODY, bold=True)
        y = theme.CENTER_Y - theme.s(20)
        y = draw.draw_center_line(surface, "FlightScnr Pi", y, font, theme.LABEL)
        draw.draw_center_line(surface, f"v{APP_VERSION}", y, font, theme.SWEEP)
    else:
        _blit_brand(surface, brand)
        _draw_version_overlay(surface)

    if boot_splash:
        return 0

    nav.draw_breadcrumb(surface, ["Radar", "About"])
    nav.draw_footer_buttons(
        surface,
        list(FOOTER_BUTTONS),
        y_offset=_FOOTER_Y_OFFSET,
        button_size=_FOOTER_BUTTON_SIZE,
    )
    return 0
