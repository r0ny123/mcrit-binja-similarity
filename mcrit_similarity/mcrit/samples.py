"""Ensure SMDA reports exist on the server, then run MatcherVs or MatcherSample."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mcrit_similarity.errors import McritError, McritUnavailableError
from mcrit_similarity.log import log_info
from mcrit_similarity.mcrit.jobs import JobClient, StopFn, await_job, job_state, resolve_matches

ProgressFn = Callable[[float], None]


_INDEX_METHOD = "updateMinHashesForSample"


class SampleClient(JobClient, Protocol):
    mcrit_server: str

    def get_jobs(self, method: str, filter_text: str) -> list[dict[str, Any]]: ...

    def get_sample_by_sha256(self, sample_sha256: str) -> dict[str, Any] | None: ...

    def add_report(self, smda_report: Any) -> dict[str, Any]: ...

    def request_matches_for_sample_vs(
        self,
        sample_id: int,
        other_sample_id: int,
        minhash_threshold: int | None = None,
        pichash_size: int | None = None,
        band_matches_required: int | None = None,
        force_recalculation: bool = False,
    ) -> Any: ...

    def request_matches_for_sample(
        self,
        sample_id: int,
        minhash_threshold: int | None = None,
        pichash_size: int | None = None,
        band_matches_required: int | None = None,
        force_recalculation: bool = False,
    ) -> Any: ...


@dataclass(frozen=True)
class SampleRef:
    sample_id: int
    sha256: str
    existed: bool


def _sample_id(sample_info: dict[str, Any]) -> int:
    try:
        return int(sample_info["sample_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise McritUnavailableError("MCRIT sample_info is missing sample_id") from exc


def _indexes_sample(job: dict[str, Any], sample_id: int) -> bool:
    payload = job.get("payload")
    if not isinstance(payload, dict) or payload.get("method") != _INDEX_METHOD:
        return False
    try:
        params = json.loads(payload.get("params") or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(params, dict) and params.get("0") == sample_id


def pending_index_job(client: SampleClient, sample_id: int) -> str | None:
    """Id of a queued or running MinHash indexing job for exactly ``sample_id``."""
    # MCRIT filters on Job.parameters, "updateMinHashesForSample(<sample_id>)" in 1.8 and 1.9. The
    # filter is a substring match, so the closing parenthesis keeps sample 7 from matching 70.
    for job in client.get_jobs(_INDEX_METHOD, f"{_INDEX_METHOD}({sample_id})"):
        if _indexes_sample(job, sample_id) and job_state(job) in ("queued", "in_progress"):
            job_id = job.get("_id")
            if isinstance(job_id, dict):
                job_id = job_id.get("$oid")
            if job_id:
                return str(job_id)
    return None


def _await_indexing(
    client: SampleClient,
    sample_id: int,
    job_id: str,
    should_stop: StopFn | None,
    on_progress: ProgressFn | None,
) -> None:
    started = time.monotonic()
    await_job(client, job_id, should_stop=should_stop, on_progress=on_progress)
    log_info(f"Sample {sample_id} indexed in {time.monotonic() - started:.1f}s")


def _existing_sample(
    client: SampleClient,
    sample_info: dict[str, Any],
    sha256: str,
    should_stop: StopFn | None,
    on_progress: ProgressFn | None,
) -> SampleRef:
    sample_id = _sample_id(sample_info)
    job_id = pending_index_job(client, sample_id)
    if job_id:
        log_info(f"MCRIT is still indexing sample {sample_id}; waiting…")
        _await_indexing(client, sample_id, job_id, should_stop, on_progress)
    return SampleRef(sample_id=sample_id, sha256=sha256, existed=True)


def ensure_sample(
    client: SampleClient,
    smda_report: Any,
    *,
    persist: bool,
    family: str = "",
    version: str = "",
    should_stop: StopFn | None = None,
    on_progress: ProgressFn | None = None,
) -> SampleRef:
    """Return the server's sample for the report, uploading it when allowed; waits for indexing."""
    sha256 = getattr(smda_report, "sha256", None)
    if not sha256 and isinstance(smda_report, dict):
        sha256 = smda_report.get("sha256")
    if not sha256:
        raise McritUnavailableError("SMDA report has no sha256")

    existing = client.get_sample_by_sha256(sha256)
    if existing is not None:
        return _existing_sample(client, existing, sha256, should_stop, on_progress)
    if not persist:
        raise McritError(
            f"Sample {sha256[:12]}… is not on the MCRIT server; enable Persist samples "
            "in the MCRIT provider settings to upload it"
        )

    # Reports are cached and shared across sessions, so labels are applied at upload time.
    if family:
        smda_report.family = family
    if version:
        smda_report.version = version
    filename = getattr(smda_report, "filename", "") or "sample"
    count = getattr(smda_report, "num_functions", "?")
    log_info(
        f"Uploading {filename} (sha256 {sha256[:12]}…, {count} functions) "
        f"to MCRIT at {client.mcrit_server}"
    )
    added = client.add_report(smda_report)
    sample_info = added["sample_info"]
    if added.get("existed"):
        return _existing_sample(client, sample_info, sha256, should_stop, on_progress)
    sample_id = _sample_id(sample_info)
    job_id = added.get("job_id")
    if job_id:
        # MinHash must finish before matching or unhashed functions produce empty results.
        log_info(f"Uploaded as sample {sample_id}; waiting for MCRIT to index it")
        _await_indexing(client, sample_id, str(job_id), should_stop, on_progress)
    return SampleRef(sample_id=sample_id, sha256=sha256, existed=False)


def match_sample_vs(
    client: SampleClient,
    own_sample_id: int,
    other_sample_id: int,
    *,
    minhash_threshold: int | None,
    pichash_size: int | None,
    band_matches_required: int | None,
    should_stop: StopFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    payload = client.request_matches_for_sample_vs(
        own_sample_id,
        other_sample_id,
        minhash_threshold=minhash_threshold,
        pichash_size=pichash_size,
        band_matches_required=band_matches_required,
    )
    return resolve_matches(client, payload, should_stop=should_stop, on_progress=on_progress)


def match_sample_corpus(
    client: SampleClient,
    own_sample_id: int,
    *,
    minhash_threshold: int | None,
    pichash_size: int | None,
    band_matches_required: int | None,
    should_stop: StopFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    # MCRIT never invalidates a cached MatcherSample result when samples are added later.
    payload = client.request_matches_for_sample(
        own_sample_id,
        minhash_threshold=minhash_threshold,
        pichash_size=pichash_size,
        band_matches_required=band_matches_required,
        force_recalculation=True,
    )
    return resolve_matches(client, payload, should_stop=should_stop, on_progress=on_progress)
