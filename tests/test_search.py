from types import SimpleNamespace

from magazarr.db import Database
from magazarr.quasarr_client import QuasarrResult
from magazarr.search import _dedupe_candidates, filter_candidates, search_magazine


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
    magazine = {"id": 7, "title": "Magazine Title Three"}
    results = [
        QuasarrResult(
            "Magazine Title Three May 2026",
            "https://example.test/1",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        QuasarrResult(
            "Example Monthlyum PC May 2026",
            "https://example.test/2",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
        QuasarrResult(
            "Magazine Title Three April 2026",
            "https://example.test/3",
            "Tue, 05 May 2026 10:00:00 +0000",
            50 * 1024 * 1024,
            "quasarr",
        ),
    ]

    db = FakeDb()

    candidates = filter_candidates(db, settings, magazine, results)

    assert [item.title for item in candidates] == ["Magazine Title Three May 2026"]
    assert db.skipped == [
        (7, "Magazine Title Three April 2026", "duplicate", "2026-04-01"),
    ]


def test_filter_candidates_skips_old_pub_dates_before_issue_parsing():
    settings = SimpleNamespace(past_days=45, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Magazine Title Three"}
    db = FakeDb()
    events = []

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Magazine Title Three 2023",
                "https://example.test/old",
                "Tue, 01 Aug 2023 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            )
        ],
        events.append,
    )

    assert candidates == []
    assert db.skipped == []
    assert events == []


def test_filter_candidates_skips_old_issue_dates_without_ui_noise():
    settings = SimpleNamespace(past_days=45, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Magazine Title Three"}
    db = FakeDb()
    events = []

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Magazine Title Three January 2023",
                "https://example.test/old",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            )
        ],
        events.append,
    )

    assert candidates == []
    assert db.skipped == [
        (7, "Magazine Title Three January 2023", "outside_past_days", "2023-01-01"),
    ]
    assert events == []


def test_filter_candidates_uses_blacklist_terms():
    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Magazine Title"}
    db = FakeDb()
    db.blacklist = ["Magazine Title Photo"]

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Magazine Title Photo 2026",
                "https://example.test/blacklisted",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            )
        ],
    )

    assert candidates == []
    assert db.skipped == [(7, "Magazine Title Photo 2026", "blacklisted", "")]


def test_search_progress_does_not_emit_raw_found_events(monkeypatch):
    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Wrong Magazine 2026",
                    "https://example.test/wrong",
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
        {"id": 7, "title": "Magazine Title"},
        events.append,
    )

    assert downloads == []
    assert "found" not in [event["event"] for event in events]
    assert any(
        event["event"] == "skipped" and event["reason"] == "title_mismatch"
        for event in events
    )


def test_search_does_not_add_url_for_active_duplicate_release(monkeypatch):
    add_url_calls = []

    class DuplicateDb(FakeDb):
        def has_active_release_download(
            self,
            magazine_id,
            issue_key,
            release_title,
            download_url,
        ):
            return True

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Magazine Title May 2026",
                    "https://example.test/magazine-title-may",
                    "Tue, 05 May 2026 10:00:00 +0000",
                    50 * 1024 * 1024,
                    "quasarr",
                )
            ]
            on_page(0, results)
            return results

        def add_url(self, download_url, category):
            add_url_calls.append((download_url, category))
            return ["pkg"]

    import magazarr.search as search

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    events = []

    downloads = search_magazine(
        DuplicateDb(),
        SimpleNamespace(
            quasarr_url="http://quasarr",
            quasarr_api_key="key",
            quasarr_search_category="7000",
            quasarr_download_category="docs",
            past_days=999,
            min_size_mb=1,
            max_size_mb=0,
        ),
        {"id": 7, "title": "Magazine Title"},
        events.append,
    )

    assert downloads == []
    assert add_url_calls == []
    assert any(
        event["event"] == "skipped" and event["reason"] == "duplicate"
        for event in events
    )


