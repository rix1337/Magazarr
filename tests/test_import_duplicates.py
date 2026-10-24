from pathlib import Path

import fitz

from magazarr.db import Database
from magazarr.importer import _import_one, import_completed
from magazarr.settings import Settings


def _pdf(path: Path, text: str, extra_pages: int = 0):
    with fitz.open() as doc:
        page = doc.new_page(width=120, height=160)
        page.insert_text((20, 40), text)
        for _ in range(extra_pages):
            doc.new_page(width=120, height=160)
        doc.save(path)


def _db(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    return db, db.magazines()[0]["id"]


def _settings(tmp_path):
    return Settings(library_dir=str(tmp_path / "library"))


def test_import_skips_same_cover_across_date_and_numbered_keys(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    notifications = []
    monkeypatch.setattr(
        importer,
        "notify_import_success",
        lambda *args, **kwargs: notifications.append((args, kwargs)),
    )
    db.record_manual_download(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        "https://example.test/date",
        1,
        "package-date",
    )
    first_source = tmp_path / "first"
    first_source.mkdir()
    _pdf(first_source / "magazine-title.pdf", "same cover")
    assert _import_one(db, settings, db.downloads()[0], str(first_source))
    assert len(notifications) == 1
    retained_path = Path(db.issues(limit=-1, magazine_id=magazine_id)[0]["file_path"])

    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/numbered",
        1,
        "package-numbered",
    )
    second_source = tmp_path / "second"
    second_source.mkdir()
    _pdf(second_source / "magazine-title.pdf", "same cover")
    second = db.downloads()[0]
    assert _import_one(db, settings, second, str(second_source))

    issues = db.issues(limit=-1, magazine_id=magazine_id)
    assert len(issues) == 1
    assert db.downloads()[0]["status"] == "imported"
    assert Path(db.downloads()[0]["storage"]) == retained_path
    assert len(notifications) == 1
    assert not second_source.exists()


def test_find_duplicates_uses_same_first_page_hash_policy(tmp_path):
    db, magazine_id = _db(tmp_path)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _pdf(first, "same cover")
    _pdf(second, "same cover")
    db.record_issue(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        str(first),
        10,
        "first",
    )
    db.record_issue(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        str(second),
        20,
        "second",
    )

    groups = db.find_duplicates(magazine_id)

    assert len(groups) == 1
    assert {row["file_path"] for row in groups[0]} == {str(first), str(second)}


def test_import_different_cover_creates_second_issue(tmp_path):
    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    db.record_manual_download(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        "https://example.test/first",
        1,
        "package-first",
    )
    source = tmp_path / "first"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "first cover")
    assert _import_one(db, settings, db.downloads()[0], str(source))

    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/second",
        1,
        "package-second",
    )
    source = tmp_path / "second"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "different cover")
    assert _import_one(db, settings, db.downloads()[0], str(source))

    assert len(db.issues(limit=-1, magazine_id=magazine_id)) == 2


def test_unreadable_existing_pdf_does_not_block_import(tmp_path):
    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    existing = tmp_path / "library" / "existing.pdf"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"not a PDF")
    db.record_issue(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        str(existing),
        existing.stat().st_size,
        "package-existing",
    )
    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/incoming",
        1,
        "package-incoming",
    )
    source = tmp_path / "incoming"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "new cover")

    assert _import_one(db, settings, db.downloads()[0], str(source))
    assert len(db.issues(limit=-1, magazine_id=magazine_id)) == 2


def test_duplicate_import_keeps_library_source_and_cleans_package(
    tmp_path, monkeypatch
):
    import magazarr.importer as importer

    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    db.record_manual_download(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        "https://example.test/first",
        1,
        "package-first",
    )
    source = tmp_path / "first"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "same cover")
    assert _import_one(db, settings, db.downloads()[0], str(source))
    existing = Path(db.issues(limit=-1, magazine_id=magazine_id)[0]["file_path"])

    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/second",
        1,
        "package-second",
    )
    source = tmp_path / "second"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "same cover")
    deleted = []

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": "package-second",
                    "status": "Completed",
                    "storage": str(source),
                    "name": "Magazine Title Issue 11 2026",
                }
            ]

        def delete_package(self, package_id, title=""):
            deleted.append((package_id, title))
            return True

    monkeypatch.setattr(importer, "QuasarrClient", FakeClient)
    assert import_completed(db, settings) == ["Magazine Title Issue 11 2026"]
    assert deleted == [("package-second", "Magazine Title Issue 11 2026")]
    assert db.downloads()[0]["status"] == "imported"
    assert existing.exists()
    assert not source.exists()


def test_duplicate_import_same_source_keeps_library_pdf(tmp_path):
    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    db.record_manual_download(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        "https://example.test/first",
        1,
        "package-first",
    )
    source = tmp_path / "first"
    source.mkdir()
    _pdf(source / "magazine-title.pdf", "same cover")
    assert _import_one(db, settings, db.downloads()[0], str(source))
    existing = Path(db.issues(limit=-1, magazine_id=magazine_id)[0]["file_path"])

    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/repeated",
        1,
        "package-repeated",
    )
    repeated = db.downloads()[0]

    assert _import_one(db, settings, repeated, str(existing))
    assert existing.exists()


def test_duplicate_source_dir_preserves_retained_library_files(tmp_path):
    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    library_dir = (
        tmp_path
        / "library"
        / "magazines"
        / str(magazine_id)
        / "2026"
        / "11"
        / "Magazine Title"
    )
    library_dir.mkdir(parents=True)
    retained = library_dir / "magazine-title-retained.pdf"
    other = library_dir / "magazine-title-other.pdf"
    incoming = library_dir / "magazine-title-incoming.pdf"
    _pdf(retained, "same cover")
    _pdf(other, "other cover")
    _pdf(incoming, "same cover", extra_pages=2)
    db.record_issue(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        str(retained),
        retained.stat().st_size,
        "retained",
    )
    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/incoming",
        incoming.stat().st_size,
        "incoming",
    )

    assert _import_one(db, settings, db.downloads()[0], str(library_dir))
    assert retained.exists()
    assert other.exists()
    assert not incoming.exists()


def test_duplicate_flat_source_is_removed_after_confirmation(tmp_path):
    db, magazine_id = _db(tmp_path)
    settings = _settings(tmp_path)
    retained = tmp_path / "library" / "retained.pdf"
    retained.parent.mkdir(parents=True)
    _pdf(retained, "same cover")
    db.record_issue(
        magazine_id,
        "2026-11-11",
        "Magazine Title November 2026",
        str(retained),
        retained.stat().st_size,
        "retained",
    )
    incoming = tmp_path / "magazine-title-incoming.pdf"
    _pdf(incoming, "same cover")
    db.record_manual_download(
        magazine_id,
        "2026-issue-0011",
        "Magazine Title Issue 11 2026",
        "https://example.test/incoming",
        incoming.stat().st_size,
        "incoming",
    )

    assert _import_one(db, settings, db.downloads()[0], str(incoming))
    assert not incoming.exists()
    assert retained.exists()
