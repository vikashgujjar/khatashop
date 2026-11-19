from __future__ import annotations
from ..core import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import UploadError
from ..security import outlet_required
from ..services import uploads
from .helpers import company_id

bp = Blueprint("menu_pdf", __name__, url_prefix="/menu-pdf")


@bp.route("/", methods=["GET", "POST"])
@outlet_required
def index():
    error = None
    if request.method == "POST":
        try:
            uploads.save_menu_pdf(request.files.get("pdf"), company_id())
            flash("Menu PDF uploaded. Customers will now see the new menu.", "success")
            return redirect(url_for("menu_pdf.index"))
        except UploadError as exc:
            error = exc.message
            flash(exc.message, "error")
    return render_template("menu_pdf/index.html", pdf=uploads.menu_pdf_info(company_id()), error=error), (422 if error else 200)
