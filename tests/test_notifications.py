import json

from magazarr.notifications import (
    DISCORD_SUPPRESS_NOTIFICATIONS,
    notify_download_started,
    notify_error,
    notify_import_success,
)
from magazarr.settings import Settings


class Response:
    status_code = 204


def test_no_webhook_sends_no_notification(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)

    assert not notify_error(Settings(), "Import error", "broken")
    assert calls == []


def test_download_started_is_silent_discord_message(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example.test/webhook")

    assert notify_download_started(settings, "Magazine Title", "Magazine Title 2026 04 30", "pkg")

    _args, kwargs = calls[0]
    payload = json.loads(kwargs["data"])
    assert payload["flags"] == DISCORD_SUPPRESS_NOTIFICATIONS
    assert payload["embeds"][0]["fields"][0] == {
        "name": "Magazine",
        "value": "Magazine Title",
        "inline": False,
    }


def test_import_success_and_error_are_not_silent(monkeypatch, tmp_path):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = Settings(discord_webhook_url="https://discord.example.test/webhook")

    assert notify_import_success(
        settings,
        "Magazine Title",
        "Magazine Title 2026 04 30",
        "2026-04-30",
        tmp_path / "issue.pdf",
    )
    assert notify_error(
        settings,
        "Download error",
        "Download failed",
        magazine_title="Magazine Title",
        release_title="Magazine Title 2026 04 30",
    )

    for _args, kwargs in calls:
        payload = json.loads(kwargs["data"])
        assert "flags" not in payload
    assert calls[0][1]["headers"] == {"Content-Type": "application/json"}
