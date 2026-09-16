from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from mcrit_similarity.config import ProviderConfig
from mcrit_similarity.mcrit.parse import parse_vs_result
from mcrit_similarity.mcrit.samples import SampleRef
from mcrit_similarity.provider import CORPUS_MATCHES, McritProvider

ON = ProviderConfig(query_per_node=True)


@pytest.fixture(autouse=True)
def _clear_cache():
    CORPUS_MATCHES._items.clear()
    yield
    CORPUS_MATCHES._items.clear()


def _node(sha, view=True):
    return SimpleNamespace(view=SimpleNamespace(sha=sha) if view else None)


def _run(provider_type, config, incoming, samples=None, corpus=None):
    """Call _corpus_payload with patched export/sample resolution; returns (payload, corpus mock)."""
    samples = samples if samples is not None else {"a": 1, "c": 5}
    corpus = corpus or Mock(return_value={"matches": {}, "info": {}})

    def sample_for(client, report, **kwargs):
        if report.sha256 not in samples:
            raise RuntimeError("not on server")
        return SampleRef(samples[report.sha256], report.sha256, True)

    provider = McritProvider(provider_type, config)
    with (
        patch(
            "mcrit_similarity.provider.export_smda_report",
            side_effect=lambda view, **kw: SimpleNamespace(sha256=view.sha),
        ),
        patch("mcrit_similarity.provider.ensure_sample", side_effect=sample_for),
        patch("mcrit_similarity.provider.match_sample_corpus", corpus),
    ):
        payload = provider._corpus_payload(
            Mock(),
            config,
            SimpleNamespace(incoming_nodes=incoming),
            SampleRef(2, "b", True),
            lambda: False,
            None,
        )
    return payload, corpus


@pytest.mark.parametrize(
    "incoming, samples",
    [
        ([_node("a")], None),
        ([_node("a"), _node("c", view=False)], None),
        ([_node("a"), _node("missing")], None),
    ],
    ids=["single-incoming", "inactive-view", "unresolvable-sample"],
)
def test_falls_back_to_matcher_vs(provider_type, incoming, samples):
    payload, corpus = _run(provider_type, ON, incoming, samples)
    assert payload is None
    corpus.assert_not_called()


def test_query_error_falls_back(provider_type):
    payload, _ = _run(
        provider_type, ON, [_node("a"), _node("c")], corpus=Mock(side_effect=RuntimeError("big"))
    )
    assert payload is None


def test_corpus_result_is_shared_across_incoming_edges(provider_type):
    incoming = [_node("a"), _node("c")]
    corpus = Mock(return_value={"matches": {}, "info": {}})
    first, _ = _run(provider_type, ON, incoming, corpus=corpus)
    second, _ = _run(provider_type, ON, incoming, corpus=corpus)
    assert first is second
    corpus.assert_called_once()
    # A different incoming set is a different query.
    _run(
        provider_type,
        ON,
        [_node("a"), _node("c"), _node("d")],
        {"a": 1, "c": 5, "d": 6},
        corpus=corpus,
    )
    assert corpus.call_count == 2


@pytest.mark.parametrize(
    "config, corpus_result, uses_corpus, uses_vs",
    [
        (ProviderConfig(), {"x": 1}, False, True),
        (ON, {"x": 1}, True, False),
        (ON, None, True, True),
    ],
    ids=["setting-off", "corpus-used", "corpus-fallback"],
)
def test_visit_chooses_query(provider_type, config, corpus_result, uses_corpus, uses_vs):
    provider = McritProvider(provider_type, config)
    with (
        patch.object(provider, "_corpus_payload", return_value=corpus_result) as corpus,
        patch("mcrit_similarity.provider.export_smda_report"),
        patch("mcrit_similarity.provider.ensure_sample"),
        patch("mcrit_similarity.provider.match_sample_vs") as vs,
        patch("mcrit_similarity.provider.parse_vs_result", return_value=[]) as parse,
        patch("mcrit_similarity.provider._entities", return_value=[]),
    ):
        node = SimpleNamespace(id=1, view=Mock(), scheduled_entities=[1])
        completion = SimpleNamespace(is_stop_requested=False, set_progress=lambda *a: None)
        assert provider._visit_edge(node, node, Mock(), completion) is True
    assert corpus.called == uses_corpus
    assert vs.called == uses_vs
    expected = vs.return_value if uses_vs else corpus_result
    assert parse.call_args.args[0] is expected


def test_slicing_corpus_result_equals_vs_result(vs_result):
    # The fixture holds candidates from samples 1 and 3; MatcherVs against 1 holds only sample 1.
    vs_only = {
        **vs_result,
        "matches": {
            "functions": [
                {**summary, "matches": [m for m in summary["matches"] if m[1] == 1]}
                for summary in vs_result["matches"]["functions"]
            ]
        },
    }
    sliced = parse_vs_result(vs_result, target_sample_id=1, max_results=5)
    direct = parse_vs_result(vs_only, target_sample_id=1, max_results=5)
    assert sliced == direct
