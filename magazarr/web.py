# -*- coding: utf-8 -*-

import json
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from bottle import Bottle, HTTPError, redirect, request, response, static_file
from loguru import logger

from magazarr.downloads import fetch_quasarr_downloads, sync_download_errors
from magazarr.importer import import_completed
from magazarr.notifications import notify_download_started, send_pushover
from magazarr.opds import handle_opds
from magazarr.quasarr_client import QuasarrClient
from magazarr.search import search_all, search_magazine
from magazarr.settings import SettingsStore
from magazarr.ui import (
    asset_root,
    dashboard,
    issue_viewer_page,
    library_page,
    page,
    settings_modal,  # noqa: F401 - re-exported for callers rendering the form
)
from magazarr.utils import clean_release_title, pdf_mime


def create_app(settings_store: SettingsStore, db, automation=None):
    app = Bottle()
    bottle_error_handler = app.default_error_handler

    def error_handler(error):
        # The web UI submits forms with fetch and shows the message as a toast.
        if "application/json" in request.headers.get("Accept", ""):
            error.set_header("Content-Type", "application/json")
            return json.dumps({"error": str(error.body or error.status_line)})
        return bottle_error_handler(error)

    app.default_error_handler = error_handler

    @app.get("/")
    def index():
        settings = settings_store.load()
        state = dashboard_payload(db, counts={}, timed_out=False)
        return page(
            "Magazarr", dashboard(settings, state), script="app.js", page_class="app"
        )

    @app.get("/static/<filename:path>")
    def static_assets(filename):
        result = static_file(filename, root=asset_root())
        if request.query.get("v"):
            # Versioned asset URLs change on every release.
            result.set_header("Cache-Control", "public, max-age=604800, immutable")
        return result

    @app.post("/settings")
    def save_settings():
        try:
            settings_store.update_from_form(request.forms)
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc
        redirect("/")

    @app.post("/notifications/pushover/test")
    def test_pushover():
        try:
            settings = settings_store.update_from_form(request.forms)
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc
        if not settings.pushover_api_token or not settings.pushover_user_key:
            raise HTTPError(
                400, "Configure a Pushover API token and user or group key first"
            )
        if not send_pushover(
            settings,
            "Magazarr Notification Test",
            "Pushover notifications are configured.",
            image_path=asset_root() / "magazarr-icon.png",
            silent=False,
        ):
            raise HTTPError(502, "Failed to send Pushover test notification")
        redirect("/")

    @app.post("/magazines")
    def add_magazine():
        db.add_magazine(str(request.forms.get("title", "")))
        redirect("/")

    @app.post("/magazines/<magazine_id:int>/active")
    def active_magazine(magazine_id):
        db.set_magazine_active(magazine_id, request.forms.get("active") == "on")
        redirect("/")

    @app.post("/magazines/<magazine_id:int>/delete")
    def delete_magazine(magazine_id):
        for issue in db.issues(limit=-1, magazine_id=magazine_id):
            path = Path(issue["file_path"])
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                db.record_event(
                    "error",
                    "library",
                    f"Failed to delete issue file: {path}",
                    str(exc),
                )
                raise HTTPError(500, str(exc)) from exc
        db.delete_magazine(magazine_id)
        redirect("/")

    @app.post("/magazines/<magazine_id:int>/search")
    def search_one(magazine_id):
        settings = settings_store.load()
        magazine = db.magazine_by_id(magazine_id)
        if not magazine:
            raise HTTPError(404, "Magazine not found")
        try:
            if automation:
                automation.search_magazine(magazine)
            else:
                search_magazine(db, settings, magazine)
        except Exception as exc:
            logger.exception(exc)
            raise HTTPError(500, str(exc)) from exc
        redirect("/")

    @app.post("/api/magazines/<magazine_id:int>/search")
    def start_search_one(magazine_id):
        if not db.magazine_by_id(magazine_id):
            raise HTTPError(404, "Magazine not found")
        if automation:
            return json_response(
                {"job_id": automation.start_search_magazine_job(magazine_id)}
            )
        settings = settings_store.load()
        downloads = search_magazine(db, settings, db.magazine_by_id(magazine_id))
        return json_response({"status": "done", "downloads": len(downloads)})

    @app.post("/api/magazines/search-all")
    def start_search_all():
        if automation:
            return json_response({"job_id": automation.start_search_all_job()})
        settings = settings_store.load()
        summary = search_all(db, settings)
        return json_response({"status": "done", "downloads": sum(summary.values())})

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        if not automation:
            raise HTTPError(404, "Jobs unavailable")
        job = automation.job_status(job_id)
        if not job:
            raise HTTPError(404, "Job not found")
        return json_response(job)

    @app.post("/magazines/<magazine_id:int>/skipped/clear")
    def clear_magazine_skipped(magazine_id):
        db.clear_skipped_releases(magazine_id)
        redirect("/")

    @app.post("/magazines/<magazine_id:int>/errors/delete")
    def delete_magazine_errors(magazine_id):
        if not db.magazine_by_id(magazine_id):
            raise HTTPError(404, "Magazine not found")
        db.delete_import_errors(magazine_id)
        db.record_event(
            "info",
            "download",
            "Deleted error downloads",
            f"magazine_id={magazine_id}",
        )
        redirect("/")

    @app.get("/api/magazines/<magazine_id:int>/items/<kind>")
    def magazine_items_api(magazine_id, kind):
        if not db.magazine_by_id(magazine_id):
            raise HTTPError(404, "Magazine not found")
        limit = max(1, min(100, _int(request.query.get("limit"), 25)))
        offset = max(0, _int(request.query.get("offset"), 0))
        search = str(request.query.get("search", "")).strip()
        settings = settings_store.load()
        if kind == "downloaded":
            rows = [
                issue_payload(row)
                for row in db.issues(
                    limit=limit,
                    offset=offset,
                    search=search,
                    magazine_id=magazine_id,
                )
            ]
            total = db.issue_count(search, magazine_id=magazine_id)
        elif kind == "skipped":
            rows, total = skipped_and_error_rows(
                db,
                magazine_id=magazine_id,
                limit=limit,
                offset=offset,
                search=search,
            )
        elif kind == "skips":
            rows = [
                skipped_payload(row)
                for row in db.skipped_releases(
                    limit=limit,
                    offset=offset,
                    search=search,
                    magazine_id=magazine_id,
                )
            ]
            total = db.skipped_release_count(search, magazine_id=magazine_id)
        elif kind == "errors":
            rows = [
                download_payload(row)
                for row in db.import_errors(
                    limit=limit,
                    offset=offset,
                    magazine_id=magazine_id,
                    search=search,
                )
            ]
            total = db.import_error_count(magazine_id, search)
        elif kind == "downloading":
            payload = download_status_payload(db, settings, magazine_id=magazine_id)
            rows = payload["active"]
            total = len(rows)
            rows = rows[offset : offset + limit]
        else:
            raise HTTPError(404, "Unknown item kind")
        return json_response(
            {
                "kind": kind,
                "rows": rows,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/downloads")
    def downloads_api():
        settings = settings_store.load()
        return json_response(download_status_payload(db, settings))

    @app.get("/api/dashboard")
    def dashboard_api():
        settings = settings_store.load()
        counts, timed_out = active_download_counts(db, settings)
        return json_response(dashboard_payload(db, counts, timed_out))

    @app.post("/downloads/<download_id:int>/delete-package")
    def delete_download_package(download_id):
        settings = settings_store.load()
        _delete_download_package(db, settings, download_id, "Deleted manually")
        redirect("/")

    @app.post("/downloads/<download_id:int>/skip")
    def skip_download_package(download_id):
        settings = settings_store.load()
        _delete_download_package(db, settings, download_id, "Skipped manually")
        redirect("/")

    @app.post("/downloads/<download_id:int>/import-now")
    def import_download_now(download_id):
        if not _download_by_id(db, download_id):
            raise HTTPError(404, "Download not found")
        settings = settings_store.load()
        try:
            if automation:
                automation.import_completed()
            else:
                import_completed(db, settings)
        except Exception as exc:
            logger.exception(exc)
            if hasattr(db, "record_event"):
                db.record_event("error", "import", str(exc))
            raise HTTPError(500, str(exc)) from exc
        redirect("/")

    @app.get("/issues/<issue_id:int>/view")
    def view_issue(issue_id):
        issue = _issue_or_404(db, issue_id)
        _issue_path_or_404(issue)
        return issue_viewer_page(issue)

    @app.get("/issues/<issue_id:int>/file")
    def issue_file(issue_id):
        issue = _issue_or_404(db, issue_id)
        path = _issue_path_or_404(issue)
        result = static_file(
            path.name, root=str(path.parent), mimetype=pdf_mime(str(path))
        )
        result.set_header(
            "Content-Disposition",
            f'inline; filename="{_header_filename(path.name)}"',
        )
        return result

    @app.post("/issues/<issue_id:int>/delete")
    def delete_issue(issue_id):
        issue = db.delete_issue(issue_id)
        if issue:
            path = Path(issue["file_path"])
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                db.record_event(
                    "error",
                    "library",
                    f"Failed to delete issue file: {path}",
                    str(exc),
                )
                raise HTTPError(500, str(exc)) from exc
            db.record_event(
                "info",
                "library",
                "Deleted issue",
                f"{issue['magazine_title']} - {issue['issue_key']}",
            )
        redirect("/")

    @app.post("/api/issues/bulk-delete")
    def bulk_delete_issues():
        data = request.json
        if not data or "issue_ids" not in data:
            raise HTTPError(400, "Missing issue_ids")
        issue_ids = data["issue_ids"]
        deleted = 0
        for issue_id in issue_ids:
            issue = db.delete_issue(int(issue_id))
            if issue:
                path = Path(issue["file_path"])
                try:
                    if path.exists() and path.is_file():
                        path.unlink()
                except OSError:
                    pass
                deleted += 1
        db.record_event("info", "library", f"Bulk deleted {deleted} issue(s)", "")
        return json_response({"deleted": deleted})

    @app.get("/library")
    def library_view():
        magazines = db.magazines()
        return page("Library", library_page(magazines), script="library.js")

    @app.get("/api/library/issues")
    def library_issues_api():
        magazine_id = _int(request.query.get("magazine_id"), None)
        limit = max(1, min(500, _int(request.query.get("limit"), 500)))
        offset = max(0, _int(request.query.get("offset"), 0))
        search = str(request.query.get("search", "")).strip()
        rows = db.issues(
            limit=limit,
            offset=offset,
            search=search,
            magazine_id=magazine_id,
        )
        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "magazine_id": row["magazine_id"],
                    "magazine_title": row["magazine_title"],
                    "issue_key": clean_release_title(row["issue_key"]),
                    "release_title": clean_release_title(row["release_title"]),
                    "cover_url": f"/opds?cmd=Cover&issueid={row['id']}&size=thumb",
                    "size_bytes": row["size_bytes"],
                    "acquired_at": row["acquired_at"],
                }
            )
        return json_response(
            {
                "items": items,
                "total": db.issue_count(search, magazine_id=magazine_id),
                "offset": offset,
                "limit": limit,
            }
        )

    @app.post("/magazines/<magazine_id:int>/blacklist")
    def add_blacklist_term(magazine_id):
        db.add_blacklist_term(magazine_id, str(request.forms.get("term", "")))
        redirect("/")

    @app.post("/blacklist/<term_id:int>/delete")
    def delete_blacklist_term(term_id):
        db.delete_blacklist_term(term_id)
        redirect("/")

    @app.get("/api/magazines/<magazine_id:int>/duplicates")
    def magazine_duplicates_api(magazine_id):
        if not db.magazine_by_id(magazine_id):
            raise HTTPError(404, "Magazine not found")
        groups = db.find_duplicates(magazine_id)
        payload = []
        for group in groups:
            payload.append([issue_payload(row) for row in group])
        return json_response(
            {"groups": payload, "total": sum(len(g) - 1 for g in groups)}
        )

    @app.post("/api/magazines/<magazine_id:int>/duplicates/clear")
    def clear_magazine_duplicates(magazine_id):
        if not db.magazine_by_id(magazine_id):
            raise HTTPError(404, "Magazine not found")
        deleted = db.delete_duplicate_issues(magazine_id)
        db.record_event(
            "info",
            "library",
            f"Cleared {deleted} duplicate(s)",
            f"magazine_id={magazine_id}",
        )
        return json_response({"deleted": deleted})

    @app.post("/downloads/<download_id:int>/retry-import")
    def retry_import(download_id):
        settings = settings_store.load()
        try:
            if automation:
                automation.retry_import_error(download_id)
            else:
                _retry_error_download(db, settings, download_id)
        except Exception as exc:
            logger.exception(exc)
            if hasattr(db, "record_event"):
                db.record_event("error", "import", str(exc))
            raise HTTPError(500, str(exc)) from exc
        redirect("/")

    @app.post("/skipped/<skip_id:int>/unskip")
    def unskip_release(skip_id):
        settings = settings_store.load()
        try:
            if automation:
                automation.unskip_release(skip_id)
            else:
                _unskip_release(db, settings, skip_id)
        except Exception as exc:
            logger.exception(exc)
            if hasattr(db, "record_event"):
                db.record_event("error", "search", str(exc))
            raise HTTPError(500, str(exc)) from exc
        redirect("/")

    @app.get("/opds")
    def opds():
        return handle_opds(db, settings_store.load())

    return app


