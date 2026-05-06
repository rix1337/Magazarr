from datetime import date, timedelta

from magazarr.utils import (
    infer_numbering_modes,
    issue_aliases,
    magazine_title_matches,
    parse_issue_date,
    parse_issue_number,
    search_term,
    token_sequence_matches,
    within_past_days,
)


def test_search_term_matches_lazylibrarian_style_cleanup():
    assert search_term("Fiction & Space/Time") == "Fiction and Space Time"


def test_title_match_uses_tokens_not_substrings():
    assert magazine_title_matches("Example Monthly USA", "Example Monthly USA May 2026")
    assert not magazine_title_matches("Example Monthly", "Example Monthlyum PC May 2026")
    assert not magazine_title_matches("Magazine Title", "Unrelated Book - Example Author")
    assert magazine_title_matches("Magazine Title", "Magazine Title No 19 2026")
    assert not magazine_title_matches(
        "Magazine Title",
        "Unrelated Book - Example Author",
    )
    assert not magazine_title_matches("Magazine Title", "Unrelated Title - Magazine Title")


def test_blacklist_term_uses_token_sequence():
    assert token_sequence_matches("Magazine Title Photo", "Magazine Title Photo 2026")
    assert not token_sequence_matches("Magazine Title Photo", "Magazine Title Special Photo Issue")


def test_parse_month_year_issue_date():
    issue = parse_issue_date("Magazine Title Three May 2026")
    assert issue is not None
    assert issue.key == "2026-05-01"


def test_parse_day_month_year_issue_date():
    issue = parse_issue_date("Weekly Title 14 May 2026")
    assert issue is not None
    assert issue.key == "2026-05-14"


def test_parse_issue_number():
    issue = parse_issue_date("Magazine Title Three Issue 116 - Desktop Topic")
    assert issue is not None
    assert issue.key == "issue-0116"


def test_parse_numbered_magazine_release():
    number = parse_issue_number(
        "Magazine.Title.No.23.2024.GERMAN.Retail.MAGAZiNE.eBook"
    )

    assert number is not None
    assert number.year == 2024
    assert number.number == 23


def test_weekly_number_aliases_can_match_dated_titles():
    modes = infer_numbering_modes(
        [
            "Magazine Title No 18 2026",
            "Magazine Title No 19 2026",
            "Magazine Title No 20 2026",
        ]
    )
    issue = parse_issue_date("Magazine Title News Magazine 2026 04 30")

    assert modes == {2026: "weekly"}
    assert issue is not None
    assert "2026-issue-0019" in issue_aliases(
        "Magazine Title News Magazine 2026 04 30",
        "",
        issue,
        modes,
    )


def test_monthly_number_aliases_match_month_year_titles():
    modes = infer_numbering_modes(
        [
            "Magazine Title Issue 04 2026",
            "Magazine Title Issue 05 2026",
            "Magazine Title Issue 06 2026",
        ]
    )

    assert modes == {2026: "monthly"}
    assert "2026-issue-0005" in issue_aliases(
        "Magazine Title UK May 2026",
        "",
        None,
        modes,
    )


def test_issue_number_release_aliases_numeric_month_year():
    assert issue_aliases("Magazine Title UK - 2026 05") & issue_aliases(
        "Magazine Title UK - Issue 421, 2026 05",
    )


def test_within_past_days_uses_issue_date():
    issue = parse_issue_date(date.today().isoformat())
    assert issue is not None
    assert within_past_days(issue, "", 1)
    old_issue = parse_issue_date((date.today() - timedelta(days=30)).isoformat())
    assert old_issue is not None
    assert not within_past_days(old_issue, "", 7)
