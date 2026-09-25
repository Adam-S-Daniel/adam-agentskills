#!/usr/bin/env python3
"""check_privacy_denylist.py — fail when a denylisted personal detail is in the repo.

This is a PUBLIC repository, and the one it replaced had to be retired because
its git history carried personal data (ADR 0013). A scanner that ships the list
of what it looks for would publish exactly that data, so the list is never in
the repo: it arrives at run time, from the environment, and this script never
prints it back.

Where the denylist comes from, first match wins:

  PRIVACY_DENYLIST       newline-separated regular expressions (CI passes the
                         repository secret of the same name through `env:`)
  PRIVACY_DENYLIST_FILE  path to a file holding the same thing (local use)

One pattern per line. Blank lines and lines starting with `#` are ignored.
Patterns are Python regular expressions matched CASE-INSENSITIVELY; a pattern
that needs case can say so inline with `(?-i:...)`.

What is scanned:

  * every tracked file's path and text (`git ls-files`), as UTF-8 with
    undecodable bytes replaced, so a binary file is scanned for its ASCII runs
    too — and, when it holds NUL bytes and decodes as UTF-16, as UTF-16 as
    well; a symlink by its committed target;
  * the branch being built, when `GITHUB_HEAD_REF` / `GITHUB_REF_NAME` names
    it;
  * with `--base REF`, additionally every commit in `REF..HEAD`: its message,
    its author and committer name and email, and the lines it ADDED against
    its first parent (so a line typed into a merge commit is seen). A detail
    added in one commit and deleted in the next is absent from the tree and
    still public in the history, which is the whole failure this check exists
    to stop.

What is printed, on a match: `path:line: pattern #N` (or `<sha>:path:line`,
`<sha>:message:line`, `<sha>:identity:line`, `branch-name` for history and
the branch). NEVER the matched text and NEVER the
pattern — a CI log on a public repo is public, and either one would publish
the detail being protected. The pattern INDEX is enough for the operator, who
holds the list, to find which entry fired.

Exit status: 0 clean; 1 a match; 2 the check could not run (no denylist in CI,
an invalid pattern, git failing). With no denylist configured the check FAILS
CLOSED when `CI=true` (a green privacy job that checked nothing is worse than a
red one), and warns and exits 0 locally, where a contributor may simply not
hold the list.

Usage:
  python3 scripts/check_privacy_denylist.py [--repo PATH] [--base REF]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator, List, Mapping, Optional, Sequence, Tuple

ENV_PATTERNS = "PRIVACY_DENYLIST"
ENV_FILE = "PRIVACY_DENYLIST_FILE"

# A match is (where, pattern index). `where` is already formatted for output.
Match = Tuple[str, int]


class CheckError(Exception):
    """The check could not run. The message never contains a pattern."""


def load_patterns(environ: Mapping[str, str]) -> Optional[List["re.Pattern[str]"]]:
    """The compiled denylist, or None when none is configured.

    An invalid pattern raises CheckError naming its INDEX only.
    """
    raw = environ.get(ENV_PATTERNS)
    if not raw or not raw.strip():
        path = environ.get(ENV_FILE)
        if not path:
            return None
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckError(
                f"{ENV_FILE} is set but the file could not be read ({type(exc).__name__})"
            ) from None
    lines = [line.strip() for line in raw.replace("\r\n", "\n").split("\n")]
    entries = [line for line in lines if line and not line.startswith("#")]
    if not entries:
        return None
    compiled = []
    for index, pattern in enumerate(entries, start=1):
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            # `from None`: the re.error message quotes the pattern.
            raise CheckError(f"pattern #{index} is not a valid regular expression") from None
    return compiled


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        # git's stderr can echo a path or a ref, never a pattern; still, keep
        # the message to the command so nothing file-derived reaches the log.
        raise CheckError(f"`git {args[0]}` failed (exit {proc.returncode})")
    return proc.stdout


def _scan_text(text: str, patterns: Sequence["re.Pattern[str]"], where: str) -> Iterator[Match]:
    for number, line in enumerate(text.splitlines(), start=1):
        for index, pattern in enumerate(patterns, start=1):
            if pattern.search(line):
                yield (f"{where}:{number}", index)


def _path_hits(name: str, patterns: Sequence["re.Pattern[str]"]) -> List[int]:
    return [index for index, pattern in enumerate(patterns, start=1) if pattern.search(name)]


def _utf16(data: bytes) -> Optional[str]:
    """`data` decoded as UTF-16 when it plausibly is, else None.

    A UTF-16 file is mostly NUL bytes to a UTF-8 reader, so every ASCII word
    in it arrives with a NUL between each letter and no pattern matches it.
    A BOM decides; without one, a NUL in a quarter of either byte column does.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    quarter = max(1, len(data) // 4)
    if data[1::2].count(0) >= quarter:
        return data.decode("utf-16-le", "replace")
    if data[0::2].count(0) >= quarter:
        return data.decode("utf-16-be", "replace")
    return None


def _texts(data: bytes) -> List[str]:
    """Every reading of `data` worth scanning: UTF-8, plus UTF-16 when the
    bytes carry NULs and decode as it."""
    texts = [data.decode("utf-8", "replace")]
    if b"\0" in data:
        wide = _utf16(data)
        if wide is not None:
            texts.append(wide)
    return texts


def _scan_bytes(data: bytes, patterns: Sequence["re.Pattern[str]"], where: str) -> List[Match]:
    found: List[Match] = []
    for text in _texts(data):
        for match in _scan_text(text, patterns, where):
            if match not in found:
                found.append(match)
    return found


def scan_tree(repo: Path, patterns: Sequence["re.Pattern[str]"]) -> List[Match]:
    """Every tracked file's content, as the working tree holds it.

    A file whose PATH matches is reported without its path — printing it would
    print the match — as `<tracked file #N>`, N being its 1-based position in
    `git ls-files`, so the operator can find it with `git ls-files | sed -n Np`.

    A SYMLINK (mode 120000) is scanned by its committed TARGET, read from the
    blob: the target string is published in the repository like any file's
    content, and on a checkout that writes links as links there is no file
    body to read at all.
    """
    found: List[Match] = []
    listing = _git(repo, "ls-files", "-s", "-z")
    entries = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        meta, raw_name = raw.split(b"\t", 1)
        mode, blob = meta.split()[:2]
        entries.append((mode.decode("ascii"), blob.decode("ascii"),
                        raw_name.decode("utf-8", "replace")))
    for position, (mode, blob, name) in enumerate(entries, start=1):
        path = repo / name
        hits = _path_hits(name, patterns)
        label = f"<tracked file #{position}>" if hits else name
        found.extend((f"{label}:path", index) for index in hits)
        if mode == "120000":
            found.extend(_scan_bytes(_git(repo, "cat-file", "blob", blob), patterns, label))
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.extend(_scan_bytes(path.read_bytes(), patterns, label))
    return found


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# The empty tree's object name: what a root commit is diffed against.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _commit_diff(repo: Path, sha: str) -> str:
    """The lines `sha` added relative to its FIRST parent (or to nothing).

    Not `git show`: for a merge commit it prints a combined diff, which omits
    every line that merged cleanly — and a line typed into the merge commit
    itself (an "evil merge") can hide there. Diffing against the first parent
    shows everything the merge brought onto the branch, side-branch lines
    included; those side commits are scanned on their own too.
    """
    parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).decode("ascii").split()[1:]
    return _git(repo, "diff", "--unified=0", "--no-color", "--no-ext-diff", "--text",
                "--src-prefix=a/", "--dst-prefix=b/",
                parents[0] if parents else _EMPTY_TREE, sha).decode("utf-8", "replace")


