#!/usr/bin/env python3
"""Tests for adam-anything-anywhere's fleet-guidance SessionStart hook (ADR 0023).

Covers plugins/adam-anything-anywhere/hooks/: on-session-start.sh (the plugin's
own hook, run in place), the bundled fleet-memory.sh and fleet-guidance.md it
installs through, and the shape of hooks/claude-code.json and the manifests.

Hermetic and deterministic:
  - nothing touches the network: PATH starts with a tmp dir holding a FAKE
    curl (a small bash script the fixture writes). It records its argv, then
    serves a canned GitHub API answer or a canned base.md and exits with a
    canned status; any other URL exits 6;
  - HOME, CLAUDE_CONFIG_DIR, XDG_STATE_HOME, XDG_CACHE_HOME, TMPDIR and
    CLAUDE_PROJECT_DIR (an empty dir) are tmp dirs, CODEX_HOME names a path that
    does not exist, and every FLEET_* variable is unset unless a case sets it.
    The session's environment is built from scratch, so nothing leaks in;
  - the one git repository, a scratch "session checkout", is built with fixed
    author and committer dates (@1700000900) and no global git config;
  - the API answer is invented: a made-up 40-hex commit id and the committer
    date 2023-11-14T22:13:20Z (epoch 1700000000). No sleeps and no wall
    clock. The one timeout is a deadlock guard on a subprocess.

The shell-driven tests are POSIX-only; the shape tests also run on Windows.

Run: python3 -m pytest scripts/test_fleet_guidance_hook.py -q
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_consistency  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "adam-anything-anywhere"
HOOKS = PLUGIN / "hooks"
GUIDANCE = HOOKS / "fleet-guidance"
ON_SESSION_START = GUIDANCE / "on-session-start.sh"
FLEET_MEMORY = GUIDANCE / "fleet-memory.sh"
BUNDLED_PAYLOAD = GUIDANCE / "fleet-guidance.md"
BASH = shutil.which("bash")

posix_only = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="drives the hook scripts with a POSIX bash and a fake curl")

# The bytes every base.md starts with, and so every payload the hook accepts.
HEADER = ("# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**").encode()
BODY_TAIL = (b"\n> Edit only below the `## Repo-specific additions` header.\n"
             b"> Everything above it will be overwritten on the next sync.\n\n")

MAX_BYTES = 1048576
SHA = "0123456789abcdef0123456789abcdef01234567"  # invented
DATE = "2023-11-14T22:13:20Z"
EPOCH = 1700000000
API_URL = "https://api.github.com/repos/Adam-S-Daniel/_agent-guidance/commits?sha=main&per_page=1"
RAW_URL = f"https://raw.githubusercontent.com/Adam-S-Daniel/_agent-guidance/{SHA}/agents-md/base.md"
SOURCE_LINE = ("fleet-guidance-source: the adam-anything-anywhere plugin's bundled copy "
               "({reason}), which can be older than _agent-guidance main")

FAKE_CURL = r"""#!/usr/bin/env bash
printf '%s\t' "$@" >> "$FAKE_CURL_LOG"
printf '\n' >> "$FAKE_CURL_LOG"
url=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    https://*) url="$1" ;;
  esac
  shift
done
case "$url" in
  https://api.github.com/*) body="$FAKE_API_BODY"; code="${FAKE_API_EXIT:-0}" ;;
  https://raw.githubusercontent.com/*) body="$FAKE_RAW_BODY"; code="${FAKE_RAW_EXIT:-0}" ;;
  *) exit 6 ;;
esac
if [ -n "$out" ]; then cp -- "$body" "$out"; fi
exit "$code"
"""


def clean_path():
    return os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and "/mnt/" not in p)


def sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def payload(canary: str, size: int = 0) -> bytes:
    """A body that passes the hook's validation, padded to `size` bytes if given."""
    body = HEADER + BODY_TAIL + canary.encode() + b"\n"
    if size:
        assert len(body) <= size
        body += b"x" * (size - len(body))
    return body


def api_answer(sha=SHA, date=DATE) -> str:
    committer = {} if date is None else {"date": date}
    return json.dumps([{"sha": sha, "commit": {"committer": committer}}])


