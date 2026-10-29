"""Corpus tools: MCRIT export files, and one function matched against the whole corpus."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcrit_similarity.mcrit.client import McritClient
from mcrit_similarity.mcrit.parse import target_functions
from mcrit_similarity.scoring import IS_LIBRARY_FLAG, IS_PICHASH_FLAG

MAX_ROWS = 100
# MCRIT's default MINHASH_FN_MIN_INS: the function query fails for anything this small.
MIN_INSTRUCTIONS = 10


class FunctionClient(Protocol):
    def get_functions_by_ids(self, function_ids: list[int]) -> dict[int, dict[str, Any]]: ...


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


def too_small(name: str, function: Any) -> str:
    """Why MCRIT cannot match ``function``, or nothing when it can."""
    if function.num_instructions > MIN_INSTRUCTIONS:
        return ""
    return (
        f"{name} has {count(function.num_instructions, 'instruction')}; MCRIT only matches "
        f"functions of more than {MIN_INSTRUCTIONS}"
    )


# The binary's identity and layout scalars; everything else report-wide (symbols, exports,
# header bytes, data references, metadata) stays on this machine.
_QUERY_FIELDS = (
    "architecture",
    "abi",
    "base_addr",
    "binary_size",
    "bitness",
    "confidence_threshold",
    "execution_time",
    "identified_alignment",
    "message",
    "sha256",
    "smda_version",
    "status",
    "timestamp",
)


def single_function_report(report: Any, function: Any) -> dict[str, Any]:
    """``report`` reduced to ``function``: MCRIT's function query takes exactly one."""
    reduced = copy.copy(report)
    reduced.xcfg = {function.offset: function}
    # Dropped before serialising only to keep it cheap; the allowlist is what bounds the payload.
    reduced.data_refs_from = reduced.data_refs_to = reduced.xmetadata = reduced.buffer = None
    full = reduced.toDict()
    query = {key: full[key] for key in _QUERY_FIELDS if key in full}
    # SmdaReport.fromDict requires these keys, empty or not.
    query.update(
        xcfg=full["xcfg"],
        metadata={},
        code_areas=[],
        code_sections=[],
        disassembly_errors={},
        xheader="",
    )
    return query


@dataclass(frozen=True)
class CorpusMatch:
    score: float
    flags: int
    function_id: int
    function_offset: int | None
    function_label: str
    family: str
    version: str
    filename: str
    sha256: str


def corpus_matches(result: Any, client: FunctionClient, own_sha256: str) -> list[CorpusMatch]:
    """Matches of a function query, best first, without the queried binary itself."""
    matches = result.get("matches") if isinstance(result, dict) else None
    if not isinstance(matches, dict):
        return []
    samples: dict[int, dict[str, Any]] = {}
    for entry in matches.get("samples") or []:
        if isinstance(entry, dict) and isinstance(entry.get("sample_id"), int):
            samples[entry["sample_id"]] = entry
    raw: list[tuple[int, int, float, int]] = []
    for summary in matches.get("functions") or []:
        if not isinstance(summary, dict):
            continue
        for row in summary.get("matches") or []:
            try:
                sample_id, function_id, score = int(row[1]), int(row[2]), float(row[3])
                flags = int(row[4]) if len(row) > 4 else 0
            except (TypeError, ValueError, IndexError):
                continue
            # Servers older than MCRIT 1.16 ignore exclude_self_matches.
            if samples.get(sample_id, {}).get("sha256") != own_sha256:
                raw.append((sample_id, function_id, score, flags))
    functions = target_functions(client.get_functions_by_ids(sorted({row[1] for row in raw})))
    found = []
    for sample_id, function_id, score, flags in raw:
        sample = samples.get(sample_id, {})
        offset, label = functions.get(function_id, (None, ""))
        found.append(
            CorpusMatch(
                score=score,
                flags=flags,
                function_id=function_id,
                function_offset=offset,
                function_label=label,
                family=str(sample.get("family") or ""),
                version=str(sample.get("version") or ""),
                filename=str(sample.get("filename") or ""),
                sha256=str(sample.get("sha256") or ""),
            )
        )
    found.sort(key=lambda match: (-match.score, match.family, match.filename))
    return found


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def matches_markdown(name: str, address: int, matches: list[CorpusMatch], where: str) -> str:
    lines = [f"# MCRIT corpus matches for {_cell(name)} at {address:#x}", ""]
    if not matches:
        lines.append(f"No function in the corpus of {where} matches this one.")
        return "\n".join(lines) + "\n"
    families = {match.family for match in matches if match.family}
    samples = {match.sha256 for match in matches}
    lines += [
        f"{count(len(matches), 'matching function')} in {count(len(samples), 'sample')}"
        + (f" of {count(len(families), 'family', 'families')}" if families else "")
        + f", from {where}.",
        "",
        "| Score | Kind | Function | Family | Version | Sample |",
        "|---:|---|---|---|---|---|",
    ]
    for match in matches[:MAX_ROWS]:
        kind = "PicHash" if match.flags & IS_PICHASH_FLAG else "MinHash"
        if match.flags & IS_LIBRARY_FLAG:
            kind += ", library"
        function = match.function_label or (
            f"{match.function_offset:#x}"
            if match.function_offset is not None
            else f"id {match.function_id}"
        )
        sample = f"{match.filename} ({match.sha256[:12]})" if match.filename else match.sha256[:12]
        lines.append(
            f"| {match.score:.1f} | {kind} | {_cell(function)} | {_cell(match.family)} | "
            f"{_cell(match.version)} | {_cell(sample)} |"
        )
    if len(matches) > MAX_ROWS:
        lines += ["", f"{len(matches) - MAX_ROWS} weaker matches are not listed."]
    return "\n".join(lines) + "\n"
