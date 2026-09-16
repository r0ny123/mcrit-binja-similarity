"""Map MCRIT function offsets onto Binary Ninja similarity entities."""

from __future__ import annotations

from dataclasses import dataclass

from mcrit_similarity.mcrit.parse import FunctionMatch
from mcrit_similarity.scoring import confidence_from_match, similarity_from_minhash


@dataclass(frozen=True)
class Entity:
    entity_id: int
    address: int
    name: str


@dataclass(frozen=True)
class MatchPair:
    source_entity_id: int
    target_entity_id: int
    similarity: int
    confidence: int
    source_offset: int
    target_offset: int
    score: float
    flags: int


def _aliases(address: int) -> tuple[int, ...]:
    cleared = address & ~1
    return (address, cleared) if cleared != address else (address,)


def index_entities(entities: list[Entity]) -> dict[int, Entity]:
    indexed: dict[int, Entity] = {}
    for entity in entities:
        for alias in _aliases(entity.address):
            indexed.setdefault(alias, entity)
    return indexed


def lookup_entity(index: dict[int, Entity], address: int) -> Entity | None:
    for alias in _aliases(address):
        entity = index.get(alias)
        if entity is not None:
            return entity
    return None


def pair_matches(
    matches: list[FunctionMatch],
    source_index: dict[int, Entity],
    target_index: dict[int, Entity],
    scheduled_source_ids: set[int],
) -> tuple[list[MatchPair], list[FunctionMatch]]:
    """Return pairs whose source is scheduled and both offsets resolve. Unresolved matches are listed."""
    paired: list[MatchPair] = []
    unresolved: list[FunctionMatch] = []
    for match in matches:
        if match.target_offset is None:
            unresolved.append(match)
            continue
        source = lookup_entity(source_index, match.source_offset)
        target = lookup_entity(target_index, match.target_offset)
        if source is None or target is None:
            unresolved.append(match)
            continue
        if source.entity_id not in scheduled_source_ids:
            continue
        paired.append(
            MatchPair(
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id,
                similarity=255 if match.is_pichash else similarity_from_minhash(match.score),
                confidence=confidence_from_match(
                    match.score,
                    match.flags,
                    match.num_bytes,
                    match.margin,
                    match.pichash_candidates,
                ),
                source_offset=match.source_offset,
                target_offset=match.target_offset,
                score=match.score,
                flags=match.flags,
            )
        )
    return paired, unresolved
