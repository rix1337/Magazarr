from types import SimpleNamespace

import pytest

from magazarr.db import Database
from magazarr.quasarr_client import QuasarrResult
from magazarr.search import _dedupe_candidates, filter_candidates, search_magazine
from magazarr.utils import parse_issue_date

SETTINGS = SimpleNamespace(past_days=0, min_size_mb=1, max_size_mb=0)
SEARCH_SETTINGS = SimpleNamespace(
    quasarr_url="http://quasarr",
    quasarr_api_key="key",
    quasarr_search_category="7000",
    quasarr_download_category="docs",
    past_days=0,
    min_size_mb=1,
    max_size_mb=0,
)
SIZE = 10 * 1024 * 1024


@pytest.fixture
def magazine_db(tmp_path):
    db = Database(tmp_path / "magazarr.sqlite")
    db.migrate()
    db.add_magazine("Weekly Title")
    return db, dict(db.magazines()[0])


@pytest.fixture
def monthly_db(tmp_path):
    db = Database(tmp_path / "monthly.sqlite")
    db.migrate()
    db.add_magazine("Monthly Title")
    return db, dict(db.magazines()[0])


def release(title, number):
    return QuasarrResult(
        title,
        f"https://example.test/{number}",
        "",
        SIZE,
        "quasarr",
    )


def candidates(db, magazine, titles):
    return filter_candidates(
        db,
        SETTINGS,
        magazine,
        [release(title, index) for index, title in enumerate(titles, 1)],
    )


def install_search_client(monkeypatch, results, add_url_calls, notifications):
    import magazarr.search as search

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            if on_page:
                on_page(0, results)
            return results

        def add_url(self, download_url, category):
            add_url_calls.append((download_url, category))
            return [f"package-{len(add_url_calls)}"]

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    monkeypatch.setattr(
        search,
        "notify_download_started",
        lambda settings, magazine_title, title, package_id: notifications.append(
            (magazine_title, title, package_id)
        ),
    )


def test_weekly_dates_same_month_survive_stored_and_skipped_releases(magazine_db):
    db, magazine = magazine_db
    stored = release("Weekly Title 2026 10 02", "stored")
    db.record_manual_download(
        magazine["id"],
        "2026-10-02",
        stored.title,
        stored.download_url,
        stored.size_bytes,
        None,
    )
    skipped = release("Weekly Title 2026 10 09", "skipped")
    db.record_skipped_release(magazine["id"], skipped, "duplicate", "2026-10-09")

    found = candidates(
        db,
        magazine,
        [
            stored.title,
            skipped.title,
            "Weekly Title 2026 10 16",
            "Weekly Title 2026 10 23",
        ],
    )

    assert [item.title for item in found] == [
        "Weekly Title 2026 10 09",
        "Weekly Title 2026 10 16",
        "Weekly Title 2026 10 23",
    ]
    downloads = _dedupe_candidates(found)
    assert [item.title for item in downloads] == [
        "Weekly Title 2026 10 09",
        "Weekly Title 2026 10 16",
        "Weekly Title 2026 10 23",
    ]


def test_two_dated_releases_in_same_iso_week_stay_distinct(magazine_db):
    db, magazine = magazine_db
    found = candidates(
        db,
        magazine,
        ["Weekly Title 2026 10 01", "Weekly Title 2026 10 02"],
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Weekly Title 2026 10 01",
        "Weekly Title 2026 10 02",
    ]


def test_equivalent_month_only_releases_dedupe(magazine_db):
    db, magazine = magazine_db
    found = candidates(
        db,
        magazine,
        ["Weekly Title November 2026", "Weekly Title 2026 11"],
    )

    assert len(_dedupe_candidates(found)) == 1


def test_same_number_in_different_years_stays_distinct(magazine_db):
    db, magazine = magazine_db
    found = candidates(
        db,
        magazine,
        ["Weekly Title No 19 2025", "Weekly Title No 19 2026"],
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Weekly Title No 19 2025",
        "Weekly Title No 19 2026",
    ]


def test_explicit_numbers_same_month_stay_distinct(magazine_db):
    db, magazine = magazine_db
    found = candidates(
        db,
        magazine,
        [
            "Weekly Title No 10 2026 10 02",
            "Weekly Title No 11 2026 10 09",
        ],
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Weekly Title No 10 2026 10 02",
        "Weekly Title No 11 2026 10 09",
    ]


