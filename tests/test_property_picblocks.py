"""Properties of the block alignment that drives render annotations."""

import random
import time

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mcrit_similarity import picblocks
from mcrit_similarity.picblocks import (
    BlockHash,
    InstructionHash,
    _annotations,
    annotation_blocks,
    hash_escaped_block,
)

# Escaped instruction forms: register permutations and masked branch targets, as MCRIT emits them.
INSTRUCTIONS = [
    "4889e5",
    "488b45f0",
    "c3",
    "0f8f????????",
    "0f84????????",
    "4d6336",
    "4863 36",
    "",
]

PROPERTY = settings(deadline=None, max_examples=60, suppress_health_check=[HealthCheck.too_slow])


def make_block(texts, offset, entry=False):
    instructions = []
    cursor = offset
    for text in texts:
        size = max(1, len(text) // 2)
        instructions.append(InstructionHash(offset=cursor, size=size, escaped=text))
        cursor += size
    joined = "".join(texts)
    return hash_escaped_block(
        joined.encode(), offset, max(1, cursor - offset), joined, tuple(instructions), entry
    )


@st.composite
def functions(draw, min_blocks=0, max_blocks=6):
    count = draw(st.integers(min_value=min_blocks, max_value=max_blocks))
    has_entry = draw(st.booleans()) if count else False
    blocks = []
    offset = 0x401000
    for index in range(count):
        texts = draw(st.lists(st.sampled_from(INSTRUCTIONS), min_size=0, max_size=4))
        block = make_block(texts, offset, entry=has_entry and index == 0)
        blocks.append(block)
        offset += block.size + draw(st.sampled_from([0, 1, 16]))
    return blocks


def painted(annotation):
    target = annotation.instruction or annotation.block
    return (target.offset, target.size)


def ranges_are_disjoint(annotations):
    spans = sorted(painted(annotation) for annotation in annotations)
    return all(a[0] + a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))


def within_function(annotations, blocks):
    by_offset = {block.offset: block for block in blocks}
    for annotation in annotations:
        block = by_offset.get(annotation.block.offset)
        if block is None or block != annotation.block:
            return False
        start, size = painted(annotation)
        if start < block.offset or start + size > block.offset + block.size:
            return False
    return True


def signature(annotations):
    return sorted((painted(a), a.changed) for a in annotations)


def painted_blocks(annotations):
    return {annotation.block.offset for annotation in annotations}


@given(functions(), functions())
@PROPERTY
def test_annotations_stay_inside_their_function_and_never_overlap(source, target):
    source_out, target_out = annotation_blocks(source, target)
    for annotations, blocks in ((source_out, source), (target_out, target)):
        assert within_function(annotations, blocks)
        assert ranges_are_disjoint(annotations)


@given(functions())
@PROPERTY
def test_identical_functions_are_never_painted(blocks):
    assert annotation_blocks(blocks, list(blocks)) == ([], [])


@given(
    st.lists(st.sampled_from(INSTRUCTIONS), min_size=1, max_size=4),
    st.lists(st.sampled_from(INSTRUCTIONS), min_size=1, max_size=4),
)
@PROPERTY
def test_swapping_the_sides_maps_removed_onto_added(shared, only_source):
    """Unrelated leftovers must be painted the same way whichever side they are read from.

    Blocks that a fuzzy pairing could go either way on are excluded on purpose: difflib's ratio
    and opcodes are direction-dependent, so a near-threshold block is not required to agree.
    """
    anchor = make_block(shared, 0x401000)
    removed = make_block(only_source, 0x402000)
    added = make_block(["cc" * 40], 0x403000)
    source_out, target_out = annotation_blocks([anchor, removed], [anchor, added])
    swapped_target, swapped_source = annotation_blocks([anchor, added], [anchor, removed])
    assert signature(source_out) == signature(swapped_source)
    assert signature(target_out) == signature(swapped_target)
    assert all(not annotation.changed for annotation in source_out + target_out)