def dashboard_payload(db, counts: dict[int, int], timed_out: bool) -> dict:
    blacklist = (
        db.blacklist_terms_by_magazine()
        if hasattr(db, "blacklist_terms_by_magazine")
        else {}
    )
    magazines = []
    for mag in db.magazines():
        magazines.append(
            {
                "id": mag["id"],
                "title": mag["title"],
                "active": bool(mag["active"]),
                "issue_count": mag["issue_count"],
                "last_issue_at": mag["last_issue_at"],
                "last_search_at": mag["last_search_at"],
                "cover_url": (
                    f"/opds?cmd=Cover&issueid={mag['latest_issue_id']}&size=thumb"
                    if mag["latest_issue_id"]
                    else ""
                ),
                "blacklist": [
                    {"id": term["id"], "term": term["term"]}
                    for term in blacklist.get(mag["id"], [])
                ],
                "downloading": counts.get(mag["id"], 0),
                "skipped": db.skipped_release_count(magazine_id=mag["id"]),
                "errors": db.import_error_count(mag["id"]),
            }
        )
    return {"magazines": magazines, "quasarr_timeout": timed_out}


def active_download_counts(db, settings) -> tuple[dict[int, int], bool]:
    downloads = list(db.downloads())
    if not _has_pending_downloads(downloads):
        return {}, False
    by_package, by_title = _download_match_indexes(downloads)
    counts: dict[int, int] = {}
    timed_out = False
    try:
        from magazarr.quasarr_client import SHORT_TIMEOUT

        queue, history = fetch_quasarr_downloads(settings, timeout=SHORT_TIMEOUT)
        sync_download_errors(db, settings, downloads, queue, history)
    except Exception:
        timed_out = True
        for row in downloads:
            if row["status"] in {"snatched", "completed"}:
                magazine_id = row["magazine_id"]
                counts[magazine_id] = counts.get(magazine_id, 0) + 1
        return counts, timed_out

    seen = set()
    for item in [*queue, *history]:
        download = _download_for_quasarr_item(item, by_package, by_title)
        if not download:
            continue
        download_id = download["id"]
        if download_id in seen:
            continue
        seen.add(download_id)
        magazine_id = download["magazine_id"]
        counts[magazine_id] = counts.get(magazine_id, 0) + 1
    return counts, timed_out


