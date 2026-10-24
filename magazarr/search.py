# -*- coding: utf-8 -*-

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from loguru import logger

from magazarr.notifications import notify_download_started, notify_error
from magazarr.quasarr_client import QuasarrClient, QuasarrResult
from magazarr.settings import Settings
from magazarr.utils import (
    MONTHS,
    IssueDate,
    issue_aliases,
    magazine_title_matches,
    parse_issue_date,
    parse_issue_number,
    pub_date_within_past_days,
    search_term,
    token_sequence_matches,
    tokens,
    within_past_days,
)


@dataclass(frozen=True)
class Candidate:
    magazine_id: int
    magazine_title: str
    title: str
    download_url: str
    pub_date: str
    size_bytes: int
    issue_key: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SequenceAnchor:
    family: tuple[str, ...]
    year: int
    issue_number: int
    issue_date: date


@dataclass(frozen=True)
class SequenceProfile:
    family: tuple[str, ...]
    year: int
    weekly_offsets: tuple[int, ...] = ()
    ordinal_week_offsets: tuple[int, ...] = ()
    month_offsets: tuple[int, ...] = ()
    cadence_days: int | None = None
    anchors: tuple[SequenceAnchor, ...] = ()


def search_magazine(
    db, settings: Settings, magazine, on_progress=None
) -> list[Candidate]:
    client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
    query = search_term(magazine["title"])
    logger.info(f"Searching {magazine['title']} with query {query}")
    _emit(on_progress, "searching", magazine, query=query)

    def on_page(offset, page):
        _emit(on_progress, "page", magazine, offset=offset, count=len(page))

    results = client.search(
        query,
        settings.quasarr_search_category,
        on_page=on_page,
    )
    _emit(on_progress, "results", magazine, count=len(results))
    candidates = filter_candidates(db, settings, magazine, results, on_progress)

    downloads = _dedupe_candidates(candidates)
    sent_downloads = []
    for candidate in downloads:
        if _has_active_release_download(db, candidate):
            _emit(
                on_progress,
                "skipped",
                magazine,
                release_title=candidate.title,
                reason="duplicate",
                details=candidate.issue_key,
            )
            continue
        _emit(on_progress, "sending", magazine, release_title=candidate.title)
        try:
            package_ids = client.add_url(
                candidate.download_url,
                settings.quasarr_download_category,
            )
        except Exception as exc:
            notify_error(
                settings,
                "Download start failed",
                str(exc),
                magazine_title=candidate.magazine_title,
                release_title=candidate.title,
            )
            raise
        package_id = package_ids[0] if package_ids else None
        download_id = db.record_download(candidate.magazine_id, candidate, package_id)
        reference = notify_download_started(
            settings,
            candidate.magazine_title,
            candidate.title,
            package_id,
        )
        if reference:
            db.update_download_notifications(download_id, {"discord": reference})
        sent_downloads.append(candidate)
        logger.info(f"Sent {candidate.title} to Quasarr as {package_id or 'unknown'}")
        _emit(
            on_progress,
            "sent",
            magazine,
            release_title=candidate.title,
            package_id=package_id or "",
        )
    return sent_downloads


def search_all(db, settings: Settings, on_progress=None) -> dict[str, int]:
    summary = {}
    for magazine in db.magazines(active_only=True):
        summary[magazine["title"]] = len(
            search_magazine(db, settings, magazine, on_progress)
        )
    return summary


