#!/usr/bin/env python3
"""Tests for adam-anything-anywhere's fleet-guidance SessionStart hook (ADR 0023).

Covers plugins/adam-anything-anywhere/hooks/: on-session-start.sh (the plugin's
own hook, run in place), the bundled fleet-memory.sh and fleet-guidance.md it
installs through, and the shape of hooks/claude-code.json and the manifests.

Hermetic and deterministic:
  - the raw download never touches the network: PATH starts with a tmp dir
    holding a FAKE curl (a small bash script the fixture writes). It records its
    argv, then serves a canned base.md and exits with a canned status; any
    other URL exits 6;
  - the commit lookup runs REAL git against a local bare repository standing in
    for GitHub: a tmp global git config (GIT_CONFIG_GLOBAL, with
    GIT_CONFIG_NOSYSTEM=1) maps https://github.com/Adam-S-Daniel/_agent-guidance
    onto it with url.<file-url>.insteadOf. Its refs/heads/main is one commit made
    with fixed author and committer dates (@1700000000 +0000);
  - HOME, CLAUDE_CONFIG_DIR, XDG_STATE_HOME, XDG_CACHE_HOME, TMPDIR and
    CLAUDE_PROJECT_DIR (an empty dir) are tmp dirs, CODEX_HOME names a path that
    does not exist, and every FLEET_* variable is unset unless a case sets it.
    The session's environment is built from scratch, so nothing leaks in. Every
    hook run starts in CLAUDE_PROJECT_DIR, as Claude Code starts a hook, so a
    file the hook read from $PWD would be the planted one;
  - GIT_ALLOW_PROTOCOL=file lets the hook's git reach the local bare repo even
    though the hook allows only https. A case that needs another protocol sets
    it, or unsets it to test the hook's own protocol settings;
  - a recording `sleep` first on PATH logs the pid of every sleep the hook
    starts, and every run checks that none is left alive;
  - the commit and the base.md bytes are invented. No sleeps and no wall clock.
    The timeouts are deadline guards on a subprocess, and the stalled-fetch
    cases poll a log file only up to a monotonic deadline.

The shell-driven tests are POSIX-only; the shape tests also run on Windows.

Run: python3 -m pytest scripts/test_fleet_guidance_hook.py -q
"""

import hashlib
import http.server
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
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
    os.name == "nt" or BASH is None or shutil.which("ps") is None,
    reason="drives the hook scripts with a POSIX bash, a fake curl, real git and ps")

# The bytes every base.md starts with, and so every payload the hook accepts.
HEADER = ("# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**").encode()
BODY_TAIL = (b"\n> Edit only below the `## Repo-specific additions` header.\n"
             b"> Everything above it will be overwritten on the next sync.\n\n")

MAX_BYTES = 1048576
EPOCH = 1700000000  # the committer time of the invented main commit
REMOTE_URL = "https://github.com/Adam-S-Daniel/_agent-guidance"
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
  https://raw.githubusercontent.com/*) body="$FAKE_RAW_BODY"; code="${FAKE_RAW_EXIT:-0}" ;;
  *) exit 6 ;;
esac
if [ -n "$out" ]; then cp -- "$body" "$out"; fi
exit "$code"
"""

# A git that records its argv and runs the real one. With CRLF, the one call that
# prints the commit line (`log ... FETCH_HEAD`) ends it with \r\n, the way a
# Windows git can.
GIT_WRAPPER = r"""#!/usr/bin/env bash
printf '%s\t' "$@" >> LOG_FILE
printf '\n' >> LOG_FILE
if [ "CRLF_FLAG" = 1 ]; then
  case " $* " in
    *" log "*" FETCH_HEAD "*)
      REAL_GIT "$@" | awk '{ printf "%s\r\n", $0 }'
      exit "${PIPESTATUS[0]}"
      ;;
  esac
