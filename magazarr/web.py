# -*- coding: utf-8 -*-

import html
import json
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from bottle import Bottle, HTTPError, redirect, request, response, static_file
from loguru import logger

from magazarr.downloads import fetch_quasarr_downloads, sync_download_errors
from magazarr.importer import import_completed
from magazarr.notifications import notify_download_started
from magazarr.opds import handle_opds
from magazarr.quasarr_client import QuasarrClient
from magazarr.search import search_all, search_magazine
from magazarr.settings import SettingsStore
from magazarr.utils import pdf_mime
from magazarr.version import __version__


def asset_root() -> Path:
    packaged = Path(__file__).resolve().parent / "assets"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parent.parent / "assets"


def create_app(settings_store: SettingsStore, db, automation=None):
    app = Bottle()

    @app.get("/")
    def index():
        settings = settings_store.load()
        return page("Magazarr", dashboard(settings, db))

    @app.get("/static/<filename:path>")
    def static_assets(filename):
        return static_file(filename, root=asset_root())

    @app.post("/settings")
    def save_settings():
        settings_store.update_from_form(request.forms)
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
            return json_response({"job_id": automation.start_search_magazine_job(magazine_id)})
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
        return json_response(
            {
                "magazines": magazine_rows(
                    db.magazines(),
                    db.blacklist_terms_by_magazine()
                    if hasattr(db, "blacklist_terms_by_magazine")
                    else {},
                    db,
                    active_download_counts(db, settings),
                )
            }
        )

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
        result = static_file(path.name, root=str(path.parent), mimetype=pdf_mime(str(path)))
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

    @app.post("/magazines/<magazine_id:int>/blacklist")
    def add_blacklist_term(magazine_id):
        db.add_blacklist_term(magazine_id, str(request.forms.get("term", "")))
        redirect("/")

    @app.post("/blacklist/<term_id:int>/delete")
    def delete_blacklist_term(term_id):
        db.delete_blacklist_term(term_id)
        redirect("/")

    @app.post("/downloads/<download_id:int>/retry-import")
    def retry_import(download_id):
        settings = settings_store.load()
        try:
            if automation:
                automation.retry_import_error(download_id)
            else:
                db.reset_import_error(download_id)
                import_completed(db, settings)
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


def dashboard(settings, db) -> str:
    magazines = db.magazines()
    blacklist = (
        db.blacklist_terms_by_magazine()
        if hasattr(db, "blacklist_terms_by_magazine")
        else {}
    )
    downloading_counts = active_download_counts(db, settings)
    return f"""
    <section class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <a class="brand-link" href="https://github.com/rix1337/Magazarr" target="_blank" rel="noreferrer">
            <img class="brand-logo" src="/static/magazarr-logo.png" alt="Magazarr">
            <h1 class="sr-only">Magazarr</h1>
          </a>
          <span>v{__version__}</span>
        </div>
        <nav class="top-actions">
          <button class="secondary" type="button" data-open-downloads>Downloads</button>
          <a class="button-link secondary" href="/opds">OPDS</a>
          <button class="secondary" type="button" data-open-settings>Settings</button>
        </nav>
      </div>
    </section>

    <main class="layout">
      <section class="panel">
        <div class="panel-head">
          <h2>Magazines</h2>
          <div class="toolbar">
            <form class="inline" method="post" action="/magazines">
              <input name="title" placeholder="Magazine title" required>
              <button type="submit">Add</button>
            </form>
            <form class="inline js-job-form" method="post" action="/api/magazines/search-all">
              <button type="submit">Search All</button>
            </form>
          </div>
        </div>
        <div class="mag-list">
          {magazine_rows(magazines, blacklist, db, downloading_counts)}
        </div>
      </section>
    </main>

    {downloads_modal()}
    {settings_modal(settings)}
    {magazine_modal()}
    {job_modal()}
    {confirm_modal()}
    """


def input_row(label: str, name: str, value: str, input_type: str = "text") -> str:
    return (
        f'<label><span>{html.escape(label)}</span>'
        f'<input type="{input_type}" name="{name}" value="{html.escape(value or "")}"></label>'
    )


