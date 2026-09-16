"""Malformed MCRIT payloads must yield safe defaults or the project's own errors."""

import json
import math
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mcrit_similarity.errors import McritError
from mcrit_similarity.mcrit.parse import (
    FunctionMatch,
    corpus_label,
    decode_offset,
    parse_vs_result,
    target_functions,
    unique_target_function_ids,
    with_target_offsets,
)
from mcrit_similarity.scoring import IS_LIBRARY_FLAG, IS_PICHASH_FLAG

PROPERTY = settings(deadline=None, max_examples=150)

# Anything json.loads can produce, including the NaN and Infinity literals it accepts by default.
scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.integers(min_value=-(10**40), max_value=10**40),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(),
    st.just(chr(0) + chr(0xD7FF) + chr(0xFFFF)),
)
json_values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5), st.dictionaries(st.text(max_size=6), children, max_size=5)
    ),
    max_leaves=12,
)

match_rows = st.one_of(
    json_values,
    st.lists(scalars, min_size=0, max_size=7),
    st.tuples(scalars, scalars, scalars, scalars, scalars),
)
summaries = st.one_of(
    json_values,
    st.fixed_dictionaries(
        {
            "offset": scalars,
            "fid": scalars,
            "num_bytes": scalars,
            "matches": st.lists(match_rows, max_size=4),
        }
    ),
)
payloads = st.one_of(
    json_values,
    st.builds(
        lambda functions: {"matches": {"functions": functions}}, st.lists(summaries, max_size=4)
    ),
)

LEAKS = (KeyError, TypeError, AttributeError, IndexError, OverflowError, ValueError)


@given(
    payloads,
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.booleans(),
    st.integers(min_value=1, max_value=8),
)
@PROPERTY
def test_parsing_never_leaks_a_builtin_error(payload, min_score, include_library, max_results):
    try:
        matches = parse_vs_result(
            payload,
            min_score=min_score,
            include_library=include_library,
            max_results=max_results,
        )
    except McritError:
        return
    except LEAKS as exc:  # pragma: no cover - the assertion reports the payload
        raise AssertionError(f"{type(exc).__name__} leaked for {payload!r}") from exc
    assert all(isinstance(match, FunctionMatch) for match in matches)
    for match in matches:
        if not math.isnan(match.score):
            assert match.score >= min_score
        if not include_library:
            assert not match.flags & IS_LIBRARY_FLAG
        assert isinstance(match.source_offset, int)
        assert match.source_function_id >= 0
    per_offset: dict[int, int] = {}
    for match in matches:
        per_offset[match.source_offset] = per_offset.get(match.source_offset, 0) + 1
    assert all(count <= max_results for count in per_offset.values())


@given(payloads)
@PROPERTY
def test_parsing_is_deterministic(payload):
    assert parse_vs_result(payload) == parse_vs_result(payload)


@given(scalars, st.sampled_from([8, 16, 32, 64]))
@PROPERTY
def test_decode_offset_stays_in_the_address_space(value, bitness):
    offset = decode_offset(value, bitness)
    assert offset is None or offset >= 0
    if offset is not None and isinstance(value, int) and value < 0:
        assert offset < 1 << bitness


@given(json_values)
@PROPERTY
def test_target_functions_ignores_unusable_entries(entry):
    found = target_functions({7: entry})
    assert set(found) <= {7}
    for offset, label in found.values():
        assert isinstance(offset, int) and isinstance(label, str)


@given(json_values)
@PROPERTY
def test_corpus_label_always_returns_a_string(labels):
    assert isinstance(corpus_label({"function_labels": labels}), str)


def test_non_finite_numbers_are_dropped_not_raised():
    # Regression: int(float("inf")) raises OverflowError, which the except clauses missed.
    inf = float("inf")

    def parse_row(row):
        return parse_vs_result(
            {"matches": {"functions": [{"offset": 0x1000, "fid": 1, "matches": [row]}]}}
        )

    for field in ("offset", "fid"):
        summary = {"offset": 0x1000, "fid": 1, "num_bytes": 4.0, "matches": [[1, 2, 3, 50.0, 1]]}
        summary[field] = inf
        assert parse_vs_result({"matches": {"functions": [summary]}}) == []
    assert (
        len(
            parse_vs_result(
                {
                    "matches": {
                        "functions": [
                            {
                                "offset": 1,
                                "fid": 1,
                                "num_bytes": inf,
                                "matches": [[1, 2, 3, 50.0, 1]],
                            }
                        ]
                    }
                }
            )
        )
        == 1
    )
    for index in (0, 1, 2):
        row = [1, 2, 3, 50.0, 1]
        row[index] = inf
        assert parse_row(row) == [], f"index {index} must drop the row"
    assert math.isinf(parse_row([1, 2, 3, inf, 1])[0].score)
    assert parse_row([1, 2, 3, 50.0, inf])[0].flags == 0
    assert decode_offset(inf) is None
    assert target_functions({1: {"offset": inf}}) == {}