def reason_label(reason: str) -> str:
    return {
        "too_small": "Too small",
        "too_large": "Too large",
        "title_mismatch": "Title mismatch",
        "issue_unparsed": "Issue not detected",
        "outside_past_days": "Outside past-days window",
        "duplicate": "Already snatched/imported",
        "blacklisted": "Blacklisted",
    }.get(reason, reason)


def json_response(data):
    response.content_type = "application/json"
    return json.dumps(data)


def issue_payload(row):
    return {
        "id": row["id"],
        "magazine_title": row["magazine_title"],
        "issue_key": clean_release_title(row["issue_key"]),
        "release_title": clean_release_title(row["release_title"]),
        "file_path": row["file_path"],
        "cover_url": f"/opds?cmd=Cover&issueid={row['id']}&size=thumb",
        "view_url": f"/issues/{row['id']}/view",
        "file_url": f"/issues/{row['id']}/file",
        "acquired_at": row["acquired_at"],
        "size_bytes": row["size_bytes"],
    }


def skipped_payload(row):
    return {
        "item_kind": "skipped",
        "id": row["id"],
        "magazine_title": row["magazine_title"],
        "release_title": row["release_title"],
        "reason": reason_label(row["reason"]),
        "updated_at": row["updated_at"],
    }


def download_payload(row):
    return {
        "item_kind": "error",
        "id": row["id"],
        "magazine_title": row["magazine_title"],
        "release_title": row["release_title"],
        "issue_key": row["issue_key"],
        "status": row["status"],
        "package_id": row["package_id"],
        "updated_at": row["updated_at"],
    }


