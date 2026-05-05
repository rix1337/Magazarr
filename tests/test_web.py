from io import BytesIO
from wsgiref.util import setup_testing_defaults

from magazarr.db import Database
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
    environ = {}
    setup_testing_defaults(environ)
    environ.update(
        {
            "REQUEST_METHOD": "GET",
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
