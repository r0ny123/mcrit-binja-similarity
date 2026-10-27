"""Render a pairwise MCRIT result with Binary Ninja DiffRenderer annotations."""

from __future__ import annotations

from mcrit_similarity.cache import LockedCache
from mcrit_similarity.export import cached_report
from mcrit_similarity.log import log_debug, log_warn
from mcrit_similarity.picblocks import BlockHash, annotation_blocks, hash_smda_function

# (report sha256, function offset) -> PicBlock hashes. Render runs on the UI thread, so a
# re-opened comparison must not re-hash every instruction of both functions again.
_PICBLOCKS = LockedCache(max_items=1024)


def _report_for_view(view) -> object | None:
    return cached_report(view) if view is not None else None


def smda_function(report, address: int):
    if report is None:
        return None
    function = report.getFunction(address)
    return function if function is not None else report.getFunction(address & ~1)


def _block_hashes(report, address: int) -> list[BlockHash]:
    function = smda_function(report, address)
    if function is None or report is None:
        return []
    sha256 = getattr(report, "sha256", "") or ""

    def _hash() -> list[BlockHash]:
        try:
            return hash_smda_function(
                function,
                report.architecture,
                int(report.base_addr or 0),
                int(report.binary_size or 0),
            )
        except Exception as exc:
            log_debug(f"PicBlock hashing failed at {address:#x}: {exc}")
            return []

    return _PICBLOCKS.get_or_set((sha256, function.offset), _hash, cache_empty=True)


def _range_for_block(bn_function, offset: int, size: int) -> tuple[int, int]:
    block = bn_function.get_basic_block_at(offset)
    if block is not None:
        return block.start, block.end
    return offset, offset + max(size, 1)


def render_match(
    context,
    source_function,
    target_function,
    source_entity,
    target_entity,
) -> None:
    from binaryninja.enums import SimilarityAnnotationType
    from binaryninja.similarity import DiffRenderer, SimilarityRangeAnnotation

    source_report = _report_for_view(source_function.view)
    target_report = _report_for_view(target_function.view)
    reports_missing = source_report is None or target_report is None
    if reports_missing:
        # Exporting here would run update_analysis_and_wait() on the UI thread and deadlock.
        log_warn(
            "MCRIT render: no SMDA report is cached for this binary (for example after a "
            "restart); re-run the session to enable diff colouring"
        )
    source_blocks = _block_hashes(source_report, source_function.start)
    target_blocks = _block_hashes(target_report, target_function.start)
    unmatched_source, unmatched_target = annotation_blocks(source_blocks, target_blocks)

    def paint(function, annotations, one_sided):
        renderer = DiffRenderer()
        for annotation in annotations:
            instruction = annotation.instruction
            if instruction is not None:
                start, end = instruction.offset, instruction.offset + instruction.size
            else:
                start, end = _range_for_block(
                    function, annotation.block.offset, annotation.block.size
                )
            kind = (
                SimilarityAnnotationType.SimilarityAnnotationChanged
                if annotation.changed
                else one_sided
            )
            renderer.add_range_annotation(SimilarityRangeAnnotation(start, end, kind))
        return renderer

    paint(
        source_function, unmatched_source, SimilarityAnnotationType.SimilarityAnnotationRemoved
    ).render(context, source_function, entity=source_entity)
    paint(
        target_function, unmatched_target, SimilarityAnnotationType.SimilarityAnnotationAdded
    ).render(context, target_function, entity=target_entity)

    if not reports_missing:
        if not source_blocks and not target_blocks:
            log_warn(
                "MCRIT render had no PicBlock hashes for either function; showing unannotated comparison"
            )
        elif not source_blocks or not target_blocks:
            log_warn("MCRIT render had asymmetric PicBlock hashes; showing unannotated comparison")
