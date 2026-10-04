"""Exercise inventory classification only against invented temporary homes."""

import json
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "memory-inventory.sh"


def _snapshot(home):
    return {
        str(path.relative_to(home)): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("existing", "EXISTING"),
        ("missing", "ORPHANED"),
        ("unknown", "UNRESOLVED"),
        ("raw_dot", "UNRESOLVED"),
        ("missing_hyphenated", "UNRESOLVED"),
        ("mixed", "UNRESOLVED"),
        ("multiple_existing", "UNRESOLVED"),
        ("dotted_alias", "UNRESOLVED"),
        ("dotted_only", "UNRESOLVED"),
        ("symlink", "UNRESOLVED"),
        ("dangling_symlink", "UNRESOLVED"),
        ("file_target", "UNRESOLVED"),
        ("missing_ancestor", "UNRESOLVED"),
        ("space_existing", "EXISTING"),
        ("unicode_existing", "EXISTING"),
        ("unreadable_parent", "UNRESOLVED"),
        ("trailing_separator", "UNRESOLVED"),
    ],
)
def test_inventory_classification(tmp_path, scenario, status):
    home = tmp_path / "home"
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    workspace = workspaces / "project"
    restricted = None
    if scenario in {"existing", "space_existing", "unicode_existing"}:
        if scenario == "space_existing":
            workspace = workspaces / "project space"
        elif scenario == "unicode_existing":
            workspace = workspaces / "projecté"
        workspace.mkdir()
    elif scenario == "missing_hyphenated":
        workspace = workspaces / "project-name"
    elif scenario in {"mixed", "multiple_existing"}:
        workspace = workspaces / "area-item"
        workspace.mkdir()
        (workspaces / "area").mkdir()
        if scenario == "multiple_existing":
            (workspaces / "area" / "item").mkdir()
    elif scenario in {"dotted_alias", "dotted_only"}:
        workspace = workspaces / "example-com"
        (workspaces / "example.com").mkdir()
        if scenario == "dotted_alias":
            (workspaces / "example").mkdir()
    elif scenario in {"symlink", "dangling_symlink"}:
        target = workspaces / "target"
        if scenario == "symlink":
            target.mkdir()
        workspace.symlink_to(target, target_is_directory=True)
    elif scenario == "file_target":
        workspace.write_text("invented workspace replacement\n")
    elif scenario == "missing_ancestor":
        workspace = workspaces / "removed" / "project"
    elif scenario == "unreadable_parent":
        if os.geteuid() == 0:
            pytest.skip("root bypasses directory access permissions")
        restricted = workspaces / "restricted"
        restricted.mkdir()
        workspace = restricted / "project"
        restricted.chmod(0)

    if scenario == "trailing_separator":
        workspace.mkdir()
    munged = str(workspace).replace("/", "-")
    if scenario == "trailing_separator":
        munged += "-"
    if scenario == "unknown":
        munged = "custom-store"
    elif scenario == "raw_dot":
        munged += ".custom"
    memory = home / ".claude" / "projects" / munged / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Invented memory for example.com.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    before = _snapshot(home)
    script = Path(os.environ.get("MEMORY_INVENTORY_TEST_SCRIPT", SCRIPT))
    try:
        result = subprocess.run(
            ["bash", str(script), "--json"], env=env,
            check=True, capture_output=True, text=True,
        )
        entries = json.loads(result.stdout)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["status"] == status
        assert entry["orphaned"] is (status == "ORPHANED")
        assert entry["unresolved"] is (status == "UNRESOLVED")
        assert entry["path"] == (None if status == "UNRESOLVED" else str(workspace))
        assert entry["file_count"] == 1
        assert entry["munged"] == munged

        text = subprocess.run(
            ["bash", str(script)], env=env,
            check=True, capture_output=True, text=True,
        ).stdout
        assert "GUESS" not in text
        assert ("[ORPHANED:" in text) is (status == "ORPHANED")
        assert ("[UNRESOLVED:" in text) is (status == "UNRESOLVED")
        assert f"1 stores, {int(status == 'ORPHANED')} orphaned" in text
        assert f"{int(status == 'UNRESOLVED')} unresolved" in text
        if status != "UNRESOLVED":
            assert f"Path: {workspace}" in text
        assert _snapshot(home) == before
        assert list(temp.iterdir()) == []
    finally:
        if restricted is not None:
            restricted.chmod(0o700)


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "aggregate"])
def test_inventory_summary(tmp_path, populated):
    home = tmp_path / "home"
    projects = home / ".claude" / "projects"
    projects.mkdir(parents=True)
    expected = {}
    if populated:
        workspaces = tmp_path / "workspaces"
        workspaces.mkdir()
        existing = workspaces / "existing"
        existing.mkdir()
        missing = workspaces / "missing"
        expected = {
            str(existing).replace("/", "-"): "EXISTING",
            str(missing).replace("/", "-"): "ORPHANED",
            "custom-store": "UNRESOLVED",
        }
        for munged in expected:
            memory = projects / munged / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("Invented memory for example.net.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    script = Path(os.environ.get("MEMORY_INVENTORY_TEST_SCRIPT", SCRIPT))
    before = _snapshot(home)
    result = subprocess.run(
        ["bash", str(script), "--json"], env=env,
        check=True, capture_output=True, text=True,
    )
    assert {entry["munged"]: entry["status"] for entry in json.loads(result.stdout)} == expected
    text = subprocess.run(
        ["bash", str(script)], env=env,
        check=True, capture_output=True, text=True,
    ).stdout
    count = int(populated)
    assert text.rstrip().endswith(
        f"{3 * count} stores, {count} orphaned (decoded workspace path does not exist), "
        f"{count} unresolved (path could not be determined safely)"
    )
    assert _snapshot(home) == before
    assert list(temp.iterdir()) == []
