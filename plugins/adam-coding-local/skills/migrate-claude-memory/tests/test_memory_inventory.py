"""Exercise inventory classification only against invented temporary homes."""

import json
import os
from pathlib import Path
import subprocess
import string
import shutil

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "memory-inventory.sh"

UNSUPPORTED_REASON = (
    "ERROR: memory inventory requires native POSIX paths with Bash and GNU tools; "
    "native Windows Git Bash/MSYS, Cygwin, and win32 Bash are unsupported."
)


def _script():
    return Path(os.environ.get("MEMORY_INVENTORY_TEST_SCRIPT", SCRIPT))


def _diagnostics(command, result):
    return (
        f"command: {command!r}\nreturn code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _is_windows_launcher(candidate, system_root):
    """Reject bash.exe under SystemRoot; it is the WSL launcher."""
    try:
        Path(candidate).resolve().relative_to(Path(system_root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _find_posix_bash(*, platform_name=None, which=shutil.which, environ=None):
    """Find an absolute POSIX Bash path, excluding System32's WSL launcher.

    Candidate search follows scripts/test_generate_skills_lock.py's
    ``_find_posix_bash``; kept local because importing that test module would
    require a sys.path change.
    """
    platform_name = os.name if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    found = which("bash")
    if platform_name != "nt":
        return str(Path(found).resolve()) if found else None

    candidates = [Path(found)] if found else []
    git = which("git")
    if git:
        git_path = Path(git).resolve()
        for up in (2, 3):
            try:
                root = git_path.parents[up - 1]
            except IndexError:
                continue
            candidates.append(root / "usr" / "bin" / "bash.exe")
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = environ.get(var)
        if base:
            candidates.append(Path(base) / "Git" / "usr" / "bin" / "bash.exe")
            candidates.append(Path(base) / "Programs" / "Git" / "usr" / "bin" / "bash.exe")

    system_root = Path(environ.get("SystemRoot", r"C:\Windows"))
    for candidate in candidates:
        if _is_windows_launcher(candidate, system_root):
            continue
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _capability_decision(bash, returncode, stdout, stderr, *, is_windows):
    """Return (run|skip|fail, reason) for the actual script capability probe."""
    if bash is None:
        return "skip", "no POSIX bash found (only the WSL launcher or none)"
    if returncode == 0:
        return "run", ""
    if returncode == 3 and is_windows and stdout == "" and stderr == UNSUPPORTED_REASON + "\n":
        return "skip", f"native Windows rejection: {UNSUPPORTED_REASON!r}"
    return "fail", f"capability probe exited {returncode}; stdout={stdout!r}; stderr={stderr!r}"


@pytest.mark.parametrize(
    "bash,returncode,stdout,stderr,is_windows,expected,reason_part",
    [
        (None, None, "", "", True, "skip", "no POSIX bash found"),
        (None, None, "", "", False, "skip", "no POSIX bash found"),
        ("/git/bash", 3, "", UNSUPPORTED_REASON + "\n", True, "skip", UNSUPPORTED_REASON),
        ("/usr/bin/bash", 3, "", UNSUPPORTED_REASON + "\n", False, "fail", "exited 3"),
        ("/git/bash", 3, "unexpected", UNSUPPORTED_REASON + "\n", True, "fail", "stdout='unexpected'"),
        ("/usr/bin/bash", 0, "[]\n", "", False, "run", ""),
        ("/usr/bin/bash", 3, "", "different\n", True, "fail", "stdout=''; stderr='different\\n'"),
        ("/usr/bin/bash", 1, "partial", "problem", False, "fail", "stdout='partial'; stderr='problem'"),
    ],
    ids=[
        "no-bash-windows", "no-bash-posix", "native-windows-rejection",
        "linux-exit-3-fails", "windows-with-stdout-fails", "exit-zero-runs",
        "windows-other-exit-fails", "other-exit-fails",
    ],
)
def test_capability_decision_table(
    bash, returncode, stdout, stderr, is_windows, expected, reason_part
):
    decision, reason = _capability_decision(
        bash, returncode, stdout, stderr, is_windows=is_windows
    )
    assert decision == expected
    assert reason_part in reason


def test_posix_bash_resolver_skips_windows_launcher_and_finds_git_bash(tmp_path):
    system = tmp_path / "Windows"
    launcher = system / "System32" / "bash.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    git_bash = tmp_path / "Program Files" / "Git" / "usr" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.touch()
    found = {"bash": str(launcher), "git": None}
    result = _find_posix_bash(
        platform_name="nt",
        which=lambda name: found.get(name),
        environ={"SystemRoot": str(system), "ProgramFiles": str(tmp_path / "Program Files")},
    )
    assert result == str(git_bash.resolve())


def test_posix_bash_resolver_returns_none_when_only_wsl_launcher_exists(tmp_path):
    system = tmp_path / "Windows"
    launcher = system / "System32" / "bash.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    result = _find_posix_bash(
        platform_name="nt",
        which=lambda name: str(launcher) if name == "bash" else None,
        environ={"SystemRoot": str(system)},
    )
    assert result is None


def test_posix_bash_resolver_finds_portable_git_install(tmp_path):
    git = tmp_path / "Portable" / "Git" / "cmd" / "git.exe"
    git.parent.mkdir(parents=True)
    git.touch()
    bash = tmp_path / "Portable" / "Git" / "usr" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.touch()
    result = _find_posix_bash(
        platform_name="nt",
        which=lambda name: str(git) if name == "git" else None,
        environ={"SystemRoot": str(tmp_path / "Windows")},
    )
    assert result == str(bash.resolve())


@pytest.mark.parametrize("found", [True, False], ids=["absolute-path", "missing"])
def test_posix_bash_resolver_on_posix_returns_absolute_or_none(tmp_path, found):
    bash = tmp_path / "bin" / "bash"
    bash.parent.mkdir()
    bash.touch()
    result = _find_posix_bash(
        platform_name="posix",
        which=lambda name: str(bash) if found else None,
        environ={},
    )
    assert result == (str(bash.resolve()) if found else None)


def _run(command, *, env, expected=0):
    result = subprocess.run(
        command, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="surrogateescape",
    )
    if expected is not None and result.returncode != expected:
        raise AssertionError(_diagnostics(command, result))
    return result


@pytest.fixture
def inventory_runtime(tmp_path):
    """Return Bash after the script's platform capability has been verified."""
    bash = _find_posix_bash()
    if bash is None:
        decision, reason = _capability_decision(
            bash, None, "", "", is_windows=(os.name == "nt")
        )
        if decision == "skip":
            pytest.skip(reason)
        pytest.fail(reason)
    home = tmp_path / "capability-home"
    (home / ".claude" / "projects").mkdir(parents=True)
    temp = tmp_path / "capability-temp"
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    command = [bash, _script().as_posix(), "--json"]
    result = _run(command, env=env, expected=None)
    decision, reason = _capability_decision(
        bash, result.returncode, result.stdout, result.stderr, is_windows=(os.name == "nt")
    )
    if decision == "skip":
        pytest.skip(reason)
    assert decision == "run", f"{reason}\n{_diagnostics(command, result)}"
    assert result.stdout == "[]\n", _diagnostics(command, result)
    assert list(temp.iterdir()) == []
    assert _snapshot(home) == {}
    return bash


def _restrict_or_skip(bash, directory, env):
    """Probe actual Bash permissions instead of assuming platform or user ID."""
    try:
        directory.chmod(0)
    except OSError as error:
        pytest.skip(f"filesystem cannot remove directory permissions: {type(error).__name__}")
    result = _run(
        [bash, "-c", '[[ ! -r "$1" && ! -x "$1" ]]', "permission-probe", directory.as_posix()],
        env=env, expected=None,
    )
    if result.returncode == 1:
        directory.chmod(0o700)
        pytest.skip("filesystem or current user does not enforce unreadable directory permissions")
    if result.returncode != 0:
        directory.chmod(0o700)
        raise AssertionError(_diagnostics(result.args, result))


def _munge(path):
    """Mirror the vendor ASCII regex over UTF-16 code units."""
    ascii_alnum = string.ascii_letters + string.digits
    return "".join(
        char if char in ascii_alnum else "-" * (len(char.encode("utf-16-le")) // 2)
        for char in str(path)
    )


def _test_env(tmp_path, home, temp):
    # Keep device IDs and unrelated ancestor entries independent of the host.
    # Inside the controlled fixture root, find still examines every real entry.
    commands = tmp_path / "baseline-commands"
    commands.mkdir()
    shim = commands / "stat"
    shim.write_text('#!/usr/bin/env bash\nprintf "1\n"\n', newline="\n")
    shim.chmod(0o700)
    real_find = shutil.which("find")
    assert real_find is not None
    shim = commands / "find"
    shim.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$#" == 6 && "$2" == -mindepth && "$3" == 1 && "$4" == -maxdepth && "$5" == 1 && "$6" == -print0 && "$1" != "$INVENTORY_FIXTURE_ROOT" && "$INVENTORY_FIXTURE_ROOT" == "${1%/}/"* ]]; then\n'
        '  suffix="${INVENTORY_FIXTURE_ROOT#"${1%/}/"}"\n'
        '  printf "%s\\0" "${1%/}/${suffix%%/*}"\n'
        '  exit 0\n'
        'fi\n'
        'exec "$INVENTORY_BASE_FIND" "$@"\n', newline="\n"
    )
    shim.chmod(0o700)
    return {**os.environ, "HOME": str(home), "TMPDIR": str(temp), "PATH": str(commands) + os.pathsep + os.environ["PATH"], "INVENTORY_FIXTURE_ROOT": str(tmp_path), "INVENTORY_BASE_FIND": real_find}


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
        ("dotted_only", "EXISTING"),
        ("symlink", "UNRESOLVED"),
        ("dangling_symlink", "UNRESOLVED"),
        ("file_target", "UNRESOLVED"),
        ("missing_ancestor", "UNRESOLVED"),
        ("space_existing", "EXISTING"),
        ("unicode_existing", "EXISTING"),
        ("unreadable_parent", "UNRESOLVED"),
        ("trailing_separator", "UNRESOLVED"),
        ("underscore_alias", "UNRESOLVED"),
        ("space_alias", "UNRESOLVED"),
        ("unicode_alias", "UNRESOLVED"),
        ("intermediate_alias", "UNRESOLVED"),
        ("missing_suffix_branch", "UNRESOLVED"),
        ("unicode_parent", "ORPHANED"),
        ("consecutive_underscores", "EXISTING"),
        ("consecutive_mixed", "EXISTING"),
        ("leading_dot", "EXISTING"),
        ("astral_existing", "EXISTING"),
        ("astral_alias", "UNRESOLVED"),
        ("punctuation_only", "EXISTING"),
        ("alias_file", "UNRESOLVED"),
        ("intermediate_file", "UNRESOLVED"),
        ("alias_symlink_loop", "UNRESOLVED"),
        ("unreadable_alias", "UNRESOLVED"),
        ("raw_underscore", "UNRESOLVED"),
        ("raw_space", "UNRESOLVED"),
        ("raw_unicode", "UNRESOLVED"),
        ("long_slug", "UNRESOLVED"),
        ("invalid_utf8_sibling", "UNRESOLVED"),
    ],
)
def test_inventory_classification(tmp_path, inventory_runtime, scenario, status):
    bash = inventory_runtime
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
        else:
            workspace = workspaces / "example.com"
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
        restricted = workspaces / "restricted"
        restricted.mkdir()
        workspace = restricted / "project"

    if scenario in {"underscore_alias", "space_alias", "unicode_alias", "alias_file", "alias_symlink_loop", "unreadable_alias"}:
        prefix, alias = {
            "underscore_alias": ("proj", "proj_old"),
            "space_alias": ("spc", "spc leaf"),
            "unicode_alias": ("na", "naïve"),
        }.get(scenario, ("proj", "proj_old"))
        (workspaces / prefix).mkdir()
        workspace = workspaces / alias
        if scenario == "alias_file":
            workspace.write_text("Invented replacement for example.net.\n")
        elif scenario == "alias_symlink_loop":
            workspace.symlink_to(workspace.name, target_is_directory=True)
        else:
            workspace.mkdir()
            if scenario == "unreadable_alias":
                restricted = workspace
    elif scenario == "intermediate_alias":
        (workspaces / "area-one").mkdir()
        (workspaces / "area_one").mkdir()
        workspace = workspaces / "area-one" / "leaf"
    elif scenario == "missing_suffix_branch":
        (workspaces / "a-b").mkdir()
        (workspaces / "a").mkdir()
        workspace = workspaces / "a-b" / "c"
    elif scenario == "unicode_parent":
        (workspaces / "parenté").mkdir()
        workspace = workspaces / "parenté" / "leaf"
    elif scenario in {"consecutive_underscores", "consecutive_mixed", "leading_dot", "astral_existing", "astral_alias", "punctuation_only"}:
        workspace = workspaces / {
            "consecutive_underscores": "a__b", "consecutive_mixed": "a - b",
            "leading_dot": ".hidden", "astral_existing": "na😀ve",
            "astral_alias": "na😀ve", "punctuation_only": "__",
        }[scenario]
        workspace.mkdir()
        if scenario == "astral_alias":
            (workspaces / "na--ve").mkdir()
    elif scenario == "intermediate_file":
        (workspaces / "area-one").mkdir()
        (workspaces / "area_one").write_text("Invented obstruction for example.com.\n")
        workspace = workspaces / "area-one" / "leaf"
    if scenario == "invalid_utf8_sibling":
        invalid_name = b"invalid\xff".decode("utf-8", errors="surrogateescape")
        try:
            (workspaces / invalid_name).mkdir()
        except (UnicodeError, OSError) as error:
            pytest.skip(f"filesystem cannot create an invalid UTF-8 sibling: {type(error).__name__}")
    if scenario == "trailing_separator":
        workspace.mkdir()
    munged = _munge(workspace)
    if scenario == "trailing_separator":
        munged += "-"
    if scenario == "unknown":
        munged = "custom-store"
    elif scenario in {"raw_dot", "raw_underscore", "raw_space", "raw_unicode"}:
        munged += {"raw_dot": ".custom", "raw_underscore": "_custom", "raw_space": " custom", "raw_unicode": "é"}[scenario]
    elif scenario == "long_slug":
        munged += "x" * (210 - len(munged))
    memory = home / ".claude" / "projects" / munged / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Invented memory for example.com.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = _test_env(tmp_path, home, temp)
    before = _snapshot(home)
    script = _script()
    if restricted is not None:
        _restrict_or_skip(bash, restricted, env)
    try:
        result = _run(
            [bash, script.as_posix(), "--json"], env=env,
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

        text = _run(
            [bash, script.as_posix()], env=env,
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
def test_inventory_summary(tmp_path, inventory_runtime, populated):
    bash = inventory_runtime
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
            _munge(existing): "EXISTING",
            _munge(missing): "ORPHANED",
            "custom-store": "UNRESOLVED",
        }
        for munged in expected:
            memory = projects / munged / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("Invented memory for example.net.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = _test_env(tmp_path, home, temp)
    script = _script()
    before = _snapshot(home)
    result = _run(
        [bash, script.as_posix(), "--json"], env=env,
    )
    assert {entry["munged"]: entry["status"] for entry in json.loads(result.stdout)} == expected
    text = _run(
        [bash, script.as_posix()], env=env,
    ).stdout
    count = int(populated)
    assert text.rstrip().endswith(
        f"{3 * count} stores, {count} orphaned (decoded workspace path does not exist), "
        f"{count} unresolved (path could not be determined safely)"
    )
    assert _snapshot(home) == before
    assert list(temp.iterdir()) == []


@pytest.mark.parametrize("path", ["/mnt/x/leaf", "/mnt/c/Case/leaf", "/media/leaf", "/run/media/leaf", "/Volumes/leaf"])
def test_known_mount_roots_are_unresolved(tmp_path, inventory_runtime, path):
    """No real mount or drive is inspected; only the lexical guard is exercised."""
    bash = inventory_runtime
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    env = _test_env(tmp_path, home, temp)
    script = _script()
    before = _snapshot(home)
    result = _run(
        [bash, "-c", 'source "$1" --json; _decode_try() { _decode_candidate "$1" ORPHANED; }; decode_munged_path "$2"; printf "%s\n" "$decode_status"', "inventory-test", script.as_posix(), _munge(path)],
        env=env,
    )
    assert result.stdout.splitlines() == ["[]", "UNRESOLVED"]
    assert _snapshot(home) == before
    assert list(temp.iterdir()) == []


@pytest.mark.parametrize("failure", ["device", "malformed_device", "stat_error", "enumeration_error", "locale_error"])
def test_unexamined_parent_is_unresolved(tmp_path, inventory_runtime, failure):
    bash = inventory_runtime
    home = tmp_path / "home"
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    workspace = workspaces / "leaf"
    memory = home / ".claude" / "projects" / _munge(workspace) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Invented memory for example.com.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    command = {"enumeration_error": "find", "locale_error": "locale"}.get(failure, "stat")
    real_command = shutil.which(command)
    assert real_command is not None
    shim = commands / command
    shim.write_text(
        '#!/usr/bin/env bash\n'
        'for argument in "$@"; do\n'
        '  if [[ "$argument" == "$INVENTORY_BLOCKED_PARENT" ]]; then\n'
        + ('    printf "999999999\n"; exit 0\n' if failure == "device" else '    printf "invalid\n"; exit 0\n' if failure == "malformed_device" else '    exit 1\n')
        + '  fi\ndone\nexec "$INVENTORY_REAL_COMMAND" "$@"\n', newline="\n"
    )
    if failure == "locale_error":
        shim.write_text("#!/usr/bin/env bash\nexit 1\n", newline="\n")
    shim.chmod(0o700)
    env = _test_env(tmp_path, home, temp)
    if command in {"stat", "find"}:
        real_command = str(tmp_path / "baseline-commands" / command)
    env.update({"PATH": str(commands) + os.pathsep + env["PATH"], "INVENTORY_BLOCKED_PARENT": str(workspaces), "INVENTORY_REAL_COMMAND": real_command})
    script = _script()
    before = _snapshot(home)
    result = _run([bash, script.as_posix(), "--json"], env=env)
    entries = json.loads(result.stdout)
    assert len(entries) == 1
    assert entries[0]["status"] == "UNRESOLVED"
    assert entries[0]["path"] is None
    assert entries[0]["orphaned"] is False
    assert entries[0]["unresolved"] is True
    assert _snapshot(home) == before
    assert list(temp.iterdir()) == []


def test_failure_diagnostics_preserve_invalid_bytes_and_both_streams(tmp_path, inventory_runtime):
    bash = inventory_runtime
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    command = [bash, "-c", r"printf 'stdout\377'; printf 'stderr\376' >&2; exit 7"]
    with pytest.raises(AssertionError) as failure:
        _run(command, env=env)
    message = str(failure.value)
    assert f"command: {command!r}" in message
    assert "return code: 7" in message
    assert "stdout:\nstdout\udcff" in message
    assert "stderr:\nstderr\udcfe" in message


@pytest.mark.parametrize("ostype", ["msys", "msys_nt", "cygwin", "cygwin_nt", "win32", "win32_nt"])
@pytest.mark.parametrize("missing_home", [False, True], ids=["empty-projects", "missing-home"])
def test_windows_guard_precedes_home_lookup_without_modifying_files(
    tmp_path, inventory_runtime, ostype, missing_home
):
    bash = inventory_runtime
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    if missing_home:
        env["HOME"] = str(tmp_path / "absent-home")
    before = _snapshot(tmp_path)
    paths_before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    result = _run(
        [bash, "-c", 'OSTYPE="$1"; source "$2" --json', "guard-test", ostype, _script().as_posix()],
        env=env, expected=3,
    )
    assert result.stdout == ""
    assert result.stderr == UNSUPPORTED_REASON + "\n"
    assert _snapshot(tmp_path) == before
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == paths_before


@pytest.mark.parametrize("ostype", ["msys", "cygwin", "win32"])
def test_argument_validation_precedes_windows_guard(tmp_path, inventory_runtime, ostype):
    bash = inventory_runtime
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(temp)}
    result = _run(
        [bash, "-c", 'OSTYPE="$1"; source "$2" --invalid', "guard-test", ostype, _script().as_posix()],
        env=env, expected=2,
    )
    assert result.stdout == ""
    assert result.stderr == "Usage: memory-inventory.sh [--json]\n"
    assert list(temp.iterdir()) == []


def test_unreadable_parent_with_missing_plain_leaf_is_unresolved(tmp_path, inventory_runtime):
    """An orphan-shaped missing leaf cannot establish a readable parent chain."""
    bash = inventory_runtime
    home = tmp_path / "home"
    parent = tmp_path / "workspaces" / "restricted"
    parent.mkdir(parents=True)
    workspace = parent / "leaf"
    memory = home / ".claude" / "projects" / _munge(workspace) / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Invented memory for example.com.\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    env = _test_env(tmp_path, home, temp)
    before = _snapshot(home)
    _restrict_or_skip(bash, parent, env)
    try:
        result = _run([bash, _script().as_posix(), "--json"], env=env)
        entries = json.loads(result.stdout)
        assert len(entries) == 1
        assert entries[0]["status"] == "UNRESOLVED"
        assert entries[0]["path"] is None
        assert entries[0]["orphaned"] is False
        assert _snapshot(home) == before
        assert list(temp.iterdir()) == []
    finally:
        parent.chmod(0o700)
