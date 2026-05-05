from magazarr.settings import Settings, SettingsStore


def test_default_automation_interval_is_60_minutes():
    assert Settings().automation_interval_minutes == 60
    assert Settings().import_check_interval_minutes == 5


def test_settings_form_updates_automation_interval(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")

    settings = store.update_from_form(
        {
            "quasarr_url": "http://127.0.0.1:8080",
            "quasarr_external_url": "https://quasarr.example.test/",
            "quasarr_api_key": "key",
            "discord_webhook_url": "https://discord.example.test/webhook",
            "quasarr_search_category": "7000",
            "quasarr_download_category": "docs",
            "past_days": "45",
            "min_size_mb": "1",
            "max_size_mb": "0",
            "library_dir": "library",
            "import_root": "/downloads",
            "automation_interval_minutes": "15",
            "import_check_interval_minutes": "4",
            "opds_page_size": "50",
        }
    )

    assert settings.automation_interval_minutes == 15
    assert settings.import_check_interval_minutes == 4
    assert settings.quasarr_external_url == "https://quasarr.example.test"
    assert settings.discord_webhook_url == "https://discord.example.test/webhook"
    assert store.load().automation_interval_minutes == 15
    assert store.load().import_check_interval_minutes == 4