def test_numbered_only_and_date_only_release_need_sequence_evidence(magazine_db):
    db, magazine = magazine_db
    found = candidates(
        db,
        magazine,
        ["Weekly Title No 02 2026", "Weekly Title 2026 02 01"],
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Weekly Title No 02 2026",
        "Weekly Title 2026 02 01",
    ]


def test_monthly_numbered_title_parses_month_date():
    assert (
        parse_issue_date("Monthly Title Magazin November No 11 2026").key
        == "2026-11-01"
    )


def test_monthly_history_dedupes_numbered_and_full_date_releases(
    monthly_db, monkeypatch
):
    db, magazine = monthly_db
    stored = release("Monthly Title - 2026 10", "stored-october")
    db.record_manual_download(
        magazine["id"],
        "2026-10-01",
        stored.title,
        stored.download_url,
        stored.size_bytes,
        None,
    )
    results = [
        release("Monthly Title Magazin November No 11 2026", "numbered-november"),
        release("Monthly Title No 2026 11 11", "dated-november"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert [item.title for item in found] == [results[0].title]
    assert [url for url, _ in add_url_calls] == [results[0].download_url]


def test_monthly_batch_dedupes_all_november_forms_but_keeps_october(
    monthly_db, monkeypatch
):
    db, magazine = monthly_db
    results = [
        release("Monthly Title October 2026", "october"),
        release("Monthly Title November 2026", "november"),
        release("Monthly Title Magazin November No 11 2026", "numbered-november"),
        release("Monthly Title No 2026 11 11", "dated-november"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert len(found) == 2
    assert {item.title for item in found} == {
        results[0].title,
        results[1].title,
    }
    assert len(add_url_calls) == 2


def test_monthly_evidence_does_not_collapse_multiple_explicit_days(
    monthly_db, monkeypatch
):
    db, magazine = monthly_db
    results = [
        release("Monthly Title June 2026", "june"),
        release("Monthly Title July 2026", "july"),
        release("Monthly Title 2026 10 02", "october-02"),
        release("Monthly Title 2026 10 16", "october-16"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert {item.title for item in found} >= {
        "Monthly Title 2026 10 02",
        "Monthly Title 2026 10 16",
    }


def test_weekly_malformed_month_evidence_does_not_collapse_dated_releases(
    magazine_db, monkeypatch
):
    db, magazine = magazine_db
    stored = release("Weekly Title 2026 10 02", "stored-october")
    db.record_manual_download(
        magazine["id"],
        "2026-10-02",
        stored.title,
        stored.download_url,
        stored.size_bytes,
        None,
    )
    results = [
        release("Weekly Title News Magazine No 28 vom 03.2026 07", "july"),
        release("Weekly Title News Magazine No24 vom05.2026 06", "june"),
        release("Weekly Title 2026 10 09", "october-09"),
        release("Weekly Title 2026 10 16", "october-16"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert {item.title for item in found} >= {
        "Weekly Title 2026 10 09",
        "Weekly Title 2026 10 16",
    }


def test_search_repeat_cycle_sends_new_weekly_releases_once(magazine_db, monkeypatch):
    db, magazine = magazine_db
    stored = release("Weekly Title 2026 10 02", "stored")
    db.record_manual_download(
        magazine["id"],
        "2026-10-02",
        stored.title,
        stored.download_url,
        stored.size_bytes,
        None,
    )
    skipped = release("Weekly Title 2026 10 09", "skipped")
    db.record_skipped_release(magazine["id"], skipped, "duplicate", "2026-10-09")
    results = [
        stored,
        skipped,
        release("Weekly Title 2026 10 16", "oct16"),
        release("Weekly Title 2026 10 23", "oct23"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    first = search_magazine(db, SEARCH_SETTINGS, magazine)
    second = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert [item.title for item in first] == [
        "Weekly Title 2026 10 09",
        "Weekly Title 2026 10 16",
        "Weekly Title 2026 10 23",
    ]
    assert second == []
    assert [url for url, category in add_url_calls] == [
        "https://example.test/skipped",
        "https://example.test/oct16",
        "https://example.test/oct23",
    ]
    assert all(category == "docs" for _, category in add_url_calls)
    assert [title for _, title, _ in notifications] == [item.title for item in first]


def test_search_keeps_biweekly_dates_in_same_month(magazine_db, monkeypatch):
    db, magazine = magazine_db
    results = [
        release("Weekly Title 2026 10 02", "oct02"),
        release("Weekly Title 2026 10 16", "oct16"),
        release("Weekly Title 2026 10 30", "oct30"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert [item.title for item in found] == [item.title for item in results]
    assert len(add_url_calls) == 3


def test_search_dedupes_numbered_and_date_release_with_anchored_sequence(
    magazine_db, monkeypatch
):
    db, magazine = magazine_db
    for number, day in ((1, "02"), (2, "16")):
        stored = release(f"Weekly Title No {number} 2026 10 {day}", f"stored-{number}")
        db.record_manual_download(
            magazine["id"],
            f"2026-10-{day}",
            stored.title,
            stored.download_url,
            stored.size_bytes,
            None,
        )
    results = [
        release("Weekly Title No 3 2026", "numbered"),
        release("Weekly Title 2026 10 30", "dated"),
    ]
    add_url_calls = []
    notifications = []
    install_search_client(monkeypatch, results, add_url_calls, notifications)

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert len(found) == 1
    assert found[0].title in {item.title for item in results}
    assert len(add_url_calls) == 1


@pytest.mark.parametrize("with_download", [True, False])
def test_deleted_issue_release_is_blocked_from_every_source(
    magazine_db, tmp_path, monkeypatch, with_download
):
    db, magazine = magazine_db
    title = "Weekly Title No 44 2026"
    if with_download:
        db.record_manual_download(
            magazine["id"],
            "2026-issue-0044",
            title,
            "https://example.test/first-source",
            SIZE,
            "package-44",
        )
    db.record_issue(
        magazine["id"],
        "2026-issue-0044",
        title,
        str(tmp_path / "weekly-44.pdf"),
        SIZE,
        "package-44" if with_download else None,
    )
    db.delete_issue(db.issues(limit=-1, magazine_id=magazine["id"])[0]["id"])

    results = [
        QuasarrResult(title, "https://example.test/other-source", "", SIZE, "other"),
        release("Weekly Title No 45 2026", "next-45"),
    ]
    install_search_client(monkeypatch, results, [], [])

    found = search_magazine(db, SEARCH_SETTINGS, magazine)

    assert [item.title for item in found] == ["Weekly Title No 45 2026"]
    skipped = db.skipped_releases(limit=10, magazine_id=magazine["id"])
    assert [(row["release_title"], row["reason"]) for row in skipped] == [
        (title, "Deleted manually")
    ]


def test_migration_blocks_previously_deleted_issues(magazine_db):
    db, magazine = magazine_db
    for key, title in (
        ("2026-issue-0043", "Weekly Title No 43 2026"),
        ("2026-issue-0044", "Weekly Title No 44 2026"),
    ):
        db.record_manual_download(
            magazine["id"], key, title, f"https://example.test/{key}", SIZE, key
        )
    for download in db.downloads():
        db.update_download_status(download["id"], "deleted")
    skipped_download = next(
        row for row in db.downloads() if row["issue_key"] == "2026-issue-0044"
    )
    db.record_skipped_download(skipped_download, "Skipped manually")

    db.migrate()
    db.migrate()

    skipped = db.skipped_releases(limit=10, magazine_id=magazine["id"])
    assert sorted((row["release_title"], row["reason"]) for row in skipped) == [
        ("Weekly Title No 43 2026", "Deleted manually"),
        ("Weekly Title No 44 2026", "Skipped manually"),
    ]


def test_unskipping_deleted_issue_without_source_only_unblocks(magazine_db, tmp_path):
    from magazarr.web import _unskip_release

    db, magazine = magazine_db
    db.record_issue(
        magazine["id"],
        "2026-issue-0044",
        "Weekly Title No 44 2026",
        str(tmp_path / "weekly-44.pdf"),
        SIZE,
        None,
    )
    db.delete_issue(db.issues(limit=-1, magazine_id=magazine["id"])[0]["id"])
    skip_id = db.skipped_releases(limit=10, magazine_id=magazine["id"])[0]["id"]

    assert _unskip_release(db, SEARCH_SETTINGS, skip_id) is None
    assert db.skipped_releases(limit=10, magazine_id=magazine["id"]) == []