def filter_candidates(
    db,
    settings: Settings,
    magazine,
    results: list[QuasarrResult],
    on_progress=None,
) -> list[Candidate]:
    candidates = []
    min_size = settings.min_size_mb * 1024 * 1024
    max_size = settings.max_size_mb * 1024 * 1024
    blacklist_terms = (
        db.blacklist_terms(magazine["id"]) if hasattr(db, "blacklist_terms") else []
    )
    issue_records = _issue_records(db, magazine["id"])
    titles = [row[0] for row in issue_records] + [
        result.title
        for result in results
        if magazine_title_matches(magazine["title"], result.title)
    ]
    profiles = _sequence_profiles(magazine["title"], titles)
    monthly_families = _monthly_families(magazine["title"], titles)
    existing_aliases = _existing_aliases(
        issue_records, profiles, magazine["title"], monthly_families
    )

    for result in results:
        recent_by_pub_date = pub_date_within_past_days(
            result.pub_date, settings.past_days
        )
        if recent_by_pub_date is False:
            continue
        blacklisted_by = next(
            (
                term
                for term in blacklist_terms
                if token_sequence_matches(term, result.title)
            ),
            "",
        )
        if blacklisted_by:
            _record_skip(db, magazine, result, "blacklisted")
            _emit_skip(on_progress, magazine, result, "blacklisted", blacklisted_by)
            continue
        if min_size and result.size_bytes and result.size_bytes < min_size:
            _record_skip(db, magazine, result, "too_small")
            _emit_skip(on_progress, magazine, result, "too_small")
            continue
        if max_size and result.size_bytes > max_size:
            _record_skip(db, magazine, result, "too_large")
            _emit_skip(on_progress, magazine, result, "too_large")
            continue
        if _has_skipped_release(db, magazine["id"], result):
            _emit_skip(on_progress, magazine, result, "skipped")
            continue
        if not magazine_title_matches(magazine["title"], result.title):
            _emit_skip(on_progress, magazine, result, "title_mismatch")
            continue
        issue = parse_issue_date(result.title, result.pub_date)
        if not issue:
            _record_skip(db, magazine, result, "issue_unparsed")
            _emit_skip(on_progress, magazine, result, "issue_unparsed")
            continue
        if not within_past_days(issue, result.pub_date, settings.past_days):
            _record_skip(db, magazine, result, "outside_past_days", issue.key)
            continue
        aliases = _release_aliases(
            result.title,
            result.pub_date,
            issue,
            profiles,
            magazine["title"],
            monthly_families,
        )
        number = parse_issue_number(result.title)
        family = _release_family(magazine["title"], result.title)
        scoped_family = _family_scoped_only(family)
        issue_key = _canonical_issue_key(issue, number)
        if scoped_family:
            issue_key = f"{issue_key}-edition-{'.'.join(family)}"
        if (
            _has_issue_key_duplicate(
                db,
                magazine["id"],
                issue_key,
                bypass=bool(number or scoped_family),
            )
            or aliases & existing_aliases
        ):
            _record_skip(db, magazine, result, "duplicate", issue_key)
            _emit_skip(on_progress, magazine, result, "duplicate", issue_key)
            continue
        candidate = Candidate(
            magazine_id=magazine["id"],
            magazine_title=magazine["title"],
            title=result.title,
            download_url=result.download_url,
            pub_date=result.pub_date,
            size_bytes=result.size_bytes,
            issue_key=issue_key,
            aliases=tuple(sorted(aliases)),
        )
        candidates.append(candidate)
        _emit(
            on_progress,
            "candidate",
            magazine,
            release_title=result.title,
            issue_key=issue.key,
        )
    return candidates


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    downloads = []
    claimed_aliases = set()
    for candidate in sorted(candidates, key=lambda item: item.size_bytes, reverse=True):
        aliases = set(candidate.aliases) or {candidate.issue_key}
        if aliases & claimed_aliases:
            continue
        downloads.append(candidate)
        claimed_aliases.update(aliases)
    return downloads


def _issue_records(db, magazine_id: int) -> list[tuple[str, str, str]]:
    records = []
    if not hasattr(db, "issue_records"):
        return records
    for row in db.issue_records(magazine_id):
        title = row["release_title"] if hasattr(row, "keys") else row[1]
        pub_date = row["pub_date"] if hasattr(row, "keys") else row[2]
        key = row["issue_key"] if hasattr(row, "keys") else row[0]
        records.append((str(title or ""), str(pub_date or ""), str(key or "")))
    return records


def _existing_aliases(
    issue_records: list[tuple[str, str, str]],
    profiles: list[SequenceProfile],
    magazine_title: str,
    monthly_families: set[tuple[tuple[str, ...], int]],
) -> set[str]:
    aliases = set()
    for title, pub_date, key in issue_records:
        issue = parse_issue_date(title, pub_date) or parse_issue_date(key)
        if issue is None and key:
            issue = IssueDate(str(key), None)
        aliases.update(
            _release_aliases(
                title, pub_date, issue, profiles, magazine_title, monthly_families
            )
        )
    return aliases


