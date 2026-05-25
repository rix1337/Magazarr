import json
from io import BytesIO
from wsgiref.util import setup_testing_defaults

from magazarr.db import Database
from magazarr.quasarr_client import QuasarrResult
from magazarr.settings import Settings, SettingsStore
from magazarr.web import (
    _delete_download_package,
    _download_match_indexes,
    _download_still_in_quasarr,
    active_download_counts,
    create_app,
    download_status_payload,
    issue_payload,
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


def test_active_download_count_skips_quasarr_without_pending_downloads(
    tmp_path,
    monkeypatch,
):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")

    def fail_fetch(settings):
        raise AssertionError("Quasarr should not be queried")

    monkeypatch.setattr(web, "fetch_quasarr_downloads", fail_fetch)

    assert active_download_counts(db, Settings()) == {}


def test_download_status_skips_quasarr_without_pending_downloads(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_issue(
        magazine["id"],
        "2026-05",
        "Magazine Title - 2026 05",
        str(tmp_path / "issue.pdf"),
        123,
        None,
    )

    def fail_fetch(settings):
        raise AssertionError("Quasarr should not be queried")

    monkeypatch.setattr(web, "fetch_quasarr_downloads", fail_fetch)

    payload = download_status_payload(db, Settings(), magazine_id=magazine["id"])

    assert payload["active"] == []
    assert payload["error"] == ""


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


def test_manual_delete_package_marks_release_skipped(tmp_path, monkeypatch):
    import magazarr.web as web

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            self.base_url = base_url
            self.api_key = api_key

        def delete_package(self, package_id, title=""):
            return True

    monkeypatch.setattr(web, "QuasarrClient", FakeQuasarrClient)
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
    download = db.downloads()[0]

    _delete_download_package(db, Settings(), download["id"])

    updated = db.downloads()[0]
    assert updated["status"] == "deleted"
    skipped = db.skipped_releases(magazine_id=magazine["id"])
    assert skipped[0]["release_title"] == "Magazine Title - 2026 05"
    assert skipped[0]["reason"] == "Deleted manually"


def test_manual_skip_package_marks_release_skipped(tmp_path, monkeypatch):
    import magazarr.web as web

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def delete_package(self, package_id, title=""):
            return True

    monkeypatch.setattr(web, "QuasarrClient", FakeQuasarrClient)
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
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)
    download = db.downloads()[0]

    status, headers, body = _wsgi_post(app, f"/downloads/{download['id']}/skip")

    assert status.startswith("302")
    updated = db.downloads()[0]
    assert updated["status"] == "deleted"
    skipped = db.skipped_releases(magazine_id=magazine["id"])
    assert skipped[0]["reason"] == "Skipped manually"


def test_dashboard_includes_search_all_button(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_get(app, "/")

    assert status.startswith("200")
    assert b'action="/api/magazines/search-all"' in body
    assert b">Search All</button>" in body


def test_search_all_api_starts_automation_job(tmp_path):
    class FakeAutomation:
        def start_search_all_job(self):
            return "job-1"

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    app = create_app(
        SettingsStore(tmp_path / "settings.json"),
        db,
        automation=FakeAutomation(),
    )

    status, headers, body = _wsgi_post(app, "/api/magazines/search-all")
    payload = json.loads(body)

    assert status.startswith("200")
    assert payload == {"job_id": "job-1"}


def test_search_all_api_runs_without_automation(tmp_path, monkeypatch):
    import magazarr.web as web

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)
    monkeypatch.setattr(web, "search_all", lambda db, settings: {"One": 1, "Two": 2})

    status, headers, body = _wsgi_post(app, "/api/magazines/search-all")
    payload = json.loads(body)

    assert status.startswith("200")
    assert payload == {"status": "done", "downloads": 3}


def test_dashboard_api_returns_magazines(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine One")
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_get(app, "/api/dashboard")
    payload = json.loads(body)

    assert status.startswith("200")
    assert b"Magazine One" in payload["magazines"].encode()


def test_skipped_view_returns_errors_before_skipped(tmp_path):
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
    db.update_download_status(db.downloads()[0]["id"], "import_error")
    db.record_skipped_release(
        magazine["id"],
        QuasarrResult(
            "Magazine Title June 2026",
            "https://example.test/magazine-title-june",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        "duplicate",
        "2026-06-01",
    )
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_get(
        app,
        f"/api/magazines/{magazine['id']}/items/skipped",
    )
    payload = json.loads(body)

    assert status.startswith("200")
    assert payload["total"] == 2
    assert [row["item_kind"] for row in payload["rows"]] == ["error", "skipped"]


def test_delete_errors_bulk_action_keeps_skipped_rows(tmp_path):
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
    db.update_download_status(db.downloads()[0]["id"], "import_error")
    db.record_skipped_release(
        magazine["id"],
        QuasarrResult(
            "Magazine Title June 2026",
            "https://example.test/magazine-title-june",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        "duplicate",
        "2026-06-01",
    )
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_post(
        app,
        f"/magazines/{magazine['id']}/errors/delete",
    )
    rows, total = _combined_skipped_payload(app, magazine["id"])

    assert status.startswith("302")
    assert db.import_error_count(magazine["id"]) == 0
    assert total == 2
    assert [row["item_kind"] for row in rows] == ["skipped", "skipped"]
    assert {row["release_title"] for row in rows} == {
        "Magazine Title - 2026 05",
        "Magazine Title June 2026",
    }


def test_delete_download_errors_preserves_skipped_release_info(tmp_path):
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
    db.update_download_status(db.downloads()[0]["id"], "download_error")
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_post(
        app,
        f"/magazines/{magazine['id']}/errors/delete",
    )
    rows, total = _combined_skipped_payload(app, magazine["id"])

    assert status.startswith("302")
    assert db.import_error_count(magazine["id"]) == 0
    assert total == 1
    assert rows[0]["release_title"] == "Magazine Title - 2026 05"
    assert rows[0]["reason"] == "Deleted download error"


def test_quasarr_public_url_prefers_external_url():
    settings = Settings(
        quasarr_url="http://127.0.0.1:8080",
        quasarr_external_url="https://quasarr.example.test/",
    )

    assert quasarr_public_url(settings) == "https://quasarr.example.test"


def test_issue_payload_includes_viewer_urls(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_issue(
        magazine["id"],
        "2026-05",
        "Magazine Title - 2026 05",
        str(tmp_path / "issue.pdf"),
        123,
        None,
    )

    payload = issue_payload(db.issues()[0])

    assert payload["cover_url"] == f"/opds?cmd=Cover&issueid={payload['id']}"
    assert payload["view_url"] == f"/issues/{payload['id']}/view"
    assert payload["file_url"] == f"/issues/{payload['id']}/file"


def test_issue_viewer_serves_html_and_inline_pdf(tmp_path):
    pdf = tmp_path / 'issue "quoted".pdf'
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_issue(
        magazine["id"],
        "2026-05",
        "Magazine Title - 2026 05",
        str(pdf),
        pdf.stat().st_size,
        None,
    )
    issue = db.issues()[0]
    app = create_app(SettingsStore(tmp_path / "settings.json"), db)

    status, headers, body = _wsgi_get(app, f"/issues/{issue['id']}/view")

    assert status.startswith("200")
    assert b'<iframe class="pdf-viewer"' in body
    assert f"/issues/{issue['id']}/file".encode() in body

    status, headers, body = _wsgi_get(app, f"/issues/{issue['id']}/file")

    assert status.startswith("200")
    assert headers["Content-Type"].startswith("application/pdf")
    assert headers["Content-Disposition"] == 'inline; filename="issue _quoted_.pdf"'
    assert body == pdf.read_bytes()


def _wsgi_get(app, path: str):
    return _wsgi_request(app, "GET", path)


def _combined_skipped_payload(app, magazine_id: int):
    status, headers, body = _wsgi_get(
        app,
        f"/api/magazines/{magazine_id}/items/skipped",
    )
    payload = json.loads(body)
    assert status.startswith("200")
    return payload["rows"], payload["total"]


def _wsgi_post(app, path: str):
    return _wsgi_request(app, "POST", path)


def _wsgi_request(app, method: str, path: str):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "wsgi.input": BytesIO(),
        }
    )
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body
