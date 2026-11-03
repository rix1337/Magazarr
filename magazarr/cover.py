# -*- coding: utf-8 -*-

import hashlib
import os
import threading
from functools import lru_cache
from pathlib import Path

from loguru import logger

COVER_MIME = "image/png"
THUMBNAIL_MIME = "image/jpeg"
THUMBNAIL_WIDTH = 480

# Covers render on demand and OPDS clients decide how many they request at once.
# Bound the CPU and memory spent on rendering regardless of how many connections arrive.
_render_slots = threading.BoundedSemaphore(2)


class CoverError(Exception):
    pass


def pdf_cover_path(pdf_path: Path) -> Path:
    return pdf_path.with_name(f".{pdf_path.stem}.cover.png")


def pdf_thumbnail_path(pdf_path: Path) -> Path:
    return pdf_path.with_name(f".{pdf_path.stem}.thumb.jpg")


def extract_pdf_cover(pdf_path: Path) -> Path:
    """Full-size first page as PNG, for notifications and OPDS image links."""
    pdf_path = pdf_path.resolve()
    return _render_first_page(
        pdf_path, pdf_cover_path(pdf_path), lambda width: 2, output="png"
    )


def extract_pdf_thumbnail(pdf_path: Path) -> Path:
    """Small JPEG of the first page, for cover grids and OPDS thumbnail links."""
    pdf_path = pdf_path.resolve()
    return _render_first_page(
        pdf_path,
        pdf_thumbnail_path(pdf_path),
        lambda width: THUMBNAIL_WIDTH / width if width else 1,
        output="jpeg",
        jpg_quality=82,
    )


def _render_first_page(pdf_path: Path, target: Path, zoom, **save_options) -> Path:
    if _is_fresh(target, pdf_path):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with _render_slots:
        # Another request may have rendered it while this one waited.
        if _is_fresh(target, pdf_path):
            return target
        try:
            import fitz

            with fitz.open(pdf_path) as doc:
                if doc.page_count < 1:
                    raise CoverError("PDF has no pages")
                page = doc.load_page(0)
                scale = zoom(page.rect.width)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                # Concurrent requests must never see a half-written image as fresh.
                partial = target.with_name(
                    f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                try:
                    pix.save(partial, **save_options)
                    os.replace(partial, target)
                finally:
                    partial.unlink(missing_ok=True)
        except CoverError:
            raise
        except Exception as exc:
            logger.warning(f"Could not extract PDF cover from {pdf_path}: {exc}")
            raise CoverError("Could not extract PDF cover") from exc
    return target


def pdf_first_page_hash(pdf_path: Path) -> str | None:
    """Return a rendered first-page hash, or None when PDF cannot be read."""
    try:
        stat = Path(pdf_path).stat()
    except OSError:
        return None
    return _first_page_hash(str(pdf_path), stat.st_mtime_ns, stat.st_size)


# Duplicate scans hash every issue; cache by path and file identity.
@lru_cache(maxsize=4096)
def _first_page_hash(pdf_path: str, mtime_ns: int, size: int) -> str | None:
    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            if doc.page_count < 1:
                return None
            pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
            return hashlib.sha256(pix.tobytes("png")).hexdigest()
    except Exception:
        return None


def _is_fresh(cover_path: Path, pdf_path: Path) -> bool:
    try:
        return (
            cover_path.exists()
            and cover_path.stat().st_mtime >= pdf_path.stat().st_mtime
        )
    except OSError:
        return False
