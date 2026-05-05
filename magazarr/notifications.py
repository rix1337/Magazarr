# -*- coding: utf-8 -*-

import json
from pathlib import Path

import requests
from loguru import logger

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
    return send_discord(
        settings,
        "Import completed",
        release_title,
        fields={
            "Magazine": magazine_title,
            "Issue": issue_key,
            "File": str(file_path),
        },
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
    payload = {
        "username": "Magazarr",
        "embeds": [embed],
    }
    if silent:
        payload["flags"] = DISCORD_SUPPRESS_NOTIFICATIONS

    try:
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
