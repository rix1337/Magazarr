from magazarr.automation import _json_safe, _progress_message
from magazarr.search import Candidate


def test_job_result_payloads_are_json_safe():
    payload = _json_safe(
        {
            "downloads": [
                Candidate(
                    magazine_id=7,
                    magazine_title="Magazine Title",
                    title="Magazine Title 2026 05",
                    download_url="https://example.invalid/Magazine Title",
                    pub_date="Tue, 05 May 2026 10:00:00 +0000",
                    size_bytes=42,
                    issue_key="2026-05-05",
                )
            ]
        }
    )

    assert payload["downloads"][0]["title"] == "Magazine Title 2026 05"


def test_skipped_progress_message_includes_reason():
    message = _progress_message(
        {
            "event": "skipped",
            "magazine_title": "Magazine Title",
            "release_title": "Magazine Title Photo 2026",
            "reason": "blacklisted",
            "details": "Magazine Title Photo",
        }
    )

    assert (
        message
        == "Magazine Title: Magazine Title Photo 2026 - blacklisted: Magazine Title Photo"
    )


def test_manual_jobs_run_in_start_order():
    import time

    from magazarr.automation import AutomationService

    service = AutomationService(settings_store=None, db=None)
    order = []
    job_ids = []
    with service._lock:
        # Hold the lock so every job is queued before the first one runs.
        for index in range(5):
            job_id = service._new_job(f"Job {index}")
            job_ids.append(job_id)
            service._enqueue_job(
                job_id, lambda progress, index=index: order.append(index)
            )
        assert all(
            service.job_status(job_id)["status"] == "queued" for job_id in job_ids
        )
    deadline = time.time() + 5
    while (
        service.job_status(job_ids[-1])["status"] != "done" and time.time() < deadline
    ):
        time.sleep(0.01)

    assert order == [0, 1, 2, 3, 4]
    assert service.job_status(job_ids[0])["event_count"] == 3
