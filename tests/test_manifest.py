"""plugin.json must declare the same pinned dependencies as pyproject.toml and requirements.txt."""

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