def skipped_and_error_rows(
    db,
    magazine_id: int,
    limit: int,
    offset: int,
    search: str = "",
) -> tuple[list[dict], int]:
    error_total = db.import_error_count(magazine_id, search)
    skipped_total = db.skipped_release_count(search, magazine_id=magazine_id)
    rows = []
    if offset < error_total:
        error_limit = min(limit, error_total - offset)
        rows.extend(
            download_payload(row)
            for row in db.import_errors(
                limit=error_limit,
                offset=offset,
                magazine_id=magazine_id,
                search=search,
            )
        )
    skipped_limit = limit - len(rows)
    if skipped_limit > 0:
        skipped_offset = max(0, offset - error_total)
        rows.extend(
            skipped_payload(row)
            for row in db.skipped_releases(
                limit=skipped_limit,
                offset=skipped_offset,
                search=search,
                magazine_id=magazine_id,
            )
        )
    return rows, error_total + skipped_total


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def download_status_payload(db, settings, magazine_id: int | None = None):
    downloads = list(db.downloads(magazine_id))
    if not _has_pending_downloads(downloads):
        return {"active": [], "error": "", "quasarr_url": quasarr_public_url(settings)}
    by_package, by_title = _download_match_indexes(downloads)
    try:
        from magazarr.quasarr_client import SHORT_TIMEOUT

        queue, history = fetch_quasarr_downloads(settings, timeout=SHORT_TIMEOUT)
        sync_download_errors(db, settings, downloads, queue, history)
    except Exception as exc:
        return {
            "active": [],
            "error": str(exc),
            "quasarr_url": quasarr_public_url(settings),
        }

    active = []
    for item in [*queue, *history]:
        download = _download_for_quasarr_item(item, by_package, by_title)
        if not download:
            continue
        active.append(download_card_payload(settings, item, download))

    return {"active": active, "error": "", "quasarr_url": quasarr_public_url(settings)}


