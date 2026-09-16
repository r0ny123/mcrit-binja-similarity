from mcrit_similarity.mcrit.parse import (
    corpus_label,
    decode_offset,
    parse_vs_result,
    target_functions,
    with_target_offsets,
)
from mcrit_similarity.scoring import IS_LIBRARY_FLAG, IS_PICHASH_FLAG


def test_parse_filters_and_ranks(vs_result):
    matches = parse_vs_result(
        vs_result,
        min_score=50,
        include_library=False,
        max_results=2,
        target_sample_id=1,
    )
    by_offset = {}
    for match in matches:
        by_offset.setdefault(match.source_offset, []).append(match)
    first = by_offset[0x401000]
    assert first[0].is_pichash
    assert first[0].target_function_id == 20
    assert first[0].score == 100.0
    assert first[1].target_function_id == 21
    assert all(not match.is_library for match in matches)
    assert 0x401100 in by_offset


def test_parse_keeps_library_when_requested(vs_result):
    matches = parse_vs_result(vs_result, min_score=50, include_library=True, max_results=5)
    assert any(match.flags & IS_LIBRARY_FLAG for match in matches)


def test_legacy_boolean_flags():
    payload = {
        "info": {"sample": {}},
        "matches": {
            "functions": [
                {
                    "fid": 1,
                    "num_bytes": 10,
                    "offset": 100,
                    "matches": [[0, 1, 2, 100.0, True]],
                }
            ]
        },
    }
    matches = parse_vs_result(payload, min_score=0, max_results=1)
    assert matches[0].flags & IS_PICHASH_FLAG


def test_decode_and_attach_offsets():
    assert decode_offset(-1) == 0xFFFFFFFFFFFFFFFF
    found = target_functions({20: {"offset": 0x400100}, 21: {"offset": -16}, 22: {}})
    assert found == {20: (0x400100, ""), 21: (0xFFFFFFFFFFFFFFF0, "")}
    offsets = {fid: offset for fid, (offset, _) in found.items()}
    matches = parse_vs_result(
        {
            "info": {"sample": {}},
            "matches": {
                "functions": [
                    {
                        "fid": 1,
                        "num_bytes": 8,
                        "offset": 0x401000,
                        "matches": [[0, 1, 20, 100.0, 2]],
                    }
                ]
            },
        }
    )
    filled = with_target_offsets(matches, offsets)
    assert filled[0].target_offset == 0x400100


def _payload(*candidates, num_bytes=128):
    return {
        "matches": {
            "functions": [
                {"fid": 1, "num_bytes": num_bytes, "offset": 0x1000, "matches": list(candidates)}
            ]
        }
    }


def test_candidate_stats_survive_max_results_truncation():
    matches = parse_vs_result(
        _payload([0, 1, 20, 90.0, 1], [0, 1, 21, 90.0, 1], [0, 1, 22, 60.0, 1]), max_results=1
    )
    assert len(matches) == 1
    only = matches[0]
    assert (only.candidates, only.best_score, only.runner_up_score) == (3, 90.0, 90.0)
    assert only.margin == 0


def test_margin_is_relative_to_strongest_other_candidate():
    best, worse = parse_vs_result(_payload([0, 1, 20, 95.0, 1], [0, 1, 21, 70.0, 1]))
    assert best.margin == 25
    assert worse.margin == -25
    (sole,) = parse_vs_result(_payload([0, 1, 20, 95.0, 1]))
    assert sole.candidates == 1 and sole.margin is None


def test_pichash_candidates_are_counted():
    matches = parse_vs_result(
        _payload([0, 1, 20, 100.0, 3], [0, 1, 21, 100.0, 2], [0, 1, 22, 80.0, 1])
    )
    assert [match.pichash_candidates for match in matches] == [2, 2, 2]


def test_corpus_label_prefers_newest_real_label():
    def label(text, stamp):
        return {"function_label": text, "username": "u", "timestamp": stamp}

    entry = {
        "function_labels": [
            label("old_name", "2026-01-01T00:00:00"),
            label("new_name", "2026-02-01T00:00:00"),
            label("sub_401000", "2026-03-01T00:00:00"),
            label("", "2026-04-01T00:00:00"),
        ]
    }
    assert corpus_label(entry) == "new_name"
    assert corpus_label({"function_labels": None}) == ""
    assert corpus_label({}) == ""
