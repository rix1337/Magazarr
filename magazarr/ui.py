# -*- coding: utf-8 -*-
"""Server-rendered page shells. Dynamic content is rendered by assets/app.js."""

import html
import json
from pathlib import Path

from magazarr.version import __version__

# Stroke icons (24px grid, round caps), referenced with <use href="#i-name">.
ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "settings": (
        '<path d="M12.2 2h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-1 1.7l-.4.3a2 2 0 0 1-2 0l-.2-.1'
        "a2 2 0 0 0-2.7.7l-.2.4a2 2 0 0 0 .7 2.7l.2.1a2 2 0 0 1 1 1.7v.6a2 2 0 0 1-1 1.7"
        "l-.2.1a2 2 0 0 0-.7 2.7l.2.4a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2 0l.4.3a2 2 0 0 1 1 1.7"
        "v.2a2 2 0 0 0 2 2h.4a2 2 0 0 0 2-2v-.2a2 2 0 0 1 1-1.7l.4-.3a2 2 0 0 1 2 0l.2.1"
        "a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7l-.2-.1a2 2 0 0 1-1-1.7v-.6a2 2 0 0 1 1-1.7"
        "l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1a2 2 0 0 1-2 0l-.4-.3"
        'a2 2 0 0 1-1-1.7V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
    ),
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M5 21h14"/>',
    "library": (
        '<path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6.5A2.5 2.5 0 0 0 4 21.5"/>'
        '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H19"/>'
    ),
    "grid": (
        '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'
    ),
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "trash": '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
    "refresh": '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M3 21v-5h5"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "play": '<path d="m6 4 14 8-14 8z"/>',
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "arrow-left": '<path d="M19 12H5m7 7-7-7 7-7"/>',
    "ban": '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "skip": '<path d="m5 4 10 8-10 8zM19 5v14"/>',
    "rss": '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "sort": '<path d="M3 6h18M6 12h12M10 18h4"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "upload": '<path d="M12 21V9m-5 5 5-5 5 5M5 3h14"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
}


def icon(name: str, cls: str = "icon") -> str:
    return f'<svg class="{cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def icon_sprite() -> str:
    symbols = "".join(
        f'<symbol id="i-{name}" viewBox="0 0 24 24">{paths}</symbol>'
        for name, paths in ICONS.items()
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" class="icon-sprite" aria-hidden="true">'
        f"{symbols}</svg>"
    )


def asset_root() -> Path:
    packaged = Path(__file__).resolve().parent / "assets"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parent.parent / "assets"


