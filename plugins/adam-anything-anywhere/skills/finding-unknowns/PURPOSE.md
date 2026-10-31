# Purpose — finding-unknowns

Maintenance context only; never loaded at inference.

## What it is for

On a non-trivial task, output quality is bounded by how well the task's
ambiguities got resolved, not by coding effort. This skill runs that
clarification as a loop before, during and after implementation: the
blind-spot pass, the self-interview, a spec anchored in code, implementation
notes, and a post-hoc explainer or quiz.

## Changes

- **Output contract before a batch of whole-object writes**
  ([adam-agentskills#21](https://github.com/Adam-S-Daniel/adam-agentskills/issues/21)).
  In the 2026-09-25 vendor changelog session
  ([_agent-guidance#171](https://github.com/Adam-S-Daniel/_agent-guidance/pull/171)),
  the output contract for 32 GitHub issues changed twice after they were
  filed: first a fixed "if behavior and the changelog disagree" block, then
  release publish times beside every version. An issue update replaces the
  whole body, so each change re-sent about 80 KB of exact quotes, and each
  pass was another chance for transcription errors. The first-pass bodies were
  never read back, so a later write that silently dropped the footer went
  unnoticed until verification was added. The step under "Before
  implementation" fixes the contract up front and reads back one item before
  sending the rest.
