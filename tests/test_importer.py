from magazarr.db import Database
from magazarr.importer import _import_one, _library_destination, import_completed
from magazarr.settings import Settings


def test_import_uses_absolute_quasarr_storage(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "RandomTitle"
    source_dir.mkdir(parents=True)
    (source_dir / "issue.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Magazine Title",
        "issue_key": "2026-05-05",
        "release_title": "Magazine Title 2026 05",
        "package_id": "pkg",
        "download_url": "https://example.test/download",
        "size_bytes": 1234,
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert not source_dir.exists()
    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "05"
        / "Magazine Title"
        / "2026-05-05 - issue.pdf"
    ).exists()


def test_completed_history_imports_quasarr_storage(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    source_dir = tmp_path / "Quasarr" / "Magazine Title - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "magazine-title.pdf").write_bytes(b"%PDF-1.4")
    deleted_packages = []

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": " completed ",
                    "storage": str(source_dir),
                    "name": "Magazine Title - 2026 05",
                },
            ]

        def delete_package(self, package_id, title=""):
            deleted_packages.append((package_id, title))
            return True

    monkeypatch.setattr(importer, "QuasarrClient", FakeQuasarrClient)

    imported = import_completed(
        db,
        Settings(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            library_dir=str(tmp_path / "library"),
        ),
    )

    assert imported == ["Magazine Title - 2026 05"]
    assert deleted_packages == [(package_id, "Magazine Title - 2026 05")]
    assert db.downloads()[0]["status"] == "imported"
    assert (
        tmp_path
        / "library"
        / "magazines"
        / str(magazine["id"])
        / "2026"
        / "05"
        / "Magazine Title"
        / "2026-05-01 - magazine-title.pdf"
    ).exists()


def test_completed_history_imports_when_quasarr_returns_uuid_nzo_id(
    tmp_path, monkeypatch
):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/download",
        1234,
        "Quasarr_docs_123",
    )
    source_dir = tmp_path / "Quasarr" / "Magazine Title - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "magazine-title.pdf").write_bytes(b"%PDF-1.4")
    deleted_packages = []

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": "jd-package-uuid",
                    "status": "Completed",
                    "storage": str(source_dir),
                    "name": "Magazine Title - 2026 05",
                },
            ]

        def delete_package(self, package_id, title=""):
            deleted_packages.append((package_id, title))
            return package_id == "Quasarr_docs_123"

    monkeypatch.setattr(importer, "QuasarrClient", FakeQuasarrClient)

    imported = import_completed(
        db,
        Settings(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            library_dir=str(tmp_path / "library"),
        ),
    )

    assert imported == ["Magazine Title - 2026 05"]
    assert deleted_packages == [
        ("jd-package-uuid", "Magazine Title - 2026 05"),
        ("Quasarr_docs_123", "Magazine Title - 2026 05"),
    ]
    assert db.downloads()[0]["status"] == "imported"


def test_history_waits_for_quasarr_completed_status(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    source_dir = tmp_path / "Quasarr" / "Magazine Title - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "magazine-title.pdf").write_bytes(b"%PDF-1.4")

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": "Downloading",
                    "storage": str(source_dir),
                    "name": "Magazine Title - 2026 05",
                },
            ]

    monkeypatch.setattr(importer, "QuasarrClient", FakeQuasarrClient)

    imported = import_completed(
        db,
        Settings(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            library_dir=str(tmp_path / "library"),
        ),
    )

    assert imported == []
    assert db.downloads()[0]["status"] == "snatched"
    assert (source_dir / "magazine-title.pdf").exists()


def test_failed_history_deletes_quasarr_package(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    deleted_packages = []

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": "Failed",
                    "storage": "",
                    "name": "Magazine Title - 2026 05",
                    "fail_message": "Download failed",
                },
            ]

        def delete_package(self, package_id, title=""):
            deleted_packages.append((package_id, title))
            return True

    monkeypatch.setattr(importer, "QuasarrClient", FakeQuasarrClient)

    imported = import_completed(
        db,
        Settings(quasarr_url="http://quasarr", quasarr_api_key="key"),
    )

    assert imported == []
    assert db.downloads()[0]["status"] == "download_error"
    assert deleted_packages == [(package_id, "Magazine Title - 2026 05")]


def test_failed_import_deletes_quasarr_package(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "Magazine Title - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    source_dir = tmp_path / "Quasarr" / "Magazine Title - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "Different Publication May 2026.pdf").write_bytes(b"%PDF-1.4")
    deleted_packages = []

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": "Completed",
                    "storage": str(source_dir),
                    "name": "Magazine Title - 2026 05",
                },
            ]

        def delete_package(self, package_id, title=""):
            deleted_packages.append((package_id, title))
            return True

    monkeypatch.setattr(importer, "QuasarrClient", FakeQuasarrClient)

    imported = import_completed(
        db,
        Settings(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            library_dir=str(tmp_path / "library"),
        ),
    )

    assert imported == []
    assert db.downloads()[0]["status"] == "import_error"
    assert deleted_packages == [(package_id, "Magazine Title - 2026 05")]


def test_library_destination_is_nested_and_filesystem_safe(tmp_path):
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "magazine_id": 42,
        "magazine_title": "Magazine Title: Wissen/Plus?",
        "issue_key": "2026-04-02",
    }

    dest = _library_destination(settings, download, tmp_path / "source copy.PDF")

    assert dest == (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "04"
        / "Magazine Title Wissen Plus"
        / "2026-04-02 - source copy.pdf"
    )