def scan_history(repo: Path, base: str, patterns: Sequence["re.Pattern[str]"]) -> List[Match]:
    """Every commit in base..HEAD: its message, its author and committer
    identity (reported as `<sha>:identity:1..4` = author name, author email,
    committer name, committer email), and the lines it added."""
    found: List[Match] = []
    commits = _git(repo, "rev-list", "--reverse", f"{base}..HEAD").decode("ascii").split()
    for sha in commits:
        short = sha[:12]
        message = _git(repo, "log", "-1", "--format=%B", sha).decode("utf-8", "replace")
        found.extend(_scan_text(message, patterns, f"{short}:message"))
        identity = _git(repo, "log", "-1", "--format=%an%n%ae%n%cn%n%ce", sha)
        found.extend(_scan_text(identity.decode("utf-8", "replace"), patterns,
                                f"{short}:identity"))
        diff = _commit_diff(repo, sha)
        current = None
        line_no = 0
        for line in diff.splitlines():
            if line.startswith("+++ "):
                target = line[4:]
                current = target[2:] if target.startswith("b/") else None
                if current is not None:
                    hits = _path_hits(current, patterns)
                    if hits:
                        # Same rule as scan_tree: a matching path is withheld.
                        current = "<path withheld>"
                        found.extend((f"{short}:{current}:path", index) for index in hits)
                continue
            hunk = _HUNK_RE.match(line)
            if hunk:
                line_no = int(hunk.group(1))
                continue
            if line.startswith("+") and current is not None:
                for _, index in _scan_text(line[1:], patterns, ""):
                    found.append((f"{short}:{current}:{line_no}", index))
                line_no += 1
    return found


