"""Parse MatcherVs JSON into function-level matches."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from mcrit_similarity.scoring import IS_LIBRARY_FLAG, IS_PICHASH_FLAG

_DEFAULT_NAME = re.compile(r"sub_[0-9a-fA-F]+")


@dataclass(frozen=True)
class FunctionMatch:
    source_offset: int
    target_offset: int | None
    source_function_id: int
    target_function_id: int
    target_sample_id: int
    target_family_id: int
    score: float
    flags: int
    num_bytes: float
    # Candidate statistics for the source function, taken before max_results truncation.
    candidates: int = 1
    best_score: float | None = None
    runner_up_score: float | None = None
    pichash_candidates: int = 0

    @property
    def margin(self) -> float | None:
        """Score lead over the strongest other candidate; None when there is no other candidate."""
        if self.candidates < 2 or self.best_score is None or self.runner_up_score is None:
            return None
        rival = self.runner_up_score if self.score >= self.best_score else self.best_score
        return self.score - rival

    @property
    def is_pichash(self) -> bool:
        return bool(self.flags & IS_PICHASH_FLAG)

    @property
    def is_library(self) -> bool:
        return bool(self.flags & IS_LIBRARY_FLAG)


def _as_flags(value: Any) -> int:
    if isinstance(value, bool):
        return IS_PICHASH_FLAG if value else 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _match_sort_key(match: FunctionMatch) -> tuple[int, float, float]:
    return (0 if match.is_pichash else 1, -match.score, -match.num_bytes)


def parse_vs_result(
    payload: dict[str, Any],
    *,
    min_score: float = 0,
    include_library: bool = True,
    max_results: int = 5,
    target_sample_id: int | None = None,
) -> list[FunctionMatch]:
    raw_matches = payload.get("matches") if isinstance(payload, dict) else None
    matches_dict = raw_matches if isinstance(raw_matches, dict) else {}
    functions = matches_dict.get("functions") or []
    grouped: dict[int, list[FunctionMatch]] = {}
    for summary in functions:
        try:
            offset = int(summary["offset"])
            fid = abs(int(summary["fid"]))
            num_bytes = float(summary.get("num_bytes") or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        for raw in summary.get("matches") or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            flags = _as_flags(raw[4]) if len(raw) > 4 else 0
            if not include_library and flags & IS_LIBRARY_FLAG:
                continue
            try:
                score = float(raw[3])
                matched_fid = int(raw[2])
                matched_sample = int(raw[1])
                matched_family = int(raw[0])
            except (TypeError, ValueError, OverflowError):
                continue
            if score < min_score:
                continue
            if target_sample_id is not None and matched_sample != target_sample_id:
                continue
            grouped.setdefault(offset, []).append(
                FunctionMatch(
                    source_offset=offset,
                    target_offset=None,
                    source_function_id=fid,
                    target_function_id=matched_fid,
                    target_sample_id=matched_sample,
                    target_family_id=matched_family,
                    score=score,
                    flags=flags,
                    num_bytes=num_bytes,
                )
            )
    selected: list[FunctionMatch] = []
    limit = max(1, int(max_results))
    for offset in grouped:
        ranked = sorted(grouped[offset], key=_match_sort_key)
        scores = sorted((match.score for match in ranked), reverse=True)
        stats = {
            "candidates": len(ranked),
            "best_score": scores[0],
            "runner_up_score": scores[1] if len(scores) > 1 else None,
            "pichash_candidates": sum(1 for match in ranked if match.is_pichash),
        }
        selected.extend(replace(match, **stats) for match in ranked[:limit])
    return selected


def decode_offset(value: Any, bitness: int = 64) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 0:
        modulus = 1 << bitness
        return (number + modulus) & (modulus - 1)
    return number


def corpus_label(entry: dict[str, Any]) -> str:
    """Newest MCRIT function label that is not a placeholder; timestamps are ``%Y-%m-%dT%H:%M:%S``."""
    labels = entry.get("function_labels")
    best = ("", "")
    for label in labels if isinstance(labels, list) else ():
        if not isinstance(label, dict):
            continue
        text = str(label.get("function_label") or "").strip()
        stamp = str(label.get("timestamp") or "")
        if text and not _DEFAULT_NAME.fullmatch(text) and stamp >= best[0]:
            best = (stamp, text)
    return best[1]


def target_functions(entries: dict[int, dict[str, Any]]) -> dict[int, tuple[int, str]]:
    """Map function id to (offset, corpus label) from ``POST /functions`` entries."""
    found: dict[int, tuple[int, str]] = {}
    for function_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        offset = decode_offset(entry.get("offset"))
        if offset is None:
            continue
        try:
            found[int(function_id)] = (offset, corpus_label(entry))
        except (TypeError, ValueError, OverflowError):
            continue
    return found


def with_target_offsets(
    matches: Iterable[FunctionMatch], offsets_by_fid: dict[int, int]
) -> list[FunctionMatch]:
    filled: list[FunctionMatch] = []
    for match in matches:
        offset = offsets_by_fid.get(match.target_function_id)
        if offset is None:
            filled.append(match)
            continue
        filled.append(replace(match, target_offset=offset))
    return filled


def unique_target_function_ids(matches: Iterable[FunctionMatch]) -> list[int]:
    seen: set[int] = set()
    ids: list[int] = []
    for match in matches:
        if match.target_function_id not in seen:
            seen.add(match.target_function_id)
            ids.append(match.target_function_id)
    return ids
