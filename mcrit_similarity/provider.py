"""Binary Ninja SimilarityProvider backed by MCRIT MatcherVs."""

from __future__ import annotations

import threading
from collections.abc import Callable

from binaryninja.similarity import (
    SimilarityApplyStatus,
    SimilarityEntityInfo,
    SimilarityEntityRef,
    SimilarityEntityType,
    SimilarityProvider,
    SimilarityProviderResults,
    SimilarityProviderType,
    SimilaritySessionCompletion,
    SimilaritySessionCompletionQuery,
    SimilaritySessionNode,
)

from mcrit_similarity import PROVIDER_NAME
from mcrit_similarity.cache import FUNCTION_OFFSETS, LockedCache
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.errors import McritError, McritJobError
from mcrit_similarity.export import export_smda_report
from mcrit_similarity.log import log_debug, log_error, log_info, log_warn
from mcrit_similarity.mapping import Entity, index_entities, lookup_entity, pair_matches
from mcrit_similarity.mcrit.client import McritClient
from mcrit_similarity.mcrit.parse import (
    applied_overrides,
    parse_vs_result,
    target_functions,
    unique_target_function_ids,
    with_target_offsets,
)
from mcrit_similarity.mcrit.samples import ensure_sample, match_sample_corpus, match_sample_vs
from mcrit_similarity.render import render_match, smda_function
from mcrit_similarity.settings import (
    config_from_settings,
    default_provider_settings,
    global_defaults,
)

# Whole-corpus MatcherSample payloads; each can hold matches for every function of a sample.
CORPUS_MATCHES = LockedCache(max_items=4)


def _node_by_id(node: SimilaritySessionNode, node_id: int) -> SimilaritySessionNode | None:
    if node.id == node_id:
        return node
    for other in (*node.incoming_nodes, *node.outgoing_nodes):
        if other.id == node_id:
            return other
    return None


def _target_functions(
    client: McritClient, server: str, function_ids: list[int]
) -> dict[int, tuple[int, str]]:
    """(offset, corpus label) per function id; one request covers every uncached id."""
    missing = [fid for fid in function_ids if FUNCTION_OFFSETS.get((server, fid)) is None]
    if missing:
        for fid, found in target_functions(client.get_functions_by_ids(missing)).items():
            FUNCTION_OFFSETS.set((server, fid), found)
    known: dict[int, tuple[int, str]] = {}
    for fid in function_ids:
        found = FUNCTION_OFFSETS.get((server, fid))
        if found is not None:
            known[fid] = found
    return known


def _has_default_name(function) -> bool:
    # Function.symbol synthesizes an auto sub_ symbol when none is defined at the start.
    return function.view.get_symbol_at(function.start) is None


def _target_has_default_name(node: SimilaritySessionNode, result: int) -> bool:
    match = node.get_result(result)
    if match is None:
        return False
    neighbor = _node_by_id(node, match.target.node_id)
    if neighbor is None:
        return False
    target = neighbor.get_entity_function(match.target.entity_id)
    return target is not None and _has_default_name(target)


def _entities(node: SimilaritySessionNode) -> list[Entity]:
    collected: list[Entity] = []
    for entity_id in node.entities:
        info = node.get_entity(entity_id)
        # Only functions can carry a match: BN plans more entity types (data, types), and
        # indexing those by address would pair them with functions.
        if info is None or info.type is not SimilarityEntityType.SimilarityEntityFunction:
            continue
        collected.append(Entity(entity_id, info.address, info.name))
    return collected


class _Stopped(Exception):
    """The session asked to stop; unwinds the visit."""


def _function_name(report, offset: int) -> str:
    function = smda_function(report, offset)
    if function is not None and function.function_name:
        return str(function.function_name)
    return f"sub_{offset:x}"


