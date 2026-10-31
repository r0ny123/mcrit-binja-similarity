"""Tests that need a real headless Binary Ninja (MCRIT_TEST_REAL_BINARYNINJA=1). No MCRIT server."""

import threading
import weakref
from pathlib import Path
from typing import Any

import binaryninja as bn
import pytest

pytestmark = pytest.mark.binaryninja

FIXTURES = Path(__file__).parent / "fixtures"

ultimate = pytest.mark.skipif(
    "ultimate" not in (bn.core_product() or "").lower(),
    reason="Binary Similarity needs Binary Ninja Ultimate",
)


def _function(view, name):
    # Mach-O prefixes C symbols with an underscore; ELF does not, and adds a PLT stub per export.
    (symbol,) = (
        symbol
        for symbol in view.get_symbols_by_name(name) or view.get_symbols_by_name(name.lstrip("_"))
        if symbol.type == bn.SymbolType.FunctionSymbol
    )
    function = view.get_function_at(symbol.address)
    assert function is not None, name
    return function


def _instructions(block):
    view = block.view
    addr, found = block.start, []
    for _tokens, length in block:
        found.append((addr, view.read(addr, length)))
        addr += length
    return found


def test_export_produces_the_analysed_functions(views):
    from mcrit_similarity.export import export_smda_report

    for view in views:
        report: Any = export_smda_report(view)
        exported = {function.offset for function in report.getFunctions()}
        assert exported
        assert exported <= {function.start for function in view.functions}
        assert export_smda_report(view) is report


def test_plugin_logs_through_binary_ninja():
    from mcrit_similarity import log

    assert isinstance(log._bn_logger(), bn.Logger)
    log.log_info("MCRIT test log line")


@ultimate
def test_plugin_registers_the_mcrit_provider():
    provider_type = bn.SimilarityProviderType["MCRIT"]
    assert provider_type.create(provider_type.get_default_settings()) is not None


@ultimate
def test_render_paints_real_ranges_and_keeps_headers_neutral(views):
    from mcrit_similarity.export import export_smda_report
    from mcrit_similarity.render import render_match

    for view in views:
        export_smda_report(view)
    source, target = (_function(view, "_deflate") for view in views)

    painted: dict[int, list[tuple[int, int, Any]]] = {}
    # Keyed by id(): keep every renderer alive so a freed one cannot hand its id to the next.
    renderers: list[Any] = []
    original = bn.similarity.DiffRenderer.add_range_annotation

    def record(renderer, annotation):
        if id(renderer) not in painted:
            renderers.append(renderer)
        painted.setdefault(id(renderer), []).append(
            (annotation.start, annotation.end, annotation.type)
        )
        return original(renderer, annotation)

    bn.similarity.DiffRenderer.add_range_annotation = record
    try:
        context = bn.similarity.SimilarityRenderContext()
        render_match(context, source, target, None, None)
    finally:
        bn.similarity.DiffRenderer.add_range_annotation = original

    assert context.views
    assert len(painted) == 2, "the two builds of _deflate differ, so both sides are painted"
    changed = bn.enums.SimilarityAnnotationType.SimilarityAnnotationChanged
    for annotations, function in zip(painted.values(), (source, target), strict=True):
        ranges = set()
        for block in function.basic_blocks:
            ranges.add((block.start, block.end))
            ranges.update((addr, addr + len(data)) for addr, data in _instructions(block))
        for start, end, kind in annotations:
            assert (start, end) in ranges
            if start <= function.start < end:
                assert kind == changed, "the function header must not look removed or added"


def test_mid_size_functions_keep_the_instruction_level_diff(views):
    """_deflate must stay on the greedy pairing, and the largest function must still be quick."""
    import time

    from mcrit_similarity.export import export_smda_report
    from mcrit_similarity.picblocks import _annotations, annotation_blocks
    from mcrit_similarity.render import _block_hashes

    if views[0].arch is not None and "x86" not in str(views[0].arch.name):
        pytest.skip("the measured budget figures come from the x86_64 fixtures")
    reports = [export_smda_report(view) for view in views]

    def hashes(name):
        return [
            _block_hashes(report, _function(view, name).start)
            for view, report in zip(views, reports, strict=True)
        ]

    source, target = hashes("_deflate")
    assert source and target
    _annotations.cache_clear()
    painted = annotation_blocks(source, target)
    changed = sum(1 for side in painted for annotation in side if annotation.changed)
    assert changed >= 300, (
        f"_deflate ({len(source)} vs {len(target)} blocks) fell back to positional pairing"
    )

    source, target = hashes("_inflate")
    _annotations.cache_clear()
    start = time.perf_counter()
    annotation_blocks(source, target)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"aligning _inflate took {elapsed:.2f}s"


@ultimate
def test_connection_check_needs_no_open_file():
    from mcrit_similarity.commands import COMMAND_NAME

    commands = {command.name: command for command in bn.PluginCommand}
    assert commands[COMMAND_NAME].type == bn.PluginCommandType.GlobalPluginCommand


# View events cannot be unregistered, so one forwarder is registered per process and only
# forwards while a test has set a target.
_EVENT_TARGET: list = []
_EVENT_VIEWS: list = []


def _forward_view_event(view) -> None:
    if _EVENT_TARGET:
        _EVENT_VIEWS.append(weakref.ref(view))
        _EVENT_TARGET[0](view)


bn.BinaryViewType.add_binaryview_initial_analysis_completion_event(_forward_view_event)


def test_a_queued_background_preload_still_reaches_its_view(monkeypatch):
    import gc
    import importlib
    import time

    from mcrit_similarity import export
    from tests.conftest import analysed_views

    entry = importlib.import_module("__init__")
    preloaded: list[str] = []
    done = threading.Event()

    def record(view) -> None:
        preloaded.append(view.arch.name)
        done.set()

    monkeypatch.setattr(export, "_preload", record)
    _EVENT_VIEWS.clear()
    _EVENT_TARGET.append(entry._preload_in_background)
    # Hold the lock from before the file opens so this preload queues past the event callback,
    # then give the event's view wrapper a second to be freed; a preload holding it keeps it.
    export._PRELOAD_LOCK.acquire()
    try:
        with analysed_views("zlib-x86_64-Os"):
            assert _EVENT_VIEWS, "Binary Ninja fired no analysis completion event"
            settle = time.monotonic() + 1
            while _EVENT_VIEWS[0]() is not None and time.monotonic() < settle:
                gc.collect()
                time.sleep(0.05)
            export._PRELOAD_LOCK.release()
            assert done.wait(30), "the queued preload never reached its view"
    finally:
        if export._PRELOAD_LOCK.locked() and not done.is_set():
            export._PRELOAD_LOCK.release()
        _EVENT_TARGET.clear()
    assert preloaded[0] == "x86_64"
