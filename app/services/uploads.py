"""Saving and locating uploaded images and the menu PDF.

Uses the same layout as the PHP app so both can share one uploads folder:
  category_img/<category id>.jpg
  item_img/<item id>.jpg
  category_img/menupdf.pdf
"""
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from flask import current_app, url_for
from PIL import Image, UnidentifiedImageError

from ..errors import UploadError

log = logging.getLogger("khatashop.uploads")

CATEGORY_FOLDER = "category_img"
ITEM_FOLDER = "item_img"
MENU_PDF = ("category_img", "menupdf.pdf")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_IMAGE_SIDE = 1200


def base_dir() -> Path:
    return Path(current_app.config["KS"]["uploads"]["base_dir"])


def _target(folder: str, filename: str) -> Path:
    path = (base_dir() / folder / filename).resolve()
    if base_dir().resolve() not in path.parents:
        raise UploadError("Invalid upload location.")
    return path


def _has_file(file_storage) -> bool:
    return bool(file_storage and file_storage.filename)


def _write_atomically(target: Path, writer) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".uploading")
        writer(tmp)
        os.replace(tmp, target)
    except UploadError:
        raise
    except OSError as exc:
        log.exception("Could not write upload to %s", target)
        raise UploadError(f"The file could not be saved on the server ({exc.strerror or exc}).") from exc


def save_image(file_storage, folder: str, record_id: int) -> bool:
    """Validates, shrinks and stores an image as <id>.jpg. Returns False if no file was chosen."""
    if not _has_file(file_storage):
        return False
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise UploadError("Photos must be JPG, PNG, WEBP, GIF or BMP images.", errors={"image": "Unsupported file type."})
    try:
        image = Image.open(file_storage.stream)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadError("That file is not a readable image.", errors={"image": "Not a valid image."}) from exc
    if image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    _write_atomically(_target(folder, f"{record_id}.jpg"), lambda p: image.convert("RGB").save(p, "JPEG", quality=85, optimize=True))
    return True


def delete_image(folder: str, record_id: int) -> None:
    path = _target(folder, f"{record_id}.jpg")
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not delete image %s", path, exc_info=True)


def image_url(folder: str, record_id: int) -> str | None:
    path = base_dir() / folder / f"{record_id}.jpg"
    try:
        version = int(path.stat().st_mtime)
    except OSError:
        return None
    return url_for("uploads.file", filename=f"{folder}/{record_id}.jpg", v=version)


LOGO_FOLDER = "logo"


def save_logo(file_storage, company_id: int) -> bool:
    """Stores a restaurant logo as logo/<id>.png (the PHP app's location), keeping transparency."""
    if not _has_file(file_storage):
        return False
    if Path(file_storage.filename).suffix.lower() not in IMAGE_EXTENSIONS:
        raise UploadError("Logos must be PNG, JPG, WEBP or GIF images.", errors={"logo": "Unsupported file type."})
    try:
        image = Image.open(file_storage.stream)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadError("That logo file is not a readable image.", errors={"logo": "Not a valid image."}) from exc
    image = image.convert("RGBA")
    image.thumbnail((800, 400))
    _write_atomically(_target(LOGO_FOLDER, f"{company_id}.png"), lambda p: image.save(p, "PNG", optimize=True))
    return True


def delete_logo(company_id: int) -> None:
    try:
        _target(LOGO_FOLDER, f"{company_id}.png").unlink(missing_ok=True)
    except OSError:
        log.warning("Could not delete logo for company %s", company_id, exc_info=True)


def logo_url(company_id: int | None) -> str | None:
    """The restaurant's own logo if one was uploaded, else None (callers fall back to the brand logo)."""
    if not company_id:
        return None
    path = base_dir() / LOGO_FOLDER / f"{company_id}.png"
    try:
        version = int(path.stat().st_mtime)
    except OSError:
        return None
    return url_for("uploads.file", filename=f"{LOGO_FOLDER}/{company_id}.png", v=version)


MENU_PDF_FOLDER = "menu_pdf"


def menu_pdf_path(company_id: int | None = None) -> Path:
    """The restaurant's own menu PDF (menu_pdf/<id>.pdf); without an id, the old shared file."""
    if company_id is None:
        return _target(*MENU_PDF)
    return _target(MENU_PDF_FOLDER, f"{int(company_id)}.pdf")


def _legacy_company() -> int | None:
    value = current_app.config["KS"]["uploads"].get("legacy_menu_pdf_company")
    return int(value) if value not in (None, "") else None


def menu_pdf_info(company_id: int) -> dict | None:
    """Info about this restaurant's menu PDF. The restaurant set as legacy_menu_pdf_company
    also falls back to the old shared file until it uploads its own."""
    candidates = [(menu_pdf_path(company_id), f"{MENU_PDF_FOLDER}/{int(company_id)}.pdf", False)]
    if _legacy_company() == company_id:
        candidates.append((menu_pdf_path(None), "/".join(MENU_PDF), True))
    for path, rel, legacy in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        return {
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime),
            "url": url_for("uploads.file", filename=rel, v=int(stat.st_mtime)),
            "legacy": legacy,
        }
    return None


def save_menu_pdf(file_storage, company_id: int) -> None:
    if not _has_file(file_storage):
        raise UploadError("Choose a PDF file to upload.", errors={"pdf": "No file selected."})
    if Path(file_storage.filename).suffix.lower() != ".pdf":
        raise UploadError("Only PDF files can be uploaded.", errors={"pdf": "File must end in .pdf"})
    head = file_storage.stream.read(5)
    file_storage.stream.seek(0)
    if head != b"%PDF-":
        raise UploadError("That file is not a valid PDF document.", errors={"pdf": "Not a PDF file."})

    target = menu_pdf_path(company_id)
    if target.exists():
        try:
            shutil.copy2(target, target.with_name(f"{int(company_id)}.previous.pdf"))
        except OSError:
            log.warning("Could not keep a backup of the previous menu PDF", exc_info=True)
    _write_atomically(target, lambda p: file_storage.save(p))
    if _legacy_company() == company_id:
        # keep the old website's shared file (category_img/menupdf.pdf) in step for this restaurant
        try:
            shutil.copy2(target, menu_pdf_path(None))
        except OSError:
            log.warning("Could not update the shared legacy menupdf.pdf", exc_info=True)
    log.info("Menu PDF of restaurant %s replaced (%s)", company_id, file_storage.filename)
