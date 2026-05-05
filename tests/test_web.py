from magazarr.db import Database
from magazarr.settings import Settings
from magazarr.web import download_status_payload, quasarr_public_url


def test_missing_quasarr_download_becomes_error_and_skipped(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Der Spiegel")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-04-02",
        "Der Spiegel - 2026 04 02",
        "https://example.invalid/download",
        42,
        "missing-package",
    )
    monkeypatch.setattr(web, "fetch_quasarr_downloads", lambda settings: ([], []))

    payload = download_status_payload(db, Settings(), magazine_id=magazine["id"])

    assert payload["error"] == ""
    assert payload["active"] == []
    errors = db.import_errors(magazine_id=magazine["id"])
    assert errors[0]["status"] == "download_error"
    skipped = db.skipped_releases(magazine_id=magazine["id"])
    assert skipped[0]["reason"] == "Download disappeared from Quasarr before import completed"


def test_quasarr_public_url_prefers_external_url():
    settings = Settings(
        quasarr_url="http://127.0.0.1:8080",
        quasarr_external_url="https://quasarr.example.test/",
    )

    assert quasarr_public_url(settings) == "https://quasarr.example.test"
