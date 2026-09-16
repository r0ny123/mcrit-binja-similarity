from mcrit_similarity.mapping import Entity, index_entities, lookup_entity, pair_matches
from mcrit_similarity.mcrit.parse import FunctionMatch


def test_thumb_alias_lookup():
    entities = [Entity(1, 0x1001, "thumb")]
    index = index_entities(entities)
    even = lookup_entity(index, 0x1000)
    odd = lookup_entity(index, 0x1001)
    assert even is not None and even.entity_id == 1
    assert odd is not None and odd.entity_id == 1


def test_pair_requires_scheduled_source():
    source = index_entities([Entity(10, 0x401000, "a")])
    target = index_entities([Entity(20, 0x400100, "b")])
    match = FunctionMatch(
        source_offset=0x401000,
        target_offset=0x400100,
        source_function_id=1,
        target_function_id=2,
        target_sample_id=1,
        target_family_id=0,
        score=100,
        flags=2,
        num_bytes=80,
    )
    paired, unresolved = pair_matches([match], source, target, scheduled_source_ids=set())
    assert paired == []
    assert unresolved == []

    paired, unresolved = pair_matches([match], source, target, scheduled_source_ids={10})
    assert len(paired) == 1
    assert paired[0].similarity == 255
    assert paired[0].confidence == 255
    assert unresolved == []


def test_unresolved_when_target_offset_missing():
    source = index_entities([Entity(10, 0x401000, "a")])
    target = index_entities([Entity(20, 0x400100, "b")])
    match = FunctionMatch(
        source_offset=0x401000,
        target_offset=None,
        source_function_id=1,
        target_function_id=2,
        target_sample_id=1,
        target_family_id=0,
        score=80,
        flags=1,
        num_bytes=80,
    )
    paired, unresolved = pair_matches([match], source, target, scheduled_source_ids={10})
    assert paired == []
    assert unresolved == [match]


def test_ambiguous_matches_lose_confidence_but_keep_similarity():
    source = index_entities([Entity(10, 0x401000, "a")])
    target = index_entities([Entity(20, 0x400100, "b"), Entity(21, 0x400200, "c")])
    common = dict(
        source_offset=0x401000,
        source_function_id=1,
        target_sample_id=1,
        target_family_id=0,
        score=100,
        flags=2,
        num_bytes=80,
        candidates=2,
        best_score=100,
        runner_up_score=100,
        pichash_candidates=2,
    )
    matches = [
        FunctionMatch(target_offset=0x400100, target_function_id=2, **common),
        FunctionMatch(target_offset=0x400200, target_function_id=3, **common),
    ]
    paired, _ = pair_matches(matches, source, target, scheduled_source_ids={10})
    assert [(pair.similarity, pair.confidence) for pair in paired] == [(255, 128), (255, 128)]
