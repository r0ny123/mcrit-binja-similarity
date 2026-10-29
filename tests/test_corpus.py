"""Corpus import/export, the single-function query, and the commands that drive them."""

import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mcrit_similarity import builtin, commands, corpus
from mcrit_similarity.config import ProviderConfig, config_from_values
from mcrit_similarity.connection import resolve
from mcrit_similarity.errors import McritHttpError
from mcrit_similarity.mcrit.client import McritClient
from mcrit_similarity.mcrit.samples import SampleRef
from mcrit_similarity.scoring import IS_LIBRARY_FLAG, IS_MINHASH_FLAG, IS_PICHASH_FLAG

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = builtin.Endpoint(url="http://127.0.0.1:4321/", token="secret")
OWN = "a" * 64


class FakeClient:
    def __init__(self, functions=None):
        self.functions = functions or {}
        self.asked: list[list[int]] = []

    def get_functions_by_ids(self, function_ids):
        self.asked.append(function_ids)
        return {fid: self.functions[fid] for fid in function_ids if fid in self.functions}


def _sample(sample_id, sha256, family="", filename="", version=""):
    return {
        "sample_id": sample_id,
        "sha256": sha256,
        "family": family,
        "filename": filename,
        "version": version,
    }


def _result(rows, samples):
    return {
        "matches": {
            "functions": [{"offset": 0x1000, "fid": -1, "matches": rows}],
            "samples": samples,
        }
    }


def _function(offset, label=""):
    labels = [{"function_label": label, "timestamp": "2026-01-01T00:00:00"}] if label else []
    return {"offset": offset, "function_labels": labels}


def test_matches_are_ranked_and_labelled_and_the_queried_binary_is_left_out():
    result = _result(
        [
            [1, 0, 10, 62.5, IS_MINHASH_FLAG],
            [1, 1, 11, 100.0, IS_MINHASH_FLAG | IS_PICHASH_FLAG],
            [2, 2, 12, 100.0, IS_PICHASH_FLAG],
        ],
        [
            _sample(0, "b" * 64, "zlib", "zlib-O2", "1.3"),
            _sample(1, "c" * 64, "zlib", "zlib-Os"),
            _sample(2, OWN, "self"),
        ],
    )
    client = FakeClient({10: _function(0x2000, "inflate"), 11: _function(0x3000)})
    found = corpus.corpus_matches(result, client, OWN)
    assert [(m.score, m.function_offset, m.function_label, m.filename) for m in found] == [
        (100.0, 0x3000, "", "zlib-Os"),
        (62.5, 0x2000, "inflate", "zlib-O2"),
    ]
    assert found[1].version == "1.3"
    assert client.asked == [[10, 11]]


@pytest.mark.parametrize("result", [None, {}, {"matches": []}, _result([["x"], [1, 2]], [])])
def test_a_malformed_result_yields_no_matches(result):
    assert corpus.corpus_matches(result, FakeClient(), OWN) == []


def _match(score=90.0, flags=IS_MINHASH_FLAG, label="f", family="fam", sha256="d" * 64):
    return corpus.CorpusMatch(score, flags, 1, 0x10, label, family, "v1", "s.exe", sha256)


def test_the_report_tables_every_match_and_escapes_cells():
    markdown = corpus.matches_markdown(
        "main",
        0x401000,
        [_match(label="a|b"), _match(flags=IS_PICHASH_FLAG | IS_LIBRARY_FLAG)],
        "X",
    )
    assert "# MCRIT corpus matches for main at 0x401000" in markdown
    assert "2 matching functions in 1 sample of 1 family, from X." in markdown
    assert "| 90.0 | MinHash | a\\|b | fam | v1 | s.exe (dddddddddddd) |" in markdown
    assert "| PicHash, library |" in markdown


def test_the_report_names_the_corpus_when_nothing_matches():
    markdown = corpus.matches_markdown("f", 0x10, [], "the built-in MCRIT server")
    assert "No function in the corpus of the built-in MCRIT server matches this one." in markdown


