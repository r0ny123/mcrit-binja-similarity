"""PicBlock hashes for render annotations. Same algorithm MCRITweb uses first."""

from __future__ import annotations

import functools
import hashlib
import struct
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

# Below this ratio two escaped blocks are unrelated rather than a changed version of each other.
_CHANGED_RATIO = 0.5
# Scoring every leftover pair is O(n*m); past this many pairs fall back to positional
# pairing so a render callback cannot stall the UI thread.
_FUZZY_PAIR_BUDGET = 20000


@dataclass(frozen=True)
class InstructionHash:
    offset: int
    size: int
    escaped: str


@dataclass(frozen=True)
class BlockHash:
    offset: int
    size: int
    digest: int
    escaped: str = ""
    instructions: tuple[InstructionHash, ...] = ()
    entry: bool = False


@dataclass(frozen=True)
class BlockAnnotation:
    """A range to paint: ``changed`` means it has a counterpart that differs.

    ``instruction`` narrows the range to one instruction; ``None`` paints the whole block.
    """

    block: BlockHash
    changed: bool
    instruction: InstructionHash | None = None


def _u64(data: bytes) -> int:
    return struct.unpack("Q", hashlib.sha256(data).digest()[:8])[0]


def hash_escaped_block(
    escaped: bytes,
    offset: int,
    size: int,
    text: str = "",
    instructions: tuple[InstructionHash, ...] = (),
    entry: bool = False,
) -> BlockHash:
    return BlockHash(
        offset=offset,
        size=size,
        digest=_u64(escaped),
        escaped=text,
        instructions=instructions,
        entry=entry,
    )


def hash_smda_function(
    smda_function, architecture: str, base_addr: int, binary_size: int
) -> list[BlockHash]:
    from smda.common.SmdaFunction import SmdaFunction

    escaper = SmdaFunction.getInstructionEscaper(architecture)
    if escaper is None:
        return []
    upper = base_addr + binary_size
    hashed: list[BlockHash] = []
    for block in smda_function.getBlocks():
        instructions = tuple(
            InstructionHash(
                offset=instruction.offset,
                size=len(getattr(instruction, "bytes", None) or "") // 2,
                escaped=instruction.getEscapedBinary(
                    escaper,
                    escape_intraprocedural_jumps=True,
                    lower_addr=base_addr,
                    upper_addr=upper,
                ),
            )
            for instruction in block.getInstructions()
        )
        text = "".join(instruction.escaped for instruction in instructions)
        payload = bytes(ord(c) for c in text)
        byte_size = sum(instruction.size for instruction in instructions)
        if byte_size <= 0:
            byte_size = max(getattr(block, "length", 1) or 1, 1)
        if any(instruction.size <= 0 for instruction in instructions):
            instructions = ()
        entry = block.offset == smda_function.offset
        hashed.append(
            hash_escaped_block(payload, block.offset, byte_size, text, instructions, entry)
        )
    return hashed


def _scorer(right: str) -> SequenceMatcher:
    # SequenceMatcher caches its analysis of the second sequence, so keep one per target block.
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(right)
    return matcher


def _ratio(matcher: SequenceMatcher, left: str) -> float:
    if not left:
        return 0.0
    matcher.set_seq1(left)
    # Both are cheap upper bounds on ratio(), so either falling short settles it.
    if matcher.real_quick_ratio() < _CHANGED_RATIO or matcher.quick_ratio() < _CHANGED_RATIO:
        return 0.0
    return matcher.ratio()


def _unanchored(source: list[BlockHash], target: list[BlockHash]) -> tuple[list[int], list[int]]:
    """Indices left over after matching identical digests one-to-one.

    Entry blocks only anchor to each other: a function's entry corresponds to the other entry
    even when its bytes equal some other block there.
    """
    source_entry = next((i for i, block in enumerate(source) if block.entry), None)
    target_entry = next((j for j, block in enumerate(target) if block.entry), None)
    both_entries = source_entry is not None and target_entry is not None
    slots: dict[int, list[int]] = {}
    for index, block in enumerate(target):
        if not (both_entries and index == target_entry):
            slots.setdefault(block.digest, []).append(index)
    matched_targets: set[int] = set()
    source_left: list[int] = []
    for index, block in enumerate(source):
        if both_entries and index == source_entry:
            continue
        available = slots.get(block.digest)
        if available:
            matched_targets.add(available.pop(0))
        else:
            source_left.append(index)
    target_left = [
        index
        for index in range(len(target))
        if index not in matched_targets and not (both_entries and index == target_entry)
    ]
    if both_entries and source[source_entry].digest != target[target_entry].digest:
        source_left.append(source_entry)
        target_left.append(target_entry)
    return source_left, target_left


