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

## Notifications

- `magazarr/settings.py` stores optional Discord and Pushover credentials in the existing JSON settings. Pushover is disabled when both fields are blank; configuring it requires a 30-character alphanumeric application token and user or group key.
- `magazarr/web.py` owns the settings form and Pushover test route. Tests save the form before sending a normal alert. Icons are PNG assets served through `/static/`.
- Present Discord and Pushover with matching headings, icons, and credential layouts. Both providers are optional; omit optional labels and setup hints from the UI. Provider sections sit side by side on desktop and stack on mobile.
- `magazarr/notifications.py` sends download starts silently and imports/errors with normal alerts to each configured provider independently. Pushover messages use escaped HTML with bold field labels, blank-line field separation, a 1024-character entity-safe bound, and silent priority `-2`; imports/errors use priority `0`. Import notifications reuse the extracted PDF cover, with Pushover attachments limited to 5 MiB and text fallback when the cover cannot be attached.
- Discord message references remain the only persisted tracking state. Pushover sends a new message for each lifecycle event.
- Keep notification setup optional. Preserve existing Discord tracking and return contracts when adding providers.
- Notification tests use mocked HTTP requests and synthetic titles and URLs. Run `uv run pytest` for verification.
