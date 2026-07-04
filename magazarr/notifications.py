# -*- coding: utf-8 -*-

import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from loguru import logger

from magazarr.cover import COVER_MIME, CoverError, extract_pdf_cover
from magazarr.settings import Settings

DISCORD_SUPPRESS_NOTIFICATIONS = 1 << 12
DISCORD_TIMEOUT_SECONDS = 15


def notify_download_started(
    settings: Settings,
    magazine_title: str,
    release_title: str,
    package_id: str | None = None,
) -> dict | None:
    """Send a silent tracked Discord message when a download starts.

    Returns a reference dict that can later be passed to ``notify_import_success``
    or ``notify_error`` to edit the message in place.
    """
    return send_tracked_discord(
        settings,
        "Download started",
        release_title,
        fields={
            "Magazine": magazine_title,
            "Package": package_id or "unknown",
        },
        silent=True,
    )


def notify_import_success(
    settings: Settings,
    magazine_title: str,
    release_title: str,
    issue_key: str,
    file_path: str | Path,
    reference: dict | None = None,
) -> bool:
    """Notify about a successful import.

    If a tracked ``reference`` from ``notify_download_started`` is provided, the
    original message is edited in place and the extracted PDF cover is attached.
    """
    cover_path = _cover_attachment_path(file_path)
    fields = {
        "Magazine": magazine_title,
        "Issue": issue_key,
        "File": str(file_path),
    }
    if reference:
        return edit_discord(
            settings,
            reference,
            "Import completed",
            release_title,
            fields=fields,
            image_path=cover_path,
            silent=False,
        )
    return send_discord(
        settings,
        "Import completed",
        release_title,
        fields=fields,
        image_path=cover_path,
        silent=False,
    )


def notify_error(
    settings: Settings,
    title: str,
    message: str,
    *,
    magazine_title: str = "",
    release_title: str = "",
    reference: dict | None = None,
) -> bool:
    """Notify about an error.

    If a tracked ``reference`` is provided, the original message is edited and a
    non-silent follow-up is sent when the original message was previously silent.
    """
    fields = {}
    if magazine_title:
        fields["Magazine"] = magazine_title
    if release_title:
        fields["Release"] = release_title

    if reference:
        edited = edit_discord(
            settings,
            reference,
            title,
            message,
            fields=fields,
            silent=False,
        )
        if edited and reference.get("silent"):
            send_discord(
                settings,
                title,
                message,
                fields=fields,
                silent=False,
            )
        return edited
    return send_discord(
        settings,
        title,
        message,
        fields=fields,
        silent=False,
    )


def send_discord(
    settings: Settings,
    title: str,
    description: str,
    *,
    fields: dict[str, str] | None = None,
    image_path: str | Path | None = None,
    silent: bool = False,
) -> bool:
    """Send a rendered Discord webhook notification. Returns True on success."""
    webhook_url = settings.discord_webhook_url.strip()
    if not webhook_url:
        return False

    embed, attachment = _build_embed(title, description, fields, image_path)
    payload = {
        "username": "Magazarr",
        "embeds": [embed],
    }
    if attachment:
        payload["attachments"] = [{"id": 0, "filename": attachment.name}]
    if silent:
        payload["flags"] = DISCORD_SUPPRESS_NOTIFICATIONS

    try:
        if attachment:
            with attachment.path.open("rb") as handle:
                response = requests.post(
                    webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": (attachment.name, handle, COVER_MIME)},
                    timeout=DISCORD_TIMEOUT_SECONDS,
                )
        else:
            response = requests.post(
                webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=DISCORD_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        logger.warning(f"Discord notification error: {exc}")
        return False

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            f"Discord notification failed with status {response.status_code}"
        )
        return False
    return True


