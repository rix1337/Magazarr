from pathlib import Path

from magazarr.cover import extract_pdf_cover, pdf_cover_path


def test_extract_pdf_cover_renders_first_page(tmp_path: Path):
    import fitz

    pdf_path = tmp_path / "issue.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=120, height=160)
        page.insert_text((20, 40), "Cover")
        doc.save(pdf_path)

    cover_path = extract_pdf_cover(pdf_path)

    assert cover_path == pdf_cover_path(pdf_path)
    assert cover_path.read_bytes().startswith(b"\x89PNG")


def test_extract_pdf_cover_reuses_fresh_cache(tmp_path: Path):
    import fitz

    pdf_path = tmp_path / "issue.pdf"
    with fitz.open() as doc:
        doc.new_page(width=120, height=160)
        doc.save(pdf_path)

    cover_path = extract_pdf_cover(pdf_path)
    before = cover_path.stat().st_mtime

    assert extract_pdf_cover(pdf_path) == cover_path
    assert cover_path.stat().st_mtime == before
