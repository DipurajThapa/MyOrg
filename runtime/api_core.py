#!/usr/bin/env python3
"""What the HTTP boundary's pieces all need: its limits, its id shapes, and its refusals.

Split out so the route modules can have them without importing `api`, which imports the
route modules. Nothing here does any work.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

WEBHOOK_SECRET_SUFFIX = "_WEBHOOK_SECRET"

MAX_JSON_BYTES = 262_144
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
RESOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
LOG = logging.getLogger("myorg.api")
CONSOLE_PAGE = Path(__file__).with_name("console.html")
BOARD_PAGE = Path(__file__).with_name("kanban.html")


class BadRequest(RuntimeError):
    pass


class WebhookDenied(RuntimeError):
    """One answer for every inbound rejection, so the route leaks nothing about what exists."""


class PayloadTooLarge(RuntimeError):
    pass


class UnsupportedMedia(RuntimeError):
    pass


class TooManyRequests(RuntimeError):
    pass


class RouteNotFound(RuntimeError):
    pass
