# -*- coding: utf-8 -*-

import base64
import binascii
import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree

from bottle import HTTPError, request, response, static_file

from magazarr.cover import (
    COVER_MIME,
    THUMBNAIL_MIME,
    CoverError,
    extract_pdf_cover,
    extract_pdf_thumbnail,
)
from magazarr.settings import Settings
from magazarr.utils import clean_release_title, pdf_mime

NS = "http://www.w3.org/2005/Atom"
OPDS = "http://opds-spec.org/2010/catalog"
ACQ = "http://opds-spec.org/acquisition"
IMAGE = "http://opds-spec.org/image"
THUMBNAIL = "http://opds-spec.org/image/thumbnail"
NAV = "application/atom+xml; profile=opds-catalog; kind=navigation"
ACQ_FEED = "application/atom+xml; profile=opds-catalog; kind=acquisition"

ElementTree.register_namespace("", NS)


def require_opds_auth(settings: Settings):
    if not settings.opds_auth_enabled:
        return
    scheme, _, credentials = request.get_header("Authorization", "").partition(" ")
    if scheme.lower() != "basic" or not credentials:
        _auth_challenge()
    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        _auth_challenge()
    if not (
        hmac.compare_digest(username, settings.opds_username)
        and hmac.compare_digest(password, settings.opds_password)
    ):
        _auth_challenge()


def handle_opds(db, settings: Settings):
    require_opds_auth(settings)
    cmd = str(request.query.get("cmd") or "root")
    if cmd == "root":
        return root_feed(db)
    if cmd == "RecentMags":
        return recent_feed(db, settings)
    if cmd == "Magazines":
        return magazines_feed(db, settings)
    if cmd == "Magazine":
        return magazine_feed(db, settings, _id_param("magid"))
    if cmd == "Serve":
        return serve_issue(db, _id_param("issueid"))
    if cmd == "Cover":
        return serve_cover(
            db, _id_param("issueid"), thumbnail=request.query.get("size") == "thumb"
        )
    raise HTTPError(404, f"Unknown OPDS command: {cmd}")


def root_feed(db):
    entries = []
    recent_count = len(db.recent_issues(limit=1))
    magazine_count = len(db.magazines())
    if recent_count:
        entries.append(
            nav_entry(
                "Recent Magazine Issues",
                "RecentMags",
                "/opds?cmd=RecentMags",
                "Recently imported magazine issues",
            )
        )
    if magazine_count:
        entries.append(
            nav_entry(
                "Magazines",
                "Magazines",
                "/opds?cmd=Magazines",
                "Magazine titles",
            )
        )
    return atom("Magazarr OPDS", "root", "/opds", entries)


def recent_feed(db, settings: Settings):
    offset, size = _offset(), _page_size(settings)
    issues = list(db.recent_issues(size + 1, offset))
    return atom(
        "Magazarr OPDS - Recent Magazines",
        "RecentMags",
        "/opds?cmd=RecentMags",
        [issue_entry(issue) for issue in issues[:size]],
        paging=_paging("/opds?cmd=RecentMags", offset, size, len(issues) > size),
    )


def magazines_feed(db, settings: Settings):
    entries = []
    for mag in db.magazines():
        if mag["issue_count"] <= 0:
            continue
        entries.append(
            nav_entry(
                f"{mag['title']} ({mag['issue_count']})",
                f"magazine:{mag['id']}",
                f"/opds?cmd=Magazine&magid={mag['id']}",
                mag["title"],
            )
        )
    return atom(
        "Magazarr OPDS - Magazines", "Magazines", "/opds?cmd=Magazines", entries
    )


def magazine_feed(db, settings: Settings, magazine_id: int):
    mag = db.magazine_by_id(magazine_id)
    if not mag:
        raise HTTPError(404, "Magazine not found")
    offset, size = _offset(), _page_size(settings)
    issues = list(db.issues_for_magazine(magazine_id, size + 1, offset))
    href = f"/opds?cmd=Magazine&magid={magazine_id}"
    return atom(
        f"Magazarr OPDS - {mag['title']}",
        f"magazine:{magazine_id}",
        href,
        [issue_entry(issue) for issue in issues[:size]],
        paging=_paging(href, offset, size, len(issues) > size),
    )


def serve_issue(db, issue_id: int):
    issue = db.issue_by_id(issue_id)
    if not issue:
        raise HTTPError(404, "Issue not found")
    path = Path(issue["file_path"])
    if not path.exists():
        raise HTTPError(404, "Issue file missing")
    return static_file(
        path.name,
        root=str(path.parent),
        mimetype=pdf_mime(str(path)),
        download=path.name,
    )