def magazine_rows(magazines, blacklist, db, downloading_counts=None) -> str:
    rows = []
    downloading_counts = downloading_counts or {}
    for mag in magazines:
        blacklisted_terms = blacklist.get(mag["id"], [])
        skipped = db.skipped_release_count(magazine_id=mag["id"])
        errors = db.import_error_count(mag["id"])
        skipped_and_errors = skipped + errors
        downloading = downloading_counts.get(mag["id"], 0)
        rows.append(
            f"""
            <article class="mag-card">
              <div class="mag-cover-block">
                <button class="cover-button" type="button" data-open-mag-items
                  data-kind="downloaded" data-magazine-id="{mag['id']}"
                  data-title="Downloaded" data-magazine-title="{html.escape(mag['title'])}"
                  aria-label="Downloaded {mag['issue_count']}">
                  <span class="mag-cover">{magazine_cover(mag)}</span>
                  <span class="downloaded-entry"><span class="downloaded-label">Downloaded</span><strong>{mag['issue_count']}</strong></span>
                </button>
              </div>
              <div class="mag-main">
                <div class="mag-title">
                  <h3>{html.escape(mag["title"])}</h3>
                </div>
                <div class="chips">{blacklist_chips(blacklisted_terms)}</div>
                <form class="chip-form" method="post" action="/magazines/{mag['id']}/blacklist">
                  <input name="term" placeholder="Blacklist term">
                  <button type="submit">Add</button>
                </form>
                <div class="mag-stats">
                  {mag_stat_button("Downloading", downloading, mag["id"], "downloading", mag["title"])}
                  {mag_stat_button("Skipped / Errors", skipped_and_errors, mag["id"], "skipped", mag["title"])}
                </div>
                <div class="card-actions">
                  <form class="js-job-form" method="post" action="/api/magazines/{mag['id']}/search"><button>Search</button></form>
                  <form method="post" action="/magazines/{mag['id']}/active">
                    <button class="secondary active-toggle {'is-active' if mag['active'] else 'is-inactive'}" type="submit" name="active" value="{'' if mag['active'] else 'on'}">{'Disable' if mag['active'] else 'Enable'}</button>
                  </form>
                  <form method="post" action="/magazines/{mag['id']}/delete" data-confirm="Delete this magazine and all local records?"><button class="secondary">Delete</button></form>
                </div>
              </div>
            </article>
            """
        )
    return "".join(rows) or '<div class="empty">No magazines.</div>'


def active_download_counts(db, settings) -> dict[int, int]:
    downloads = list(db.downloads())
    if not _has_pending_downloads(downloads):
        return {}
    by_package, by_title = _download_match_indexes(downloads)
    counts: dict[int, int] = {}
    try:
        queue, history = fetch_quasarr_downloads(settings)
        sync_download_errors(db, settings, downloads, queue, history)
    except Exception:
        for row in downloads:
            if row["status"] in {"snatched", "completed"}:
                magazine_id = row["magazine_id"]
                counts[magazine_id] = counts.get(magazine_id, 0) + 1
        return counts

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
    return counts


def magazine_cover(mag) -> str:
    issue_id = mag["latest_issue_id"]
    if issue_id:
        return f'<img src="/opds?cmd=Cover&issueid={issue_id}" alt="">'
    initials = "".join(word[:1] for word in str(mag["title"]).split()[:3]).upper()
    return f'<div class="cover-placeholder"><span>{html.escape(initials or "M")}</span></div>'


def mag_stat_button(
    label: str,
    count: int,
    magazine_id: int,
    kind: str,
    magazine_title: str | None = None,
) -> str:
    title = magazine_title or ""
    return (
        f'<button class="stat" type="button" data-open-mag-items '
        f'data-kind="{kind}" data-magazine-id="{magazine_id}" '
        f'data-title="{html.escape(label)}" data-magazine-title="{html.escape(title)}">'
        f'<span>{html.escape(label)}</span><strong>{count}</strong></button>'
    )


def blacklist_chips(terms) -> str:
    chips = []
    for term in terms:
        chips.append(
            f"""
            <form class="chip" method="post" action="/blacklist/{term['id']}/delete">
              <span>{html.escape(term["term"])}</span>
              <button type="submit" title="Remove blacklist term">x</button>
            </form>
            """
        )
    return "".join(chips)


def settings_modal(settings) -> str:
    return f"""
    <dialog id="settings-modal">
      <form method="dialog" class="modal-head">
        <h2>Settings</h2>
        <button type="submit" class="secondary">Close</button>
      </form>
      <form class="settings-form" method="post" action="/settings">
        <div class="settings-grid">
          <fieldset class="settings-card">
            <legend>Quasarr</legend>
            {input_row("Internal URL", "quasarr_url", settings.quasarr_url)}
            {input_row("External URL", "quasarr_external_url", settings.quasarr_external_url)}
            {input_row("API Key", "quasarr_api_key", settings.quasarr_api_key, "password")}
            {input_row("Search Category", "quasarr_search_category", settings.quasarr_search_category)}
            {input_row("Download Category", "quasarr_download_category", settings.quasarr_download_category)}
          </fieldset>
          <fieldset class="settings-card">
            <legend>Automation</legend>
            {input_row("Search Interval Minutes", "automation_interval_minutes", str(settings.automation_interval_minutes), "number")}
            {input_row("Import Check Minutes", "import_check_interval_minutes", str(settings.import_check_interval_minutes), "number")}
            {input_row("Past Days", "past_days", str(settings.past_days), "number")}
            {input_row("Min Size MB", "min_size_mb", str(settings.min_size_mb), "number")}
            {input_row("Max Size MB", "max_size_mb", str(settings.max_size_mb), "number")}
          </fieldset>
          <fieldset class="settings-card">
            <legend>Storage</legend>
            {input_row("Library Dir", "library_dir", settings.library_dir)}
          </fieldset>
          <fieldset class="settings-card">
            <legend>OPDS</legend>
            <label class="check"><input type="checkbox" name="opds_auth_enabled" {"checked" if settings.opds_auth_enabled else ""}> <span>Basic auth</span></label>
            {input_row("Username", "opds_username", settings.opds_username)}
            {input_row("Password", "opds_password", settings.opds_password, "password")}
            {input_row("Page Size", "opds_page_size", str(settings.opds_page_size), "number")}
          </fieldset>
          <fieldset class="settings-card settings-wide">
            <legend>Notifications</legend>
            {input_row("Discord Webhook URL", "discord_webhook_url", settings.discord_webhook_url, "password")}
          </fieldset>
        </div>
        <div class="settings-footer">
          <button type="submit">Save Settings</button>
        </div>
      </form>
    </dialog>
    """


