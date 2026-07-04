import json

from magazarr.notifications import (
    DISCORD_SUPPRESS_NOTIFICATIONS,
    notify_download_started,
    notify_error,
    notify_import_success,
)
from magazarr.settings import Settings

WEBHOOK_URL = "https://discord.example.test/webhook"


class Response:
    def __init__(self, status_code=204, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def test_no_webhook_sends_no_notification(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)

    assert not notify_error(Settings(), "Import error", "broken")
    assert calls == []


def test_download_started_is_silent_tracked_discord_message(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(status_code=200, json_data={"id": "msg-1"})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = Settings(discord_webhook_url=WEBHOOK_URL)

    reference = notify_download_started(
        settings, "Magazine Title", "Magazine Title 2026 04 30", "pkg"
    )

    assert reference == {
        "message_id": "msg-1",
        "webhook_fingerprint": reference["webhook_fingerprint"],
        "silent": True,
    }
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url.startswith(WEBHOOK_URL)
    assert "wait=true" in url
    payload = json.loads(kwargs["data"])
    assert payload["flags"] == DISCORD_SUPPRESS_NOTIFICATIONS
    assert payload["embeds"][0]["fields"][0] == {
        "name": "Magazine",
        "value": "Magazine Title",
        "inline": False,
    }


def test_import_success_edits_tracked_message(monkeypatch, tmp_path):
    post_calls = []
    patch_calls = []

    def fake_post(url, **kwargs):
        post_calls.append((url, kwargs))
        return Response(status_code=200, json_data={"id": "msg-1"})

    def fake_patch(url, **kwargs):
        patch_calls.append((url, kwargs))
        return Response(status_code=200)

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    monkeypatch.setattr("magazarr.notifications.requests.patch", fake_patch)

    reference = {
        "message_id": "msg-1",
        "webhook_fingerprint": "ignored-in-test",
        "silent": True,
    }
    pdf = tmp_path / "issue.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-cover")
    monkeypatch.setattr("magazarr.notifications.extract_pdf_cover", lambda _path: cover)

    settings = Settings(discord_webhook_url=WEBHOOK_URL)
    monkeypatch.setattr(
        "magazarr.notifications._webhook_fingerprint",
        lambda _url: reference["webhook_fingerprint"],
    )

    assert notify_import_success(
        settings,
        "Magazine Title",
        "Magazine Title 2026 04 30",
        "2026-04-30",
        pdf,
        reference=reference,
    )

    assert len(post_calls) == 0
    assert len(patch_calls) == 1
    url, kwargs = patch_calls[0]
    assert "/messages/msg-1" in url
    payload = json.loads(kwargs["data"]["payload_json"])
    assert payload["embeds"][0]["title"] == "Import completed"
    assert payload["embeds"][0]["image"] == {"url": "attachment://cover.png"}
    assert payload["attachments"] == [{"id": 0, "filename": "cover.png"}]
    assert "flags" not in payload


def test_import_success_without_reference_sends_new_message(monkeypatch, tmp_path):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = Settings(discord_webhook_url=WEBHOOK_URL)

    pdf = tmp_path / "issue.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    assert notify_import_success(
        settings,
        "Magazine Title",
        "Magazine Title 2026 04 30",
        "2026-04-30",
        pdf,
    )

    assert len(calls) == 1
    _url, kwargs = calls[0]
    payload = json.loads(kwargs["data"])
    assert payload["embeds"][0]["title"] == "Import completed"
    assert "flags" not in payload


def test_error_edits_tracked_message_and_sends_follow_up(monkeypatch):
    post_calls = []
    patch_calls = []

    def fake_post(url, **kwargs):
        post_calls.append((url, kwargs))
        return Response()

    def fake_patch(url, **kwargs):
        patch_calls.append((url, kwargs))
        return Response(status_code=200)

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    monkeypatch.setattr("magazarr.notifications.requests.patch", fake_patch)

    reference = {
        "message_id": "msg-1",
        "webhook_fingerprint": "ignored-in-test",
        "silent": True,
    }
    settings = Settings(discord_webhook_url=WEBHOOK_URL)
    monkeypatch.setattr(
        "magazarr.notifications._webhook_fingerprint",
        lambda _url: reference["webhook_fingerprint"],
    )

    assert notify_error(
        settings,
        "Download error",
        "Download failed",
        magazine_title="Magazine Title",
        release_title="Magazine Title 2026 04 30",
        reference=reference,
    )

    assert len(patch_calls) == 1
    url, kwargs = patch_calls[0]
    assert "/messages/msg-1" in url
    payload = json.loads(kwargs["data"])
    assert payload["embeds"][0]["title"] == "Download error"
    assert "flags" not in payload

    assert len(post_calls) == 1
    url, kwargs = post_calls[0]
    assert url == WEBHOOK_URL
    payload = json.loads(kwargs["data"])
    assert payload["embeds"][0]["title"] == "Download error"
    assert "flags" not in payload


def test_error_without_reference_sends_new_message(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = Settings(discord_webhook_url=WEBHOOK_URL)

    assert notify_error(
        settings,
        "Download error",
        "Download failed",
        magazine_title="Magazine Title",
        release_title="Magazine Title 2026 04 30",
    )

    assert len(calls) == 1
    _url, kwargs = calls[0]
    payload = json.loads(kwargs["data"])
    assert "flags" not in payload


def test_import_success_attaches_pdf_cover(monkeypatch, tmp_path):
    calls = []
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-cover")
    pdf = tmp_path / "issue.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def fake_post(url, **kwargs):
        file_item = kwargs["files"]["files[0]"]
        kwargs["files"]["files[0]"] = (file_item[0], file_item[1].read(), file_item[2])
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    monkeypatch.setattr("magazarr.notifications.extract_pdf_cover", lambda _path: cover)
    settings = Settings(discord_webhook_url=WEBHOOK_URL)

    assert notify_import_success(
        settings,
        "Magazine Title",
        "Magazine Title 2026 04 30",
        "2026-04-30",
        pdf,
    )

    _url, kwargs = calls[0]
    payload = json.loads(kwargs["data"]["payload_json"])
    assert payload["embeds"][0]["image"] == {"url": "attachment://cover.png"}
    assert payload["attachments"] == [{"id": 0, "filename": "cover.png"}]
    assert "headers" not in kwargs
    assert kwargs["files"]["files[0]"][0] == "cover.png"
    assert kwargs["files"]["files[0]"][1] == b"png-cover"
    assert kwargs["files"]["files[0]"][2] == "image/png"