def test_search_retries_issue_after_import_error_with_new_release(
    tmp_path, monkeypatch
):
    add_url_calls = []

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Magazine Title June 2026",
                    "https://example.test/magazine-title-june",
                    "Tue, 02 Jun 2026 10:00:00 +0000",
                    50 * 1024 * 1024,
                    "quasarr",
                )
            ]
            on_page(0, results)
            return results

        def add_url(self, download_url, category):
            add_url_calls.append((download_url, category))
            return ["Quasarr_docs_456"]

    import magazarr.search as search

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-06-01",
        "Magazine Title - June 2026",
        "https://example.test/magazine-title-june-broken",
        1234,
        "Quasarr_docs_123",
    )
    db.update_download_status(db.downloads()[0]["id"], "import_error")

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
            discord_webhook_url="",
        ),
        magazine,
    )

    rows = db.downloads(magazine_id=magazine["id"])
    assert [item.title for item in downloads] == ["Magazine Title June 2026"]
    assert add_url_calls == [("https://example.test/magazine-title-june", "docs")]
    assert [(row["issue_key"], row["status"]) for row in rows] == [
        ("2026-06-01-retry-2", "snatched"),
        ("2026-06-01", "import_error"),
    ]


def test_search_does_not_retry_skipped_import_error_release(tmp_path, monkeypatch):
    add_url_calls = []

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Magazine Title June 2026",
                    "https://example.test/magazine-title-june-bad",
                    "Tue, 02 Jun 2026 10:00:00 +0000",
                    50 * 1024 * 1024,
                    "quasarr",
                ),
                QuasarrResult(
                    "Magazine Title June 2026 Alternate",
                    "https://example.test/magazine-title-june-alt",
                    "Tue, 02 Jun 2026 10:00:00 +0000",
                    49 * 1024 * 1024,
                    "quasarr",
                ),
            ]
            on_page(0, results)
            return results

        def add_url(self, download_url, category):
            add_url_calls.append((download_url, category))
            return ["Quasarr_docs_456"]

    import magazarr.search as search

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-06-01",
        "Magazine Title June 2026",
        "https://example.test/magazine-title-june-bad",
        1234,
        "Quasarr_docs_123",
    )
    bad_download = db.downloads()[0]
    db.update_download_status(bad_download["id"], "import_error")
    db.record_skipped_download(bad_download, "PDF filename does not match magazine")

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
            discord_webhook_url="",
        ),
        magazine,
    )

    assert [item.title for item in downloads] == ["Magazine Title June 2026 Alternate"]
    assert add_url_calls == [("https://example.test/magazine-title-june-alt", "docs")]


def test_search_does_not_retry_download_error_release(tmp_path, monkeypatch):
    add_url_calls = []

    class FakeClient:
        def __init__(self, base_url, api_key):
            pass

        def search(self, query, category, on_page=None):
            results = [
                QuasarrResult(
                    "Magazine Title July 2026",
                    "https://example.test/magazine-title-july-bad",
                    "Tue, 07 Jul 2026 10:00:00 +0000",
                    50 * 1024 * 1024,
                    "quasarr",
                ),
                QuasarrResult(
                    "Magazine Title July 2026 Alternate",
                    "https://example.test/magazine-title-july-alt",
                    "Tue, 07 Jul 2026 10:00:00 +0000",
                    49 * 1024 * 1024,
                    "quasarr",
                ),
            ]
            on_page(0, results)
            return results

        def add_url(self, download_url, category):
            add_url_calls.append((download_url, category))
            return ["Quasarr_docs_456"]

    import magazarr.search as search

    monkeypatch.setattr(search, "QuasarrClient", FakeClient)
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    db.record_manual_download(
        magazine["id"],
        "2026-07-01",
        "Magazine Title July 2026",
        "https://example.test/magazine-title-july-bad",
        1234,
        "Quasarr_docs_123",
    )
    db.update_download_status(db.downloads()[0]["id"], "download_error")

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
            discord_webhook_url="",
        ),
        magazine,
    )

    assert [item.title for item in downloads] == ["Magazine Title July 2026 Alternate"]
    assert add_url_calls == [("https://example.test/magazine-title-july-alt", "docs")]


