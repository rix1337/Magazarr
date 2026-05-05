# -*- coding: utf-8 -*-

import dataclasses
import threading
import time
from datetime import datetime, timezone

from loguru import logger

from magazarr.downloads import sync_download_errors
from magazarr.importer import import_completed
from magazarr.notifications import notify_download_started
from magazarr.quasarr_client import QuasarrClient
from magazarr.search import search_all, search_magazine


class AutomationService:
    def __init__(self, settings_store, db, tick_seconds: int = 30):
        self.settings_store = settings_store
        self.db = db
        self.tick_seconds = tick_seconds
        self._lock = threading.Lock()
        self._jobs_lock = threading.Lock()
        self._jobs = {}
        self._job_seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="magazarr-automation",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def search_all(self):
        with self._lock:
            settings = self.settings_store.load()
            return search_all(self.db, settings)

    def search_magazine(self, magazine):
        with self._lock:
            settings = self.settings_store.load()
            return search_magazine(self.db, settings, magazine)

    def start_search_magazine_job(self, magazine_id: int) -> str:
        magazine = self.db.magazine_by_id(magazine_id)
        title = magazine["title"] if magazine else "Missing magazine"
        job_id = self._new_job(f"Search {title}")
        thread = threading.Thread(
            target=self._run_search_magazine_job,
            args=(job_id, magazine_id),
            name=f"magazarr-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def job_status(self, job_id: str):
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {
                **job,
                "events": list(job["events"]),
            }

    def import_completed(self):
        with self._lock:
            settings = self.settings_store.load()
            return import_completed(self.db, settings)

    def retry_import_error(self, download_id: int):
        with self._lock:
            self.db.reset_import_error(download_id)
            settings = self.settings_store.load()
            return import_completed(self.db, settings)

    def unskip_release(self, skip_id: int):
        with self._lock:
            skipped = self.db.skipped_release_by_id(skip_id)
            if not skipped:
                return None
            settings = self.settings_store.load()
            client = QuasarrClient(settings.quasarr_url, settings.quasarr_api_key)
            package_ids = client.add_url(
                skipped["download_url"],
                settings.quasarr_download_category,
            )
            package_id = package_ids[0] if package_ids else None
            issue_key = self.db.record_manual_download(
                skipped["magazine_id"],
                skipped["issue_key"] or f"manual-{skip_id}",
                skipped["release_title"],
                skipped["download_url"],
                skipped["size_bytes"],
                package_id,
            )
            self.db.mark_skipped_release_unskipped(skip_id, package_id)
            notify_download_started(
                settings,
                skipped["magazine_title"],
                skipped["release_title"],
                package_id,
            )
            self.db.record_event(
                "info",
                "search",
                "Unskipped release sent to Quasarr",
                f"{skipped['magazine_title']} - {skipped['release_title']} ({issue_key})",
            )
            return package_id

    def _run_search_magazine_job(self, job_id: str, magazine_id: int):
        def run(progress):
            magazine = self.db.magazine_by_id(magazine_id)
            if not magazine:
                raise ValueError("Magazine not found")
            return search_magazine(self.db, self.settings_store.load(), magazine, progress)

        self._run_job(job_id, run)

    def _run_job(self, job_id: str, callback):
        self._append_job_event(job_id, "queued", "Waiting for current task")
        with self._lock:
            self._set_job_status(job_id, "running")
            self._append_job_event(job_id, "started", "Search started")
            try:
                result = callback(lambda event: self._append_progress(job_id, event))
            except Exception as exc:
                logger.exception(exc)
                self._append_job_event(job_id, "error", str(exc))
                self._set_job_status(job_id, "error")
                return
        self._append_job_event(job_id, "done", "Search complete", result=result)
        self._set_job_status(job_id, "done")

    def _new_job(self, title: str) -> str:
        with self._jobs_lock:
            self._job_seq += 1
            job_id = str(self._job_seq)
            self._jobs[job_id] = {
                "id": job_id,
                "title": title,
                "status": "queued",
                "created_at": _now(),
                "updated_at": _now(),
                "events": [],
            }
            return job_id

    def _set_job_status(self, job_id: str, status: str):
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = status
                job["updated_at"] = _now()

    def _append_progress(self, job_id: str, event):
        self._append_job_event(
            job_id,
            event.get("event", "progress"),
            _progress_message(event),
            result=event,
        )

    def _append_job_event(self, job_id: str, event: str, message: str, result=None):
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["events"].append(
                {
                    "at": _now(),
                    "event": event,
                    "message": message,
                    "result": _json_safe(result or {}),
                }
            )
            job["events"] = job["events"][-300:]
            job["updated_at"] = _now()

    def run_cycle(self):
        with self._lock:
            settings = self.settings_store.load()
            summary = search_all(self.db, settings)
            imported = self._run_import_check_locked(settings)
        logger.info(f"Automation complete: searched={summary}, imported={len(imported)}")
        return summary, imported

    def run_import_check(self):
        with self._lock:
            settings = self.settings_store.load()
            imported = self._run_import_check_locked(settings)
        logger.info(f"Import check complete: imported={len(imported)}")
        return imported

    def _run_import_check_locked(self, settings):
        sync_download_errors(self.db, settings)
        return import_completed(self.db, settings)

    def _loop(self):
        last_search_interval = self._search_interval_minutes()
        last_import_interval = self._import_interval_minutes()
        next_search = self._next_run("Search automation", last_search_interval)
        next_import = self._next_run("Import check", last_import_interval)
        while not self._stop.wait(self.tick_seconds):
            now = time.monotonic()
            search_interval = self._search_interval_minutes()
            if search_interval <= 0:
                next_search = None
            elif next_search is None or search_interval != last_search_interval:
                next_search = self._next_run("Search automation", search_interval)
            elif now >= next_search:
                try:
                    self.run_cycle()
                except Exception as exc:
                    logger.exception(f"Automation failed: {exc}")
                search_interval = self._search_interval_minutes()
                next_search = self._next_run("Search automation", search_interval)
                import_interval = self._import_interval_minutes()
                next_import = self._next_run("Import check", import_interval)
            last_search_interval = search_interval

            import_interval = self._import_interval_minutes()
            if import_interval <= 0:
                next_import = None
            elif next_import is None or import_interval != last_import_interval:
                next_import = self._next_run("Import check", import_interval)
            elif now >= next_import:
                try:
                    self.run_import_check()
                except Exception as exc:
                    logger.exception(f"Import check failed: {exc}")
                import_interval = self._import_interval_minutes()
                next_import = self._next_run("Import check", import_interval)
            last_import_interval = import_interval

    def _next_run(self, label: str, interval: int):
        if interval <= 0:
            logger.info(f"{label} disabled")
            return None
        logger.info(f"{label} interval: {interval} minute(s)")
        return time.monotonic() + interval * 60

    def _search_interval_minutes(self) -> int:
        settings = self.settings_store.load()
        return max(0, int(settings.automation_interval_minutes))

    def _import_interval_minutes(self) -> int:
        settings = self.settings_store.load()
        return max(0, int(settings.import_check_interval_minutes))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _progress_message(event) -> str:
    magazine = event.get("magazine_title", "")
    release = event.get("release_title", "")
    kind = event.get("event", "progress")
    reason = event.get("reason", "")
    details = event.get("details", "")
    if kind == "skipped" and release:
        suffix = reason
        if details:
            suffix = f"{suffix}: {details}" if suffix else details
        return f"{magazine}: {release} - {suffix or 'skipped'}"
    if release:
        return f"{magazine}: {release}"
    if kind == "page":
        return f"{magazine}: loaded {event.get('count', 0)} result(s)"
    if kind == "searching":
        return f"{magazine}: searching {event.get('query', '')}"
    if kind == "results":
        return f"{magazine}: {event.get('count', 0)} result(s)"
    return magazine or kind


def _json_safe(value):
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
