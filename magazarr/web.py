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
from magazarr.search import search_magazine
from magazarr.settings import SettingsStore
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

    @app.post("/downloads/<download_id:int>/delete-package")
    def delete_download_package(download_id):
        settings = settings_store.load()
        download = next(
            (item for item in db.downloads() if int(item["id"]) == int(download_id)),
            None,
        )
        if not download or not download["package_id"]:
            raise HTTPError(404, "Download package not found")
        client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
        if not client.delete_package(download["package_id"], download["release_title"]):
            raise HTTPError(500, "Quasarr package delete failed")
        db.update_download_status(download_id, "deleted")
        db.record_event(
            "info",
            "download",
            "Deleted download package",
            download["release_title"],
        )
        redirect("/")

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
    return f"""
    <section class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <img class="brand-logo" src="/static/magazarr-logo.png" alt="Magazarr">
          <h1 class="sr-only">Magazarr</h1>
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
          </div>
        </div>
        <section class="job-panel" id="job-panel" hidden>
          <h3 id="job-title">Search</h3>
          <div class="job-status" id="job-status">Queued</div>
          <div class="job-results" id="job-results"></div>
        </section>
        <div class="mag-list">
          {magazine_rows(magazines, blacklist, db)}
        </div>
      </section>
    </main>

    {downloads_modal()}
    {settings_modal(settings)}
    {magazine_modal()}
    """


def input_row(label: str, name: str, value: str, input_type: str = "text") -> str:
    return (
        f'<label><span>{html.escape(label)}</span>'
        f'<input type="{input_type}" name="{name}" value="{html.escape(value or "")}"></label>'
    )


def magazine_rows(magazines, blacklist, db) -> str:
    rows = []
    for mag in magazines:
        checked = "checked" if mag["active"] else ""
        blacklisted_terms = blacklist.get(mag["id"], [])
        skipped = db.skipped_release_count(magazine_id=mag["id"])
        errors = db.import_error_count(mag["id"])
        downloading = db.download_count(mag["id"], ("snatched", "completed"))
        rows.append(
            f"""
            <article class="mag-card">
              <div class="mag-cover">{magazine_cover(mag)}</div>
              <div class="mag-main">
                <div class="mag-title">
                  <h3>{html.escape(mag["title"])}</h3>
                  <form method="post" action="/magazines/{mag['id']}/active">
                    <label class="switch"><input type="checkbox" name="active" {checked} onchange="this.form.submit()"> Active</label>
                  </form>
                </div>
                <div class="chips">{blacklist_chips(blacklisted_terms)}</div>
                <form class="chip-form" method="post" action="/magazines/{mag['id']}/blacklist">
                  <input name="term" placeholder="Blacklist term">
                  <button type="submit">Add</button>
                </form>
                <div class="mag-stats">
                  {mag_stat_button("Downloaded", mag["issue_count"], mag["id"], "downloaded", mag["title"])}
                  {mag_stat_button("Downloading", downloading, mag["id"], "downloading", mag["title"])}
                  {mag_stat_button("Skipped", skipped, mag["id"], "skipped", mag["title"])}
                  {mag_stat_button("Errors", errors, mag["id"], "errors", mag["title"])}
                </div>
                <div class="card-actions">
                  <form class="js-job-form" method="post" action="/api/magazines/{mag['id']}/search"><button>Search</button></form>
                  <form method="post" action="/magazines/{mag['id']}/delete"><button class="secondary">Delete</button></form>
                </div>
              </div>
            </article>
            """
        )
    return "".join(rows) or '<div class="empty">No magazines.</div>'


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
            {input_row("Import Root", "import_root", settings.import_root)}
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
          <button type="submit" class="secondary">Clear</button>
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
        "acquired_at": row["acquired_at"],
        "size_bytes": row["size_bytes"],
    }


def skipped_payload(row):
    return {
        "id": row["id"],
        "magazine_title": row["magazine_title"],
        "release_title": row["release_title"],
        "reason": reason_label(row["reason"]),
        "updated_at": row["updated_at"],
    }