def test_library_destination_uses_release_date_for_numbered_issue_key(tmp_path):
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "magazine_id": 3,
        "magazine_title": "Fictional Games Monthly",
        "issue_key": "2026-issue-0421",
        "release_title": "Fictional Games Monthly UK - Issue 421, 2026 05",
    }

    dest = _library_destination(
        settings,
        download,
        tmp_path / "Fictional.Games.Monthly.UK..Issue.421.May.2026.PDF",
    )

    assert dest == (
        tmp_path
        / "library"
        / "magazines"
        / "3"
        / "2026"
        / "05"
        / "Fictional Games Monthly"
        / "2026-issue-0421 - Fictional.Games.Monthly.UK..Issue.421.May.2026.pdf"
    )


def test_blank_quasarr_storage_does_not_import_from_cwd(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Magazine Title",
        "issue_key": "2026-05-05",
        "release_title": "Magazine Title 2026 05",
        "package_id": "pkg",
        "download_url": "https://example.test/download",
        "size_bytes": 1234,
    }

    assert not _import_one(db, settings, download, "")

    events = db.events()
    assert events[0]["message"] == "Storage path missing from Quasarr history"


def test_import_rejects_pdf_name_for_different_magazine(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "Magazine Title - 2026 05"
    source_dir.mkdir(parents=True)
    pdf = source_dir / "Different Publication May 2026.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Magazine Title",
        "issue_key": "2026-05-05",
        "release_title": "Magazine Title 2026 05",
        "package_id": "pkg",
        "download_url": "https://example.invalid/download",
        "size_bytes": 1234,
    }

    assert not _import_one(db, settings, download, str(source_dir))

    assert pdf.exists()
    assert not (tmp_path / "library").exists()
    events = db.events()
    assert events[0]["message"] == (
        "PDF filename does not match magazine: Different Publication May 2026.pdf"
    )


def test_import_allows_compact_typo_pdf_name_for_same_magazine(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "Weekly Gazette - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "Gazete202620.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Weekly Gazette",
        "issue_key": "2026-05-08",
        "release_title": "Weekly Gazette No 20 2026 05 08",
        "package_id": "pkg",
        "download_url": "https://example.invalid/download",
        "size_bytes": 1234,
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "05"
        / "Weekly Gazette"
        / "2026-05-08 - Gazete202620.pdf"
    ).exists()


def test_import_allows_title_acronym_pdf_name(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "PlayZone - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "PZ526.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "PlayZone",
        "issue_key": "2026-05-08",
        "release_title": "PlayZone No 5 2026 05 08",
        "package_id": "pkg",
        "download_url": "https://example.invalid/download",
        "size_bytes": 1234,
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "05"
        / "PlayZone"
        / "2026-05-08 - PZ526.pdf"
    ).exists()


def test_import_allows_short_lowercase_title_embedded_in_pdf_name(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "qx Specials - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "prefix-qxSpecialsNr.052026.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "qx",
        "issue_key": "2026-05-08",
        "release_title": "qx Specials - Nr.05 2026",
        "package_id": "pkg",
        "download_url": "https://example.invalid/download",
        "size_bytes": 1234,
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "05"
        / "qx"
        / "2026-05-08 - prefix-qxSpecialsNr.052026.pdf"
    ).exists()


def test_import_allows_camelcase_compact_pdf_name(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    source_dir = tmp_path / "Quasarr" / "GameDesk - 2026 07"
    source_dir.mkdir(parents=True)
    (source_dir / "GaD7.26.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "GameDesk",
        "issue_key": "2026-07-01",
        "release_title": "GameDesk - 2026 07",
        "package_id": "pkg",
        "download_url": "https://example.invalid/download",
        "size_bytes": 1234,
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "07"
        / "GameDesk"
        / "2026-07-01 - GaD7.26.pdf"
    ).exists()


def test_import_flat_pdf_does_not_delete_import_root(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    import_root = tmp_path / "Quasarr"
    import_root.mkdir()
    source = import_root / "test.pdf"
    source.write_bytes(b"%PDF-1.4")
    settings = Settings(
        import_root=str(import_root),
        library_dir=str(tmp_path / "library"),
    )
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Magazine Title",
        "issue_key": "2026-05-05",
        "release_title": "Magazine Title 2026 05",
        "package_id": "pkg",
    }

    assert _import_one(db, settings, download, str(source))

    assert import_root.exists()
    assert not source.exists()
    assert (
        tmp_path
        / "library"
        / "magazines"
        / "42"
        / "2026"
        / "05"
        / "Magazine Title"
        / "2026-05-05 - test.pdf"
    ).exists()


def test_import_subfolder_deletes_only_download_subfolder(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    import_root = tmp_path / "Quasarr"
    source_dir = import_root / "RandomTitle"
    source_dir.mkdir(parents=True)
    (source_dir / "issue.pdf").write_bytes(b"%PDF-1.4")
    settings = Settings(
        import_root=str(import_root),
        library_dir=str(tmp_path / "library"),
    )
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "Magazine Title",
        "issue_key": "2026-05-05",
        "release_title": "Magazine Title 2026 05",
        "package_id": "pkg",
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert import_root.exists()
    assert not source_dir.exists()
