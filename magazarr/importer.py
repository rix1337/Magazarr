# -*- coding: utf-8 -*-

import shutil
from pathlib import Path

import fitz
from loguru import logger

from magazarr.notifications import notify_error, notify_import_success
from magazarr.quasarr_client import QuasarrClient
from magazarr.settings import Settings
from magazarr.utils import MONTHS, parse_issue_date, safe_filename, tokens


def import_completed(db, settings: Settings) -> list[str]:
    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    history = client.history()
    history_by_id = {str(item.get("nzo_id")): item for item in history if item.get("nzo_id")}
    history_by_name = _history_by_name(history)

    imported = []
    for download in db.snatched_downloads():
        item = _history_item_for_download(download, history_by_id, history_by_name)
        if not item:
            continue
        if _history_status(item) == "failed":
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
        if _history_status(item) != "completed":
            continue
        storage = str(item.get("storage") or "").strip()
        db.update_download_storage(download["id"], storage, "completed")
        if _import_one(db, settings, download, storage):
            _delete_imported_package(db, client, download, item)
            imported.append(download["release_title"])
        else:
            db.update_download_storage(download["id"], storage, "import_error")
    return imported


def _history_item_for_download(download, history_by_id: dict, history_by_name: dict):
    item = history_by_id.get(str(download["package_id"]))
    if item:
        return item
    return history_by_name.get(_history_name_key(download["release_title"]))


def _history_by_name(history: list[dict]) -> dict[str, dict]:
    items = {}
    for item in history:
        key = _history_name_key(item.get("name"))
        if key and key not in items:
            items[key] = item
    return items


def _history_name_key(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _history_status(item: dict) -> str:
    return str(item.get("status") or "").strip().lower()


def _delete_imported_package(db, client: QuasarrClient, download, item: dict):
    package_id = str(item.get("nzo_id") or download["package_id"] or "").strip()
    if not package_id:
        return
    title = download["release_title"]
    try:
        if client.delete_package(package_id, title):
            return
    except Exception as exc:
        message = f"Imported package cleanup failed: {exc}"
    else:
        fallback_id = str(download["package_id"] or "").strip()
        if fallback_id and fallback_id != package_id:
            try:
                if client.delete_package(fallback_id, title):
                    return
            except Exception as exc:
                message = f"Imported package cleanup failed: {exc}"
            else:
                message = f"Imported package cleanup failed: {title}"
        else:
            message = f"Imported package cleanup failed: {title}"
    logger.warning(message)
    if hasattr(db, "record_event"):
        db.record_event("warning", "import", message, title)


def _import_one(db, settings: Settings, download, storage: str) -> bool:
    storage = str(storage or "").strip()
    if not storage:
        _import_error(db, settings, "Storage path missing from Quasarr history", download)
        return False

    source = Path(storage).expanduser()

    if not source.exists():
        _import_error(db, settings, f"Storage path not found: {source}", download)
        return False
    if source.is_dir() and _is_filesystem_root(source):
        _import_error(db, settings, f"Refusing to import from filesystem root: {source}", download)
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
    if cleanup_dir and not _is_filesystem_root(cleanup_dir):
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


def _is_filesystem_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == Path(resolved.anchor)


def _library_destination(settings: Settings, download, pdf: Path) -> Path:
    year, month = _issue_path_parts(
        download["issue_key"],
        _download_value(download, "release_title"),
    )
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


def _download_value(download, key: str) -> str:
    try:
        return str(download[key] or "")
    except (KeyError, IndexError):
        return ""


def _issue_path_parts(issue_key: str, release_title: str = "") -> tuple[str, str]:
    parts = str(issue_key or "").split("-")
    if len(parts) >= 2 and len(parts[0]) == 4 and len(parts[1]) == 2:
        if parts[0].isdigit() and parts[1].isdigit():
            return parts[0], parts[1]
    issue = parse_issue_date(release_title)
    if issue and issue.value:
        return str(issue.value.year), f"{issue.value.month:02d}"
    release_parts = _issue_path_parts_from_text(release_title)
    if release_parts:
        return release_parts
    return "unknown-year", "unknown-month"


def _issue_path_parts_from_text(value: str) -> tuple[str, str] | None:
    words = tokens(value)
    for idx, word in enumerate(words):
        if word.isdigit() and len(word) == 4 and word.startswith("20"):
            for pos in (idx + 1, idx - 1):
                if 0 <= pos < len(words):
                    month = _path_month(words[pos])
                    if month:
                        return word, f"{month:02d}"
        month = MONTHS.get(word)
        if month:
            for pos in (idx + 1, idx - 1):
                if 0 <= pos < len(words):
                    year = words[pos]
                    if year.isdigit() and len(year) == 4 and year.startswith("20"):
                        return year, f"{month:02d}"
    return None


def _path_month(value: str) -> int | None:
    if not value.isdigit():
        return None
    month = int(value)
    if 1 <= month <= 12:
        return month
    return None


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