def _release_aliases(
    title: str,
    pub_date: str,
    issue: IssueDate | None,
    profiles: list[SequenceProfile],
    magazine_title: str,
    monthly_families: set[tuple[tuple[str, ...], int]],
) -> set[str]:
    aliases = issue_aliases(title, pub_date, issue)
    family = _release_family(magazine_title, title)
    number = parse_issue_number(title)
    if number:
        # An explicit issue number is authoritative. A date in the same title
        # may be a publication date for a different numbered release.
        if issue and issue.value:
            aliases.discard(issue.key)
            aliases.discard(f"date:{issue.value.isoformat()}")
    elif _family_scoped_only(family) and issue and issue.value:
        # Calendar aliases must stay inside recognized edition families.
        aliases.discard(issue.key)
        aliases.discard(f"date:{issue.value.isoformat()}")
        aliases.add(f"date:{'.'.join(family)}:{issue.value.isoformat()}")
    if issue and issue.value and (not number or issue.precision == "month"):
        for alias_family in _compatible_alias_families(family):
            if (alias_family, issue.value.year) in monthly_families:
                aliases.add(
                    f"monthly:{'.'.join(alias_family)}:{issue.value.year}-{issue.value.month:02d}"
                )
    if number:
        aliases.discard(f"issue-{number.number:04d}")
        alias_families = _alias_families(family, number.year, issue, profiles)
        if number.year is None:
            for alias_family in alias_families:
                aliases.add(
                    _sequence_issue_alias(alias_family, f"issue-{number.number:04d}")
                )
        if number.year:
            aliases.discard(f"{number.year}-issue-{number.number:04d}")
            for alias_family in alias_families:
                aliases.add(
                    _sequence_issue_alias(
                        alias_family,
                        f"{number.year}-issue-{number.number:04d}",
                    )
                )
    if not number:
        aliases.update(
            _inferred_sequence_aliases(title, issue, profiles, magazine_title)
        )
    return aliases


def _monthly_families(
    magazine_title: str, titles: list[str]
) -> set[tuple[tuple[str, ...], int]]:
    months: dict[tuple[tuple[str, ...], int], set[int]] = {}
    dated: dict[tuple[tuple[str, ...], int], dict[int, set[int]]] = {}
    for title in titles:
        issue = parse_issue_date(title)
        if not issue or not issue.value:
            continue
        family = _release_family(magazine_title, title)
        for alias_family in _compatible_alias_families(family):
            key = (alias_family, issue.value.year)
            if issue.precision == "month":
                # "Ausgabe YYYY-NN" is an issue number, not monthly evidence.
                if not parse_issue_number(title) or set(tokens(title)) & MONTHS.keys():
                    months.setdefault(key, set()).add(issue.value.month)
            else:
                dated.setdefault(key, {}).setdefault(issue.value.month, set()).add(
                    issue.value.day
                )
    # Weekly and biweekly series can also have ambiguous YYYY-NN listings.
    # Multiple dated issues in a month rule out monthly aliasing for that series.
    return {
        family
        for family, values in months.items()
        if len(values) >= 2
        and all(len(days) <= 1 for days in dated.get(family, {}).values())
    }


def _sequence_profiles(magazine_title: str, titles: list[str]) -> list[SequenceProfile]:
    anchors = [_sequence_anchor(magazine_title, title) for title in titles]
    anchors = [anchor for anchor in anchors if anchor]
    grouped: dict[tuple[tuple[str, ...], int], list[SequenceAnchor]] = {}
    for anchor in anchors:
        grouped.setdefault((anchor.family, anchor.year), []).append(anchor)

    profiles = []
    for (family, year), items in grouped.items():
        by_pair = {(item.issue_number, item.issue_date): item for item in items}
        unique_items = tuple(by_pair.values())
        if len(unique_items) < 2:
            continue
        profiles.append(
            SequenceProfile(
                family=family,
                year=year,
                weekly_offsets=_common_offsets(
                    item.issue_number - item.issue_date.isocalendar().week
                    for item in unique_items
                ),
                ordinal_week_offsets=_common_offsets(
                    item.issue_number - _ordinal_week(item.issue_date)
                    for item in unique_items
                ),
                month_offsets=_common_offsets(
                    item.issue_number - item.issue_date.month for item in unique_items
                ),
                cadence_days=_cadence_days(unique_items),
                anchors=unique_items,
            )
        )
    return profiles