def test_numbered_release_is_duplicate_when_existing_title_has_same_number():
    class ExistingDateDb(FakeDb):
        def has_issue_or_download(self, magazine_id, issue_key):
            return False

        def issue_records(self, magazine_id):
            return [
                {
                    "issue_key": "2026-04-30",
                    "release_title": "Magazine Title News Magazine No 19 2026 04 30",
                    "pub_date": "",
                }
            ]

    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Magazine Title"}
    db = ExistingDateDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Magazine Title No 18 2026",
                "https://example.test/18",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Magazine Title No 19 2026",
                "https://example.test/19",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Magazine Title No 20 2026",
                "https://example.test/20",
                "Tue, 05 May 2026 10:00:00 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    assert [item.title for item in candidates] == [
        "Magazine Title No 18 2026",
        "Magazine Title No 20 2026",
    ]
    assert db.skipped == [
        (7, "Magazine Title No 19 2026", "duplicate", "2026-issue-0019")
    ]


def test_dedupe_candidates_keeps_distinct_issues_with_same_pub_date():
    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Weekly Title"}
    db = FakeDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Weekly Title No 10 2026 05 02",
                "https://example.test/10",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Weekly Title No 08 2026",
                "https://example.test/08",
                "Sun, 24 May 2026 21:51:22 +0000",
                49 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    downloads = _dedupe_candidates(candidates)

    assert [item.title for item in downloads] == [
        "Weekly Title No 10 2026 05 02",
        "Weekly Title No 08 2026",
    ]


def test_date_only_release_duplicates_numbered_issue_when_sequence_is_anchored():
    class ExistingNumberDb(FakeDb):
        def has_issue_or_download(self, magazine_id, issue_key):
            return False

        def issue_records(self, magazine_id):
            return [
                {
                    "issue_key": "2026-issue-0018",
                    "release_title": "Weekly Title News Magazine No 18 2026",
                    "pub_date": "",
                }
            ]

    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Weekly Title"}
    db = ExistingNumberDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Weekly Title News Magazine No 17 2026 04 17",
                "https://example.test/17",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Weekly Title News Magazine No 19 2026 05 01",
                "https://example.test/19",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Weekly Title - 2026 04 24",
                "https://example.test/date-only",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    assert [item.title for item in candidates] == [
        "Weekly Title News Magazine No 17 2026 04 17",
        "Weekly Title News Magazine No 19 2026 05 01",
    ]
    assert db.skipped == [
        (7, "Weekly Title - 2026 04 24", "duplicate", "2026-04-24"),
    ]


def test_single_sequence_anchor_does_not_infer_duplicate():
    class ExistingNumberDb(FakeDb):
        def has_issue_or_download(self, magazine_id, issue_key):
            return False

        def issue_records(self, magazine_id):
            return [
                {
                    "issue_key": "2026-issue-0018",
                    "release_title": "Weekly Title News Magazine No 18 2026",
                    "pub_date": "",
                }
            ]

    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Weekly Title"}
    db = ExistingNumberDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Weekly Title News Magazine No 19 2026 05 01",
                "https://example.test/19",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Weekly Title - 2026 04 24",
                "https://example.test/date-only",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    assert [item.title for item in candidates] == [
        "Weekly Title News Magazine No 19 2026 05 01",
        "Weekly Title - 2026 04 24",
    ]
    assert db.skipped == []


def test_sequence_inference_keeps_variants_separate():
    class ExistingSpecialDb(FakeDb):
        def has_issue_or_download(self, magazine_id, issue_key):
            return False

        def issue_records(self, magazine_id):
            return [
                {
                    "issue_key": "2026-issue-0002",
                    "release_title": "Tech Title Make No 02 2026",
                    "pub_date": "",
                }
            ]

    settings = SimpleNamespace(past_days=999, min_size_mb=1, max_size_mb=0)
    magazine = {"id": 7, "title": "Tech Title"}
    db = ExistingSpecialDb()

    candidates = filter_candidates(
        db,
        settings,
        magazine,
        [
            QuasarrResult(
                "Tech Title No 01 2026 01 03",
                "https://example.test/normal-1",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Tech Title No 03 2026 01 17",
                "https://example.test/normal-3",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
            QuasarrResult(
                "Tech Title - 2026 01 10",
                "https://example.test/normal-date",
                "Sun, 24 May 2026 21:51:22 +0000",
                50 * 1024 * 1024,
                "quasarr",
            ),
        ],
    )

    assert [item.title for item in candidates] == [
        "Tech Title No 01 2026 01 03",
        "Tech Title No 03 2026 01 17",
        "Tech Title - 2026 01 10",
    ]
    assert db.skipped == []