def tool_farm(directory: Path, leave_out) -> Path:
    """A directory of symlinks to every tool on the clean PATH except `leave_out`."""
    directory.mkdir(parents=True, exist_ok=True)
    for entry in clean_path().split(os.pathsep):
        folder = Path(entry)
        if not folder.is_dir():
            continue
        for tool in folder.iterdir():
            if leave_out(tool.name) or (directory / tool.name).exists() \
                    or (directory / tool.name).is_symlink():
                continue
            (directory / tool.name).symlink_to(tool)
    return directory


class World:
    """One tmp HOME, one fake curl, and the canned answers it serves."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.claude_dir = tmp_path / "claude"
        self.tmpdir = tmp_path / "tmpdir"
        self.cwd = tmp_path / "cwd"
        self.bin = tmp_path / "bin"
        self.project = tmp_path / "project"  # CLAUDE_PROJECT_DIR: no checkout in it
        for d in (self.home, self.tmpdir, self.cwd, self.bin, self.project):
            d.mkdir()
        self.log = tmp_path / "curl.log"
        self.log.write_text("", encoding="utf-8")
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(0o755)
        self.api_body = tmp_path / "api.json"
        self.raw_body = tmp_path / "raw.md"
        self.fetched = payload("FETCHED CANARY")
        self.set_api(api_answer())
        self.set_raw(self.fetched)
        self.path = f"{self.bin}{os.pathsep}{clean_path()}"

    @property
    def claude_md(self) -> Path:
        return self.claude_dir / "CLAUDE.md"

    def set_api(self, text: str):
        self.api_body.write_text(text, encoding="utf-8")

    def set_raw(self, data: bytes):
        self.raw_body.write_bytes(data)

    def env(self, **extra) -> dict:
        env = {
            "PATH": self.path, "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.claude_dir),
            "CODEX_HOME": str(self.tmp / "no-codex"),
            "TMPDIR": str(self.tmpdir),
            "XDG_STATE_HOME": str(self.tmp / "state"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "PYTHONIOENCODING": "utf-8",
            "CLAUDE_PROJECT_DIR": str(self.project),
            "FAKE_CURL_LOG": str(self.log),
            "FAKE_API_BODY": str(self.api_body),
            "FAKE_RAW_BODY": str(self.raw_body),
        }
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run(self, **extra):
        proc = subprocess.run([BASH, str(ON_SESSION_START)], env=self.env(**extra),
                              cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                              capture_output=True, encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc
        return proc

    def curl_calls(self):
        text = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        return [line.rstrip("\t").split("\t") for line in text.splitlines()]

    def urls(self):
        found = []
        for argv in self.curl_calls():
            urls = [a for a in argv if a.startswith("https://")]
            assert len(urls) == 1, argv
            found.append(urls[0])
        return found

    def preinstall(self, canary: str, stamp: int):
        """Install a block by running the bundled fleet-memory.sh directly."""
        source = self.tmp / f"preinstall-{canary.replace(' ', '-')}.md"
        source.write_bytes(payload(canary))
        proc = subprocess.run(
            [BASH, str(FLEET_MEMORY)],
            env=self.env(FLEET_GUIDANCE_PAYLOAD=str(source),
                         FLEET_GUIDANCE_PAYLOAD_STAMP=str(stamp)),
            cwd=str(self.cwd), stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", timeout=120)
        assert proc.returncode == 0 and proc.stdout.startswith("fleet-guidance: installed"), proc


    def checkout(self, body: bytes, stamp: int, name: str = "checkout") -> Path:
        """A scratch git repo whose .claude/hooks/fleet-guidance.md is `body`,
        committed at `stamp`: the shape of a fleet repo's own copy."""
        repo = self.tmp / name
        payload_file = repo / ".claude" / "hooks" / "fleet-guidance.md"
        payload_file.parent.mkdir(parents=True)
        payload_file.write_bytes(body)
        when = f"@{stamp} +0000"
        git_env = {
            "PATH": clean_path(), "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Example Person",
            "GIT_AUTHOR_EMAIL": "person@example.com", "GIT_COMMITTER_NAME": "Example Person",
            "GIT_COMMITTER_EMAIL": "person@example.com", "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        }
        for args in (["init", "-q", "-b", "main"], ["add", "."],
                     ["commit", "-q", "-m", "carry the guidance"]):
            proc = subprocess.run(["git", *args], cwd=str(repo), env=git_env,
                                  capture_output=True, text=True)
            assert proc.returncode == 0, (args, proc.stderr)
        return repo


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def verdict_version(stdout: str) -> str:
    match = re.match(r"fleet-guidance: (?:installed|current) \(v([0-9a-f]{8}), \d+ bytes\)",
                     stdout.splitlines()[0])
    assert match, stdout
    return match.group(1)


def assert_temp_dir_gone(world: World):
    assert list(world.tmpdir.glob("fleet-guidance.*")) == []


def assert_bundled_fallback(world: World, proc, reason: str):
    lines = proc.stdout.splitlines()
    assert len(lines) == 2, proc.stdout
    assert verdict_version(proc.stdout) == sha8(BUNDLED_PAYLOAD.read_bytes())
    assert lines[1] == SOURCE_LINE.format(reason=reason)
    assert world.claude_md.is_file()
    assert "FETCHED CANARY" not in world.claude_md.read_text(encoding="utf-8")
    assert_temp_dir_gone(world)


# =================================================================================
# The fetch succeeds
# =================================================================================


@posix_only
def test_a_successful_fetch_installs_the_current_base_md(world):
    proc = world.run(GITHUB_TOKEN="ghp_example_not_real", GH_TOKEN="ghp_example_not_real")
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert "fleet-guidance-source:" not in proc.stdout
    assert proc.stderr == ""
    text = world.claude_md.read_text(encoding="utf-8")
    assert "FETCHED CANARY" in text
    assert f"fleet-guidance-delivered: {EPOCH}" in text

    # Exactly the commits request, then the raw file at that commit.
    assert world.urls() == [API_URL, RAW_URL]
    calls = world.curl_calls()
    for argv in calls:
        assert "--proto" in argv and argv[argv.index("--proto") + 1] == "=https", argv
        assert "--proto-redir" in argv, argv
        assert "--max-time" in argv, argv
        assert "-L" not in argv and "--location" not in argv, argv
        joined = " ".join(argv).lower()
        for forbidden in ("authorization", "bearer", "ghp_example_not_real", "--netrc", "-u "):
            assert forbidden not in joined, argv
        # The scratch files live under TMPDIR.
        target = argv[argv.index("-o") + 1]
        assert target.startswith(str(world.tmpdir) + os.sep + "fleet-guidance."), target
    assert_temp_dir_gone(world)


@posix_only
def test_a_body_of_exactly_the_size_limit_is_accepted(world):
    world.fetched = payload("FETCHED CANARY", MAX_BYTES)
    world.set_raw(world.fetched)
    proc = world.run()
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert "fleet-guidance-source:" not in proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)


