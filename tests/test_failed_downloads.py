import base64
from types import SimpleNamespace

from magazarr.db import Database
from magazarr.downloads import sync_download_errors
from magazarr.quasarr_client import QuasarrResult
from magazarr.search import filter_candidates
from magazarr.settings import Settings
from magazarr.utils import release_source


def _payload_url(title, source):
    fields = [title, "https://source.example/release", "0", "", "", source]
    payload = base64.urlsafe_b64encode("|".join(fields).encode()).decode()
    return f"http://quasarr.test/download/?payload={payload.rstrip('=')}"


def _db_with_download(tmp_path, title, url, package_id="pkg-1"):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"], "2026-10-09", title, url, 1234, package_id
    )
    return db, magazine


def test_release_source_reads_quasarr_payload():
    assert release_source(_payload_url("Magazine Title 2026 10", "ab")) == "ab"
    assert release_source("https://example.test/plain") == ""


def test_sync_deletes_failed_package_and_blocks_release(tmp_path, monkeypatch):
    import magazarr.downloads as downloads

    title = "Magazine Title - 2026 10 09"
    url = _payload_url(title, "ab")
    db, magazine = _db_with_download(tmp_path, title, url)
    deleted = []

    class FakeClient:
        def __init__(self, *args):
            pass

        def delete_package(self, package_id, title=""):
            deleted.append(package_id)
            return True

    monkeypatch.setattr(downloads, "QuasarrClient", FakeClient)
    monkeypatch.setattr(downloads, "notify_error", lambda *a, **k: None)
    failed = {
        "nzo_id": "pkg-1",
        "status": "Failed",
        "name": title,
        "fail_message": "File error in package",
    }
    settings = Settings(quasarr_url="http://quasarr", quasarr_api_key="key")

    sync_download_errors(db, settings, queue=[], history=[failed])
    # A package left over from earlier runs is cleaned up on the next sync too.
    sync_download_errors(db, settings, queue=[], history=[failed])

    assert db.downloads()[0]["status"] == "download_error"
    assert deleted == ["pkg-1", "pkg-1"]
    assert db.has_skipped_release(magazine["id"], title, url)


def test_same_title_same_source_is_not_downloaded_again(tmp_path):
    title = "Magazine Title - 2026 10 09"
    db, magazine = _db_with_download(tmp_path, title, _payload_url(title, "ab"))
    db.update_download_status(db.downloads()[0]["id"], "imported")
    settings = SimpleNamespace(past_days=999, min_size_mb=0, max_size_mb=0)

    def result(source, pub_date):
        return QuasarrResult(title, _payload_url(title, source), pub_date, 0, source)

    # Same title and source with a shifted pub date stays blocked.
    assert not filter_candidates(
        db, settings, magazine, [result("ab", "Fri, 16 Oct 2026 10:00:00 +0000")]
    )
    assert db.has_downloaded_release(magazine["id"], title, _payload_url(title, "ab"))
    assert not db.has_downloaded_release(
        magazine["id"], title, _payload_url(title, "cd")
    )


def test_pdf_name_accepts_partial_title_and_rejects_other_magazine():
    from pathlib import Path

    from magazarr.importer import _pdf_name_matches_download

    def matches(filename, magazine):
        return _pdf_name_matches_download(
            Path(filename), {"magazine_title": magazine, "release_title": ""}
        )

    assert matches("Der30102026.pdf", "Der Spiegel")
    assert matches("der foo10202.pdf", "Der Spiegel")
    assert matches("spiegel_44.pdf", "Der Spiegel")
    # A Spiegel file inside a GameStar package must not be imported as GameStar.
    assert not matches("Der30102026.pdf", "GameStar")
