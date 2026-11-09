#!/usr/bin/env python3
"""Tests for the required-status-check invariants of .github/workflows/ci.yml.

`pytest-windows` is a REQUIRED status check on this repo's default branch. The
declaration lives in another repo — repo-settings' `fleet.yml`, under
`Adam-S-Daniel/adam-agentskills` — which is exactly why these tests exist here: this
repo owns the workflow that has to keep satisfying it, and has nothing else
that would notice when it stops.

Every assertion below guards the same failure, which is severe and silent:
GitHub holds a required context that is never reported as *pending forever*, so
every open PR becomes unmergeable at once, with no failing check to point at.

Parsed as YAML rather than grepped on purpose. A line scan reads clean on a
`concurrency:` nested inside a job it never looked into, and cannot tell a job
id from the same word in one of ci.yml's comments — which is most of the file.

Run: python3 -m pytest scripts/test_ci_workflow.py -q
"""

import os
import re
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The contexts repo-settings' fleet.yml makes required for this repo. Kept in
# step by hand because the two repos cannot read each other — and that is the
# job of the first test below: it is what fails when this list and the workflow
# drift apart, instead of a PR queue silently wedging.
# `pytest` too: the live ruleset requires it (read 2026-11-09), and the
# one-way-door check in it is enforcement only while it stays required.
REQUIRED_CONTEXTS = ["pytest", "pytest-windows"]