fi
exec REAL_GIT "$@"
"""

# A sleep that records its pid and arguments and becomes the real one (exec keeps
# the pid). The hook starts one for its fetch watchdog; every run checks that none
# is left.
FAKE_SLEEP = r"""#!/usr/bin/env bash
printf '%s %s\n' "$$" "$*" >> "$FAKE_SLEEP_LOG"
exec REAL_SLEEP "$@"
"""

# A git that stalls: the real one for every subcommand but `fetch`, where it logs
# its pid and hangs for 30 s, the way a fetch to a blackholed proxy does.
# HANG_COMMAND is `exec sleep 30` (git itself hangs), or `sleep 30 & wait` (git
# hands the transfer to a child, as it does to git remote-https, and hangs
# waiting for it).
STALLED_GIT = r"""#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = fetch ]; then
    printf '%s\n' "$$" >> STALL_LOG
    HANG_COMMAND
  fi
done
exec REAL_GIT "$@"
"""
STALLS = {"git-hangs": "exec sleep 30", "child-hangs": "sleep 30 & wait"}

# A git whose fetch waits until the hook's watchdog has started its sleep (the
# sleep log has a line), so a test can read the watchdog's limit without racing a
# fetch that ends first. Busy-waits, bounded by SECONDS.
HELD_GIT = r"""#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = fetch ]; then
    SECONDS=0
    while [ ! -s "$FAKE_SLEEP_LOG" ] && [ "$SECONDS" -lt 10 ]; do :; done
    break
  fi
done
exec REAL_GIT "$@"
"""

# An ssh that records that it ran and fails.
FAKE_SSH = r"""#!/usr/bin/env bash
printf '%s\t' "$@" >> "$FAKE_SSH_LOG"
printf '\n' >> "$FAKE_SSH_LOG"
exit 1
"""

# An askpass program that records that it was asked and answers.
ASKPASS_LOGGER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> ASKPASS_LOG
printf 'nobody\n'
"""

