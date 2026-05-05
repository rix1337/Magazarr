from magazarr.db import Database
from magazarr.importer import _import_one, _library_destination
from magazarr.settings import Settings


def test_import_uses_quasarr_storage_without_import_root(tmp_path):
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