def _download_still_in_quasarr(queue, history, by_package, by_title) -> bool:
    for item in [*queue, *history]:
        if _download_for_quasarr_item(item, by_package, by_title):
            return True
    return False


def _has_pending_downloads(downloads) -> bool:
    return any(row["status"] in {"snatched", "completed"} for row in downloads)


def _download_by_id(db, download_id: int):
    return next(
        (item for item in db.downloads() if int(item["id"]) == int(download_id)),
        None,
    )


def _download_match_indexes(downloads):
    by_package = {
        str(item["package_id"]): item for item in downloads if item["package_id"]
    }
    by_title = {}
    for item in downloads:
        key = _download_title_key(item["release_title"])
        if key and key not in by_title:
            by_title[key] = item
    return by_package, by_title


def _download_for_quasarr_item(item, by_package, by_title):
    download = by_package.get(str(item.get("nzo_id")))
    if download:
        return download
    return by_title.get(_download_title_key(item.get("name") or item.get("filename")))


def _delete_download_package(
    db,
    settings,
    download_id: int,
    skipped_reason: str = "Deleted manually",
):
    download = _download_by_id(db, download_id)
    if not download:
        raise HTTPError(404, "Download not found")
    # Error rows may never have reached Quasarr; there is nothing to delete there.
    if download["package_id"]:
        client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
        if not client.delete_package(download["package_id"], download["release_title"]):
            queue, history = client.queue(), client.history()
            by_package, by_title = _download_match_indexes([download])
            if _download_still_in_quasarr(queue, history, by_package, by_title):
                raise HTTPError(500, "Quasarr package delete failed")
    db.update_download_status(download_id, "deleted")
    if hasattr(db, "record_skipped_download"):
        db.record_skipped_download(download, skipped_reason)
    db.record_event(
        "info",
        "download",
        (
            "Deleted download package"
            if skipped_reason == "Deleted manually"
            else "Skipped download package"
        ),
        download["release_title"],
    )


