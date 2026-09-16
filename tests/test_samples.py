import json
import logging

import pytest

from mcrit_similarity.errors import McritError, McritJobError
from mcrit_similarity.mcrit.samples import (
    ensure_sample,
    match_sample_corpus,
    match_sample_vs,
    pending_index_job,
)


class Report:
    family = None
    version = None
    filename = ""
    num_functions = 0

    def __init__(self, sha256):
        self.sha256 = sha256

    def toDict(self):
        return {"sha256": self.sha256}


def _parameters(job):
    """MCRIT's Job.parameters string, e.g. ``updateMinHashesForSample(7)``."""
    params = json.loads(job["payload"]["params"])
    return f"{job['payload']['method']}({', '.join(str(v) for v in params.values())})"


def _index_job(job_id, sample_id, state, method="updateMinHashesForSample"):
    fields = {
        "queued": {"locked_by": None, "started_at": None, "finished_at": None},
        "in_progress": {"locked_by": "w", "started_at": "t", "finished_at": None},
        "finished": {"locked_by": "w", "started_at": "t", "finished_at": "t"},
    }[state]
    return {
        "_id": {"$oid": job_id},
        "terminated": False,
        "attempts_left": 3,
        "payload": {"method": method, "params": json.dumps({"0": sample_id})},
        **fields,
    }


class FakeClient:
    mcrit_server = "http://127.0.0.1:8000/"

    def __init__(self):
        self.samples = {}
        self.added = []
        self.jobs = {}
        self.queue = []
        self.job_queries = []
        self.polled = []
        self.add_existed = False
        self.vs_payload = None
        self.match_result = None

    def get_jobs(self, method, filter_text):
        self.job_queries.append((method, filter_text))
        return [job for job in self.queue if filter_text in _parameters(job)]

    def get_sample_by_sha256(self, sample_sha256):
        return self.samples.get(sample_sha256)

    def add_report(self, smda_report):
        self.added.append(smda_report)
        if self.add_existed:
            return {"existed": True, "sample_info": {"sample_id": 7, "sha256": smda_report.sha256}}
        return {
            "sample_info": {"sample_id": 9, "sha256": smda_report.sha256},
            "job_id": "hash-job",
        }

    def get_job_data(self, job_id):
        self.polled.append(job_id)
        return self.jobs.get(job_id, {"attempts_left": 1, "result": "r1", "finished_at": "now"})

    def get_result(self, result_id, compact=False):
        return self.match_result or {"ok": True}

    def get_result_for_job(self, job_id, compact=False):
        return None

    def request_matches_for_sample_vs(
        self,
        sample_id,
        other_sample_id,
        minhash_threshold=None,
        pichash_size=None,
        band_matches_required=None,
        force_recalculation=False,
    ):
        return self.vs_payload

    def request_matches_for_sample(
        self,
        sample_id,
        minhash_threshold=None,
        pichash_size=None,
        band_matches_required=None,
        force_recalculation=False,
    ):
        self.corpus_request = (sample_id, minhash_threshold, force_recalculation)
        return self.vs_payload


def test_ensure_existing_sample_does_not_upload():
    client = FakeClient()
    client.samples["aa"] = {"sample_id": 3, "sha256": "aa"}
    ref = ensure_sample(client, Report("aa"), persist=True)
    assert ref.sample_id == 3
    assert ref.existed
    assert client.added == []
    assert client.job_queries == [("updateMinHashesForSample", "updateMinHashesForSample(3)")]
    assert client.polled == []


def test_ensure_uploads_and_waits_for_minhash():
    client = FakeClient()
    ref = ensure_sample(client, Report("bb"), persist=True)
    assert ref.sample_id == 9
    assert client.added


def test_ensure_requires_persist():
    client = FakeClient()
    with pytest.raises(McritError, match="enable Persist samples"):
        ensure_sample(client, Report("cc"), persist=False)


def test_match_vs_accepts_job_id(vs_result):
    client = FakeClient()
    client.vs_payload = "job-1"
    client.jobs["job-1"] = {"attempts_left": 1, "result": "r1", "finished_at": "now"}
    client.match_result = vs_result
    payload = match_sample_vs(
        client,
        2,
        1,
        minhash_threshold=50,
        pichash_size=10,
        band_matches_required=2,
    )
    assert payload is vs_result


def test_cancelled_minhash_job():
    client = FakeClient()
    with pytest.raises(McritJobError):
        ensure_sample(client, Report("dd"), persist=True, should_stop=lambda: True)


def test_labels_are_applied_at_upload_not_to_existing_samples():
    fresh = Report("ee")
    ensure_sample(FakeClient(), fresh, persist=True, family="emotet", version="v2")
    assert (fresh.family, fresh.version) == ("emotet", "v2")

    client = FakeClient()
    client.samples["ff"] = {"sample_id": 9, "sha256": "ff"}
    existing = Report("ff")
    ensure_sample(client, existing, persist=True, family="emotet")
    assert existing.family is None


def test_corpus_match_always_forces_recalculation(vs_result):
    client = FakeClient()
    client.vs_payload = vs_result
    payload = match_sample_corpus(
        client, 2, minhash_threshold=50, pichash_size=10, band_matches_required=2
    )
    assert payload is vs_result
    assert client.corpus_request == (2, 50, True)


@pytest.mark.parametrize("state", ["queued", "in_progress"])
def test_existing_sample_waits_for_pending_indexing(state, caplog):
    client = FakeClient()
    client.samples["aa"] = {"sample_id": 7, "sha256": "aa"}
    client.queue = [_index_job("other", 17, state), _index_job("mine", 7, state)]
    with caplog.at_level(logging.INFO):
        ref = ensure_sample(client, Report("aa"), persist=False)
    assert ref.sample_id == 7
    assert client.polled == ["mine"]
    assert "still indexing sample 7" in caplog.text
    assert "Sample 7 indexed in" in caplog.text


def test_pending_index_job_matches_exact_sample_id():
    client = FakeClient()
    client.queue = [
        _index_job("seventeen", 17, "in_progress"),
        _index_job("seventy", 70, "queued"),
        _index_job("done", 7, "finished"),
        _index_job("other-method", 7, "queued", method="updateMinHashes"),
    ]
    assert pending_index_job(client, 7) is None
    assert pending_index_job(client, 17) == "seventeen"
    assert pending_index_job(client, 70) == "seventy"


def test_upload_logs_progress_and_waits(caplog):
    client = FakeClient()
    report = Report("b" * 64)
    report.filename = "evil.dll"
    report.num_functions = 12
    with caplog.at_level(logging.INFO):
        ref = ensure_sample(client, report, persist=True)
    assert (ref.sample_id, ref.existed) == (9, False)
    assert client.polled == ["hash-job"]
    assert client.job_queries == []
    text = caplog.text
    assert (
        "Uploading evil.dll (sha256 bbbbbbbbbbbb…, 12 functions) to MCRIT at http://127.0.0.1:8000/"
        in text
    )
    assert "Uploaded as sample 9; waiting for MCRIT to index it" in text
    assert "Sample 9 indexed in" in text


def test_upload_race_with_existing_sample_checks_indexing():
    client = FakeClient()
    client.add_existed = True
    client.queue = [_index_job("mine", 7, "queued")]
    ref = ensure_sample(client, Report("cc"), persist=True)
    assert (ref.sample_id, ref.existed) == (7, True)
    assert client.polled == ["mine"]
