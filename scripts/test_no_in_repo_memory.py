#!/usr/bin/env python3
"""This repo keeps no Claude Code memory in git (STRATEGY.md, "Memory").

The in-repo `autoMemoryDirectory` pattern was retired because cloud and
Projects sessions never used it. These tests keep it from coming back by
accident, for example through the `migrate-claude-memory` snippet.

Run: python3 -m pytest scripts/test_no_in_repo_memory.py -q
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_settings_do_not_set_auto_memory_directory():
    settings = json.loads(
        (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "autoMemoryDirectory" not in settings


def test_there_is_no_in_repo_memory_directory():
    assert not (REPO_ROOT / ".claude" / "memory").exists()
