from unittest.mock import Mock, patch

from mcrit_similarity.cache import REPORT_KEYS, REPORTS
from mcrit_similarity.picblocks import BlockHash, InstructionHash, hash_escaped_block
from mcrit_similarity.render import _range_for_block, _report_for_view, _smda_function, render_match


class FakeBasicBlock:
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end


class FakeFunction:
    def __init__(self, start: int, view, blocks: list[FakeBasicBlock]):
        self.start = start
        self.view = view
        self.basic_blocks = blocks

    def get_basic_block_at(self, addr: int):
        for b in self.basic_blocks:
            if b.start == addr:
                return b
        return None


class FakeFile:
    def __init__(self, raw=None, session_id=0):
        self.raw = raw
        self.session_id = session_id
        self.filename = "sample.bin"
        self.original_filename = "sample.bin"


class FakeView:
    def __init__(self, raw=None, session_id=0):
        self.file = FakeFile(raw, session_id)
        self.functions = []


def test_range_for_block():
    bb = FakeBasicBlock(0x1000, 0x1020)
    func = FakeFunction(0x1000, None, [bb])

    # Direct hit at start of BB
    assert _range_for_block(func, 0x1000, 10) == (0x1000, 0x1020)

    # Offset inside BB
    assert _range_for_block(func, 0x1008, 8) == (0x1000, 0x1020)

    # Offset outside any BB
    assert _range_for_block(func, 0x2000, 16) == (0x2000, 0x2010)

    # Size <= 0 clamps to 1
    assert _range_for_block(func, 0x2000, 0) == (0x2000, 0x2001)


def test_report_for_view_follows_the_open_file_not_the_wrapper():
    """Binary Ninja hands out a new wrapper per access; the open file's session id is stable."""
    assert _report_for_view(FakeView(session_id=101)) is None

    report_obj = object()
    REPORTS.set(("digest-101", 5), report_obj)
    REPORT_KEYS.set(101, ("digest-101", 5))
    assert _report_for_view(FakeView(session_id=101)) is report_obj
    assert _report_for_view(FakeView(session_id=102)) is None


def test_report_for_view_misses_after_the_report_is_evicted():
    REPORT_KEYS.set(103, ("digest-evicted", 1))
    assert _report_for_view(FakeView(session_id=103)) is None


def test_render_match_warns_once_when_report_is_not_cached():
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    with (
        patch("mcrit_similarity.render._report_for_view", return_value=None),
        patch("mcrit_similarity.render.log_warn") as warn,
        patch("binaryninja.similarity.DiffRenderer"),
    ):
        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())

    assert warn.call_count == 1
    assert "re-run the session" in warn.call_args[0][0]


def test_smda_function_thumb_alias():
    report = Mock()
    report.getFunction.side_effect = lambda addr: "fn" if addr == 0x1000 else None
    assert _smda_function(report, 0x1001) == "fn"
    assert _smda_function(report, 0x2000) is None
    assert _smda_function(None, 0x1000) is None


def test_render_match_paints_nothing_for_identical_blocks():
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    with (
        patch("mcrit_similarity.render._block_hashes") as mock_hashes,
        patch("binaryninja.similarity.DiffRenderer") as MockRenderer,
    ):
        mock_hashes.return_value = [BlockHash(0x1000, 10, 12345)]
        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())
        renderer_instance = MockRenderer.return_value
        # Identical block hashes on both sides leave nothing to paint
        renderer_instance.add_range_annotation.assert_not_called()


def test_render_match_skips_on_asymmetric_cache_miss():
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    with (
        patch("mcrit_similarity.render._report_for_view", side_effect=[None, object()]),
        patch("binaryninja.similarity.DiffRenderer") as MockRenderer,
    ):
        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())
        renderer_instance = MockRenderer.return_value
        # One side missing must never paint all-red
        renderer_instance.add_range_annotation.assert_not_called()


