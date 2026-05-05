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
    assert search_term("Edge & Space/Time") == "Edge and Space Time"


def test_title_match_uses_tokens_not_substrings():
    assert magazine_title_matches("Maxim USA", "Maxim USA May 2026")
    assert not magazine_title_matches("Maxim", "Maximum PC May 2026")
    assert not magazine_title_matches("ct", "Cthulhus Ruf - Hajo Bremer")
    assert magazine_title_matches("Der Spiegel", "Der Spiegel No 19 2026")
    assert not magazine_title_matches(
        "Der Spiegel",
        "Der Tod wird euch finden - Ein SPIEGEL-Buch - Lawrence Wright",
    )
    assert not magazine_title_matches("Der Spiegel", "DeepViolette - Der Spiegel")


def test_blacklist_term_uses_token_sequence():
    assert token_sequence_matches("ct fotografie", "ct Fotografie 2026")
    assert not token_sequence_matches("ct fotografie", "ct Sonderheft Fotografie")


def test_parse_month_year_issue_date():
    issue = parse_issue_date("Linux Format May 2026")
    assert issue is not None
    assert issue.key == "2026-05-01"


def test_parse_day_month_year_issue_date():
    issue = parse_issue_date("The Week 14 May 2026")
    assert issue is not None
    assert issue.key == "2026-05-14"


def test_parse_issue_number():
    issue = parse_issue_date("Linux Format Issue 116 - KDE")
    assert issue is not None
    assert issue.key == "issue-0116"


def test_parse_numbered_magazine_release():
    number = parse_issue_number("Der.Spiegel.No.23.2024.GERMAN.Retail.MAGAZiNE.eBook")

    assert number is not None
    assert number.year == 2024
    assert number.number == 23


def test_weekly_number_aliases_can_match_dated_titles():
    modes = infer_numbering_modes(
        [
            "Der Spiegel No 18 2026",
            "Der Spiegel No 19 2026",
            "Der Spiegel No 20 2026",
        ]
    )
    issue = parse_issue_date("Der Spiegel Nachrichtenmagazin 2026 04 30")

    assert modes == {2026: "weekly"}
    assert issue is not None
    assert "2026-issue-0019" in issue_aliases(
        "Der Spiegel Nachrichtenmagazin 2026 04 30",
        "",
        issue,
        modes,
    )


def test_within_past_days_uses_issue_date():
    issue = parse_issue_date(date.today().isoformat())
    assert issue is not None
    assert within_past_days(issue, "", 1)
    old_issue = parse_issue_date((date.today() - timedelta(days=30)).isoformat())
    assert old_issue is not None
    assert not within_past_days(old_issue, "", 7)
