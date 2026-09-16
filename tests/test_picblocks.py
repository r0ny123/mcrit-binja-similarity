from dataclasses import replace

from mcrit_similarity.picblocks import (
    InstructionHash,
    _annotations,
    annotation_blocks,
    hash_escaped_block,
    hash_smda_function,
)

# Escaped forms of `movsxd r14, [r14]; jg` and `movsxd rsi, [rsi]; je` taken from two
# register-permuted variants of the same function. MCRIT masks the branch target but not
# the register, so these never hash equal despite being the same block.
LEFT_VARIANT = "4d63360f8f????????"
RIGHT_VARIANT = "4863360f84????????"
UNRELATED = "4889e5488b45f0c3"


def block(text, offset, size=8):
    return hash_escaped_block(text.encode(), offset, size, text)


def test_identical_functions_are_not_painted():
    shared = block(LEFT_VARIANT, 0x1000)
    other = block(UNRELATED, 0x1100)
    source, target = annotation_blocks([shared, other], [other, shared])
    assert source == []
    assert target == []


def test_one_sided_hashes_are_not_painted():
    source, target = annotation_blocks([], [block(UNRELATED, 0x2000)])
    assert source == []
    assert target == []


def test_identical_digests_anchor_and_are_not_painted():
    shared = block(LEFT_VARIANT, 0x1000)
    source, target = annotation_blocks([shared], [shared])
    assert source == []
    assert target == []


def test_register_permuted_blocks_paint_as_changed():
    """The real MinHash case: corresponding blocks that cannot hash equal."""
    source, target = annotation_blocks(
        [block(LEFT_VARIANT, 0x1000)], [block(RIGHT_VARIANT, 0x2000)]
    )
    assert [(a.block.offset, a.changed) for a in source] == [(0x1000, True)]
    assert [(a.block.offset, a.changed) for a in target] == [(0x2000, True)]


def test_unrelated_blocks_are_one_sided_not_changed():
    source, target = annotation_blocks([block(LEFT_VARIANT, 0x1000)], [block(UNRELATED, 0x2000)])
    assert [(a.block.offset, a.changed) for a in source] == [(0x1000, False)]
    assert [(a.block.offset, a.changed) for a in target] == [(0x2000, False)]


def test_anchors_are_kept_while_leftovers_pair_up():
    shared = block(UNRELATED, 0x1000)
    source, target = annotation_blocks(
        [shared, block(LEFT_VARIANT, 0x1010)], [shared, block(RIGHT_VARIANT, 0x2010)]
    )
    assert [(a.block.offset, a.changed) for a in source] == [(0x1010, True)]
    assert [(a.block.offset, a.changed) for a in target] == [(0x2010, True)]


def test_each_block_pairs_at_most_once():
    source, target = annotation_blocks(
        [block(LEFT_VARIANT, 0x1000), block(LEFT_VARIANT + "90", 0x1010)],
        [block(RIGHT_VARIANT, 0x2000)],
    )
    assert sum(a.changed for a in source) == 1
    assert sum(a.changed for a in target) == 1


def ins(text, offset, size=4):
    return InstructionHash(offset, size, text)


def block_of(offset, *instructions):
    text = "".join(i.escaped for i in instructions)
    size = sum(i.size for i in instructions)
    return hash_escaped_block(text.encode(), offset, size, text, tuple(instructions))


def painted(annotations):
    return [(a.instruction.offset, a.changed) for a in annotations]


def diff(left, right):
    return annotation_blocks([left], [right])


def test_equal_instructions_are_not_painted_and_replace_is_changed():
    source, target = diff(
        block_of(0x1000, ins("4889e5", 0x1000), ins("4d6336", 0x1004), ins("c3", 0x1008)),
        block_of(0x2000, ins("4889e5", 0x2000), ins("486336", 0x2004), ins("c3", 0x2008)),
    )
    assert painted(source) == [(0x1004, True)]
    assert painted(target) == [(0x2004, True)]


