"""Optional multi-touch diagnostics for journalctl (TOUCH_DEBUG=1)."""

import logging
import os

import pygame

logger = logging.getLogger("flightscnr.display")

_ACTIVE: dict[int, tuple[float, float]] = {}


def enabled() -> bool:
    return os.environ.get("TOUCH_DEBUG", "").strip().lower() in ("1", "true", "yes")


def _logical_xy(event: pygame.event.Event) -> tuple[int, int]:
    from display.round_touch import rotation

    width = pygame.display.get_surface().get_width()
    height = pygame.display.get_surface().get_height()
    if event.type in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION):
        x = event.x * width
        y = event.y * height
    else:
        x, y = event.pos
    return rotation.to_logical(x, y)


def log_event(event: pygame.event.Event) -> None:
    if not enabled():
        return

    if event.type == pygame.FINGERDOWN:
        fid = int(event.finger_id)
        lx, ly = _logical_xy(event)
        _ACTIVE[fid] = (event.x, event.y)
        logger.info(
            "touch FINGERDOWN id=%d norm=(%.4f,%.4f) logical=(%d,%d) active=%d ids=%s",
            fid,
            event.x,
            event.y,
            lx,
            ly,
            len(_ACTIVE),
            sorted(_ACTIVE),
        )
        return

    if event.type == pygame.FINGERMOTION:
        fid = int(event.finger_id)
        if fid in _ACTIVE:
            _ACTIVE[fid] = (event.x, event.y)
        lx, ly = _logical_xy(event)
        logger.info(
            "touch FINGERMOTION id=%d norm=(%.4f,%.4f) logical=(%d,%d) active=%d",
            fid,
            event.x,
            event.y,
            lx,
            ly,
            len(_ACTIVE),
        )
        return

    if event.type == pygame.FINGERUP:
        fid = int(event.finger_id)
        _ACTIVE.pop(fid, None)
        lx, ly = _logical_xy(event)
        logger.info(
            "touch FINGERUP id=%d norm=(%.4f,%.4f) logical=(%d,%d) active=%d ids=%s",
            fid,
            event.x,
            event.y,
            lx,
            ly,
            len(_ACTIVE),
            sorted(_ACTIVE),
        )
        return

    if event.type == pygame.MOUSEBUTTONDOWN:
        lx, ly = _logical_xy(event)
        logger.info(
            "touch MOUSEBUTTONDOWN button=%d logical=(%d,%d) pos=%s",
            event.button,
            lx,
            ly,
            event.pos,
        )
        return

    if event.type == pygame.MOUSEBUTTONUP:
        lx, ly = _logical_xy(event)
        logger.info(
            "touch MOUSEBUTTONUP button=%d logical=(%d,%d) pos=%s",
            event.button,
            lx,
            ly,
            event.pos,
        )
        return

    if event.type == pygame.MOUSEMOTION and event.buttons[0]:
        lx, ly = _logical_xy(event)
        logger.info(
            "touch MOUSEMOTION logical=(%d,%d) buttons=%s",
            lx,
            ly,
            event.buttons,
        )


def log_startup() -> None:
    if not enabled():
        return
    logger.info(
        "touch debug enabled — use two fingers on screen; watch: journalctl -u flightscnr -f | grep touch"
    )