@posix_only
@pytest.mark.parametrize("value", ["", "1", "true", "yes"])
def test_fetching_stays_on_for_any_other_fetch_setting(world, value):
    proc = world.run(FLEET_GUIDANCE_FETCH=value)
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [API_URL, RAW_URL]


@posix_only
def test_an_empty_payload_override_is_not_an_override(world):
    proc = world.run(FLEET_GUIDANCE_PAYLOAD="")
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [API_URL, RAW_URL]


@posix_only
def test_the_hook_does_not_depend_on_the_working_directory_or_the_exec_bit(world, tmp_path):
    # The command line runs `bash <script>` from wherever the session starts,
    # and a synced copy of the plugin may have lost its modes.
    copy = tmp_path / "copy"
    shutil.copytree(GUIDANCE, copy)
    for script in copy.glob("*.sh"):
        script.chmod(0o644)
    proc = subprocess.run([BASH, str(copy / "on-session-start.sh")], env=world.env(),
                          cwd=str(world.cwd), stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)


@posix_only
def test_the_hook_never_reads_stdin(world, tmp_path):
    # stdin is a pipe nobody writes to or closes. A hook that read it would
    # block; the timeout is only the guard against that deadlock.
    out, err = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    with open(out, "wb") as out_handle, open(err, "wb") as err_handle:
        proc = subprocess.Popen([BASH, str(ON_SESSION_START)], env=world.env(),
                                cwd=str(world.cwd), stdin=subprocess.PIPE,
                                stdout=out_handle, stderr=err_handle)
        try:
            code = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("the hook blocked, most likely reading stdin")
        finally:
            proc.stdin.close()
    assert code == 0
    assert out.read_text(encoding="utf-8").startswith("fleet-guidance: installed")