@given(functions(), functions())
@PROPERTY
def test_repeated_calls_agree_with_a_cold_cache(source, target):
    first = annotation_blocks(source, target)
    assert annotation_blocks(source, target) == first
    _annotations.cache_clear()
    assert annotation_blocks(source, target) == first


@given(functions(min_blocks=1), functions(min_blocks=1))
@PROPERTY
def test_entry_blocks_are_never_painted_removed_or_added(source, target):
    source_out, target_out = annotation_blocks(source, target)
    for annotation in source_out + target_out:
        if annotation.block.entry and painted(annotation)[0] == annotation.block.offset:
            assert annotation.changed


def test_empty_side_paints_nothing():
    block = make_block(["4889e5"], 0x1000)
    assert annotation_blocks([], [block]) == ([], [])
    assert annotation_blocks([block], []) == ([], [])


def test_duplicate_digests_anchor_one_to_one():
    same = make_block(["4889e5"], 0x1000)
    twin = make_block(["4889e5"], 0x1010)
    extra = make_block(["4889e5"], 0x1020)
    source_out, target_out = annotation_blocks([same, twin], [same, twin, extra])
    assert source_out == []
    assert [a.block.offset for a in target_out] == [0x1020]
    assert not target_out[0].changed


def test_blocks_without_escaped_text_are_never_paired():
    blank_source = BlockHash(offset=0x1000, size=4, digest=1)
    blank_target = BlockHash(offset=0x2000, size=4, digest=2)
    source_out, target_out = annotation_blocks([blank_source], [blank_target])
    assert [a.changed for a in source_out] == [False]
    assert [a.changed for a in target_out] == [False]


def test_budget_fallback_matches_the_full_pairing_when_order_already_aligns(monkeypatch):
    # Each source block resembles only the target block in the same position, so the greedy
    # scoring and the positional fallback must produce the same pairs.
    source = [make_block(["4d6336", "0f8f????????"], 0x1000 + 0x100 * i) for i in range(4)]
    target = [make_block(["4863 36", "0f84????????"], 0x2000 + 0x100 * i) for i in range(4)]
    for index, block in enumerate(source):
        source[index] = hash_escaped_block(
            (block.escaped + "9" * index).encode(),
            block.offset,
            block.size,
            block.escaped + "9" * index,
            block.instructions,
        )
        target[index] = hash_escaped_block(
            (target[index].escaped + "9" * index).encode(),
            target[index].offset,
            target[index].size,
            target[index].escaped + "9" * index,
            target[index].instructions,
        )
    full = annotation_blocks(source, target)
    _annotations.cache_clear()
    monkeypatch.setattr(picblocks, "_FUZZY_WORK_BUDGET", 0)
    fallback = annotation_blocks(source, target)
    _annotations.cache_clear()
    assert [signature(side) for side in fallback] == [signature(side) for side in full]


def test_large_functions_take_the_positional_fallback(monkeypatch):
    # Regression: the cap used to count pairs, and 140x140 leftovers stay under a 20000-pair
    # budget, so two functions of realistic escaped length spent seconds inside SequenceMatcher
    # on the render path.
    def side(seed):
        rng = random.Random(seed)
        blocks = []
        offset = 0x400000
        for index in range(140):
            texts = [rng.choice(INSTRUCTIONS[:-1]) for _ in range(12)]
            # A unique tail keeps every block a leftover, which is what the fuzzy pass costs on.
            texts.append(f"{index:04x}{seed:04x}")
            block = make_block(texts, offset)
            blocks.append(block)
            offset += block.size
        return blocks

    source, target = side(3), side(5)
    scored = []
    original = picblocks._ratio
    monkeypatch.setattr(
        picblocks,
        "_ratio",
        lambda matcher, left: (scored.append(left), original(matcher, left))[1],
    )
    _annotations.cache_clear()
    start = time.perf_counter()
    annotation_blocks(source, target)
    elapsed = time.perf_counter() - start
    _annotations.cache_clear()
    # Positional pairing scores each leftover once; the greedy pass would score every pair.
    assert len(scored) <= len(source)
    assert elapsed < 5.0, f"alignment of 140x140 blocks took {elapsed:.2f}s"
