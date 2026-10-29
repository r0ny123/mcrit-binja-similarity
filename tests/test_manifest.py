"""plugin.json, pyproject.toml, requirements.txt and the README must agree on pins and minimum build."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dependency_pins_agree_across_the_repo():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    manifest = json.loads((ROOT / "plugin.json").read_text())["dependencies"]["pip"]
    assert requirements == pyproject
    assert manifest == pyproject


def test_readme_lists_the_pinned_dependencies_and_minimum_build():
    readme = (ROOT / "README.md").read_text()
    manifest = json.loads((ROOT / "plugin.json").read_text())
    for requirement in manifest["dependencies"]["pip"]:
        assert f"\n   {requirement}\n" in readme, requirement
    assert f"build {manifest['minimumbinaryninjaversion']} or newer" in readme