def _add_match_only_entities(from_node, from_view, from_report, matches, target_index) -> None:
    """Create entities for matched functions the node does not list, so they can be paired."""
    for match in matches:
        offset = match.target_offset
        if offset is None:
            log_debug(
                f"MCRIT match {match.source_offset:#x} has no target offset "
                f"for function {match.target_function_id}"
            )
            continue
        if lookup_entity(target_index, offset) is not None:
            continue
        function = from_view.get_function_at(offset)
        if function is None:
            function = from_view.get_function_at(offset & ~1)
        if function is None:
            log_debug(f"MCRIT match target {offset:#x} has no function in the target view")
            continue
        name = _function_name(from_report, offset)
        entity_id = from_node.create_entity(
            SimilarityEntityInfo(SimilarityEntityType.SimilarityEntityFunction, offset, name)
        )
        target_index[offset] = Entity(entity_id, offset, name)
        log_debug(f"Created match-only entity {entity_id} at {offset:#x}")


class McritProviderType(SimilarityProviderType):
    name = PROVIDER_NAME
    description = "MinHash/PicHash function matching via an MCRIT server"

    def get_default_settings(self):
        return default_provider_settings()

    def create(self, settings_obj):
        try:
            config = config_from_settings(settings_obj, global_defaults())
        except Exception:
            log_error("MCRIT provider settings could not be read")
            return None
        if not config.server:
            log_error("MCRIT server URL is empty")
            return None
        return McritProvider(self, config)


