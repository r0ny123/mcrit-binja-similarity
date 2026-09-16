"""Wait for MCRIT jobs. Matching endpoints may return a result or a job id."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from mcrit_similarity.errors import McritJobError
from mcrit_similarity.log import log_info

StopFn = Callable[[], bool]
ProgressFn = Callable[[float], None]


class JobClient(Protocol):
    def get_job_data(self, job_id: str) -> dict[str, Any] | None: ...

    def get_result(self, result_id: str, compact: bool = False) -> Any: ...

    def get_result_for_job(self, job_id: str, compact: bool = False) -> Any: ...


def _job_id_from(payload: Any) -> str | None:
    if isinstance(payload, str) and payload:
        return payload
    if not isinstance(payload, dict):
        return None
    if payload.get("matches") and payload.get("info"):
        return None
    for key in ("job_id", "jobId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raw_id = payload.get("_id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id
    if isinstance(raw_id, dict) and "$oid" in raw_id:
        return str(raw_id["$oid"])
    return None


def is_match_result(payload: Any) -> bool:
    return isinstance(payload, dict) and "matches" in payload and "info" in payload


def job_failed(job: dict[str, Any]) -> bool:
    # A spent attempt counter only means failure while the job has not finished: Job.complete()
    # writes finished_at before result, and that gap must not be read as a failure.
    return job_state(job) == "failed" and job.get("result") is None


def job_terminated(job: dict[str, Any]) -> bool:
    return bool(job.get("terminated") or job.get("is_terminated"))


def job_finished(job: dict[str, Any]) -> bool:
    if job.get("result") is not None:
        return True
    if job.get("finished_at") is not None:
        return True
    return bool(job.get("is_finished"))


def _job_progress(job: dict[str, Any]) -> float | None:
    progress = job.get("progress")
    if isinstance(progress, (int, float)):
        if progress < 0:
            return None
        if progress <= 1.0:
            return float(progress)
        return min(1.0, float(progress) / 100.0)
    return None


def job_state(job: dict[str, Any]) -> str:
    """MCRIT's own classification (libs/mongoqueue.py ``_identifyJobState``), in the same order."""
    finished = job.get("finished_at")
    terminated = job.get("terminated")
    if job.get("started_at") and job.get("locked_by") and not (finished or terminated):
        return "in_progress"
    if job.get("attempts_left") == 0 and not finished and not terminated:
        return "failed"
    if not finished and not job.get("locked_by") and not terminated:
        return "queued"
    if finished and not terminated:
        return "finished"
    return "terminated" if terminated else "unknown"


def await_job(
    client: JobClient,
    job_id: str,
    *,
    should_stop: StopFn | None = None,
    on_progress: ProgressFn | None = None,
    sleep_s: float = 0.25,
    max_sleep_s: float = 2.0,
    max_consecutive_missing: int = 20,
) -> Any:
    """Poll until the job finishes or is stopped.

    There is no timeout on a running job: MCRIT only advances ``progress`` between matching
    batches of 10000 functions, so a healthy job can look idle for its whole run. A dead worker
    is MCRIT's to reclaim, which moves the job back to queued or to failed.
    """
    delay = sleep_s
    missing_count = 0
    unwritten_results = 0
    announced_queue = False
    while True:
        if should_stop and should_stop():
            raise McritJobError("MCRIT job cancelled")
        job = client.get_job_data(job_id)
        if job is None:
            missing_count += 1
            if missing_count >= max_consecutive_missing:
                raise McritJobError(f"MCRIT job {job_id} not found on server")
            time.sleep(sleep_s)
            continue
        missing_count = 0
        if on_progress:
            mapped = _job_progress(job)
            if mapped is not None:
                on_progress(mapped)
        if job_terminated(job):
            raise McritJobError(f"MCRIT job {job_id} was terminated")
        if job_failed(job):
            error = job.get("last_error") or "failed"
            raise McritJobError(f"MCRIT job {job_id} failed: {error}")
        if job_finished(job):
            result_id = job.get("result")
            if result_id is None:
                via_job = client.get_result_for_job(job_id)
                if via_job is not None:
                    return via_job
                # MCRIT's Job.complete() sets finished_at and result in two separate updates.
                unwritten_results += 1
                if unwritten_results >= max_consecutive_missing:
                    raise McritJobError(f"MCRIT job {job_id} finished without a result")
                time.sleep(sleep_s)
                continue
            result = client.get_result(str(result_id))
            if result is None:
                result = client.get_result_for_job(job_id)
            if result is None:
                raise McritJobError(f"MCRIT job {job_id} result {result_id} was missing")
            return result
        if job_state(job) == "queued" and not announced_queue:
            announced_queue = True
            log_info(f"MCRIT job {job_id} is queued behind other jobs; waiting")
        time.sleep(delay)
        delay = min(max_sleep_s, delay * 2)


def resolve_matches(
    client: JobClient,
    payload: Any,
    *,
    should_stop: StopFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Accept a MatcherVs envelope or a job id / job document and return the envelope."""
    if is_match_result(payload):
        return payload
    job_id = _job_id_from(payload)
    if job_id is None:
        raise McritJobError("MCRIT matching response was neither a result nor a job id")
    result = await_job(client, job_id, should_stop=should_stop, on_progress=on_progress)
    if not is_match_result(result):
        raise McritJobError("MCRIT job finished but did not return a matching report")
    return result
