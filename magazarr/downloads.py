# -*- coding: utf-8 -*-

from magazarr.notifications import notify_error
from magazarr.quasarr_client import QuasarrClient
from magazarr.settings import Settings


def fetch_quasarr_downloads(settings: Settings) -> tuple[list[dict], list[dict]]:
    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    return client.queue(), client.history()


def sync_download_errors(
    db,
    settings: Settings,
    downloads=None,
    queue: list[dict] | None = None,
    history: list[dict] | None = None,
):
    downloads = list(downloads) if downloads is not None else list(db.downloads())
    if queue is None or history is None:
        queue, history = fetch_quasarr_downloads(settings)

    by_package = {
        str(item["package_id"]): item for item in downloads if item["package_id"]
    }
    by_title = _downloads_by_title(downloads)
    present_package_ids = set()
    present_titles = set()
    for item in queue:
        if item.get("nzo_id"):
            present_package_ids.add(str(item.get("nzo_id")))
        title_key = _title_key(_quasarr_item_title(item))
        if title_key:
            present_titles.add(title_key)

    for item in history:
        if item.get("nzo_id"):
            present_package_ids.add(str(item.get("nzo_id")))
        title_key = _title_key(_quasarr_item_title(item))
        if title_key:
            present_titles.add(title_key)
        download = by_package.get(str(item.get("nzo_id"))) or by_title.get(title_key)
        if not download:
            continue
        if _status(item) == "failed" and download["status"] != "download_error":
            reason = str(item.get("fail_message") or "Download failed")
            db.update_download_status(
                download["id"],
                "download_error",
                str(item.get("storage") or ""),
            )
            if hasattr(db, "record_skipped_download"):
                db.record_skipped_download(download, reason)
            db.record_event(
                "error",
                "download",
                f"Download failed: {item.get('name')}",
                reason,
            )
            notify_error(
                settings,
                "Download error",
                reason,
                magazine_title=download["magazine_title"],
                release_title=download["release_title"],
            )

    for download in downloads:
        package_id = str(download["package_id"] or "")
        if not package_id or download["status"] not in {"snatched", "completed"}:
            continue
        if package_id in present_package_ids:
            continue
        if _title_key(download["release_title"]) in present_titles:
            continue
        reason = "Download disappeared from Quasarr before import completed"
        db.update_download_status(download["id"], "download_error", reason)
        if hasattr(db, "record_skipped_download"):
            db.record_skipped_download(download, reason)
        db.record_event(
            "error",
            "download",
            f"Download missing from Quasarr: {download['release_title']}",
            package_id,
        )
        notify_error(
            settings,
            "Download error",
            reason,
            magazine_title=download["magazine_title"],
            release_title=download["release_title"],
        )


def _downloads_by_title(downloads):
    items = {}
    for download in downloads:
        key = _title_key(download["release_title"])
        if key and key not in items:
            items[key] = download
    return items


def _quasarr_item_title(item) -> str:
    title = str(item.get("name") or item.get("filename") or "")
    for prefix in (
        "[Downloading] ",
        "[Extracting] ",
        "[Paused] ",
        "[Linkgrabber] ",
        "[CAPTCHA not solved!] ",
    ):
        title = title.replace(prefix, "")
    return title


def _title_key(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _status(item) -> str:
    return str(item.get("status") or "").strip().lower()
