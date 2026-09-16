import pytest

from mcrit_similarity.errors import McritJobError
from mcrit_similarity.mcrit.jobs import await_job, is_match_result, resolve_matches


class FakeClient:
    def __init__(self, jobs, results):
        self.jobs = jobs
        self.results = results
        self.calls = 0

    def get_job_data(self, job_id):
        self.calls += 1
        return self.jobs[min(self.calls - 1, len(self.jobs) - 1)]

    def get_result(self, result_id, compact=False):
        return self.results[result_id]

    def get_result_for_job(self, job_id, compact=False):
        return None


def test_detects_match_envelope(vs_result):
    assert is_match_result(vs_result)
    assert not is_match_result({"job_id": "abc"})


def test_resolve_immediate_result(vs_result):
    assert resolve_matches(FakeClient([], {}), vs_result) is vs_result


def test_await_job_success(vs_result):
    client = FakeClient(
        jobs=[
            {"attempts_left": 1, "result": None, "progress": 40},
            {"attempts_left": 1, "result": "rid", "finished_at": "now", "progress": 100},
        ],
        results={"rid": vs_result},
    )
    seen = []
    result = await_job(client, "jid", on_progress=seen.append, sleep_s=0)
    assert result is vs_result
    assert seen[0] == 0.4


def test_await_job_stop():
    client = FakeClient([{"attempts_left": 1, "result": None}], {})
    with pytest.raises(McritJobError, match="cancelled"):
        await_job(client, "jid", should_stop=lambda: True, sleep_s=0)


def test_await_job_failed():
    client = FakeClient([{"attempts_left": 0, "result": None, "last_error": "boom"}], {})
    with pytest.raises(McritJobError, match="boom"):
        await_job(client, "jid", sleep_s=0)


def test_resolve_string_job_id(vs_result):
    client = FakeClient(
        jobs=[{"attempts_left": 1, "result": "rid", "finished_at": "now"}],
        results={"rid": vs_result},
    )
    assert resolve_matches(client, "abc") is vs_result
