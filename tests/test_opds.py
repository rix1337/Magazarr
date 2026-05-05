from magazarr.opds import IMAGE, THUMBNAIL, issue_entry

ATOM = "http://www.w3.org/2005/Atom"


def test_issue_entry_includes_pdf_cover_links():
    entry = issue_entry(
        {
            "id": 12,
            "magazine_title": "Linux Format",
            "issue_key": "2026-05-01",
            "release_title": "Linux Format May 2026",
            "file_path": "/library/Linux Format/Linux Format - 2026-05-01.pdf",
        }
    )

    links = entry.findall(f"{{{ATOM}}}link")
    rels = {link.attrib["rel"]: link.attrib for link in links}

    assert rels[IMAGE]["href"] == "/opds?cmd=Cover&issueid=12"
    assert rels[IMAGE]["type"] == "image/png"
    assert rels[THUMBNAIL]["href"] == "/opds?cmd=Cover&issueid=12"
