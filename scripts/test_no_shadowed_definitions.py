"""No Python file under scripts/ or plugins/ defines one top-level name twice.

A second module-level `def` silently replaces the first for every caller in
the module. On 2026-11-14 a new helper named `_git` in
test_generate_skills_lock.py replaced the original, which pinned
`core.autocrlf=false`, so tests far away from the change failed on Windows
only. Parsed with `ast`, not a line scan.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FILES = sorted(
    p for pattern in ("scripts/*.py", "plugins/*/skills/*/scripts/*.py",
                      "plugins/*/skills/*/tests/*.py")
    for p in REPO_ROOT.glob(pattern)
)


def _duplicates(source: str) -> list[str]:
    tree = ast.parse(source)
    names = Counter(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    return sorted(name for name, count in names.items() if count > 1)


def test_the_check_sees_a_shadowed_helper():
    assert _duplicates("def _git():\n    pass\n\ndef _git():\n    pass\n") == ["_git"]
    assert _duplicates("def _git():\n    pass\n\ndef _git_out():\n    pass\n") == []


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_top_level_name_is_defined_twice(path):
    assert _duplicates(path.read_text(encoding="utf-8")) == []
