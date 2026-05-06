# -*- coding: utf-8 -*-

from dataclasses import dataclass

from loguru import logger

from magazarr.notifications import notify_download_started, notify_error
from magazarr.quasarr_client import QuasarrClient, QuasarrResult
from magazarr.settings import Settings
from magazarr.utils import (
    IssueDate,
    infer_numbering_modes,
    issue_aliases,
    magazine_title_matches,
    parse_issue_date,
    pub_date_within_past_days,
    search_term,
    token_sequence_matches,
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


def search_magazine(db, settings: Settings, magazine, on_progress=None) -> list[Candidate]:
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
        db.record_download(candidate.magazine_id, candidate, package_id)
        notify_download_started(
            settings,
            candidate.magazine_title,
            candidate.title,
            package_id,
        )
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
    blacklist_terms = db.blacklist_terms(magazine["id"]) if hasattr(db, "blacklist_terms") else []
    numbering_modes = infer_numbering_modes(
        [
            result.title
            for result in results
            if magazine_title_matches(magazine["title"], result.title)
        ]
    )
    existing_aliases = _existing_aliases(db, magazine["id"], numbering_modes)

    for result in results:
        recent_by_pub_date = pub_date_within_past_days(result.pub_date, settings.past_days)
        if recent_by_pub_date is False:
            _emit_skip(on_progress, magazine, result, "outside_past_days", result.pub_date)
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
            _emit_skip(on_progress, magazine, result, "outside_past_days", issue.key)
            continue
        aliases = issue_aliases(result.title, result.pub_date, issue, numbering_modes)
        if db.has_issue_or_download(magazine["id"], issue.key) or aliases & existing_aliases:
            _record_skip(db, magazine, result, "duplicate", issue.key)
            _emit_skip(on_progress, magazine, result, "duplicate", issue.key)
            continue
        candidate = Candidate(
            magazine_id=magazine["id"],
            magazine_title=magazine["title"],
            title=result.title,
            download_url=result.download_url,
            pub_date=result.pub_date,
            size_bytes=result.size_bytes,
            issue_key=issue.key,
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


def _existing_aliases(db, magazine_id: int, numbering_modes: dict[int, str]) -> set[str]:
    aliases = set()
    if not hasattr(db, "issue_records"):
        return aliases
    for row in db.issue_records(magazine_id):
        title = row["release_title"] if hasattr(row, "keys") else row[1]
        pub_date = row["pub_date"] if hasattr(row, "keys") else row[2]
        key = row["issue_key"] if hasattr(row, "keys") else row[0]
        issue = parse_issue_date(title, pub_date) or parse_issue_date(key)
        if issue is None and key:
            issue = IssueDate(str(key), None)
        aliases.update(issue_aliases(title, pub_date, issue, numbering_modes))
    return aliases


def _has_active_release_download(db, candidate: Candidate) -> bool:
    if hasattr(db, "has_active_release_download"):
        return db.has_active_release_download(
            candidate.magazine_id,
            candidate.issue_key,
            candidate.title,
            candidate.download_url,
        )
    return db.has_issue_or_download(candidate.magazine_id, candidate.issue_key)


def _record_skip(db, magazine, result: QuasarrResult, reason: str, issue_key: str = ""):
    if hasattr(db, "record_skipped_release"):
        db.record_skipped_release(magazine["id"], result, reason, issue_key)


def _emit_skip(on_progress, magazine, result: QuasarrResult, reason: str, details: str = ""):
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