def _download_title_key(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def download_card_payload(settings, item, download):
    package_id = str(item.get("nzo_id") or "")
    title = str(item.get("filename") or item.get("name") or download["release_title"])
    for prefix in (
        "[Downloading] ",
        "[Extracting] ",
        "[Paused] ",
        "[Linkgrabber] ",
        "[CAPTCHA not solved!] ",
    ):
        title = title.replace(prefix, "")
    return {
        "id": download["id"],
        "package_id": package_id,
        "title": title,
        "magazine": download["magazine_title"],
        "status": item.get("status") or item.get("type") or "Downloading",
        "type": item.get("type") or "",
        "percentage": item.get("percentage") or 0,
        "timeleft": item.get("timeleft") or "",
        "mb": item.get("mb") or 0,
        "mbleft": item.get("mbleft") or 0,
        "captcha_url": urljoin(
            quasarr_public_url(settings).rstrip("/") + "/",
            f"captcha?package_id={quote_plus(package_id)}",
        ),
    }


def quasarr_public_url(settings) -> str:
    return (settings.quasarr_external_url or settings.quasarr_url).rstrip("/")


def _unskip_release(db, settings, skip_id: int):
    skipped = db.skipped_release_by_id(skip_id)
    if not skipped:
        return None
    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    package_ids = client.add_url(
        skipped["download_url"], settings.quasarr_download_category
    )
    package_id = package_ids[0] if package_ids else None
    issue_key = db.record_manual_download(
        skipped["magazine_id"],
        skipped["issue_key"] or f"manual-{skip_id}",
        skipped["release_title"],
        skipped["download_url"],
        skipped["size_bytes"],
        package_id,
    )
    db.mark_skipped_release_unskipped(skip_id, package_id)
    reference = notify_download_started(
        settings,
        skipped["magazine_title"],
        skipped["release_title"],
        package_id,
    )
    download_id = db.download_id_by_issue_key(skipped["magazine_id"], issue_key)
    if reference and download_id:
        db.update_download_notifications(download_id, {"discord": reference})
    db.record_event(
        "info",
        "search",
        "Unskipped release sent to Quasarr",
        f"{skipped['magazine_title']} - {skipped['release_title']} ({issue_key})",
    )
    return package_id


def _retry_error_download(db, settings, download_id: int):
    download = _download_by_id(db, download_id)
    if not download:
        raise HTTPError(404, "Download not found")
    if download["status"] == "download_error":
        client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
        package_ids = client.add_url(
            download["download_url"],
            settings.quasarr_download_category,
        )
        if not package_ids:
            raise HTTPError(502, "Quasarr did not return a package id")
        package_id = package_ids[0] if package_ids else None
        retried = db.retry_download_error(download_id, package_id)
        if not retried:
            raise HTTPError(404, "Download error not found")
        reference = notify_download_started(
            settings,
            download["magazine_title"],
            download["release_title"],
            package_id,
        )
        if reference:
            db.update_download_notifications(download_id, {"discord": reference})
        db.record_event(
            "info",
            "download",
            "Retried download error",
            download["release_title"],
        )
        return package_id
    db.reset_import_error(download_id)
    import_completed(db, settings)
    return None


def _issue_or_404(db, issue_id: int):
    issue = db.issue_by_id(issue_id)
    if not issue:
        raise HTTPError(404, "Issue not found")
    return issue


def _issue_path_or_404(issue) -> Path:
    path = Path(issue["file_path"])
    if not path.exists() or not path.is_file():
        raise HTTPError(404, "Issue file missing")
    return path


def _header_filename(filename: str) -> str:
    return filename.replace("\\", "_").replace('"', "_")