def magazine_modal() -> str:
    return """
    <dialog id="magazine-modal">
      <form method="dialog" class="modal-head">
        <h2 id="magazine-modal-title">Magazine</h2>
        <button type="submit" class="secondary">Close</button>
      </form>
      <div class="modal-tools">
        <input id="magazine-modal-search" placeholder="Search">
        <span id="magazine-modal-count"></span>
        <form id="magazine-modal-clear" method="post" hidden>
          <button type="submit" class="secondary">Clear Skipped</button>
        </form>
        <form id="magazine-modal-delete-errors" method="post" hidden data-confirm="Delete all error downloads for this magazine?">
          <button type="submit" class="secondary">Delete Errors</button>
        </form>
      </div>
      <div id="magazine-modal-body" class="modal-list"></div>
      <div class="pager">
        <button type="button" class="secondary" id="magazine-modal-prev">Prev</button>
        <span id="magazine-modal-page"></span>
        <button type="button" class="secondary" id="magazine-modal-next">Next</button>
      </div>
    </dialog>
    """


def confirm_modal() -> str:
    return """
    <dialog id="confirm-modal" class="confirm-dialog">
      <div class="confirm-head"><h2>Are you sure?</h2></div>
      <p id="confirm-message" class="muted">This cannot be undone.</p>
      <div class="confirm-actions">
        <form method="dialog"><button type="submit" class="secondary">Cancel</button></form>
        <button type="button" id="confirm-accept">Delete</button>
      </div>
    </dialog>
    """


def downloads_modal() -> str:
    return """
    <dialog id="downloads-modal">
      <form method="dialog" class="modal-head">
        <h2>Downloads</h2>
        <button type="submit" class="secondary">Close</button>
      </form>
      <div id="download-status" class="download-list">
        <div class="muted">Loading...</div>
      </div>
    </dialog>
    """


def job_modal() -> str:
    return """
    <dialog id="job-modal">
      <form method="dialog" class="modal-head">
        <h2 id="job-title">Search</h2>
        <button type="submit" class="secondary">Close</button>
      </form>
      <section class="job-panel" id="job-panel" hidden>
        <div class="job-status" id="job-status">Queued</div>
        <div class="job-results" id="job-results"></div>
      </section>
    </dialog>
    """


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
        "issue_key": row["issue_key"],
        "release_title": row["release_title"],
        "file_path": row["file_path"],
        "cover_url": f"/opds?cmd=Cover&issueid={row['id']}",
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
        queue, history = fetch_quasarr_downloads(settings)
        sync_download_errors(db, settings, downloads, queue, history)
    except Exception as exc:
        return {"active": [], "error": str(exc), "quasarr_url": quasarr_public_url(settings)}

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
    if not download or not download["package_id"]:
        raise HTTPError(404, "Download package not found")
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
    for prefix in ("[Downloading] ", "[Extracting] ", "[Paused] ", "[Linkgrabber] ", "[CAPTCHA not solved!] "):
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


