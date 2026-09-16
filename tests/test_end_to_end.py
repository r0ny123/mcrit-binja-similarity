"""The plugin against a real MCRIT server (MCRIT_TEST_REAL_BINARYNINJA=1 and MCRIT_TEST_E2E=1).

Binary Ninja analyses the zlib fixtures, the provider exports and uploads them, and the server
answers real MatcherVs results. The module-scoped session reuses one server and empties it with
POST /respawn, because a server start plus two analysed views costs far more than the reset;
every test that mutates a server otherwise launches one of its own and stops it again.
"""

from __future__ import annotations

import gc
import runpy
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

import binaryninja as bn
import pytest
import requests

from mcrit_similarity import provider as provider_module
from mcrit_similarity.cache import FUNCTION_OFFSETS, SAMPLES
from mcrit_similarity.export import export_smda_report
from tests.conftest import FIXTURES, analysed_views

if not hasattr(bn, "SimilaritySession"):
    pytest.skip("needs the real Binary Ninja API, not the unit-test stub", allow_module_level=True)

# Symbols every fixture build exports, big enough to stay above the default MinHash threshold
# between -O2 and -Os. _adler32 and _crc32 are there too, but their two builds score below it.
KNOWN_FUNCTIONS = ("_deflate", "_inflate")
# The two builds differ only in optimisation level, so a large majority of functions must match.
MIN_RESULTS = 20
SESSION_TIMEOUT = 600
REQUEST_TIMEOUT = 2

pytestmark = [
    pytest.mark.binaryninja,
    pytest.mark.e2e,
    pytest.mark.skipif(
        "ultimate" not in (bn.core_product() or "").lower(),
        reason="Binary Similarity needs Binary Ninja Ultimate",
    ),
]


def _provider_type():
    from mcrit_similarity.provider import McritProviderType

    for registered in bn.similarity.SimilarityProviderType._registered_types:
        if isinstance(registered, McritProviderType):
            return registered
    pytest.skip("the MCRIT provider is only registered on Binary Ninja Ultimate")


def _make_provider(server: str, **overrides: Any):
    provider_type = _provider_type()
    settings = provider_type.get_default_settings()
    values: dict[str, Any] = {
        "server": server,
        "persist_samples": True,
        "family": "zlib",
        "version": "e2e",
        **overrides,
    }
    for key, value in values.items():
        full = f"mcrit.{key}"
        if isinstance(value, bool):
            settings.set_bool(full, value)
        elif isinstance(value, int):
            settings.set_integer(full, value)
        else:
            settings.set_string(full, value)
    provider = provider_type.create(settings)
    assert provider is not None, "the MCRIT provider could not be created"
    return provider


def _forget_servers() -> None:
    """Drop what the plugin remembers about any server; the exported reports stay cached."""
    for cache in (SAMPLES, FUNCTION_OFFSETS, provider_module.CORPUS_MATCHES):
        cache.clear()


def _reset_server(server: str) -> None:
    requests.post(f"{server}respawn", timeout=120).raise_for_status()
    _forget_servers()


def _server_samples(server: str) -> dict[str, dict[str, Any]]:
    response = requests.get(f"{server}samples", timeout=60)
    response.raise_for_status()
    return {entry["sha256"]: entry for entry in response.json()["data"].values()}