# A gpg that prints a report on stderr, the way gpg does for a signed commit.
FAKE_GPG = r"""#!/usr/bin/env bash
echo "gpg: Signature made, using an invented key" >&2
echo 'gpg: Good signature from "Example Person"' >&2
echo "[GNUPG:] GOODSIG 0000000000000000 Example Person"
exit 0
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


def process_args(pid: int):
    """The command line of a live process, or None when it is gone or a zombie."""
    proc = subprocess.run(["ps", "-o", "stat=,args=", "-p", str(pid)],
                          capture_output=True, text=True)
    line = proc.stdout.strip()
    if proc.returncode != 0 or not line:
        return None
    state, _, args = line.partition(" ")
    return None if state.startswith("Z") else args.strip()


def live_sleeps(pids):
    """(pid, args) of every pid in `pids` that is a live `sleep`. A pid of 1 or
    less is never looked at, let alone signalled."""
    found = []
    for pid in pids:
        if not isinstance(pid, int) or pid <= 1:
            continue
        args = process_args(pid)
        if args and os.path.basename(args.split()[0]) == "sleep":
            found.append((pid, args))
    return found


def snapshot(directory: Path) -> dict:
    """Every path under `directory` with its bytes, to prove nothing was written."""
    return {str(p.relative_to(directory)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(directory.rglob("*"))}


class World:
    """One tmp HOME, one fake curl, a local bare repo as GitHub, and the canned
    answers they serve."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.claude_dir = tmp_path / "claude"
        self.tmpdir = tmp_path / "tmpdir"
        self.bin = tmp_path / "bin"
        self.project = tmp_path / "project"  # CLAUDE_PROJECT_DIR: empty
        # Claude Code starts a hook with the project directory as its cwd.
        self.cwd = self.project
        for d in (self.home, self.tmpdir, self.bin, self.project):
            d.mkdir()
        self.log = tmp_path / "curl.log"
        self.log.write_text("", encoding="utf-8")
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(0o755)
        self.sleep_log = tmp_path / "sleep.log"
        real_sleep = shutil.which("sleep", path=clean_path())
        assert real_sleep is not None
        self.install(self.bin / "sleep",
                     FAKE_SLEEP.replace("REAL_SLEEP", shlex.quote(real_sleep)))
        self.raw_body = tmp_path / "raw.md"
        self.fetched = payload("FETCHED CANARY")
        self.set_raw(self.fetched)
        self.path = f"{self.bin}{os.pathsep}{clean_path()}"
        self.git_log = tmp_path / "git.log"
        # The bare repo that stands in for GitHub, and the config that maps to it.
        self.remote = tmp_path / "remote.git"
        self.gitconfig = tmp_path / "gitconfig"
        self.git("init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git("--git-dir", str(self.remote), "config", "uploadpack.allowFilter", "true")
        self.sha = self.commit(EPOCH)
        self.map_remote(self.remote)

    # --- the fake GitHub ---------------------------------------------------------

    def git(self, *args, when=None, text_input=None) -> str:
        env = {
            "PATH": clean_path(), "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Example Person",
            "GIT_AUTHOR_EMAIL": "person@example.com", "GIT_COMMITTER_NAME": "Example Person",
            "GIT_COMMITTER_EMAIL": "person@example.com",
        }
        if when is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        proc = subprocess.run(["git", *args], env=env, input=text_input,
                              capture_output=True, text=True)
        assert proc.returncode == 0, (args, proc.stderr)
        return proc.stdout.strip()

    def remote_git(self, *args, **kwargs) -> str:
        return self.git("--git-dir", str(self.remote), *args, **kwargs)

    def commit(self, epoch: int, branch: str = "main", parent: str = None) -> str:
        """Make one commit with committer time `epoch` the tip of `branch`."""
        when = f"@{epoch} +0000"
        tree = self.remote_git("mktree", text_input="")
        parents = ["-p", parent] if parent else []
        sha = self.remote_git("commit-tree", tree, *parents, "-m", "carry the guidance",
                              when=when)
        self.remote_git("update-ref", f"refs/heads/{branch}", sha)
        assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
        return sha

    def map_remote(self, target: Path):
        """Point https://github.com/Adam-S-Daniel/_agent-guidance at `target`."""
        self.map_url(target.as_uri())

    def map_url(self, base: str):
        """Point the remote at `base`, with url.<base>.insteadOf, replacing any
        config set so far."""
        self.gitconfig.unlink(missing_ok=True)
        self.add_config(f"url.{base}.insteadOf", REMOTE_URL)

    def add_config(self, key: str, value: str):
        """Add one setting to the user's global git config."""
        self.git("config", "--file", str(self.gitconfig), key, value)

    def signed_commit(self, epoch: int) -> str:
        """Like commit(), but the commit object carries a gpgsig header."""
        tree = self.remote_git("mktree", text_input="")
        when = f"{epoch} +0000"
        who = f"Example Person <person@example.com> {when}"
        raw = (f"tree {tree}\nauthor {who}\ncommitter {who}\n"
               "gpgsig -----BEGIN PGP SIGNATURE-----\n \n AAAAexample\n"
               " -----END PGP SIGNATURE-----\n\ncarry the guidance\n")
        sha = self.remote_git("hash-object", "-t", "commit", "-w", "--stdin", text_input=raw)
        self.remote_git("update-ref", "refs/heads/main", sha)
        return sha

    def install(self, path: Path, text: str):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    @property
    def raw_url(self) -> str:
        return (f"https://raw.githubusercontent.com/Adam-S-Daniel/_agent-guidance/"
                f"{self.sha}/agents-md/base.md")

    def wrap_git(self, crlf: bool = False):
        """Put a recording git first on PATH; it runs the real one."""
        real = shutil.which("git", path=clean_path())
        assert real is not None
        script = (GIT_WRAPPER.replace("LOG_FILE", shlex.quote(str(self.git_log)))
                  .replace("CRLF_FLAG", "1" if crlf else "0")
                  .replace("REAL_GIT", shlex.quote(real)))
        self.install(self.bin / "git", script)

    def hold_git(self):
        """Put a git first on PATH whose fetch waits for the watchdog to start."""
        real = shutil.which("git", path=clean_path())
        assert real is not None
        self.install(self.bin / "git", HELD_GIT.replace("REAL_GIT", shlex.quote(real)))

    def stall_git(self, how: str = "git-hangs"):
        """Put a git first on PATH whose fetch hangs for 30 s, in the way STALLS
        names. Returns the file that gets the pid of each stalled fetch."""
        real = shutil.which("git", path=clean_path())
        assert real is not None
        stalls = self.tmp / "stall.log"
        self.install(self.bin / "git", STALLED_GIT.replace("STALL_LOG", shlex.quote(str(stalls)))
                     .replace("HANG_COMMAND", STALLS[how]).replace("REAL_GIT", shlex.quote(real)))
        return stalls

    def git_calls(self):
        text = self.git_log.read_text(encoding="utf-8") if self.git_log.exists() else ""
        return [line.rstrip("\t").split("\t") for line in text.splitlines()]

    # --- the session --------------------------------------------------------------

    @property
    def claude_md(self) -> Path:
        return self.claude_dir / "CLAUDE.md"

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
            "GIT_CONFIG_GLOBAL": str(self.gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_CURL_LOG": str(self.log),
            "FAKE_RAW_BODY": str(self.raw_body),
            "FAKE_SLEEP_LOG": str(self.sleep_log),
            # The hook allows only https. The local bare repo that stands in for
            # GitHub is a file URL, so the fixture lets that through.
            "GIT_ALLOW_PROTOCOL": "file",
        }
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def sleeps(self):
        """(pid, [argv]) of every sleep the hook started."""
        text = self.sleep_log.read_text(encoding="utf-8") if self.sleep_log.exists() else ""
        return [(int(line.split()[0]), line.split()[1:]) for line in text.splitlines()
                if line.split() and line.split()[0].isdigit()]

    def sleeper_pids(self):
        return [pid for pid, _argv in self.sleeps()]

    def reap_sleeps(self):
        """Kill every sleep the hook started that is still alive, and return them."""
        survivors = live_sleeps(self.sleeper_pids())
        for pid, _args in survivors:
            os.kill(pid, signal.SIGKILL)
        return survivors

    def run(self, *, timeout=120, **extra):
        try:
            proc = subprocess.run([BASH, str(ON_SESSION_START)], env=self.env(**extra),
                                  cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                                  capture_output=True, encoding="utf-8", timeout=timeout)
        finally:
            survivors = self.reap_sleeps()
        # A hook that drives a spawn must not leave it behind.
        assert survivors == [], survivors
        assert proc.returncode == 0, proc
        # curl reads ~/.curlrc unless -q is its FIRST argument: a curlrc could
        # print into the session or turn redirects back on.
        for argv in self.curl_calls():
            assert argv[0] == "-q", argv
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

    # Exactly one request: the raw file at the sha git reported. No API call.
    assert world.urls() == [world.raw_url]
    for argv in world.curl_calls():
        assert argv[0] == "-q", argv
        assert "--proto" in argv and argv[argv.index("--proto") + 1] == "=https", argv
        assert "--proto-redir" in argv and argv[argv.index("--proto-redir") + 1] == "=https", argv
        assert "--max-time" in argv and "--connect-timeout" in argv, argv
        assert "--max-filesize" in argv, argv
        assert "-L" not in argv and "--location" not in argv, argv
        joined = " ".join(argv).lower()
        for forbidden in ("authorization", "bearer", "ghp_example_not_real", "--netrc", "-u "):
            assert forbidden not in joined, argv
        # The scratch files live under TMPDIR.
        target = argv[argv.index("-o") + 1]
        assert target.startswith(str(world.tmpdir) + os.sep + "fleet-guidance."), target
    assert_temp_dir_gone(world)


@posix_only
def test_the_commit_lookup_is_a_commit_only_git_fetch_into_a_private_bare_repo(world):
    world.wrap_git()
    proc = world.run()
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    # fleet-memory.sh also runs git, always with -C. The hook never does.
    calls = [argv for argv in world.git_calls() if "-C" not in argv]
    scratch = f"{world.tmpdir}{os.sep}fleet-guidance."
    init = [argv for argv in calls if "init" in argv]
    fetch = [argv for argv in calls if "fetch" in argv]
    log = [argv for argv in calls if "log" in argv]
    assert len(init) == len(fetch) == len(log) == 1, calls
    assert len(calls) == 3, calls

    assert init[0][:4] == ["init", "-q", "--bare", "--template="], init[0]
    bare = init[0][4]
    assert bare.startswith(scratch), bare

    # --git-dir, never -C, so an inherited GIT_DIR cannot redirect either call.
    for argv in (fetch[0], log[0]):
        assert argv[0] == f"--git-dir={bare}", argv
    argv = fetch[0]
    settings = [argv[i + 1] for i, word in enumerate(argv) if word == "-c"]
    assert sorted(settings) == ["core.askPass=", "core.hooksPath=/dev/null",
                                "credential.helper=", "http.lowSpeedLimit=1000",
                                "http.lowSpeedTime=5", "protocol.allow=never",
                                "protocol.https.allow=always"], argv
    subcommand = argv.index("fetch")
    assert argv[subcommand + 1:] == ["-q", "--no-tags", "--depth=1", "--filter=tree:0",
                                     REMOTE_URL, "refs/heads/main"], argv
    assert log[0][1:] == ["log", "-1", "--no-show-signature", "--format=%H %ct",
                          "FETCH_HEAD"], log[0]

    # Nothing was left behind or written where the session started: its cwd is the
    # project directory.
    assert list(world.project.iterdir()) == []
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
    assert world.urls() == [world.raw_url]


@posix_only
def test_an_empty_payload_override_is_not_an_override(world):
    proc = world.run(FLEET_GUIDANCE_PAYLOAD="")
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [world.raw_url]


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
def test_a_carriage_return_after_the_git_log_is_tolerated(world):
    # A Windows git can end the commit line with \r\n, and `$(...)` would keep a
    # \r on the epoch. This git wraps the real one and ends that line so.
    world.wrap_git(crlf=True)
    proc = world.run()
    assert any("log" in argv and "FETCH_HEAD" in argv for argv in world.git_calls())
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert f"fleet-guidance-delivered: {EPOCH}" in world.claude_md.read_text(encoding="utf-8")
    assert world.urls() == [world.raw_url]


@posix_only
def test_an_inherited_git_dir_cannot_redirect_the_lookup(world):
    decoy = world.tmp / "decoy.git"
    world.git("init", "-q", "--bare", str(decoy))
    before = snapshot(decoy)
    proc = world.run(GIT_DIR=str(decoy))
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [world.raw_url]
    assert snapshot(decoy) == before


# Variables that name a repository, with the kind of value each takes. These are
# the ones that change the fetch's result when inherited. The hook also unsets
# GIT_INDEX_FILE, GIT_ALTERNATE_OBJECT_DIRECTORIES and GIT_GRAFT_FILE, which made
# no difference to this fetch when measured, so no case here could fail on them.
REPO_LOCATION_VARS = [
    ("GIT_OBJECT_DIRECTORY", "dir"),
    ("GIT_WORK_TREE", "dir"),
    ("GIT_COMMON_DIR", "dir"),
    ("GIT_NAMESPACE", "name"),
    ("GIT_SHALLOW_FILE", "file"),
]


@posix_only
@pytest.mark.parametrize("name,kind", REPO_LOCATION_VARS, ids=[n for n, _k in REPO_LOCATION_VARS])
def test_an_inherited_repo_location_variable_cannot_redirect_the_fetch(world, name, kind):
    # An inherited GIT_OBJECT_DIRECTORY sends the fetch's pack into another repo;
    # GIT_WORK_TREE, GIT_COMMON_DIR and GIT_SHALLOW_FILE make the fetch fail.
    scratch = world.tmp / "scratch"
    scratch.mkdir()
    # Like the real main, the tip has a parent, so a depth-1 fetch writes a shallow file.
    world.sha = world.commit(EPOCH, parent=world.commit(EPOCH - 100, branch="parent"))
    value = {"dir": str(scratch), "file": str(scratch / "file"), "name": "decoy"}[kind]
    proc = world.run(**{name: value})
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert f"fleet-guidance-delivered: {EPOCH}" in world.claude_md.read_text(encoding="utf-8")
    assert world.urls() == [world.raw_url]
    assert list(scratch.iterdir()) == []


@posix_only
def test_settings_passed_through_git_config_variables_still_reach_the_fetch(world):
    # A cloud session can carry its git settings in GIT_CONFIG_COUNT/KEY/VALUE, so
    # the hook must not clear them. Here the only mapping to the local remote is one.
    proc = world.run(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="1",
                     GIT_CONFIG_KEY_0=f"url.{world.remote.as_uri()}.insteadOf",
                     GIT_CONFIG_VALUE_0=REMOTE_URL)
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert "fleet-guidance-source:" not in proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)


