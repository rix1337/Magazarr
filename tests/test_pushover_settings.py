import json
from io import BytesIO
from unittest.mock import Mock
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest

from magazarr.settings import Settings, SettingsStore
from magazarr.web import create_app, settings_modal


def _post(app, path, form):
    payload = urlencode(form).encode()
    environ = {}
    setup_testing_defaults(environ)
    environ.update(
        REQUEST_METHOD="POST",
        PATH_INFO=path,
        CONTENT_TYPE="application/x-www-form-urlencoded",
        CONTENT_LENGTH=str(len(payload)),
    )
    environ["wsgi.input"] = BytesIO(payload)
    result = {}

    def start_response(status, headers, exc_info=None):
        result["status"] = status
        result["headers"] = dict(headers)

    result["body"] = b"".join(app(environ, start_response))
    return result


def test_existing_settings_load_with_pushover_disabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"discord_webhook_url": "synthetic-webhook"}))
    settings = SettingsStore(path).load()

    assert settings.discord_webhook_url == "synthetic-webhook"
    assert settings.pushover_api_token == ""
    assert settings.pushover_user_key == ""


def test_optional_pushover_settings_roundtrip_and_clear(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.update_from_form({}).pushover_api_token == ""
    settings = store.update_from_form(
        {"pushover_api_token": " A" + "A" * 29 + " ", "pushover_user_key": "B" * 30}
    )
    assert settings.pushover_api_token == "A" * 30
    assert store.load().pushover_user_key == "B" * 30
    assert store.update_from_form({}).pushover_user_key == "B" * 30
    cleared = store.update_from_form(
        {"pushover_api_token": "", "pushover_user_key": ""}
    )
    assert cleared.pushover_api_token == ""
    assert store.load().pushover_user_key == ""


@pytest.mark.parametrize(
    "token,key",
    [("A" * 30, ""), ("", "B" * 30), ("A" * 29, "B" * 30), ("A" * 30, "B" * 29 + "!")],
)
def test_invalid_credentials_do_not_save_settings(tmp_path, token, key):
    store = SettingsStore(tmp_path / "settings.json")
    before = store.path.read_text()
    with pytest.raises(ValueError, match="Pushover"):
        store.update_from_form({"pushover_api_token": token, "pushover_user_key": key})
    assert store.path.read_text() == before


def test_settings_form_has_matching_provider_sections_and_static_icons():
    rendered = settings_modal(Settings(pushover_api_token='synthetic"<token'))
    assert 'name="pushover_api_token"' in rendered
    assert 'name="pushover_user_key"' in rendered
    assert 'type="password" name="pushover_api_token"' in rendered
    assert 'value="synthetic&quot;&lt;token"' in rendered
    assert 'src="/static/discord-icon.png"' in rendered
    assert 'src="/static/pushover-icon.png"' in rendered
    assert 'formaction="/notifications/pushover/test"' in rendered
    assert 'class="notification-providers"' in rendered
    assert 'class="notification-provider-title"' in rendered
    assert "> Discord</h3>" in rendered
    assert "> Pushover</h3>" in rendered
    assert "Webhook URL" in rendered
    assert "API Token" in rendered
    assert "Save and Test" in rendered
    assert "(optional)" not in rendered
    assert "Leave both" not in rendered
    assert (
        "Download starts are silent; imports and errors use normal alerts." in rendered
    )
    assert "required" not in rendered


def test_settings_route_accepts_blank_pushover_and_rejects_partial_pair(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    app = create_app(store, Mock())
    result = _post(app, "/settings", {})
    assert result["status"].startswith("302")
    result = _post(app, "/settings", {"pushover_api_token": "A" * 30})
    assert result["status"].startswith("400")
    assert store.load().pushover_api_token == ""


@pytest.mark.parametrize("sent,status", [(True, "302"), (False, "502")])
def test_pushover_test_saves_credentials_and_sends_normal_alert(
    tmp_path, monkeypatch, sent, status
):
    store = SettingsStore(tmp_path / "settings.json")
    sender = Mock(return_value=sent)
    monkeypatch.setattr("magazarr.web.send_pushover", sender)
    result = _post(
        create_app(store, Mock()),
        "/notifications/pushover/test",
        {"pushover_api_token": "A" * 30, "pushover_user_key": "B" * 30},
    )
    assert result["status"].startswith(status)
    assert store.load().pushover_api_token == "A" * 30
    assert sender.call_args.args[0].pushover_user_key == "B" * 30
    assert sender.call_args.kwargs["silent"] is False
    assert sender.call_args.kwargs["image_path"].name == "magazarr-icon.png"


def test_pushover_test_requires_credentials_without_sending(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    sender = Mock()
    monkeypatch.setattr("magazarr.web.send_pushover", sender)
    result = _post(create_app(store, Mock()), "/notifications/pushover/test", {})
    assert result["status"].startswith("400")
    sender.assert_not_called()