def test_the_report_counts_the_rows_it_leaves_out():
    many = [_match(sha256=f"{i:064x}") for i in range(corpus.MAX_ROWS + 3)]
    markdown = corpus.matches_markdown("f", 0x10, many, "X")
    assert markdown.count("| 90.0 |") == corpus.MAX_ROWS
    assert "3 weaker matches are not listed." in markdown


def test_the_import_summary_mentions_skips_and_a_different_smda():
    report = {
        "num_samples_imported": 2,
        "num_functions_imported": 152,
        "num_samples_skipped": 1,
        "escaper_mismatch": True,
    }
    summary = corpus.import_summary(report, "c.json")
    assert summary.startswith("Imported 2 samples (152 functions) from c.json; 1 sample was")
    assert "different SMDA" in summary


@pytest.mark.parametrize(
    ("instructions", "expected"),
    [
        (1, "f has 1 instruction; MCRIT only matches functions of more than 10"),
        (
            corpus.MIN_INSTRUCTIONS,
            "f has 10 instructions; MCRIT only matches functions of more than 10",
        ),
        (corpus.MIN_INSTRUCTIONS + 1, ""),
    ],
)
def test_functions_mcrit_cannot_hash_are_explained(instructions, expected):
    function = types.SimpleNamespace(num_instructions=instructions)
    assert corpus.too_small("f", function) == expected


def test_a_function_query_carries_only_that_function_and_the_binarys_identity():
    smda = pytest.importorskip("smda.Disassembler")
    from smda.common.SmdaReport import SmdaReport

    report = smda.Disassembler().disassembleFile(
        str(ROOT / "tests" / "fixtures" / "zlib-x86_64-Os")
    )
    function = max(report.getFunctions(), key=lambda f: f.num_instructions)
    report.data_refs_to = {1: [2]}
    report.xmetadata = {"symbols": ["secret"]}
    report.xheader = b"MZ"
    query = corpus.single_function_report(report, function)
    assert set(query) == {
        *corpus._QUERY_FIELDS,
        "xcfg",
        "metadata",
        "code_areas",
        "code_sections",
        "disassembly_errors",
        "xheader",
    }
    assert list(query["xcfg"]) == [function.offset]
    assert query["metadata"] == {} and query["xheader"] == ""
    assert "secret" not in json.dumps(query)
    # MCRIT and mcritweb both parse the query with SmdaReport.fromDict.
    parsed = SmdaReport.fromDict(json.loads(json.dumps(query)))
    assert parsed is not None and parsed.sha256 == report.sha256
    assert report.xmetadata == {"symbols": ["secret"]}


def test_an_incompatible_export_is_an_error():
    client = McritClient("http://127.0.0.1:1/")
    with (
        patch.object(McritClient, "_request", return_value=None),
        pytest.raises(McritHttpError, match="incompatible MCRIT configuration"),
    ):
        client.import_corpus(b"{}")


def test_the_function_query_sends_matching_parameters_and_skips_self_matches():
    client = McritClient("http://127.0.0.1:1/")
    with patch.object(McritClient, "_request", return_value={}) as request:
        client.query_function({"xcfg": {}}, minhash_threshold=60, pichash_size=8)
    args, kwargs = request.call_args
    assert args == ("POST", "query/function")
    assert kwargs["params"] == {
        "minhash_score": 60,
        "pichash_size": 8,
        "exclude_self_matches": "true",
    }
    assert kwargs["json"] == {"xcfg": {}}


def test_the_corpus_setting_is_read_and_passed_to_the_builtin_server(monkeypatch):
    config = config_from_values({"server": "builtin", "builtin_corpus": " /c.json "})
    assert config.builtin_corpus == "/c.json"
    asked = []
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": asked.append(corpus) or ENDPOINT)
    resolve(config)
    assert asked == ["/c.json"]


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(builtin, "_endpoint", None)
    monkeypatch.setattr(builtin, "_start", lambda: ENDPOINT)