def _await(completion, timeout: float = SESSION_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not completion.is_finished and time.monotonic() < deadline:
        time.sleep(0.1)
    if not completion.is_finished:
        completion.request_stop()
        deadline = time.monotonic() + 30
        while not completion.is_finished and time.monotonic() < deadline:
            time.sleep(0.1)
        pytest.fail(f"the similarity session did not finish within {timeout}s")


def _run_edges(provider, edges):
    """Run one session over ``edges`` (pairs of nodes) and wait for it.

    The returned session owns the results, so a caller reading them has to keep it alive.
    """
    session = bn.SimilaritySession()
    session.add_provider(provider)
    nodes = {id(node): node for pair in edges for node in pair}
    for node in nodes.values():
        session.graph.add_node(node)
    for from_node, to_node in edges:
        session.graph.add_edge(from_node, to_node)
    _await(session.run())
    return session


def _results(node) -> list[tuple[Any, Any, Any]]:
    """(entity id, result id, result) for every result on ``node``."""
    collected = []
    for entity_id in node.entities:
        for result_id in node.get_results(entity_id):
            result = node.get_result(result_id)
            if result is not None:
                collected.append((entity_id, result_id, result))
    return collected


def _entity_name(node, entity_id) -> str:
    info = node.get_entity(entity_id)
    if info is not None and info.name:
        return str(info.name)
    function = node.get_entity_function(entity_id)
    return function.name if function is not None else ""


def _instruction_ranges(function) -> set[tuple[int, int]]:
    view = function.view
    ranges: set[tuple[int, int]] = set()
    for block in function.basic_blocks:
        ranges.add((block.start, block.end))
        address = block.start
        while address < block.end:
            length = view.get_instruction_length(address, block.arch)
            if not length:
                break
            ranges.add((address, address + length))
            address += length
    return ranges


@dataclass
class Matched:
    reference: Any
    target: Any
    reference_node: Any
    target_node: Any
    provider: Any
    session: Any
    server: str
    samples_before: int
    arch: str


@pytest.fixture(scope="module", params=["x86_64", "arm64"])
def matched(request, mcrit_server):
    """One finished O2 -> Os session per architecture, against a freshly reset server."""
    arch = request.param
    _reset_server(mcrit_server)
    provider = _make_provider(mcrit_server, version=f"e2e-{arch}")
    before = len(_server_samples(mcrit_server))
    with analysed_views(f"zlib-{arch}-O2", f"zlib-{arch}-Os") as (reference, target):
        reference_node = bn.SimilaritySessionNode(reference)
        target_node = bn.SimilaritySessionNode(target)
        session = _run_edges(provider, [(reference_node, target_node)])
        yield Matched(
            reference=reference,
            target=target,
            reference_node=reference_node,
            target_node=target_node,
            provider=provider,
            session=session,
            server=mcrit_server,
            samples_before=before,
            arch=arch,
        )
        # Drop the session objects while the views are still open, as the headless example does.
        del session, reference_node, target_node, provider
        gc.collect()


def test_session_uploads_and_indexes_both_samples(matched):
    assert matched.samples_before == 0, "the E2E server must start without samples"
    stored = _server_samples(matched.server)
    assert len(stored) == 2
    for view in (matched.reference, matched.target):
        report: Any = export_smda_report(view)
        entry = stored[report.sha256]
        assert entry["statistics"]["num_functions"] == len(list(report.getFunctions()))
        assert entry["family"] == "zlib"


def test_matching_produces_results_within_the_score_range(matched):
    results = _results(matched.target_node)
    assert len(results) >= MIN_RESULTS
    for _entity_id, _result_id, result in results:
        assert 0 <= result.similarity <= 255
        assert 0 <= result.confidence <= 255
        # A PicHash match is scored 255 or 128; anything else is scaled down from the similarity.
        assert result.confidence in (128, 255) or result.confidence <= result.similarity


def test_known_functions_match_their_namesake(matched):
    matches: dict[str, set[str]] = {}
    for entity_id, result_id, result in _results(matched.target_node):
        source = _entity_name(matched.target_node, entity_id)
        target = _entity_name(matched.reference_node, result.target.entity_id)
        matches.setdefault(source, set()).add(target)
        label = matched.provider.get_name(matched.target_node, entity_id, result_id)
        assert label, f"no name for the match of {source}"
    for name in KNOWN_FUNCTIONS:
        assert name in matches, f"{name} produced no MCRIT match"
        assert name in matches[name], f"{name} did not match {name}: {sorted(matches[name])}"

    best: dict[str, tuple[int, str]] = {}
    for entity_id, _result_id, result in _results(matched.target_node):
        source = _entity_name(matched.target_node, entity_id)
        target = _entity_name(matched.reference_node, result.target.entity_id)
        if result.similarity >= best.get(source, (-1, ""))[0]:
            best[source] = (result.similarity, target)
    agreeing = sum(1 for source, (_score, target) in best.items() if source == target)
    # Inlining differs between -O2 and -Os, so a few best matches legitimately land on a
    # neighbouring function; a majority pointing elsewhere would mean the matching is wrong.
    assert agreeing >= 0.6 * len(best), f"only {agreeing} of {len(best)} best matches agree by name"


def test_render_paints_both_sides_of_a_matched_pair(matched):
    entity_id, result_id, result = next(
        (entity, result_id, result)
        for entity, result_id, result in _results(matched.target_node)
        if _entity_name(matched.target_node, entity) == "_deflate"
    )
    source = matched.target_node.get_entity_function(entity_id)
    target = matched.reference_node.get_entity_function(result.target.entity_id)
    assert source is not None and target is not None

    painted: dict[int, list[tuple[int, int, Any]]] = {}
    # Keyed by id(): keep every renderer alive so a freed one cannot hand its id to the next.
    renderers: list[Any] = []
    original = bn.similarity.DiffRenderer.add_range_annotation

    def record(renderer, annotation):
        if id(renderer) not in painted:
            renderers.append(renderer)
        painted.setdefault(id(renderer), []).append(
            (annotation.start, annotation.end, annotation.type)
        )
        return original(renderer, annotation)

    bn.similarity.DiffRenderer.add_range_annotation = record
    try:
        context = bn.similarity.SimilarityRenderContext()
        matched.provider.render(matched.target_node, entity_id, context, result_id)
    finally:
        bn.similarity.DiffRenderer.add_range_annotation = original

    rendered = {(view.entity.node_id, view.entity.entity_id) for view in context.views}
    assert rendered == {
        (matched.target_node.id, entity_id),
        (matched.reference_node.id, result.target.entity_id),
    }
    assert len(painted) == 2, "the two builds of _deflate differ, so both sides are painted"
    changed = bn.enums.SimilarityAnnotationType.SimilarityAnnotationChanged
    for painted_ranges, function in zip(painted.values(), (source, target), strict=True):
        ranges = _instruction_ranges(function)
        for start, end, kind in painted_ranges:
            assert (start, end) in ranges
            if start <= function.start < end:
                assert kind == changed, "the function header must not look removed or added"


def test_second_session_reuses_the_uploaded_samples(matched):
    first = {
        (
            _entity_name(matched.target_node, entity_id),
            _entity_name(matched.reference_node, result.target.entity_id),
            result.similarity,
            result.confidence,
        )
        for entity_id, _result_id, result in _results(matched.target_node)
    }
    stored = _server_samples(matched.server)
    # A second provider and session, but the same server, and without the sample ids the first
    # run cached: ensure_sample has to find both samples on the server instead of uploading them.
    _forget_servers()
    session = None
    provider = _make_provider(matched.server, version="e2e-again")
    reference_node = bn.SimilaritySessionNode(matched.reference)
    target_node = bn.SimilaritySessionNode(matched.target)
    try:
        session = _run_edges(provider, [(reference_node, target_node)])
        second = {
            (
                _entity_name(target_node, entity_id),
                _entity_name(reference_node, result.target.entity_id),
                result.similarity,
                result.confidence,
            )
            for entity_id, _result_id, result in _results(target_node)
        }
        assert second == first
        assert _server_samples(matched.server).keys() == stored.keys()
    finally:
        del session, reference_node, target_node, provider
        gc.collect()


@pytest.fixture
def own_servers(mcrit_server_factory):
    """Servers this test alone owns, stopped as soon as it ends rather than at session teardown."""
    handles = []

    def start(**environment: str) -> str:
        handles.append(mcrit_server_factory(**environment))
        return handles[-1].url

    yield start
    for handle in handles:
        handle.stop()
    _forget_servers()


@pytest.fixture
def own_server(own_servers):
    """Resetting the shared server here would pull it out from under the module fixture."""
    return own_servers()


@pytest.fixture
def silent_server():
    """A listening socket that never accepts, so a request hangs until the client times out."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/"
    finally:
        listener.close()


@pytest.fixture
def short_request_timeout():
    """The HTTP timeout is a global setting, not part of the provider settings object."""
    settings = bn.Settings()
    settings.set_integer("mcrit.timeout", REQUEST_TIMEOUT)
    yield REQUEST_TIMEOUT
    settings.reset("mcrit.timeout")


@pytest.fixture
def x86_views():
    """The x86_64 -O2 and -Os builds, closed when the test ends."""
    with analysed_views("zlib-x86_64-O2", "zlib-x86_64-Os") as opened:
        yield opened
        gc.collect()


def test_unknown_sample_without_persist_fails_the_visit(x86_views, own_server, caplog):
    reference, target = x86_views
    session = None
    provider = _make_provider(own_server, persist_samples=False)
    reference_node = bn.SimilaritySessionNode(reference)
    target_node = bn.SimilaritySessionNode(target)
    try:
        with caplog.at_level("ERROR", logger="MCRIT"):
            session = _run_edges(provider, [(reference_node, target_node)])
        assert "enable Persist samples" in caplog.text
        assert not _results(target_node)
        assert not _server_samples(own_server)
    finally:
        del session, reference_node, target_node, provider
        gc.collect()


def test_an_unresponsive_server_fails_the_visit_without_hanging(
    x86_views, silent_server, short_request_timeout, caplog
):
    reference, target = x86_views
    # Export first, so the measured time is the request the provider hangs on and nothing else.
    for view in x86_views:
        export_smda_report(view)
    session = None
    provider = _make_provider(silent_server)
    reference_node = bn.SimilaritySessionNode(reference)
    target_node = bn.SimilaritySessionNode(target)
    try:
        started = time.monotonic()
        with caplog.at_level("ERROR", logger="MCRIT"):
            session = _run_edges(provider, [(reference_node, target_node)])
        elapsed = time.monotonic() - started
        assert elapsed < 5 * short_request_timeout, f"the visit hung for {elapsed:.1f}s"
        assert "MCRIT request failed" in caplog.text
        assert not _results(target_node)
    finally:
        _forget_servers()
        del session, reference_node, target_node, provider
        gc.collect()


def test_a_failing_matches_endpoint_is_reported(x86_views, own_servers, caplog):
    # 500 is outside the retried set (502, 503, 504), so one injected failure ends the visit.
    server = own_servers(MCRIT_E2E_FAIL_PATH="/matches", MCRIT_E2E_FAIL_COUNT="1")
    reference, target = x86_views
    session = None
    provider = _make_provider(server)
    reference_node = bn.SimilaritySessionNode(reference)
    target_node = bn.SimilaritySessionNode(target)
    try:
        with caplog.at_level("ERROR", logger="MCRIT"):
            session = _run_edges(provider, [(reference_node, target_node)])
        assert "MCRIT server error HTTP 500" in caplog.text
        assert not _results(target_node)
        # The failure is in matching only, so both samples did reach the server.
        assert len(_server_samples(server)) == 2
    finally:
        _forget_servers()
        del session, reference_node, target_node, provider
        gc.collect()


def test_a_read_path_survives_two_unavailable_answers(x86_views, own_servers):
    # The sample lookup opens every visit, and 503 is retried, so the session must still finish.
    server = own_servers(
        MCRIT_E2E_FAIL_PATH="/samples/sha256",
        MCRIT_E2E_FAIL_COUNT="2",
        MCRIT_E2E_FAIL_STATUS="503",
    )
    reference, target = x86_views
    session = None
    provider = _make_provider(server)
    reference_node = bn.SimilaritySessionNode(reference)
    target_node = bn.SimilaritySessionNode(target)
    try:
        session = _run_edges(provider, [(reference_node, target_node)])
        assert len(_results(target_node)) >= MIN_RESULTS
        assert len(_server_samples(server)) == 2
    finally:
        _forget_servers()
        del session, reference_node, target_node, provider
        gc.collect()


def test_stopping_a_running_session_finishes_it(x86_views, own_server):
    reference, target = x86_views
    session = None
    provider = _make_provider(own_server)
    reference_node = bn.SimilaritySessionNode(reference)
    target_node = bn.SimilaritySessionNode(target)
    session = bn.SimilaritySession()
    session.add_provider(provider)
    try:
        session.graph.add_node(reference_node)
        session.graph.add_node(target_node)
        session.graph.add_edge(reference_node, target_node)
        completion = session.run()
        completion.request_stop()
        _await(completion, timeout=120)
        assert completion.is_stop_requested
    finally:
        del session, reference_node, target_node, provider
        gc.collect()


def test_query_per_node_serves_two_incoming_nodes(x86_views, own_server):
    reference, target = x86_views
    with analysed_views("zlib-arm64-O2") as (other,):
        session = None
        provider = _make_provider(own_server, query_per_node=True)
        reference_node = bn.SimilaritySessionNode(reference)
        other_node = bn.SimilaritySessionNode(other)
        target_node = bn.SimilaritySessionNode(target)
        try:
            session = _run_edges(
                provider, [(reference_node, target_node), (other_node, target_node)]
            )
            assert len(_server_samples(own_server)) == 3
            matched_names = {
                _entity_name(target_node, entity_id)
                for entity_id, _result_id, _result in _results(target_node)
            }
            assert set(KNOWN_FUNCTIONS) <= matched_names
        finally:
            del session, other_node, reference_node, target_node, provider
            gc.collect()


def test_the_headless_example_runs_against_the_server(matched, monkeypatch, capsys):
    """examples/headless_mcrit.py stays in step with the API it demonstrates."""
    script = FIXTURES.parent.parent / "examples" / "headless_mcrit.py"
    settings = bn.Settings()
    previous = settings.get_string("mcrit.server")
    settings.set_string("mcrit.server", matched.server)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            str(FIXTURES / f"zlib-{matched.arch}-O2"),
            str(FIXTURES / f"zlib-{matched.arch}-Os"),
        ],
    )
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_:
        assert exit_.code in (0, None), exit_.code
    finally:
        settings.set_string("mcrit.server", previous)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("_deflate -> _deflate:") for line in lines), lines[-5:]
