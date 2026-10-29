"""Check that a release tag matches the project's versions and print its changelog section.

    python .github/scripts/release_notes.py v0.1.0 > notes.md

Exits non-zero with the reason when the tag is not Semantic Versioning, a version field
disagrees with it, or CHANGELOG.md has no dated section and link for it.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The pattern suggested by https://semver.org/spec/v2.0.0.html
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def project_versions(root: Path = ROOT) -> dict[str, str]:
    init = (root / "mcrit_similarity" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init, re.MULTILINE)
    return {
        "pyproject.toml": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["version"],
        "plugin.json": json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"],
        "mcrit_similarity/__init__.py": match.group(1) if match else "",
    }


def changelog_section(changelog: str, version: str) -> str:
    """The body of the dated ``## [version]`` section, as Keep a Changelog 1.1.0 lays it out."""
    if "## [Unreleased]" not in changelog:
        raise ValueError("CHANGELOG.md has no [Unreleased] section")
    heading = re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE
    )
    if heading is None:
        raise ValueError(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")
    if not re.search(rf"^\[{re.escape(version)}\]: https://\S+$", changelog, re.MULTILINE):
        raise ValueError(f"CHANGELOG.md has no link for [{version}]")
    following = re.search(r"^## \[|^\[[^\]]+\]: ", changelog[heading.end() :], re.MULTILINE)
    end = heading.end() + following.start() if following else len(changelog)
    body = changelog[heading.end() : end].strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section [{version}] is empty")
    return body


def release_notes(tag: str, root: Path = ROOT) -> str:
    version = tag.removeprefix("v")
    if not tag.startswith("v") or not SEMVER.match(version):
        raise ValueError(f"tag {tag!r} is not v followed by a Semantic Versioning version")
    mismatched = {path: found for path, found in project_versions(root).items() if found != version}
    if mismatched:
        details = ", ".join(f"{path} has {found!r}" for path, found in mismatched.items())
        raise ValueError(f"tag {tag} does not match the project version: {details}")
    return changelog_section((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py vX.Y.Z", file=sys.stderr)
        return 2
    try:
        print(release_notes(argv[1]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