def page_script() -> str:
    return """
<script>
(() => {
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));

  const jobPanel = document.getElementById("job-panel");
  const jobModal = document.getElementById("job-modal");
  const jobTitle = document.getElementById("job-title");
  const jobStatus = document.getElementById("job-status");
  const jobResults = document.getElementById("job-results");
  const confirmModal = document.getElementById("confirm-modal");
  const confirmMessage = document.getElementById("confirm-message");
  const confirmAccept = document.getElementById("confirm-accept");
  let pendingConfirmForm = null;

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
    if (form.dataset.confirmed === "true") {
      delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    pendingConfirmForm = form;
    if (confirmMessage) confirmMessage.textContent = form.dataset.confirm || "This cannot be undone.";
    confirmModal?.showModal();
  });

  confirmAccept?.addEventListener("click", () => {
    if (!pendingConfirmForm) return;
    pendingConfirmForm.dataset.confirmed = "true";
    pendingConfirmForm.requestSubmit();
  });

  async function startJob(form) {
    const button = form.querySelector("button");
    if (button) button.disabled = true;
    jobModal?.showModal();
    jobPanel.hidden = false;
    jobTitle.textContent = button?.textContent?.trim() || "Search";
    jobStatus.textContent = "Starting...";
    jobResults.replaceChildren();
    try {
      const res = await fetch(form.action, { method: "POST" });
      const data = await res.json();
      if (!data.job_id) {
        jobStatus.textContent = "Done";
        if (button) button.disabled = false;
        await refreshDashboard();
        await loadDownloads();
        return;
      }
      pollJob(data.job_id, button);
    } catch (error) {
      jobStatus.textContent = error.message;
      if (button) button.disabled = false;
    }
  }

  async function pollJob(jobId, button) {
    const seen = new Set();
    while (true) {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      jobTitle.textContent = job.title;
      jobStatus.textContent = job.status;
      for (const item of job.events || []) {
        const key = `${item.at}-${item.event}-${item.message}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const row = document.createElement("div");
        row.className = "job-event";
        row.innerHTML = `<span>${esc(item.event)}</span><span>${esc(item.message)}</span>`;
        jobResults.append(row);
        scrollJobToBottom();
      }
      if (["done", "error"].includes(job.status)) {
        if (button) button.disabled = false;
        await refreshDashboard();
        await loadDownloads();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  function scrollJobToBottom() {
    requestAnimationFrame(() => {
      jobPanel.scrollTop = jobPanel.scrollHeight;
      jobModal.scrollTop = jobModal.scrollHeight;
    });
  }

  const baseLimit = 25;
  const magList = document.querySelector(".mag-list");
  const settingsModal = document.getElementById("settings-modal");
  const downloadsModal = document.getElementById("downloads-modal");
  const magazineModal = document.getElementById("magazine-modal");
  const magazineModalTitle = document.getElementById("magazine-modal-title");
  const magazineModalSearch = document.getElementById("magazine-modal-search");
  const magazineModalCount = document.getElementById("magazine-modal-count");
  const magazineModalBody = document.getElementById("magazine-modal-body");
  const magazineModalPage = document.getElementById("magazine-modal-page");
  const magazineModalPrev = document.getElementById("magazine-modal-prev");
  const magazineModalNext = document.getElementById("magazine-modal-next");
  const magazineModalClear = document.getElementById("magazine-modal-clear");
  const magazineModalDeleteErrors = document.getElementById("magazine-modal-delete-errors");
  let activeMagazine = { id: 0, kind: "", label: "", title: "", offset: 0 };

  document.querySelector("[data-open-settings]")?.addEventListener("click", () => {
    settingsModal?.showModal();
  });

  document.querySelector("[data-open-downloads]")?.addEventListener("click", () => {
    downloadsModal?.showModal();
    loadDownloads();
  });

  async function refreshDashboard() {
    if (!magList) return;
    try {
      const res = await fetch("/api/dashboard");
      const data = await res.json();
      if (typeof data.magazines === "string") {
        magList.innerHTML = data.magazines;
      }
    } catch (error) {
      console.warn(error);
    }
  }

  function renderDownloadCard(item, quasarrUrl = "") {
    const card = document.createElement("article");
    card.className = "download-card";
    const pct = Math.max(0, Math.min(100, Number(item.percentage || 0)));
    const captcha = item.type === "protected"
      ? `<a class="button-link" href="${esc(item.captcha_url)}" target="_blank" rel="noreferrer">Solve CAPTCHA</a>`
      : "";
    const quasarr = quasarrUrl
      ? `<a class="button-link secondary" href="${esc(quasarrUrl)}" target="_blank" rel="noreferrer">Quasarr</a>`
      : "";
    const importNow = String(item.status || "").trim().toLowerCase() === "completed"
      ? `<form method="post" action="/downloads/${item.id}/import-now"><button>Import Now</button></form>`
      : "";
    card.innerHTML = `
      <div class="download-title">${esc(item.title || item.release_title)}</div>
      <div class="download-meta">
        <span>${esc(item.status)}</span>
        <span>${pct}%</span>
        <span>${esc(item.timeleft || "")}</span>
        <span>${esc(item.mbleft ?? "?")} / ${esc(item.mb ?? "?")} MB</span>
      </div>
      <div class="progress"><span style="width:${pct}%"></span></div>
      <div class="download-actions">
        ${importNow}
        ${captcha}
        ${quasarr}
        <form method="post" action="/downloads/${item.id}/skip">
          <button>Skip</button>
        </form>
        <form method="post" action="/downloads/${item.id}/delete-package" data-confirm="Delete this download?">
          <button class="secondary">Delete</button>
        </form>
      </div>`;
    return card;
  }

  const downloadStatus = document.getElementById("download-status");
  async function loadDownloads() {
    if (!downloadStatus) return;
    try {
      const res = await fetch("/api/downloads");
      const data = await res.json();
      downloadStatus.replaceChildren();
      if (data.error) {
        downloadStatus.innerHTML = `<div class="muted">${esc(data.error)}</div>`;
        return;
      }
      if (!data.active.length) {
        downloadStatus.innerHTML = '<div class="muted">No active downloads.</div>';
        return;
      }
      for (const item of data.active) {
        downloadStatus.append(renderDownloadCard(item, data.quasarr_url));
      }
    } catch (error) {
      downloadStatus.innerHTML = `<div class="muted">${esc(error.message)}</div>`;
    }
  }

  function itemCard(kind, item) {
    const card = document.createElement("article");
    card.className = "list-card";
    if (kind === "downloaded") {
      card.className = "list-card carousel-card";
      card.innerHTML = `
        <div class="carousel-cover"><img src="${esc(item.cover_url)}" alt=""></div>
        <div class="carousel-detail">
          <div class="list-title">${esc(item.issue_key || item.release_title)}</div>
          <div class="muted">${esc(item.release_title)}</div>
          <div class="file-path">${esc(item.file_path)}</div>
          <div class="download-actions">
            <a class="button-link" href="${esc(item.view_url)}" target="_blank" rel="noreferrer">View</a>
            <a class="button-link secondary" href="${esc(item.file_url)}" target="_blank" rel="noreferrer">PDF</a>
            <form method="post" action="/issues/${item.id}/delete" data-confirm="Delete this imported PDF?"><button>Delete</button></form>
          </div>
        </div>`;
      return card;
    }
    if (kind === "skipped" && item.item_kind === "error") {
      card.innerHTML = `
        <div class="list-title">${esc(item.release_title)}</div>
        <div class="download-meta"><span>${esc(item.status)}</span><span>${esc(item.updated_at)}</span></div>
        <div class="download-actions">
          <form method="post" action="/downloads/${item.id}/retry-import"><button>Retry</button></form>
          <form method="post" action="/downloads/${item.id}/delete-package" data-confirm="Delete this error download?"><button class="secondary">Delete</button></form>
        </div>`;
      return card;
    }
    if (kind === "skipped") {
      card.innerHTML = `
        <div class="list-title">${esc(item.release_title)}</div>
        <div class="download-meta"><span>${esc(item.reason)}</span><span>${esc(item.updated_at)}</span></div>
        <div class="download-actions">
          <form method="post" action="/skipped/${item.id}/unskip"><button>Unskip</button></form>
        </div>`;
      return card;
    }
    if (kind === "errors") {
      card.innerHTML = `
        <div class="list-title">${esc(item.release_title)}</div>
        <div class="download-meta"><span>${esc(item.status)}</span><span>${esc(item.updated_at)}</span></div>
        <div class="download-actions">
          <form method="post" action="/downloads/${item.id}/retry-import"><button>Retry</button></form>
          <form method="post" action="/downloads/${item.id}/delete-package" data-confirm="Delete this error download?"><button class="secondary">Delete</button></form>
        </div>`;
      return card;
    }
    return renderDownloadCard(item);
  }

  async function loadMagazineItems(nextOffset = 0) {
    activeMagazine.offset = Math.max(0, nextOffset);
    const limit = activeMagazine.kind === "downloaded" ? 1 : baseLimit;
    const params = new URLSearchParams({
      limit,
      offset: activeMagazine.offset,
      search: magazineModalSearch.value.trim(),
    });
    const res = await fetch(`/api/magazines/${activeMagazine.id}/items/${activeMagazine.kind}?${params}`);
    const data = await res.json();
    magazineModalBody.replaceChildren();
    for (const item of data.rows) {
      magazineModalBody.append(itemCard(activeMagazine.kind, item));
    }
    if (!data.rows.length) {
      magazineModalBody.innerHTML = `<div class="muted">No ${esc(activeMagazine.label.toLowerCase())}.</div>`;
    }
    magazineModalCount.textContent = `${data.total} item(s)`;
    const start = data.total ? data.offset + 1 : 0;
    const end = Math.min(data.offset + data.limit, data.total);
    magazineModalPage.textContent = activeMagazine.kind === "downloaded"
      ? `${start}/${data.total}`
      : `${start}-${end}`;
    magazineModalPrev.disabled = data.offset <= 0;
    magazineModalNext.disabled = data.offset + data.limit >= data.total;
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains("js-job-form")) return;
    event.preventDefault();
    startJob(form);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-mag-items]");
    if (!button) return;
      activeMagazine = {
        id: button.dataset.magazineId,
        kind: button.dataset.kind,
        label: button.dataset.title,
        title: button.dataset.magazineTitle,
        offset: 0,
      };
      magazineModalTitle.textContent = `${activeMagazine.title} - ${activeMagazine.label}`;
      magazineModalSearch.value = "";
      if (magazineModalClear) {
        magazineModalClear.hidden = activeMagazine.kind !== "skipped";
        magazineModalClear.action = `/magazines/${activeMagazine.id}/skipped/clear`;
      }
      if (magazineModalDeleteErrors) {
        magazineModalDeleteErrors.hidden = activeMagazine.kind !== "skipped";
        magazineModalDeleteErrors.action = `/magazines/${activeMagazine.id}/errors/delete`;
      }
      magazineModal?.showModal();
      loadMagazineItems(0);
  });

  magazineModalSearch?.addEventListener("input", () => loadMagazineItems(0));
  magazineModalPrev?.addEventListener("click", () => {
    const limit = activeMagazine.kind === "downloaded" ? 1 : baseLimit;
    loadMagazineItems(activeMagazine.offset - limit);
  });
  magazineModalNext?.addEventListener("click", () => {
    const limit = activeMagazine.kind === "downloaded" ? 1 : baseLimit;
    loadMagazineItems(activeMagazine.offset + limit);
  });
  loadDownloads();
  setInterval(() => {
    refreshDashboard();
    loadDownloads();
    if (magazineModal?.open && activeMagazine.kind === "downloading") {
      loadMagazineItems(activeMagazine.offset);
    }
  }, 5000);
})();
</script>
"""


