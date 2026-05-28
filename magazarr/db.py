# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path


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
                ORDER BY i.issue_key DESC, i.acquired_at DESC, i.id DESC
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
                ORDER BY i.issue_key DESC, i.acquired_at DESC, i.id DESC
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
                ORDER BY i.issue_key ASC, i.acquired_at ASC
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

    def record_download(self, magazine_id: int, candidate, package_id: str | None):
        issue_key = self._available_issue_key(magazine_id, candidate.issue_key)
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
                    candidate.title,
                    candidate.download_url,
                    package_id,
                    candidate.size_bytes,
                ),
            )
            conn.execute(
                "UPDATE magazines SET last_search_at=CURRENT_TIMESTAMP WHERE id=?",
                (magazine_id,),
            )

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

    def record_issue(
        self,
        magazine_id: int,
        issue_key: str,
        release_title: str,
        file_path: str,
        size_bytes: int,
        package_id: str | None,
    ):
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO issues(
                    magazine_id, issue_key, release_title, file_path,
                    size_bytes, package_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    magazine_id,
                    issue_key,
                    release_title,
                    file_path,
                    size_bytes,
                    package_id,
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
