"""Map MCRIT 0–100 scores and match flags onto Binary Ninja 0–255 values."""

from __future__ import annotations

IS_MINHASH_FLAG = 1
IS_PICHASH_FLAG = 1 << 1
IS_LIBRARY_FLAG = 1 << 2

# Stubs below this size get linearly reduced confidence so a resolver prefers real functions.
_CONFIDENCE_FULL_BYTES = 64.0
# MinHash points (0-100) a match must lead every other candidate by to keep full confidence.
_CLEAR_MARGIN = 10.0


def clamp_score(value: int) -> int:
    return max(0, min(255, value))


def similarity_from_minhash(matched_score: float) -> int:
    if matched_score <= 0:
        return 0
    if matched_score >= 100:
        return 255
    return clamp_score(round(matched_score / 100.0 * 255))


def uniqueness_weight(margin: float | None) -> float:
    """1.0 for a sole or clearly leading candidate, 0.5 for a tie, 0.25 floor for trailing ones."""
    if margin is None:
        return 1.0
    return max(0.25, min(1.0, 0.5 + margin / (2 * _CLEAR_MARGIN)))


def confidence_from_match(
    matched_score: float,
    flags: int,
    num_bytes: float,
    margin: float | None = None,
    pichash_candidates: int = 0,
) -> int:
    if flags & IS_PICHASH_FLAG:
        return 255 if pichash_candidates <= 1 else 128
    similarity = similarity_from_minhash(matched_score)
    size = max(0.0, float(num_bytes))
    weight = 1.0 if size >= _CONFIDENCE_FULL_BYTES else size / _CONFIDENCE_FULL_BYTES
    return clamp_score(round(similarity * weight * uniqueness_weight(margin)))


def is_pichash(flags: int) -> bool:
    return bool(flags & IS_PICHASH_FLAG)


def is_library(flags: int) -> bool:
    return bool(flags & IS_LIBRARY_FLAG)


def is_minhash(flags: int) -> bool:
    return bool(flags & IS_MINHASH_FLAG)
