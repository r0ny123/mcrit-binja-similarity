import logging
from unittest.mock import Mock, patch

import pytest

from mcrit_similarity.errors import McritJobError
from mcrit_similarity.mcrit.jobs import await_job

RUNNING = {"started_at": "t", "locked_by": "w", "finished_at": None, "attempts_left": 3}
QUEUED = {"started_at": None, "locked_by": None, "finished_at": None, "attempts_left": 3}


def test_running_job_without_progress_changes_is_not_abandoned():
    """MCRIT only reports progress between 10000-function batches, so a busy job looks idle."""
    client = Mock()
    client.get_job_data.side_effect = [{**RUNNING, "progress": 0}] * 50 + [
        {**RUNNING, "finished_at": "t", "result": "r"}
    ]
    client.get_result.return_value = {"ok": True}
    with patch("mcrit_similarity.mcrit.jobs.time"):
        assert await_job(client, "j1") == {"ok": True}


def test_result_written_after_finished_at_is_awaited():
    finished_only = {**RUNNING, "finished_at": "t", "result": None}
    client = Mock()
    client.get_job_data.side_effect = [
        finished_only,
        finished_only,
        {**finished_only, "result": "r"},
    ]
    client.get_result_for_job.return_value = None
    client.get_result.return_value = {"ok": True}
    with patch("mcrit_similarity.mcrit.jobs.time"):
        assert await_job(client, "j1") == {"ok": True}


def test_finished_job_that_never_gets_a_result_fails():
    client = Mock()
    client.get_job_data.return_value = {**RUNNING, "finished_at": "t", "result": None}
    client.get_result_for_job.return_value = None
    with (
        patch("mcrit_similarity.mcrit.jobs.time"),
        pytest.raises(McritJobError, match="without a result"),
    ):
        await_job(client, "j1", max_consecutive_missing=3)
    assert client.get_job_data.call_count == 3


def test_queued_job_is_logged_once(caplog):
    client = Mock()
    client.get_job_data.side_effect = [QUEUED] * 5 + [
        {**RUNNING, "finished_at": "t", "result": "r"}
    ]
    client.get_result.return_value = {"ok": True}
    with patch("mcrit_similarity.mcrit.jobs.time"), caplog.at_level(logging.INFO):
        assert await_job(client, "j1") == {"ok": True}
    assert caplog.text.count("queued behind other jobs") == 1


def test_queued_job_can_be_cancelled():
    client = Mock()
    client.get_job_data.return_value = QUEUED
    stops = iter([False, False, True])
    with pytest.raises(McritJobError, match="cancelled"):
        await_job(client, "j1", sleep_s=0, should_stop=lambda: next(stops))


def test_poll_interval_backs_off_to_the_cap():
    client = Mock()
    client.get_job_data.side_effect = [QUEUED] * 6 + [
        {**RUNNING, "finished_at": "t", "result": "r"}
    ]
    with patch("mcrit_similarity.mcrit.jobs.time") as clock:
        clock.monotonic.return_value = 0.0
        await_job(client, "j1", sleep_s=0.25, max_sleep_s=2.0)
    assert [c.args[0] for c in clock.sleep.call_args_list] == [0.25, 0.5, 1.0, 2.0, 2.0, 2.0]


def test_await_job_consecutive_missing_raises():
    client = Mock()
    client.get_job_data.return_value = None
    with pytest.raises(McritJobError, match="not found on server"):
        await_job(client, "j1", sleep_s=0.001, max_consecutive_missing=3)
    assert client.get_job_data.call_count == 3


def test_await_job_terminated_raises():
    client = Mock()
    client.get_job_data.return_value = {"terminated": True}
    with pytest.raises(McritJobError, match="terminated"):
        await_job(client, "j1", sleep_s=0.001)
