# -*- coding: utf-8 -*-

from pathlib import Path

from loguru import logger

COVER_MIME = "image/png"


class CoverError(Exception):
    pass


def pdf_cover_path(pdf_path: Path) -> Path:
    return pdf_path.with_name(f".{pdf_path.stem}.cover.png")


def extract_pdf_cover(pdf_path: Path) -> Path:
    pdf_path = pdf_path.resolve()
    cover_path = pdf_cover_path(pdf_path)
    if _is_fresh(cover_path, pdf_path):
        return cover_path
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            if doc.page_count < 1:
                raise CoverError("PDF has no pages")
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(cover_path)
    except CoverError:
        raise
    except Exception as exc:
        logger.warning(f"Could not extract PDF cover from {pdf_path}: {exc}")
        raise CoverError("Could not extract PDF cover") from exc
    return cover_path


def _is_fresh(cover_path: Path, pdf_path: Path) -> bool:
    try:
        return (
            cover_path.exists()
            and cover_path.stat().st_mtime >= pdf_path.stat().st_mtime
        )
    except OSError:
        return False
