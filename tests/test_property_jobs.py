"""The await_job state machine: every server story must terminate, and never in a busy loop."""

import contextlib
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mcrit_similarity.errors import McritJobError
from mcrit_similarity.mcrit import jobs
from mcrit_similarity.mcrit.jobs import await_job, job_failed, job_state, resolve_matches

PROPERTY = settings(deadline=None, max_examples=100, suppress_health_check=[HealthCheck.too_slow])

RESULT = {"matches": {"functions": []}, "info": {}}

QUEUED = {"attempts_left": 3, "locked_by": None}
RUNNING = {"attempts_left": 3, "locked_by": "worker", "started_at": "t0", "progress": 40}
FINISHED_NO_RESULT = {"attempts_left": 3, "locked_by": "worker", "finished_at": "t1"}
FINISHED = dict(FINISHED_NO_RESULT, result="r1")
FAILED = {"attempts_left": 0, "locked_by": None, "last_error": "boom"}
TERMINATED = {"attempts_left": 3, "terminated": True}
STATES = [QUEUED, RUNNING, FINISHED_NO_RESULT, FINISHED, FAILED, TERMINATED, None]


class FakeClient:
    def __init__(self, timeline, results=None, result_for_job=None):
        self.timeline = list(timeline)
        self.results = results if results is not None else {"r1": RESULT}
        self.result_for_job = result_for_job
        self.polls = 0

    def get_job_data(self, job_id):
        self.polls += 1
        index = min(self.polls - 1, len(self.timeline) - 1)
        job = self.timeline[index]
        return dict(job) if isinstance(job, dict) else job

    def get_result(self, result_id, compact=False):
        return self.results.get(result_id)

    def get_result_for_job(self, job_id, compact=False):
        return self.result_for_job


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(jobs.time, "sleep", recorded.append)
    return recorded


@given(st.lists(st.sampled_from(STATES), min_size=1, max_size=12))
@PROPERTY
def test_every_timeline_terminates(timeline):
    recorded = []
    ending = timeline[-1]
    if ending in (QUEUED, RUNNING):
        # A healthy job that never completes is polled forever by design; end the story.
        timeline = [*timeline, FINISHED]
    with (
        patch.object(jobs.time, "sleep", recorded.append),
        contextlib.suppress(McritJobError),
    ):
        assert await_job(FakeClient(timeline), "j1", max_consecutive_missing=3) == RESULT
    assert all(delay > 0 for delay in recorded), "await_job must not spin without sleeping"
    assert len(recorded) <= 4 * (len(timeline) + 3)


@given(st.lists(st.sampled_from(STATES), min_size=1, max_size=6))
@PROPERTY
def test_progress_callbacks_stay_a_fraction(timeline):
    seen = []
    with patch.object(jobs.time, "sleep", lambda _: None), contextlib.suppress(McritJobError):
        await_job(
            FakeClient([*timeline, TERMINATED]),
            "j1",
            on_progress=seen.append,
            max_consecutive_missing=2,
        )
    assert all(0.0 <= value <= 1.0 for value in seen)


def test_failed_and_terminated_raise(sleeps):
    with pytest.raises(McritJobError, match="failed: boom"):
        await_job(FakeClient([FAILED]), "j1")
    with pytest.raises(McritJobError, match="terminated"):
        await_job(FakeClient([TERMINATED]), "j1")


def test_missing_job_gives_up_after_the_limit(sleeps):
    client = FakeClient([None])
    with pytest.raises(McritJobError, match="not found"):
        await_job(client, "j1", max_consecutive_missing=4)
    assert client.polls == 4
    assert len(sleeps) == 3


def test_a_single_gap_does_not_end_the_wait(sleeps):
    client = FakeClient([RUNNING, None, RUNNING, FINISHED])
    assert await_job(client, "j1", max_consecutive_missing=3) == RESULT


def test_result_written_after_finished_at_is_waited_for(sleeps):
    # Regression: attempts_left == 0 on an already finished job was read as a failure, so the
    # documented two-step Job.complete() could be reported as a crashed job.
    late = dict(FINISHED_NO_RESULT, attempts_left=0)
    client = FakeClient([late, late, dict(FINISHED, attempts_left=0)])
    assert await_job(client, "j1", max_consecutive_missing=5) == RESULT
    assert not job_failed(late)
    assert job_state(late) == "finished"


def test_finished_without_a_result_is_bounded(sleeps):
    client = FakeClient([FINISHED_NO_RESULT])
    with pytest.raises(McritJobError, match="without a result"):
        await_job(client, "j1", max_consecutive_missing=3)
    assert client.polls == 3


def test_result_id_falling_back_to_the_job_endpoint(sleeps):
    client = FakeClient([FINISHED], results={}, result_for_job=RESULT)
    assert await_job(client, "j1") == RESULT
    missing = FakeClient([FINISHED], results={})
    with pytest.raises(McritJobError, match="was missing"):
        await_job(missing, "j1")


def test_backoff_doubles_and_is_capped(sleeps):
    client = FakeClient([RUNNING] * 12 + [FINISHED])
    await_job(client, "j1", sleep_s=0.25, max_sleep_s=2.0)
    assert sleeps[:4] == [0.25, 0.5, 1.0, 2.0]
    assert max(sleeps) == 2.0


def test_should_stop_cancels_before_the_first_poll(sleeps):
    client = FakeClient([RUNNING])
    with pytest.raises(McritJobError, match="cancelled"):
        await_job(client, "j1", should_stop=lambda: True)
    assert client.polls == 0


def test_should_stop_cancels_a_long_wait(sleeps):
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(McritJobError, match="cancelled"):
        await_job(FakeClient([RUNNING]), "j1", should_stop=stop)


@given(
    st.one_of(
        st.none(),
        st.integers(),
        st.text(),
        st.dictionaries(st.text(max_size=4), st.integers(), max_size=3),
    )
)
@PROPERTY
def test_resolve_matches_rejects_anything_that_is_not_a_job(payload):
    if isinstance(payload, (str, dict)) and payload:
        return
    with patch.object(jobs.time, "sleep", lambda _: None), pytest.raises(McritJobError):
        resolve_matches(FakeClient([None]), payload)


def test_job_id_is_read_from_every_envelope_shape(sleeps):
    client = FakeClient([FINISHED])
    for payload in (
        "j1",
        {"job_id": "j1"},
        {"jobId": "j1"},
        {"_id": "j1"},
        {"_id": {"$oid": "j1"}},
    ):
        assert resolve_matches(client, payload) == RESULT


def test_job_that_returns_a_non_report_is_rejected(sleeps):
    client = FakeClient([FINISHED], results={"r1": {"unexpected": True}})
    with pytest.raises(McritJobError, match="did not return a matching report"):
        resolve_matches(client, "j1")


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (QUEUED, "queued"),
        (RUNNING, "in_progress"),
        (FINISHED, "finished"),
        (FAILED, "failed"),
        (TERMINATED, "terminated"),
    ],
)
def test_state_names(job, expected):
    assert job_state(job) == expected
