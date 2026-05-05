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
    present_package_ids = set()
    for item in queue:
        if item.get("nzo_id"):
            present_package_ids.add(str(item.get("nzo_id")))

    for item in history:
        if item.get("nzo_id"):
            present_package_ids.add(str(item.get("nzo_id")))
        download = by_package.get(str(item.get("nzo_id")))
        if not download:
            continue
        if item.get("status") == "Failed" and download["status"] != "download_error":
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
