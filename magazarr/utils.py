# -*- coding: utf-8 -*-

import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class IssueDate:
    key: str
    value: date | None


@dataclass(frozen=True)
class IssueNumber:
    number: int
    year: int | None = None


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", errors="ignore").decode("ascii")
    return value.lower()


def tokens(value: str) -> list[str]:
    return [fix_token(item) for item in TOKEN_RE.findall(normalize_text(value))]


def fix_token(value: str) -> str:
    if value in {"&", "+"}:
        return "and"
    return value


def magazine_title_matches(title: str, release_title: str) -> bool:
    wanted = [token for token in tokens(title) if token]
    available = [token for token in tokens(release_title) if token]
    return bool(wanted) and available[: len(wanted)] == wanted


def token_sequence_matches(term: str, value: str) -> bool:
    wanted = [token for token in tokens(term) if token]
    available = [token for token in tokens(value) if token]
    if not wanted or len(wanted) > len(available):
        return False
    for idx in range(0, len(available) - len(wanted) + 1):
        if available[idx : idx + len(wanted)] == wanted:
            return True
    return False


def search_term(title: str) -> str:
    replacements = {
        "...": "",
        " & ": " and ",
        " + ": " plus ",
        " = ": " ",
        "?": "",
        "$": "s",
        '"': "",
        ",": "",
        "*": "",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    return re.sub(r"[.\-/]", " ", title).strip()


def parse_issue_date(title: str, pub_date: str = "") -> IssueDate | None:
    clean = " ".join(tokens(title))

    for pattern in (
        r"\b(20\d{2})[ ._-]?(0[1-9]|1[0-2])[ ._-]?([0-2]\d|3[01])\b",
        r"\b(20\d{2})[ ._-](0?[1-9]|1[0-2])[ ._-]([0-2]?\d|3[01])\b",
    ):
        match = re.search(pattern, clean)
        if match:
            value = _date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if value:
                return IssueDate(value.isoformat(), value)

    match = re.search(r"\b([0-2]?\d|3[01])[ ._-](0?[1-9]|1[0-2])[ ._-](20\d{2})\b", clean)
    if match:
        value = _date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if value:
            return IssueDate(value.isoformat(), value)

    words = clean.split()
    for idx, word in enumerate(words):
        month = MONTHS.get(word)
        if not month:
            continue
        year = _nearby_year(words, idx)
        if year:
            day = _nearby_day(words, idx)
            value = _date(year, month, day or 1)
            if value:
                return IssueDate(value.isoformat(), value)

    match = re.search(r"\b(?:issue|iss|no|nr|number)\s*(\d{1,4})(?:\s*(20\d{2}))?\b", clean)
    if match:
        number = int(match.group(1))
        year = match.group(2)
        if year:
            return IssueDate(f"{year}-issue-{number:04d}", None)
        return IssueDate(f"issue-{number:04d}", None)

    if pub_date:
        parsed = parse_rfc822(pub_date)
        if parsed:
            return IssueDate(parsed.date().isoformat(), parsed.date())

    return None


def parse_issue_number(title: str) -> IssueNumber | None:
    words = tokens(title)
    for idx, word in enumerate(words[:-1]):
        if word not in {"issue", "iss", "no", "nr", "number"}:
            continue
        number_word = words[idx + 1]
        if not number_word.isdigit():
            continue
        number = int(number_word)
        if number <= 0:
            continue
        year = None
        for pos in (idx + 2, idx + 3, idx - 1):
            if 0 <= pos < len(words) and re.fullmatch(r"20\d{2}", words[pos]):
                year = int(words[pos])
                break
        return IssueNumber(number=number, year=year)
    return None


def issue_aliases(
    title: str,
    pub_date: str = "",
    issue: IssueDate | None = None,
) -> set[str]:
    issue = issue or parse_issue_date(title, pub_date)
    aliases = set()
    if issue:
        aliases.add(issue.key)
        if issue.value:
            aliases.add(f"date:{issue.value.isoformat()}")

    number = parse_issue_number(title)
    if number:
        if number.year:
            aliases.add(f"{number.year}-issue-{number.number:04d}")
        aliases.add(f"issue-{number.number:04d}")

    aliases.update(_month_year_aliases(title))
    return aliases


def parse_rfc822(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def within_past_days(issue: IssueDate, pub_date: str, days: int) -> bool:
    if days <= 0:
        return True
    cutoff = date.today() - timedelta(days=days)
    if issue.value:
        return issue.value >= cutoff
    parsed = parse_rfc822(pub_date)
    if parsed:
        return parsed.date() >= cutoff
    return False


def pub_date_within_past_days(pub_date: str, days: int) -> bool | None:
    if days <= 0:
        return True
    parsed = parse_rfc822(pub_date)
    if not parsed:
        return None
    cutoff = date.today() - timedelta(days=days)
    return parsed.date() >= cutoff




def safe_filename(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", errors="ignore").decode("ascii")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = " ".join(value.split()).strip(" .")
    return value[:180] or "magazine"


def pdf_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/pdf"


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _nearby_year(words: list[str], idx: int) -> int | None:
    for pos in (idx + 1, idx - 1, idx + 2):
        if 0 <= pos < len(words) and re.fullmatch(r"20\d{2}", words[pos]):
            return int(words[pos])
    return None


def _nearby_day(words: list[str], idx: int) -> int | None:
    for pos in (idx - 1, idx + 1):
        if 0 <= pos < len(words) and re.fullmatch(r"\d{1,2}", words[pos]):
            day = int(words[pos])
            if 1 <= day <= 31:
                return day
    return None


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_year_aliases(title: str) -> set[str]:
    words = tokens(title)
    aliases = set()
    for idx, word in enumerate(words):
        month = MONTHS.get(word)
        year = _nearby_year(words, idx) if month else None
        if year and month:
            value = date(year, month, 1)
            aliases.add(value.isoformat())
            aliases.add(f"date:{value.isoformat()}")
            continue
        if re.fullmatch(r"20\d{2}", word):
            for pos in (idx + 1, idx - 1):
                if 0 <= pos < len(words) and words[pos].isdigit():
                    month = int(words[pos])
                    if 1 <= month <= 12:
                        value = date(int(word), month, 1)
                        aliases.add(value.isoformat())
                        aliases.add(f"date:{value.isoformat()}")
    return aliases
