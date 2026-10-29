"""The release workflow's checks: versions, Semantic Versioning tags and the changelog."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / ".github" / "scripts" / "release_notes.py"
)
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-11-02

### Added

- Something new.

## [1.1.0] - 2026-10-01

### Fixed

- Something old.

[Unreleased]: https://example.com/compare/v1.2.0...HEAD
[1.2.0]: https://example.com/compare/v1.1.0...v1.2.0
[1.1.0]: https://example.com/releases/tag/v1.1.0
"""


def _project(tmp_path, version="1.2.0", changelog=CHANGELOG, plugin_version=None):
    (tmp_path / "mcrit_similarity").mkdir()
    (tmp_path / "mcrit_similarity" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (tmp_path / "plugin.json").write_text(json.dumps({"version": plugin_version or version}))
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    return tmp_path


def test_the_repository_is_releasable_at_its_current_version():
    versions = release.project_versions()
    assert len(set(versions.values())) == 1, versions
    (version,) = set(versions.values())
    notes = release.release_notes(f"v{version}")
    assert notes.startswith("### ")


def test_every_released_section_is_dated_and_linked():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(\d[^\]]*)\]", changelog, re.MULTILINE)
    assert versions
    for version in versions:
        assert release.SEMVER.match(version), version
        release.changelog_section(changelog, version)


def test_notes_are_the_tagged_section_only(tmp_path):
    notes = release.release_notes("v1.2.0", _project(tmp_path))
    assert notes == "### Added\n\n- Something new."
    older = release.changelog_section(CHANGELOG, "1.1.0")
    assert older == "### Fixed\n\n- Something old."


@pytest.mark.parametrize(
    "tag", ["1.2.0", "v1.2", "v01.2.0", "v1.2.0.0", "vlatest", "release-1.2.0"]
)
def test_a_tag_that_is_not_semantic_versioning_is_refused(tmp_path, tag):
    with pytest.raises(ValueError, match="Semantic Versioning"):
        release.release_notes(tag, _project(tmp_path))


@pytest.mark.parametrize(
    "version", ["1.0.0-rc.1", "2.0.0+build.5", "0.1.0-alpha.1+exp.sha.5114f85"]
)
def test_pre_release_and_build_versions_are_semantic_versioning(version):
    assert release.SEMVER.match(version)


def test_a_version_field_that_disagrees_with_the_tag_is_named(tmp_path):
    with pytest.raises(ValueError, match="plugin.json has '1.1.0'"):
        release.release_notes("v1.2.0", _project(tmp_path, plugin_version="1.1.0"))


@pytest.mark.parametrize(
    ("changelog", "reason"),
    [
        (CHANGELOG.replace("## [Unreleased]\n", ""), "no \\[Unreleased\\] section"),
        (CHANGELOG.replace("## [1.2.0] - 2026-11-02", "## [1.2.0]"), "no '## \\[1.2.0\\]"),
        (
            CHANGELOG.replace("[1.2.0]: https://example.com/compare/v1.1.0...v1.2.0\n", ""),
            "no link",
        ),
        (CHANGELOG.replace("### Added\n\n- Something new.\n", ""), "is empty"),
    ],
)
def test_an_incomplete_changelog_is_refused(tmp_path, changelog, reason):
    with pytest.raises(ValueError, match=reason):
        release.release_notes("v1.2.0", _project(tmp_path, changelog=changelog))


def test_the_command_line_reports_errors_without_a_traceback(capsys):
    assert release.main(["release_notes.py", "not-a-tag"]) == 1
    assert "error: tag 'not-a-tag'" in capsys.readouterr().err
    assert release.main(["release_notes.py"]) == 2
