"""Tests that need a real headless Binary Ninja (MCRIT_TEST_REAL_BINARYNINJA=1). No MCRIT server."""

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


@pytest.fixture(scope="module", params=["x86_64", "arm64"])
def views(request):
    """The -O2 and -Os builds of the same zlib sources for one architecture."""
    with (
        bn.load(str(FIXTURES / f"zlib-{request.param}-O2")) as optimized,
        bn.load(str(FIXTURES / f"zlib-{request.param}-Os")) as small,
    ):
        for view in (optimized, small):
            view.update_analysis_and_wait()
        yield optimized, small


def _function(view, name):
    (symbol,) = view.get_symbols_by_name(name)
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


def test_block_decoding_matches_binary_ninja(views):
    from mcrit_similarity.backend import BinjaSmdaInterface

    for view in views:
        interface = BinjaSmdaInterface(view)
        for function in view.functions:
            for block in function.basic_blocks:
                decoded = [
                    (addr, interface.getInstructionBytes(addr))
                    for addr in interface._instruction_addresses(block)
                ]
                assert decoded == _instructions(block), hex(block.start)


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
