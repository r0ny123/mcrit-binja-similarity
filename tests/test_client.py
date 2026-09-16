from unittest.mock import patch

import pytest

from mcrit_similarity.errors import McritAuthError, McritHttpError, McritUnavailableError
from mcrit_similarity.mcrit.client import McritClient


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_add_report_and_sha_lookup():
    client = McritClient("http://127.0.0.1:8000", apitoken="secret", username="user")
    sample = {"sample_id": 7, "sha256": "ab"}
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": sample})
        assert client.get_sample_by_sha256("ab") == sample
        request.return_value = FakeResponse(
            202, {"status": "successful", "data": {"sample_info": sample, "job_id": "j1"}}
        )
        added = client.add_report({"sha256": "ab"})
        assert added["job_id"] == "j1"
        kwargs = request.call_args.kwargs
        assert kwargs["headers"]["apitoken"] == "secret"
        assert "secret" not in str(added)


def test_auth_error():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(403, {"status": "failed"})
        with pytest.raises(McritAuthError, match="contributor"):
            client.add_report({})


def test_server_error():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(500, {"status": "failed"})
        with pytest.raises(McritHttpError):
            client.get_version()


def test_empty_server():
    with pytest.raises(McritUnavailableError):
        McritClient("  ")


def test_functions_by_ids_posts_comma_separated_batches():
    client = McritClient("http://127.0.0.1:8000")
    bodies = []

    def fake_request(method, url, **kwargs):
        bodies.append((method, url, kwargs["data"]))
        ids = [int(x) for x in kwargs["data"].split(",")]
        return FakeResponse(
            200, {"status": "successful", "data": {str(i): {"offset": i * 16} for i in ids}}
        )

    with (
        patch("mcrit_similarity.mcrit.client._FUNCTION_BATCH", 2),
        patch("mcrit_similarity.mcrit.client.requests.request", side_effect=fake_request),
    ):
        entries = client.get_functions_by_ids([1, 2, 3])
    assert bodies == [
        ("POST", "http://127.0.0.1:8000/functions", "1,2"),
        ("POST", "http://127.0.0.1:8000/functions", "3"),
    ]
    assert entries == {1: {"offset": 16}, 2: {"offset": 32}, 3: {"offset": 48}}
