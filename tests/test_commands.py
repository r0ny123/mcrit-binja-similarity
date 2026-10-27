from unittest.mock import patch

from mcrit_similarity.commands import check_connection
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.errors import McritAuthError
from mcrit_similarity.mcrit.client import McritClient, is_loopback


def test_check_connection_reports_the_server_version():
    config = ProviderConfig(server="http://127.0.0.1:8000/")
    with patch.object(McritClient, "get_version", return_value="1.16.0"):
        assert check_connection(config) == (
            True,
            "Connected to http://127.0.0.1:8000/ (MCRIT 1.16.0)",
        )


def test_check_connection_reports_a_rejected_token():
    config = ProviderConfig(server="https://mcrit.example/api/", api_token="bad")
    with patch.object(McritClient, "get_version", side_effect=McritAuthError("HTTP 403")):
        assert check_connection(config) == (False, "HTTP 403")


def test_check_connection_refuses_a_token_over_plain_http():
    ok, message = check_connection(ProviderConfig(server="http://mcrit.example/", api_token="t"))
    assert not ok
    assert "unencrypted HTTP" in message


def test_loopback_hosts():
    assert is_loopback("http://127.0.0.1:8000/")
    assert is_loopback("http://[::1]:8000/")
    assert is_loopback("http://localhost/")
    assert not is_loopback("https://mcrit.malpedia.io/api/")
    assert not is_loopback("http://127.0.0.1.evil.example/")
    assert not is_loopback("http://127.0.0.1@evil.example/")