def download_payload(row):
    return {
        "id": row["id"],
        "magazine_title": row["magazine_title"],
        "release_title": row["release_title"],
        "issue_key": row["issue_key"],
        "status": row["status"],
        "package_id": row["package_id"],
        "updated_at": row["updated_at"],
    }


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def download_status_payload(db, settings, magazine_id: int | None = None):
    downloads = list(db.downloads(magazine_id))
    by_package = {
        str(item["package_id"]): item for item in downloads if item["package_id"]
    }
    try:
        queue, history = fetch_quasarr_downloads(settings)
        sync_download_errors(db, settings, downloads, queue, history)
    except Exception as exc:
        return {"active": [], "error": str(exc), "quasarr_url": quasarr_public_url(settings)}

    active = []
    for item in queue:
        download = by_package.get(str(item.get("nzo_id")))
        if not download:
            continue
        active.append(download_card_payload(settings, item, download))

    return {"active": active, "error": "", "quasarr_url": quasarr_public_url(settings)}


def download_card_payload(settings, item, download):
    package_id = str(item.get("nzo_id") or "")
    title = str(item.get("filename") or download["release_title"])
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
  const jobTitle = document.getElementById("job-title");
  const jobStatus = document.getElementById("job-status");
  const jobResults = document.getElementById("job-results");

  async function startJob(form) {
    const button = form.querySelector("button");
    if (button) button.disabled = true;
    jobPanel.hidden = false;
    jobTitle.textContent = "Search";
    jobStatus.textContent = "Starting...";
    jobResults.replaceChildren();
    try {
      const res = await fetch(form.action, { method: "POST" });
      const data = await res.json();
      if (!data.job_id) {
        jobStatus.textContent = "Done";
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
        jobResults.scrollTop = jobResults.scrollHeight;
      }
      if (["done", "error"].includes(job.status)) {
        if (button) button.disabled = false;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  for (const form of document.querySelectorAll(".js-job-form")) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      startJob(form);
    });
  }

  const limit = 25;
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
  let activeMagazine = { id: 0, kind: "", label: "", title: "", offset: 0 };

  document.querySelector("[data-open-settings]")?.addEventListener("click", () => {
    settingsModal?.showModal();
  });

  document.querySelector("[data-open-downloads]")?.addEventListener("click", () => {
    downloadsModal?.showModal();
    loadDownloads();
  });

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
        ${captcha}
        ${quasarr}
        <form method="post" action="/downloads/${item.id}/delete-package">
          <button>Delete</button>
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
      card.innerHTML = `
        <div class="list-title">${esc(item.issue_key || item.release_title)}</div>
        <div class="muted">${esc(item.release_title)}</div>
        <div class="file-path">${esc(item.file_path)}</div>
        <div class="download-actions">
          <form method="post" action="/issues/${item.id}/delete"><button>Delete</button></form>
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
          <form method="post" action="/downloads/${item.id}/delete-package"><button class="secondary">Delete</button></form>
        </div>`;
      return card;
    }
    return renderDownloadCard(item);
  }

  async function loadMagazineItems(nextOffset = 0) {
    activeMagazine.offset = Math.max(0, nextOffset);
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
    magazineModalPage.textContent = `${start}-${end}`;
    magazineModalPrev.disabled = data.offset <= 0;
    magazineModalNext.disabled = data.offset + data.limit >= data.total;
  }

  for (const button of document.querySelectorAll("[data-open-mag-items]")) {
    button.addEventListener("click", () => {
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
      magazineModal?.showModal();
      loadMagazineItems(0);
    });
  }

  magazineModalSearch?.addEventListener("input", () => loadMagazineItems(0));
  magazineModalPrev?.addEventListener("click", () => loadMagazineItems(activeMagazine.offset - limit));
  magazineModalNext?.addEventListener("click", () => loadMagazineItems(activeMagazine.offset + limit));
  loadDownloads();
  setInterval(() => {
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
    dialog::backdrop {{ background: rgba(0, 0, 0, 0.4); }}
    .modal-head, .modal-tools, .pager {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .modal-tools input {{ max-width: 360px; }}
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
      margin-top: 14px;
      padding-top: 12px;
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
    .mag-cover {{
      width: 116px;
      aspect-ratio: 210 / 297;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: var(--soft);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--panel) 60%, transparent);
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
      margin-top: 10px;
    }}
    .mag-stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}
    .stat {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 44px;
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
        grid-template-columns: 92px minmax(0, 1fr);
      }}
      .mag-cover {{
        width: 92px;
      }}
      .modal-tools {{
        align-items: stretch;
        flex-direction: column;
      }}
      .modal-tools input {{
        max-width: none;
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
      .mag-cover {{
        width: min(112px, 38vw);
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