def send_tracked_discord(
    settings: Settings,
    title: str,
    description: str,
    *,
    fields: dict[str, str] | None = None,
    image_path: str | Path | None = None,
    silent: bool = False,
) -> dict | None:
    """Send a Discord message and return a reference for later edits."""
    webhook_url = settings.discord_webhook_url.strip()
    if not webhook_url:
        return None

    embed, attachment = _build_embed(title, description, fields, image_path)
    payload = {
        "username": "Magazarr",
        "embeds": [embed],
    }
    if attachment:
        payload["attachments"] = [{"id": 0, "filename": attachment.name}]
    if silent:
        payload["flags"] = DISCORD_SUPPRESS_NOTIFICATIONS

    try:
        if attachment:
            with attachment.path.open("rb") as handle:
                response = requests.post(
                    _build_webhook_url(webhook_url, wait=True),
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": (attachment.name, handle, COVER_MIME)},
                    timeout=DISCORD_TIMEOUT_SECONDS,
                )
        else:
            response = requests.post(
                _build_webhook_url(webhook_url, wait=True),
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=DISCORD_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        logger.warning(f"Discord notification error: {exc}")
        return None

    if response.status_code != 200:
        logger.warning(
            f"Discord notification failed with status {response.status_code}"
        )
        return None

    try:
        response_data = response.json()
    except (TypeError, ValueError):
        logger.warning("Discord webhook did not return a valid message response.")
        return None

    message_id = response_data.get("id") if isinstance(response_data, dict) else None
    if not message_id:
        logger.warning("Discord webhook message response did not include an ID.")
        return None

    return {
        "message_id": str(message_id),
        "webhook_fingerprint": _webhook_fingerprint(webhook_url),
        "silent": bool(silent),
    }


def edit_discord(
    settings: Settings,
    reference: dict,
    title: str,
    description: str,
    *,
    fields: dict[str, str] | None = None,
    image_path: str | Path | None = None,
    silent: bool = False,
) -> bool:
    """Edit a tracked Discord webhook message. Returns True on success."""
    webhook_url = settings.discord_webhook_url.strip()
    if not webhook_url or not isinstance(reference, dict):
        return False

    message_id = reference.get("message_id")
    webhook_fingerprint = reference.get("webhook_fingerprint")
    if not message_id or webhook_fingerprint != _webhook_fingerprint(webhook_url):
        return False

    embed, attachment = _build_embed(title, description, fields, image_path)
    payload = {"embeds": [embed]}
    if attachment:
        payload["attachments"] = [{"id": 0, "filename": attachment.name}]
    if silent:
        payload["flags"] = DISCORD_SUPPRESS_NOTIFICATIONS

    try:
        if attachment:
            with attachment.path.open("rb") as handle:
                response = requests.patch(
                    _build_webhook_url(webhook_url, message_id=message_id),
                    data={"payload_json": json.dumps(payload)},
                    files={"files[0]": (attachment.name, handle, COVER_MIME)},
                    timeout=DISCORD_TIMEOUT_SECONDS,
                )
        else:
            response = requests.patch(
                _build_webhook_url(webhook_url, message_id=message_id),
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=DISCORD_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        logger.warning(f"Discord notification edit error: {exc}")
        return False

    if response.status_code != 200:
        logger.warning(
            f"Discord notification edit failed with status {response.status_code}"
        )
        return False
    return True


def _build_embed(
    title: str,
    description: str,
    fields: dict[str, str] | None,
    image_path: str | Path | None,
) -> tuple[dict, "_DiscordAttachment | None"]:
    embed = {
        "title": title,
        "description": description,
        "fields": [
            {"name": name, "value": value or "-", "inline": False}
            for name, value in (fields or {}).items()
        ],
    }
    attachment = _discord_attachment(image_path)
    if attachment:
        embed["image"] = {"url": f"attachment://{attachment.name}"}
    return embed, attachment


def _cover_attachment_path(file_path: str | Path) -> Path | None:
    pdf_path = Path(file_path)
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        return None
    try:
        return extract_pdf_cover(pdf_path)
    except CoverError:
        return None


class _DiscordAttachment:
    def __init__(self, path: Path, name: str):
        self.path = path
        self.name = name


def _discord_attachment(image_path: str | Path | None) -> _DiscordAttachment | None:
    if not image_path:
        return None
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return None
    return _DiscordAttachment(path, "cover.png")


def _build_webhook_url(
    webhook_url: str, message_id: str | None = None, wait: bool = False
) -> str:
    parts = urlsplit(webhook_url)
    path = parts.path.rstrip("/")
    if message_id is not None:
        path = f"{path}/messages/{quote(str(message_id), safe='')}"

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.pop("wait", None)
    if wait:
        query["wait"] = "true"

    return urlunsplit(
        (parts.scheme, parts.netloc, path, urlencode(query), parts.fragment)
    )


def _webhook_fingerprint(webhook_url: str) -> str:
    normalized_url = _build_webhook_url(webhook_url)
    return sha256(normalized_url.encode("utf-8")).hexdigest()