@posix_only
def test_a_carriage_return_after_the_python_step_is_tolerated(world, tmp_path):
    # Python on Windows writes \r\n in text mode, so `$(...)` would keep a \r on
    # the epoch. This python3 wraps the real one and ends every stdout line so.
    real = shutil.which("python3", path=clean_path())
    if real is None:
        pytest.skip("no python3 on the clean PATH to wrap")
    seen = tmp_path / "wrapper.log"
    wrapper = world.bin / "python3"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$#\" >> {shlex.quote(str(seen))}\n"
        f"{shlex.quote(real)} \"$@\" | awk '{{ printf \"%s\\r\\n\", $0 }}'\n"
        "exit \"${PIPESTATUS[0]}\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    proc = world.run()
    assert seen.read_text(encoding="utf-8") != "", "the wrapper was never used"
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert f"fleet-guidance-delivered: {EPOCH}" in world.claude_md.read_text(encoding="utf-8")
    assert world.urls() == [API_URL, RAW_URL]


# =================================================================================
# The session's own checkout wins, with no network
# =================================================================================


CHECKOUT_STAMP = 1700000900


@posix_only
def test_the_sessions_own_checkout_is_installed_without_the_network(world):
    body = payload("CHECKOUT CANARY")
    repo = world.checkout(body, CHECKOUT_STAMP)
    proc = world.run(CLAUDE_PROJECT_DIR=str(repo))
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert "fleet-guidance-source:" not in proc.stdout
    assert verdict_version(proc.stdout) == sha8(body)
    text = world.claude_md.read_text(encoding="utf-8")
    assert "CHECKOUT CANARY" in text and "FETCHED CANARY" not in text
    assert f"fleet-guidance-delivered: {CHECKOUT_STAMP}" in text
    assert world.curl_calls() == []
    assert_temp_dir_gone(world)


@posix_only
def test_the_checkout_wins_even_when_fetching_is_off(world):
    repo = world.checkout(payload("CHECKOUT CANARY"), CHECKOUT_STAMP)
    proc = world.run(CLAUDE_PROJECT_DIR=str(repo), FLEET_GUIDANCE_FETCH="off")
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert "fleet-guidance-source:" not in proc.stdout
    assert "CHECKOUT CANARY" in world.claude_md.read_text(encoding="utf-8")
    assert world.curl_calls() == []


@posix_only
def test_the_checkout_does_not_outrank_a_later_delivery(world):
    # fleet-memory.sh orders by stamp, so a checkout older than the block
    # already installed leaves it alone.
    world.preinstall("NEWER CANARY", 1800000000)
    before = world.claude_md.read_bytes()
    repo = world.checkout(payload("CHECKOUT CANARY"), CHECKOUT_STAMP)
    proc = world.run(CLAUDE_PROJECT_DIR=str(repo))
    assert "kept newer" in proc.stdout, proc.stdout
    assert world.claude_md.read_bytes() == before


@posix_only
def test_skip_outranks_the_checkout(world):
    repo = world.checkout(payload("CHECKOUT CANARY"), CHECKOUT_STAMP)
    proc = world.run(CLAUDE_PROJECT_DIR=str(repo), FLEET_GUIDANCE_SKIP="1")
    assert proc.stdout.startswith("fleet-guidance: skipped"), proc.stdout
    assert world.curl_calls() == []
    assert not world.claude_md.exists() or "CHECKOUT CANARY" not in world.claude_md.read_text(
        encoding="utf-8")


