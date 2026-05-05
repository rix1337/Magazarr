# -*- coding: utf-8 -*-

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Settings:
    quasarr_url: str = "http://127.0.0.1:8080"
    quasarr_external_url: str = ""
    quasarr_api_key: str = ""
    discord_webhook_url: str = ""
    quasarr_search_category: str = "7000"
    quasarr_download_category: str = "docs"
    past_days: int = 45
    min_size_mb: int = 1
    max_size_mb: int = 0
    library_dir: str = "library"
    import_root: str = ""
    automation_interval_minutes: int = 60
    import_check_interval_minutes: int = 5
    opds_auth_enabled: bool = False
    opds_username: str = ""
    opds_password: str = ""
    opds_page_size: int = 50


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(Settings())

    def load(self) -> Settings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        defaults = asdict(Settings())
        defaults.update({k: v for k, v in data.items() if k in defaults})
        return Settings(**defaults)

    def save(self, settings: Settings):
        self.path.write_text(
            json.dumps(asdict(settings), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def update_from_form(self, form):
        settings = self.load()
        settings.quasarr_url = str(form.get("quasarr_url", "")).strip().rstrip("/")
        settings.quasarr_external_url = (
            str(form.get("quasarr_external_url", "")).strip().rstrip("/")
        )
        settings.quasarr_api_key = str(form.get("quasarr_api_key", "")).strip()
        settings.discord_webhook_url = str(
            form.get("discord_webhook_url", "")
        ).strip()
        settings.quasarr_search_category = str(
            form.get("quasarr_search_category", "7000")
        ).strip()
        settings.quasarr_download_category = str(
            form.get("quasarr_download_category", "docs")
        ).strip()
        settings.past_days = _int(form.get("past_days"), 45)
        settings.min_size_mb = _int(form.get("min_size_mb"), 1)
        settings.max_size_mb = _int(form.get("max_size_mb"), 0)
        settings.library_dir = str(form.get("library_dir", "")).strip()
        settings.import_root = str(form.get("import_root", "")).strip()
        settings.automation_interval_minutes = _int(
            form.get("automation_interval_minutes"), 60
        )
        settings.import_check_interval_minutes = _int(
            form.get("import_check_interval_minutes"), 5
        )
        settings.opds_auth_enabled = form.get("opds_auth_enabled") == "on"
        settings.opds_username = str(form.get("opds_username", "")).strip()
        settings.opds_password = str(form.get("opds_password", "")).strip()
        settings.opds_page_size = _int(form.get("opds_page_size"), 50)
        self.save(settings)
        return settings


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
