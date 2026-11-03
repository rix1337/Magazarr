import base64

import pytest
from bottle import HTTPError, request

from magazarr.opds import IMAGE, THUMBNAIL, issue_entry, require_opds_auth
from magazarr.settings import Settings

ATOM = "http://www.w3.org/2005/Atom"


def test_issue_entry_includes_pdf_cover_links():
    entry = issue_entry(
        {
            "id": 12,
            "magazine_title": "Magazine Title Three",
            "issue_key": "2026-05-01",
            "release_title": "Magazine Title Three May 2026",
            "file_path": "/library/Magazine Title Three/Magazine Title Three - 2026-05-01.pdf",
        }
    )

    links = entry.findall(f"{{{ATOM}}}link")
    rels = {link.attrib["rel"]: link.attrib for link in links}

    assert rels[IMAGE]["href"] == "/opds?cmd=Cover&issueid=12"
    assert rels[IMAGE]["type"] == "image/png"
    assert rels[THUMBNAIL]["href"] == "/opds?cmd=Cover&issueid=12&size=thumb"


def test_require_opds_auth_challenges_with_browser_prompt_header():
    request.bind({})

    with pytest.raises(HTTPError) as exc_info:
        require_opds_auth(Settings(opds_auth_enabled=True))

    assert exc_info.value.status_code == 401
    assert (
        exc_info.value.get_header("WWW-Authenticate") == 'Basic realm="Magazarr OPDS"'
    )


def test_require_opds_auth_accepts_valid_basic_credentials():
    credentials = base64.b64encode(b"user:pass").decode("ascii")
    request.bind({"HTTP_AUTHORIZATION": f"Basic {credentials}"})

    require_opds_auth(
        Settings(opds_auth_enabled=True, opds_username="user", opds_password="pass")
    )


def test_require_opds_auth_accepts_case_insensitive_basic_scheme():
    credentials = base64.b64encode(b"user:pass").decode("ascii")
    request.bind({"HTTP_AUTHORIZATION": f"basic {credentials}"})

    require_opds_auth(
        Settings(opds_auth_enabled=True, opds_username="user", opds_password="pass")
    )


@pytest.mark.parametrize(
    "authorization",
    [
        "Basic not-base64",
        "Basic dXNlcm5vY29sb24=",
        "Basic ",
        "Bearer token",
    ],
)
def test_require_opds_auth_rejects_invalid_authorization(authorization):
    request.bind({"HTTP_AUTHORIZATION": authorization})

    with pytest.raises(HTTPError) as exc_info:
        require_opds_auth(
            Settings(opds_auth_enabled=True, opds_username="user", opds_password="pass")
        )

    assert exc_info.value.status_code == 401
    assert (
        exc_info.value.get_header("WWW-Authenticate") == 'Basic realm="Magazarr OPDS"'
    )


def _get(app, path):
    from io import BytesIO
    from wsgiref.util import setup_testing_defaults

    path, _, query = path.partition("?")
    environ = {"PATH_INFO": path, "QUERY_STRING": query, "REQUEST_METHOD": "GET"}
    setup_testing_defaults(environ)
    environ["wsgi.input"] = BytesIO()
    result = {}

    def start_response(status, headers, exc_info=None):
        result["status"], result["headers"] = status, dict(headers)

    result["body"] = b"".join(app(environ, start_response))
    return result


def _opds_app(tmp_path, issues=0, page_size=2):
    import fitz

    from magazarr.db import Database
    from magazarr.settings import SettingsStore
    from magazarr.web import create_app

    db = Database(tmp_path / "magazarr.db")
    db.migrate()
    db.add_magazine("Magazine Title")
    magazine = db.magazines()[0]
    for idx in range(issues):
        pdf = tmp_path / f"issue-{idx}.pdf"
        doc = fitz.open()
        doc.new_page(width=300, height=400)
        doc.save(pdf)
        doc.close()
        db.record_issue(
            magazine["id"], f"2026-0{idx + 1}-01", f"Issue {idx}", str(pdf), 1, None
        )
    store = SettingsStore(tmp_path / "settings.json")
    settings = store.load()
    settings.opds_page_size = page_size
    store.save(settings)
    return create_app(store, db), magazine["id"]


def test_feeds_link_next_page_until_exhausted(tmp_path):
    app, magazine_id = _opds_app(tmp_path, issues=3)

    first = _get(app, f"/opds?cmd=Magazine&magid={magazine_id}")["body"].decode()
    last = _get(app, f"/opds?cmd=Magazine&magid={magazine_id}&index=2")["body"]

    assert (
        f'href="/opds?cmd=Magazine&amp;magid={magazine_id}&amp;index=2" rel="next"'
        in first
    )
    assert first.count("<entry>") == 2
    assert b'rel="next"' not in last
    assert last.decode().count("<entry>") == 1


@pytest.mark.parametrize(
    "query", ["cmd=Serve&issueid=abc", "cmd=Cover&issueid=1x", "cmd=Magazine&magid=%20"]
)
def test_malformed_ids_are_not_found(tmp_path, query):
    app, _ = _opds_app(tmp_path)

    assert _get(app, f"/opds?{query}")["status"].startswith("404")


def test_cover_thumbnail_is_small_cached_jpeg(tmp_path):
    app, _ = _opds_app(tmp_path, issues=1)

    result = _get(app, "/opds?cmd=Cover&issueid=1&size=thumb")

    assert result["status"].startswith("200")
    assert result["headers"]["Content-Type"] == "image/jpeg"
    assert "max-age" in result["headers"]["Cache-Control"]
    assert result["body"][:2] == b"\xff\xd8"
