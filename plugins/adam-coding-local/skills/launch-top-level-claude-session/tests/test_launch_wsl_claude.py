"""Regression tests for the wt.exe ';' escaping bug in launch-wsl-claude.sh
and launch-wsl-claude.ps1.

wt.exe re-parses its OWN command line and treats an unescaped ';' as a
subcommand separator (new-tab) EVEN when it arrives inside an argument that
was already a single, correctly quoted argv element — quoting alone does not
protect it. Windows Terminal's documented escape is a literal backslash
before the semicolon (`\\;`). Without it, a prompt like
"Work issue #5; it has evidence" opened the real tab with the prompt
truncated at the ';', plus a stray tab trying to run the remainder as a
command (error 0x80070002).

Both scripts are run for real — never reimplemented in Python — against
hermetic stubs, so a regression in the actual escaping/quoting logic fails
these tests:

- launch-wsl-claude.sh: real `bash`, with stub `claude` and `wt.exe`
  executables placed on PATH and `LAUNCH_WSL_CLAUDE_DRY_RUN=1`. The real
  launch backgrounds wt.exe with `&` and `disown`s it (so remote-controlled
  sessions survive the launching shell exiting), which makes the resulting
  process nondeterministic to wait on from a test — `disown` also detaches
  it from this shell's job table, so even `wait "$pid"` no longer blocks on
  it (confirmed by hand: a disowned 1s sleep job returns from `wait`
  immediately). The dry-run mode sidesteps all of that: it prints the
  final, already-escaped wt.exe argv, one per line, and exits before ever
  launching anything.
- launch-wsl-claude.ps1: real `pwsh`, with `wsl.exe`/`wt.exe` PowerShell
  *functions* defined ahead of it in the same session. Functions take
  precedence over external commands of the same bare name (verified by
  hand: a `function wsl.exe { ... }` shadowed the real
  C:\\Windows\\System32\\wsl.exe on a machine that has it installed), so
  this needs no real WSL distro or Windows Terminal — and the script's
  `-PrintArgs` switch prints the final command line instead of calling
  Start-Process.

Both scripts always run against their own real source — never a
reimplementation of the escaping — so a regression in either script fails
here.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
SH_SCRIPT = Path(
    os.environ.get("LAUNCH_WSL_CLAUDE_SH")
    or SKILL_DIR / "scripts" / "launch-wsl-claude.sh"
)
PS1_SCRIPT = Path(
    os.environ.get("LAUNCH_WSL_CLAUDE_PS1")
    or SKILL_DIR / "scripts" / "launch-wsl-claude.ps1"
)

BASH = shutil.which("bash")
PWSH = os.environ.get("LAUNCH_WSL_CLAUDE_PWSH") or shutil.which("pwsh")

# Decode subprocess output explicitly and never die on a stray byte:
# `text=True` alone lets the OS/locale choose the decoder (cp1252 on
# Windows), which raises UnicodeDecodeError on a byte outside that codepage;
# utf-8 with errors=replace is deterministic across platforms.
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


# ---------------------------------------------------------------------------
# launch-wsl-claude.sh
# ---------------------------------------------------------------------------

# A stub claude that reports the two session-persistence variables and its
# argv, so a test can EXECUTE the launched command and see what the new
# session's process would really get.
_ENV_REPORTING_CLAUDE_SH = (
    "#!/usr/bin/env bash\n"
    'echo "CHILD=[${CLAUDE_CODE_CHILD_SESSION-unset}]"\n'
    'echo "FORCE=[${CLAUDE_CODE_FORCE_SESSION_PERSISTENCE-unset}]"\n'
    "printf 'ARG:%s\\n' \"$@\"\n"
)


def _make_executable_stub(path: Path, body: str = "#!/usr/bin/env bash\necho stub\n") -> None:
    path.write_text(body, newline="\n")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def sh_argv(tmp_path):
    """Returns a function that runs the real .sh script and returns the
    dry-run argv it would have handed to wt.exe, one element per line.
    """
    if not BASH:
        pytest.skip("bash is not on PATH")

    bin_dir = tmp_path / "bin"
    home_dir = tmp_path / "home"
    bin_dir.mkdir()
    home_dir.mkdir()
    _make_executable_stub(bin_dir / "claude", _ENV_REPORTING_CLAUDE_SH)
    _make_executable_stub(bin_dir / "wt.exe")
    # `bash -lic` (used by the script to capture the login PATH) is an
    # interactive login shell: without these it either prints a "no
    # ~/.bash_profile" warning or (some bash builds) auto-creates one —
    # neither is hermetic.
    (home_dir / ".bash_profile").write_text("")
    (home_dir / ".bashrc").write_text("")

    env = dict(
        HOME=str(home_dir),
        PATH=f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        LAUNCH_WSL_CLAUDE_DRY_RUN="1",
    )

    def run(*args: str) -> list[str]:
        try:
            proc = subprocess.run(
                [BASH, str(SH_SCRIPT), *args],
                env=env,
                capture_output=True,
                timeout=30,
                **TEXT,
            )
        except OSError as exc:
            pytest.skip(f"could not run bash hermetically: {exc}")
        if proc.returncode != 0:
            if sys.platform.startswith("win"):
                # launch-wsl-claude.sh assumes a real Linux/WSL userland
                # (e.g. /proc/sys/kernel/random/uuid for the session-id
                # branch, which a Windows bash like Git Bash does not have).
                # Every case below supplies --prompt precisely to avoid that
                # branch; a failure here on Windows means something else
                # about this host's bash isn't hermetic enough to trust —
                # skip rather than report a false regression.
                pytest.skip(
                    "launch-wsl-claude.sh did not run hermetically on this "
                    f"Windows bash (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}"
                )
            raise AssertionError(proc.stdout + proc.stderr)
        return proc.stdout.splitlines()

    def raw(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(SH_SCRIPT), *args],
            env=env, capture_output=True, timeout=30, **TEXT,
        )

    run.raw = raw
    run.env = env
    run.bin_dir = bin_dir
    run.tmp = tmp_path
    return run


def test_sh_prompt_with_semicolon_and_spaces_is_one_escaped_arg(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "Work issue #5; it has evidence and more",
    )
    # The prompt is the last argv element handed to wt.exe, and it must
    # arrive as ONE element (dry-run prints one per line) with every ';'
    # escaped to '\;'.
    assert argv[-1] == r"Work issue #5\; it has evidence and more"


def test_sh_prompt_without_semicolon_is_unchanged(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "no semicolons in this one",
    )
    assert argv[-1] == "no semicolons in this one"


def test_sh_remote_control_name_with_semicolon_is_escaped(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "stand by",
        "--remote-control-name", "my;rc",
    )
    idx = argv.index("--remote-control")
    assert argv[idx + 1] == r"my\;rc"


def test_sh_dir_is_passed_through_as_a_single_arg(sh_argv):
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "hi")
    assert "--cd" in argv
    assert argv[argv.index("--cd") + 1] == "/home/x/repo"


# Session persistence: a claude launched from a Claude Code tool shell inherits
# CLAUDE_CODE_CHILD_SESSION=1 and is classified as nested (no --resume, no
# history, no `claude agents`). The launched process must clear it and force
# persistence itself.

_PERSIST = ["env", "-u", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1"]


def test_sh_launched_env_clears_child_marker_and_forces_persistence(sh_argv):
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "hi")
    i = argv.index("--")
    assert argv[i + 1 : i + 5] == _PERSIST
    assert argv[i + 5].startswith("PATH=")
    # claude is handed over as a resolved, absolute path, never bare.
    claude = argv[i + 6]
    assert claude.startswith("/") and claude.endswith("/claude")


def test_sh_launched_command_really_runs_without_the_child_marker(sh_argv):
    if sys.platform.startswith("win"):
        pytest.skip("executes the WSL-side argv; needs a POSIX userland")
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "stand by")
    launched = argv[argv.index("--") + 1 :]
    env = dict(sh_argv.env, CLAUDE_CODE_CHILD_SESSION="1")
    env.pop("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", None)
    proc = subprocess.run(launched, env=env, capture_output=True, timeout=30, **TEXT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert "CHILD=[unset]" in lines
    assert "FORCE=[1]" in lines
    assert lines[-1] == "ARG:stand by"


def test_sh_login_shell_banner_before_path_does_not_corrupt_claude(sh_argv):
    """The script captures the login PATH via `bash -lic 'printf %s "$PATH"'`,
    with only stderr discarded. An interactive login shell can print a banner
    to STDOUT before running that command — e.g. Ubuntu's real "sudo_root"
    lecture on any HOME lacking ~/.sudo_as_admin_successful or ~/.hushlogin,
    reproduced here hermetically via ~/.bash_profile (sourced by a login
    shell) instead of depending on that distro-specific behavior. Without
    stripping it, the banner text becomes (part of) the launched session's
    PATH, and the resolved claude path is pushed into a LATER argv element —
    exactly what broke test_sh_launched_env_clears_child_marker_and_forces_persistence
    and test_sh_launched_command_really_runs_without_the_child_marker on a
    fresh WSL user.
    """
    if sys.platform.startswith("win"):
        pytest.skip("this HOME's login-shell startup files are POSIX bash, not cmd/pwsh")
    (sh_argv.tmp / "home" / ".bash_profile").write_text(
        'echo "MOTD-LECTURE-LINE-ONE"\necho "MOTD-LECTURE-LINE-TWO"\n'
    )
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "hi")
    i = argv.index("--")
    assert argv[i + 1 : i + 5] == _PERSIST
    assert argv[i + 5].startswith("PATH=")
    assert "MOTD-LECTURE" not in argv[i + 5]
    claude = argv[i + 6]
    assert claude.startswith("/") and claude.endswith("/claude")


def test_sh_bare_remote_control_goes_after_the_prompt(sh_argv):
    argv = sh_argv("--dir", "/home/x/repo", "--remote-control", "--prompt", "stand by")
    # Before the prompt it would take the prompt as its optional name.
    assert argv[-2:] == ["stand by", "--remote-control"]


def test_sh_remote_control_with_a_name(sh_argv):
    argv = sh_argv("--dir", "/home/x/repo", "--remote-control", "rc-one", "--prompt", "hi")
    idx = argv.index("--remote-control")
    assert argv[idx + 1] == "rc-one"
    assert argv[-1] == "hi"
    assert argv.count("--remote-control") == 1


def test_sh_prompt_file_becomes_an_instruction_to_read_it(sh_argv):
    handoff = sh_argv.tmp / "handoff.md"
    handoff.write_text("a long prompt; with \"quotes\" and 'more'\n")
    argv = sh_argv("--dir", "/home/x/repo", "--prompt-file", str(handoff))
    assert argv[-1].startswith("Read the file ")
    assert argv[-1].endswith("handoff.md and follow the instructions in it.")
    assert not any("long prompt" in a for a in argv)


def test_sh_prompt_and_prompt_file_are_mutually_exclusive(sh_argv):
    handoff = sh_argv.tmp / "handoff.md"
    handoff.write_text("x\n")
    proc = sh_argv.raw("--dir", "/home/x/repo", "--prompt", "hi", "--prompt-file", str(handoff))
    assert proc.returncode == 2, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# launch-wsl-claude.ps1
# ---------------------------------------------------------------------------

# A thin wrapper that shadows wsl.exe/wt.exe with PowerShell functions (which
# take precedence over external commands of the same name) before invoking
# the real script with -PrintArgs, so this needs no real WSL distro or
# Windows Terminal install. It forwards every bound parameter through to the
# real script untouched — the wrapper itself never sees or reconstructs the
# argument text, so it cannot introduce or hide an escaping bug.
_WRAPPER_PS1 = textwrap.dedent(
    """\
    [CmdletBinding()]
    param(
      [string] $Dir,
      [string] $Prompt,
      [string] $PromptFile,
      [string] $Distro = 'Ubuntu',
      [switch] $RemoteControl,
      [string] $RemoteControlName,
      [switch] $NoWindowsTerminal,
      [switch] $PrintArgs
    )

    function wsl.exe {
      $global:LASTEXITCODE = 0
      $joined = $args -join ' '
      if ($joined -match 'wslpath') { Write-Output '/tmp/mapped-prompt.txt' }
      elseif ($joined -match 'printf') {
        Write-Output $env:LAUNCH_WSL_CLAUDE_TEST_LOGIN_PATH
      } else {
        Write-Output $env:LAUNCH_WSL_CLAUDE_TEST_CLAUDE_PATH
      }
    }
    function wt.exe { throw 'wt.exe must not run' }
    function Start-Process { throw 'Start-Process must not run in dry-run' }
    function Register-ScheduledTask { throw 'Register-ScheduledTask must not run in dry-run' }
    function Start-ScheduledTask { throw 'Start-ScheduledTask must not run in dry-run' }
    function Unregister-ScheduledTask { throw 'Unregister-ScheduledTask must not run in dry-run' }

    $splat = @{}
    foreach ($k in $PSBoundParameters.Keys) { $splat[$k] = $PSBoundParameters[$k] }
    & $env:LAUNCH_WSL_CLAUDE_REAL_SCRIPT @splat
    """
)


@pytest.fixture()
def ps1_argv(tmp_path):
    """Returns a function that runs the real .ps1 script (via the stub
    wrapper above) with -PrintArgs and returns the final command line wt.exe
    (or, with -NoWindowsTerminal, wsl.exe) would have received.
    """
    if not PWSH:
        pytest.skip("pwsh (PowerShell 7+) is not on PATH")

    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(_WRAPPER_PS1)

    env = dict(os.environ)
    env.update(
        LAUNCH_WSL_CLAUDE_REAL_SCRIPT=str(PS1_SCRIPT),
        LAUNCH_WSL_CLAUDE_TEST_LOGIN_PATH="/usr/bin:/fake/login/bin",
        LAUNCH_WSL_CLAUDE_TEST_CLAUDE_PATH="/fake/claude/bin/claude",
    )

    def run(*args: str) -> str:
        proc = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(wrapper), *args],
            env=env,
            capture_output=True,
            timeout=60,
            **TEXT,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # The stub never runs the real wt.exe/wsl.exe, so the last
        # non-empty line is always -PrintArgs' one-line command string.
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert lines, proc.stdout + proc.stderr
        return lines[-1]

    run.env = env
    return run


def test_ps1_prompt_with_semicolon_and_spaces_is_one_escaped_arg(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "Work issue #5; it has evidence and more",
        "-PrintArgs",
    )
    assert cmdline.endswith(r'"Work issue #5\; it has evidence and more"')
    assert "WT_STUB_SHOULD_NOT_BE_CALLED" not in cmdline


def test_ps1_prompt_without_semicolon_is_unchanged_content(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "no semicolons in this one",
        "-PrintArgs",
    )
    # Still quoted as one argument (it contains spaces), but no backslash
    # was introduced anywhere in it.
    assert cmdline.endswith('"no semicolons in this one"')
    assert "\\" not in cmdline


def test_ps1_remote_control_name_with_semicolon_is_escaped(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-RemoteControlName", "my;rc",
        "-PrintArgs",
    )
    assert "--remote-control my\\;rc" in cmdline


def test_ps1_no_windows_terminal_fallback_does_not_escape_semicolons(ps1_argv):
    # No wt.exe tokenizer involved on this path, so ';' must survive as a
    # literal character rather than picking up a backslash nothing will
    # ever strip back out.
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "a;b",
        "-NoWindowsTerminal",
        "-PrintArgs",
    )
    assert cmdline.endswith("a;b")
    assert r"a\;b" not in cmdline


_PS1_PERSIST = " -- env -u CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 PATH="


@pytest.mark.parametrize("extra", [[], ["-NoWindowsTerminal"]])
def test_ps1_launched_env_clears_child_marker_and_forces_persistence(ps1_argv, extra):
    cmdline = ps1_argv("-Dir", "/home/x/repo", "-Prompt", "hi", *extra, "-PrintArgs")
    assert _PS1_PERSIST in cmdline
    # claude follows the PATH assignment as its resolved absolute path.
    after = cmdline.split(_PS1_PERSIST, 1)[1]
    assert " /fake/claude/bin/claude " in after


def test_ps1_bare_remote_control_goes_after_the_prompt(ps1_argv):
    cmdline = ps1_argv("-Dir", "/home/x/repo", "-Prompt", "stand by", "-RemoteControl", "-PrintArgs")
    assert cmdline.endswith('"stand by" --remote-control')


def test_ps1_prompt_file_becomes_an_instruction_to_read_it(ps1_argv):
    cmdline = ps1_argv("-Dir", "/home/x/repo", "-PromptFile", "/home/x/handoff.md", "-PrintArgs")
    assert cmdline.endswith('"Read the file /home/x/handoff.md and follow the instructions in it."')


# ---------------------------------------------------------------------------
# launch-claude-session.ps1 — native Windows Terminal tab
# ---------------------------------------------------------------------------
#
# Run for real under pwsh with -PrintArgs: line 1 is the wt.exe command line,
# the rest is the script the tab's shell runs. That script travels to wt as
# -EncodedCommand (UTF-16LE base64), so the tests decode it from the command
# line itself and check it matches — then EXECUTE it against a stub claude
# found first on PATH, so the environment assertions are about what a real
# process gets, not about text.

NATIVE_PS1 = SKILL_DIR / "scripts" / "launch-claude-session.ps1"
WINDOWS_POWERSHELL = shutil.which("powershell") if sys.platform.startswith("win") else None


def _write_claude_stub(bin_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        stub = bin_dir / "claude.cmd"
        stub.write_text(
            "@echo off\r\n"
            "echo CHILD=[%CLAUDE_CODE_CHILD_SESSION%]\r\n"
            "echo FORCE=[%CLAUDE_CODE_FORCE_SESSION_PERSISTENCE%]\r\n",
            newline="",
        )
        return stub
    stub = bin_dir / "claude"
    _make_executable_stub(stub, _ENV_REPORTING_CLAUDE_SH)
    return stub


def _decode_encoded_command(cmdline: str) -> str:
    tokens = cmdline.split()
    b64 = tokens[tokens.index("-EncodedCommand") + 1]
    return base64.b64decode(b64).decode("utf-16-le")


@pytest.fixture()
def native(tmp_path):
    if not PWSH:
        pytest.skip("pwsh (PowerShell 7+) is not on PATH")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = _write_claude_stub(bin_dir)
    work = tmp_path / "work dir"
    work.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", None)
    env.update(TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))

    class Native:
        pass

    n = Native()
    n.tmp, n.stub, n.work, n.env = tmp_path, stub, work, env
    wrapper = tmp_path / "native-wrapper.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Start-Process { throw 'Start-Process must not run in tests' }\n"
        "function Register-ScheduledTask { throw 'Register-ScheduledTask must not run in tests' }\n"
        "function Start-ScheduledTask { throw 'Start-ScheduledTask must not run in tests' }\n"
        "& $env:LAUNCH_CLAUDE_NATIVE_REAL_SCRIPT @args\nexit $LASTEXITCODE\n"
    )
    env["LAUNCH_CLAUDE_NATIVE_REAL_SCRIPT"] = str(NATIVE_PS1)

    def raw(*args: str, shell: list[str] | None = None) -> subprocess.CompletedProcess:
        cmd = (shell or [PWSH, "-NoProfile", "-File"]) + [str(wrapper), *args]
        return subprocess.run(cmd, env=env, capture_output=True, timeout=60, **TEXT)

    def run(*args: str, shell: list[str] | None = None) -> tuple[str, str]:
        proc = raw(*args, "-PrintArgs", shell=shell)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        lines = proc.stdout.splitlines()
        cmdline, script = lines[0], "\n".join(lines[1:]).strip()
        assert _decode_encoded_command(cmdline) == script
        return cmdline, script

    n.raw, n.run = raw, run
    return n


def _call_line(script: str) -> str:
    return [ln for ln in script.splitlines() if ln.startswith("& ")][-1]


def test_native_wt_command_opens_a_tab_in_the_directory(native):
    cmdline, _ = native.run("-Dir", str(native.work))
    assert cmdline.startswith("new-tab -d ")
    assert "work dir" in cmdline
    assert " -NoExit -EncodedCommand " in cmdline


def test_native_tab_script_clears_child_marker_then_forces_persistence(native):
    _, script = native.run("-Dir", str(native.work), "-Prompt", "hi")
    lines = script.splitlines()
    clear = lines.index("Remove-Item -Path Env:CLAUDE_CODE_CHILD_SESSION -ErrorAction SilentlyContinue")
    force = lines.index("$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'")
    call = lines.index(_call_line(script))
    assert clear < call and force < call
    assert lines[call - 1].startswith("Set-Location -LiteralPath '")
    assert lines[call - 1].endswith("work dir'")


def test_native_claude_is_a_resolved_full_path(native):
    _, script = native.run("-Dir", str(native.work), "-Prompt", "hi")
    call = _call_line(script)
    resolved = call[len("& '"):].split("'", 1)[0]
    assert os.path.isabs(resolved)
    assert os.path.samefile(resolved, native.stub)


def test_native_tab_script_really_runs_without_the_child_marker(native):
    cmdline, _ = native.run("-Dir", str(native.work), "-Prompt", "stand by; it's fine")
    b64 = cmdline.split()[cmdline.split().index("-EncodedCommand") + 1]
    env = dict(native.env, CLAUDE_CODE_CHILD_SESSION="1")
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
        env=env, capture_output=True, timeout=60, **TEXT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert any(ln in ("CHILD=[]", "CHILD=[unset]") for ln in lines), proc.stdout
    assert "FORCE=[1]" in lines
    if not sys.platform.startswith("win"):
        # The whole prompt, quote and ';' included, is ONE argv element.
        assert lines[-1] == "ARG:stand by; it's fine"


def test_native_prompt_is_one_quoted_literal(native):
    _, script = native.run("-Dir", str(native.work), "-Prompt", "it's; a test")
    assert _call_line(script).endswith(" 'it''s; a test'")


def test_native_without_a_prompt_opens_by_session_id(native):
    _, script = native.run("-Dir", str(native.work))
    assert re.search(r" '--session-id' '[0-9a-f-]{36}'$", _call_line(script))


def test_native_bare_remote_control_goes_after_the_prompt(native):
    _, script = native.run("-Dir", str(native.work), "-Prompt", "stand by", "-RemoteControl")
    assert _call_line(script).endswith(" 'stand by' '--remote-control'")


def test_native_remote_control_with_a_name(native):
    _, script = native.run("-Dir", str(native.work), "-Prompt", "hi", "-RemoteControlName", "rc-one")
    call = _call_line(script)
    assert " '--remote-control' 'rc-one' 'hi'" in call
    assert call.count("--remote-control") == 1


def test_native_prompt_file_becomes_an_instruction_to_read_it(native):
    handoff = native.tmp / "handoff.md"
    handoff.write_text("a long prompt; with \"quotes\" and 'more'\n")
    _, script = native.run("-Dir", str(native.work), "-PromptFile", str(handoff))
    call = _call_line(script)
    assert call.endswith("handoff.md and follow the instructions in it.'")
    assert "' 'Read the file " in call
    assert "long prompt" not in script


def test_native_semicolon_in_dir_is_escaped_for_wt(native):
    odd = native.tmp / "a;b"
    odd.mkdir()
    cmdline, script = native.run("-Dir", str(odd))
    assert "a\\;b" in cmdline
    # The tab script is not re-parsed by wt, so it keeps the real name.
    assert "a;b'" in script and "a\\;b" not in script


def test_native_prompt_and_prompt_file_are_mutually_exclusive(native):
    handoff = native.tmp / "handoff.md"
    handoff.write_text("x\n")
    proc = native.raw("-Dir", str(native.work), "-Prompt", "hi", "-PromptFile", str(handoff))
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_native_missing_directory_fails_before_launching(native):
    proc = native.raw("-Dir", str(native.tmp / "nope"), "-PrintArgs")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "new-tab" not in proc.stdout


@pytest.mark.skipif(not WINDOWS_POWERSHELL, reason="Windows PowerShell 5.1 is Windows-only")
def test_native_runs_under_windows_powershell_5_1(native):
    shell = [WINDOWS_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    _, script = native.run("-Dir", str(native.work), "-Prompt", "hi", shell=shell)
    assert "$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'" in script.splitlines()


# ---------------------------------------------------------------------------
# Windows session 0: no Store alias activation outside the user's desktop.
# All tests stop at a dry-run or shadow every scheduling/process cmdlet.
# ---------------------------------------------------------------------------

SESSION0_HELPER = SKILL_DIR / "scripts" / "launch-session0-task.ps1"


def _task_preview(output):
    lines = [line for line in output.splitlines() if line.startswith("TASK ")]
    assert len(lines) == 1, output
    return json.loads(lines[0][5:])


def _assert_interactive_task(task):
    assert task["TaskName"].startswith("launch-claude-session-")
    assert task["Principal"] == {
        "UserId": "<current-user>", "LogonType": "Interactive", "RunLevel": "Limited"
    }
    assert task["Action"]["Execute"] == "wt.exe"


def test_native_session0_prints_interactive_task_without_prompt_text(native):
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    proc = native.raw("-Dir", str(native.work), "-Prompt", "private test prompt; unchanged", "-PrintArgs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    task = _task_preview(proc.stdout)
    _assert_interactive_task(task)
    script = _decode_encoded_command(task["Action"]["Arguments"])
    assert "private test prompt" not in script
    assert "Read the file " in script
    assert "launch-claude-prompt-<guid>.txt" in script
    assert not list(native.tmp.rglob("launch-claude-prompt*"))
    encoded = task["Action"]["Arguments"].split("-EncodedCommand ", 1)[1]
    launched = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=dict(native.env, CLAUDE_CODE_CHILD_SESSION="1"), capture_output=True, timeout=60, **TEXT)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert any(line in ("CHILD=[]", "CHILD=[unset]") for line in launched.stdout.splitlines())
    assert "FORCE=[1]" in launched.stdout.splitlines()


def test_native_session1_keeps_direct_encoded_command(native):
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "0"
    command, script = native.run("-Dir", str(native.work), "-Prompt", "direct; text")
    assert command.startswith("new-tab -d ")
    if not sys.platform.startswith("win"):
        encoded = command.split("-EncodedCommand ", 1)[1]
        proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=native.env, capture_output=True, timeout=60, **TEXT)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.splitlines()[-1] == "ARG:direct; text"


def test_native_session0_preserves_directory_semicolon_escape(native):
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    odd = native.tmp / "a;b"
    odd.mkdir()
    proc = native.raw("-Dir", str(odd), "-PrintArgs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    arguments = _task_preview(proc.stdout)["Action"]["Arguments"]
    assert "a\\;b" in arguments
    assert "a;b'" in _decode_encoded_command(arguments)


def test_ps1_session0_keeps_wt_argv_for_prompt_file(ps1_argv):
    args = ("-Dir", "/home/x/repo", "-PromptFile", "/tmp/handoff;notes.txt", "-RemoteControlName", "rc;name", "-PrintArgs")
    ps1_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "0"
    direct = ps1_argv(*args)
    ps1_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    task = _task_preview(ps1_argv(*args))
    _assert_interactive_task(task)
    assert task["Action"]["Arguments"] == direct
    assert _PS1_PERSIST in direct
    assert "rc\\;name" in direct and "handoff\\;notes" in direct


def test_ps1_session0_prompt_becomes_path_only(ps1_argv):
    ps1_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    task = _task_preview(ps1_argv("-Dir", "/home/x/repo", "-Prompt", "private; prompt", "-PrintArgs"))
    assert "private" not in task["Action"]["Arguments"]
    assert "Read the file /tmp/launch-claude-prompt-<guid>.txt" in task["Action"]["Arguments"]


def test_ps1_no_windows_terminal_remains_direct_in_session0(ps1_argv):
    ps1_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    output = ps1_argv("-Dir", "/home/x/repo", "-Prompt", "direct; prompt", "-NoWindowsTerminal", "-PrintArgs")
    assert "TASK " not in output
    assert output.endswith('"direct; prompt"')


def _stub_windows_ps_for_bash(sh_argv):
    if not PWSH:
        pytest.skip("pwsh is not on PATH")
    wrapper = sh_argv.tmp / "bash-ps-wrapper.ps1"
    wrapper.write_text(
        "function Register-ScheduledTask { throw 'No registration in tests' }\n"
        "function Start-ScheduledTask { throw 'No scheduled start in tests' }\n"
        "$target = $args[0]\n$forward = $args[1..($args.Length - 1)]\n& $target @forward\n"
    )
    sh_argv.env["TEST_PS_WRAPPER"] = str(wrapper)
    _make_executable_stub(sh_argv.bin_dir / "pwsh.exe", '#!/usr/bin/env bash\nwhile [ "$1" != -File ]; do shift; done\nshift\nexec "$TEST_PWSH" -NoProfile -File "$TEST_PS_WRAPPER" "$@"\n')
    _make_executable_stub(sh_argv.bin_dir / "wslpath", '#!/usr/bin/env bash\nprintf "%s\\n" "$2"\n')
    sh_argv.env["TEST_PWSH"] = PWSH


def test_sh_session0_task_preserves_argv_and_hides_prompt(sh_argv):
    _stub_windows_ps_for_bash(sh_argv)
    sh_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    proc = sh_argv.raw("--dir", "/home/x/repo;one", "--prompt", "private; prompt", "--remote-control-name", "rc;one")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    task = _task_preview(proc.stdout)
    _assert_interactive_task(task)
    arguments = task["Action"]["Arguments"]
    assert arguments.startswith("wsl.exe -d Ubuntu --cd /home/x/repo\\;one -- env ")
    assert "-u CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1" in arguments
    assert "PATH=" in arguments and "/claude" in arguments
    assert "rc\\;one" in arguments
    assert "private" not in arguments
    assert arguments.endswith('"Read the file /tmp/launch-claude-prompt-<guid>.txt and follow the instructions in it."')


def test_sh_session1_keeps_direct_argv(sh_argv):
    sh_argv.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "0"
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "direct; prompt")
    assert argv[0].endswith("/wt.exe")
    assert argv[-1] == "direct\\; prompt"


_TASK_STUBS = r"""
$ErrorActionPreference = 'Stop'
. $env:TEST_SESSION0_HELPER
function Get-LaunchCurrentUser { [pscustomobject] @{ Name = 'EXAMPLE\fictional'; User = @{ Value = 'fictional-sid' } } }
function Test-LaunchInteractiveSession { $env:TEST_TASK_SCENARIO -ne 'no-user' }
function Get-Command { [pscustomobject] @{ Source = 'C:\example\wt.exe' } }
function New-ScheduledTaskPrincipal {
  param($UserId, $LogonType, $RunLevel)
  if ($UserId -ne 'EXAMPLE\fictional' -or $LogonType -ne 'Interactive' -or $RunLevel -ne 'Limited') { throw 'Bad principal' }
  'principal'
}
function New-ScheduledTaskAction {
  param($Execute, $Argument)
  if ($Execute -ne 'C:\example\wt.exe' -or $Argument -ne 'unchanged\; args') { throw 'Bad action' }
  'action'
}
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries) 'settings' }
function Register-ScheduledTask {
  param($TaskName, $TaskPath, $Action, $Principal, $Settings, $Description, $ErrorAction)
  if ($env:TEST_TASK_SCENARIO -ne 'register-no-create') { $script:taskDescription = $Description }
  'REGISTER'
  if ($env:TEST_TASK_SCENARIO -in @('register-fails', 'register-no-create')) { throw 'registration failed' }
}
function Start-ScheduledTask {
  param($TaskName, $TaskPath, $ErrorAction)
  'START'
  if ($env:TEST_TASK_SCENARIO -eq 'start-fails') { throw 'start failed' }
}
function Get-ScheduledTask {
  param($TaskName, $TaskPath, $ErrorAction)
  if (-not $TaskName) { throw 'Dedicated folder absent' }
  if ($env:TEST_TASK_SCENARIO -eq 'poll-fails') { throw 'poll failed' }
  $state = if ($env:TEST_TASK_SCENARIO -eq 'running') { 'Running' } else { 'Ready' }
  [pscustomobject] @{ State = $state; Description = $script:taskDescription }
}
$script:infoCalls = 0
function Get-ScheduledTaskInfo {
  param($TaskName, $TaskPath, $ErrorAction)
  $script:infoCalls++
  $result = if ($env:TEST_TASK_SCENARIO -eq 'result-fails') { 5 } elseif ($env:TEST_TASK_SCENARIO -eq 'running') { 267009 } else { 0 }
  $run = if ($script:infoCalls -gt 1 -and $env:TEST_TASK_SCENARIO -notin @('timeout', 'running')) { 1 } else { 0 }
  [pscustomobject] @{ LastRunTime = $run; LastTaskResult = $result }
}
function Start-Sleep { param($Milliseconds) }
function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm, $ErrorAction) 'UNREGISTER' }
function Start-Process { throw 'No direct launch is allowed' }
try { Invoke-LaunchSession0Task -CommandLine 'unchanged\; args'; 'OK' }
catch { 'FAIL: ' + $_.Exception.Message; exit 1 }
"""


@pytest.mark.parametrize("scenario, registered, expected", [
    ("success", True, 0), ("running", True, 0),
    ("no-user", False, 1), ("register-fails", True, 1), ("register-no-create", False, 1),
    ("start-fails", True, 1), ("result-fails", True, 1),
    ("timeout", True, 1), ("poll-fails", True, 1),
])
def test_session0_task_lifecycle_is_always_cleaned_up(tmp_path, scenario, registered, expected):
    if not PWSH:
        pytest.skip("pwsh is not on PATH")
    wrapper = tmp_path / "task-stubs.ps1"
    wrapper.write_text(_TASK_STUBS)
    env = dict(os.environ, TEST_SESSION0_HELPER=str(SESSION0_HELPER), TEST_TASK_SCENARIO=scenario)
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(wrapper)], env=env, capture_output=True, timeout=60, **TEXT)
    assert proc.returncode == expected, proc.stdout + proc.stderr
    assert ("UNREGISTER" in proc.stdout) is registered, proc.stdout + proc.stderr
    if scenario == "no-user":
        assert "Log on to Windows" in proc.stdout
        assert "REGISTER" not in proc.stdout
    if scenario == "timeout":
        assert "did not start" in proc.stdout


def test_session0_override_is_ignored_outside_dry_run(tmp_path):
    if not PWSH:
        pytest.skip("pwsh is not on PATH")
    wrapper = tmp_path / "session-id.ps1"
    wrapper.write_text(". $env:TEST_SESSION0_HELPER\nGet-LaunchSessionId -DryRun $false\n")
    env = dict(os.environ, TEST_SESSION0_HELPER=str(SESSION0_HELPER), LAUNCH_CLAUDE_FORCE_SESSION0="1")
    # On Windows query the actual id before setting the override; on Linux the
    # test implementation returns 1, because Windows SessionId is inapplicable.
    if sys.platform.startswith("win"):
        expected = subprocess.run([PWSH, "-NoProfile", "-Command", "[System.Diagnostics.Process]::GetCurrentProcess().SessionId"], capture_output=True, **TEXT).stdout.strip()
    else:
        expected = "1"
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(wrapper)], env=env, capture_output=True, timeout=60, **TEXT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == expected


@pytest.mark.parametrize("content", [b"", b"wsl.exe", b"\x00", b"wt.exe\x00"])
def test_session0_rejects_malformed_argument_handoff(tmp_path, content):
    if not PWSH:
        pytest.skip("pwsh is not on PATH")
    handoff = tmp_path / "argv.bin"
    handoff.write_bytes(content)
    wrapper = tmp_path / "bad-handoff.ps1"
    wrapper.write_text("function Register-ScheduledTask { throw 'No registration in tests' }\n& $env:TEST_SESSION0_HELPER -ArgumentFile $env:TEST_ARGV_FILE -PrintArgs\n")
    env = dict(os.environ, TEST_SESSION0_HELPER=str(SESSION0_HELPER), TEST_ARGV_FILE=str(handoff))
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(wrapper)], env=env, capture_output=True, timeout=60, **TEXT)
    assert proc.returncode != 0
    assert "Invalid argv handoff" in proc.stderr
    assert "TASK " not in proc.stdout


def test_native_session0_prompt_file_keeps_prepared_argv(native):
    handoff = native.tmp / "handoff.md"
    handoff.write_text("file-only prompt")
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "0"
    direct, _ = native.run("-Dir", str(native.work), "-PromptFile", str(handoff))
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    proc = native.raw("-Dir", str(native.work), "-PromptFile", str(handoff), "-PrintArgs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _task_preview(proc.stdout)["Action"]["Arguments"] == direct


def test_native_session0_rejects_missing_prompt_file_in_dry_run(native):
    native.env["LAUNCH_CLAUDE_FORCE_SESSION0"] = "1"
    proc = native.raw("-Dir", str(native.work), "-PromptFile", str(native.tmp / "missing.md"), "-PrintArgs")
    assert proc.returncode == 2
    assert "TASK " not in proc.stdout


_PROMPT_TASK_SHIM = """
. $env:TEST_SESSION0_HELPER
function Get-LaunchSessionId { param($DryRun) 0 }
function Invoke-LaunchSession0Task {
  param($CommandLine, [switch]$PrintArgs)
  if ($PrintArgs) { throw 'This fixture exercises real file creation' }
  'ACTION ' + (@{ Arguments = $CommandLine } | ConvertTo-Json -Compress)
  if ($env:TEST_PROMPT_TASK_FAIL -eq '1') { throw 'Simulated task startup failure' }
}
"""


def _copy_launcher_with_task_shim(tmp_path, source):
    directory = tmp_path / "isolated-launcher"
    directory.mkdir()
    script = directory / source.name
    script.write_bytes(source.read_bytes())
    (directory / "launch-session0-task.ps1").write_text(_PROMPT_TASK_SHIM)
    # Only source payloads are copied; this fixture carries no .git/config.
    assert not (directory / ".git").exists()
    return script


@pytest.mark.parametrize("failed", [False, True])
def test_native_session0_real_prompt_file_lifetime(native, failed):
    script = _copy_launcher_with_task_shim(native.tmp, NATIVE_PS1)
    native.env.update(
        LAUNCH_CLAUDE_NATIVE_REAL_SCRIPT=str(script),
        TEST_SESSION0_HELPER=str(SESSION0_HELPER),
        TEST_PROMPT_TASK_FAIL="1" if failed else "0",
    )
    proc = native.raw("-Dir", str(native.work), "-Prompt", "file content; only")
    files = list(native.tmp.glob("launch-claude-prompt-*.txt"))
    if failed:
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert not files
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(files) == 1
        assert files[0].read_text() == "file content; only"
        action_line = next(line[7:] for line in proc.stdout.splitlines() if line.startswith("ACTION "))
        decoded = _decode_encoded_command(json.loads(action_line)["Arguments"])
        assert "file content; only" not in decoded
        assert str(files[0]) in decoded


@pytest.mark.parametrize("failed", [False, True])
def test_ps1_session0_real_prompt_file_lifetime(ps1_argv, tmp_path, failed):
    script = _copy_launcher_with_task_shim(tmp_path, PS1_SCRIPT)
    ps1_argv.env.update(
        LAUNCH_WSL_CLAUDE_REAL_SCRIPT=str(script),
        TEST_SESSION0_HELPER=str(SESSION0_HELPER),
        TEST_PROMPT_TASK_FAIL="1" if failed else "0",
        TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
    )
    # The shared wrapper forbids real scheduling/process creation. Its wsl.exe
    # function resolves a deterministic mapped path without invoking Windows.
    wrapper = tmp_path / "real-file-wrapper.ps1"
    wrapper.write_text(_WRAPPER_PS1)
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(wrapper), "-Dir", "/home/x/repo", "-Prompt", "file content; only"], env=ps1_argv.env, capture_output=True, timeout=60, **TEXT)
    files = list(tmp_path.glob("launch-claude-prompt-*.txt"))
    if failed:
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert not files
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(files) == 1
        assert files[0].read_text() == "file content; only"
        assert "file content; only" not in proc.stdout
        assert "Read the file /tmp/mapped-prompt.txt" in proc.stdout



def test_ps1_session0_failed_supplied_prompt_preserves_caller_files(ps1_argv, tmp_path):
    script = _copy_launcher_with_task_shim(tmp_path, PS1_SCRIPT)
    supplied = tmp_path / "supplied.md"
    decoy = tmp_path / "unrelated.md"
    supplied.write_text("caller supplied prompt")
    decoy.write_text("unrelated caller file")
    ps1_argv.env.update(
        LAUNCH_WSL_CLAUDE_REAL_SCRIPT=str(script),
        TEST_SESSION0_HELPER=str(SESSION0_HELPER),
        TEST_PROMPT_TASK_FAIL="1",
        TEST_CALLER_DECOY=str(decoy),
    )
    wrapper = tmp_path / "caller-scope-wrapper.ps1"
    wrapper.write_text(_WRAPPER_PS1.replace(
        "& $env:LAUNCH_WSL_CLAUDE_REAL_SCRIPT @splat",
        "$windowsPromptFile = $env:TEST_CALLER_DECOY\n& $env:LAUNCH_WSL_CLAUDE_REAL_SCRIPT @splat",
    ))
    proc = subprocess.run([PWSH, "-NoProfile", "-File", str(wrapper), "-Dir", "/home/x/repo", "-PromptFile", str(supplied)], env=ps1_argv.env, capture_output=True, timeout=60, **TEXT)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert supplied.read_text() == "caller supplied prompt"
    assert decoy.read_text() == "unrelated caller file"