class McritProvider(SimilarityProvider):
    def __init__(self, provider_type: McritProviderType, config: ProviderConfig):
        super().__init__(provider_type=provider_type)
        self._lock = threading.Lock()
        # (server, sha256) -> SampleRef. Per provider, not per process: a server that was reset
        # hands out ids again, so a new session must look its samples up afresh.
        self._samples = LockedCache()
        # Edges into one node can be visited concurrently; each must see the other's new entities.
        self._entity_locks: dict[int, threading.Lock] = {}
        self._config = config
        # (target node id, target entity id) -> MCRIT label of the matched function
        self._labels: dict[tuple[int, int], str] = {}

    def _entity_lock(self, node_id: int) -> threading.Lock:
        with self._lock:
            return self._entity_locks.setdefault(node_id, threading.Lock())

    def _client(self) -> McritClient:
        with self._lock:
            config = self._config
        return McritClient(
            config.server,
            apitoken=config.api_token or None,
            username=config.username or None,
            timeout=config.timeout,
        )

    def perform_update_settings(self, settings_obj) -> bool:
        try:
            config = config_from_settings(settings_obj, global_defaults())
        except Exception:
            return False
        if not config.server:
            return False
        with self._lock:
            self._config = config
        return True

    def perform_visit_node(self, node, results, completion) -> bool:
        # All results are written from edge visits; there is nothing to do per node.
        return True

    def perform_visit_node_edge(
        self,
        from_node: SimilaritySessionNode,
        to_node: SimilaritySessionNode,
        results: SimilarityProviderResults,
        completion: SimilaritySessionCompletion,
    ) -> bool:
        try:
            return self._visit_edge(from_node, to_node, results, completion)
        except _Stopped:
            log_info("MCRIT matching stopped")
            return False
        except McritError as exc:
            # A cancelled job raises McritJobError; any other error is a real failure.
            if isinstance(exc, McritJobError) and completion.is_stop_requested:
                log_info("MCRIT matching stopped")
            else:
                log_error(str(exc))
            return False
        except Exception:
            from binaryninja.log import log_error_for_exception

            log_error_for_exception("Unhandled exception in MCRIT similarity provider")
            return False

    def _progress(
        self, completion: SimilaritySessionCompletion, node: SimilaritySessionNode, value: float
    ) -> None:
        query = SimilaritySessionCompletionQuery.for_node(node.id).with_provider(self.id)
        completion.set_progress(query, value)

    def _scale_progress(
        self,
        completion: SimilaritySessionCompletion,
        node: SimilaritySessionNode,
        start: float,
        end: float,
    ) -> Callable[[float], None]:
        def _set(fraction: float) -> None:
            clamped = max(0.0, min(1.0, fraction))
            self._progress(completion, node, start + (end - start) * clamped)

        return _set

    def _sample(self, client, config, report, should_stop, on_progress=None):
        return self._samples.get_or_set(
            (config.server, report.sha256),
            lambda: ensure_sample(
                client,
                report,
                persist=config.persist_samples,
                family=config.family,
                version=config.version,
                should_stop=should_stop,
                on_progress=on_progress,
            ),
            should_stop=should_stop,
        )

    def _corpus_payload(self, client, config, to_node, to_sample, should_stop, on_progress):
        """One MatcherSample result shared by every incoming edge of ``to_node``, or None."""
        incoming = to_node.incoming_nodes
        if len(incoming) < 2:
            return None
        try:
            sample_ids = set()
            for node in incoming:
                view = node.view
                if view is None:
                    return None
                report = export_smda_report(view, should_stop=should_stop)
                sample_ids.add(self._sample(client, config, report, should_stop).sample_id)
            key = (
                config.server,
                to_sample.sample_id,
                config.minhash_threshold,
                config.pichash_size,
                config.band_matches_required,
                frozenset(sample_ids),
            )
            return CORPUS_MATCHES.get_or_set(
                key,
                lambda: match_sample_corpus(
                    client,
                    to_sample.sample_id,
                    minhash_threshold=config.minhash_threshold,
                    pichash_size=config.pichash_size,
                    band_matches_required=config.band_matches_required,
                    should_stop=should_stop,
                    on_progress=on_progress,
                ),
                should_stop=should_stop,
            )
        except Exception as exc:
            log_warn(f"MCRIT per-node query failed, using MatcherVs for this edge: {exc}")
            return None

    def _match_payload(
        self, client, config, from_sample, to_sample, to_node, should_stop, on_progress
    ):
        payload = None
        if config.query_per_node:
            payload = self._corpus_payload(
                client, config, to_node, to_sample, should_stop, on_progress
            )
        if payload is not None:
            return payload
        if should_stop():
            raise _Stopped
        return match_sample_vs(
            client,
            to_sample.sample_id,
            from_sample.sample_id,
            minhash_threshold=config.minhash_threshold,
            pichash_size=config.pichash_size,
            band_matches_required=config.band_matches_required,
            should_stop=should_stop,
            on_progress=on_progress,
        )

    def _visit_edge(
        self,
        from_node: SimilaritySessionNode,
        to_node: SimilaritySessionNode,
        results: SimilarityProviderResults,
        completion: SimilaritySessionCompletion,
    ) -> bool:
        scheduled = set(to_node.scheduled_entities)
        if not scheduled:
            return True
        # Every .view access builds a new wrapper; fetch each once.
        from_view = from_node.view
        to_view = to_node.view
        if from_view is None or to_view is None:
            log_error("MCRIT visit requires active views on both edge endpoints")
            return False

        with self._lock:
            config = self._config

        def should_stop() -> bool:
            return completion.is_stop_requested

        def advance(progress: float) -> None:
            self._progress(completion, to_node, progress)
            if should_stop():
                raise _Stopped

        def span(start: float, end: float) -> Callable[[float], None]:
            return self._scale_progress(completion, to_node, start, end)

        advance(0.02)
        from_report = export_smda_report(from_view, should_stop=should_stop)
        advance(0.18)
        to_report = export_smda_report(to_view, should_stop=should_stop)
        advance(0.32)

        client = self._client()
        from_sample = self._sample(client, config, from_report, should_stop, span(0.32, 0.42))
        advance(0.42)
        to_sample = self._sample(client, config, to_report, should_stop, span(0.42, 0.52))
        advance(0.55)

        payload = self._match_payload(
            client, config, from_sample, to_sample, to_node, should_stop, span(0.55, 0.88)
        )
        advance(0.88)
        overrides = applied_overrides(
            payload,
            {
                "minhash_threshold": config.minhash_threshold,
                "pichash_size": config.pichash_size,
                "band_matches_required": config.band_matches_required,
            },
        )
        if overrides:
            changes = ", ".join(
                f"{key} {asked} -> {got}" for key, (asked, got) in overrides.items()
            )
            log_warn(f"MCRIT applied different matching parameters than requested: {changes}")
        matches = parse_vs_result(
            payload,
            min_score=config.minhash_threshold,
            include_library=config.include_library,
            max_results=config.max_results,
            target_sample_id=from_sample.sample_id,
        )
        targets = _target_functions(client, config.server, unique_target_function_ids(matches))
        matches = with_target_offsets(
            matches, {fid: offset for fid, (offset, _) in targets.items()}
        )
        advance(0.9)

        source_index = index_entities(_entities(to_node))
        with self._entity_lock(from_node.id):
            target_index = index_entities(_entities(from_node))
            _add_match_only_entities(from_node, from_view, from_report, matches, target_index)
        paired, unresolved = pair_matches(matches, source_index, target_index, scheduled)
        for leftover in unresolved:
            log_debug(
                f"Dropped MCRIT match {leftover.source_offset:#x} -> "
                f"{leftover.target_offset!r}: entity mapping failed"
            )

        corpus_labels = {offset: label for offset, label in targets.values() if label}
        with self._lock:
            self._labels.update(
                {
                    (from_node.id, pair.target_entity_id): corpus_labels[pair.target_offset]
                    for pair in paired
                    if pair.target_offset in corpus_labels
                }
            )

        emitted = 0
        for pair in paired:
            if should_stop():
                raise _Stopped
            result_id = results.add_result(
                SimilarityEntityRef(to_node.id, pair.source_entity_id),
                SimilarityEntityRef(from_node.id, pair.target_entity_id),
                pair.similarity,
                pair.confidence,
            )
            if result_id:
                emitted += 1

        # A partial reschedule leaves matches for functions this visit must not touch.
        wanted = sum(
            1
            for match in matches
            if (source := lookup_entity(source_index, match.source_offset)) is None
            or source.entity_id in scheduled
        )
        if wanted and emitted == 0:
            log_error(
                f"MCRIT found {wanted} function matches, but none could be mapped to Binary Ninja entities"
            )
            return False

        self._progress(completion, to_node, 1.0)
        log_info(f"MCRIT reported {emitted} function matches for this edge")
        return True

    def _label(self, target: SimilarityEntityRef) -> str:
        with self._lock:
            return self._labels.get((target.node_id, target.entity_id), "")

    def perform_get_name(self, node, entity, result) -> str | None:
        match = node.get_result(result)
        if match is None:
            return None
        target = match.target
        neighbor = _node_by_id(node, target.node_id)
        if neighbor is None:
            return None
        label = self._label(target)
        info = neighbor.get_entity(target.entity_id)
        name = info.name if info is not None and info.name else ""
        if name and (not label or label == name):
            return name
        function = neighbor.get_entity_function(target.entity_id)
        if not name and function is not None:
            name = function.name
        if not label or label == name:
            return name or None
        if not name or (function is not None and _has_default_name(function)):
            return label
        return f"{name} ({label})"

    def perform_apply(self, node, entity, result) -> SimilarityApplyStatus:
        with self._lock:
            enabled = self._config.apply_corpus_labels
        match = node.get_result(result) if enabled else None
        label = self._label(match.target) if match is not None else ""
        function = node.get_entity_function(entity) if label else None
        # A real target name is more specific than a corpus label, and the default apply copies it.
        if function is None or not _target_has_default_name(node, result):
            return super().perform_apply(node, entity, result)
        view = function.view
        start = function.start
        unnamed = view.get_symbol_at(start) is None
        status = super().perform_apply(node, entity, result)
        if status != SimilarityApplyStatus.SimilarityApplySuccess or not unnamed:
            return status
        # The default apply copies the target's name, placeholders included, as an auto symbol.
        symbol = view.get_symbol_at(start)
        if symbol is None or symbol.auto:
            function.name = label
        return status

    def perform_render(self, node, entity, context, result) -> None:
        match = node.get_result(result)
        if match is None:
            return
        source_function = node.get_entity_function(entity)
        neighbor = _node_by_id(node, match.target.node_id)
        if source_function is None or neighbor is None:
            log_warn("MCRIT render skipped: missing function or neighbor node")
            return
        target_function = neighbor.get_entity_function(match.target.entity_id)
        if target_function is None:
            log_warn("MCRIT render skipped: matched function is not loaded")
            return
        render_match(
            context,
            source_function,
            target_function,
            SimilarityEntityRef(node.id, entity),
            SimilarityEntityRef(neighbor.id, match.target.entity_id),
        )
