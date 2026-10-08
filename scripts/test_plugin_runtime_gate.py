#!/usr/bin/env python3
"""Tests for scripts/plugin_runtime_gate.py and the workflow that runs it.

Hermetic and deterministic: the gate's network side is replaced by an
in-memory FakeFetcher holding a base and a head tree, so nothing here calls
GitHub. No sleeps, no wall-clock time.

Each reason (a)-(g) gets a case that gates and a nearby case that does not:
a gate that has never been shown saying "no" would approve nothing, and one
never shown saying "yes" would stop nothing.

The workflow-shape tests at the end parse plugin-runtime-review.yml as YAML
rather than grepping it, for the reason scripts/test_ci_workflow.py gives.

Run: python3 -m pytest scripts/test_plugin_runtime_gate.py -q
"""

import json
import os
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


def test_a_script_no_hook_calls_does_not_gate():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config('"${CLAUDE_PLUGIN_ROOT}"/skills/one/scripts/hook.sh')})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_not_gated(run(base, head))


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


@pytest.mark.parametrize("path", ["plugins/alpha/skills/one/package.json",
                                  "plugins/alpha/skills/one/settings.json",
                                  "package.json", "plugins/alpha/yarn.lock"])
def test_the_same_names_elsewhere_do_not_gate(path):
    base = tree()
    assert_not_gated(run(base, dict(base, **{path: b"{}"})))


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


def test_a_marketplace_change_without_runtime_keys_does_not_gate():
    base = tree(**{".claude-plugin/marketplace.json": marketplace("alpha", alpha={"hooks": {}})})
    head = dict(base, **{".claude-plugin/marketplace.json":
                         marketplace("alpha", "beta", alpha={"hooks": {}, "description": "new"})})
    assert_not_gated(run(base, head))


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


def test_a_sibling_with_a_matching_prefix_does_not_gate():
    base = tree(**{"plugins/alpha/hooks/hooks.json":
                   hook_config("${CLAUDE_PLUGIN_ROOT}/skills/one/scripts/tool")})
    head = dict(base, **{"plugins/alpha/skills/one/scripts/tool.sh": b"echo two\n"})
    assert_not_gated(run(base, head))


def test_rests_after_the_plugin_root_are_read_as_paths():
    rests = gate._rests_after_plugin_root
    assert "skills/x/y.sh" in rests('"${CLAUDE_PLUGIN_ROOT}"/skills/x/y.sh arg')
    assert "my dir/x.sh" in rests('"${CLAUDE_PLUGIN_ROOT}/my dir/x.sh"')
    assert rests("${CLAUDE_PLUGIN_ROOTS}/x.sh") == set()
    assert "a/" in rests("${CLAUDE_PLUGIN_ROOT}/a/*.sh")
    assert "a/b.sh" in rests("${CLAUDE_PLUGIN_ROOT}\\a\\b.sh")
    assert "x.sh" in rests('"${CLAUDE_PLUGIN_ROOT}/x.sh')  # unbalanced quote


# =================================================================================
# (f) the gate itself
# =================================================================================


@pytest.mark.parametrize("path", sorted(gate.GATE_FILES))
def test_a_change_to_the_gate_gates(path):
    base = tree()
    result = run(base, dict(base, **{path: b"changed\n"}))
    assert ("gate-itself", path) in result[1], result


def test_a_change_to_another_workflow_or_script_does_not_gate():
    base = tree()
    head = dict(base, **{".github/workflows/ci.yml": b"x\n", "scripts/check_skills.py": b"x\n"})
    assert_not_gated(run(base, head))


def test_every_gate_file_exists():
    for path in gate.GATE_FILES:
        assert (REPO_ROOT / path).is_file(), path


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


def test_every_action_is_pinned_to_a_full_sha():
    for job in load_workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                ref = step["uses"].rsplit("@", 1)[1]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), step


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


def test_both_jobs_have_a_timeout():
    for job_id, job in load_workflow()["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), job_id


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