def asset_url(name: str) -> str:
    # Assets are cached as immutable, so the key must change whenever the file does.
    try:
        stamp = int((asset_root() / name).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{name}?v={__version__}.{stamp}"


def page(title: str, body: str, script: str = "", page_class: str = "") -> str:
    scripts = "".join(
        f'<script src="{asset_url(name)}" defer></script>'
        for name in (("common.js", script) if script else ())
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#f5f6f8" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#0d1117" media="(prefers-color-scheme: dark)">
  <link rel="icon" href="/static/magazarr-icon.png" type="image/png">
  <link rel="stylesheet" href="{asset_url("app.css")}">
  <title>{html.escape(title)}</title>
  {scripts}
</head>
<body class="{page_class}">
{icon_sprite()}
{body}
<div class="toasts" id="toasts" role="status" aria-live="polite"></div>
</body>
</html>"""


def topbar(active: str) -> str:
    def tab(href, name, label):
        current = ' aria-current="page"' if active == name else ""
        return f'<a class="nav-link" href="{href}"{current}>{label}</a>'

    dashboard_actions = ""
    if active == "magazines":
        dashboard_actions = f"""
        <span class="status-note" id="quasarr-status" hidden title="Download counts may be stale">{icon("alert")}<span>Quasarr unreachable</span></span>
        <button class="btn btn-quiet" type="button" data-open-downloads>
          {icon("download")}<span class="hide-sm">Downloads</span><span class="count-badge" id="downloads-badge" hidden>0</span>
        </button>
        <button class="btn btn-quiet btn-icon" type="button" data-open-settings title="Settings" aria-label="Settings">{icon("settings")}</button>"""
    return f"""
    <header class="topbar">
      <div class="topbar-inner">
        <a class="brand" href="/" aria-label="Magazarr home">
          <img class="brand-logo" src="/static/magazarr-icon.png" alt="">
          <span class="brand-name">Magazarr</span>
        </a>
        <nav class="nav" aria-label="Main">
          {tab("/", "magazines", "Magazines")}
          {tab("/library", "library", "Library")}
        </nav>
        <div class="topbar-actions">
          {dashboard_actions}
          <button class="btn btn-quiet btn-icon" type="button" data-copy-opds title="Copy OPDS feed URL" aria-label="Copy OPDS feed URL">{icon("rss")}</button>
        </div>
      </div>
    </header>"""


def dashboard(settings, initial_state: dict) -> str:
    state = json.dumps(initial_state).replace("<", "\\u003c")
    return f"""
    {topbar("magazines")}
    <main class="shell">
      <header class="masthead">
        <div>
          <h1 class="display">Magazines</h1>
          <p class="dek" id="dek">&nbsp;</p>
        </div>
        <form method="post" action="/api/magazines/search-all" data-action="job">
          <button class="btn btn-ink" type="submit">{icon("search")}Search All</button>
        </form>
      </header>

      <div class="bar">
        <form class="add-form" method="post" action="/magazines" data-action="add-magazine" autocomplete="off">
          <input name="title" placeholder="Add a title, e.g. Der Spiegel" aria-label="Magazine title" maxlength="200">
          <button class="btn btn-ink" type="submit">{icon("plus")}Add</button>
        </form>
        <nav class="filters" aria-label="Show">
          <button type="button" data-filter="all" aria-pressed="true">All <span data-filter-count="all"></span></button>
          <button type="button" data-filter="active" aria-pressed="false">Active <span data-filter-count="active"></span></button>
          <button type="button" data-filter="paused" aria-pressed="false">Paused <span data-filter-count="paused"></span></button>
          <button type="button" data-filter="attention" aria-pressed="false">Needs attention <span data-filter-count="attention"></span></button>
        </nav>
        <div class="bar-tools">
          <label class="search-field">
            {icon("search")}
            <input type="search" id="mag-filter" placeholder="Filter" aria-label="Filter magazines" aria-keyshortcuts="/">
            <kbd>/</kbd>
          </label>
          <label class="select-field">
            <span class="sr-only">Sort</span>
            <select id="mag-sort" aria-label="Sort magazines">
              <option value="title">A–Z</option>
              <option value="recent">Latest issue</option>
              <option value="issues">Most issues</option>
              <option value="attention">Needs attention</option>
            </select>
          </label>
          <div class="view-toggle" role="group" aria-label="Layout">
            <button type="button" data-view="rack" aria-pressed="true" title="Covers" aria-label="Covers">{icon("grid")}</button>
            <button type="button" data-view="list" aria-pressed="false" title="List" aria-label="List">{icon("sort")}</button>
          </div>
        </div>
      </div>

      <section class="rack" id="mag-grid"></section>
      <div class="empty-state" id="mag-empty" hidden>
        <h2 class="display-sm">An empty newsstand</h2>
        <p>Add a magazine title above. Magazarr asks Quasarr for new issues on a schedule and files finished PDFs into your library.</p>
      </div>
      <p class="empty-inline" id="mag-no-match" hidden>Nothing matches this filter.</p>
    </main>

    {downloads_modal()}
    {settings_modal(settings)}
    {magazine_modal()}
    {job_panel()}
    {confirm_modal()}
    <script type="application/json" id="initial-state">{state}</script>
    """


def library_page(magazines) -> str:
    options = "".join(
        f'<option value="{m["id"]}">{html.escape(m["title"])}</option>'
        for m in magazines
    )
    return f"""
    {topbar("library")}
    <main class="shell">
      <header class="masthead">
        <div>
          <h1 class="display">Library</h1>
          <p class="dek" id="library-count">&nbsp;</p>
        </div>
      </header>
      <div class="bar">
        <label class="search-field grow">
          {icon("search")}
          <input type="search" id="library-search" placeholder="Search issues" aria-label="Search issues" aria-keyshortcuts="/">
          <kbd>/</kbd>
        </label>
        <div class="bar-tools">
          <label class="select-field">
            <span class="sr-only">Magazine</span>
            <select id="library-mag-filter" aria-label="Magazine">
              <option value="">All magazines</option>
              {options}
            </select>
          </label>
          <button class="btn btn-line" id="library-select-mode" type="button" aria-pressed="false">{icon("check")}Select</button>
        </div>
      </div>
      <section id="library-grid" class="issue-grid" aria-live="polite"></section>
      <div class="empty-state" id="library-empty" hidden>
        <h2 class="display-sm">No issues yet</h2>
        <p>Imported issues land here, newest first.</p>
      </div>
      <div class="load-more"><button class="btn btn-line" id="library-more" type="button" hidden>Load more</button></div>
    </main>
    <div class="bulk-bar" id="library-bulk" hidden>
      <span id="library-selected-count">0 selected</span>
      <button class="btn btn-quiet" id="library-select-all" type="button">Select all</button>
      <button class="btn btn-quiet" id="library-deselect-all" type="button">Clear</button>
      <button class="btn btn-danger" id="library-bulk-delete" type="button">{icon("trash")}Delete</button>
    </div>
    {confirm_modal()}
    """


def input_row(
    label: str, name: str, value: str, input_type: str = "text", hint: str = ""
) -> str:
    hint_html = f'<small class="hint">{html.escape(hint)}</small>' if hint else ""
    return (
        f'<label class="field"><span class="field-label">{html.escape(label)}</span>'
        f'<input type="{input_type}" name="{name}" value="{html.escape(value or "")}">'
        f"{hint_html}</label>"
    )


def settings_modal(settings) -> str:
    return f"""
    <dialog id="settings-modal" class="modal" data-persist aria-labelledby="settings-title">
      <form class="settings-form" method="post" action="/settings" data-action="settings">
        <header class="modal-head">
          <div><h2 class="display-sm" id="settings-title">Settings</h2></div>
          <button type="button" class="btn btn-quiet btn-icon" data-close aria-label="Close">{icon("x")}</button>
        </header>
        <div class="modal-body settings-grid">
          <fieldset class="settings-card">
            <legend>Quasarr</legend>
            {input_row("Internal URL", "quasarr_url", settings.quasarr_url, hint="Used by Magazarr to reach Quasarr")}
            {input_row("External URL", "quasarr_external_url", settings.quasarr_external_url, hint="Used for browser links like CAPTCHA solving")}
            {input_row("API Key", "quasarr_api_key", settings.quasarr_api_key, "password")}
            <div class="field-pair">
              {input_row("Search Category", "quasarr_search_category", settings.quasarr_search_category)}
              {input_row("Download Category", "quasarr_download_category", settings.quasarr_download_category)}
            </div>
          </fieldset>
          <fieldset class="settings-card">
            <legend>Automation</legend>
            <div class="field-pair">
              {input_row("Search Interval Minutes", "automation_interval_minutes", str(settings.automation_interval_minutes), "number", "0 disables")}
              {input_row("Import Check Minutes", "import_check_interval_minutes", str(settings.import_check_interval_minutes), "number", "0 disables")}
            </div>
            {input_row("Past Days", "past_days", str(settings.past_days), "number", "Ignore releases older than this")}
            <div class="field-pair">
              {input_row("Min Size MB", "min_size_mb", str(settings.min_size_mb), "number")}
              {input_row("Max Size MB", "max_size_mb", str(settings.max_size_mb), "number", "0 means no limit")}
            </div>
          </fieldset>
          <fieldset class="settings-card">
            <legend>Storage</legend>
            {input_row("Library Dir", "library_dir", settings.library_dir, hint="Imported PDFs are moved here")}
          </fieldset>
          <fieldset class="settings-card">
            <legend>OPDS</legend>
            <label class="switch-row"><span><span class="field-label">Basic auth</span><small class="hint">Protect the OPDS feed with a username and password</small></span>
              <span class="switch"><input type="checkbox" name="opds_auth_enabled" {"checked" if settings.opds_auth_enabled else ""}><span class="switch-track"></span></span>
            </label>
            <div class="field-pair">
              {input_row("Username", "opds_username", settings.opds_username)}
              {input_row("Password", "opds_password", settings.opds_password, "password")}
            </div>
            {input_row("Page Size", "opds_page_size", str(settings.opds_page_size), "number")}
          </fieldset>
          <fieldset class="settings-card settings-wide">
            <legend>Notifications</legend>
            <div class="notification-providers">
              <section class="notification-provider">
                <h3 class="notification-provider-title"><img src="/static/discord-icon.png" alt="Discord logo" width="20" height="20"> Discord</h3>
                {input_row("Webhook URL", "discord_webhook_url", settings.discord_webhook_url, "password")}
              </section>
              <section class="notification-provider">
                <h3 class="notification-provider-title"><img src="/static/pushover-icon.png" alt="Pushover logo" width="20" height="20"> Pushover</h3>
                {input_row("API Token", "pushover_api_token", settings.pushover_api_token, "password")}
                {input_row("User or Group Key", "pushover_user_key", settings.pushover_user_key, "password")}
                <button type="submit" class="btn btn-line" formaction="/notifications/pushover/test">Save and Test</button>
              </section>
            </div>
            <p class="muted">Download starts are silent; imports and errors use normal alerts.</p>
          </fieldset>
        </div>
        <footer class="modal-foot">
          <button type="button" class="btn btn-quiet" data-close>Cancel</button>
          <button type="submit" class="btn btn-ink">Save Settings</button>
        </footer>
      </form>
    </dialog>
    """


def magazine_modal() -> str:
    tabs = (
        ("downloaded", "Issues"),
        ("downloading", "Downloading"),
        ("errors", "Errors"),
        ("skips", "Skipped"),
        ("duplicates", "Duplicates"),
        ("blocklist", "Blocklist"),
    )
    tab_html = "".join(
        f'<button type="button" role="tab" class="tab" data-tab="{kind}" aria-selected="false">'
        f'{label}<span class="tab-count" data-tab-count="{kind}"></span></button>'
        for kind, label in tabs
    )
    return f"""
    <dialog id="magazine-modal" class="sheet" aria-labelledby="magazine-modal-title">
      <header class="sheet-head">
        <div class="sheet-cover" id="magazine-modal-cover"></div>
        <div class="sheet-title">
          <h2 class="display-sm" id="magazine-modal-title">Magazine</h2>
          <p class="muted" id="magazine-modal-meta"></p>
          <div class="sheet-controls">
            <button type="button" class="btn btn-ink btn-sm" id="magazine-modal-search-now">{icon("search")}Search now</button>
            <label class="switch"><input type="checkbox" id="magazine-modal-active"><span class="switch-track"></span><span class="switch-label">Automatic search</span></label>
          </div>
        </div>
        <div class="sheet-head-actions">
          <div class="menu-wrap">
            <button type="button" class="btn btn-quiet btn-icon" id="magazine-modal-menu" aria-haspopup="menu" aria-expanded="false" aria-label="More">{icon("more")}</button>
            <div class="menu" role="menu" hidden>
              <button type="button" role="menuitem" class="is-danger" id="magazine-modal-delete">{icon("trash")}Delete magazine…</button>
            </div>
          </div>
          <button type="button" class="btn btn-quiet btn-icon" data-close aria-label="Close">{icon("x")}</button>
        </div>
      </header>
      <nav class="tabs" role="tablist" id="magazine-modal-tabs">{tab_html}</nav>
      <div class="sheet-tools" id="magazine-modal-tools">
        <label class="search-field grow">
          {icon("search")}
          <input type="search" id="magazine-modal-search" placeholder="Search" aria-label="Search">
        </label>
        <div class="tool-actions" id="magazine-modal-actions"></div>
      </div>
      <div id="magazine-modal-body" class="sheet-body"></div>
      <footer class="pager" id="magazine-modal-pager">
        <button type="button" class="btn btn-quiet btn-sm" id="magazine-modal-prev">{icon("chevron-left")}Newer</button>
        <span id="magazine-modal-page" class="muted"></span>
        <button type="button" class="btn btn-quiet btn-sm" id="magazine-modal-next">Older{icon("chevron-right")}</button>
      </footer>
    </dialog>
    """


def confirm_modal() -> str:
    return f"""
    <dialog id="confirm-modal" class="modal modal-sm confirm-dialog" aria-labelledby="confirm-title">
      <div class="confirm-body">
        <div class="confirm-icon">{icon("alert")}</div>
        <div>
          <h2 id="confirm-title">Are you sure?</h2>
          <p id="confirm-message" class="muted">This cannot be undone.</p>
        </div>
      </div>
      <footer class="modal-foot">
        <button type="button" class="btn btn-quiet" id="confirm-cancel">Cancel</button>
        <button type="button" class="btn btn-danger" id="confirm-accept">Delete</button>
      </footer>
    </dialog>
    """


def downloads_modal() -> str:
    return f"""
    <dialog id="downloads-modal" class="sheet" aria-labelledby="downloads-title">
      <header class="sheet-head is-plain">
        <div class="sheet-title">
          <h2 class="display-sm" id="downloads-title">Downloads</h2>
          <p class="muted" id="downloads-sub">Live from Quasarr</p>
        </div>
        <div class="sheet-head-actions">
          <a class="btn btn-quiet btn-sm" id="downloads-quasarr" href="#" target="_blank" rel="noreferrer" hidden>Open Quasarr{icon("external")}</a>
          <button type="button" class="btn btn-quiet btn-icon" data-close aria-label="Close">{icon("x")}</button>
        </div>
      </header>
      <div id="download-status" class="sheet-body download-list"></div>
    </dialog>
    """


def job_panel() -> str:
    return f"""
    <section class="job-panel" id="job-panel" hidden aria-labelledby="job-title">
      <header class="job-head">
        <span class="spinner" id="job-spinner"></span>{icon("check", "icon job-done-mark")}
        <div class="job-heading"><strong id="job-title">Search</strong><span class="muted" id="job-status">Queued</span></div>
        <button type="button" class="btn btn-quiet btn-icon btn-sm" id="job-toggle" aria-label="Collapse" aria-expanded="true">{icon("chevron-right", "icon chevron")}</button>
        <button type="button" class="btn btn-quiet btn-icon btn-sm" id="job-close" aria-label="Close">{icon("x")}</button>
      </header>
      <div class="job-queue" id="job-queue" hidden></div>
      <div class="job-results" id="job-results"></div>
    </section>
    """


def issue_viewer_page(issue) -> str:
    title = f"{issue['magazine_title']} - {issue['issue_key']}"
    body = f"""
    <section class="viewer-shell">
      <header class="viewer-bar">
        <a class="btn btn-quiet btn-icon" href="/library" aria-label="Back to library">{icon("arrow-left")}</a>
        <div class="viewer-title">
          <h1>{html.escape(title)}</h1>
          <div class="muted">{html.escape(issue["release_title"])}</div>
        </div>
        <a class="btn btn-line" href="/issues/{issue["id"]}/file" download>{icon("download")}<span class="hide-sm">Download</span></a>
        <a class="btn btn-ink" href="/issues/{issue["id"]}/file" target="_blank" rel="noreferrer">{icon("external")}<span class="hide-sm">Open PDF</span></a>
      </header>
      <iframe class="pdf-viewer" src="/issues/{issue["id"]}/file" title="{html.escape(title)}"></iframe>
      <p class="viewer-fallback">
        If PDF preview is unavailable, <a href="/issues/{issue["id"]}/file">open the PDF directly</a>.
      </p>
    </section>
    """
    return page(title, body, page_class="viewer-page")