def load_ci() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def ci_triggers(doc: dict) -> dict:
    # YAML 1.1 (what pyyaml implements) resolves a bare `on` to the boolean
    # True, so the trigger block is doc[True] and not doc["on"]. Accept either
    # rather than depending on which spec version the parser follows.
    return doc[True] if True in doc else doc["on"]


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_every_required_context_is_still_a_job_here(context):
    assert context in load_ci()["jobs"], (
        f"{context} is a required status check (repo-settings fleet.yml) but is "
        f"no longer a job in ci.yml. Nothing will ever report that context, and "
        f"every open PR is blocked until fleet.yml is updated to match."
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_a_required_job_publishes_its_context_under_its_job_id(context):
    """GitHub names a check after the job id only while the job has neither a
    `name:` nor a `strategy.matrix`. Either one renames the context — a matrix
    to `pytest-windows (windows-latest)` — which is the whole reason the
    Windows lane is a separate job rather than a matrix leg of `pytest`."""
    job = load_ci()["jobs"][context]
    assert "name" not in job, (
        f"{context} gained a `name:`, which renames the published check context "
        f"away from the required `{context}`."
    )
    assert "matrix" not in (job.get("strategy") or {}), (
        f"{context} gained a `strategy.matrix`, which renames the published "
        f"check context away from the required `{context}`."
    )


def test_ci_reports_on_every_pull_request():
    """A `paths:`/`branches:` filter on `on: pull_request` would leave the
    required context unreported on any PR that misses the filter — pending
    forever, not skipped."""
    pull_request = ci_triggers(load_ci())["pull_request"]
    narrowing = {"paths", "paths-ignore", "branches", "branches-ignore"}
    assert not (narrowing & set(pull_request or {})), (
        "ci.yml's `on: pull_request` was narrowed. A PR that misses the filter "
        "never reports the required contexts and can never be merged."
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_no_concurrency_group_governs_a_required_job(context):
    """A job publishing a required context gets no `concurrency` block, at
    either level. GitHub picks non-deterministically between a cancelled and a
    successful run for the same context + sha, and when cancelled wins the
    merge API returns `405 Required status check "<ctx>" is cancelled` — which
    nothing overrides, not auto-merge and not an explicit merge call.

    `cancel-in-progress: false` does not make this safe: GitHub still keeps
    only the in-progress run plus the latest pending one per group and cancels
    the other pending duplicates."""
    doc = load_ci()
    assert "concurrency" not in doc, (
        "ci.yml gained a workflow-level `concurrency:` block, which governs "
        f"every job in it including the required `{context}`."
    )
    assert "concurrency" not in doc["jobs"][context], (
        f"the required job `{context}` gained a `concurrency:` block."
    )


# --- The docs-only early-skip gate -----------------------------------------
#
# Because `pytest-windows` is required, the docs-only skip lives INSIDE the two
# pytest jobs (an always-run `salient` step gating the heavy steps) rather than
# as a trigger filter — test_ci_reports_on_every_pull_request above is what
# forbids the trigger form. That trade is safe only while three things hold,
# and each gets its own test: the two jobs carry the SAME gate (a list edited
# in one job only is a silent divergence), the gate pins the MEASURED inert
# list (every path no suite reads; plugins/**/*.md stays salient because
# SKILL.md/PURPOSE.md feed the digests and lock checks), and the gate actually
# behaves — the body is executed here against fixture repos in both
# directions, because a green gate that was never shown able to say "run" is
# a light wired to nothing.

INERT_DOCS_REGEX = (
    r"^(docs/.*\.md|README\.md|AGENTS\.md|CLAUDE\.md|STRATEGY\.md)$"
)

PYTEST_JOBS = ["pytest", "pytest-windows"]


def _salience_step(job_id: str) -> dict:
    steps = [s for s in load_ci()["jobs"][job_id].get("steps", [])
             if s.get("id") == "salient"]
    assert len(steps) == 1, (
        f"job `{job_id}` must carry exactly one step with id `salient`; "
        f"found {len(steps)}. Without it the heavy steps' `if:` guards "
        f"evaluate against a missing step and the job runs nothing."
    )
    return steps[0]


def test_the_pytest_jobs_share_one_salience_gate():
    """The two suites must skip and run on the same verdict. An inert list
    widened in one job only would let the required Windows lane skip a change
    the ubuntu lane still tests — or the reverse, which reports the required
    context green on a change nothing Windows-shaped ever saw."""
    ubuntu, windows = (_salience_step(j) for j in PYTEST_JOBS)
    assert ubuntu["run"] == windows["run"], (
        "the `salient` step bodies in `pytest` and `pytest-windows` differ — "
        "edit them together, byte for byte."
    )
    assert ubuntu.get("env") == windows.get("env"), (
        "the `salient` step env blocks in `pytest` and `pytest-windows` "
        "differ — edit them together."
    )


def test_the_salience_gate_pins_the_measured_inert_list():
    """The inert list is an allowlist of paths measured to be read by no test
    this workflow runs. This pin makes widening it a two-place edit — here and
    in ci.yml — so it happens deliberately or fails the suite."""
    body = _salience_step("pytest")["run"]
    assert INERT_DOCS_REGEX in body, (
        "the salience gate's inert regex no longer matches the measured list "
        "this test pins. If the change is deliberate, re-measure (which tests "
        "read the paths being added?) and update both places."
    )
    assert 'git diff --no-renames --name-only "$BASE_SHA" "$HEAD_SHA"' in body, (
        "the salience gate must diff with --no-renames: with rename detection "
        "on, moving a salient file to an inert path lists only the inert "
        "destination and the suite is skipped."
    )
    assert '"$EVENT_NAME" != "pull_request"' in body, (
        "the salience gate lost its fail-open guard: every non-pull_request "
        "event (push, workflow_dispatch) must run the full suite."
    )


@pytest.mark.parametrize("job_id", PYTEST_JOBS)
def test_the_heavy_steps_are_gated_on_the_salience_verdict(job_id):
    """A detect step nothing consults is dead code that looks like a control.
    The pip install and the pytest run in both jobs must consult it."""
    gate = "steps.salient.outputs.run == 'true'"
    gated = [s for s in load_ci()["jobs"][job_id]["steps"]
             if s.get("if") == gate]
    runs = " ".join(s.get("run") or "" for s in gated)
    assert "-m pytest" in runs and "pip install" in runs, (
        f"job `{job_id}` must gate both its pip install and its pytest run "
        f"on `{gate}`; the gated steps found were: "
        f"{[s.get('name', '<unnamed>') for s in gated]}"
    )


def test_the_pytest_jobs_run_the_suite_in_parallel_and_identically():
    """The serial Windows suite was ~17 minutes on `pytest-windows` — the only
    required status check this repo has (issue #176) — against ubuntu's ~4.5.
    -n auto (pytest-xdist) is what closes that gap. A one-job edit would let
    the lanes drift silently: the required Windows check testing at a
    different parallelism than ubuntu, or `-n auto` landing in only one of
    them while the other quietly stays serial."""
    doc = load_ci()
    runs = {}
    for job_id in PYTEST_JOBS:
        steps = [s for s in doc["jobs"][job_id].get("steps", [])
                 if s.get("name") == "Run every test suite in the repo"]
        assert len(steps) == 1, (
            f"job `{job_id}` must carry exactly one step named "
            f"`Run every test suite in the repo`; found {len(steps)}."
        )
        runs[job_id] = steps[0]["run"]
    ubuntu, windows = (runs[job_id] for job_id in PYTEST_JOBS)
    assert ubuntu == windows, (
        "the `Run every test suite in the repo` run lines in `pytest` and "
        "`pytest-windows` differ — edit them together, byte for byte."
    )
    tokens = shlex.split(ubuntu)
    pairs = list(zip(tokens, tokens[1:]))
    assert ("-n", "auto") in pairs, (
        f"the `Run every test suite in the repo` run line must pass `-n auto` "
        f"to pytest so the suite runs in parallel; got: {ubuntu!r}"
    )


def _run_salience_body(tmp_path, changed_files, event_name, renames=()):
    """Execute the real gate body from ci.yml in a throwaway two-commit repo
    and return the GITHUB_OUTPUT it wrote. Each (old, new) in `renames` is
    committed at `old` in the base and moved to `new` in the head."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the gate body")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q")
    # Repo-local identity, the same pattern the lock tests use, so this
    # passes with no global git identity available.
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.com")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "seed.txt")
    for old, _new in renames:
        path = repo / old
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("moved content\nline two\nline three\n", encoding="utf-8")
        git("add", old)
    git("commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True
                          ).stdout.strip()
    for name in changed_files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
        git("add", name)
    for old, new in renames:
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        git("mv", old, new)
    git("commit", "-q", "-m", "head")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True
                          ).stdout.strip()

    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    import os
    env = {**os.environ,
           "BASE_SHA": base, "HEAD_SHA": head, "EVENT_NAME": event_name,
           # as_posix() so the redirection target parses the same under Git
           # Bash on Windows as under bash on Linux.
           "GITHUB_OUTPUT": out_file.as_posix()}
    result = subprocess.run([bash, "-c", _salience_step("pytest")["run"]],
                            cwd=str(repo), env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"the gate body itself failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return out_file.read_text(encoding="utf-8")


def test_the_gate_skips_a_docs_only_change(tmp_path):
    out = _run_salience_body(
        tmp_path,
        ["docs/guide.md", "README.md", "AGENTS.md", "CLAUDE.md", "STRATEGY.md"],
        "pull_request")
    assert "run=false" in out and "run=true" not in out


def test_the_gate_runs_when_any_salient_file_rides_along(tmp_path):
    """The mixed changeset is the common case, not the corner — a docs edit
    riding with a code edit must run everything."""
    out = _run_salience_body(
        tmp_path, ["docs/guide.md", "scripts/thing.py"], "pull_request")
    assert "run=true" in out and "run=false" not in out


def test_the_gate_treats_skill_markdown_as_salient(tmp_path):
    """plugins/**/*.md is content: SKILL.md and PURPOSE.md move digests and
    the lock checks. The inert list must never grow to cover it."""
    out = _run_salience_body(
        tmp_path, ["plugins/adam/skills/x/SKILL.md"], "pull_request")
    assert "run=true" in out and "run=false" not in out


def test_the_gate_runs_when_a_salient_file_is_renamed_to_an_inert_path(tmp_path):
    """With rename detection on, `git diff --name-only` lists only a
    rename's destination, so moving scripts/thing.py to docs/thing.md would
    look docs-only. --no-renames lists both sides, and the source is salient."""
    out = _run_salience_body(
        tmp_path, [], "pull_request",
        renames=[("scripts/thing.py", "docs/thing.md")])
    assert "run=true" in out and "run=false" not in out


def test_the_gate_fails_open_off_pull_request_events(tmp_path):
    out = _run_salience_body(tmp_path, ["docs/guide.md"], "workflow_dispatch")
    assert "run=true" in out and "run=false" not in out


# --- The Claude Code install step (adam-agentskills#28, then "always latest") ---
#
# Harness CLIs always install latest (fleet rule, _agent-guidance "Dependency
# updates"): the step resolves npm's `latest`, installs exactly that, checks
# the `claude` on PATH IS that version (a stale one earlier on PATH would
# shadow it), and records the version. These pin the shape, then execute the
# real step body against fake `npm` / `claude` binaries.

INSTALL_STEP = "Install Claude Code"


def _install_step() -> dict:
    job = load_ci()["jobs"]["plugin-validate"]
    return next(s for s in job["steps"] if s.get("name") == INSTALL_STEP)


def test_the_install_step_pins_no_claude_code_version():
    import re
    assert not re.search(r"claude-code@\d", _install_step()["run"])


def test_the_install_step_always_installs_the_resolved_latest():
    run = _install_step()["run"]
    assert "command -v claude" not in run, "a preinstalled CLI must not be reused"
    assert 'latest="$(npm view @anthropic-ai/claude-code version)"' in run
    assert ('npm install "${args[@]}" "@anthropic-ai/claude-code@${latest}"'
            in run)
    assert "args=(-g)" in run and "args+=(--force)" in run
    assert '[[ "${version%% *}" != "$latest" ]]' in run


def test_the_install_step_bounds_version_and_records_it():
    run = _install_step()["run"]
    assert run.splitlines()[0] == "set -euo pipefail"
    # Each version check is bounded: 60 s, or what is left of the 180 s limit.
    assert 'version="$(timeout -k 10 "$vt" claude --version 2>"$errf")"' in run
    assert "((vt <= 60)) || vt=60" in run
    assert "limit=180" in run and "delays=(5 10 20 40 80)" in run
    assert "did not install a working claude after ${attempt} attempts" in run
    assert "claude --version printed no version" in run
    assert '"$GITHUB_STEP_SUMMARY"' in run
    assert "${{" not in run


def test_the_plugin_validate_job_has_a_timeout():
    minutes = load_ci()["jobs"]["plugin-validate"].get("timeout-minutes")
    assert isinstance(minutes, int) and 0 < minutes <= 30


LATEST = "2.1.300"


def _run_install_step(tmp_path, npm_view=f'echo "{LATEST}"', install_ok=True,
                      claude_body=None, shadow_body=None, timeout_args=None):
    """Run the real step body with fake binaries on PATH.

    The fake `npm view` runs `npm_view`; the fake `npm install -g
    @anthropic-ai/claude-code@X` writes a `claude` into its own bin dir whose
    body is `claude_body` (default: print "X (Claude Code)"). `shadow_body`,
    when set, is a `claude` EARLIER on PATH than npm's bin dir.
    """
    import shutil
    import subprocess
    import sys

    if sys.platform == "win32":
        pytest.skip("fake executables on PATH need a POSIX shell")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the step body")
    shadow = tmp_path / "shadow"
    npm_bin = tmp_path / "npm-bin"
    tools = tmp_path / "tools"
    for d in (shadow, npm_bin, tools):
        d.mkdir()

    def write(path, body):
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    if shadow_body is not None:
        write(shadow / "claude", shadow_body)
    # The installed CLI's body; "@@VER@@" becomes the version npm was asked
    # to install. A template file keeps shell quoting out of the fake npm.
    template = tools / "claude.template"
    template.write_text("#!/bin/sh\n" + (
        claude_body if claude_body is not None
        else 'echo "@@VER@@ (Claude Code)"') + "\n", encoding="utf-8")
    write(tools / "npm", "\n".join([
        'if [ "$1" = view ]; then',
        '  [ "$*" = "view @anthropic-ai/claude-code version" ] || exit 8',
        f'  {npm_view}',
        '  exit $?',
        'fi',
        '[ "$1 $2" = "install -g" ] || exit 9',
        '[ "$3" = --force ] && shift',
        'case "$3" in @anthropic-ai/claude-code@*) ;; *) exit 9 ;; esac',
        'ver="${3#@anthropic-ai/claude-code@}"',
        "exit 1" if not install_ok else ":",
        f'sed "s/@@VER@@/$ver/g" "{template}" > "{npm_bin}/claude"',
        f'chmod 755 "{npm_bin}/claude"',
    ]))
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    env = {"PATH": f"{shadow}:{npm_bin}:{tools}:/usr/bin:/bin",
           "GITHUB_STEP_SUMMARY": str(summary),
           # No real sleep and no wall clock: the retry delays are injected.
           "SLEEP_CMD": "true", "NOW_CMD": "echo 0"}
    body = _install_step()["run"]
    if timeout_args is not None:
        # The shipped bound is 60 s; a test cannot wait that long, so swap
        # in a short one. The shape test pins the shipped value.
        assert 'timeout -k 10 "$vt" ' in body
        body = body.replace('timeout -k 10 "$vt" ', f"timeout {timeout_args} ")
    result = subprocess.run(
        # GitHub's default for a step with no `shell:` is `bash -e {0}`;
        # anything stricter must come from the body's own `set` line.
        [bash, "--noprofile", "--norc", "-e", "-c", body],
        env=env, capture_output=True, text=True, timeout=120)
    return result, summary.read_text(encoding="utf-8")


def test_the_latest_version_is_installed_and_recorded(tmp_path):
    result, summary = _run_install_step(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Claude Code `{LATEST} (Claude Code)` (npm latest)" in summary


def test_a_stale_cli_earlier_on_path_fails_the_step(tmp_path):
    result, summary = _run_install_step(
        tmp_path, shadow_body='echo "2.1.100 (Claude Code)"')
    assert result.returncode != 0
    assert ("did not install a working claude after 6 attempts in 0 seconds "
            f"(last error: claude on PATH reports '2.1.100 (Claude Code)', "
            f"not npm latest {LATEST})") in result.stdout
    assert summary == ""


def test_an_unresolvable_latest_fails_the_step(tmp_path):
    cases = {"exit 1": "could not resolve the latest Claude Code version",
             "true": "npm reported no usable latest Claude Code version",
             "echo '<html>'": "npm reported no usable latest Claude Code version",
             "echo 2.2.0-beta.1": "npm reported no usable latest Claude Code version",
             "printf '2.1.3\\n2.1.4\\n'": "npm reported no usable latest Claude Code version",
             "echo 2x1y3": "npm reported no usable latest Claude Code version",
             "echo ..": "npm reported no usable latest Claude Code version"}
    for view, message in cases.items():
        result, summary = _run_install_step(_fresh(tmp_path), npm_view=view)
        assert result.returncode != 0, view
        assert f"::error::{message}" in result.stdout, (view, result.stdout)
        assert summary == "", view


def test_a_shadowing_cli_whose_version_extends_latest_fails(tmp_path):
    # A prefix match would take 2.1.30 for 2.1.3.
    result, summary = _run_install_step(
        tmp_path, npm_view='echo "2.1.3"',
        shadow_body='echo "2.1.30 (Claude Code)"')
    assert result.returncode != 0
    assert "claude on PATH reports '2.1.30 (Claude Code)', not npm latest 2.1.3)" in result.stdout
    assert summary == ""


def test_multi_line_version_output_keeps_the_first_line(tmp_path):
    result, summary = _run_install_step(
        tmp_path, claude_body='printf "@@VER@@ (Claude Code)\\nnoise\\n"')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"`{LATEST} (Claude Code)` (npm latest)" in summary


def test_a_failed_install_fails_the_step(tmp_path):
    result, summary = _run_install_step(tmp_path, install_ok=False)
    assert result.returncode != 0
    assert summary == ""


def test_a_failing_version_fails_the_step_loudly(tmp_path):
    # It prints the right version and THEN fails, so only the
    # "--version failed" guard can stop it.
    result, summary = _run_install_step(
        tmp_path, claude_body='echo "@@VER@@ (Claude Code)"; exit 3')
    assert result.returncode != 0
    assert "did not install a working claude after" in result.stdout
    assert summary == ""


def test_a_hanging_version_is_ended_even_if_it_ignores_sigterm(tmp_path):
    import time
    start = time.monotonic()
    result, summary = _run_install_step(
        tmp_path, claude_body='trap "" TERM; sleep 30', timeout_args="-k 1 1")
    assert time.monotonic() - start < 60
    assert result.returncode != 0
    assert "did not install a working claude after" in result.stdout
    assert summary == ""


def test_an_empty_version_fails_the_step_loudly(tmp_path):
    result, _ = _run_install_step(tmp_path, claude_body="true")
    assert result.returncode != 0
    assert "(last error: claude --version printed no version)" in result.stdout


def test_markdown_in_the_version_cannot_reach_the_summary(tmp_path):
    result, summary = _run_install_step(
        tmp_path, claude_body='echo "@@VER@@\\` [x](https://example.com) \\`"')
    assert result.returncode == 0, result.stdout + result.stderr
    line = summary.splitlines()[-1]
    assert line.count("`") == 2 and "[" not in line and "]" not in line


def test_a_huge_version_line_is_capped(tmp_path):
    result, summary = _run_install_step(
        tmp_path, claude_body="printf \"@@VER@@ (Claude Code) %0200000d\" 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(summary.splitlines()[-1]) < 200


def _run_retry(tmp_path, *, bad_attempts=0, bad_version=None, install_seconds=0):
    """Run the real step body with a stateful fake npm, a fake sleep and a fake
    clock. The first `bad_attempts` installs leave a broken `claude` (or one
    reporting `bad_version`); the sleep log and npm calls come back too."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the step body")
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    clock, count = tmp_path / "clock", tmp_path / "count"
    npm_log, sleep_log = tmp_path / "npm.log", tmp_path / "sleep.log"
    clock.write_text("1000\n")
    count.write_text("0\n")
    summary = tmp_path / "summary.md"
    q = shlex.quote

    def tool(path, body):
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(0o755)

    bad = (f"echo {q(bad_version + ' (Claude Code)')}" if bad_version
           else "echo 'Error: claude native binary not installed.'; exit 1")
    tool(fakes / "claude.bad", bad + "\n")
    tool(fakes / "claude.good", f"echo '{LATEST} (Claude Code)'\n")
    tool(fakes / "npm", "\n".join([
        'if [ "$1" = view ]; then echo ' + LATEST + '; exit 0; fi',
        f'echo "$*" >> {q(str(npm_log))}',
        f'n=$(( $(<{q(str(count))}) + 1 )); echo $n > {q(str(count))}',
        f'echo $(( $(<{q(str(clock))}) + {install_seconds} )) > {q(str(clock))}',
        f'if [ $n -le {bad_attempts} ]; then src=claude.bad; else src=claude.good; fi',
        f'cp {q(str(fakes))}/$src {q(str(fakes))}/claude',
    ]))
    tool(fakes / "fake-sleep", "\n".join([
        f'echo "$1" >> {q(str(sleep_log))}',
        f'echo $(( $(<{q(str(clock))}) + $1 )) > {q(str(clock))}',
    ]))
    env = {"PATH": f"{fakes}:/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary),
           "SLEEP_CMD": str(fakes / "fake-sleep"), "NOW_CMD": f"cat {clock}"}
    result = subprocess.run([bash, "--noprofile", "--norc", "-e", "-c",
                             _install_step()["run"]],
                            env=env, capture_output=True, text=True, timeout=120)

    def read(p):
        return p.read_text().splitlines() if p.is_file() else []

    return result, read(npm_log), read(sleep_log)


PKG = "@anthropic-ai/claude-code@" + LATEST


def test_success_on_the_first_try_installs_once_without_sleeping(tmp_path):
    result, npm, sleeps = _run_retry(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert npm == [f"install -g {PKG}"]
    assert sleeps == []
    assert f"Claude Code {LATEST} installed and verified on attempt 1" in result.stdout


def test_success_on_the_third_try_records_the_sleeps(tmp_path):
    result, npm, sleeps = _run_retry(tmp_path, bad_attempts=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sleeps == ["5", "10"]
    # A plain install first; every retry adds --force.
    assert npm == [f"install -g {PKG}", f"install -g --force {PKG}",
                   f"install -g --force {PKG}"]
    assert "on attempt 3" in result.stdout


def test_it_gives_up_before_an_attempt_that_would_cross_the_limit(tmp_path):
    # Installs take 30 s each and end at 30, 65, 105 and 155 s; the next delay
    # (40 s) would start attempt 5 at 195 s, past the 180 s limit.
    result, npm, sleeps = _run_retry(tmp_path, bad_attempts=99, install_seconds=30)
    assert result.returncode == 1
    assert sleeps == ["5", "10", "20"]
    assert len(npm) == 4
    errors = [ln for ln in result.stdout.splitlines() if ln.startswith("::error::")]
    assert errors == [
        f"::error::Claude Code {LATEST} did not install a working claude after "
        "4 attempts in 155 seconds (last error: Error: claude native binary "
        "not installed.)"]


def test_a_version_mismatch_is_retried_then_fails(tmp_path):
    result, npm, sleeps = _run_retry(tmp_path, bad_attempts=99,
                                     bad_version="2.1.299")
    assert result.returncode == 1
    assert sleeps == ["5", "10", "20", "40", "80"]
    assert len(npm) == 6
    assert ("(last error: claude on PATH reports '2.1.299 (Claude Code)', not "
            f"npm latest {LATEST})") in result.stdout


def _fresh(tmp_path):
    import itertools
    for i in itertools.count():
        d = tmp_path / f"case{i}"
        if not d.exists():
            d.mkdir()
            return d


# --- Action pins: actions.lock and its verify step ---------------------------
#
# Steps name third-party actions by release tag and .github/workflows/actions.lock
# pins each tag to a commit (ADR 0024 in _agent-guidance). The `pytest` job's
# "Verify action lockfile" step runs the shared verify-actions-lock action.

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA_REF = re.compile(r"@[0-9a-f]{40}\Z")


def test_cross_repo_reusable_workflows_are_pinned_to_a_full_sha():
    # actions.lock does not lock reusable workflows, and neither `--verify` nor `sha_pinning_required` flags a tag there.
    paths = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert paths
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            uses = job.get("uses") if isinstance(job, dict) else None
            if uses is None or uses.startswith(("./", "$/", "Adam-S-Daniel/cms-platform/")):
                continue
            assert FULL_SHA_REF.search(uses), (
                f"{path.name} job `{job_id}` calls a reusable workflow by "
                f"`{uses}`; pin it to a full 40-character lowercase commit SHA."
            )


VERIFY_STEP = "Verify action lockfile"
VERIFY_USES = re.compile(r"^Adam-S-Daniel/verify-actions-lock@v\d+\.\d+\.\d+$")


def test_the_pytest_job_verifies_the_action_lockfile():
    steps = [s for s in load_ci()["jobs"]["pytest"]["steps"]
             if s.get("name") == VERIFY_STEP]
    assert len(steps) == 1, f"job `pytest` must carry exactly one `{VERIFY_STEP}` step"
    step = steps[0]
    assert VERIFY_USES.match(step.get("uses", "")), step.get("uses")
    # The action decides by itself whether the PR touched workflow dependencies.
    assert "if" not in step
    assert "continue-on-error" not in step


def test_every_ci_action_is_locked_to_a_full_sha():
    lock = yaml.safe_load((WORKFLOWS_DIR / "actions.lock").read_text(encoding="utf-8"))
    listed = lock["workflows"][".github/workflows/ci.yml"]
    checked = 0
    for job in load_ci()["jobs"].values():
        for step in job.get("steps", []):
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
            assert entry["ref"] == uses.rsplit("@", 1)[1], (uses, entry)
            assert key in listed, f"{uses} is not listed for ci.yml in actions.lock"
            checked += 1
    assert checked, "expected at least one third-party action to check"


SKILL_GATES_STEP = "Check skill graduation and touch gates"


def test_the_pytest_job_runs_the_skill_gates_on_pull_requests():
    """AGENTS.md's graduation and touch gates are enforced only if the step
    sits in a REQUIRED job, runs after the dependencies it imports are
    installed, and reads the shas from env rather than interpolating them."""
    steps = load_ci()["jobs"]["pytest"]["steps"]
    names = [s.get("name") for s in steps]
    matches = [s for s in steps if s.get("name") == SKILL_GATES_STEP]
    assert len(matches) == 1, f"job `pytest` must carry exactly one `{SKILL_GATES_STEP}` step"
    step = matches[0]
    assert names.index("Install Python dependencies") < names.index(SKILL_GATES_STEP) \
        < names.index("Run every test suite in the repo")
    assert step["if"] == ("github.event_name == 'pull_request' && "
                          "steps.salient.outputs.run == 'true'")
    assert step["env"] == {"BASE_SHA": "${{ github.event.pull_request.base.sha }}",
                           "HEAD_SHA": "${{ github.event.pull_request.head.sha }}"}
    assert shlex.split(step["run"]) == ["python3", "scripts/check_skill_gates.py",
                                        "--base", "$BASE_SHA", "--head", "$HEAD_SHA"]
    assert "${{" not in step["run"]
    assert "continue-on-error" not in step


ONE_WAY_DOORS_STEP = "Shipped names and loop pull requests pass the one-way-door check"


def test_the_pytest_job_runs_the_one_way_door_check_on_every_pull_request():
    """Issue #133: the check guards one-way doors only from a REQUIRED job, on
    every pull request (not just salient ones), with the body read from the API
    so a re-run sees an edited body, and with nothing interpolated into run:."""
    steps = load_ci()["jobs"]["pytest"]["steps"]
    names = [s.get("name") for s in steps]
    matches = [s for s in steps if s.get("name") == ONE_WAY_DOORS_STEP]
    assert len(matches) == 1, f"job `pytest` must carry exactly one `{ONE_WAY_DOORS_STEP}` step"
    step = matches[0]
    assert names.index(SKILL_GATES_STEP) < names.index(ONE_WAY_DOORS_STEP) \
        < names.index("Run every test suite in the repo")
    assert step["if"] == "github.event_name == 'pull_request'"
    assert step["env"] == {"HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
                           "GH_TOKEN": "${{ github.token }}"}
    # The base comes from the API's current base.ref, so a retargeted PR is
    # judged against its new base, and the trigger re-runs on that edit.
    assert 'base="refs/remotes/origin/$(jq -r .base.ref "$d/pr.json")"' in step["run"]
    assert '--base "$base"' in step["run"]
    # The base branch's copy runs, so a PR cannot loosen its own judge.
    assert 'git show "$base:scripts/check_one_way_doors.py" > "$d/check.py"' in step["run"]
    assert 'python3 -I "$d/check.py"' in step["run"]
    types = ci_triggers(load_ci())["pull_request"]["types"]
    assert {"opened", "synchronize", "reopened", "edited", "ready_for_review",
            "auto_merge_enabled", "auto_merge_disabled"} <= set(types)
    assert "${{" not in step["run"]
    assert 'gh api "repos/$GITHUB_REPOSITORY/pulls/$n"' in step["run"]
    assert "--open-prs" in step["run"]
    assert "continue-on-error" not in step
    assert load_ci()["jobs"]["pytest"]["permissions"]["pull-requests"] == "read"


AGENT_CHECKS_WORKFLOW = WORKFLOWS_DIR / "agent-checks.yml"


def load_agent_checks() -> dict:
    return yaml.safe_load(AGENT_CHECKS_WORKFLOW.read_text(encoding="utf-8"))


def test_agent_checks_is_a_job_of_its_own_workflow_and_not_of_ci():
    """The required context `agent-checks` is published once: the job lives in
    agent-checks.yml (so a description edit re-runs it without re-running the
    suites) and ci.yml must not carry a second one."""
    assert "agent-checks" in load_agent_checks()["jobs"]
    assert "agent-checks" not in load_ci()["jobs"]


def test_the_agent_checks_job_publishes_its_context_under_its_job_id():
    job = load_agent_checks()["jobs"]["agent-checks"]
    assert "name" not in job
    assert "matrix" not in (job.get("strategy") or {})


def test_no_concurrency_group_governs_the_agent_checks_job():
    doc = load_agent_checks()
    assert "concurrency" not in doc
    assert "concurrency" not in doc["jobs"]["agent-checks"]


def test_agent_checks_reports_on_every_pull_request_and_reruns_on_an_edit():
    pull_request = ci_triggers(load_agent_checks())["pull_request"]
    narrowing = {"paths", "paths-ignore", "branches", "branches-ignore"}
    assert not (narrowing & set(pull_request)), "a filter would leave the required context unreported"
    assert {"opened", "edited", "reopened", "synchronize"} <= set(pull_request["types"])


ONE_WAY_DOORS_RECHECK = "The one-way-door check still passes with the pull request as it is now"


def test_the_one_way_door_check_runs_again_as_the_pytest_jobs_last_step():
    """Round 9: several runs share a head sha, and the one that reports last
    must have read the pull request's body, draft state, arming and base late."""
    steps = load_ci()["jobs"]["pytest"]["steps"]
    last = steps[-1]
    assert last.get("name") == ONE_WAY_DOORS_RECHECK
    assert last["if"] == "github.event_name == 'pull_request'"
    assert 'gh api "repos/$GITHUB_REPOSITORY/pulls/$n" > "$d/pr.json"' in last["run"]
    assert 'python3 -I "$d/check.py" --base "$base"' in last["run"]
    assert "${{" not in last["run"]
    # Round 14: the suites ran the pull request's code since the first copy.
    assert last["run"].index('git show "$base:scripts/check_one_way_doors.py" > "$d/check.py"') \
        < last["run"].index('python3 -I "$d/check.py"')


LOOP_PATH_GUARD = "Loop pull requests change nothing that runs before their check"


def test_loop_pull_requests_are_held_to_their_paths_before_any_of_their_code_runs():
    """Round 13: the pip install and the skill gates run a pull request's
    code before the one-way-door check; for an unreviewed loop pull request
    that code could make the check pass. A bash-only guard runs first."""
    steps = load_ci()["jobs"]["pytest"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index(LOOP_PATH_GUARD) < names.index("Install Python dependencies") \
        < names.index(SKILL_GATES_STEP) < names.index(ONE_WAY_DOORS_STEP)
    guard = steps[names.index(LOOP_PATH_GUARD)]
    assert guard["if"] == "github.event_name == 'pull_request'"
    assert "python" not in guard["run"] and "${{" not in guard["run"]
    assert 'git show "$base:scripts/loop_prs.json"' in guard["run"]
    assert "^plugins/[^/]+/skills/[^/]+/." in guard["run"] and "scripts/check" not in guard["run"]


def _run_guard(tmp_path, ref: str, changed: str, stale=None, api_base: str = "main",
               base=None) -> int:
    import json
    import subprocess
    run = next(s for s in load_ci()["jobs"]["pytest"]["steps"]
               if s.get("name") == LOOP_PATH_GUARD)["run"]
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repo), *a],  # noqa: E731
                                    check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "loop_prs.json").write_text(json.dumps(
        {"lanes": {"draft-skill/": "create", "eval-improve/": "edit"}}), encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    target = repo / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    git("checkout", "-q", "-b", "head")
    git("add", "-A")
    git("commit", "-q", "-m", "change")
    head = git("rev-parse", "HEAD")
    # The workspace is the merge commit, as actions/checkout leaves it.
    git("checkout", "-q", "main" if base is None else base(git))
    git("merge", "-q", "--no-ff", "-m", "merge", head if stale is None else stale(git, head))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {
        "number": 7, "base": {"ref": "main"}, "head": {"sha": head, "ref": ref}}}),
        encoding="utf-8")
    # A stand-in for `gh api .../pulls/N --jq .base.ref`: the base as it is now.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text(f"#!/bin/sh\necho {api_base}\n", encoding="utf-8", newline="\n")
    (fake / "gh").chmod(0o755)
    script = tmp_path / "guard.sh"
    script.write_text(run, encoding="utf-8", newline="\n")
    proc = subprocess.run(["bash", str(script)], cwd=repo, capture_output=True, text=True,
                          env={**os.environ, "GITHUB_EVENT_PATH": str(event),
                               "GITHUB_REPOSITORY": "Example/registry",
                               "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"})
    return proc.returncode


@pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None,
                    reason="runs the step's bash with git and jq, as the Linux runner does")
@pytest.mark.parametrize("changed, code", [
    ("plugins/p/skills/s/SKILL.md", 0),
    ("plugins/p/.claude-plugin/plugin.json", 0),
    ("plugins/p/hooks/skills/a/run.sh", 1),
    ("plugins/p/hooks/plugin.json", 1),
    ("requirements-dev.txt", 1),
])
def test_the_loop_path_guard_matches_whole_path_segments(tmp_path, changed, code):
    """Round 14: a case glob's `*` matches `/`, so it admitted hook paths."""
    assert _run_guard(tmp_path, "eval-improve/s", changed) == code


@pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None,
                    reason="runs the step's bash with git and jq, as the Linux runner does")
def test_the_loop_path_guard_refuses_a_workspace_of_another_head(tmp_path):
    """Round 16: a merge ref lagging a push would install an older head's files."""
    def older(git, head):
        git("checkout", "-q", "-b", "older", "main")
        git("commit", "-q", "--allow-empty", "-m", "older head")
        older_head = git("rev-parse", "HEAD")
        git("checkout", "-q", "main")
        return older_head
    assert _run_guard(tmp_path, "eval-improve/s", "plugins/p/skills/s/SKILL.md", older) == 1


@pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None,
                    reason="runs the step's bash with git and jq, as the Linux runner does")
def test_the_loop_path_guard_refuses_a_workspace_merged_into_another_base(tmp_path):
    """Round 17: a re-run keeps a workspace merged into the base from before
    a retarget, whose requirements-dev.txt nobody judged."""
    def other(git):
        git("checkout", "-q", "-b", "other", "main")
        (Path(git("rev-parse", "--show-toplevel")) / "requirements-dev.txt").write_text(
            "./invented\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "other base")
        return "other"
    assert _run_guard(tmp_path, "eval-improve/s", "plugins/p/skills/s/SKILL.md",
                      base=other) == 1


@pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None,
                    reason="runs the step's bash with git and jq, as the Linux runner does")
def test_the_loop_path_guard_fails_closed_without_a_readable_base(tmp_path):
    assert _run_guard(tmp_path, "eval-improve/s", "plugins/p/skills/s/SKILL.md",
                      api_base="gone") == 1


def test_the_loop_path_guard_names_the_path_it_refuses(tmp_path):
    run = next(s for s in load_ci()["jobs"]["pytest"]["steps"]
               if s.get("name") == LOOP_PATH_GUARD)["run"]
    assert '"$path"' in run.split("*)", 1)[1].split(";;", 1)[0]


@pytest.mark.skipif(os.name == "nt" or shutil.which("jq") is None,
                    reason="runs the step's bash with git and jq, as the Linux runner does")
def test_the_loop_path_guard_ignores_other_branches(tmp_path):
    assert _run_guard(tmp_path, "claude/some-change", "requirements-dev.txt") == 0
