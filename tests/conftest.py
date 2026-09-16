import base64
import importlib
import os
import sys
import types
from enum import IntEnum

import pytest

# Set to 1 to run the suite against a real headless Binary Ninja instead of the stub below.
REAL_BINARYNINJA = os.environ.get("MCRIT_TEST_REAL_BINARYNINJA") == "1"


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


def pytest_collection_modifyitems(config, items):
    if REAL_BINARYNINJA:
        return
    skip = pytest.mark.skip(reason="set MCRIT_TEST_REAL_BINARYNINJA=1 to use a real Binary Ninja")
    for item in items:
        if "binaryninja" in item.keywords:
            item.add_marker(skip)


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
