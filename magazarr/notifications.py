# -*- coding: utf-8 -*-

import json
from pathlib import Path

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
) -> bool:
    return send_discord(
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
) -> bool:
    cover_path = _cover_attachment_path(file_path)
    return send_discord(
        settings,
        "Import completed",
        release_title,
        fields={
            "Magazine": magazine_title,
            "Issue": issue_key,
            "File": str(file_path),
        },
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
) -> bool:
    fields = {}
    if magazine_title:
        fields["Magazine"] = magazine_title
    if release_title:
        fields["Release"] = release_title
    return send_discord(settings, title, message, fields=fields, silent=False)


def send_discord(
    settings: Settings,
    title: str,
    description: str,
    *,
    fields: dict[str, str] | None = None,
    image_path: str | Path | None = None,
    silent: bool = False,
) -> bool:
    webhook_url = settings.discord_webhook_url.strip()
    if not webhook_url:
        return False

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
    payload = {
        "username": "Magazarr",
        "embeds": [embed],
    }
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
