"""First-time Wi-Fi setup screen — QR to join the captive hotspot."""

from __future__ import annotations

import io
import logging

import pygame

from display.round_touch import draw, theme
from utilities import wifi_setup

logger = logging.getLogger("flightscnr.display")

_qr_cache: tuple[str, int, pygame.Surface] | None = None


def _qr_surface(payload: str, pixel_size: int) -> pygame.Surface | None:
    """Render a WIFI:/URL payload as a pygame surface (Pillow + qrcode)."""
    global _qr_cache
    if (
        _qr_cache is not None
        and _qr_cache[0] == payload
        and _qr_cache[1] == pixel_size
    ):
        return _qr_cache[2]
    try:
        import qrcode
    except ImportError:
        logger.warning("qrcode package missing — install requirements.txt")
        return None
    try:
        from PIL import Image
    except ImportError:
        Image = None

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    if Image is not None and not isinstance(img, Image.Image):
        img = img.get_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    surf = pygame.image.load(buf).convert()
    if surf.get_width() != pixel_size or surf.get_height() != pixel_size:
        surf = pygame.transform.smoothscale(surf, (pixel_size, pixel_size))
    _qr_cache = (payload, pixel_size, surf)
    return surf


def draw_wifi_setup(surface: pygame.Surface) -> None:
    """Compact vertical stack that stays inside the round visible circle."""
    draw.fill_background(surface)
    title_font = draw.load_font(theme.s(20))
    body_font = draw.load_font(theme.s(13))
    mono_font = draw.load_font(theme.s(12))

    try:
        creds = wifi_setup.get_ap_credentials()
    except Exception:
        logger.exception("Wi-Fi setup credentials unavailable")
        draw.draw_center_line(
            surface, "Wi-Fi setup unavailable", theme.CENTER_Y, title_font, theme.LABEL
        )
        return

    # Keep content inside a chord well clear of the bezel (circle half-width shrinks at top/bottom).
    top = theme.CENTER_Y - theme.s(150)
    bottom = theme.CENTER_Y + theme.s(150)
    # QR sized so plate + SSID + password + URL still fit below.
    qr_size = theme.s(168)
    qr = _qr_surface(creds.wifi_qr_payload, qr_size)

    y = top
    draw.draw_center_line(surface, "FlightScnr Pi", int(y), title_font, theme.LABEL)
    y += theme.s(20)
    draw.draw_center_line(
        surface, "Scan to connect this tracker", int(y), body_font, theme.MUTED
    )
    y += theme.s(16)

    if qr is not None:
        pad = theme.s(6)
        plate = pygame.Rect(
            theme.CENTER_X - qr_size // 2 - pad,
            int(y),
            qr_size + pad * 2,
            qr_size + pad * 2,
        )
        pygame.draw.rect(surface, (236, 240, 232), plate, border_radius=theme.s(6))
        surface.blit(qr, (plate.x + pad, plate.y + pad))
        y = plate.bottom + theme.s(12)
    else:
        draw.draw_center_line(
            surface, "QR unavailable — join Wi-Fi below", int(y), body_font, theme.HINT
        )
        y += theme.s(20)

    draw.draw_center_line(surface, creds.ssid, int(y), mono_font, theme.SWEEP)
    y += theme.s(16)
    draw.draw_center_line(
        surface, f"Password: {creds.password}", int(y), mono_font, theme.MUTED
    )
    y += theme.s(16)
    draw.draw_center_line(
        surface, "Finish setup in the portal", int(y), body_font, theme.MUTED
    )
    y += theme.s(15)
    draw.draw_center_line(
        surface, f"{creds.gateway}/wifi", int(y), mono_font, theme.LABEL
    )

    status = wifi_setup.status_message() or wifi_setup.last_error()
    if status and y + theme.s(18) < bottom:
        y += theme.s(16)
        draw.draw_center_line(surface, status[:40], int(y), body_font, theme.HINT)
