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
        "magazine_title": "ct",
        "issue_key": "2026-05-05",
        "release_title": "ct 2026 05",
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
        / "ct"
        / "2026-05-05 - issue.pdf"
    ).exists()


def test_completed_history_imports_quasarr_storage(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("GameStar")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "GameStar - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    source_dir = tmp_path / "Quasarr" / "GameStar - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "gamestar.pdf").write_bytes(b"%PDF-1.4")

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": " completed ",
                    "storage": str(source_dir),
                    "name": "GameStar - 2026 05",
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

    assert imported == ["GameStar - 2026 05"]
    assert db.downloads()[0]["status"] == "imported"
    assert (
        tmp_path
        / "library"
        / "magazines"
        / str(magazine["id"])
        / "2026"
        / "05"
        / "GameStar"
        / "2026-05-01 - gamestar.pdf"
    ).exists()


def test_history_waits_for_quasarr_completed_status(tmp_path, monkeypatch):
    import magazarr.importer as importer

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("GameStar")
    magazine = db.magazines()[0]
    package_id = "Quasarr_docs_123"
    db.record_manual_download(
        magazine["id"],
        "2026-05-01",
        "GameStar - 2026 05",
        "https://example.test/download",
        1234,
        package_id,
    )
    source_dir = tmp_path / "Quasarr" / "GameStar - 2026 05"
    source_dir.mkdir(parents=True)
    (source_dir / "gamestar.pdf").write_bytes(b"%PDF-1.4")

    class FakeQuasarrClient:
        def __init__(self, base_url, api_key):
            pass

        def history(self):
            return [
                {
                    "nzo_id": package_id,
                    "status": "Downloading",
                    "storage": str(source_dir),
                    "name": "GameStar - 2026 05",
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
    assert (source_dir / "gamestar.pdf").exists()


def test_library_destination_is_nested_and_filesystem_safe(tmp_path):
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "magazine_id": 42,
        "magazine_title": 'Der Spiegel: Wissen/Plus?',
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
        / "Der Spiegel Wissen Plus"
        / "2026-04-02 - source copy.pdf"
    )


def test_blank_quasarr_storage_does_not_import_from_cwd(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    settings = Settings(library_dir=str(tmp_path / "library"))
    download = {
        "id": 1,
        "magazine_id": 42,
        "magazine_title": "ct",
        "issue_key": "2026-05-05",
        "release_title": "ct 2026 05",
        "package_id": "pkg",
        "download_url": "https://example.test/download",
        "size_bytes": 1234,
    }

    assert not _import_one(db, settings, download, "")

    events = db.events()
    assert events[0]["message"] == "Storage path missing from Quasarr history"


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
        "magazine_title": "ct",
        "issue_key": "2026-05-05",
        "release_title": "ct 2026 05",
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
        / "ct"
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
        "magazine_title": "ct",
        "issue_key": "2026-05-05",
        "release_title": "ct 2026 05",
        "package_id": "pkg",
    }

    assert _import_one(db, settings, download, str(source_dir))

    assert import_root.exists()
    assert not source_dir.exists()
