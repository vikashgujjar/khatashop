"""Old app-facing API URLs (customer app, driver app, website, attendance machine, payments).

Every old URL keeps working at the same path on this server:
  * endpoints rebuilt in Python are served here directly (NATIVE below);
  * every other old URL is forwarded unchanged to the existing Laravel backend
    (db_config.json -> legacy_api.base_url) and its response is returned as-is.
That lets the apps move to this server without any URL change, while the remaining
endpoints are moved to Python one by one.

Logged-in customer/driver endpoints stay forwarded for now: Laravel's logout blacklists
tokens in its own cache, so checking those tokens here could accept logged-out phones.
"""
from __future__ import annotations
import http.client
import logging
import math
from pathlib import Path
from urllib.parse import urlsplit

from ..core import Blueprint, Response, current_app, jsonify, request

from .. import db
from ..services import uploads

log = logging.getLogger("khatashop.mobile_api")
bp = Blueprint("mobile_api", __name__)
legacy = Blueprint("legacy_api", __name__)

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "content-encoding", "content-length", "host"}


def _public_url() -> str:
    return current_app.config["KS"]["legacy_api"]["public_base_url"].rstrip("/")


def _exists(folder: str, name: str) -> bool:
    try:
        return (uploads.base_dir() / folder / name).is_file()
    except OSError:
        return False


# ------------------------------------------------------------------ native --

@bp.route("/api/mb_api/menu_category", methods=["POST"])
def mb_menu_category():
    # The PHP endpoint is a placeholder that always returns this.
    return jsonify({"1": "test"})


@bp.route("/api/get_lastid_attendances", methods=["GET"])
def last_attendance_id():
    """Attendance machine sync: last punch id received from machine 101 (plain text, like PHP)."""
    value = db.fetch_value("SELECT punch_id FROM attendances1 WHERE machine_no = 101 ORDER BY id DESC LIMIT 1", None, 0)
    return Response(str(value), mimetype="text/html")


@bp.route("/api/getmenuitems", methods=["POST"])
def website_menu_items():
    """Website menu: every category of a restaurant with its items split into two columns."""
    base = _public_url()
    company = request.values.get("company_detail_id") or (request.get_json(silent=True) or {}).get("company_detail_id")
    dummy = f"{base}/uploads/item_img/skpdummy.jpeg"
    categories = db.fetch_all("SELECT menu_category_id, menu_category_name FROM menu_category WHERE company_detail_id = %s "
                              "ORDER BY `order` ASC", (company,))
    sections = []
    for cat in categories:
        items = [{
            "item_id": it["item_id"], "name": it["item_name"], "gst": it["item_gst"], "price": it["rate"],
            "description": it["item_description"],
            "image": f"{base}/uploads/item_img/{it['item_id']}.jpg" if _exists("item_img", f"{it['item_id']}.jpg") else dummy,
        } for it in db.fetch_all("SELECT item_id, item_name, item_gst, rate, item_description FROM menu WHERE menu_category_id = %s",
                                 (cat["menu_category_id"],))]
        size = math.ceil(len(items) / 2)
        left, right = (items[:size], items[size:]) if size else ([], [])
        image = (f"{base}/uploads/category_img/{cat['menu_category_id']}.jpg"
                 if _exists("category_img", f"{cat['menu_category_id']}.jpg") else dummy)
        sections.append({"pdf": f"{base}/uploads/category_img/menupdf.pdf", "title": cat["menu_category_name"], "leftItems": left,
                         "rightItems": {"topImage": image, "items": right, "bottomImage": image}})
    return jsonify(sections)


NATIVE = {"POST /api/mb_api/menu_category", "GET /api/get_lastid_attendances", "POST /api/getmenuitems"}


# ------------------------------------------------------------- forwarding --

def _forward(path: str):
    cfg = current_app.config["KS"]["legacy_api"]
    if not cfg.get("enabled", True):
        return jsonify({"success": False, "status_code": 503, "message": "This endpoint has not been moved to the new server yet."}), 503
    base = urlsplit(cfg["base_url"].rstrip("/"))
    target = (base.path or "") + "/" + path.lstrip("/")
    query = request.query_string.decode("latin-1")
    if query:
        target += "?" + query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["X-Forwarded-For"] = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    headers["X-Forwarded-Host"] = request.host
    body = request.get_data()
    conn_class = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
    conn = conn_class(base.hostname, base.port, timeout=cfg.get("timeout_seconds", 30))
    try:
        conn.request(request.method, target, body=body if body or request.method not in ("GET", "HEAD") else None,
                     headers=headers)
        upstream = conn.getresponse()
        content = upstream.read()
        status = upstream.status
        out_headers = [(k, v) for k, v in upstream.getheaders() if k.lower() not in HOP_HEADERS]
    except (OSError, http.client.HTTPException) as exc:
        log.error("Legacy API %s %s failed: %s", request.method, path, exc)
        return jsonify({"success": False, "status_code": 502, "message": "The service is temporarily unavailable. Please try again."}), 502
    finally:
        conn.close()
    log.debug("Forwarded %s /%s -> %s", request.method, path, status)
    return Response(content, status=status, headers=out_headers)


METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@legacy.route("/api/<path:subpath>", methods=METHODS)
def forward_api(subpath):
    return _forward("api/" + subpath)


@legacy.route("/payment/<path:subpath>", methods=METHODS)
def forward_payment(subpath):
    return _forward("payment/" + subpath)


@legacy.route("/unauthenticated", methods=METHODS)
def forward_unauthenticated():
    return _forward("unauthenticated")
