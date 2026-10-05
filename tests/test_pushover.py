from pathlib import Path

import requests

from magazarr.notifications import (
    PUSHOVER_MAX_ATTACHMENT_BYTES,
    PUSHOVER_MAX_MESSAGE_LENGTH,
    PUSHOVER_MAX_TITLE_LENGTH,
    notify_download_started,
    notify_error,
    notify_import_success,
    send_pushover,
)
from magazarr.settings import Settings


class Response:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def pushover_settings():
    settings = Settings()
    settings.pushover_api_token = "synthetic-token"
    settings.pushover_user_key = "synthetic-user"
    return settings


def test_missing_credentials_do_not_send(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "magazarr.notifications.requests.post",
        lambda *args, **kwargs: calls.append(args),
    )

    assert not send_pushover(Settings(), "Synthetic title", "Synthetic message")
    assert calls == []


def test_success_sets_priority_and_bounds_fields(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    settings = pushover_settings()

    assert send_pushover(
        settings,
        "T" * (PUSHOVER_MAX_TITLE_LENGTH + 10),
        "M" * (PUSHOVER_MAX_MESSAGE_LENGTH + 10),
        fields={"Field": "value"},
        silent=True,
    )
    payload = calls[0][1]["data"]
    assert payload["priority"] == -2
    assert payload["html"] == 1
    assert len(payload["title"]) == PUSHOVER_MAX_TITLE_LENGTH
    assert len(payload["message"]) == PUSHOVER_MAX_MESSAGE_LENGTH

    assert send_pushover(settings, "Synthetic title", "Synthetic message", silent=False)
    assert calls[1][1]["data"]["priority"] == 0


def test_message_uses_escaped_html_fields_and_title_once(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)

    assert send_pushover(
        pushover_settings(),
        'Synthetic <title> & "quoted"',
        'Description <unsafe> & "quoted"',
        fields={"Field <name>": 'Value & "quoted"', "Later": "kept"},
    )

    payload = calls[0]["data"]
    assert payload["title"] == 'Synthetic <title> & "quoted"'
    assert payload["message"] == (
        "Description &lt;unsafe&gt; &amp; &quot;quoted&quot;\n\n"
        "<b>Field &lt;name&gt;:</b>\nValue &amp; &quot;quoted&quot;\n\n"
        "<b>Later:</b>\nkept"
    )
    assert payload["message"].count("Synthetic") == 0


def test_oversized_fields_are_skipped_and_later_fields_survive(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    oversized = "x" * PUSHOVER_MAX_MESSAGE_LENGTH

    assert send_pushover(
        pushover_settings(),
        "Synthetic title",
        "Description",
        fields={"Oversized": oversized, "Later": "kept"},
    )

    message = calls[0]["data"]["message"]
    assert len(message) <= PUSHOVER_MAX_MESSAGE_LENGTH
    assert "Oversized" not in message
    assert "<b>Later:</b>\nkept" in message


def test_description_bound_does_not_cut_html_entities(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    assert send_pushover(
        pushover_settings(),
        "Synthetic title",
        "<" * PUSHOVER_MAX_MESSAGE_LENGTH,
    )

    message = calls[0]["data"]["message"]
    assert len(message) <= PUSHOVER_MAX_MESSAGE_LENGTH
    assert message.endswith("&lt;")


def test_api_and_network_failures_return_false(monkeypatch):
    settings = pushover_settings()
    monkeypatch.setattr(
        "magazarr.notifications.requests.post",
        lambda *args, **kwargs: Response(json_data={"status": 0}),
    )
    assert not send_pushover(settings, "Synthetic title", "Synthetic message")

    def fail(*args, **kwargs):
        raise requests.RequestException("synthetic failure")

    monkeypatch.setattr("magazarr.notifications.requests.post", fail)
    assert not send_pushover(settings, "Synthetic title", "Synthetic message")


def test_cover_attachment_is_uploaded(monkeypatch, tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"synthetic png")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)

    assert send_pushover(
        pushover_settings(),
        "Synthetic title",
        "Synthetic message",
        image_path=cover,
    )
    attachment = calls[0]["files"]["attachment"]
    assert attachment == ("cover.png", b"synthetic png", "image/png")


def test_missing_unreadable_empty_or_oversized_cover_falls_back_to_text(
    monkeypatch, tmp_path
):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response(json_data={"status": 1})

    monkeypatch.setattr("magazarr.notifications.requests.post", fake_post)
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    oversized = tmp_path / "oversized.png"
    oversized.write_bytes(b"x" * (PUSHOVER_MAX_ATTACHMENT_BYTES + 1))

    for image_path in (tmp_path / "missing.png", empty, oversized):
        assert send_pushover(
            pushover_settings(),
            "Synthetic title",
            "Synthetic message",
            image_path=image_path,
        )
        assert calls[-1]["files"] is None

    unreadable = tmp_path / "unreadable.png"
    unreadable.write_bytes(b"synthetic png")
    original_read_bytes = Path.read_bytes

    def fail_read_bytes(path):
        if path == unreadable:
            raise OSError("synthetic read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    assert send_pushover(
        pushover_settings(),
        "Synthetic title",
        "Synthetic message",
        image_path=unreadable,
    )
    assert calls[-1]["files"] is None


def test_http_and_malformed_json_failures_return_false(monkeypatch):
    settings = pushover_settings()
    monkeypatch.setattr(
        "magazarr.notifications.requests.post",
        lambda *args, **kwargs: Response(status_code=500, json_data={"status": 1}),
    )
    assert not send_pushover(settings, "Synthetic title", "Synthetic message")
    monkeypatch.setattr(
        "magazarr.notifications.requests.post",
        lambda *args, **kwargs: Response(status_code=200, json_data=["synthetic"]),
    )
    assert not send_pushover(settings, "Synthetic title", "Synthetic message")


def test_download_started_keeps_discord_reference_and_sends_pushover(monkeypatch):
    reference = {"message_id": "synthetic-id", "silent": True}
    calls = []
    monkeypatch.setattr(
        "magazarr.notifications.send_tracked_discord", lambda *a, **k: reference
    )
    monkeypatch.setattr(
        "magazarr.notifications.send_pushover",
        lambda *a, **k: calls.append((a, k)) or True,
    )

    result = notify_download_started(
        pushover_settings(),
        "Synthetic magazine",
        "Synthetic release",
        "synthetic-package",
    )

    assert result is reference
    assert calls[0][1]["silent"] is True


def test_import_success_sends_pushover_with_or_without_discord_reference(
    monkeypatch, tmp_path
):
    pdf = tmp_path / "issue.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    pushover_calls = []
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"synthetic png")
    monkeypatch.setattr(
        "magazarr.notifications._cover_attachment_path", lambda _path: cover
    )
    monkeypatch.setattr(
        "magazarr.notifications.send_pushover",
        lambda *a, **k: pushover_calls.append(k) or True,
    )
    monkeypatch.setattr("magazarr.notifications.send_discord", lambda *a, **k: False)
    monkeypatch.setattr("magazarr.notifications.edit_discord", lambda *a, **k: False)

    assert notify_import_success(
        pushover_settings(),
        "Synthetic magazine",
        "Synthetic release",
        "synthetic-issue",
        pdf,
    )
    assert notify_import_success(
        pushover_settings(),
        "Synthetic magazine",
        "Synthetic release",
        "synthetic-issue",
        pdf,
        reference={"message_id": "synthetic-id"},
    )
    assert len(pushover_calls) == 2
    assert all(call["image_path"] == cover for call in pushover_calls)
    assert all(call["silent"] is False for call in pushover_calls)


def test_error_returns_pushover_success_when_discord_fails(monkeypatch):
    calls = []
    monkeypatch.setattr("magazarr.notifications.send_discord", lambda *a, **k: False)
    monkeypatch.setattr("magazarr.notifications.edit_discord", lambda *a, **k: False)
    monkeypatch.setattr(
        "magazarr.notifications.send_pushover", lambda *a, **k: calls.append(k) or True
    )

    assert notify_error(pushover_settings(), "Synthetic error", "Synthetic message")
    assert notify_error(
        pushover_settings(),
        "Synthetic error",
        "Synthetic message",
        reference={"message_id": "synthetic-id"},
    )
    assert len(calls) == 2
