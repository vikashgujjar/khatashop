"""Application exceptions and the global error handlers.

Controllers raise AppError subclasses for expected problems (bad input,
missing records, permission issues). Anything else is caught by the
catch-all handler, logged with a short error id, and shown to the user as a
friendly page (or JSON for API calls) without leaking internals.
"""
from __future__ import annotations
import logging
import uuid

import mysql.connector
from .core import flash, jsonify, redirect, render_template, request, url_for
from .core.http import HTTPException, RequestEntityTooLarge

log = logging.getLogger("khatashop.errors")


class AppError(Exception):
    status_code = 400
    title = "Something went wrong"

    def __init__(self, message: str, status_code: int | None = None, errors: dict | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.errors = errors or {}


class ValidationError(AppError):
    status_code = 422
    title = "Please check the details"


class NotFoundError(AppError):
    status_code = 404
    title = "Not found"


class AuthError(AppError):
    status_code = 401
    title = "Please sign in"


class ForbiddenError(AppError):
    status_code = 403
    title = "Not allowed"


class DatabaseError(AppError):
    status_code = 503
    title = "Database unavailable"


class UploadError(ValidationError):
    title = "Upload failed"


def wants_json() -> bool:
    if request.path.startswith("/api/") or request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]


def _respond(status: int, title: str, message: str, errors: dict | None = None, error_id: str | None = None):
    if wants_json():
        body = {"ok": False, "error": message, "title": title}
        if errors:
            body["errors"] = errors
        if error_id:
            body["error_id"] = error_id
        return jsonify(body), status
    return (
        render_template("errors/error.html", status=status, title=title, message=message, error_id=error_id),
        status,
    )


def register_error_handlers(app):
    @app.errorhandler(AuthError)
    def handle_auth(exc: AuthError):
        if wants_json():
            return _respond(exc.status_code, exc.title, exc.message)
        flash(exc.message, "warning")
        return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))

    from .security import OutletNotSelected

    @app.errorhandler(OutletNotSelected)
    def handle_no_outlet(exc):
        message = "Choose a restaurant first, then open its screens."
        if wants_json():
            return _respond(409, "Choose a restaurant", message)
        flash(message, "info")
        return redirect(url_for("admin.restaurants"))

    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        log.log(level, "%s %s -> %s: %s", request.method, request.path, exc.status_code, exc.message)
        return _respond(exc.status_code, exc.title, exc.message, exc.errors)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(exc):
        limit = app.config["KS"]["uploads"]["max_upload_mb"]
        return _respond(413, "File too large", f"The uploaded file is larger than the {limit} MB limit.")

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        messages = {
            404: "The page you are looking for does not exist.",
            405: "That action is not allowed on this page.",
        }
        return _respond(exc.code or 500, exc.name, messages.get(exc.code, exc.description or exc.name))

    @app.errorhandler(mysql.connector.errors.OperationalError)
    @app.errorhandler(mysql.connector.errors.InterfaceError)
    def handle_db_down(exc):
        error_id = uuid.uuid4().hex[:8]
        log.error("[%s] Database connection problem on %s %s: %s", error_id, request.method, request.path, exc)
        return _respond(
            503,
            DatabaseError.title,
            "The database could not be reached. Please make sure MySQL is running and try again.",
            error_id=error_id,
        )

    @app.errorhandler(mysql.connector.Error)
    def handle_db_error(exc):
        error_id = uuid.uuid4().hex[:8]
        log.exception("[%s] Database error on %s %s", error_id, request.method, request.path)
        return _respond(500, "Database error", "The database rejected the request. The error has been logged.", error_id=error_id)

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        error_id = uuid.uuid4().hex[:8]
        log.exception("[%s] Unhandled error on %s %s", error_id, request.method, request.path)
        return _respond(500, "Unexpected error", "Something went wrong on our side. The error has been logged.", error_id=error_id)