@posix_only
def test_a_payload_override_outranks_the_checkout(world, tmp_path):
    repo = world.checkout(payload("CHECKOUT CANARY"), CHECKOUT_STAMP)
    override = tmp_path / "override.md"
    override.write_bytes(payload("OVERRIDE CANARY"))
    proc = world.run(CLAUDE_PROJECT_DIR=str(repo), FLEET_GUIDANCE_PAYLOAD=str(override))
    assert proc.stdout == "" and proc.stderr == ""
    assert not world.claude_md.exists()


def _project_without_a_usable_copy(world, kind):
    if kind == "unset":
        return None
    if kind == "empty-string":
        return ""
    if kind == "missing-dir":
        return str(world.tmp / "no-such-project")
    hooks = world.project / ".claude" / "hooks"
    if kind == "empty-dir":
        return str(world.project)
    hooks.mkdir(parents=True)
    if kind == "hooks-dir-without-the-file":
        return str(world.project)
    target = hooks / "fleet-guidance.md"
    if kind == "empty-file":
        target.write_bytes(b"")
    else:
        assert kind == "directory"
        target.mkdir()
    return str(world.project)


@posix_only
@pytest.mark.parametrize("kind", ["unset", "empty-string", "missing-dir", "empty-dir",
                                  "hooks-dir-without-the-file", "empty-file", "directory"])
def test_a_project_dir_without_a_usable_copy_still_fetches(world, kind):
    value = _project_without_a_usable_copy(world, kind)
    proc = world.run(CLAUDE_PROJECT_DIR=value)
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert "FETCHED CANARY" in world.claude_md.read_text(encoding="utf-8")
    assert world.urls() == [API_URL, RAW_URL]


# =================================================================================
# The fetch fails: the bundled copy is installed and the source is named
# =================================================================================


@posix_only
def test_a_failed_api_request_falls_back_to_the_bundled_copy(world):
    proc = world.run(FAKE_API_EXIT="22")
    assert_bundled_fallback(world, proc, "the GitHub API request failed")
    assert world.urls() == [API_URL]


BAD_API_ANSWERS = [
    pytest.param("not json", id="invalid-json"),
    pytest.param("[]", id="empty-list"),
    pytest.param("{}", id="object"),
    pytest.param('[{"sha": "main", "commit": {"committer": {"date": "%s"}}}]' % DATE,
                 id="sha-main"),
    pytest.param(api_answer(sha=SHA[:39]), id="sha-39-hex"),
    pytest.param(api_answer(sha=SHA.upper()), id="sha-uppercase"),
    pytest.param(api_answer(sha=SHA + "0"), id="sha-41-hex"),
    pytest.param(api_answer(date="2023-11-14 22:13:20"), id="date-with-space"),
    pytest.param(api_answer(date="2023-11-14T22:13:20+00:00"), id="date-with-offset"),
    pytest.param(api_answer(date="2023-13-45T99:13:20Z"), id="date-out-of-range"),
    pytest.param(api_answer(date="1970-01-01T00:00:00Z"), id="date-epoch-zero"),
    pytest.param(api_answer(date="9999-12-31T23:59:59Z"), id="date-epoch-over-ten-digits"),
    pytest.param(api_answer(date=None), id="date-missing"),
    pytest.param(json.dumps([{"sha": 5, "commit": {"committer": {"date": DATE}}}]),
                 id="sha-not-a-string"),
    pytest.param(json.dumps([None]), id="element-not-an-object"),
]


@posix_only
@pytest.mark.parametrize("answer", BAD_API_ANSWERS)
def test_an_answer_that_is_not_a_commit_falls_back(world, answer):
    world.set_api(answer)
    proc = world.run()
    assert_bundled_fallback(world, proc, "the GitHub API answer was not a commit")
    assert world.urls() == [API_URL]


@posix_only
def test_a_failed_download_falls_back_to_the_bundled_copy(world):
    proc = world.run(FAKE_RAW_EXIT="22")
    assert_bundled_fallback(world, proc, "the base.md download failed")
    assert world.urls() == [API_URL, RAW_URL]


BAD_RAW_BODIES = [
    pytest.param(b"<html><body>429 Too Many Requests</body></html>\n", id="html-error-page"),
    pytest.param(b"", id="empty"),
    pytest.param(HEADER[:-1], id="header-short-by-one-byte"),
    pytest.param(b"\n" + HEADER + BODY_TAIL, id="header-after-a-blank-line"),
    pytest.param(payload("FETCHED CANARY", MAX_BYTES + 1), id="one-byte-over-the-limit"),
]


