# PURPOSE: tool-powershell

Maintenance context only; never loaded at inference.

## The lesson it answers

A session miner flagged repeated PowerShell tool failures in several
repositories: runs of the same call failing four to eight times in a row with
only an exit code, and one deletion refused by the tool's safety policy on a
protected storage path. The common thread is not a missing command; it is an
agent that keeps trying after the first failure instead of reading the error or
stopping.

## What each section maps to

- Section 1 answers the opaque exit code: surface the error once, then fix its
  cause.
- Section 2 answers the long retry runs: two strikes, then change the kind of
  approach.
- Section 3 answers the protected-path refusal: report and hand over, never
  route around.
- Section 4 is the guard rail for pattern deletes, the operation most likely to
  hit that refusal.

## Triage

Not a hook: the failures are runtime outcomes no commit, push or CI check can
observe, and a tool-call guard cannot know that an earlier call failed or that
a path is protected. Overlap with windows-elevation-from-wsl is deliberate and
narrow: that skill owns the filtered-token "Access is denied" case from WSL;
this one owns everything else a PowerShell call can do wrong.

## Decisions made without a person

The skill-creator flow asks questions; none were asked. The plugin is
adam-coding-local because the skill only applies on a Windows host.