def test_the_corpus_is_loaded_once_when_the_server_starts(fresh, monkeypatch):
    loaded = []
    monkeypatch.setattr(builtin, "_load_corpus", lambda started, path: loaded.append(path))
    assert builtin.endpoint("/c.json") is ENDPOINT
    assert builtin.endpoint("/c.json") is ENDPOINT
    assert loaded == ["/c.json"]


def test_an_unreadable_corpus_leaves_the_server_running(fresh, tmp_path, caplog):
    missing = tmp_path / "missing.json"
    assert builtin.endpoint(str(missing)) is ENDPOINT
    assert f"could not load the corpus {missing}" in caplog.text


def test_importing_a_missing_file_says_which_file(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": ENDPOINT)
    ok, message = commands.import_corpus(ProviderConfig(), str(tmp_path / "nope.json"))
    assert not ok
    assert message.startswith(f"Could not import {tmp_path / 'nope.json'}")


@pytest.mark.parametrize(
    ("existed", "expected"),
    [
        (False, "Added this binary to the built-in MCRIT server as sample 7"),
        (True, "the built-in MCRIT server already holds this binary as sample 7; MCRIT keeps"),
    ],
)
def test_adding_a_binary_uploads_it_with_its_labels(monkeypatch, existed, expected):
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": ENDPOINT)
    calls = []

    def ensure(client, report, **kwargs):
        calls.append(kwargs)
        return SampleRef(sample_id=7, sha256=OWN, existed=existed)

    monkeypatch.setattr(commands, "ensure_sample", ensure)
    ok, message = commands.add_to_corpus(ProviderConfig(), object(), "zlib", "1.3")
    assert ok
    assert message.startswith(expected)
    assert calls == [{"persist": True, "family": "zlib", "version": "1.3"}]


_ROUND_TRIP = textwrap.dedent(
    """
    import json, sys
    from smda.Disassembler import Disassembler
    from mcrit_similarity import builtin, corpus
    from mcrit_similarity.mcrit.client import McritClient

    phase, export, fixtures = sys.argv[1], sys.argv[2], sys.argv[3]
    if phase == "save":
        started = builtin.endpoint()
        client = McritClient(started.url, apitoken=started.token, timeout=None)
        report = Disassembler().disassembleFile(f"{fixtures}/zlib-x86_64-O2").toDict()
        report["metadata"] = {**report["metadata"], "family": "zlib", "version": "O2"}
        client.add_report(report)
        print(json.dumps(corpus.export_file(client, export)))
    else:
        started = builtin.endpoint(export)
        client = McritClient(started.url, apitoken=started.token, timeout=None)
        report = Disassembler().disassembleFile(f"{fixtures}/zlib-x86_64-Os")
        function = max(report.getFunctions(), key=lambda f: f.num_instructions)
        result = client.query_function(corpus.single_function_report(report, function))
        found = corpus.corpus_matches(result, client, report.sha256)
        print(json.dumps([[m.family, m.version, m.filename] for m in found]))
    """
)


def _run(phase: str, export: Path) -> Any:
    done = subprocess.run(
        [sys.executable, "-c", _ROUND_TRIP, phase, str(export), str(ROOT / "tests" / "fixtures")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_saved_corpus_loads_into_a_new_builtin_server_and_matches(tmp_path):
    pytest.importorskip("mcrit.config", reason="the built-in server needs the mcrit package")
    export = tmp_path / "corpus.json"
    # Each phase is its own process: one process holds exactly one built-in server.
    assert _run("save", export) == {
        "is_compressed": True,
        "num_families": 1,
        "num_samples": 1,
        "num_functions": 72,
    }
    assert ["zlib", "O2", "zlib-x86_64-O2"] in _run("load", export)
