from mcrit_similarity.scoring import (
    IS_LIBRARY_FLAG,
    IS_PICHASH_FLAG,
    confidence_from_match,
    similarity_from_minhash,
    uniqueness_weight,
)


def test_similarity_bounds():
    assert similarity_from_minhash(0) == 0
    assert similarity_from_minhash(100) == 255
    assert similarity_from_minhash(50) == 128
    assert similarity_from_minhash(-1) == 0
    assert similarity_from_minhash(150) == 255


def test_pichash_confidence_is_max():
    assert confidence_from_match(50, IS_PICHASH_FLAG, 8) == 255


def test_stub_size_dampens_minhash_confidence():
    full = confidence_from_match(100, 0, 64)
    half = confidence_from_match(100, 0, 32)
    assert full == 255
    assert half == 128


def test_library_flag_does_not_reduce_confidence():
    plain = confidence_from_match(100, 0, 128)
    library = confidence_from_match(100, IS_LIBRARY_FLAG, 128)
    assert plain == library == 255


def test_unique_pichash_keeps_full_confidence_but_ambiguous_one_halves():
    assert confidence_from_match(100, IS_PICHASH_FLAG, 128, pichash_candidates=1) == 255
    assert confidence_from_match(100, IS_PICHASH_FLAG, 128, pichash_candidates=3) == 128


def test_tie_halves_minhash_confidence():
    assert confidence_from_match(90, 0, 128, margin=None) == 230
    assert confidence_from_match(90, 0, 128, margin=0) == 115


def test_clear_margin_keeps_full_confidence_and_trailing_candidates_drop():
    assert confidence_from_match(90, 0, 128, margin=10) == 230
    assert confidence_from_match(90, 0, 128, margin=5) == 172
    assert confidence_from_match(70, 0, 128, margin=-20) == 44


def test_uniqueness_weight_bounds():
    assert uniqueness_weight(None) == 1.0
    assert uniqueness_weight(100) == 1.0
    assert uniqueness_weight(0) == 0.5
    assert uniqueness_weight(-100) == 0.25
    for margin in (-1000, -5, 0, 5, 1000):
        assert 0 <= confidence_from_match(100, 0, 1 << 20, margin=margin) <= 255
