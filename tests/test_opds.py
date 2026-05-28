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
    assert rels[THUMBNAIL]["href"] == "/opds?cmd=Cover&issueid=12"


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
