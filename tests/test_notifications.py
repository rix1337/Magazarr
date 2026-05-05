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


def test_import_success_attaches_pdf_cover(monkeypatch, tmp_path):
    calls = []
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-cover")
    pdf = tmp_path / "issue.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def fake_post(*args, **kwargs):
        file_item = kwargs["files"]["files[0]"]
        kwargs["files"]["files[0]"] = (file_item[0], file_item[1].read(), file_item[2])
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    monkeypatch.setattr("magazarr.notifications.extract_pdf_cover", lambda _path: cover)
    settings = Settings(discord_webhook_url="https://discord.example.test/webhook")

    assert notify_import_success(
        settings,
        "Magazine Title",
        "Magazine Title 2026 04 30",
        "2026-04-30",
        pdf,
    )

    _args, kwargs = calls[0]
    payload = json.loads(kwargs["data"]["payload_json"])
    assert payload["embeds"][0]["image"] == {"url": "attachment://cover.png"}
    assert "headers" not in kwargs
    assert kwargs["files"]["files[0]"][0] == "cover.png"
    assert kwargs["files"]["files[0]"][1] == b"png-cover"
    assert kwargs["files"]["files[0]"][2] == "image/png"