def test_deleted_instruction_is_removed_on_source_only():
    source, target = diff(
        block_of(0x1000, ins("4889e5", 0x1000), ins("90", 0x1004), ins("c3", 0x1008)),
        block_of(0x2000, ins("4889e5", 0x2000), ins("c3", 0x2004)),
    )
    assert painted(source) == [(0x1004, False)]
    assert target == []


def test_inserted_instruction_is_added_on_target_only():
    source, target = diff(
        block_of(0x1000, ins("4889e5", 0x1000), ins("c3", 0x1004)),
        block_of(0x2000, ins("4889e5", 0x2000), ins("90", 0x2004), ins("c3", 0x2008)),
    )
    assert source == []
    assert painted(target) == [(0x2004, False)]


def test_replace_is_changed_even_when_mnemonics_differ():
    source, target = diff(
        block_of(0x1000, ins("4889e5", 0x1000), ins("4d89c0", 0x1004)),
        block_of(0x2000, ins("4889e5", 0x2000), ins("4d87c9", 0x2004), ins("90", 0x2008)),
    )
    assert painted(source) == [(0x1004, True)]
    assert painted(target) == [(0x2004, True), (0x2008, True)]


def test_paired_blocks_without_instructions_paint_whole_blocks():
    source, target = diff(
        block_of(0x1000, ins("4d6336", 0x1000), ins("0f8f????????", 0x1004)),
        block(RIGHT_VARIANT, 0x2000),
    )
    assert [(a.block.offset, a.changed, a.instruction) for a in source] == [(0x1000, True, None)]
    assert [(a.block.offset, a.changed, a.instruction) for a in target] == [(0x2000, True, None)]


def test_unpaired_blocks_stay_whole_even_with_instructions():
    source, target = diff(
        block_of(0x1000, ins(LEFT_VARIANT, 0x1000)), block_of(0x2000, ins(UNRELATED, 0x2000))
    )
    assert [(a.changed, a.instruction) for a in source] == [(False, None)]
    assert [(a.changed, a.instruction) for a in target] == [(False, None)]


class FakeInstruction:
    def __init__(self, offset, hexbytes):
        self.offset = offset
        self.bytes = hexbytes

    def getEscapedBinary(self, escaper, **kwargs):
        return self.bytes


class FakeBlock:
    def __init__(self, offset, instructions):
        self.offset = offset
        self._instructions = instructions

    def getInstructions(self):
        return self._instructions


class FakeSmdaFunction:
    def __init__(self, blocks, offset=0x1000):
        self._blocks = blocks
        self.offset = offset

    def getBlocks(self):
        return self._blocks


def test_hash_smda_function_carries_instructions_with_unchanged_digest():
    fn = FakeSmdaFunction(
        [FakeBlock(0x1000, [FakeInstruction(0x1000, "4889e5"), FakeInstruction(0x1003, "c3")])]
    )
    (hashed,) = hash_smda_function(fn, "intel", 0x1000, 0x100)
    assert hashed == replace(
        block_of(0x1000, ins("4889e5", 0x1000, 3), ins("c3", 0x1003, 1)), entry=True
    )
    assert hashed.digest == hash_escaped_block(b"4889e5c3", 0x1000, 4).digest


def test_hash_smda_function_drops_instructions_without_bytes():
    fn = FakeSmdaFunction(
        [FakeBlock(0x1000, [FakeInstruction(0x1000, "4889e5"), FakeInstruction(0x1003, "")])]
    )
    (hashed,) = hash_smda_function(fn, "intel", 0x1000, 0x100)
    assert hashed.instructions == ()
    assert hashed.size == 3


def test_repeated_render_reuses_the_alignment():
    _annotations.cache_clear()
    first = annotation_blocks([block(LEFT_VARIANT, 0x1000)], [block(RIGHT_VARIANT, 0x2000)])
    again = annotation_blocks([block(LEFT_VARIANT, 0x1000)], [block(RIGHT_VARIANT, 0x2000)])
    assert first == again
    assert _annotations.cache_info().hits == 1
    # Returned lists are fresh, so callers cannot corrupt the cached result.
    first[0].clear()
    assert annotation_blocks([block(LEFT_VARIANT, 0x1000)], [block(RIGHT_VARIANT, 0x2000)])[0]