def test_nan_score_survives_to_a_zero_similarity():
    payload = json.loads(
        '{"matches": {"functions": [{"offset": 4096, "fid": 1, "num_bytes": 64,'
        ' "matches": [[0, 1, 2, NaN, 1]]}]}}'
    )
    from mcrit_similarity.scoring import confidence_from_match, similarity_from_minhash

    (match,) = parse_vs_result(payload)
    assert math.isnan(match.score)
    assert similarity_from_minhash(match.score) == 0
    assert confidence_from_match(match.score, match.flags, match.num_bytes) == 0


def test_missing_and_extra_fields_are_tolerated():
    payload = {
        "unexpected": object(),
        "matches": {
            "extra": 1,
            "functions": [
                {"fid": 1},
                {"offset": 0x1000},
                {"offset": 0x1000, "fid": -9, "matches": None},
                {"offset": 0x1000, "fid": 2, "matches": [[1, 2, 3]]},
                {"offset": 0x2000, "fid": 3, "matches": [[1, 2, 3, 70.0]], "spare": "x"},
            ],
        },
    }
    (match,) = parse_vs_result(payload)
    assert match.source_offset == 0x2000
    assert match.flags == 0
    assert match.num_bytes == 0.0


def test_wrong_container_types_return_no_matches():
    # parse_vs_result guards the payload type itself, so callers may hand it anything JSON returns.
    wrong: list[Any] = [[], "matches", {"matches": []}, {"matches": {"functions": "abc"}}, None, 7]
    for payload in wrong:
        assert parse_vs_result(payload) == []


def test_unicode_and_huge_ints_round_trip():
    payload = {
        "matches": {
            "functions": [
                {
                    "offset": 10**30,
                    "fid": -(10**30),
                    "num_bytes": "12",
                    "matches": [[1, 2, 10**25, "55.5", IS_PICHASH_FLAG]],
                }
            ]
        }
    }
    (match,) = parse_vs_result(payload)
    assert match.source_offset == 10**30
    assert match.source_function_id == 10**30
    assert match.target_function_id == 10**25
    assert match.score == 55.5
    assert match.is_pichash
    assert (
        corpus_label({"function_labels": [{"function_label": "héllo☃", "timestamp": "2026"}]})
        == "héllo☃"
    )


def test_boolean_flag_is_read_as_pichash():
    payload = {
        "matches": {"functions": [{"offset": 1, "fid": 1, "matches": [[1, 2, 3, 10.0, True]]}]}
    }
    (match,) = parse_vs_result(payload)
    assert match.is_pichash


def test_candidate_statistics_precede_truncation():
    rows = [[0, 1, fid, float(score), 1] for fid, score in ((10, 90.0), (11, 80.0), (12, 10.0))]
    payload = {
        "matches": {"functions": [{"offset": 1, "fid": 1, "num_bytes": 64, "matches": rows}]}
    }
    selected = parse_vs_result(payload, max_results=1)
    assert len(selected) == 1
    assert selected[0].candidates == 3
    assert selected[0].margin == 10.0


def test_placeholder_labels_are_ignored():
    entry = {
        "offset": 0x1000,
        "function_labels": [
            {"function_label": "sub_401000", "timestamp": "2026-01-02T00:00:00"},
            {"function_label": "parse_header", "timestamp": "2026-01-01T00:00:00"},
        ],
    }
    assert target_functions({5: entry}) == {5: (0x1000, "parse_header")}


def test_offset_filling_and_id_dedup():
    base = FunctionMatch(1, None, 1, 42, 1, 1, 50.0, 0, 8.0)
    matches = [base, base, FunctionMatch(2, None, 2, 43, 1, 1, 50.0, 0, 8.0)]
    assert unique_target_function_ids(matches) == [42, 43]
    filled = with_target_offsets(matches, {42: 0x500})
    assert [match.target_offset for match in filled] == [0x500, 0x500, None]


@pytest.mark.parametrize("bad", [{"$oid": 1}, [1, 2], "0x10", "", "nan"])
def test_unparsable_offsets_are_none(bad):
    assert decode_offset(bad) is None


@given(st.dictionaries(scalars, json_values, max_size=4))
@PROPERTY
def test_target_functions_tolerates_any_key(entries):
    found = target_functions(entries)
    assert all(isinstance(key, int) for key in found)


def test_non_numeric_function_ids_are_skipped():
    # The client normalises ids, but the parser is the layer that must survive a raw payload.
    entries: dict[Any, Any] = {
        "7": {"offset": 0x1000},
        "seven": {"offset": 0x2000},
        None: {"offset": 0x3000},
    }
    assert target_functions(entries) == {7: (0x1000, "")}
