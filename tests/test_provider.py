from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from binaryninja.similarity import SimilarityApplyStatus, SimilarityProvider

from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.mapping import Entity
from mcrit_similarity.mcrit.parse import FunctionMatch
from mcrit_similarity.provider import McritProvider


class FakeNode:
    def __init__(self, node_id: int, view=None):
        self.id = node_id
        self.view = view
        self.incoming_nodes = []
        self.outgoing_nodes = []
        self.scheduled_entities = [1]
        self._created_entities = []
        self.get_result: Callable[..., Any] = lambda result: None
        self.get_entity: Callable[..., Any] = lambda entity_id: None
        self.get_entity_function: Callable[..., Any] = lambda entity_id: None

    def create_entity(self, info):
        self._created_entities.append(info)
        return len(self._created_entities) + 100


class FakeResults:
    def __init__(self):
        self.results = []

    def add_result(self, src, tgt, similarity, confidence):
        self.results.append((src, tgt, similarity, confidence))
        return len(self.results)


class FakeCompletion:
    def __init__(self, stop: bool = False):
        self.is_stop_requested = stop
        self.progress = 0.0

    def set_progress(self, *args, **kwargs):
        pass


def test_perform_visit_node_returns_true(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    assert provider.perform_visit_node(Mock(), Mock(), Mock()) is True


def test_visit_edge_cancelled_returns_false(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    from_node = FakeNode(1, Mock())
    to_node = FakeNode(2, Mock())
    completion = FakeCompletion(stop=True)
    assert provider.perform_visit_node_edge(from_node, to_node, FakeResults(), completion) is False


def test_partial_reschedule_without_matches_for_scheduled_functions_succeeds(provider_type):
    """Matches for unscheduled functions are not a mapping failure."""
    provider = McritProvider(provider_type, ProviderConfig())
    from_node = FakeNode(1, Mock())
    to_node = FakeNode(2, Mock())
    to_node.scheduled_entities = [1]
    match = FunctionMatch(
        source_offset=0x1000,
        target_offset=0x2000,
        source_function_id=10,
        target_function_id=20,
        target_sample_id=1,
        target_family_id=1,
        score=90.0,
        flags=0,
        num_bytes=50.0,
    )
    results = FakeResults()
    with (
        patch("mcrit_similarity.provider.export_smda_report"),
        patch("mcrit_similarity.provider.ensure_sample"),
        patch("mcrit_similarity.provider.match_sample_vs"),
        patch("mcrit_similarity.provider.parse_vs_result", return_value=[match]),
        patch("mcrit_similarity.provider._target_functions", return_value={20: (0x2000, "")}),
        patch(
            "mcrit_similarity.provider._entities",
            side_effect=[[Entity(2, 0x1000, "unscheduled")], [Entity(7, 0x2000, "target")]],
        ),
    ):
        assert provider._visit_edge(from_node, to_node, results, FakeCompletion(stop=False))
    assert results.results == []


def test_visit_edge_mapping_failure_returns_false(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    from_view = Mock()
    from_view.get_function_at.return_value = None
    to_view = Mock()
    to_view.get_function_at.return_value = None

    from_node = FakeNode(1, from_view)
    to_node = FakeNode(2, to_view)
    completion = FakeCompletion(stop=False)
    results = FakeResults()

    match = FunctionMatch(
        source_offset=0x1000,
        target_offset=0x2000,
        source_function_id=10,
        target_function_id=20,
        target_sample_id=1,
        target_family_id=1,
        score=100.0,
        flags=0,
        num_bytes=50.0,
    )

    with (
        patch("mcrit_similarity.provider.export_smda_report"),
        patch("mcrit_similarity.provider.ensure_sample"),
        patch("mcrit_similarity.provider.match_sample_vs"),
        patch("mcrit_similarity.provider.parse_vs_result", return_value=[match]),
        patch("mcrit_similarity.provider._target_functions", return_value={20: (0x2000, "")}),
        patch("mcrit_similarity.provider._entities", return_value=[]),
    ):
        # Because neither function can be mapped to a BN function/entity, emitted == 0
        success = provider._visit_edge(from_node, to_node, results, completion)
        assert success is False
        assert len(results.results) == 0
        # And because from_view.get_function_at is None, no ghost entity was created
        assert len(from_node._created_entities) == 0


def test_target_functions_are_cached_per_server_and_fetched_only_when_missing():
    from mcrit_similarity.provider import _target_functions

    label = [{"function_label": "rand", "username": "u", "timestamp": "2026-01-01T00:00:00"}]
    client = Mock()
    client.get_functions_by_ids.return_value = {
        30: {"offset": 0x3000, "function_labels": label},
        31: {"offset": 0x3100, "function_labels": []},
    }
    assert _target_functions(client, "http://a/", [30, 31]) == {
        30: (0x3000, "rand"),
        31: (0x3100, ""),
    }
    client.get_functions_by_ids.assert_called_once_with([30, 31])

    client.get_functions_by_ids.reset_mock()
    assert _target_functions(client, "http://a/", [30]) == {30: (0x3000, "rand")}
    client.get_functions_by_ids.assert_not_called()

    # The same function id on another server is a different function.
    client.get_functions_by_ids.return_value = {30: {"offset": 0x9000}}
    assert _target_functions(client, "http://b/", [30]) == {30: (0x9000, "")}


def _visit_with_label(provider, label="CryptEncrypt_wrapper"):
    from_view = Mock()
    from_view.get_function_at.return_value = None
    from_node = FakeNode(1, from_view)
    to_node = FakeNode(2, Mock())
    match = FunctionMatch(
        source_offset=0x1000,
        target_offset=0x2000,
        source_function_id=10,
        target_function_id=20,
        target_sample_id=1,
        target_family_id=1,
        score=90.0,
        flags=1,
        num_bytes=100.0,
    )
    entities = {2: [Entity(1, 0x1000, "sub_1000")], 1: [Entity(7, 0x2000, "sub_2000")]}
    with (
        patch("mcrit_similarity.provider.export_smda_report"),
        patch("mcrit_similarity.provider.ensure_sample"),
        patch("mcrit_similarity.provider.match_sample_vs"),
        patch("mcrit_similarity.provider.parse_vs_result", return_value=[match]),
        patch("mcrit_similarity.provider._target_functions", return_value={20: (0x2000, label)}),
        patch("mcrit_similarity.provider._entities", side_effect=lambda node: entities[node.id]),
    ):
        results = FakeResults()
        assert provider._visit_edge(from_node, to_node, results, FakeCompletion()) is True
    assert len(results.results) == 1
    return from_node


class NamedFunction:
    """A function whose view reports a defined symbol (auto or user) or none at its start."""

    def __init__(self, name, symbol=None):
        self._name = name
        self.start = 0x1000
        self.view = Mock()
        self.view.get_symbol_at.return_value = symbol
        self.renamed_to = None

    @property
    def name(self):
        return self.renamed_to or self._name

    @name.setter
    def name(self, value):
        self.renamed_to = value


AUTO = SimpleNamespace(auto=True)
USER = SimpleNamespace(auto=False)


def _session_node(target_node, target_name, target_function):
    target_node.get_entity = lambda entity_id: SimpleNamespace(name=target_name)
    target_node.get_entity_function = lambda entity_id: target_function
    node = FakeNode(2)
    node.outgoing_nodes = [target_node]
    node.get_result = lambda result: Mock(target=Mock(node_id=1, entity_id=7))
    return node


def test_visit_records_label_for_target_entity(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    _visit_with_label(provider)
    assert provider._labels == {(1, 7): "CryptEncrypt_wrapper"}


def test_get_name_shows_label_for_default_names_and_appends_it_to_real_symbols(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    target = _visit_with_label(provider)
    auto = _session_node(target, "sub_2000", NamedFunction("sub_2000"))
    assert provider.perform_get_name(auto, 1, 5) == "CryptEncrypt_wrapper"
    named = _session_node(target, "encrypt", NamedFunction("encrypt", AUTO))
    assert provider.perform_get_name(named, 1, 5) == "encrypt (CryptEncrypt_wrapper)"
    same = _session_node(target, "CryptEncrypt_wrapper", None)
    assert provider.perform_get_name(same, 1, 5) == "CryptEncrypt_wrapper"


def test_get_name_without_label_keeps_binary_ninja_name(provider_type):
    provider = McritProvider(provider_type, ProviderConfig())
    target = FakeNode(1)
    node = _session_node(target, "", NamedFunction("sub_2000"))
    assert provider.perform_get_name(node, 1, 5) == "sub_2000"


def _apply(provider_type, config, *, before=None, after=None, base_status=None, target_symbol=None):
    """Apply with the source's symbol state before and after the default metadata transfer."""
    provider = McritProvider(provider_type, config)
    target = _visit_with_label(provider)
    source = NamedFunction("sub_1000")
    source.view.get_symbol_at.side_effect = [before, after]

    def base_apply(node, entity, result):
        return base_status or SimilarityApplyStatus.SimilarityApplySuccess

    node = _session_node(target, "sub_2000", NamedFunction("sub_2000", target_symbol))
    node.get_entity_function = lambda entity_id: source
    with patch.object(SimilarityProvider, "perform_apply", side_effect=base_apply):
        return provider.perform_apply(node, 1, 5), source.renamed_to


def test_apply_renames_unnamed_source_only_when_enabled(provider_type):
    ok = SimilarityApplyStatus.SimilarityApplySuccess
    on = ProviderConfig(apply_corpus_labels=True)
    assert _apply(provider_type, ProviderConfig()) == (ok, None)
    assert _apply(provider_type, on) == (ok, "CryptEncrypt_wrapper")
    # The default apply copied the target's name as an auto symbol: the label replaces it.
    assert _apply(provider_type, on, after=AUTO) == (ok, "CryptEncrypt_wrapper")
    assert _apply(provider_type, on, after=USER) == (ok, None)
    assert _apply(provider_type, on, before=AUTO) == (ok, None)
    # A real target name is what the default apply copies; the label must not replace it.
    assert _apply(provider_type, on, target_symbol=AUTO) == (ok, None)
    assert _apply(provider_type, on, target_symbol=USER) == (ok, None)
    failed = SimilarityApplyStatus.SimilarityApplyFailed
    assert _apply(provider_type, on, base_status=failed) == (failed, None)