@posix_only
@pytest.mark.parametrize("body", BAD_RAW_BODIES)
def test_a_download_that_fails_validation_falls_back(world, body):
    world.set_raw(body)
    proc = world.run()
    assert_bundled_fallback(world, proc, "the downloaded base.md failed validation")
    assert world.urls() == [API_URL, RAW_URL]


@posix_only
@pytest.mark.parametrize("value", ["0", "off", "FALSE", "No"])
def test_fetching_can_be_turned_off(world, value):
    proc = world.run(FLEET_GUIDANCE_FETCH=value)
    assert_bundled_fallback(world, proc, "fetching is off (FLEET_GUIDANCE_FETCH)")
    assert world.curl_calls() == []


@posix_only
def test_a_missing_curl_falls_back(world, tmp_path):
    world.path = str(tool_farm(tmp_path / "farm", lambda name: name == "curl"))
    assert shutil.which("curl", path=world.path) is None
    proc = world.run()
    assert_bundled_fallback(world, proc, "curl is not installed")
    assert world.curl_calls() == []


@posix_only
def test_a_python_that_does_not_run_falls_back(world, tmp_path):
    # The Windows Store stub: it resolves on PATH and exits 49.
    farm = tool_farm(tmp_path / "farm", lambda name: name.startswith("python") or name == "curl")
    stub = farm / "python3"
    stub.write_text("#!/bin/sh\necho 'Python was not found' >&2\nexit 49\n", encoding="utf-8")
    stub.chmod(0o755)
    (farm / "curl").symlink_to(world.bin / "curl")
    world.path = str(farm)
    proc = world.run()
    lines = proc.stdout.splitlines()
    # The bundled installer needs Python too, so only the source line is certain.
    assert lines[-1] == SOURCE_LINE.format(reason="no working Python"), proc.stdout
    assert world.curl_calls() == []
    assert_temp_dir_gone(world)


@posix_only
def test_an_unusable_temp_directory_falls_back(world, tmp_path):
    missing = tmp_path / "no-such-tmp"
    proc = world.run(TMPDIR=str(missing))
    lines = proc.stdout.splitlines()
    assert verdict_version(proc.stdout) == sha8(BUNDLED_PAYLOAD.read_bytes())
    assert lines[-1] == SOURCE_LINE.format(reason="cannot create a temporary directory")
    assert world.curl_calls() == []


# =================================================================================
# The knobs that mean "not this hook"
# =================================================================================


def pre_existing_block(world: World):
    world.claude_dir.mkdir(parents=True, exist_ok=True)
    world.claude_md.write_text("my own notes\n", encoding="utf-8")
    world.preinstall("PREINSTALLED CANARY", 1500000000)
    text = world.claude_md.read_text(encoding="utf-8")
    assert "my own notes" in text and "BEGIN FLEET GUIDANCE" in text


@posix_only
@pytest.mark.parametrize("value", ["1", "true", "YES", "anything"])
def test_skip_removes_the_block_and_never_calls_curl(world, value):
    pre_existing_block(world)
    proc = world.run(FLEET_GUIDANCE_SKIP=value)
    assert proc.stdout.startswith("fleet-guidance: skipped"), proc.stdout
    assert world.curl_calls() == []
    text = world.claude_md.read_text(encoding="utf-8")
    assert "BEGIN FLEET GUIDANCE" not in text and "PREINSTALLED CANARY" not in text
    assert "my own notes" in text
    assert_temp_dir_gone(world)


@posix_only
@pytest.mark.parametrize("value", ["", "0", "false", "No", "OFF"])
def test_skip_values_that_mean_off_do_not_skip(world, value):
    proc = world.run(FLEET_GUIDANCE_SKIP=value)
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [API_URL, RAW_URL]


@posix_only
def test_a_payload_override_means_another_delivery_owns_the_session(world, tmp_path):
    override = tmp_path / "override.md"
    override.write_bytes(payload("OVERRIDE CANARY"))
    proc = world.run(FLEET_GUIDANCE_PAYLOAD=str(override))
    assert proc.stdout == "" and proc.stderr == ""
    assert world.curl_calls() == []
    assert not world.claude_md.exists()


