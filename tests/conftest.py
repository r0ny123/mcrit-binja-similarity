import base64
import importlib
import os
import signal
import subprocess
import sys
import time
import types
from contextlib import ExitStack, contextmanager
from enum import IntEnum
from pathlib import Path

import pytest

# Set to 1 to run the suite against a real headless Binary Ninja instead of the stub below.
REAL_BINARYNINJA = os.environ.get("MCRIT_TEST_REAL_BINARYNINJA") == "1"
# Set to 1 to also run the end-to-end tests, which launch a throwaway MCRIT server.
RUN_E2E = os.environ.get("MCRIT_TEST_E2E") == "1"
FIXTURES = Path(__file__).parent / "fixtures"


def _make_module(name, **attrs):
    module = types.ModuleType(name)
    for attr_name, attr_value in attrs.items():
        setattr(module, attr_name, attr_value)
    sys.modules[name] = module
    return module


if REAL_BINARYNINJA:
    # Documented headless switches, set before the import: no installed plugins or user settings.
    for _name in (
        "BN_DISABLE_USER_PLUGINS",
        "BN_DISABLE_REPOSITORY_PLUGINS",
        "BN_DISABLE_USER_SETTINGS",
    ):
        os.environ.setdefault(_name, "True")
    # CI passes the license base64-encoded. Remove it from the environment before anything else
    # can read it, and hand it to Binary Ninja in memory only.
    _license = os.environ.pop("BN_LICENSE_B64", "")
    import binaryninja

    if _license:
        binaryninja.core_set_license(base64.b64decode(_license).decode())
    del _license
    # The plugin's own load path registers the MCRIT provider type once for the whole session.
    importlib.import_module("__init__")
elif "binaryninja" not in sys.modules:

    class SimilarityEntityType(IntEnum):
        SimilarityEntityFunction = 0

    class SimilarityAnnotationType(IntEnum):
        SimilarityAnnotationAdded = 0
        SimilarityAnnotationRemoved = 1
        SimilarityAnnotationChanged = 2

    class SimilarityApplyStatus(IntEnum):
        SimilarityApplySuccess = 0
        SimilarityApplyNodeInactive = 1
        SimilarityApplyEntityNotFound = 2
        SimilarityApplyUnsupported = 3
        SimilarityApplyFailed = 4

    class SimilarityEntityInfo:
        def __init__(self, type, address, name=""):
            self.type = type
            self.address = address
            self.name = name

    class SimilarityEntityRef:
        def __init__(self, node_id, entity_id):
            self.node_id = node_id
            self.entity_id = entity_id

    class SimilarityProvider:
        def __init__(self, *args, **kwargs):
            self.id = 0

        def perform_apply(self, node, entity, result):
            return SimilarityApplyStatus.SimilarityApplySuccess

    class SimilarityProviderType:
        def register(self):
            pass

    class SimilarityProviderResults:
        pass

    class SimilaritySessionCompletion:
        pass

    class SimilaritySessionCompletionQuery:
        @classmethod
        def for_node(cls, node_id):
            return cls()

        def with_provider(self, provider_id):
            return self

    class SimilaritySessionNode:
        pass

    class DiffRenderer:
        def add_range_annotation(self, ann):
            pass

        def render(self, context, func, entity=None):
            pass

    class SimilarityRangeAnnotation:
        def __init__(self, start, end, type):
            self.start = start
            self.end = end
            self.type = type

    class Settings:
        default_handle = 1

        def __init__(self, id="default"):
            self.id = id
            self._data = {}

        def register_group(self, group, title):
            return True

        def register_setting(self, key, props):
            return True

        def contains(self, key):
            return key in self._data

        def get_string(self, key):
            return str(self._data.get(key, ""))

        def get_integer(self, key):
            return int(self._data.get(key, 0))

        def get_bool(self, key):
            return bool(self._data.get(key, False))

        def reset(self, key):
            self._data.pop(key, None)

    class SecretsProvider:
        @classmethod
        def get(cls, name):
            return None

    bn_sim = _make_module(
        "binaryninja.similarity",
        SimilarityApplyStatus=SimilarityApplyStatus,
        SimilarityEntityInfo=SimilarityEntityInfo,
        SimilarityEntityRef=SimilarityEntityRef,
        SimilarityEntityType=SimilarityEntityType,
        SimilarityProvider=SimilarityProvider,
        SimilarityProviderType=SimilarityProviderType,
        SimilarityProviderResults=SimilarityProviderResults,
        SimilaritySessionCompletion=SimilaritySessionCompletion,
        SimilaritySessionCompletionQuery=SimilaritySessionCompletionQuery,
        SimilaritySessionNode=SimilaritySessionNode,
        DiffRenderer=DiffRenderer,
        SimilarityRangeAnnotation=SimilarityRangeAnnotation,
    )

    bn_enums = _make_module(
        "binaryninja.enums",
        SimilarityApplyStatus=SimilarityApplyStatus,
        SimilarityEntityType=SimilarityEntityType,
        SimilarityAnnotationType=SimilarityAnnotationType,
        SettingsUserScope=4,
        SettingsProjectScope=8,
        SettingsResourceScope=16,
    )

    bn_log = types.ModuleType("binaryninja.log")
    setattr(bn_log, "log_error_for_exception", lambda msg: None)
    sys.modules["binaryninja.log"] = bn_log

    _make_module(
        "binaryninja",
        Settings=Settings,
        SecretsProvider=SecretsProvider,
        core_product=lambda: "Binary Ninja Ultimate",
        BinaryViewType=type(
            "BinaryViewType",
            (),
            {"add_binaryview_initial_analysis_completion_event": staticmethod(lambda cb: None)},
        ),
        log=bn_log,
        similarity=bn_sim,
        enums=bn_enums,
    )


