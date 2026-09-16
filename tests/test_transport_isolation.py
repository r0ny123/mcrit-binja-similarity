"""The MCRIT transport layer must not import Binary Ninja."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "mcrit_similarity"
FORBIDDEN = ("binaryninja", "binaryninjaui")
ALLOWED_BN_FILES = {
    "provider.py",
    "settings.py",
    "export.py",
    "render.py",
    "backend.py",
    "log.py",
}


def test_transport_has_no_binaryninja_imports():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path.name in ALLOWED_BN_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            if any(name in FORBIDDEN for name in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
