"""Corpus import/export for the built-in server, and the commands that drive them."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mcrit_similarity import builtin, commands, corpus
from mcrit_similarity.config import ProviderConfig, config_from_values
from mcrit_similarity.connection import resolve
from mcrit_similarity.errors import McritHttpError
from mcrit_similarity.mcrit.client import McritClient

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = builtin.Endpoint(url="http://127.0.0.1:4321/", token="secret")
OWN = "a" * 64


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


def test_an_incompatible_export_is_an_error():
    client = McritClient("http://127.0.0.1:1/")
    with (
        patch.object(McritClient, "_request", return_value=None),
        pytest.raises(McritHttpError, match="incompatible MCRIT configuration"),
    ):
        client.import_corpus(b"{}")


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
    monkeypatch.setattr(builtin, "unloaded_corpus", "")
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
    assert builtin.unloaded_corpus == str(missing)


def test_an_export_cannot_replace_the_corpus_that_failed_to_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert commands._same_file("c.json", str(tmp_path / "c.json"))
    assert not commands._same_file("c.json", "")
    assert not commands._same_file("c.json", str(tmp_path / "d.json"))


class _Exporting:
    def export_corpus(self):
        return {"content": {"num_samples": 1}, "data": "new"}


def test_an_export_replaces_the_file_only_once_fully_written(tmp_path, monkeypatch):
    exporting: Any = _Exporting()
    target = tmp_path / "corpus.json"
    target.write_text("previous", encoding="utf-8")

    def full_disk(self, *args, **kwargs):
        raise OSError("No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(Path, "write_text", full_disk)
        with pytest.raises(OSError):
            corpus.export_file(exporting, str(target))
    assert target.read_text(encoding="utf-8") == "previous"
    assert corpus.export_file(exporting, str(target)) == {"num_samples": 1}
    assert json.loads(target.read_text(encoding="utf-8"))["data"] == "new"
    assert [path.name for path in tmp_path.iterdir()] == ["corpus.json"]


def test_importing_a_missing_file_says_which_file(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin, "endpoint", lambda corpus="": ENDPOINT)
    ok, message = commands.import_corpus(ProviderConfig(), str(tmp_path / "nope.json"))
    assert not ok
    assert message.startswith(f"Could not import {tmp_path / 'nope.json'}")


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
        report = Disassembler().disassembleFile(f"{fixtures}/zlib-x86_64-O2")
        sample = client.get_sample_by_sha256(report.sha256) or {}
        print(json.dumps([sample.get(key) for key in ("family", "version", "filename")]))
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


def test_a_saved_corpus_loads_into_a_new_builtin_server(tmp_path):
    pytest.importorskip("mcrit.config", reason="the built-in server needs the mcrit package")
    export = tmp_path / "corpus.json"
    # Each phase is its own process: one process holds exactly one built-in server.
    assert _run("save", export) == {
        "is_compressed": True,
        "num_families": 1,
        "num_samples": 1,
        "num_functions": 72,
    }
    assert _run("load", export) == ["zlib", "O2", "zlib-x86_64-O2"]