def _sequence_anchor(magazine_title: str, title: str) -> SequenceAnchor | None:
    issue = parse_issue_date(title, "")
    number = parse_issue_number(title)
    if issue is None or issue.value is None or number is None:
        return None
    year = number.year or issue.value.year
    if year != issue.value.year:
        return None
    return SequenceAnchor(
        family=_release_family(magazine_title, title),
        year=year,
        issue_number=number.number,
        issue_date=issue.value,
    )


def _inferred_sequence_aliases(
    title: str,
    issue: IssueDate | None,
    profiles: list[SequenceProfile],
    magazine_title: str,
) -> set[str]:
    issue = issue or parse_issue_date(title, "")
    if not issue or not issue.value:
        return set()
    matching_profiles = _matching_profiles(
        profiles,
        _release_family(magazine_title, title),
        issue.value.year,
    )
    predictions = [
        _inferred_issue_numbers(issue.value, profile) for profile in matching_profiles
    ]
    if not predictions or any(len(numbers) != 1 for numbers in predictions):
        return set()
    inferred_numbers = {next(iter(numbers)) for numbers in predictions}
    if len(inferred_numbers) != 1:
        return set()
    number = next(iter(inferred_numbers))
    issue_numbers = {(profile.family, number) for profile in matching_profiles}
    return {
        _sequence_issue_alias(family, f"{issue.value.year}-issue-{number:04d}")
        for profile_family, number in issue_numbers
        for family in _compatible_alias_families(profile_family)
    }


def _matching_profiles(
    profiles: list[SequenceProfile],
    family: tuple[str, ...],
    year: int,
) -> list[SequenceProfile]:
    year_profiles = [profile for profile in profiles if profile.year == year]
    if family:
        return [profile for profile in year_profiles if profile.family == family]
    return [
        profile for profile in year_profiles if not _family_scoped_only(profile.family)
    ]


def _alias_families(
    family: tuple[str, ...],
    number_year: int | None,
    issue: IssueDate | None,
    profiles: list[SequenceProfile],
) -> tuple[tuple[str, ...], ...]:
    if family:
        return _compatible_alias_families(family)
    year = number_year or (issue.value.year if issue and issue.value else None)
    if year is None:
        return (family,)
    year_profiles = [
        profile
        for profile in profiles
        if profile.year == year and not _family_scoped_only(profile.family)
    ]
    if len(year_profiles) == 1:
        return tuple(
            dict.fromkeys(
                (
                    family,
                    *_compatible_alias_families(year_profiles[0].family),
                )
            )
        )
    return (family,)


