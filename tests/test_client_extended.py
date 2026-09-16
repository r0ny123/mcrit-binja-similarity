from unittest.mock import patch

import pytest

from mcrit_similarity.errors import McritAuthError, McritHttpError
from mcrit_similarity.mcrit.client import McritClient


class FakeResponse:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_token_rejected_over_insecure_http():
    with pytest.raises(McritAuthError, match="Cannot transmit API token over unencrypted HTTP"):
        McritClient("http://mcrit.corp.example.com", apitoken="secret")

    # Loopback addresses must be allowed
    c1 = McritClient("http://127.0.0.1:8000", apitoken="secret")
    assert c1.headers["apitoken"] == "secret"

    c2 = McritClient("http://localhost:8000", apitoken="secret")
    assert c2.headers["apitoken"] == "secret"

    # HTTPS must be allowed for remote
    c3 = McritClient("https://mcrit.corp.example.com", apitoken="secret")
    assert c3.headers["apitoken"] == "secret"


def test_redirects_blocked():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(302)
        with pytest.raises(McritHttpError, match="redirect"):
            client.get_version()
        assert request.call_args.kwargs.get("allow_redirects") is False


def test_http_400_raises_detailed_error():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(400, {"message": "Invalid threshold parameter"})
        with pytest.raises(McritHttpError, match="Invalid threshold parameter"):
            client.get_version()


def test_response_size_limit_exceeded():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(
            200,
            payload={"status": "successful", "data": {}},
            headers={"Content-Length": str(100 * 1024 * 1024)},  # 100 MB
        )
        with pytest.raises(McritHttpError, match="maximum size limit"):
            client.get_version()


def test_sample_corpus_request_sends_matching_params():
    client = McritClient("http://127.0.0.1:8000")
    with patch("mcrit_similarity.mcrit.client.requests.request") as request:
        request.return_value = FakeResponse(200, {"status": "successful", "data": "job-7"})
        assert (
            client.request_matches_for_sample(
                4,
                minhash_threshold=60,
                pichash_size=10,
                band_matches_required=2,
                force_recalculation=True,
            )
            == "job-7"
        )
    args, kwargs = request.call_args
    assert args == ("GET", "http://127.0.0.1:8000/matches/sample/4")
    assert kwargs["params"] == {
        "minhash_score": 60,
        "pichash_size": 10,
        "band_matches_required": 2,
        "force_recalculation": True,
    }
