"""MCRIT export files for the built-in server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcrit_similarity.mcrit.client import McritClient


def count(number: int, noun: str, plural: str = "") -> str:
    return f"{number} {noun if number == 1 else plural or noun + 's'}"


def import_file(client: McritClient, path: str) -> dict[str, Any]:
    """Load an MCRIT export (``mcrit client export`` writes the same format) into the server."""
    return client.import_corpus(Path(path).read_bytes())


def export_file(client: McritClient, path: str) -> dict[str, Any]:
    """Write the server's whole corpus to ``path``; returns the export's content counts."""
    data = client.export_corpus()
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    content = data.get("content")
    return content if isinstance(content, dict) else {}


def import_summary(report: dict[str, Any], path: str) -> str:
    samples = report.get("num_samples_imported", 0)
    functions = report.get("num_functions_imported", 0)
    text = f"Imported {count(samples, 'sample')} ({count(functions, 'function')}) from {path}"
    skipped = report.get("num_samples_skipped", 0)
    if skipped:
        text += f"; {count(skipped, 'sample')} {'was' if skipped == 1 else 'were'} already there"
    if report.get("escaper_mismatch"):
        text += (
            ". The export was made with a different SMDA, so some of its functions will "
            "match less than they should"
        )
    return text
