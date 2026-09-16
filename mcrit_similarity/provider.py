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
from mcrit_similarity.cache import FUNCTION_OFFSETS, SAMPLES, LockedCache
from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.errors import McritAuthError, McritError, McritJobError
from mcrit_similarity.export import export_smda_report
from mcrit_similarity.log import log_debug, log_error, log_info, log_warn
from mcrit_similarity.mapping import Entity, index_entities, lookup_entity, pair_matches
from mcrit_similarity.mcrit.client import McritClient
from mcrit_similarity.mcrit.parse import (
    parse_vs_result,
    target_functions,
    unique_target_function_ids,
    with_target_offsets,
)
from mcrit_similarity.mcrit.samples import ensure_sample, match_sample_corpus, match_sample_vs
from mcrit_similarity.render import render_match
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
    neighbor = _node_by_id(node, match.target.node_id) if match is not None else None
    if neighbor is None:
        return False
    target = neighbor.get_entity_function(match.target.entity_id)
    return target is not None and _has_default_name(target)


def _entities(node: SimilaritySessionNode) -> list[Entity]:
    collected: list[Entity] = []
    for entity_id in node.entities:
        info = node.get_entity(entity_id)
        if info is None:
            continue
        collected.append(Entity(entity_id, info.address, info.name))
    return collected


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
        self._config = config
        # (target node id, target entity id) -> MCRIT label of the matched function
        self._labels: dict[tuple[int, int], str] = {}

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
        except McritJobError as exc:
            if completion.is_stop_requested:
                log_info("MCRIT matching stopped")
                return False
            log_error(str(exc))
            return False
        except McritAuthError as exc:
            log_error(str(exc))
            return False
        except McritError as exc:
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
        return SAMPLES.get_or_set(
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

    def _visit_edge(
        self,
        from_node: SimilaritySessionNode,
        to_node: SimilaritySessionNode,
        results: SimilarityProviderResults,
        completion: SimilaritySessionCompletion,
    ) -> bool:
        scheduled = list(to_node.scheduled_entities)
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

        self._progress(completion, to_node, 0.02)

        from_report = export_smda_report(from_view, should_stop=should_stop)
        self._progress(completion, to_node, 0.18)
        if completion.is_stop_requested:
            return False

        to_report = export_smda_report(to_view, should_stop=should_stop)
        self._progress(completion, to_node, 0.32)
        if completion.is_stop_requested:
            return False

        client = self._client()
        from_sample = self._sample(
            client,
            config,
            from_report,
            should_stop,
            self._scale_progress(completion, to_node, 0.32, 0.42),
        )
        if completion.is_stop_requested:
            return False

        to_sample = self._sample(
            client,
            config,
            to_report,
            should_stop,
            self._scale_progress(completion, to_node, 0.42, 0.52),
        )
        self._progress(completion, to_node, 0.55)
        if completion.is_stop_requested:
            return False

        match_progress = self._scale_progress(completion, to_node, 0.55, 0.88)
        payload = None
        if config.query_per_node:
            payload = self._corpus_payload(
                client, config, to_node, to_sample, should_stop, match_progress
            )
            if completion.is_stop_requested:
                return False
        if payload is None:
            payload = match_sample_vs(
                client,
                to_sample.sample_id,
                from_sample.sample_id,
                minhash_threshold=config.minhash_threshold,
                pichash_size=config.pichash_size,
                band_matches_required=config.band_matches_required,
                should_stop=should_stop,
                on_progress=match_progress,
            )
        if completion.is_stop_requested:
            return False

        matches = parse_vs_result(
            payload,
            min_score=config.minhash_threshold,
            include_library=config.include_library,
            max_results=config.max_results,
            target_sample_id=from_sample.sample_id,
        )
        if completion.is_stop_requested:
            return False

        targets = _target_functions(client, config.server, unique_target_function_ids(matches))
        matches = with_target_offsets(
            matches, {fid: offset for fid, (offset, _) in targets.items()}
        )
        label_by_offset = {offset: label for offset, label in targets.values() if label}
        self._progress(completion, to_node, 0.9)
        if completion.is_stop_requested:
            return False

        source_index = index_entities(_entities(to_node))
        target_index = index_entities(_entities(from_node))
        for match in matches:
            if match.target_offset is None:
                log_debug(
                    f"MCRIT match {match.source_offset:#x} has no target offset for function {match.target_function_id}"
                )
                continue
            if lookup_entity(target_index, match.target_offset) is None:
                get_fn = getattr(from_view, "get_function_at", None)
                target_fn = get_fn(match.target_offset) if get_fn is not None else None
                if target_fn is None and get_fn is not None:
                    target_fn = get_fn(match.target_offset & ~1)
                if target_fn is None:
                    log_debug(
                        f"MCRIT match target offset {match.target_offset:#x} has no function in target view; skipping entity creation"
                    )
                    continue
                name = _function_name(from_report, match.target_offset) or getattr(
                    target_fn, "name", None
                )
                entity_id = from_node.create_entity(
                    SimilarityEntityInfo(
                        SimilarityEntityType.SimilarityEntityFunction,
                        match.target_offset,
                        name,
                    )
                )
                target_index[match.target_offset] = Entity(
                    entity_id, match.target_offset, name or ""
                )
                log_debug(f"Created match-only entity {entity_id} at {match.target_offset:#x}")

        if completion.is_stop_requested:
            return False

        scheduled_ids = set(scheduled)
        paired, unresolved = pair_matches(matches, source_index, target_index, scheduled_ids)
        for leftover in unresolved:
            log_debug(
                f"Dropped MCRIT match {leftover.source_offset:#x} -> "
                f"{leftover.target_offset!r}: entity mapping failed"
            )

        target_node_id = from_node.id
        labels = {
            (target_node_id, pair.target_entity_id): label_by_offset[pair.target_offset]
            for pair in paired
            if pair.target_offset in label_by_offset
        }
        if labels:
            with self._lock:
                self._labels.update(labels)

        emitted = 0
        for pair in paired:
            if completion.is_stop_requested:
                return False
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
            or source.entity_id in scheduled_ids
        )
        if wanted and emitted == 0:
            log_error(
                f"MCRIT found {wanted} function matches, but none could be mapped to Binary Ninja entities"
            )
            return False

        self._progress(completion, to_node, 1.0)
        log_info(f"MCRIT reported {emitted} function matches for this edge")
        return True

    def _label_for(self, node, result) -> str:
        match = node.get_result(result)
        if match is None:
            return ""
        with self._lock:
            return self._labels.get((match.target.node_id, match.target.entity_id), "")

    def perform_get_name(self, node, entity, result) -> str | None:
        match = node.get_result(result)
        if match is None:
            return None
        target = match.target
        neighbor = _node_by_id(node, target.node_id)
        if neighbor is None:
            return None
        with self._lock:
            label = self._labels.get((target.node_id, target.entity_id), "")
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
        label = self._label_for(node, result) if enabled else ""
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


def _function_name(report, offset: int) -> str:
    getter = getattr(report, "getFunction", None)
    if getter is not None:
        function = getter(offset) or getter(offset & ~1)
        name = getattr(function, "function_name", None) if function is not None else None
        if name:
            return str(name)
    symbols = getattr(report, "function_symbols", None) or {}
    return str(symbols.get(offset) or symbols.get(offset & ~1) or f"sub_{offset:x}")
