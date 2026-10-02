# -*- coding: utf-8 -*-

import json
import re
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from magazarr.utils import clean_release_title, parse_issue_date


def _compute_sort_value(issue_key: str, release_title: str = "") -> str:
    """Compute a normalized sort value from issue_key for proper ordering.

    Date-based keys sort normally. Issue-number keys sort after all dates
    in the same year (using month 00) so they appear after date-based issues
    in DESC order.
    """
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", issue_key)
    if match:
        return issue_key
    match = re.fullmatch(r"(\d{4})-issue-(\d{4})", issue_key)
    if match:
        year, number = match.group(1), int(match.group(2))
        return f"{year}-00-{number:04d}"
    match = re.fullmatch(r"issue-(\d{4})", issue_key)
    if match:
        return f"0000-00-{int(match.group(1)):04d}"
    issue = parse_issue_date(release_title)
    if issue and issue.value:
        return issue.value.isoformat()
    return issue_key


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS magazines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    active INTEGER NOT NULL DEFAULT 1,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_search_at TEXT,
                    last_import_at TEXT
                );

                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    magazine_id INTEGER NOT NULL REFERENCES magazines(id),
                    issue_key TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    package_id TEXT,
                    sort_value TEXT,
                    UNIQUE(magazine_id, issue_key)
                );

                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    magazine_id INTEGER NOT NULL REFERENCES magazines(id),
                    issue_key TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    package_id TEXT,
                    storage TEXT,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'snatched',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    pub_date TEXT NOT NULL DEFAULT '',
                    UNIQUE(magazine_id, issue_key)
                );

                CREATE TABLE IF NOT EXISTS skipped_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    magazine_id INTEGER NOT NULL REFERENCES magazines(id),
                    issue_key TEXT NOT NULL DEFAULT '',
                    release_title TEXT NOT NULL,
                    download_url TEXT NOT NULL DEFAULT '',
                    pub_date TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'skipped',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    unskipped_at TEXT,
                    package_id TEXT,
                    UNIQUE(magazine_id, release_title, reason)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    area TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS magazine_blacklist_terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    magazine_id INTEGER NOT NULL REFERENCES magazines(id),
                    term TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(magazine_id, term COLLATE NOCASE)
                );
            """
            )
        self._migrate_clean_retry_suffixes()
        self._migrate_downloads_notifications_column()
        self._migrate_downloads_pub_date_column()
        self._migrate_sort_value()
        self._migrate_fix_unknown_paths()
        self._migrate_clamp_future_dates()

    def _migrate_clean_retry_suffixes(self):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, magazine_id, issue_key, release_title
                   FROM issues WHERE issue_key LIKE '%-retry-%'
                      OR release_title LIKE '%-retry-%'"""
            ).fetchall()
            for row in rows:
                clean_key = clean_release_title(row["issue_key"])
                clean_title = clean_release_title(row["release_title"])
                # If clean issue_key already exists for this magazine, drop the retry duplicate
                if clean_key != row["issue_key"]:
                    exists = conn.execute(
                        "SELECT id FROM issues WHERE magazine_id=? AND issue_key=? AND id!=?",
                        (row["magazine_id"], clean_key, row["id"]),
                    ).fetchone()
                    if exists:
                        conn.execute("DELETE FROM issues WHERE id=?", (row["id"],))
                        continue
                conn.execute(
                    "UPDATE issues SET issue_key=?, release_title=? WHERE id=?",
                    (clean_key, clean_title, row["id"]),
                )

    def _migrate_downloads_notifications_column(self):
        with self.connect() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(downloads)")
            }
            if "notifications" not in columns:
                conn.execute("ALTER TABLE downloads ADD COLUMN notifications TEXT")

    def _migrate_downloads_pub_date_column(self):
        with self.connect() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(downloads)")
            }
            if "pub_date" not in columns:
                conn.execute(
                    "ALTER TABLE downloads ADD COLUMN pub_date TEXT NOT NULL DEFAULT ''"
                )

    def _migrate_sort_value(self):
        with self.connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(issues)")}
            if "sort_value" in columns:
                return
            conn.execute("ALTER TABLE issues ADD COLUMN sort_value TEXT")
            rows = conn.execute(
                "SELECT id, issue_key, release_title FROM issues"
            ).fetchall()
            for row in rows:
                sort_value = _compute_sort_value(row["issue_key"], row["release_title"])
                conn.execute(
                    "UPDATE issues SET sort_value=? WHERE id=?",
                    (sort_value, row["id"]),
                )

    def _migrate_fix_unknown_paths(self):
        """Move issues from unknown-year/unknown-month to correct folders."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT id, magazine_id, issue_key, release_title, file_path, acquired_at
                   FROM issues WHERE file_path LIKE '%/unknown-year/%'"""
            ).fetchall()
            for row in rows:
                issue_key = row["issue_key"]
                release_title = row["release_title"]
                acquired_at = row["acquired_at"]
                old_path = Path(row["file_path"])
                parts = issue_key.split("-")
                year = None
                month = None
                if len(parts) >= 1 and len(parts[0]) == 4 and parts[0].isdigit():
                    year = parts[0]
                if year:
                    issue = parse_issue_date(release_title)
                    if issue and issue.value:
                        month = f"{issue.value.month:02d}"
                    else:
                        from magazarr.utils import MONTHS as _MONTHS
                        from magazarr.utils import tokens as _tokens

                        words = _tokens(release_title)
                        for idx, word in enumerate(words):
                            m = _MONTHS.get(word)
                            if m:
                                month = f"{m:02d}"
                                break
                            if word.isdigit() and 1 <= int(word) <= 12:
                                for pos in (idx + 1, idx - 1):
                                    if (
                                        0 <= pos < len(words)
                                        and words[pos].isdigit()
                                        and len(words[pos]) == 4
                                    ):
                                        month = f"{int(word):02d}"
                                        break
                                if month:
                                    break
                    # Fallback: use acquired_at month if no month found in title
                    if not month and acquired_at:
                        try:
                            acq_date = date.fromisoformat(str(acquired_at)[:10])
                            month = f"{acq_date.month:02d}"
                        except (ValueError, TypeError):
                            pass
                if year and month:
                    new_path = (
                        old_path.parent.parent.parent
                        / year
                        / month
                        / old_path.parent.name
                        / old_path.name
                    )
                    if new_path != old_path and old_path.exists():
                        try:
                            new_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(old_path), str(new_path))
                            conn.execute(
                                "UPDATE issues SET file_path=? WHERE id=?",
                                (str(new_path), row["id"]),
                            )
                            cover_path = old_path.parent / f".{old_path.stem}.cover.png"
                            if cover_path.exists():
                                new_cover = (
                                    new_path.parent / f".{new_path.stem}.cover.png"
                                )
                                shutil.move(str(cover_path), str(new_cover))
                        except (OSError, PermissionError):
                            pass

    def _migrate_clamp_future_dates(self):
        """Fix issues with future dates by clamping and deduplicating."""
        today = date.today()
        future_cutoff = today + timedelta(days=30)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, magazine_id, issue_key, release_title, file_path FROM issues"
            ).fetchall()
            for row in rows:
                issue_key = row["issue_key"]
                match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", issue_key)
                if not match:
                    continue
                try:
                    d = date(
                        int(match.group(1)), int(match.group(2)), int(match.group(3))
                    )
                except ValueError:
                    continue
                if d <= future_cutoff:
                    continue
                new_key = today.isoformat()
                existing = conn.execute(
                    "SELECT id FROM issues WHERE magazine_id=? AND issue_key=? AND id!=?",
                    (row["magazine_id"], new_key, row["id"]),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    "UPDATE issues SET issue_key=?, sort_value=? WHERE id=?",
                    (new_key, new_key, row["id"]),
                )
                old_path = Path(row["file_path"])
                new_filename = old_path.name.replace(issue_key, new_key, 1)
                new_path = old_path.with_name(new_filename)
                if old_path.exists() and not new_path.exists():
                    try:
                        shutil.move(str(old_path), str(new_path))
                        conn.execute(
                            "UPDATE issues SET file_path=? WHERE id=?",
                            (str(new_path), row["id"]),
                        )
                        sort_value = _compute_sort_value(new_key, row["release_title"])
                        conn.execute(
                            "UPDATE issues SET sort_value=? WHERE id=?",
                            (sort_value, row["id"]),
                        )
                    except (OSError, PermissionError):
                        pass

    def add_magazine(self, title: str):
        clean = " ".join(title.split())
        if not clean:
            return
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO magazines(title, active) VALUES(?, 1)",
                (clean,),
            )

    def set_magazine_active(self, magazine_id: int, active: bool):
        with self.connect() as conn:
            conn.execute(
                "UPDATE magazines SET active=? WHERE id=?",
                (1 if active else 0, magazine_id),
            )

    def delete_magazine(self, magazine_id: int):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM magazine_blacklist_terms WHERE magazine_id=?",
                (magazine_id,),
            )
            conn.execute(
                "DELETE FROM skipped_releases WHERE magazine_id=?",
                (magazine_id,),
            )
            conn.execute("DELETE FROM downloads WHERE magazine_id=?", (magazine_id,))
            conn.execute("DELETE FROM issues WHERE magazine_id=?", (magazine_id,))
            conn.execute("DELETE FROM magazines WHERE id=?", (magazine_id,))

    def magazines(self, active_only=False):
        sql = """
            SELECT m.*,
                   COUNT(i.id) AS issue_count,
                   MAX(i.acquired_at) AS last_issue_at,
                   (
                       SELECT li.id
                       FROM issues li
                       WHERE li.magazine_id = m.id
                       ORDER BY li.acquired_at DESC, li.id DESC
                       LIMIT 1
                   ) AS latest_issue_id
            FROM magazines m
            LEFT JOIN issues i ON i.magazine_id = m.id
        """
        params = ()
        if active_only:
            sql += " WHERE m.active=1"
        sql += " GROUP BY m.id ORDER BY m.title COLLATE NOCASE"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def recent_issues(self, limit=50, offset=0):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT i.*, m.title AS magazine_title
                FROM issues i
                JOIN magazines m ON m.id = i.magazine_id
                ORDER BY COALESCE(i.sort_value, i.issue_key) DESC, i.acquired_at DESC, i.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    def issue_count(self, search="", magazine_id: int | None = None) -> int:
        clauses = []
        params: list[object] = []
        if magazine_id is not None:
            clauses.append("m.id=?")
            params.append(magazine_id)
        if search:
            clauses.append(
                "(i.release_title LIKE ? OR i.issue_key LIKE ? OR m.title LIKE ?)"
            )
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM issues i
                JOIN magazines m ON m.id = i.magazine_id
                {where}
                """,
                tuple(params),
            ).fetchone()
        return int(row["count"] or 0)

    def issues(self, limit=50, offset=0, search="", magazine_id: int | None = None):
        clauses = []
        params: list[object] = []
        if magazine_id is not None:
            clauses.append("m.id=?")
            params.append(magazine_id)
        if search:
            clauses.append(
                "(i.release_title LIKE ? OR i.issue_key LIKE ? OR m.title LIKE ?)"
            )
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT i.*, m.title AS magazine_title
                FROM issues i
                JOIN magazines m ON m.id = i.magazine_id
                {where}
                ORDER BY COALESCE(i.sort_value, i.issue_key) DESC, i.acquired_at DESC, i.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()

    def issues_for_magazine(self, magazine_id: int, limit=100, offset=0):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT i.*, m.title AS magazine_title
                FROM issues i
                JOIN magazines m ON m.id = i.magazine_id
                WHERE m.id=?
                ORDER BY COALESCE(i.sort_value, i.issue_key) ASC, i.acquired_at ASC
                LIMIT ? OFFSET ?
                """,
                (magazine_id, limit, offset),
            ).fetchall()

    def magazine_by_id(self, magazine_id: int):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM magazines WHERE id=?",
                (magazine_id,),
            ).fetchone()

    def delete_issue(self, issue_id: int):
        issue = self.issue_by_id(issue_id)
        if not issue:
            return None
        with self.connect() as conn:
            conn.execute("DELETE FROM issues WHERE id=?", (issue_id,))
            conn.execute(
                """
                UPDATE downloads
                SET status='deleted', updated_at=CURRENT_TIMESTAMP
                WHERE package_id=? OR (magazine_id=? AND issue_key=?)
                """,
                (issue["package_id"], issue["magazine_id"], issue["issue_key"]),
            )
        return issue

    def magazine_by_title(self, title: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM magazines WHERE title=? COLLATE NOCASE",
                (title,),
            ).fetchone()

    def issue_by_id(self, issue_id: int):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT i.*, m.title AS magazine_title
                FROM issues i
                JOIN magazines m ON m.id = i.magazine_id
                WHERE i.id=?
                """,
                (issue_id,),
            ).fetchone()

    def has_issue_or_download(self, magazine_id: int, issue_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM issues WHERE magazine_id=? AND issue_key=?
                UNION
                SELECT 1 FROM downloads
                WHERE magazine_id=? AND issue_key=?
                  AND status IN ('snatched', 'completed', 'imported')
                LIMIT 1
                """,
                (magazine_id, issue_key, magazine_id, issue_key),
            ).fetchone()
            return row is not None

    def has_active_release_download(
        self,
        magazine_id: int,
        issue_key: str,
        release_title: str,
        download_url: str,
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM issues WHERE magazine_id=? AND issue_key=?
                UNION
                SELECT 1 FROM downloads
                WHERE magazine_id=?
                  AND status IN ('snatched', 'completed', 'imported')
                  AND (
                    issue_key=?
                    OR release_title=? COLLATE NOCASE
                    OR download_url=?
                  )
                LIMIT 1
                """,
                (
                    magazine_id,
                    issue_key,
                    magazine_id,
                    issue_key,
                    release_title,
                    download_url,
                ),
            ).fetchone()
            return row is not None

    def issue_records(self, magazine_id: int):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT issue_key, release_title, '' AS pub_date
                FROM issues
                WHERE magazine_id=?
                UNION ALL
                SELECT issue_key, release_title, '' AS pub_date
                FROM downloads
                WHERE magazine_id=?
                  AND status IN ('snatched', 'completed', 'imported')
                """,
                (magazine_id, magazine_id),
            ).fetchall()

    def record_download(
        self, magazine_id: int, candidate, package_id: str | None
    ) -> int:
        issue_key = self._available_issue_key(magazine_id, candidate.issue_key)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO downloads(
                    magazine_id, issue_key, release_title, download_url,
                    package_id, size_bytes, status, pub_date
                ) VALUES (?, ?, ?, ?, ?, ?, 'snatched', ?)
                """,
                (
                    magazine_id,
                    issue_key,
                    candidate.title,
                    candidate.download_url,
                    package_id,
                    candidate.size_bytes,
                    getattr(candidate, "pub_date", ""),
                ),
            )
            conn.execute(
                "UPDATE magazines SET last_search_at=CURRENT_TIMESTAMP WHERE id=?",
                (magazine_id,),
            )
            return cursor.lastrowid

    def record_manual_download(
        self,
        magazine_id: int,
        issue_key: str,
        release_title: str,
        download_url: str,
        size_bytes: int,
        package_id: str | None,
    ) -> str:
        issue_key = self._available_issue_key(magazine_id, issue_key)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads(
                    magazine_id, issue_key, release_title, download_url,
                    package_id, size_bytes, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'snatched')
                """,
                (
                    magazine_id,
                    issue_key,
                    release_title,
                    download_url,
                    package_id,
                    size_bytes,
                ),
            )
            conn.execute(
                "UPDATE magazines SET last_search_at=CURRENT_TIMESTAMP WHERE id=?",
                (magazine_id,),
            )
        return issue_key

    def _available_issue_key(self, magazine_id: int, issue_key: str) -> str:
        base = issue_key or "manual"
        candidate = base
        idx = 2
        with self.connect() as conn:
            while True:
                row = conn.execute(
                    """
                    SELECT 1 FROM issues WHERE magazine_id=? AND issue_key=?
                    UNION
                    SELECT 1 FROM downloads WHERE magazine_id=? AND issue_key=?
                    LIMIT 1
                    """,
                    (magazine_id, candidate, magazine_id, candidate),
                ).fetchone()
                if row is None:
                    return candidate
                candidate = f"{base}-retry-{idx}"
                idx += 1

    def snatched_downloads(self):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT d.*, m.title AS magazine_title
                FROM downloads d
                JOIN magazines m ON m.id = d.magazine_id
                WHERE d.status='snatched' AND d.package_id IS NOT NULL
                ORDER BY d.created_at
                """
            ).fetchall()

    def downloads(self, magazine_id: int | None = None):
        where = ""
        params: tuple = ()
        if magazine_id is not None:
            where = "WHERE d.magazine_id=?"
            params = (magazine_id,)
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT d.*, m.title AS magazine_title
                FROM downloads d
                JOIN magazines m ON m.id = d.magazine_id
                {where}
                ORDER BY d.updated_at DESC, d.id DESC
                """,
                params,
            ).fetchall()

    def download_count(self, magazine_id: int, statuses: tuple[str, ...]) -> int:
        placeholders = ",".join("?" for _ in statuses)
        params = (magazine_id, *statuses)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM downloads
                WHERE magazine_id=? AND status IN ({placeholders})
                """,
                params,
            ).fetchone()
        return int(row["count"] or 0)

    def update_download_storage(self, download_id: int, storage: str, status: str):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE downloads
                SET storage=?, status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (storage, status, download_id),
            )

    def update_download_status(self, download_id: int, status: str, storage: str = ""):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE downloads
                SET status=?,
                    storage=COALESCE(NULLIF(?, ''), storage),
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, storage, download_id),
            )

    def update_download_notifications(
        self, download_id: int, notifications: dict
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE downloads
                SET notifications=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(notifications), download_id),
            )

    def download_id_by_issue_key(self, magazine_id: int, issue_key: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM downloads
                WHERE magazine_id=? AND issue_key=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (magazine_id, issue_key),
            ).fetchone()
            return int(row["id"]) if row else None

    def retry_download_error(self, download_id: int, package_id: str | None):
        with self.connect() as conn:
            download = conn.execute(
                """
                SELECT * FROM downloads
                WHERE id=? AND status='download_error'
                """,
                (download_id,),
            ).fetchone()
            if not download:
                return None
            conn.execute(
                """
                UPDATE downloads
                SET status='snatched',
                    package_id=?,
                    storage='',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (package_id, download_id),
            )
            conn.execute(
                """
                UPDATE skipped_releases
                SET status='unskipped',
                    package_id=?,
                    unskipped_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE magazine_id=?
                  AND status='skipped'
                  AND (
                    release_title=? COLLATE NOCASE
                    OR download_url=?
                  )
                """,
                (
                    package_id,
                    download["magazine_id"],
                    download["release_title"],
                    download["download_url"],
                ),
            )
            return download

    def record_issue(
        self,
        magazine_id: int,
        issue_key: str,
        release_title: str,
        file_path: str,
        size_bytes: int,
        package_id: str | None,
    ):
        sort_value = _compute_sort_value(issue_key, release_title)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO issues(
                    magazine_id, issue_key, release_title, file_path,
                    size_bytes, package_id, sort_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    magazine_id,
                    issue_key,
                    release_title,
                    file_path,
                    size_bytes,
                    package_id,
                    sort_value,
                ),
            )
            conn.execute(
                """
                UPDATE downloads
                SET status='imported', updated_at=CURRENT_TIMESTAMP
                WHERE magazine_id=? AND issue_key=?
                """,
                (magazine_id, issue_key),
            )
            conn.execute(
                "UPDATE magazines SET last_import_at=CURRENT_TIMESTAMP WHERE id=?",
                (magazine_id,),
            )

    def record_skipped_release(
        self,
        magazine_id: int,
        result,
        reason: str,
        issue_key: str = "",
    ):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skipped_releases(
                    magazine_id, issue_key, release_title, download_url,
                    pub_date, size_bytes, reason, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped')
                ON CONFLICT(magazine_id, release_title, reason) DO UPDATE SET
                    issue_key=excluded.issue_key,
                    download_url=excluded.download_url,
                    pub_date=excluded.pub_date,
                    size_bytes=excluded.size_bytes,
                    status='skipped',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    magazine_id,
                    issue_key or "",
                    result.title,
                    result.download_url,
                    result.pub_date,
                    result.size_bytes,
                    reason,
                ),
            )

    def record_skipped_download(self, download, reason: str):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skipped_releases(
                    magazine_id, issue_key, release_title, download_url,
                    size_bytes, reason, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'skipped')
                ON CONFLICT(magazine_id, release_title, reason) DO UPDATE SET
                    issue_key=excluded.issue_key,
                    download_url=excluded.download_url,
                    size_bytes=excluded.size_bytes,
                    status='skipped',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    download["magazine_id"],
                    download["issue_key"],
                    download["release_title"],
                    download["download_url"],
                    download["size_bytes"],
                    reason,
                ),
            )

    def has_skipped_release(
        self, magazine_id: int, release_title: str, download_url: str
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM skipped_releases
                WHERE magazine_id=?
                  AND status='skipped'
                  AND (
                    release_title=? COLLATE NOCASE
                    OR download_url=?
                  )
                UNION
                SELECT 1 FROM downloads
                WHERE magazine_id=?
                  AND status IN ('import_error', 'download_error')
                  AND (
                    release_title=? COLLATE NOCASE
                    OR download_url=?
                  )
                LIMIT 1
                """,
                (
                    magazine_id,
                    release_title,
                    download_url,
                    magazine_id,
                    release_title,
                    download_url,
                ),
            ).fetchone()
            return row is not None

    def skipped_releases(
        self,
        limit=50,
        offset=0,
        search="",
        magazine_id: int | None = None,
    ):
        where = "WHERE s.status='skipped'"
        params: list[object] = []
        if magazine_id is not None:
            where += " AND s.magazine_id=?"
            params.append(magazine_id)
        if search:
            where += (
                " AND (s.release_title LIKE ? OR m.title LIKE ? OR s.reason LIKE ?)"
            )
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        params.extend([limit, offset])
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT s.*, m.title AS magazine_title
                FROM skipped_releases s
                JOIN magazines m ON m.id = s.magazine_id
                {where}
                ORDER BY s.updated_at DESC, s.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()

    def skipped_release_count(self, search="", magazine_id: int | None = None) -> int:
        where = "WHERE s.status='skipped'"
        params: list[object] = []
        if magazine_id is not None:
            where += " AND s.magazine_id=?"
            params.append(magazine_id)
        if search:
            where += (
                " AND (s.release_title LIKE ? OR m.title LIKE ? OR s.reason LIKE ?)"
            )
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM skipped_releases s
                JOIN magazines m ON m.id = s.magazine_id
                {where}
                """,
                tuple(params),
            ).fetchone()
        return int(row["count"] or 0)

    def skipped_release_by_id(self, skip_id: int):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT s.*, m.title AS magazine_title
                FROM skipped_releases s
                JOIN magazines m ON m.id = s.magazine_id
                WHERE s.id=?
                """,
                (skip_id,),
            ).fetchone()

    def mark_skipped_release_unskipped(self, skip_id: int, package_id: str | None):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE skipped_releases
                SET status='unskipped',
                    package_id=?,
                    unskipped_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (package_id, skip_id),
            )

    def clear_skipped_releases(self, magazine_id: int | None = None):
        with self.connect() as conn:
            if magazine_id is None:
                conn.execute("DELETE FROM skipped_releases WHERE status='skipped'")
            else:
                conn.execute(
                    "DELETE FROM skipped_releases WHERE status='skipped' AND magazine_id=?",
                    (magazine_id,),
                )

    def delete_import_errors(self, magazine_id: int | None = None):
        with self.connect() as conn:
            self._preserve_failed_downloads_as_skipped(conn, magazine_id)
            if magazine_id is None:
                conn.execute(
                    """
                    DELETE FROM downloads
                    WHERE status IN ('import_error', 'download_error')
                    """
                )
            else:
                conn.execute(
                    """
                    DELETE FROM downloads
                    WHERE magazine_id=? AND status IN ('import_error', 'download_error')
                    """,
                    (magazine_id,),
                )

    def _preserve_failed_downloads_as_skipped(self, conn, magazine_id: int | None):
        where = "status IN ('import_error', 'download_error')"
        params: tuple[object, ...] = ()
        if magazine_id is not None:
            where = f"magazine_id=? AND {where}"
            params = (magazine_id,)
        conn.execute(
            f"""
            INSERT INTO skipped_releases(
                magazine_id, issue_key, release_title, download_url,
                size_bytes, reason, status
            )
            SELECT
                magazine_id,
                issue_key,
                release_title,
                download_url,
                size_bytes,
                CASE status
                    WHEN 'download_error' THEN 'Deleted download error'
                    ELSE 'Deleted import error'
                END,
                'skipped'
            FROM downloads
            WHERE {where}
            ON CONFLICT(magazine_id, release_title, reason) DO UPDATE SET
                issue_key=excluded.issue_key,
                download_url=excluded.download_url,
                size_bytes=excluded.size_bytes,
                status='skipped',
                updated_at=CURRENT_TIMESTAMP
            """,
            params,
        )

    def import_errors(
        self,
        limit=50,
        offset=0,
        magazine_id: int | None = None,
        search: str = "",
    ):
        where = "WHERE d.status IN ('import_error', 'download_error')"
        params: list[object] = []
        if magazine_id is not None:
            where += " AND d.magazine_id=?"
            params.append(magazine_id)
        if search:
            where += " AND d.release_title LIKE ?"
            params.append(f"%{search}%")
        params.extend([limit, offset])
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT d.*, m.title AS magazine_title
                FROM downloads d
                JOIN magazines m ON m.id = d.magazine_id
                {where}
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()

    def import_error_count(self, magazine_id: int, search: str = "") -> int:
        where = "WHERE magazine_id=? AND status IN ('import_error', 'download_error')"
        params: list[object] = [magazine_id]
        if search:
            where += " AND release_title LIKE ?"
            params.append(f"%{search}%")
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM downloads
                {where}
                """,
                tuple(params),
            ).fetchone()
        return int(row["count"] or 0)

    def reset_import_error(self, download_id: int):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE downloads
                SET status='snatched', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='import_error'
                """,
                (download_id,),
            )

    def record_event(self, level: str, area: str, message: str, details: str = ""):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO events(level, area, message, details)
                VALUES (?, ?, ?, ?)
                """,
                (level, area, message, details),
            )

    def events(self, limit=50):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def blacklist_terms(self, magazine_id: int) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT term
                FROM magazine_blacklist_terms
                WHERE magazine_id=?
                ORDER BY term COLLATE NOCASE
                """,
                (magazine_id,),
            ).fetchall()
        return [str(row["term"]) for row in rows]

    def add_blacklist_term(self, magazine_id: int, term: str):
        clean = " ".join(term.split())
        if not clean:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO magazine_blacklist_terms(magazine_id, term)
                VALUES (?, ?)
                """,
                (magazine_id, clean),
            )

    def delete_blacklist_term(self, term_id: int):
        with self.connect() as conn:
            conn.execute("DELETE FROM magazine_blacklist_terms WHERE id=?", (term_id,))

    def blacklist_terms_by_magazine(self) -> dict[int, list[sqlite3.Row]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM magazine_blacklist_terms
                ORDER BY term COLLATE NOCASE
                """
            ).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["magazine_id"]), []).append(row)
        return grouped

    def find_duplicates(self, magazine_id: int) -> list[list[sqlite3.Row]]:
        """Find groups of duplicate issues for a magazine.

        Uses both issue-number matching and date-proximity detection.
        Calculates the median interval between issues to determine
        expected publication frequency, then flags issues within 60%
        of that interval as potential duplicates.
        """

        from magazarr.utils import parse_issue_date, parse_issue_number

        issues = self.issues(limit=-1, magazine_id=magazine_id)
        if len(issues) < 2:
            return []

        # Parse dates and issue numbers for all issues
        issue_data = []
        for issue in issues:
            parsed_date = parse_issue_date(issue["release_title"])
            issue_number = parse_issue_number(issue["release_title"])
            issue_data.append(
                {
                    "issue": issue,
                    "date": parsed_date.value if parsed_date else None,
                    "number": issue_number.number if issue_number else None,
                    "year": issue_number.year if issue_number else None,
                }
            )

        # Group by issue number (catches ct-style duplicates)
        number_groups: dict[str, list] = {}
        for data in issue_data:
            if data["number"] is not None:
                key = f"{data['year'] or 0}-issue-{data['number']:04d}"
                number_groups.setdefault(key, []).append(data)

        # Calculate median interval for date-based detection
        dated_issues = sorted(
            [d for d in issue_data if d["date"] is not None], key=lambda x: x["date"]
        )
        median_interval = None
        if len(dated_issues) >= 3:
            intervals = []
            for i in range(1, len(dated_issues)):
                delta = (dated_issues[i]["date"] - dated_issues[i - 1]["date"]).days
                if delta > 0:
                    intervals.append(delta)
            if intervals:
                intervals.sort()
                median_interval = intervals[len(intervals) // 2]

        # Group by date proximity (catches GameStar/Der Spiegel-style duplicates)
        proximity_groups: list[list] = []
        if median_interval and len(dated_issues) >= 2:
            threshold = max(1, int(median_interval * 0.6))
            used = set()
            for i, data1 in enumerate(dated_issues):
                if i in used:
                    continue
                group = [data1]
                used.add(i)
                for j, data2 in enumerate(dated_issues[i + 1 :], start=i + 1):
                    if j in used:
                        continue
                    delta = abs((data2["date"] - data1["date"]).days)
                    if delta <= threshold:
                        group.append(data2)
                        used.add(j)
                if len(group) > 1:
                    proximity_groups.append(group)

        # Group by PDF first-page comparison (catches same content downloaded twice)
        pdf_groups: list[list] = []
        if len(issue_data) >= 2:
            # Extract first-page hashes for all issues
            page_hashes = []
            for data in issue_data:
                file_path_str = data["issue"]["file_path"]
                try:
                    file_path = Path(file_path_str)
                    if not file_path.exists():
                        continue
                    # Extract first page and compute hash
                    import fitz

                    doc = fitz.open(str(file_path))
                    if len(doc) > 0:
                        page = doc[0]
                        pix = page.get_pixmap(
                            matrix=fitz.Matrix(0.5, 0.5)
                        )  # 50% scale for speed
                        import hashlib

                        page_hash = hashlib.md5(pix.tobytes("png")).hexdigest()
                        page_hashes.append((data, page_hash))
                    doc.close()
                except Exception:
                    pass

            # Group by page hash
            hash_groups: dict[str, list] = {}
            for data, page_hash in page_hashes:
                hash_groups.setdefault(page_hash, []).append(data)

            # Only keep groups with 2+ issues
            for group in hash_groups.values():
                if len(group) >= 2:
                    pdf_groups.append(group)

        # Combine results
        result = []
        seen_ids: set[int] = set()

        # Add number-based groups
        for group in number_groups.values():
            if len(group) >= 2:
                issues_in_group = [g["issue"] for g in group]
                ids = frozenset(i["id"] for i in issues_in_group)
                if ids not in seen_ids:
                    seen_ids.add(ids)
                    issues_in_group.sort(key=lambda i: i["size_bytes"], reverse=True)
                    result.append(issues_in_group)

        # Add proximity-based groups
        for group in proximity_groups:
            issues_in_group = [g["issue"] for g in group]
            ids = frozenset(i["id"] for i in issues_in_group)
            if ids not in seen_ids:
                seen_ids.add(ids)
                issues_in_group.sort(key=lambda i: i["size_bytes"], reverse=True)
                result.append(issues_in_group)

        # Add PDF first-page groups
        for group in pdf_groups:
            issues_in_group = [g["issue"] for g in group]
            ids = frozenset(i["id"] for i in issues_in_group)
            if ids not in seen_ids:
                seen_ids.add(ids)
                issues_in_group.sort(key=lambda i: i["size_bytes"], reverse=True)
                result.append(issues_in_group)

        return result

    def delete_duplicate_issues(self, magazine_id: int) -> int:
        """Delete duplicate issues, keeping the largest file in each group."""
        duplicates = self.find_duplicates(magazine_id)
        deleted = 0
        for group in duplicates:
            for issue in group[1:]:
                self.delete_issue(issue["id"])
                deleted += 1
        return deleted