@posix_only
def test_a_users_log_showsignature_does_not_break_the_commit_line(world):
    # With log.showSignature=true, git prints gpg's report ahead of a signed
    # commit's `%H %ct` line. The remote's main is a signed commit here, and the
    # user's config names a gpg that prints such a report.
    gpg = world.bin / "fake-gpg"
    world.install(gpg, FAKE_GPG)
    world.sha = world.signed_commit(EPOCH)
    world.add_config("log.showSignature", "true")
    world.add_config("gpg.program", str(gpg))
    # Negative control: the same config does put the report into a plain log.
    shown = world.remote_git("-c", "log.showSignature=true", "-c", f"gpg.program={gpg}",
                             "log", "-1", "--format=%H %ct", "main")
    assert shown.startswith("gpg: Signature made"), shown
    proc = world.run()
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    assert f"fleet-guidance-delivered: {EPOCH}" in world.claude_md.read_text(encoding="utf-8")
    assert world.urls() == [world.raw_url]


class Unauthorized(http.server.BaseHTTPRequestHandler):
    """Answers every request with HTTP 401 and a Basic challenge, and records it."""

    def do_GET(self):
        self.server.requests.append(self.path)
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="example"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture
def unauthorized_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@posix_only
def test_no_askpass_program_is_called_when_the_remote_asks_for_a_login(world, unauthorized_server):
    # VS Code terminals and Linux desktops export GIT_ASKPASS and SSH_ASKPASS, and
    # a user may set core.askPass. GIT_TERMINAL_PROMPT=0 stops none of them, so a
    # 401 (a repository gone private, say) would raise a dialog. The remote here
    # answers 401; GIT_ALLOW_PROTOCOL lets the plain-http request through, as a
    # user who exports it may.
    asked = world.tmp / "askpass.log"
    logger = world.bin / "askpass-logger"
    world.install(logger, ASKPASS_LOGGER.replace("ASKPASS_LOG", shlex.quote(str(asked))))
    world.map_url(f"http://127.0.0.1:{unauthorized_server.server_address[1]}/")
    world.add_config("core.askPass", str(logger))
    proc = world.run(GIT_ALLOW_PROTOCOL="file:http", GIT_ASKPASS=str(logger),
                     SSH_ASKPASS=str(logger))
    assert unauthorized_server.requests, "git never reached the 401 server"
    assert not asked.exists(), asked.read_text(encoding="utf-8")
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []


