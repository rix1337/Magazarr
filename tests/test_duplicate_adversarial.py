from types import SimpleNamespace

from magazarr.db import Database
from magazarr.quasarr_client import QuasarrResult
from magazarr.search import _dedupe_candidates, filter_candidates, search_magazine

SETTINGS = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
SIZE = 10 * 1024 * 1024


class EmptyDb:
    def issue_records(self, magazine_id):
        return []

    def blacklist_terms(self, magazine_id):
        return []

    def has_issue_or_download(self, magazine_id, issue_key):
        return False


def _candidates(titles, magazine_title="Synthetic Journal"):
    magazine = {"id": 1, "title": magazine_title}
    results = [
        QuasarrResult(
            title,
            f"https://example.test/release-{index}",
            "",
            SIZE,
            "synthetic",
        )
        for index, title in enumerate(titles, 1)
    ]
    return filter_candidates(EmptyDb(), SETTINGS, magazine, results)


def test_same_date_different_editions_are_not_duplicates():
    found = _candidates(
        [
            "Synthetic Journal US Edition 2026 10 02",
            "Synthetic Journal EU Edition 2026 10 02",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal US Edition 2026 10 02",
        "Synthetic Journal EU Edition 2026 10 02",
    ]


def test_explicit_number_conflicting_with_inferred_sequence_is_kept():
    found = _candidates(
        [
            "Synthetic Journal No 1 2026 01 02",
            "Synthetic Journal No 2 2026 01 09",
            "Synthetic Journal 2026 01 16",
            "Synthetic Journal No 99 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal No 1 2026 01 02",
        "Synthetic Journal No 2 2026 01 09",
        "Synthetic Journal 2026 01 16",
        "Synthetic Journal No 99 2026 01 16",
    ]


def test_sqlite_repeat_cycle_keeps_edition_keys_distinct(tmp_path, monkeypatch):
    db = Database(tmp_path / "synthetic.sqlite")
    db.migrate()
    db.add_magazine("Synthetic Journal")
    magazine = dict(db.magazines()[0])
    results = [
        QuasarrResult(
            "Synthetic Journal US Edition 2026 10 02",
            "https://example.test/us",
            "",
            SIZE,
            "synthetic",
        ),
        QuasarrResult(
            "Synthetic Journal EU Edition 2026 10 02",
            "https://example.test/eu",
            "",
            SIZE,
            "synthetic",
        ),
    ]
    sent = []

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            return results

        def add_url(self, download_url, category):
            sent.append(download_url)
            return [f"package-{len(sent)}"]

    monkeypatch.setattr("magazarr.search.QuasarrClient", FakeClient)
    monkeypatch.setattr(
        "magazarr.search.notify_download_started",
        lambda *args: None,
    )
    settings = SimpleNamespace(
        quasarr_url="http://quasarr",
        quasarr_api_key="key",
        quasarr_search_category="7000",
        quasarr_download_category="docs",
        past_days=999,
        min_size_mb=1,
        max_size_mb=0,
    )

    first = search_magazine(db, settings, magazine)
    second = search_magazine(db, settings, magazine)

    assert {item.title for item in first} == {item.title for item in results}
    assert second == []
    assert sent == [item.download_url for item in results]
    assert {row["issue_key"] for row in db.issue_records(magazine["id"])} == {
        "2026-10-02-edition-us.edition",
        "2026-10-02-edition-eu.edition",
    }


def test_sequence_inference_keeps_conflicting_explicit_number():
    found = _candidates(
        [
            "Synthetic Journal No 1 2026 01 02",
            "Synthetic Journal No 2 2026 01 09",
            "Synthetic Journal No 99 2026 01 16",
            "Synthetic Journal 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal No 1 2026 01 02",
        "Synthetic Journal No 2 2026 01 09",
        "Synthetic Journal No 99 2026 01 16",
        "Synthetic Journal 2026 01 16",
    ]


def test_sequence_inference_ignores_historical_outlier():
    found = _candidates(
        [
            "Synthetic Journal No 1 2026 01 02",
            "Synthetic Journal No 2 2026 01 09",
            "Synthetic Journal No 99 2026 02 01",
            "Synthetic Journal No 3 2026 01 16",
            "Synthetic Journal 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal No 1 2026 01 02",
        "Synthetic Journal No 2 2026 01 09",
        "Synthetic Journal No 99 2026 02 01",
        "Synthetic Journal No 3 2026 01 16",
    ]


def test_plain_date_uses_agreeing_ordinary_source_profiles():
    found = _candidates(
        [
            "Synthetic Journal News Magazine No 1 2026 01 02",
            "Synthetic Journal News Magazine No 2 2026 01 09",
            "Synthetic Journal German eBook No 1 2026 01 02",
            "Synthetic Journal German eBook No 2 2026 01 09",
            "Synthetic Journal 2026 01 16",
            "Synthetic Journal News Magazine No 3 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal News Magazine No 1 2026 01 02",
        "Synthetic Journal News Magazine No 2 2026 01 09",
        "Synthetic Journal 2026 01 16",
    ]


def test_plain_date_gets_no_alias_when_ordinary_profiles_conflict():
    found = _candidates(
        [
            "Synthetic Journal News Magazine No 1 2026 01 02",
            "Synthetic Journal News Magazine No 2 2026 01 09",
            "Synthetic Journal German eBook No 11 2026 01 02",
            "Synthetic Journal German eBook No 12 2026 01 09",
            "Synthetic Journal 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal News Magazine No 1 2026 01 02",
        "Synthetic Journal News Magazine No 2 2026 01 09",
        "Synthetic Journal German eBook No 11 2026 01 02",
        "Synthetic Journal German eBook No 12 2026 01 09",
        "Synthetic Journal 2026 01 16",
    ]


def test_scoped_profile_does_not_alias_generic_fresh_batch():
    found = _candidates(
        [
            "Synthetic Journal Special Edition No 1 2026 01 02",
            "Synthetic Journal Special Edition No 2 2026 01 09",
            "Synthetic Journal 2026 01 16",
            "Synthetic Journal No 3 2026 01 16",
            "Synthetic Journal Special Edition No 3 2026 01 16",
        ]
    )

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal Special Edition No 1 2026 01 02",
        "Synthetic Journal Special Edition No 2 2026 01 09",
        "Synthetic Journal 2026 01 16",
        "Synthetic Journal No 3 2026 01 16",
        "Synthetic Journal Special Edition No 3 2026 01 16",
    ]


def test_scoped_profile_does_not_alias_generic_with_stored_issue(tmp_path):
    db = Database(tmp_path / "scoped.sqlite")
    db.migrate()
    db.add_magazine("Synthetic Journal")
    magazine = dict(db.magazines()[0])
    stored = QuasarrResult(
        "Synthetic Journal Special Edition No 1 2026 01 02",
        "https://example.test/stored-special",
        "",
        SIZE,
        "synthetic",
    )
    db.record_manual_download(
        magazine["id"],
        "2026-01-02-edition-special.edition",
        stored.title,
        stored.download_url,
        stored.size_bytes,
        None,
    )
    results = [
        QuasarrResult(
            title,
            f"https://example.test/stored-{index}",
            "",
            SIZE,
            "synthetic",
        )
        for index, title in enumerate(
            [
                "Synthetic Journal Special Edition No 2 2026 01 09",
                "Synthetic Journal 2026 01 16",
                "Synthetic Journal No 3 2026 01 16",
                "Synthetic Journal Special Edition No 3 2026 01 16",
            ],
            1,
        )
    ]

    found = filter_candidates(db, SETTINGS, magazine, results)

    assert [item.title for item in _dedupe_candidates(found)] == [
        "Synthetic Journal Special Edition No 2 2026 01 09",
        "Synthetic Journal 2026 01 16",
        "Synthetic Journal No 3 2026 01 16",
        "Synthetic Journal Special Edition No 3 2026 01 16",
    ]
