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
