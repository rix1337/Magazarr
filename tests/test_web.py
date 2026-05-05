from magazarr.db import Database
from magazarr.settings import Settings
from magazarr.web import (
    _download_match_indexes,
    _download_still_in_quasarr,
    active_download_counts,
    download_status_payload,
    quasarr_public_url,
)


def test_missing_quasarr_download_becomes_error_and_skipped(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-04-02",
        "Magazine Title - 2026 04 02",
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


def test_download_status_includes_quasarr_queue_and_history(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/magazine-title",
        1234,
        "Quasarr_docs_123",
    )
    db.record_manual_download(
        magazine["id"],
        "2026-06-01",
        "Magazine Title Two - 2026 06",
        "https://example.test/magazine-title-two",
        4321,
        "Quasarr_docs_456",
    )
    monkeypatch.setattr(
        web,
        "fetch_quasarr_downloads",
        lambda settings: (
            [
                {
                    "nzo_id": "Quasarr_docs_456",
                    "filename": "[Downloading] Magazine Title Two - 2026 06",
                    "status": "Downloading",
                    "percentage": 42,
                }
            ],
            [
                {
                    "nzo_id": "Quasarr_docs_123",
                    "name": "Magazine Title - 2026 05",
                    "status": "Completed",
                    "percentage": 100,
                }
            ],
        ),
    )

    payload = download_status_payload(db, Settings(), magazine_id=magazine["id"])

    assert [item["title"] for item in payload["active"]] == [
        "Magazine Title Two - 2026 06",
        "Magazine Title - 2026 05",
    ]
    assert [item["status"] for item in payload["active"]] == [
        "Downloading",
        "Completed",
    ]


def test_download_status_matches_history_uuid_by_title(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/magazine-title",
        1234,
        "Quasarr_docs_123",
    )
    monkeypatch.setattr(
        web,
        "fetch_quasarr_downloads",
        lambda settings: (
            [],
            [
                {
                    "nzo_id": "jd-package-uuid",
                    "name": "Magazine Title - 2026 05",
                    "status": "Completed",
                    "percentage": 100,
                }
            ],
        ),
    )

    payload = download_status_payload(db, Settings(), magazine_id=magazine["id"])

    assert payload["active"][0]["title"] == "Magazine Title - 2026 05"
    assert payload["active"][0]["package_id"] == "jd-package-uuid"


def test_active_download_count_uses_queue_and_history(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/magazine-title",
        1234,
        "Quasarr_docs_123",
    )
    monkeypatch.setattr(
        web,
        "fetch_quasarr_downloads",
        lambda settings: (
            [],
            [
                {
                    "nzo_id": "jd-package-uuid",
                    "name": "Magazine Title - 2026 05",
                    "status": "Completed",
                    "percentage": 100,
                }
            ],
        ),
    )

    assert active_download_counts(db, Settings()) == {magazine["id"]: 1}


def test_delete_failure_is_ok_when_package_is_gone(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/magazine-title",
        1234,
        "Quasarr_docs_123",
    )
    by_package, by_title = _download_match_indexes([db.downloads()[0]])

    assert not _download_still_in_quasarr([], [], by_package, by_title)


def test_delete_failure_errors_when_package_remains_in_quasarr(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/magazine-title",
        1234,
        "Quasarr_docs_123",
    )
    by_package, by_title = _download_match_indexes([db.downloads()[0]])

    assert _download_still_in_quasarr(
        [{"nzo_id": "Quasarr_docs_123", "filename": "Magazine Title - 2026 05"}],
        [],
        by_package,
        by_title,
    )


def test_quasarr_public_url_prefers_external_url():
    settings = Settings(
        quasarr_url="http://127.0.0.1:8080",
        quasarr_external_url="https://quasarr.example.test/",
    )

    assert quasarr_public_url(settings) == "https://quasarr.example.test"