@posix_only
@pytest.mark.parametrize("base", ["ssh://git@example.invalid/", "git@example.invalid:"],
                         ids=["ssh-url", "scp-like"])
def test_an_ssh_rewrite_in_the_users_git_config_is_not_followed(world, base):
    # url.<ssh base>.insteadOf = https://github.com/... would send the fetch over
    # ssh with the user's key. Only https is allowed, so the fetch fails and the
    # hook falls back. GIT_ALLOW_PROTOCOL is unset here so ssh is not ruled out by it.
    ran = world.tmp / "ssh.log"
    world.install(world.bin / "ssh", FAKE_SSH)
    world.map_url(base)
    proc = world.run(GIT_ALLOW_PROTOCOL=None, FAKE_SSH_LOG=str(ran))
    assert not ran.exists(), ran.read_text(encoding="utf-8")
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []


# =================================================================================
# The fetch is bounded by a watchdog
# =================================================================================


@posix_only
@pytest.mark.parametrize("value,limit", [
    ("1", 1), ("37", 37), ("60", 60), ("05", 5),
    ("", 15), ("0", 15), ("00", 15), ("61", 15), ("100", 15), ("abc", 15),
    ("5s", 15), ("-3", 15), (" 5", 15), ("1.5", 15),
])
def test_the_fetch_limit_is_the_setting_when_it_is_an_integer_from_1_to_60(world, value, limit):
    world.hold_git()
    proc = world.run(FLEET_GUIDANCE_FETCH_TIMEOUT=value)
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert [argv for _pid, argv in world.sleeps()] == [[str(limit)]]


