"""Bounded retries: reads may be repeated, writes never."""

from unittest.mock import patch

import pytest
import requests

from mcrit_similarity.errors import McritHttpError, McritUnavailableError
from mcrit_similarity.mcrit import client as client_module
from mcrit_similarity.mcrit.client import McritClient


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def slept(instant_retry_backoff):
    return instant_retry_backoff


def _client():
    return McritClient("http://127.0.0.1:8000")


def test_a_read_retries_until_the_server_answers(slept):
    ok = {"status": "successful", "data": {"sample_id": 3}}
    responses = [FakeResponse(503), FakeResponse(502), FakeResponse(200, ok)]
    with patch("mcrit_similarity.mcrit.client.requests.request", side_effect=responses) as request:
        assert _client().get_sample_by_sha256("ab") == {"sample_id": 3}
    assert request.call_count == 3
    assert slept == [0.5, 1.0]


def test_a_read_gives_up_after_three_attempts(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request", return_value=FakeResponse(503)
        ) as request,
        pytest.raises(McritHttpError, match="HTTP 503"),
    ):
        _client().get_job_data("a" * 24)
    assert request.call_count == 3
    assert slept == [0.5, 1.0]


def test_connection_errors_are_retried(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request",
            side_effect=requests.ConnectionError("refused"),
        ) as request,
        pytest.raises(McritUnavailableError),
    ):
        _client().get_result("b" * 24)
    assert request.call_count == 3
    assert slept == [0.5, 1.0]


def test_a_read_timeout_is_not_retried(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request",
            side_effect=requests.ReadTimeout("too slow"),
        ) as request,
        pytest.raises(McritUnavailableError),
    ):
        _client().get_job_data("c" * 24)
    assert request.call_count == 1
    assert slept == []


def test_a_connect_timeout_is_not_retried(slept):
    # ConnectTimeout is a ConnectionError as well, and retrying it would multiply the wait.
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request",
            side_effect=requests.ConnectTimeout("no route"),
        ) as request,
        pytest.raises(McritUnavailableError),
    ):
        _client().get_job_data("e" * 24)
    assert request.call_count == 1
    assert slept == []


def test_a_server_error_is_not_retried(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request", return_value=FakeResponse(500)
        ) as request,
        pytest.raises(McritHttpError, match="HTTP 500"),
    ):
        _client().get_job_data("d" * 24)
    assert request.call_count == 1
    assert slept == []


def test_uploading_a_sample_is_never_retried(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request", return_value=FakeResponse(503)
        ) as request,
        pytest.raises(McritHttpError, match="HTTP 503"),
    ):
        _client().add_report({"sha256": "ab"})
    assert request.call_count == 1
    assert slept == []


def test_matching_requests_are_never_retried(slept):
    with (
        patch(
            "mcrit_similarity.mcrit.client.requests.request", return_value=FakeResponse(503)
        ) as request,
        pytest.raises(McritHttpError, match="HTTP 503"),
    ):
        _client().request_matches_for_sample_vs(1, 2)
    assert request.call_count == 1
    assert slept == []


def test_the_function_lookup_is_retried(slept):
    payload = {"status": "successful", "data": {"7": {"offset": 16}}}
    responses = [FakeResponse(504), FakeResponse(200, payload)]
    with patch("mcrit_similarity.mcrit.client.requests.request", side_effect=responses) as request:
        assert _client().get_functions_by_ids([7]) == {7: {"offset": 16}}
    assert request.call_count == 2
    assert slept == [0.5]


def test_the_backoff_is_capped_and_jittered(monkeypatch):
    monkeypatch.setattr(client_module, "_RETRY_BASE_DELAY", 3.0)
    bounds = []
    monkeypatch.setattr(client_module.random, "uniform", lambda low, high: bounds.append(high))
    for attempt in (1, 2, 3, 9):
        client_module._retry_delay(attempt)
    assert bounds == [3.0, 4.0, 4.0, 4.0]