def test_entry_blocks_pair_even_when_another_block_scores_higher():
    """The real case: target entry `push rdx; js` scored higher against a lone `js` block."""
    push_rdi, jpe = "57", "0f8a????????"
    push_rdx, js = "52", "0f88????????"
    source_entry = hash_escaped_block(
        (push_rdi + jpe).encode(),
        0x1000,
        7,
        push_rdi + jpe,
        (InstructionHash(0x1000, 1, push_rdi), InstructionHash(0x1001, 6, jpe)),
        entry=True,
    )
    source_js = hash_escaped_block(js.encode(), 0x1010, 6, js, (InstructionHash(0x1010, 6, js),))
    target_entry = hash_escaped_block(
        (push_rdx + js).encode(),
        0x2000,
        7,
        push_rdx + js,
        (InstructionHash(0x2000, 1, push_rdx), InstructionHash(0x2001, 6, js)),
        entry=True,
    )
    source, target = annotation_blocks([source_entry, source_js], [target_entry])
    painted_source = {
        (a.instruction.offset if a.instruction else a.block.offset, a.changed) for a in source
    }
    painted_target = {
        (a.instruction.offset if a.instruction else a.block.offset, a.changed) for a in target
    }
    assert (0x1000, True) in painted_source and (0x2000, True) in painted_target
    assert (0x1010, False) in painted_source
    assert all(changed for _, changed in painted_target)


def test_one_sided_first_instruction_of_matched_entries_is_changed():
    """An extra prologue instruction must not paint the function header as removed."""
    push, mov = "55", "4889e5"
    source = hash_escaped_block(
        (push + mov).encode(),
        0x1000,
        4,
        push + mov,
        (InstructionHash(0x1000, 1, push), InstructionHash(0x1001, 3, mov)),
        entry=True,
    )
    target = hash_escaped_block(
        mov.encode(), 0x2000, 3, mov, (InstructionHash(0x2000, 3, mov),), entry=True
    )
    painted, other = annotation_blocks([source], [target])
    assert [(a.instruction and a.instruction.offset, a.changed) for a in painted] == [
        (0x1000, True)
    ]
    assert other == []

    inner = hash_escaped_block(
        (push + mov).encode(),
        0x1100,
        4,
        push + mov,
        (InstructionHash(0x1100, 1, push), InstructionHash(0x1101, 3, mov)),
    )
    later = hash_escaped_block(mov.encode(), 0x2100, 3, mov, (InstructionHash(0x2100, 3, mov),))
    painted, _ = annotation_blocks([source, inner], [target, later])
    assert (0x1100, False) in [(a.instruction and a.instruction.offset, a.changed) for a in painted]


def test_entry_does_not_anchor_to_an_identical_non_entry_block():
    body = "4889e5"
    source_entry = hash_escaped_block(body.encode(), 0x1000, 3, body, entry=True)
    target_entry = hash_escaped_block(b"55", 0x2000, 1, "55", entry=True)
    target_twin = hash_escaped_block(body.encode(), 0x2010, 3, body)
    source, target = annotation_blocks([source_entry], [target_entry, target_twin])
    by_offset = {a.block.offset: a.changed for a in target}
    assert by_offset[0x2000] is True
    assert by_offset[0x2010] is False
    assert [a.changed for a in source] == [True]


def test_unpaired_entry_on_one_side_is_still_changed():
    lone = hash_escaped_block(b"55", 0x2000, 1, "55", entry=True)
    other = hash_escaped_block(b"c3", 0x1000, 1, "c3")
    _, target = annotation_blocks([other], [lone])
    assert [(a.block.offset, a.changed) for a in target] == [(0x2000, True)]