needs_pgrep = pytest.mark.skipif(
    shutil.which("pgrep") is None, reason="the hook stops a hung child of git through pgrep")
HANG_KINDS = [pytest.param("git-hangs", id="git-hangs"),
              pytest.param("child-hangs", id="child-hangs", marks=needs_pgrep)]


@posix_only
@pytest.mark.parametrize("how", HANG_KINDS)
def test_a_stalled_fetch_is_stopped_at_the_limit_and_the_bundled_copy_is_installed(world, how):
    # The fetch hangs for 30 s, itself or in a child it waits for. With the limit
    # at 1 s the watchdog ends it, child included, and the subprocess timeout is
    # only the guard: nothing here sleeps.
    stalls = world.stall_git(how)
    proc = world.run(FLEET_GUIDANCE_FETCH_TIMEOUT="1", timeout=10)
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []
    assert proc.stderr == ""
    assert list(world.tmpdir.iterdir()) == []
    # The stall really happened, and what it started is gone (run() also checks
    # every sleep the hook started).
    stalled = [int(line) for line in stalls.read_text(encoding="utf-8").split()]
    assert len(stalled) == 1
    assert ["30"] in [argv for _pid, argv in world.sleeps()]
    assert live_sleeps(stalled) == []


@posix_only
@pytest.mark.parametrize("how", HANG_KINDS)
def test_a_sigterm_while_the_fetch_is_stalled_ends_the_hook_and_cleans_up(world, how):
    # The default limit is 15 s. A TERM at any point in the fetch must end the hook
    # at once and take the fetch (child included), the watchdog and the temp dir
    # with it. The deadlines are subprocess and monotonic-clock guards.
    stalls = world.stall_git(how)
    hook = subprocess.Popen([BASH, str(ON_SESSION_START)], env=world.env(),
                            cwd=str(world.cwd), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    try:
        deadline = time.monotonic() + 60
        while not (stalls.exists() and stalls.read_text(encoding="utf-8").split()):
            assert time.monotonic() < deadline, "the fetch never started"
            assert hook.poll() is None, "the hook ended before the fetch started"
            try:
                hook.wait(timeout=0.02)
            except subprocess.TimeoutExpired:
                pass
        hook.send_signal(signal.SIGTERM)
        out, err = hook.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        hook.kill()
        hook.communicate()
        pytest.fail("the hook did not end within 10 s of a SIGTERM")
    finally:
        survivors = world.reap_sleeps()
        if hook.poll() is None:
            hook.kill()
            hook.communicate()
    assert hook.returncode == 0, (out, err)
    assert out == "" and err == "", (out, err)
    assert survivors == [], survivors
    assert list(world.tmpdir.iterdir()) == []
    stalled = [int(line) for line in stalls.read_text(encoding="utf-8").split()]
    assert len(stalled) == 1 and live_sleeps(stalled) == []


# =================================================================================
# The project directory is never read
# =================================================================================


def plant(world: World, kind: str):
    hooks = world.project / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    target = hooks / "fleet-guidance.md"
    if kind == "file":
        target.write_bytes(payload("PLANTED CANARY"))
    else:
        assert kind == "symlink"
        secret = world.tmp / "secret.md"
        secret.write_bytes(payload("SECRET CANARY"))
        target.symlink_to(secret)


@posix_only
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_copy_planted_in_the_project_dir_is_ignored(world, kind):
    # A repo the user opens can carry any data file. It must never reach the
    # GLOBAL ~/.claude/CLAUDE.md through this hook.
    plant(world, kind)
    proc = world.run(CLAUDE_PROJECT_DIR=str(world.project))
    lines = proc.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    text = world.claude_md.read_text(encoding="utf-8")
    assert "FETCHED CANARY" in text
    assert "PLANTED CANARY" not in text and "SECRET CANARY" not in text
    assert f"fleet-guidance-delivered: {EPOCH}" in text
    assert world.urls() == [world.raw_url]  # the fetch path ran


@posix_only
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_planted_copy_is_ignored_when_fetching_is_off(world, kind):
    plant(world, kind)
    proc = world.run(CLAUDE_PROJECT_DIR=str(world.project), FLEET_GUIDANCE_FETCH="off")
    assert_bundled_fallback(world, proc, "fetching is off (FLEET_GUIDANCE_FETCH)")
    text = world.claude_md.read_text(encoding="utf-8")
    assert "PLANTED CANARY" not in text and "SECRET CANARY" not in text
    assert world.curl_calls() == []


def test_the_hooks_code_names_neither_the_project_dir_nor_the_rest_api():
    # Comments may explain why; no command may reach either.
    code = "\n".join(line for line in ON_SESSION_START.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    assert "CLAUDE_PROJECT_DIR" not in code
    assert "api.github.com" not in code
    assert "python" not in code.lower()


# =================================================================================
# The fetch fails: the bundled copy is installed and the source is named
# =================================================================================


@posix_only
def test_an_unreachable_remote_falls_back_before_any_download(world):
    world.map_remote(world.tmp / "no-such-remote.git")
    proc = world.run()
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []


@posix_only
def test_a_remote_without_a_main_branch_falls_back(world):
    world.remote_git("update-ref", "-d", "refs/heads/main")
    world.commit(EPOCH, branch="other")
    proc = world.run()
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []


@posix_only
@pytest.mark.parametrize("epoch", [
    pytest.param(0, id="epoch-zero"),
    pytest.param(99999999999, id="epoch-over-ten-digits"),
])
def test_a_commit_time_outside_the_accepted_range_falls_back(world, epoch):
    world.sha = world.commit(epoch)
    proc = world.run()
    assert_bundled_fallback(world, proc, "the commit lookup failed")
    assert world.curl_calls() == []


@posix_only
def test_a_commit_date_far_in_the_future_is_not_installed_as_the_stamp(world):
    # 4070908800 is 2099-01-01. It is ten digits, so the hook passes it on, and
    # the bundled fleet-memory.sh ignores a stamp later than a day from now (an
    # installed stamp never decreases, so it would lock every later delivery
    # out). The block is still installed.
    world.sha = world.commit(4070908800)
    proc = world.run()
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert verdict_version(proc.stdout) == sha8(world.fetched)
    text = world.claude_md.read_text(encoding="utf-8")
    assert "FETCHED CANARY" in text
    assert "fleet-guidance-delivered: 4070908800" not in text
    assert world.urls() == [world.raw_url]


@posix_only
def test_the_download_is_pinned_to_the_commit_that_was_fetched(world):
    world.sha = world.commit(EPOCH + 5)
    proc = world.run()
    assert proc.stdout.startswith("fleet-guidance: installed"), proc.stdout
    assert world.urls() == [world.raw_url]
    assert world.sha in world.urls()[0]
    assert f"fleet-guidance-delivered: {EPOCH + 5}" in world.claude_md.read_text(encoding="utf-8")


@posix_only
def test_a_failed_download_falls_back_to_the_bundled_copy(world):
    proc = world.run(FAKE_RAW_EXIT="22")
    assert_bundled_fallback(world, proc, "the base.md download failed")
    assert world.urls() == [world.raw_url]


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
    assert world.urls() == [world.raw_url]


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
def test_a_missing_git_falls_back(world, tmp_path):
    farm = tool_farm(tmp_path / "farm", lambda name: name in ("git", "curl"))
    (farm / "curl").symlink_to(world.bin / "curl")
    world.path = str(farm)
    assert shutil.which("git", path=world.path) is None
    proc = world.run()
    lines = proc.stdout.splitlines()
    # The bundled installer may not run without git, so only the source line is certain.
    assert lines[-1] == SOURCE_LINE.format(reason="git is not installed"), proc.stdout
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
    assert world.urls() == [world.raw_url]


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
    world.preinstall("NEWER CANARY", 1750000000)
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
