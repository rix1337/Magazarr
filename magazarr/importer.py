# -*- coding: utf-8 -*-

import shutil
from pathlib import Path

import fitz
from loguru import logger

from magazarr.notifications import notify_error, notify_import_success
from magazarr.quasarr_client import QuasarrClient
from magazarr.settings import Settings
from magazarr.utils import is_relative_to, safe_filename


def import_completed(db, settings: Settings) -> list[str]:
    if not settings.import_root:
        _import_error(db, settings, "Import root missing; not importing")
        return []

    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    history = client.history()
    history_by_id = {str(item.get("nzo_id")): item for item in history if item.get("nzo_id")}

    imported = []
    for download in db.snatched_downloads():
        item = history_by_id.get(str(download["package_id"]))
        if not item:
            continue
        if item.get("status") == "Failed":
            db.update_download_storage(
                download["id"],
                str(item.get("storage") or "").strip(),
                "download_error",
            )
            _import_error(
                db,
                settings,
                f"Download failed: {item.get('fail_message') or item.get('name')}",
                download,
            )
            continue
        if item.get("status") != "Completed":
            continue
        storage = str(item.get("storage") or "").strip()
        db.update_download_storage(download["id"], storage, "completed")
        if _import_one(db, settings, download, storage):
            imported.append(download["release_title"])
        else:
            db.update_download_storage(download["id"], storage, "import_error")
    return imported


def _import_one(db, settings: Settings, download, storage: str) -> bool:
    source = Path(storage).expanduser()
    import_root = Path(settings.import_root).expanduser()

    if not source.exists():
        _import_error(db, settings, f"Storage path not found: {source}", download)
        return False
    if not is_relative_to(source, import_root):
        _import_error(
            db,
            settings,
            f"Storage outside import root; not deleting/importing: {source}",
            download,
        )
        return False

    pdf = _source_pdf(source)
    if not pdf:
        _import_error(db, settings, f"No PDF found in {source}", download)
        return False

    pdf_magazine_title = _pdf_title(pdf) or download["magazine_title"]
    dest = _unique_path(_library_destination(settings, download, pdf))
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = pdf.stat().st_size
    shutil.move(str(pdf), str(dest))

    db.record_issue(
        download["magazine_id"],
        download["issue_key"],
        download["release_title"],
        str(dest),
        size,
        download["package_id"],
    )
    cleanup_dir = source if source.is_dir() else None
    if cleanup_dir and cleanup_dir.resolve() != import_root.resolve():
        shutil.rmtree(cleanup_dir)
    logger.info(f"Imported {dest}")
    notify_import_success(
        settings,
        pdf_magazine_title,
        download["release_title"],
        download["issue_key"],
        dest,
    )
    return True


def _library_destination(settings: Settings, download, pdf: Path) -> Path:
    year, month = _issue_path_parts(download["issue_key"])
    title = safe_filename(download["magazine_title"])
    filename = safe_filename(
        f"{download['issue_key']} - {pdf.stem}"
    ) + pdf.suffix.lower()
    return (
        Path(settings.library_dir).expanduser()
        / "magazines"
        / str(download["magazine_id"])
        / year
        / month
        / title
        / filename
    )


def _issue_path_parts(issue_key: str) -> tuple[str, str]:
    parts = str(issue_key or "").split("-")
    if len(parts) >= 2 and len(parts[0]) == 4 and len(parts[1]) == 2:
        if parts[0].isdigit() and parts[1].isdigit():
            return parts[0], parts[1]
    return "unknown-year", "unknown-month"


def _import_error(db, settings: Settings, message: str, download=None):
    logger.warning(message)
    details = ""
    if download is not None:
        details = f"{download['magazine_title']} - {download['release_title']}"
        if hasattr(db, "record_skipped_download"):
            db.record_skipped_download(download, message)
        notify_error(
            settings,
            "Import error",
            message,
            magazine_title=download["magazine_title"],
            release_title=download["release_title"],
        )
    else:
        notify_error(settings, "Import error", message)
    if hasattr(db, "record_event"):
        db.record_event("warning", "import", message, details)


def _largest_pdf(folder: Path) -> Path | None:
    pdfs = [item for item in folder.rglob("*.pdf") if item.is_file()]
    if not pdfs:
        return None
    return max(pdfs, key=lambda item: item.stat().st_size)


def _source_pdf(source: Path) -> Path | None:
    if source.is_file():
        return source if source.suffix.lower() == ".pdf" else None
    if source.is_dir():
        return _largest_pdf(source)
    return None


def _pdf_title(pdf: Path) -> str:
    try:
        with fitz.open(pdf) as doc:
            return str(doc.metadata.get("title") or "").strip()
    except Exception:
        return ""


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(2, 1000):
        candidate = path.with_name(f"{stem} ({idx}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(path)
