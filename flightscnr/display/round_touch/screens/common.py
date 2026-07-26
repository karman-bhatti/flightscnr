"""Shared helpers for round-touch flight screens."""

from display.round_touch import draw, logos, settings, theme


def format_speed(ground_speed) -> str | None:
    """Format ground speed using display units from Settings → Display."""
    if ground_speed is None or ground_speed <= 0:
        return None
    kts = float(ground_speed)
    units = settings.distance_units()
    if units == "mi":
        return f"{int(kts * 1.15078)} mph"
    if units == "nm":
        return f"{int(kts)} kts"
    return f"{int(kts * 1.852)} km/h"


def format_local_distance(dist_km: float) -> str:
    units = settings.distance_units()
    if units == "mi":
        dist_mi = dist_km / 1.609344
        if dist_mi >= 0.1:
            return f"{dist_mi:.1f} mi"
        return f"{dist_km * 3280.84:.0f} ft"
    if units == "nm":
        dist_nm = dist_km / 1.852
        if dist_nm >= 0.1:
            return f"{dist_nm:.1f} nm"
        return f"{dist_km * 3280.84:.0f} ft"
    if dist_km >= 1:
        return f"{dist_km:.1f} km"
    return f"{dist_km * 1000:.0f} m"


def draw_center_row(surface, text: str, y: int, font, color) -> int:
    h = font.get_height()
    max_w = draw.circle_half_width_at_row(y, h) * 2
    line = draw.fit_text(text, font, max_w)
    rendered = font.render(line, True, color)
    surface.blit(rendered, rendered.get_rect(midtop=(theme.CENTER_X, y)))
    return h


def draw_logo(
    surface,
    flight: dict,
    y: int,
    *,
    logo_h: int | None = None,
    allow_airline_logo: bool = True,
) -> int:
    """Aircraft/vessel photo when available; optional airline logo / vessel flag fallback."""
    if flight.get("kind") == "vessel":
        return _draw_vessel_header(surface, flight, y, logo_h=logo_h)

    photo_path = (flight.get("photo_path") or "").strip()
    if photo_path:
        from display.round_touch import aircraft_photos

        max_h = theme.s(108) if logo_h is None else max(logo_h, theme.s(72))
        # Leave side margins on the round bezel so the photo isn't clipped.
        max_w = int(theme.VISIBLE_RADIUS * 1.45)
        photo = aircraft_photos.load_photo_surface(
            photo_path,
            max_h,
            max_w=max_w,
            radius=theme.s(8),
        )
        if photo is not None:
            rect = photo.get_rect(midtop=(theme.CENTER_X, y))
            surface.blit(photo, rect)
            return y + rect.height + theme.s(3)

    if not allow_airline_logo:
        return y

    logo_h = theme.s(28) if logo_h is None else logo_h
    logo = logos.load_logo_surface(logos.icao_for_flight(flight), logo_h)
    if logo is None:
        return y
    rect = logo.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(logo, rect)
    return y + rect.height + theme.s(3)


def _draw_vessel_header(surface, flight: dict, y: int, *, logo_h: int | None = None) -> int:
    """Vessel photo (Commons) when available, else flag-of-registry."""
    from display.round_touch import flags, vessel_photos

    photo_path = (flight.get("photo_path") or "").strip()
    if photo_path:
        max_h = theme.s(110) if logo_h is None else max(logo_h, theme.s(72))
        max_w = int(theme.VISIBLE_RADIUS * 1.35)
        photo = vessel_photos.load_photo_surface(photo_path, max_h, max_w=max_w)
        if photo is not None:
            rect = photo.get_rect(midtop=(theme.CENTER_X, y))
            surface.blit(photo, rect)
            return y + rect.height + theme.s(3)

    flag_h = theme.s(36) if logo_h is None else logo_h
    iso2 = (flight.get("flag_iso2") or "").strip().lower()
    logo = flags.load_flag_surface(iso2, flag_h) if iso2 else None
    if logo is None:
        font = draw.load_font(theme.s(18), bold=True)
        label = (iso2 or flight.get("flag_country") or "??").upper()
        rendered = font.render(label, True, theme.LABEL)
        rect = rendered.get_rect(midtop=(theme.CENTER_X, y))
        surface.blit(rendered, rect)
        return y + rect.height + theme.s(3)
    rect = logo.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(logo, rect)
    return y + rect.height + theme.s(3)
