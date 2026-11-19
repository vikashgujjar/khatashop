"""KhataShop's own web layer, built only on the Python standard library.

Replaces Flask / Werkzeug / Jinja2 / markupsafe (framework + templates), waitress (server),
openpyxl (Excel), bcrypt (passwords), Pillow (image checks) and requests (API forwarding).
The names match what the rest of the app imports, e.g. `from ..core import request, url_for`.
"""
from __future__ import annotations

from .framework import (App, Blueprint, LocalProxy, Session, TestClient, current_app, flash, g, get_flashed_messages,
                        has_app_context, has_request_context, jsonify, make_response, redirect, render_template, request,
                        send_file, send_from_directory, session, url_for)
from .http import (BadRequest, Forbidden, HTTPException, MethodNotAllowed, MultiDict, NotFound, Request,
                   RequestEntityTooLarge, Response, abort)
from .markup import Markup, escape

Flask = App  # the app object is created as App(__name__)

__all__ = [
    "App", "Blueprint", "Flask", "LocalProxy", "Session", "TestClient", "current_app", "flash", "g", "get_flashed_messages",
    "has_app_context", "has_request_context", "jsonify", "make_response", "redirect", "render_template", "request",
    "send_file", "send_from_directory", "session", "url_for", "BadRequest", "Forbidden", "HTTPException",
    "MethodNotAllowed", "MultiDict", "NotFound", "Request", "RequestEntityTooLarge", "Response", "abort", "Markup", "escape",
]
