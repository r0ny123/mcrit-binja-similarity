"""Transport behaviour of McritClient against a fake requests layer."""

from unittest.mock import patch

import pytest
import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from mcrit_similarity.errors import McritAuthError, McritHttpError, McritUnavailableError
from mcrit_similarity.mcrit.client import _FUNCTION_BATCH, McritClient

PROPERTY = settings(deadline=None, max_examples=100)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture(autouse=True)
def _no_retry_backoff(instant_retry_backoff):
    """Some generated statuses are retried, and the real backoff would dominate the runtime."""


def client(**kwargs):
    return McritClient(kwargs.pop("server", "https://mcrit.example/"), **kwargs)


@given(
    st.sampled_from(
        ["https://h", "https://h/", "https://h/api", "https://h/api/", "https://h:8000"]
    ),
    st.sampled_from(["version", "/version", "jobs/j1/result", "functions"]),
)
@PROPERTY
def test_urls_always_hang_below_the_configured_server(server, path):
    instance = client(server=server)
    url = instance._url(path)
    assert url.startswith(instance.mcrit_server)
    assert instance.mcrit_server.endswith("/")
    assert url.endswith(path.lstrip("/"))
    assert "//" not in url[len("https://") :]


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_server_is_rejected(blank):
    with pytest.raises(McritUnavailableError):
        McritClient(blank)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, McritAuthError),
        (403, McritAuthError),
        (400, McritHttpError),
        (301, McritHttpError),
        (308, McritHttpError),
        (418, McritHttpError),
        (500, McritHttpError),
        (503, McritHttpError),
    ],
)
def test_status_codes_map_to_project_errors(status, error):
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(status, {"message": "nope"})
        with pytest.raises(error):
            client().get_version()


@pytest.mark.parametrize("status", [404, 410])
def test_absent_resources_are_a_miss_not_an_error(status):
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(status, {})
        assert client().get_sample_by_sha256("ab") is None
        assert client().get_job_data("j1") is None


@pytest.mark.parametrize(
    "exception",
    [
        requests.ConnectionError("refused"),
        requests.Timeout("slow"),
        requests.TooManyRedirects("loop"),
        requests.RequestException("other"),
    ],
)
def test_connection_failures_become_unavailable(exception):
    with (
        patch("mcrit_similarity.mcrit.client.requests.request", side_effect=exception),
        pytest.raises(McritUnavailableError, match="MCRIT request failed"),
    ):
        client().get_version()


def test_a_non_json_body_is_rejected():
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, None, text="<html>")
        with pytest.raises(McritHttpError, match="non-JSON"):
            client().get_version()


@given(st.one_of(st.none(), st.integers(), st.text(), st.lists(st.integers(), max_size=3)))
@PROPERTY
def test_unexpected_payload_shapes_do_not_leak(payload):
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": payload})
        instance = client()
        assert instance.get_version() is None or isinstance(instance.get_version(), str)
        assert instance.get_sample_by_sha256("ab") in (None, payload)
        assert instance.get_job_data("j1") in (None, payload)
        assert isinstance(instance.get_jobs("m", "f"), list)
        assert isinstance(instance.get_functions_by_ids([1]), dict)


def test_envelopes_are_unwrapped_only_when_they_carry_data():
    plain = {"matches": {}, "info": {}}
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, plain)
        assert client().get_result("r1") == plain
        request.return_value = FakeResponse(202, {"data": "job-1"})
        assert client().get_result("r1") == "job-1"
        request.return_value = FakeResponse(200, {"status": "successful", "data": None})
        assert client().get_result("r1") is None


@pytest.mark.parametrize(
    "count",
    [0, 1, _FUNCTION_BATCH - 1, _FUNCTION_BATCH, _FUNCTION_BATCH + 1, 2 * _FUNCTION_BATCH + 3],
)
def test_function_lookups_are_split_into_bounded_batches(count):
    ids = list(range(count))
    bodies = []

    def record(method, url, **kwargs):
        bodies.append(kwargs["data"])
        return FakeResponse(
            200, {"status": "successful", "data": {str(i): {"offset": i} for i in ids}}
        )

    with patch("mcrit_similarity.mcrit.client.requests.request", side_effect=record):
        mapped = client().get_functions_by_ids(ids)
    assert len(bodies) == (count + _FUNCTION_BATCH - 1) // _FUNCTION_BATCH
    assert all(len(body.split(",")) <= _FUNCTION_BATCH for body in bodies)
    assert (
        "".join(bodies) == "" or [int(part) for body in bodies for part in body.split(",")] == ids
    )
    assert set(mapped) == set(ids)


def test_function_lookup_skips_unusable_keys():
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(
            200, {"status": "successful", "data": {"7": {"offset": 1}, "oops": {}, "": None}}
        )
        assert client().get_functions_by_ids([7]) == {7: {"offset": 1}}


def test_headers_and_timeout_reach_every_request():
    instance = McritClient("https://mcrit.example", apitoken="tok", username="me", timeout=7)
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": {"version": "1"}})
        assert instance.get_version() == "1"
        kwargs = request.call_args.kwargs
        assert kwargs["headers"] == {"apitoken": "tok", "username": "me"}
        assert kwargs["timeout"] == 7
        assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("timeout", [0, -1, None, False])
def test_a_non_positive_timeout_means_no_timeout(timeout):
    instance = McritClient("https://mcrit.example", timeout=timeout)
    assert instance.timeout is None


@pytest.mark.parametrize("server", ["http://evil.example/", "http://10.0.0.5:8000/"])
def test_tokens_are_never_sent_over_plain_http_to_a_remote_host(server):
    with pytest.raises(McritAuthError, match="HTTPS"):
        McritClient(server, apitoken="tok")


@pytest.mark.parametrize(
    "server", ["http://127.0.0.1:8000/", "http://localhost:8000/", "http://[::1]:8000/"]
)
def test_loopback_keeps_working_over_http(server):
    assert McritClient(server, apitoken="tok").headers["apitoken"] == "tok"


def test_oversized_responses_are_refused_before_parsing():
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(
            200, {"status": "successful", "data": {}}, headers={"Content-Length": str(10**12)}
        )
        with pytest.raises(McritHttpError, match="maximum size"):
            client().get_version()
        request.return_value = FakeResponse(
            200,
            {"status": "successful", "data": {"version": "1"}},
            headers={"Content-Length": "not a number"},
        )
        assert client().get_version() == "1"


def test_matching_parameters_are_omitted_when_unset():
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": "job"})
        client().request_matches_for_sample_vs(1, 2)
        assert request.call_args.kwargs["params"] == {}
        client().request_matches_for_sample_vs(1, 2, 50, 10, 2, True)
        assert request.call_args.kwargs["params"] == {
            "minhash_score": 50,
            "pichash_size": 10,
            "band_matches_required": 2,
            "force_recalculation": True,
        }
        assert request.call_args.args[1].endswith("matches/sample/1/2")


def test_add_report_requires_sample_info():
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": {"job_id": "j"}})
        with pytest.raises(McritHttpError, match="sample_info"):
            client().add_report({})
