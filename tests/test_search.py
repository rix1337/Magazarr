from types import SimpleNamespace

from magazarr.quasarr_client import QuasarrResult
from magazarr.search import filter_candidates, search_magazine


class FakeDb:
    def __init__(self):
        self.skipped = []
        self.blacklist = []

    def has_issue_or_download(self, magazine_id, issue_key):
        return issue_key == "2026-04-01"

    def record_skipped_release(self, magazine_id, result, reason, issue_key=""):
        self.skipped.append((magazine_id, result.title, reason, issue_key))

    def blacklist_terms(self, magazine_id):
        return self.blacklist

    def issue_records(self, magazine_id):
        return []


def test_filter_candidates_prefers_valid_recent_unseen_results():
    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Linux Format"}
    results = [
        QuasarrResult(
            "Linux Format May 2026",
            "http://example/1",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        QuasarrResult(
            "Maximum PC May 2026",
            "http://example/2",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        QuasarrResult(
            "Linux Format April 2026",
            "http://example/3",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
    ]

    db = FakeDb()

    candidates = filter_candidates(db, settings, magazine, results)

    assert [item.title for item in candidates] == ["Linux Format May 2026"]
    assert db.skipped == [
        (7, "Linux Format April 2026", "duplicate", "2026-04-01"),
    ]


def test_filter_candidates_skips_old_pub_dates_before_issue_parsing():
    settings = SimpleNamespace(past_days=45, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Linux Format"}
    db = FakeDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Linux Format 2023",
                "http://example/old",
                "Tue, 01 Aug 2023 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            )
        ],
    )

    assert candidates == []
    assert db.skipped == []


def test_filter_candidates_uses_blacklist_terms():
    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "ct"}
    db = FakeDb()
    db.blacklist = ["ct fotografie"]

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "ct Fotografie 2026",
                "http://example/blacklisted",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            )
        ],
    )

    assert candidates == []
    assert db.skipped == [(7, "ct Fotografie 2026", "blacklisted", "")]


def test_search_progress_does_not_emit_raw_found_events(monkeypatch):
    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Wrong Magazine 2026",
                    "http://example/wrong",
                    "Tue, 05 May 2026 10:00:00 +0000",
                    50 * 1024 * 1024,
                    "quasarr",
                )
            ]
            on_page(0, results)
            return results

    import magazarr.search as search

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    events = []
    db = FakeDb()

    downloads = search_magazine(
        db,
        SimpleNamespace(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            quasarr_search_category="7000",
            quasarr_download_category="docs",
            past_days=999,
            min_size_mb=1,
            max_size_mb=0,
        ),
        {"id": 7, "title": "ct"},
        events.append,
    )

    assert downloads == []
    assert "found" not in [event["event"] for event in events]
    assert any(event["event"] == "skipped" and event["reason"] == "title_mismatch" for event in events)


def test_numbered_release_is_duplicate_when_matching_dated_issue_exists():
    class ExistingDateDb(FakeDb):
        def has_issue_or_download(self, magazine_id, issue_key):
            return False

        def issue_records(self, magazine_id):
            return [
                {
                    "issue_key": "2026-04-30",
                    "release_title": "Der Spiegel Nachrichtenmagazin 2026 04 30",
                    "pub_date": "",
                }
            ]

    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Der Spiegel"}
    db = ExistingDateDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Der Spiegel No 18 2026",
                "http://example/18",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Der Spiegel No 19 2026",
                "http://example/19",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Der Spiegel No 20 2026",
                "http://example/20",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    assert [item.title for item in candidates] == [
        "Der Spiegel No 18 2026",
        "Der Spiegel No 20 2026",
    ]
    assert db.skipped == [(7, "Der Spiegel No 19 2026", "duplicate", "2026-issue-0019")]
