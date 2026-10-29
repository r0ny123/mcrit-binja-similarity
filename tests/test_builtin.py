"""The built-in MCRIT server and how a configuration naming it is resolved."""

import logging
import sys
import threading
import time
import types
from unittest.mock import patch

import pytest

from mcrit_similarity import builtin
from mcrit_similarity.commands import check_connection
from mcrit_similarity.config import ProviderConfig, config_from_values
from mcrit_similarity.connection import client_for, resolve
from mcrit_similarity.errors import McritUnavailableError

ENDPOINT = builtin.Endpoint(url="http://127.0.0.1:4321/", token="secret")


@pytest.fixture
def fresh(monkeypatch):
    """No built-in server started yet in this process."""
    monkeypatch.setattr(builtin, "_endpoint", None)


@pytest.mark.parametrize("typed", ["builtin", " BuiltIn ", "BUILTIN"])
def test_the_keyword_is_kept_as_typed_in_any_case(typed):
    assert config_from_values({"server": typed}).server == "builtin"


def test_a_url_is_still_normalised():
    assert config_from_values({"server": "http://h:8000"}).server == "http://h:8000/"


def test_a_server_url_resolves_to_itself():
    config = ProviderConfig(server="https://mcrit.example/", api_token="t", persist_samples=False)
    assert resolve(config) is config


def test_builtin_resolves_to_the_running_server(monkeypatch):
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": ENDPOINT)
    config = ProviderConfig(
        server="builtin", api_token="user", username="me", persist_samples=False, timeout=5
    )
    resolved = resolve(config)
    assert (resolved.server, resolved.api_token, resolved.username) == (ENDPOINT.url, "secret", "")
    assert resolved.persist_samples is True
    client = client_for(resolved)
    assert client.headers == {"apitoken": "secret"}
    assert client.timeout is None
    assert resolved.minhash_threshold == config.minhash_threshold


def test_the_server_starts_once_however_many_visits_ask(fresh, monkeypatch):
    starts = []

    def slow_start():
        starts.append(1)
        time.sleep(0.05)
        return ENDPOINT

    monkeypatch.setattr(builtin, "_start", slow_start)
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(builtin.endpoint())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert starts == [1]
    assert seen == [ENDPOINT] * 8


def test_a_missing_package_says_how_to_install_it(fresh, monkeypatch):
    monkeypatch.setitem(sys.modules, "mcrit", None)
    with pytest.raises(McritUnavailableError, match="Install python3 module"):
        builtin.endpoint()
    assert builtin._endpoint is None


def test_a_broken_dependency_is_not_blamed_on_a_shadowing_folder(fresh, monkeypatch):
    pytest.importorskip("mcrit.config")

    def missing_falcon():
        raise ModuleNotFoundError("No module named 'falcon'", name="falcon")

    monkeypatch.setattr(builtin, "_silence_progress_bars", missing_falcon)
    with pytest.raises(McritUnavailableError, match="Reinstall mcrit") as caught:
        builtin.endpoint()
    assert "falcon" in str(caught.value)
    assert "Rename" not in str(caught.value)


def test_a_server_that_cannot_bind_is_a_plugin_error(fresh, monkeypatch):
    pytest.importorskip("mcrit.config")
    import waitress

    def no_loopback(*args, **kwargs):
        raise OSError("Operation not permitted")

    monkeypatch.setattr(waitress, "create_server", no_loopback)
    with pytest.raises(McritUnavailableError, match="could not start: Operation not permitted"):
        builtin.endpoint()
    assert builtin._endpoint is None


def test_a_plugin_folder_named_mcrit_is_named_as_the_cause(fresh, monkeypatch):
    shadow = types.ModuleType("mcrit")
    shadow.__file__ = "/plugins/mcrit/__init__.py"
    for name in [name for name in sys.modules if name.startswith("mcrit.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "mcrit", shadow)
    with pytest.raises(McritUnavailableError, match="/plugins/mcrit/__init__.py"):
        builtin.endpoint()


def test_the_connection_check_starts_and_reaches_the_builtin_server(monkeypatch):
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": ENDPOINT)
    response = types.SimpleNamespace(
        status_code=200, json=lambda: {"status": "successful", "data": {"version": "1.16.0"}}
    )
    with patch("mcrit_similarity.mcrit.client._send", return_value=response) as send:
        ok, message = check_connection(ProviderConfig(server="builtin"))
    assert ok, message
    assert message == "Connected to the built-in MCRIT server (MCRIT 1.16.0)"
    assert send.call_args.kwargs["headers"] == {"apitoken": "secret"}


def test_logging_keeps_its_default_behaviour(monkeypatch):
    monkeypatch.setattr(logging.root, "handlers", [])
    monkeypatch.setattr(logging.getLogger("mcrit"), "level", logging.NOTSET)
    builtin._keep_logging_as_it_is()
    assert logging.root.handlers == [logging.lastResort]
    logging.basicConfig(level=logging.INFO)
    assert logging.root.handlers == [logging.lastResort]
    assert logging.getLogger("mcrit").level == logging.WARNING


@pytest.fixture(scope="module")
def running():
    pytest.importorskip("mcrit.config", reason="the built-in server needs the mcrit package")
    return builtin.endpoint()


def test_the_real_server_only_answers_its_token(running):
    import requests

    assert requests.get(f"{running.url}version", timeout=10).status_code == 401
    wrong = requests.get(f"{running.url}version", headers={"apitoken": "x"}, timeout=10)
    assert wrong.status_code == 401
    right = requests.get(f"{running.url}version", headers={"apitoken": running.token}, timeout=10)
    assert right.json()["data"]["version"]
    assert running.url.startswith("http://127.0.0.1:")


def test_the_real_server_spawns_no_processes_and_prints_no_progress(running):
    from mcrit.config.McritConfig import McritConfig

    assert McritConfig.MINHASH_CONFIG.MINHASH_POOL_INDEXING is False
    assert McritConfig.MINHASH_CONFIG.MINHASH_POOL_MATCHING is False
    assert McritConfig.MINHASH_CONFIG.MINHASH_MATCHING_VECTORIZED is False
    for name in ("mcrit.Worker", "mcrit.matchers.MatcherInterface"):
        assert sys.modules[name].tqdm.tqdm.keywords == {"disable": True}


def test_an_older_mcrit_is_refused(fresh, monkeypatch):
    config = pytest.importorskip("mcrit.config.McritConfig")
    monkeypatch.setattr(config.McritConfig, "VERSION", "1.15.9")
    with pytest.raises(McritUnavailableError, match="1.16 or newer, found 1.15.9"):
        builtin.endpoint()