def pytest_report_header(config):
    if REAL_BINARYNINJA:
        import binaryninja

        return f"binaryninja: {binaryninja.core_version()}"
    return "binaryninja: stub"


def pytest_configure(config):
    config.addinivalue_line("markers", "binaryninja: needs a real headless Binary Ninja")
    config.addinivalue_line("markers", "e2e: needs a launched MCRIT server as well")


def pytest_collection_modifyitems(config, items):
    skip_bn = pytest.mark.skip(
        reason="set MCRIT_TEST_REAL_BINARYNINJA=1 to use a real Binary Ninja"
    )
    skip_e2e = pytest.mark.skip(reason="set MCRIT_TEST_E2E=1 to run the end-to-end tests")
    for item in items:
        if not REAL_BINARYNINJA and "binaryninja" in item.keywords:
            item.add_marker(skip_bn)
        if not RUN_E2E and "e2e" in item.keywords:
            item.add_marker(skip_e2e)


def _log_text(log_path: Path) -> str:
    # A Windows runner defaults to cp1252, and MCRIT logs are UTF-8.
    return log_path.read_text(encoding="utf-8", errors="replace")


def _await_ready_line(process, log_path: Path, timeout: float) -> str:
    """The URL the launcher prints once waitress has bound, so no port is reserved twice."""
    prefix = "MCRIT_E2E_READY "  # READY_PREFIX in tests/e2e/mcrit_server.py
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in _log_text(log_path).splitlines():
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
        if process.poll() is not None:
            raise RuntimeError(f"MCRIT exited with {process.returncode}:\n{_log_text(log_path)}")
        time.sleep(0.25)
    raise RuntimeError(f"MCRIT never reported a port:\n{_log_text(log_path)}")


def _wait_until_serving(process, url: str, log_path: Path, timeout: float) -> None:
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MCRIT exited with {process.returncode}:\n{_log_text(log_path)}")
        try:
            if requests.get(f"{url}status", timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"MCRIT did not answer {url}status:\n{_log_text(log_path)}")


def _terminate(process) -> None:
    if process.poll() is None:
        try:
            if os.name == "nt":
                # Popen.terminate() is TerminateProcess on Windows, which runs no handler.
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait(timeout=30)


class LaunchedServer:
    """An MCRIT server, plus its worker when the backend needs one."""

    def __init__(self, url: str, processes: list) -> None:
        self.url = url
        self._processes = processes

    def stop(self) -> None:
        for process in reversed(self._processes):
            _terminate(process)


def _spawn(folder: Path, role: str, environment: dict) -> tuple:
    script = Path(__file__).parent / "e2e" / "mcrit_server.py"
    arguments = ["--role", "worker"] if role == "worker" else ["--port", "0"]
    log_path = folder / f"{role}.log"
    # CREATE_NEW_PROCESS_GROUP makes the child its own group, so CTRL_BREAK reaches only it.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(script), *arguments],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(folder),
            env=environment,
            creationflags=flags,
        )
    return process, log_path