def test_render_match_polarity_on_partial_diff():
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    shared1 = BlockHash(0x1000, 10, 111)
    shared2 = BlockHash(0x1010, 10, 222)
    src_only = BlockHash(0x1020, 10, 333)
    tgt_only = BlockHash(0x2020, 10, 444)

    with (
        patch(
            "mcrit_similarity.render._block_hashes",
            side_effect=[[shared1, shared2, src_only], [shared1, shared2, tgt_only]],
        ),
        patch("binaryninja.similarity.DiffRenderer") as MockRenderer,
    ):
        source_renderer = Mock()
        target_renderer = Mock()
        MockRenderer.side_effect = [source_renderer, target_renderer]

        from binaryninja.enums import SimilarityAnnotationType

        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())

        # Source (left) has Removed annotation for src_only
        assert source_renderer.add_range_annotation.call_count == 1
        src_ann = source_renderer.add_range_annotation.call_args[0][0]
        assert src_ann.type == SimilarityAnnotationType.SimilarityAnnotationRemoved

        # Target (right) has Added annotation for tgt_only
        assert target_renderer.add_range_annotation.call_count == 1
        tgt_ann = target_renderer.add_range_annotation.call_args[0][0]
        assert tgt_ann.type == SimilarityAnnotationType.SimilarityAnnotationAdded


def test_render_match_paints_changed_for_corresponding_blocks():
    """Register-permuted blocks correspond but never hash equal; they must render as Changed."""
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    left = hash_escaped_block(b"left", 0x1000, 10, "4d63360f8f????????")
    right = hash_escaped_block(b"right", 0x2000, 10, "4863360f84????????")

    with (
        patch("mcrit_similarity.render._block_hashes", side_effect=[[left], [right]]),
        patch("binaryninja.similarity.DiffRenderer") as MockRenderer,
    ):
        source_renderer = Mock()
        target_renderer = Mock()
        MockRenderer.side_effect = [source_renderer, target_renderer]

        from binaryninja.enums import SimilarityAnnotationType

        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())

        changed = SimilarityAnnotationType.SimilarityAnnotationChanged
        assert source_renderer.add_range_annotation.call_args[0][0].type == changed
        assert target_renderer.add_range_annotation.call_args[0][0].type == changed


def test_render_match_paints_exact_instruction_ranges():
    src_fn = FakeFunction(0x1000, FakeView(), [FakeBasicBlock(0x1000, 0x1020)])
    tgt_fn = FakeFunction(0x2000, FakeView(), [FakeBasicBlock(0x2000, 0x2030)])

    def block_of(offset, *parts):
        instructions = tuple(InstructionHash(offset + 4 * n, 4, p) for n, p in enumerate(parts))
        text = "".join(parts)
        return hash_escaped_block(text.encode(), offset, 4 * len(parts), text, instructions)

    left = block_of(0x1000, "4889e5", "4d6336", "5d", "90", "c3")
    right = block_of(0x2000, "4889e5", "486336", "5d", "c3", "cc")

    with (
        patch("mcrit_similarity.render._block_hashes", side_effect=[[left], [right]]),
        patch("binaryninja.similarity.DiffRenderer") as MockRenderer,
    ):
        source_renderer = Mock()
        target_renderer = Mock()
        MockRenderer.side_effect = [source_renderer, target_renderer]

        from binaryninja.enums import SimilarityAnnotationType as T

        render_match(Mock(), src_fn, tgt_fn, Mock(), Mock())

    def ranges(renderer):
        return [
            (a.start, a.end, a.type)
            for a in (c[0][0] for c in renderer.add_range_annotation.call_args_list)
        ]

    changed = T.SimilarityAnnotationChanged
    assert ranges(source_renderer) == [
        (0x1004, 0x1008, changed),
        (0x100C, 0x1010, T.SimilarityAnnotationRemoved),
    ]
    assert ranges(target_renderer) == [
        (0x2004, 0x2008, changed),
        (0x2010, 0x2014, T.SimilarityAnnotationAdded),
    ]