def scan_branch_name(environ: Mapping[str, str],
                     patterns: Sequence["re.Pattern[str]"]) -> List[Match]:
    """The branch being built, when CI names it: a branch name is public on
    the pull request and in every run's title."""
    name = environ.get("GITHUB_HEAD_REF") or environ.get("GITHUB_REF_NAME") or ""
    return [("branch-name", index) for index in _path_hits(name, patterns)] if name else []


def main(argv: Optional[Sequence[str]] = None,
         environ: Optional[Mapping[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", default=".", help="repository to scan (default: .)")
    parser.add_argument("--base", metavar="REF",
                        help="also scan every commit in REF..HEAD (messages and added lines)")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ

    try:
        patterns = load_patterns(environ)
    except CheckError as exc:
        print(f"ERROR: privacy denylist: {exc}", file=sys.stderr)
        return 2
    if patterns is None:
        if environ.get("CI", "").lower() == "true":
            print(
                f"ERROR: no privacy denylist configured ({ENV_PATTERNS} / {ENV_FILE} "
                "unset or empty). Failing closed: in CI a privacy check that scanned "
                f"for nothing must not pass. Set the {ENV_PATTERNS} repository secret.",
                file=sys.stderr,
            )
            return 2
        print(
            f"WARNING: no privacy denylist configured ({ENV_PATTERNS} / {ENV_FILE}); "
            "nothing was scanned. CI runs this check with the repository secret.",
            file=sys.stderr,
        )
        return 0

    repo = Path(args.repo).resolve()
    try:
        found = scan_branch_name(environ, patterns) + scan_tree(repo, patterns)
        if args.base:
            found.extend(scan_history(repo, args.base, patterns))
    except CheckError as exc:
        print(f"ERROR: privacy denylist: {exc}", file=sys.stderr)
        return 2

    if found:
        print(f"FAILED: {len(found)} denylisted match(es). Matched text is not shown; "
              "the pattern number is the line of the denylist that fired.")
        for where, index in found:
            print(f"  {where}: pattern #{index}")
        return 1
    scope = f"tracked files and commits since {args.base}" if args.base else "tracked files"
    print(f"OK: no denylisted terms in {scope} ({len(patterns)} pattern(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
