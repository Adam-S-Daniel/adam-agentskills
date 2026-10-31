---
name: migrate-claude-memory
description: >
  Inventory and clean up Claude Code auto-memory stores under
  ~/.claude/projects/<munged-path>/memory/ on this machine. List decoded paths,
  file counts, sizes, and freshness; identify ORPHANED stores for human review;
  keep undecodable or ambiguous stores UNRESOLVED and never delete on that
  basis. Trigger on "clean up claude memory", "migrate claude memory",
  "inventory memory stores", "orphaned memory", or mentions of
  `~/.claude/projects`. LOCAL-ONLY: requires this machine's ~/.claude
  directory; do not invoke in a hosted/cloud session without it.
compatibility: Inventory requires native POSIX paths with Bash and GNU tools (including find, stat, and du), such as Linux or WSL; native Windows Git Bash/MSYS, Cygwin, and win32 Bash are unsupported. Requires read/write access to ~/.claude/projects on the local machine; local execution only — memory stores are machine-local and this skill cannot run in a hosted/cloud session without that directory present.
---

# Migrate Claude Memory

## Background

Claude Code's auto-memory feature stores per-project memory files under
`~/.claude/projects/<munged-absolute-path>/memory/`, where `<munged-absolute-path>`
is the project's absolute filesystem path with every non-ASCII-alphanumeric
UTF-16 code unit replaced by `-`
(e.g. `/home/user/repos/foo` becomes `-home-user-repos-foo`).

These stores are keyed **per machine**: the same project checked out at a WSL
path and a Windows path munges to two different directory names, so memory
built up on one machine is invisible on the other. Stores also become
**orphaned** when the workspace directory they refer to is later deleted or
moved — the memory files are still on disk, but nothing points to them
anymore. And because it's all under this machine's `~/.claude`, auto-memory is
**invisible to hosted/cloud Claude sessions**, which don't share your local
filesystem.

This skill does two things:

1. **Inventory** every memory store (`memory-inventory.sh`) — read-only.
2. **Help a human clean up orphans** — the script only *points at* candidates;
   it never deletes anything. You review the `ORPHANED` entries yourself and
   run `rm -rf` on the ones you're sure about. `UNRESOLVED` means the path
   could not be determined safely; never delete a store on that basis.

Repos keep no memory store and set no `autoMemoryDirectory` (cloud sessions
ignore it and it would publish notes on public repos). To keep a fact from a
store, read it and move it to the repo's `AGENTS.md` below
`## Repo-specific additions`, per STRATEGY.md's "Memory" policy.

## Workflow 1: Inventory

```
bash scripts/memory-inventory.sh          # human-readable
bash scripts/memory-inventory.sh --json   # machine-readable JSON array
```

Inventory supports native POSIX paths with Bash and GNU tools, such as Linux
or WSL. Native Windows Git Bash/MSYS, Cygwin, and win32 Bash are unsupported:
they do not provide the native POSIX path model decoded by this script. After
argument validation, these runtimes exit **3** before accessing `HOME` or
creating a temporary file, with this fixed reason on stderr and no stdout:

```text
ERROR: memory inventory requires native POSIX paths with Bash and GNU tools; native Windows Git Bash/MSYS, Cygwin, and win32 Bash are unsupported.
```

On supported runtimes, both commands then check for the local store root:

```bash
[ -d ~/.claude/projects ] || { echo "..." >&2; exit 1; }
```

For each store, it reports the munged name, path status, file count,
human-readable size, and newest file mtime, then counts stores, orphans, and
unresolved stores. **`memory-inventory.sh` never deletes or modifies anything**
— it is strictly read-only.

- `EXISTING`: one supported decoded workspace directory exists.
- `ORPHANED`: exactly one supported decoding has a missing plain
  ASCII-alphanumeric final component beneath a readable, fully examined parent
  chain. No matching candidate branch may be uncertain. The concrete path is
  reported.