# =================================================================================
# Ordering with other deliveries
# =================================================================================


@posix_only
def test_a_block_delivered_later_than_the_fetched_commit_is_kept(world):
    world.preinstall("NEWER CANARY", 1800000000)
    before = world.claude_md.read_bytes()
    proc = world.run()
    assert "kept newer" in proc.stdout, proc.stdout
    assert world.claude_md.read_bytes() == before
    assert "FETCHED CANARY" not in world.claude_md.read_text(encoding="utf-8")


@posix_only
def test_a_block_delivered_earlier_than_the_fetched_commit_is_replaced(world):
    world.preinstall("OLDER CANARY", 1600000000)
    proc = world.run()
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    text = world.claude_md.read_text(encoding="utf-8")
    assert "FETCHED CANARY" in text and "OLDER CANARY" not in text
    assert f"fleet-guidance-delivered: {EPOCH}" in text


# =================================================================================
# Shape: the hook file, the manifests, the bundled copies
# =================================================================================


CLAUDE_CODE_JSON = HOOKS / "claude-code.json"


def test_claude_code_json_wires_one_session_start_hook():
    config = json.loads(CLAUDE_CODE_JSON.read_text(encoding="utf-8"))
    assert set(config) == {"hooks"}
    assert set(config["hooks"]) == {"SessionStart"}
    groups = config["hooks"]["SessionStart"]
    assert len(groups) == 1
    group = groups[0]
    assert group["matcher"] == "startup|resume|fork"
    assert len(group["hooks"]) == 1
    handler = group["hooks"][0]
    assert handler["type"] == "command"
    assert handler["timeout"] == 30
    # Claude-only by design (ADR 0017), so no Codex fields and no Claude-only ones.
    assert not {"if", "args", "shell", "commandWindows"} & set(handler), handler
    words = shlex.split(handler["command"])
    assert words[:2] == ["/usr/bin/env", "bash"], handler["command"]
    assert words[2:] == ["${CLAUDE_PLUGIN_ROOT}/hooks/fleet-guidance/on-session-start.sh"]


def test_the_manifests_name_the_claude_only_file_and_codex_never_sees_it():
    claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert claude["hooks"] == "./hooks/claude-code.json"
    agent_plugin = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in agent_plugin
    # Both harnesses load hooks/hooks.json by default; this plugin must not have one.
    assert not (HOOKS / "hooks.json").exists()
    assert claude["version"] == agent_plugin["version"]


def test_the_three_hook_files_exist():
    for path in (ON_SESSION_START, FLEET_MEMORY, BUNDLED_PAYLOAD):
        assert path.is_file(), path
    if os.name != "nt":
        for script in (ON_SESSION_START, FLEET_MEMORY):
            assert os.access(script, os.X_OK), script


def test_the_bundled_payload_starts_with_the_header_the_hook_requires():
    assert BUNDLED_PAYLOAD.read_bytes().startswith(HEADER)
    assert 0 < BUNDLED_PAYLOAD.stat().st_size <= MAX_BYTES


def test_the_hook_does_not_stop_on_the_first_error():
    # The hook must always exit 0, so a failing command may not end it early.
    text = ON_SESSION_START.read_text(encoding="utf-8")
    assert re.search(r"^set -uo pipefail$", text, re.MULTILINE)
    assert not re.search(r"^set -[a-z]*e", text, re.MULTILINE)


def test_the_bare_bash_rule_accepts_the_env_bash_command():
    errors = []
    check_consistency.check_hook_commands({"name": "adam-anything-anywhere"}, errors)
    assert errors == []
    config = json.loads(CLAUDE_CODE_JSON.read_text(encoding="utf-8"))
    assert check_consistency._bare_bash_hooks(config) == 0
    # Negative control: the same command with a bare `bash` first is reported.
    bare = json.loads(CLAUDE_CODE_JSON.read_text(encoding="utf-8").replace("/usr/bin/env bash", "bash"))
    assert check_consistency._bare_bash_hooks(bare) == 1