def serve_cover(db, issue_id: int, thumbnail: bool = False):
    issue = db.issue_by_id(issue_id)
    if not issue:
        raise HTTPError(404, "Issue not found")
    path = Path(issue["file_path"])
    if not path.exists():
        raise HTTPError(404, "Issue file missing")
    try:
        cover_path = (extract_pdf_thumbnail if thumbnail else extract_pdf_cover)(path)
    except CoverError as exc:
        raise HTTPError(404, str(exc)) from exc
    result = static_file(
        cover_path.name,
        root=str(cover_path.parent),
        mimetype=THUMBNAIL_MIME if thumbnail else COVER_MIME,
    )
    # Covers only change when the PDF does; let clients reuse them and revalidate
    # with Last-Modified instead of re-requesting every grid render.
    result.set_header("Cache-Control", "private, max-age=86400")
    return result


def atom(
    title: str,
    feed_id: str,
    self_href: str,
    entries: list[ElementTree.Element],
    paging: dict[str, str] | None = None,
):
    response.content_type = "application/atom+xml; charset=utf-8"
    feed = ElementTree.Element(_tag("feed"))
    ElementTree.SubElement(feed, _tag("id")).text = feed_id
    ElementTree.SubElement(feed, _tag("title")).text = title
    ElementTree.SubElement(feed, _tag("updated")).text = _now()
    ElementTree.SubElement(
        feed,
        _tag("link"),
        {"href": "/opds", "rel": "start", "type": NAV, "title": "Home"},
    )
    ElementTree.SubElement(
        feed, _tag("link"), {"href": self_href, "rel": "self", "type": NAV}
    )
    for rel, href in (paging or {}).items():
        ElementTree.SubElement(
            feed, _tag("link"), {"href": href, "rel": rel, "type": ACQ_FEED}
        )
    for entry in entries:
        feed.append(entry)
    return ElementTree.tostring(feed, encoding="utf-8", xml_declaration=True)


def nav_entry(title: str, entry_id: str, href: str, content: str):
    entry = _entry(title, entry_id, content)
    ElementTree.SubElement(
        entry,
        _tag("link"),
        {"href": href, "rel": "subsection", "type": NAV},
    )
    return entry


def issue_entry(issue):
    title = f"{issue['magazine_title']} ({clean_release_title(issue['issue_key'])})"
    entry = _entry(
        title,
        f"issue:{issue['id']}",
        clean_release_title(issue["release_title"]),
        _atom_time(issue["acquired_at"] if "acquired_at" in issue.keys() else ""),
    )
    issue_id = quote_plus(str(issue["id"]))
    ElementTree.SubElement(
        entry,
        _tag("link"),
        {
            "href": f"/opds?cmd=Serve&issueid={issue_id}",
            "rel": ACQ,
            "type": pdf_mime(issue["file_path"]),
        },
    )
    for rel, href, mime in (
        (IMAGE, f"/opds?cmd=Cover&issueid={issue_id}", COVER_MIME),
        (THUMBNAIL, f"/opds?cmd=Cover&issueid={issue_id}&size=thumb", THUMBNAIL_MIME),
    ):
        ElementTree.SubElement(
            entry, _tag("link"), {"href": href, "rel": rel, "type": mime}
        )
    ElementTree.SubElement(entry, _tag("author")).append(
        _text("name", issue["magazine_title"])
    )
    return entry


def _entry(title: str, entry_id: str, content: str, updated: str = ""):
    entry = ElementTree.Element(_tag("entry"))
    ElementTree.SubElement(entry, _tag("id")).text = entry_id
    ElementTree.SubElement(entry, _tag("title")).text = title
    ElementTree.SubElement(entry, _tag("updated")).text = updated or _now()
    ElementTree.SubElement(entry, _tag("content")).text = content
    return entry


def _text(name: str, text: str):
    node = ElementTree.Element(_tag(name))
    node.text = text
    return node


def _tag(name: str) -> str:
    return f"{{{NS}}}{name}"


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _atom_time(value) -> str:
    # SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS".
    text = str(value or "").strip()
    return f"{text.replace(' ', 'T')}Z" if len(text) == 19 else ""


def _offset() -> int:
    try:
        return max(0, int(request.query.get("index") or 0))
    except ValueError:
        return 0


def _page_size(settings: Settings) -> int:
    return max(1, min(500, int(settings.opds_page_size or 50)))


def _paging(href: str, offset: int, size: int, has_more: bool) -> dict[str, str]:
    links = {}
    if has_more:
        links["next"] = f"{href}&index={offset + size}"
    if offset > 0:
        links["previous"] = f"{href}&index={max(0, offset - size)}"
    return links


def _id_param(name: str) -> int:
    # Clients send whatever they stored; a malformed id is a missing resource, not a crash.
    try:
        return int(request.query.get(name) or 0)
    except ValueError:
        raise HTTPError(404, f"Invalid {name}") from None


def _auth_challenge():
    raise HTTPError(
        401,
        "OPDS authentication required",
        **{"WWW-Authenticate": 'Basic realm="Magazarr OPDS"'},
    )