def _unskip_release(db, settings, skip_id: int):
    skipped = db.skipped_release_by_id(skip_id)
    if not skipped:
        return None
    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    package_ids = client.add_url(skipped["download_url"], settings.quasarr_download_category)
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
    notify_download_started(
        settings,
        skipped["magazine_title"],
        skipped["release_title"],
        package_id,
    )
    db.record_event(
        "info",
        "search",
        "Unskipped release sent to Quasarr",
        f"{skipped['magazine_title']} - {skipped['release_title']} ({issue_key})",
    )
    return package_id


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


def issue_viewer_page(issue) -> str:
    title = f"{issue['magazine_title']} - {issue['issue_key']}"
    body = f"""
    <section class="viewer-shell">
      <header class="viewer-bar">
        <div>
          <a href="/">Magazarr</a>
          <h1>{html.escape(title)}</h1>
          <div class="muted">{html.escape(issue["release_title"])}</div>
        </div>
        <a class="button-link secondary" href="/issues/{issue['id']}/file" target="_blank" rel="noreferrer">Open PDF</a>
      </header>
      <iframe class="pdf-viewer" src="/issues/{issue['id']}/file" title="{html.escape(title)}"></iframe>
      <p class="viewer-fallback">
        If PDF preview is unavailable, <a href="/issues/{issue['id']}/file">open the PDF directly</a>.
      </p>
    </section>
    """
    return page(title, body)


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="/static/magazarr-icon.png" type="image/png">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f3f5f6;
      --fg: #172026;
      --muted: #68737d;
      --panel: #ffffff;
      --line: #d9dee3;
      --soft: #eef3f2;
      --accent: #0f766e;
      --accent-fg: #ffffff;
      --shadow: 0 12px 30px rgba(23, 32, 38, 0.07);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101417;
        --fg: #eef2f4;
        --muted: #9ba7b0;
        --panel: #181e23;
        --line: #303840;
        --soft: #202a2f;
        --accent: #14b8a6;
        --accent-fg: #06201d;
        --shadow: 0 16px 36px rgba(0, 0, 0, 0.32);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }}
    a {{ color: var(--accent); }}
    h1, h2 {{ margin: 0; font-weight: 650; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 16px; margin-bottom: 14px; }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 4px;
    }}
    .panel-head h2 {{ margin-bottom: 0; }}
    .topbar {{
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 0;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel) 94%, var(--bg) 6%);
      backdrop-filter: blur(14px);
    }}
    .topbar span {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(280px, 420px) 1fr;
      gap: 16px;
      padding: 16px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}
    label {{
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      align-items: center;
      gap: 10px;
      margin: 8px 0;
    }}
    label span {{ color: var(--muted); }}
    .check {{
      display: flex;
      gap: 8px;
      margin: 12px 0;
    }}
    input {{
      width: 100%;
      min-width: 0;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: transparent;
      color: var(--fg);
    }}
    button {{
      min-height: 34px;
      padding: 8px 13px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: var(--accent-fg);
      cursor: pointer;
      font-weight: 650;
    }}
    button.secondary {{
      border-color: var(--line);
      background: transparent;
      color: var(--fg);
    }}
    .inline {{
      display: flex;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .actions {{ display: inline-flex; margin-right: 8px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }}
    th, td {{
      padding: 9px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .row-actions {{
      text-align: right;
      white-space: nowrap;
    }}
    .row-actions form {{ display: inline-flex; margin-left: 8px; }}
    .muted {{ color: var(--muted); }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      min-height: 28px;
      margin-top: 10px;
    }}
    .chips:empty {{
      display: none;
      min-height: 0;
      margin-top: 0;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 6px 3px 10px;
      color: var(--muted);
      background: var(--panel);
    }}
    .chip button {{
      min-height: 22px;
      width: 22px;
      padding: 0;
      border-radius: 999px;
      border-color: transparent;
      background: transparent;
      color: var(--muted);
    }}
    .chip-form {{
      display: flex;
      gap: 6px;
      margin-top: 8px;
    }}
    .chip-form input {{ padding: 6px 8px; }}
    .chip-form button {{ min-height: 30px; }}
    .job-panel {{
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      max-height: 280px;
      overflow: auto;
    }}
    .job-panel h3 {{ margin: 0 0 6px; font-size: 14px; }}
    .job-status {{ color: var(--muted); margin-bottom: 8px; }}
    .job-event {{
      display: grid;
      grid-template-columns: 90px minmax(0, 1fr);
      gap: 8px;
      padding: 5px 0;
      border-top: 1px solid var(--line);
    }}
    .job-event span:first-child {{ color: var(--muted); }}
    dialog {{
      width: min(1000px, calc(100vw - 32px));
      max-height: calc(100vh - 64px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--fg);
      padding: 16px;
    }}
    .confirm-dialog {{
      width: min(460px, calc(100vw - 32px));
    }}
    .confirm-head {{
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .confirm-head h2 {{
      margin: 0;
      font-size: 18px;
    }}
    .confirm-dialog p {{
      margin: 14px 0;
      font-size: 15px;
      line-height: 1.35;
    }}
    .confirm-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }}
    .confirm-actions form {{
      margin: 0;
    }}
    .confirm-actions button {{
      min-width: 92px;
    }}
    dialog::backdrop {{ background: rgba(0, 0, 0, 0.4); }}
    .modal-head, .pager {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .modal-head {{
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .modal-tools {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .modal-tools input {{
      flex: 1 1 220px;
      max-width: 360px;
    }}
    .modal-tools form {{
      margin: 0;
    }}
    .modal-tools button {{
      min-height: 34px;
      padding: 7px 11px;
    }}
    #magazine-modal-count {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .pager {{ justify-content: flex-end; margin-top: 12px; }}
    .download-list {{
      display: grid;
      gap: 12px;
    }}
    .download-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .download-title {{
      font-weight: 650;
      margin-bottom: 10px;
      overflow-wrap: anywhere;
    }}
    .download-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .progress {{
      height: 8px;
      border-radius: 999px;
      background: var(--line);
      overflow: hidden;
    }}
    .progress span {{
      display: block;
      height: 100%;
      background: var(--accent);
    }}
    .download-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
    }}
    .button-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 7px 12px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: var(--accent-fg);
      font-weight: 650;
      line-height: 1;
      text-decoration: none;
    }}
    .button-link.secondary {{
      border-color: var(--line);
      background: transparent;
      color: var(--fg);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .brand-link {{
      display: inline-flex;
      align-items: center;
    }}
    .brand-logo {{
      display: block;
      width: 190px;
      height: auto;
    }}
    .sr-only {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .topbar-inner {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      width: calc(100% - 48px);
      max-width: 1380px;
      margin: 0 auto;
      padding: 16px 18px;
    }}
    .top-actions, .toolbar, .card-actions {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}
    .card-actions {{
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }}
    .card-actions form {{
      margin: 0;
    }}
    .card-actions button {{
      min-width: 86px;
    }}
    .layout {{
      width: calc(100% - 48px);
      max-width: 1380px;
      margin: 0 auto;
      padding: 22px 0 40px;
    }}
    .toolbar .inline {{
      margin: 0;
    }}
    .toolbar input {{
      width: 230px;
    }}
    .panel-head .toolbar {{
      justify-content: flex-end;
    }}
    .mag-list {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 540px), 1fr));
      gap: 16px;
      margin-top: 16px;
    }}
    .mag-card {{
      display: grid;
      grid-template-columns: 116px minmax(0, 1fr);
      gap: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: color-mix(in srgb, var(--panel) 96%, var(--soft) 4%);
      padding: 16px;
      min-width: 0;
      align-items: start;
    }}
    .mag-cover-block {{
      width: 116px;
      min-width: 0;
    }}
    .cover-button {{
      display: grid;
      grid-template-rows: auto 34px;
      width: 100%;
      min-height: 0;
      padding: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--panel);
      color: var(--fg);
      box-shadow: 0 10px 22px rgba(23, 32, 38, 0.08);
      text-align: left;
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }}
    .cover-button:hover {{
      border-color: color-mix(in srgb, var(--accent) 42%, var(--line));
      box-shadow: 0 14px 28px rgba(23, 32, 38, 0.12);
      transform: translateY(-1px);
    }}
    .cover-button:focus-visible {{
      outline: 3px solid color-mix(in srgb, var(--accent) 42%, transparent);
      outline-offset: 3px;
    }}
    .downloaded-entry {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-width: 0;
      padding: 7px 9px;
      border-top: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel) 88%, var(--soft) 12%);
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      line-height: 1;
    }}
    .downloaded-label {{
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .downloaded-entry strong {{
      flex: 0 0 auto;
      color: var(--fg);
      font-size: 18px;
      line-height: 1;
    }}
    .mag-cover {{
      display: block;
      width: 100%;
      aspect-ratio: 210 / 297;
      border: 0;
      border-radius: 0;
      overflow: hidden;
      background: var(--soft);
    }}
    .mag-cover img {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .cover-placeholder {{
      display: grid;
      place-items: center;
      width: 100%;
      height: 100%;
      color: var(--muted);
      font-weight: 700;
      font-size: 26px;
      background: var(--soft);
    }}
    .cover-placeholder span {{
      display: grid;
      place-items: center;
      width: 52px;
      height: 52px;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: var(--panel);
    }}
    .mag-main {{
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .mag-title {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }}
    .mag-title h3 {{
      margin: 0;
      font-size: 19px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}
    .switch {{
      display: flex;
      grid-template-columns: none;
      align-items: center;
      gap: 6px;
      margin: 0;
      color: var(--muted);
      white-space: nowrap;
    }}
    .switch input {{
      width: auto;
    }}
    .chip-form {{
      max-width: none;
      margin-top: 0;
    }}
    .mag-stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 0;
    }}
    .stat {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 44px;
      padding: 8px 12px;
      border-color: var(--line);
      background: var(--panel);
      color: var(--fg);
      text-align: left;
    }}
    .stat span {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.1;
      white-space: normal;
    }}
    .stat strong {{
      font-size: 20px;
      line-height: 1;
    }}
    .settings-form {{
      display: grid;
      gap: 14px;
    }}
    .settings-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      align-items: start;
    }}
    .settings-card {{
      margin: 0;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: color-mix(in srgb, var(--panel) 96%, var(--soft) 4%);
      min-width: 0;
    }}
    .settings-card legend {{
      padding: 0 6px;
      color: var(--fg);
      font-size: 13px;
      font-weight: 700;
    }}
    .settings-card label {{
      grid-template-columns: 132px minmax(0, 1fr);
      margin: 10px 0 0;
    }}
    .settings-card label:first-of-type {{
      margin-top: 4px;
    }}
    .settings-card .check {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .settings-card .check input {{
      width: auto;
      min-height: 0;
    }}
    .settings-card label span {{
      font-size: 13px;
    }}
    .settings-card input {{
      min-height: 36px;
      background: color-mix(in srgb, var(--panel) 90%, var(--bg) 10%);
    }}
    .settings-wide {{
      grid-column: 1 / -1;
    }}
    .settings-footer {{
      display: flex;
      justify-content: flex-end;
      padding-top: 2px;
    }}
    .modal-list {{
      display: grid;
      gap: 10px;
      max-height: min(62vh, 620px);
      overflow: auto;
      padding-right: 2px;
    }}
    .list-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
    }}
    .carousel-card {{
      display: grid;
      grid-template-columns: minmax(120px, 180px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
      min-height: 260px;
    }}
    .carousel-cover {{
      aspect-ratio: 210 / 297;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: var(--soft);
    }}
    .carousel-cover img {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .carousel-detail {{
      min-width: 0;
    }}
    .list-title {{
      font-weight: 650;
      overflow-wrap: anywhere;
      margin-bottom: 6px;
    }}
    .file-path {{
      margin-top: 8px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .empty {{
      color: var(--muted);
      padding: 16px 0;
    }}
    .viewer-shell {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      height: 100vh;
      background: var(--bg);
    }}
    .viewer-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    .viewer-bar h1 {{
      margin: 4px 0;
      font-size: 18px;
      overflow-wrap: anywhere;
    }}
    .pdf-viewer {{
      display: block;
      width: 100%;
      height: 100%;
      border: 0;
      background: var(--panel);
    }}
    .viewer-fallback {{
      margin: 0;
      padding: 10px 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      background: var(--panel);
    }}
    button:hover, .button-link:hover {{
      filter: brightness(0.97);
    }}
    body > .panel {{ margin: 0 16px 16px; }}
    @media (max-width: 820px) {{
      .grid {{ grid-template-columns: 1fr; }}
      label {{ grid-template-columns: 1fr; }}
      .row-actions {{ flex-wrap: wrap; justify-content: flex-start; }}
      .topbar-inner {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .panel-head {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .toolbar, .toolbar .inline {{
        width: 100%;
      }}
      .toolbar .inline input {{
        flex: 1;
      }}
      .toolbar {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .toolbar .inline:first-child {{
        display: grid;
        grid-column: 1 / -1;
        grid-template-columns: minmax(0, 1fr) auto;
      }}
      .toolbar .inline:not(:first-child) button {{
        width: 100%;
      }}
      .mag-list {{
        grid-template-columns: 1fr;
      }}
      .mag-card {{
        grid-template-columns: 104px minmax(0, 1fr);
      }}
      .mag-cover-block {{
        width: 104px;
      }}
      .modal-tools {{
        display: grid;
        grid-template-columns: 1fr;
      }}
      .modal-tools input {{
        width: 100%;
        max-width: none;
      }}
      .modal-tools button {{
        width: 100%;
      }}
      .settings-grid {{
        grid-template-columns: 1fr;
      }}
      .settings-wide {{
        grid-column: auto;
      }}
    }}
    @media (max-width: 520px) {{
      .layout {{
        width: calc(100% - 20px);
        padding: 12px 0 28px;
      }}
      .topbar-inner {{
        width: calc(100% - 20px);
        padding: 12px 0;
      }}
      .panel, dialog {{
        padding: 12px;
      }}
      .mag-card {{
        grid-template-columns: 1fr;
      }}
      .mag-cover-block {{
        width: min(104px, 38vw);
      }}
      .mag-title {{
        flex-direction: column;
      }}
      .settings-card label {{
        grid-template-columns: 1fr;
      }}
      .settings-footer button {{
        width: 100%;
      }}
      .download-actions {{
        align-items: stretch;
        flex-direction: column;
      }}
      .download-actions > * {{
        width: 100%;
      }}
      .download-actions button,
      .download-actions .button-link {{
        justify-content: center;
        width: 100%;
      }}
      .carousel-card {{
        grid-template-columns: 1fr;
      }}
      .carousel-cover {{
        width: min(180px, 55vw);
      }}
      .viewer-bar {{
        align-items: stretch;
        flex-direction: column;
      }}
      .toolbar {{
        grid-template-columns: 1fr;
      }}
      .toolbar .inline:first-child {{
        grid-template-columns: minmax(0, 1fr) auto;
      }}
    }}
  </style>
</head>
<body>{body}{page_script()}</body>
</html>"""
