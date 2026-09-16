"""Properties of the MCRIT score to Binary Ninja 0-255 mapping."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from mcrit_similarity.scoring import (
    IS_LIBRARY_FLAG,
    IS_MINHASH_FLAG,
    IS_PICHASH_FLAG,
    clamp_score,
    confidence_from_match,
    similarity_from_minhash,
    uniqueness_weight,
)

PROPERTY = settings(deadline=None, max_examples=200)

# JSON permits NaN and Infinity, so anything a server sends can reach the mapping.
any_score = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-(10**9), max_value=10**9).map(float),
)


@given(any_score)
@PROPERTY
def test_similarity_is_always_a_byte(score):
    value = similarity_from_minhash(score)
    assert isinstance(value, int)
    assert 0 <= value <= 255


@given(
    any_score,
    st.integers(min_value=0, max_value=7),
    st.floats(allow_nan=True),
    st.one_of(st.none(), st.floats(allow_nan=True)),
    st.integers(min_value=0, max_value=5),
)
@PROPERTY
def test_confidence_is_always_a_byte(score, flags, num_bytes, margin, pichash_candidates):
    value = confidence_from_match(score, flags, num_bytes, margin, pichash_candidates)
    assert isinstance(value, int)
    assert 0 <= value <= 255


@given(
    st.floats(min_value=0, max_value=100, allow_nan=False),
    st.floats(min_value=0, max_value=100, allow_nan=False),
)
@PROPERTY
def test_similarity_is_monotonic_in_the_score(low, high):
    if low > high:
        low, high = high, low
    assert similarity_from_minhash(low) <= similarity_from_minhash(high)


@given(st.one_of(st.none(), st.floats(min_value=-100, max_value=100, allow_nan=False)))
@PROPERTY
def test_uniqueness_weight_stays_in_range(margin):
    assert 0.25 <= uniqueness_weight(margin) <= 1.0


def test_uniqueness_weight_rules():
    assert uniqueness_weight(None) == 1.0
    assert uniqueness_weight(0.0) == 0.5
    assert uniqueness_weight(10.0) == 1.0
    assert uniqueness_weight(100.0) == 1.0
    assert uniqueness_weight(-100.0) == 0.25


def test_nan_score_maps_to_zero():
    # Regression: round(nan) raised ValueError, and json.loads accepts a bare NaN literal.
    assert similarity_from_minhash(float("nan")) == 0
    assert confidence_from_match(float("nan"), IS_MINHASH_FLAG, 128.0) == 0


def test_extreme_scores_saturate():
    assert similarity_from_minhash(float("-inf")) == 0
    assert similarity_from_minhash(float("inf")) == 255
    assert similarity_from_minhash(-5.0) == 0
    assert similarity_from_minhash(1e308) == 255


def test_pichash_confidence_depends_only_on_candidate_count():
    for score in (0.0, 50.0, float("nan")):
        assert confidence_from_match(score, IS_PICHASH_FLAG, 1.0, None, 1) == 255
        assert confidence_from_match(score, IS_PICHASH_FLAG, 1.0, None, 2) == 128


def test_small_functions_lose_confidence_linearly():
    full = confidence_from_match(100.0, IS_MINHASH_FLAG, 64.0)
    assert full == 255
    assert confidence_from_match(100.0, IS_MINHASH_FLAG, 32.0) == 128
    assert confidence_from_match(100.0, IS_MINHASH_FLAG, 0.0) == 0
    assert confidence_from_match(100.0, IS_MINHASH_FLAG, -5.0) == 0
    assert confidence_from_match(100.0, IS_MINHASH_FLAG, float("nan")) == 0


def test_tie_halves_and_a_clear_lead_keeps_confidence():
    tied = confidence_from_match(90.0, IS_MINHASH_FLAG, 128.0, 0.0)
    leading = confidence_from_match(90.0, IS_MINHASH_FLAG, 128.0, 10.0)
    sole = confidence_from_match(90.0, IS_MINHASH_FLAG, 128.0, None)
    assert leading == sole
    assert tied == round(sole * 0.5)


@given(st.integers(min_value=-(10**6), max_value=10**6))
@PROPERTY
def test_clamp_keeps_the_byte_range(value):
    assert 0 <= clamp_score(value) <= 255


@given(st.integers(min_value=0, max_value=255))
@PROPERTY
def test_flag_predicates_match_the_bits(flags):
    from mcrit_similarity.scoring import is_library, is_minhash, is_pichash

    assert is_pichash(flags) == bool(flags & IS_PICHASH_FLAG)
    assert is_library(flags) == bool(flags & IS_LIBRARY_FLAG)
    assert is_minhash(flags) == bool(flags & IS_MINHASH_FLAG)


@given(st.floats(min_value=0, max_value=100, allow_nan=False))
@PROPERTY
def test_confidence_never_exceeds_similarity(score):
    similarity = similarity_from_minhash(score)
    for num_bytes in (0.0, 16.0, 64.0, 1e6):
        for margin in (None, -50.0, 0.0, 50.0):
            value = confidence_from_match(score, IS_MINHASH_FLAG, num_bytes, margin)
            assert value <= similarity or math.isclose(value, similarity)
