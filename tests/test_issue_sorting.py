from magazarr.db import Database, _compute_sort_value


def test_numbered_key_with_dated_title_sorts_by_release_date():
    assert (
        _compute_sort_value("2026-issue-0044", "Weekly Title No 44 October 23 2026")
        == "2026-10-23"
    )


def test_month_title_numbered_key_sorts_by_month_start():
    assert (
        _compute_sort_value("2026-issue-0011", "Monthly Title November No 11 2026")
        == "2026-11-01"
    )


def test_number_only_key_keeps_number_fallback():
    assert _compute_sort_value("2026-issue-0044", "Weekly Title No 44") == (
        "2026-00-0044"
    )


def test_newer_numbered_dated_issue_sorts_after_older_date_issue(tmp_path):
    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine_id = db.magazines()[0]["id"]
    db.record_issue(
        magazine_id,
        "2026-10-02",
        "Magazine Title October 2 2026",
        str(tmp_path / "older.pdf"),
        100,
        "older",
    )
    db.record_issue(
        magazine_id,
        "2026-issue-0044",
        "Magazine Title No 44 October 23 2026",
        str(tmp_path / "newer.pdf"),
        100,
        "newer",
    )

    issues = db.issues(limit=-1, magazine_id=magazine_id)

    assert [row["issue_key"] for row in issues] == [
        "2026-issue-0044",
        "2026-10-02",
    ]