def _changed_pairs(
    source: list[BlockHash],
    target: list[BlockHash],
    source_left: list[int],
    target_left: list[int],
) -> list[tuple[int, int]]:
    if not source_left or not target_left:
        return []
    if len(source_left) * len(target_left) > _FUZZY_PAIR_BUDGET:
        return [
            (i, j)
            for i, j in zip(source_left, target_left, strict=False)
            if target[j].escaped
            and _ratio(_scorer(target[j].escaped), source[i].escaped) >= _CHANGED_RATIO
        ]
    scored: list[tuple[float, int, int]] = []
    for j in target_left:
        if not target[j].escaped:
            continue
        matcher = _scorer(target[j].escaped)
        for i in source_left:
            ratio = _ratio(matcher, source[i].escaped)
            if ratio >= _CHANGED_RATIO:
                scored.append((ratio, i, j))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_source: set[int] = set()
    used_target: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in scored:
        if i in used_source or j in used_target:
            continue
        used_source.add(i)
        used_target.add(j)
        pairs.append((i, j))
    return pairs


def _instruction_diff(
    left: BlockHash, right: BlockHash
) -> tuple[list[BlockAnnotation], list[BlockAnnotation]]:
    if not left.instructions or not right.instructions:
        return [BlockAnnotation(left, True)], [BlockAnnotation(right, True)]
    matcher = SequenceMatcher(
        None,
        [instruction.escaped for instruction in left.instructions],
        [instruction.escaped for instruction in right.instructions],
        autojunk=False,
    )
    source: list[BlockAnnotation] = []
    target: list[BlockAnnotation] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # A replace occupies the same slot on both sides (condition-code swaps, junk
        # substitutions), so it is Changed even when the mnemonics differ.
        changed = tag == "replace"
        source.extend(BlockAnnotation(left, changed, ins) for ins in left.instructions[i1:i2])
        target.extend(BlockAnnotation(right, changed, ins) for ins in right.instructions[j1:j2])
    return source, target


def _entry_changed(annotations: list[BlockAnnotation]) -> list[BlockAnnotation]:
    # The function header shares the entry address, so a one-sided annotation there would paint
    # the header as if the whole matched function were removed or added.
    return [
        replace(annotation, changed=True)
        if annotation.block.entry
        and (annotation.instruction or annotation.block).offset == annotation.block.offset
        else annotation
        for annotation in annotations
    ]


def annotation_blocks(
    source: list[BlockHash],
    target: list[BlockHash],
) -> tuple[list[BlockAnnotation], list[BlockAnnotation]]:
    """Blocks to paint on each side.

    Identical digests anchor the two functions. Leftovers are paired by escaped-instruction
    similarity, because MCRIT's escaper masks addresses but not registers or condition codes:
    genuinely corresponding blocks of a MinHash match rarely hash equal. A paired leftover is
    ``changed``; an unpaired one is removed (source) or added (target). Within a paired
    block the instructions are aligned in turn, and only the differing ones are returned.

    Identical functions anchor completely and produce nothing to paint. The MCRIT score is not
    used for that: a MinHash score of 100 still allows differing instructions.
    """
    if not source or not target:
        return [], []
    source_out, target_out = _annotations(tuple(source), tuple(target))
    return list(source_out), list(target_out)


# The result depends only on the hashed blocks, so re-rendering a result skips the alignment.
@functools.lru_cache(maxsize=128)
def _annotations(
    source: tuple[BlockHash, ...],
    target: tuple[BlockHash, ...],
) -> tuple[tuple[BlockAnnotation, ...], tuple[BlockAnnotation, ...]]:
    src, tgt = list(source), list(target)
    source_left, target_left = _unanchored(src, tgt)
    # Matched functions' entry blocks correspond even when a similar-looking block scores higher.
    pairs: list[tuple[int, int]] = []
    source_entry = next((i for i in source_left if src[i].entry), None)
    target_entry = next((j for j in target_left if tgt[j].entry), None)
    if source_entry is not None and target_entry is not None:
        pairs.append((source_entry, target_entry))
        source_rest = [i for i in source_left if i != source_entry]
        target_rest = [j for j in target_left if j != target_entry]
    else:
        source_rest, target_rest = source_left, target_left
    pairs += _changed_pairs(src, tgt, source_rest, target_rest)
    paired_source = {i for i, _ in pairs}
    paired_target = {j for _, j in pairs}
    source_out = [BlockAnnotation(src[i], False) for i in source_left if i not in paired_source]
    target_out = [BlockAnnotation(tgt[j], False) for j in target_left if j not in paired_target]
    for i, j in pairs:
        left, right = _instruction_diff(src[i], tgt[j])
        source_out.extend(left)
        target_out.extend(right)
    return tuple(_entry_changed(source_out)), tuple(_entry_changed(target_out))