- `UNRESOLVED`: the path is undecodable, ambiguous, unsupported, or cannot be
  checked safely. Multiple candidates, including a mixture of existing and
  missing paths, remain unresolved. **Do not delete a store because it is
  `UNRESOLVED`.**

JSON retains `munged`, `path`, `orphaned`, `file_count`, `total_size_bytes`,
and `newest_mtime`, and adds `status` and `unresolved`. Unresolved stores have
`path: null`, `orphaned: false`, and `unresolved: true`; orphaned stores retain
their supported decoded path.

## Workflow 2: Clean up orphans

Look at the `ORPHANED` entries from the inventory. For each one you're
confident about (i.e. you recognize the reported path and independently know
that workspace is really gone), delete the memory directory yourself:

```
rm -rf ~/.claude/projects/<munged-name>
```

This skill never runs `rm` for you — cleanup is a manual, human-reviewed step.
`UNRESOLVED` is a reason to investigate the original workspace path, never a
reason to delete the store. Do not treat a guessed path as deletion evidence.

## Known limitations: lossy path decoding

Claude Code's standard munger replaces every character outside ASCII letters
and digits with `-`, including separators, dots, underscores, spaces, and
non-ASCII characters. It operates on UTF-16 code units: `naïve` becomes
`na-ve`, while `na😀ve` becomes `na--ve`. The decoder enumerates every existing
entry, including leading-dot names, at every directory level and applies that
normalization. A normalized entry matching the remaining slug through a
hyphen boundary or its end is a candidate. A unique supported existing alias
can be `EXISTING`; multiple decodings, including existing and supported missing
final paths, are `UNRESOLVED`.

A missing final component containing hyphens, a missing ancestor, any matching
symlink (including dangling links and loops), a matching non-directory, an
inaccessible candidate or parent, or an enumeration/stat failure remains
`UNRESOLVED`. A reachable prefix whose remaining suffix cannot be decoded is
also uncertainty, even if another branch supports a missing leaf. Invalid
UTF-8 entries or an unavailable UTF-8 locale also prevent
an orphan claim. Only standard ASCII-alphanumeric/hyphen store names of at most
200 characters are supported; longer names use hashing/truncation that this
decoder does not reverse, and before Claude Code 2.1.224 long paths sharing a
sanitized prefix could resolve to one another's session directory, so a
long-named directory is not evidence of a single workspace. Custom aliases that
resemble standard names cannot be identified from the store filename alone;
`CLAUDE_CODE_PROJECT_DIR_NAME` (2.1.234 and later) is one source of them,
letting a host choose a short name for the per-project transcript directory
([v2.1.234](https://github.com/anthropics/claude-code/releases/tag/v2.1.234)).
The release note does not say whether the memory folder lives under that
directory. The inventory reports a name without a leading `-` as `UNRESOLVED`,
but it cannot tell a host-chosen name that starts with `-` from a munged path
and would report it `ORPHANED` if the decoded path is missing, so confirm every
`ORPHANED` store against the workspace it names before deleting anything.

**Known mount roots and detected device boundaries are always `UNRESOLVED`.**
This includes paths under `/mnt/<segment>` (including case-insensitive
`/mnt/c` drives), `/media`,
`/run/media`, and `/Volumes`, even when populated, and any traversed directory
whose device differs from its parent's. No privileged mount inspection is
used. This conservative policy avoids an unavailable drive's empty mount
point becoming deletion evidence. Arbitrary former mount points on the same
device as their parent cannot be detected reliably by these checks.

Classification reflects directory state observed during the scan; concurrent
filesystem or mount changes can invalidate it. The checks prevent the tested
decoding failures from becoming orphan claims, but do not identify every
deleted workspace or every
custom naming convention. Always verify an `ORPHANED` result independently
before deleting anything.

The existing inventory format assumes filenames contain no control characters
(such as newlines or tabs); its line-based file counts and size extraction,
and JSON escaping, do not support those names. Inspect such stores manually
and do not use their inventory output as cleanup evidence.
