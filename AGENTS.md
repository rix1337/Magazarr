## Trail workflow

Use Trail first for repository investigation: `trail map`, `trail ask "<task or literal identifier>" --source`, and focused `trail grep`, `trail skeleton`, or `trail callers` queries. Build with `trail build` when `.trail/cache/` is absent. If Trail is missing, stop work and ask the user to install it with `npm install -g @trailhq/trail-code`; do not continue without it.

Keep the generated map gitignored. Share only concise architectural decisions and reasons in `.trail/learnings/`, using `## Decided`, `## Watch out`, exact repository-relative `touches`, and generic `author: Magazarr`. Never put real source hostnames, credentials, personal metadata, or absolute local paths in shared notes.

## Pre-commit hook

Run `pre-commit.py` before committing to catch lint errors and ensure the version is bumped vs main:

```bash
echo 'uv run pre-commit.py' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

Or run manually:

```bash
uv run pre-commit.py
```

CI runs it automatically with `--ci` which auto-fixes and pushes any changes.
Release CI on `main` keeps the committed version; version comparison and bumps apply to local runs and pull-request CI.

## Notifications

- `magazarr/settings.py` stores optional Discord and Pushover credentials in the existing JSON settings. Pushover is disabled when both fields are blank; configuring it requires a 30-character alphanumeric application token and user or group key.
- `magazarr/web.py` owns the settings form and Pushover test route. Tests save the form before sending a normal alert. Icons are PNG assets served through `/static/`.
- Present Discord and Pushover with matching headings, icons, and credential layouts. Both providers are optional; omit optional labels and setup hints from the UI. Provider sections sit side by side on desktop and stack on mobile.
- `magazarr/notifications.py` sends download starts silently and imports/errors with normal alerts to each configured provider independently. Pushover messages use escaped HTML with bold field labels, blank-line field separation, a 1024-character entity-safe bound, and silent priority `-2`; imports/errors use priority `0`. Import notifications reuse the extracted PDF cover, with Pushover attachments limited to 5 MiB and text fallback when the cover cannot be attached.
- Discord message references remain the only persisted tracking state. Pushover sends a new message for each lifecycle event.
- Keep notification setup optional. Preserve existing Discord tracking and return contracts when adding providers.
- Notification tests use mocked HTTP requests and synthetic titles and URLs. Run `uv run pytest` for verification.

## Web UI

- `magazarr/ui.py` renders page shells and dialogs; `assets/app.js` (dashboard), `assets/library.js` and shared `assets/common.js` render dynamic content. `magazarr/web.py` keeps routes and JSON payloads.
- The dashboard polls `/api/dashboard` JSON and patches magazine cards by id. Never replace containers that hold inputs; per-magazine editing such as the blocklist lives in the magazine sheet.
- Form routes still redirect for non-JS use. The UI posts with `Accept: application/json`, treats the redirect as success, and shows JSON error bodies as toasts.
- Static assets are cached as immutable under a version-and-mtime query key from `ui.asset_url`.
- Duplicate detection renders PDF first pages; load it lazily per magazine, never on the dashboard poll.
