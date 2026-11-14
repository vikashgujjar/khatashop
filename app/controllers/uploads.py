import re

from flask import Blueprint, abort, g, send_from_directory

from .. import db

from ..security import login_required
from ..services import uploads

bp = Blueprint("uploads", __name__)


def _owner_ok(filename: str) -> bool:
    """Each restaurant may open only its own photos, logo and menu PDF."""
    user = g.user or {}
    if user.get("role") == "super":
        return True
    cid = user.get("company_id")
    m = re.fullmatch(r"(category_img|item_img|logo|menu_pdf)/(\d+)\.(jpg|png|pdf)", filename)
    if filename == "category_img/menupdf.pdf":
        return uploads._legacy_company() == cid
    if not m or not cid:
        return False
    folder, record_id = m.group(1), int(m.group(2))
    if folder in ("logo", "menu_pdf"):
        return record_id == cid
    if folder == "category_img":
        sql = "SELECT company_detail_id AS c FROM menu_category WHERE menu_category_id = %s"
    else:
        sql = "SELECT MC.company_detail_id AS c FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id WHERE M.item_id = %s"
    return db.fetch_value(sql, (record_id,)) == cid


@bp.route("/uploads/<path:filename>")
@login_required
def file(filename):
    if not _owner_ok(filename):
        abort(404)
    # send_from_directory rejects paths that escape the uploads folder.
    return send_from_directory(uploads.base_dir(), filename, max_age=3600)