@pytest.fixture(scope="session")
def mcrit_server_factory(tmp_path_factory):
    """Launch MCRIT servers; each returns a handle with its URL and a stop().

    The backend follows MCRIT_E2E_STORAGE: in memory (the default) one process serves and runs
    its own jobs, while "mongodb" is MCRIT's deployed shape and needs a worker process too.
    """
    started: list[LaunchedServer] = []

    def start(**environment: str) -> LaunchedServer:
        folder = tmp_path_factory.mktemp("mcrit-server")
        env = {**os.environ, **environment}
        processes = []
        if env.get("MCRIT_E2E_STORAGE", "memory") != "memory":
            # The tests empty a server through POST /respawn, so no two may share a database.
            env["MCRIT_E2E_MONGO_DB"] = (
                f"{env.get('MCRIT_E2E_MONGO_DB', 'mcrit_e2e')}_{len(started)}"
            )
            processes.append(_spawn(folder, "worker", env))
        processes.insert(0, _spawn(folder, "server", env))
        server, log_path = processes[0]
        handle = LaunchedServer("", [process for process, _log in processes])
        started.append(handle)
        handle.url = _await_ready_line(server, log_path, timeout=180)
        _wait_until_serving(server, handle.url, log_path, timeout=60)
        return handle

    try:
        yield start
    finally:
        for handle in started:
            handle.stop()


@pytest.fixture(scope="session")
def mcrit_server(mcrit_server_factory):
    external = os.environ.get("MCRIT_E2E_SERVER_URL")
    if external:
        return external if external.endswith("/") else external + "/"
    return mcrit_server_factory().url


@pytest.fixture
def provider_type():
    """A provider type Binary Ninja accepts; the real API needs the registered instance."""
    from mcrit_similarity.provider import McritProviderType

    if not REAL_BINARYNINJA:
        return McritProviderType()
    from binaryninja.similarity import SimilarityProviderType

    for registered in SimilarityProviderType._registered_types:
        if isinstance(registered, McritProviderType):
            return registered
    pytest.skip("the MCRIT provider is only registered on Binary Ninja Ultimate")


@pytest.fixture(autouse=True)
def _match_stub_environment(request, monkeypatch):
    """Keep a real Binary Ninja run equivalent to the stub where tests depend on it."""
    if not REAL_BINARYNINJA:
        return
    from mcrit_similarity import export, log

    # The stub has no user_directory, so the disk cache is off.
    monkeypatch.setattr(export, "_disk_folder", lambda: None)
    # With the real API the plugin logs through Binary Ninja, which caplog cannot see.
    if "caplog" in request.fixturenames:
        monkeypatch.setattr(log, "_bn_logger", lambda: None)


@pytest.fixture
def vs_result():
    return {
        "info": {
            "sample": {
                "sample_id": 2,
                "sha256": "b" * 64,
                "architecture": "intel",
                "base_addr": 0x400000,
                "binary_size": 1000,
                "binweight": 10,
                "bitness": 64,
                "component": "",
                "family": "",
                "family_id": 0,
                "filename": "b.bin",
                "is_library": False,
                "smda_version": "1.0",
                "statistics": {},
                "timestamp": "2026-01-01T00-00-00",
                "version": "",
            },
            "type": "matcher_vs",
        },
        "other_sample_info": {
            "sample_id": 1,
            "sha256": "a" * 64,
            "architecture": "intel",
            "base_addr": 0x400000,
            "binary_size": 1000,
            "binweight": 10,
            "bitness": 64,
            "component": "",
            "family": "",
            "family_id": 0,
            "filename": "a.bin",
            "is_library": False,
            "smda_version": "1.0",
            "statistics": {},
            "timestamp": "2026-01-01T00-00-00",
            "version": "",
        },
        "matches": {
            "aggregation": {},
            "functions": [
                {
                    "fid": 10,
                    "num_bytes": 128.0,
                    "offset": 0x401000,
                    "matches": [
                        [0, 1, 20, 100.0, 3],
                        [0, 1, 21, 80.0, 1],
                        [0, 1, 22, 40.0, 1],
                        [1, 3, 99, 90.0, 5],
                    ],
                },
                {
                    "fid": 11,
                    "num_bytes": 16.0,
                    "offset": 0x401100,
                    "matches": [[0, 1, 23, 90.0, 1]],
                },
            ],
            "samples": [],
        },
    }


@contextmanager
def analysed_views(*names: str):
    """Open the named binaries from tests/fixtures and finish their analysis."""
    import binaryninja

    with ExitStack() as stack:
        opened = [stack.enter_context(binaryninja.load(str(FIXTURES / name))) for name in names]
        for view in opened:
            view.update_analysis_and_wait()
        yield tuple(opened)


@pytest.fixture(scope="module", params=["x86_64", "arm64"])
def views(request):
    """The -O2 and -Os builds of the same zlib sources for one architecture."""
    with analysed_views(f"zlib-{request.param}-O2", f"zlib-{request.param}-Os") as opened:
        yield opened


@pytest.fixture
def instant_retry_backoff(monkeypatch):
    """Record the client's retry backoff instead of waiting it out; jitter pinned to its bound."""
    from mcrit_similarity.mcrit import client

    delays: list[float] = []
    monkeypatch.setattr(client.time, "sleep", delays.append)
    monkeypatch.setattr(client.random, "uniform", lambda low, high: high)
    return delays
