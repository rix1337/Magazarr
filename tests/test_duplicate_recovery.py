import pytest

from magazarr.db import Database
from magazarr.quasarr_client import QuasarrResult


def _result(title="Magazine Title January 2026", url="https://example.test/magazine"):
    return QuasarrResult(title, url, "", 1234, "test")


def _database(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    return db, db.magazines()[0]["id"]


def test_duplicate_skip_does_not_block_recovery(tmp_path):
    db, magazine_id = _database(tmp_path)
    result = _result()

    db.record_skipped_release(magazine_id, result, "duplicate")

    assert not db.has_skipped_release(magazine_id, result.title, result.download_url)


@pytest.mark.parametrize("status", ["import_error", "download_error"])
def test_manual_skip_and_download_errors_still_block(tmp_path, status):
    db, magazine_id = _database(tmp_path)
    result = _result()

    db.record_skipped_release(magazine_id, result, "Skipped manually")
    assert db.has_skipped_release(magazine_id, result.title, result.download_url)

    db.clear_skipped_releases(magazine_id)
    db.record_manual_download(
        magazine_id,
        "2026-01-01",
        result.title,
        result.download_url,
        result.size_bytes,
        "package-id",
    )
    download = db.downloads(magazine_id=magazine_id)[0]
    db.update_download_status(download["id"], status)

    assert db.has_skipped_release(magazine_id, result.title, result.download_url)


def test_duplicate_skip_cannot_bypass_manual_skip_on_same_url(tmp_path):
    db, magazine_id = _database(tmp_path)
    duplicate = _result("Magazine Title January 2026", "https://example.test/shared")
    manual = _result(
        "Magazine Title January 2026 Alternate", "https://example.test/shared"
    )

    db.record_skipped_release(magazine_id, duplicate, "duplicate")
    db.record_skipped_release(magazine_id, manual, "Skipped manually")

    assert db.has_skipped_release(magazine_id, duplicate.title, duplicate.download_url)


def test_duplicate_skip_stays_blocked_for_deleted_same_issue(tmp_path):
    db, magazine_id = _database(tmp_path)
    db.record_manual_download(
        magazine_id,
        "2026-09-01",
        "Magazine Title September 2026",
        "https://example.test/original",
        1234,
        "package-id",
    )
    download = db.downloads(magazine_id=magazine_id)[0]
    db.update_download_status(download["id"], "deleted")
    duplicate = _result(
        "Magazine Title September 2026 Alternate",
        "https://example.test/alternate",
    )
    db.record_skipped_release(
        magazine_id, duplicate, "duplicate", issue_key="2026-09-01"
    )

    assert db.has_skipped_release(magazine_id, duplicate.title, duplicate.download_url)

    next_issue = _result(
        "Magazine Title October 2026 Alternate",
        "https://example.test/next",
    )
    db.record_skipped_release(
        magazine_id, next_issue, "duplicate", issue_key="2026-09-02"
    )
    assert not db.has_skipped_release(
        magazine_id, next_issue.title, next_issue.download_url
    )