def _compatible_alias_families(family: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if not family:
        return (family,)
    if _family_scoped_only(family):
        return (family,)
    return (family, ())


def _family_scoped_only(family: tuple[str, ...]) -> bool:
    return bool(
        set(family)
        & {
            "make",
            "special",
            "spezial",
            "uk",
            "usa",
            "us",
            "eu",
            "edition",
            "sonderheft",
            "sonderhefte",
            "fotografie",
        }
    )


def _inferred_issue_numbers(issue_date: date, profile: SequenceProfile) -> set[int]:
    numbers = set()
    for offset in profile.weekly_offsets:
        numbers.add(issue_date.isocalendar().week + offset)
    for offset in profile.ordinal_week_offsets:
        numbers.add(_ordinal_week(issue_date) + offset)
    for offset in profile.month_offsets:
        numbers.add(issue_date.month + offset)
    if profile.cadence_days:
        for anchor in profile.anchors:
            delta = (issue_date - anchor.issue_date).days
            if delta % profile.cadence_days == 0:
                numbers.add(anchor.issue_number + delta // profile.cadence_days)
    return {number for number in numbers if 1 <= number <= 9999}


def _release_family(magazine_title: str, title: str) -> tuple[str, ...]:
    words = tokens(title)
    magazine_words = tokens(magazine_title)
    if words[: len(magazine_words)] == magazine_words:
        words = words[len(magazine_words) :]
    family = []
    skip_next_number = False
    for word in words:
        if skip_next_number and word.isdigit():
            skip_next_number = False
            continue
        skip_next_number = False
        if word in {"issue", "iss", "no", "nr", "number", "ausgabe"}:
            skip_next_number = True
            continue
        if (
            word.isdigit()
            or word in MONTHS
            or word
            in {
                "vom",
                "aktuelle",
                "inkl",
                "jahresarchiven",
                "einzeln",
                "ladbar",
            }
        ):
            continue
        family.append(word)
    return tuple(family)


def _sequence_issue_alias(family: tuple[str, ...], issue_key: str) -> str:
    return f"sequence:{'.'.join(family)}:{issue_key}"


def _canonical_issue_key(issue: IssueDate, number) -> str:
    if number:
        year = number.year or (issue.value.year if issue.value else None)
        if year:
            return f"{year}-issue-{number.number:04d}"
        return f"issue-{number.number:04d}"
    return issue.key


def _has_issue_key_duplicate(
    db, magazine_id: int, issue_key: str, bypass: bool = False
) -> bool:
    if bypass:
        return False
    if _numbered_issue_key(issue_key) and hasattr(db, "issue_records"):
        return False
    return db.has_issue_or_download(magazine_id, issue_key)


def _numbered_issue_key(issue_key: str) -> bool:
    return bool(re.fullmatch(r"(?:20\d{2}-)?issue-\d{4}", issue_key or ""))


def _common_offsets(offsets) -> tuple[int, ...]:
    values = tuple(offsets)
    if len(values) < 2:
        return ()
    offset, support = Counter(values).most_common(1)[0]
    if support < 2 or support * 2 <= len(values):
        return ()
    return (offset,)


def _ordinal_week(value: date) -> int:
    return ((value.timetuple().tm_yday - 1) // 7) + 1


def _cadence_days(anchors: tuple[SequenceAnchor, ...]) -> int | None:
    pairs = []
    sorted_anchors = sorted(anchors, key=lambda item: item.issue_number)
    for current, following in zip(sorted_anchors, sorted_anchors[1:], strict=False):
        number_delta = following.issue_number - current.issue_number
        day_delta = (following.issue_date - current.issue_date).days
        if number_delta <= 0 or day_delta <= 0:
            continue
        pairs.append((number_delta, day_delta))
    for cadence in (7, 14, 28, 30, 31):
        if pairs and all(
            abs(day_delta - cadence * number_delta) <= 1
            for number_delta, day_delta in pairs
        ):
            return cadence
    return None


def _has_active_release_download(db, candidate: Candidate) -> bool:
    if hasattr(db, "has_active_release_download"):
        return db.has_active_release_download(
            candidate.magazine_id,
            candidate.issue_key,
            candidate.title,
            candidate.download_url,
        )
    return db.has_issue_or_download(candidate.magazine_id, candidate.issue_key)


def _has_skipped_release(db, magazine_id: int, result: QuasarrResult) -> bool:
    if not hasattr(db, "has_skipped_release"):
        return False
    return db.has_skipped_release(magazine_id, result.title, result.download_url)


def _record_skip(db, magazine, result: QuasarrResult, reason: str, issue_key: str = ""):
    if hasattr(db, "record_skipped_release"):
        db.record_skipped_release(magazine["id"], result, reason, issue_key)


def _emit_skip(
    on_progress, magazine, result: QuasarrResult, reason: str, details: str = ""
):
    _emit(
        on_progress,
        "skipped",
        magazine,
        release_title=result.title,
        reason=reason,
        details=details,
    )


def _emit(on_progress, event: str, magazine, **payload):
    if on_progress:
        on_progress(
            {
                "event": event,
                "magazine_id": magazine["id"],
                "magazine_title": magazine["title"],
                **payload,
            }
        )
