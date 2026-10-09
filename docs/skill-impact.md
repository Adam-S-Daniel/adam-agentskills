# skill-impact.md — the registry's skill-change audit trail

Every change to a skill hosted in this registry (the `adam-anything-anywhere`,
`adam-coding-anywhere`, `adam-coding-local` and `adam-non-coding-local`
plugins; before 2026-09-24, the `adam`, `adam-local` and `fastmail` bundles of
the retired `agentskills` registry) gets an entry here — creations, edits, renames, removals,
and **rejected proposals**. The rejected ones are the reason the file exists:
git history records what landed, but nothing records what was tried and turned
down, so the next session re-derives and re-proposes it. An approach already
ruled out is the expensive thing to lose. (Convention defined in skills-evals'
`DESIGN.md`, "Scaling to the registry"; the underlying evidence — a proposal
audit trail is what stops failed abstractions being re-proposed — is
WikiSkill, arXiv:2608.27454.)

What this file is NOT for: harness, hook, CI, lock or docs changes — only
skill content. Entries append in the same PR as the change, newest first.

## Entry format

```
## YYYY-MM-DD — <plugin>/<skill> — <create|edit|rename|remove|rejected>
- Motivation: one line — the incident, pattern, or issue that prompted it
- Change: one line — what changed (PR #NNN)
- Eval: the skill's eval result (exit code + counts), or "none — no eval
  exists yet", or "exempt (DESIGN.md non-coverage table)"
- Outcome: merged YYYY-MM-DD, or rejected YYYY-MM-DD — one line why. The
  full proposal survives in the closed PR; link it rather than pasting it.
```

Rules:

- **A rejected proposal is the highest-value entry.** Record it even when it
  feels like noise — especially then.
- **Append-only.** A wrong entry gets a correcting entry, not an edit.
- **This repo is public and scanned.** Nothing sensitive in an entry, ever;
  a sensitive rejection is recorded by PR link alone.

Entries before 2026-08-25 predate this file and live only in git history —
no backfill is planned; the file adds the fields git does not capture.

---


## 2026-10-09 — adam-coding-anywhere/github-actions-repo-settings — edit
- Motivation: [issue #62](https://github.com/Adam-S-Daniel/adam-agentskills/issues/62) — the skill's engine copy was 663 lines against repo-settings' 1,771 and had drifted with nothing to notice.
- Change: the engine copy and its assets (schema, example fleet config, fan-out workflow) removed; SKILL.md points at `Adam-S-Daniel/repo-settings`, adds the Actions event policy triggers and facts; ADR 0019; bundle `adam-coding-anywhere` 1.2.5 -> 1.2.6 (PR pending).
- Eval: pending — drift-diagnosis run reported in the PR; onboarding is a draft fixture with no agent arm.
- Outcome: pending review and merge.

## 2026-10-05 — adam-coding-local/launch-top-level-claude-session — edit

- Motivation: Windows session 0 cannot activate the Windows Terminal Store alias, so launchers inherited from an S4U session keeper failed before opening a tab.
- Change: session-0 launches use a current-user Interactive/Limited one-off task with the prepared command line, check for a logged-on interactive session, and unregister after startup or failure. Prompt text becomes a temporary prompt-file handoff; dry runs print a task preview and register nothing. Ordinary interactive-session launches retain their direct path.
- Eval: none — exempt/deferred in skills-evals' `DESIGN.md` deliberate non-coverage table (machine-bound launch surface). Deterministic pytest launch and task-lifecycle tests are the regression gate; no paid evaluation was run.
- Outcome: local implementation pending independent review and merge.

## 2026-10-05 — adam-coding-local/launch-top-level-claude-session, adam-coding-local/sync-cc-settings-between-wsl-and-windows, adam-non-coding-local/add-from-address, adam-non-coding-local/add-received-from-addresses — edit

- Motivation: BOM-less UTF-8 typographic dashes made a valid launcher fail to parse in Windows PowerShell 5.1, which reads such files as the ANSI codepage.
- Change: all registry PowerShell scripts use ASCII punctuation; a registry byte regression test rejects non-ASCII `.ps1` content without a UTF-8 BOM. The two email triggers and settings script change comments/status punctuation only, preserving behavior.
- Eval: none — launcher and settings sync are exempt/deferred in skills-evals' `DESIGN.md` deliberate non-coverage table; the two email triggers belong to the deferred Fastmail bundle. No paid evaluation was run. The byte gate was observed failing on the four original BOM-less scripts before normalization.
- Outcome: local implementation pending independent review and merge.

## 2026-10-05 — adam-coding-anywhere/github-actions-repo-settings, adam-coding-anywhere/skills-doctor, adam-coding-anywhere/debug-github-workflows, adam-coding-local/windows-elevation-from-wsl — edit

- Motivation: prompt-audit sweep 2026-10-05, [issue #50](https://github.com/Adam-S-Daniel/adam-agentskills/issues/50): stale pointers to fleet guidance, history narrative in SKILL.md bodies, an inconsistent signal count in skills-doctor, a hard-coded `gh` pin, and an emphatic "CRITICAL" lead-in.
- Change: github-actions-repo-settings names the fleet guidance's "Pinning GitHub Actions" section instead of AGENTS.md and moves its dated history to PURPOSE.md; skills-doctor states the multi-repo shadow-guard rule and moves three incident/measurement stories to PURPOSE.md, and its signal count now reads seven; debug-github-workflows checks `command -v gh` and defers to the official install instructions (newest release at least 7 days old) instead of a pinned tarball, and drops "CRITICAL"; windows-elevation-from-wsl drops the claim that the fleet guidance carries a pointer at it.
- Eval: not run. Skills-evals fixtures exist (`evals/debug-github-workflows`, `evals/github-actions-repo-settings`, `evals/skills-doctor`, `evals/windows-elevation-from-wsl`) and were not run by the laptop issue worker; the owner runs them before merge (touch gate outstanding).
- Outcome: pending review and merge.

## 2026-10-04 — adam-non-coding-local/rename-pdfs — edit (correction)

- Motivation: a prompt-audit triage of the local rename-pdfs evals (3 of 6 `with_skill` trials, qualify t2 and t3 and pr45-n3 t1, fell back to the scan filename for the statement date and scored date priority 1; the other three scored 8, 5 and 5) traced part of the date miss to the helper: `scripts/extract_pdf_context.py` had no day-first month-name pattern, so "1 Jan 2026" produced an empty `dates_in_text` and the priority list fell through to the filename.
- Change: the helper now reads `D Mon YYYY` and `D Month YYYY` (leading zero, ordinal suffix, trailing period or comma accepted); numeric day/month forms stay US-only because they are ambiguous. [rename-pdfs](../plugins/adam-non-coding-local/skills/rename-pdfs/SKILL.md) lists the new format in its date priority and says a statement dated only by its billing period takes the range form, even for one month. Bundle `adam-non-coding-local` 1.0.1 -> 1.0.2.
- Eval: outstanding. The skills-evals `evals/rename-pdfs` fixture was not re-run for this change (touch gate); the helper change is covered by unit tests, and the fixture's headless-confirmation confound still caps the objective score for `with_skill` regardless of this edit.
- Outcome: pending independent review and merge.

## 2026-10-04 — adam-coding-anywhere/skills-doctor — edit

- Motivation: the built-in `/skill-doctor` (2.1.261) measures per-skill context cost and usage better than this skill's estimate, and the one-letter name difference made the description's "always-on context cost" a trigger hazard ([#13](https://github.com/Adam-S-Daniel/adam-agentskills/issues/13)); a `/doctor prompt-audit` run also found old-registry issue references and a measured `adam` bundle figure for a bundle that no longer exists.
- Change: the description no longer claims context cost, the Context cost section points at `claude -p /skill-doctor` (machine-local, not under `--bare` or Remote Control) and keeps only the delivery consequence, the report no longer closes with a context-cost figure, and three old-registry issue references (#85, agentskills#122, #157) are removed from the body. `adam-coding-anywhere` bumped 1.2.3 -> 1.2.4 (one bump covers the three entries). The 2026-10-04 entry below recorded the scope as not narrowed; this narrows it.
- Eval: none run. A skills-evals fixture exists (`evals/skills-doctor/`), so the touch gate's eval is outstanding. The edit narrows scope and deletes text; it adds no instruction a fixture would need to measure.
- Outcome: pending review and merge.

## 2026-10-04 — adam-coding-anywhere/github-actions-repo-settings — edit (correction)

- Motivation: the skill said the cms-platform settings-as-code work was "landing via PR #168" and listed the Actions-permissions settings as a "known gap to close"; a `/doctor prompt-audit` flagged it, and the PR had merged 2026-07-13 with `actions_permissions` already in the platform's `repo-settings.yml`.
- Change: the section states the platform manages `sha_pinning_required` and the fork-PR `approval_policy`, with the 422 skip on private repos. Add on-touch `PURPOSE.md`.
- Eval: none run; no skills-evals fixture exists for this skill.
- Outcome: pending review and merge.

## 2026-10-04 — adam-coding-anywhere/debug-github-workflows — edit

- Motivation: the install block pinned `gh` v2.67.0, many releases old; a `/doctor prompt-audit` proposed resolving `releases/latest` at run time, which this registry declines (fleet rule: exact pins, 7-day cooldown).
- Change: the pin moves by hand to v2.101.0, the newest release older than 7 days (published 2026-09-15; tarball layout checked). Add on-touch `PURPOSE.md`. The "**CRITICAL**" lead-in stays: it carries this skill's central point and its reason follows in the same paragraph.
- Eval: none run; no skills-evals fixture exists for this skill.
- Outcome: pending review and merge.

## 2026-10-04 — adam-non-coding-local/ocr-pdfs — edit (correction)

- Motivation: review of [the missing-helper correction](https://github.com/Adam-S-Daniel/adam-agentskills/issues/26) exposed two census false positives: a repository test filename passed to a runner and a script named in a pipeline diagram.
- Change: the [conformance census](../scripts/check_skills.py) now requires shell AST invocation positions for bare scripts, preserving the original multiline OCR invocation checks and existing qualified-payload rules. Non-invoked bare scripts remain in the dismissal audit. The existing `adam-non-coding-local` 1.0.0 -> 1.0.1 bump covers this correction and the related rename wording below.
- Eval: OCR remains exempt under [skills-evals' deliberate non-coverage table](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md#deliberate-non-coverage). Focused census pytest exited 0 with 111 passed. Six isolated AST mutations each produced pytest exit 1, with failure counts 19, 17, 2, 36, 3, and 1 across 37 selected cases, covering invocation evidence, the token-position gate, PowerShell continuations, the missing-parser dependency, repository-path exclusion, and qualified-argument preservation. The four-registry census exited 0 with 36 skills, 0 findings, and 0 waived (20 local, 14 platform, 1 site, 1 private), using current remote main archives of sibling registries. Source SHA256 hashes stayed unchanged before and after verification; sentinel calls remained zero. No paid evaluation ran.
- Outcome: pending independent review and merge. This corrects the earlier same-day census precision claim without changing that entry.

## 2026-10-04 — adam-non-coding-local/rename-pdfs — edit

- Motivation: the [OCR correction](https://github.com/Adam-S-Daniel/adam-agentskills/issues/26) changed output handling, but the rename instructions still attributed legacy `-needsocr.pdf` backups to the current OCR skill.
- Change: [rename-pdfs](../plugins/adam-non-coding-local/skills/rename-pdfs/SKILL.md) keeps the legacy paired-backup exclusion and describes it as an existing convention. It links the current OCR instructions, which preserve originals and write to a separate output folder; its [purpose note](../plugins/adam-non-coding-local/skills/rename-pdfs/PURPOSE.md) explains why the legacy pairing remains protected. The same 1.0.1 bundle bump covers both skills.
- Eval: local `scripts/local_eval.py` run of [`evals/rename-pdfs`](https://github.com/Adam-S-Daniel/skills-evals/tree/main/evals/rename-pdfs) in skills-evals at c1bf71c with `--registry adam-agentskills=<this tree at af35c83>`, 3 trials per arm, judged (a local exhibit, not badge input; Claude Code 2.1.289, agent model claude-sonnet-5, judge claude-opus-4-8), exit 0, 0 errored trials; a 1-trial unjudged pre-run also exited 0. `with_skill`: objective 4/8 in every trial, judge 3.77 (2.5-4.6, n=3). `without_skill`: objective 4/8 in every trial, judge 4.37 (4.1-4.6, n=3). Both arms passed the same four checks (image-only-scan-left-alone, inbox-content-preserved, nothing-outside-inbox, receipt-left-alone) and failed the same four in all trials (duplicate-bills-disambiguated, inbox-renamed-per-convention, invoice-dated-from-body, statement-date-ranged), so the objective score did not separate the arms; the judge means are nominally lower with the skill (date priority 3.67 vs 4.67, convention fidelity 2.67 vs 3.00, restraint 6.67 vs 7.33) with overlapping ranges at n=3. This run shows no measured benefit from the skill on this fixture and does not isolate this edit, which touches only the legacy-backup wording; the fixture's header names a headless-invocation confound (the skill's per-file confirmation step has no user to answer it), so a skill-faithful arm may propose renames without applying them.
- Outcome: skill eval gate recorded above (no measured difference between arms); pending independent review and merge.

## 2026-10-04 — adam-non-coding-local/ocr-pdfs — edit

- Motivation: [the carried-over missing-script defect](https://github.com/Adam-S-Daniel/adam-agentskills/issues/26), originally old-registry issue 189, promised a batch runner and a WPF reviewer that were never shipped. Neither script was recoverable from either registry's current files or git history.
- Change: describe direct OCRmyPDF calls into separate outputs, preserve originals, record per-file outcomes, and review appearance and OCR text with installed tools. Add on-touch `PURPOSE.md`; bump `adam-non-coding-local` 1.0.0 -> 1.0.1. Extend the Markdown-AST-based census to detect bare script filenames in code blocks, including the PowerShell relative invocation form, without gating prose, external paths, or document artifacts.
- Eval: exempt under [skills-evals' deliberate non-coverage table](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md#deliberate-non-coverage) for machine-bound surfaces; no paid evaluation run. Census regressions cover missing and existing local helpers plus the precision boundaries.
- Outcome: pending independent review and merge. No account-store or installation changes made.

## 2026-10-04 — adam-coding-local/migrate-claude-memory — edit

- Motivation: nine Claude Code releases touched auto-memory files, index limits and project directories ([#14](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14)); a re-check found three that bear on this skill's path and `autoMemoryDirectory` text.
- Change: `autoMemoryDirectory` section notes that 2.1.273 stops loading a repository-chosen memory directory under `permissions.blockReadsOutsideWorkingDirectories` (in-repo exemption unmeasured); the lossy-decoding section notes long paths before 2.1.224 could share a session directory and that `CLAUDE_CODE_PROJECT_DIR_NAME` (2.1.234) yields names the decoder cannot read, which the inventory reports `ORPHANED` if they start with `-`. Decoder and scripts unchanged. `adam-coding-local` bumped 2.0.1 -> 2.0.2.
- Eval: exempt (DESIGN.md non-coverage table, `migrate-claude-memory` is machine-bound). Focused pytest on the skill: see the PR body.
- Outcome: pending merge.

## 2026-10-04 — adam-coding-anywhere/review-bash-ci-reliability — edit

- Motivation: the checklist had the process-substitution case (`set -e` misses a failure inside `<(...)`) but not its opposite, an expected non-zero inside `$(...)` aborting the script; `_agent-guidance`'s `test/run-tests.sh` died mid-file with no `Results:` line for this reason ([#20](https://github.com/Adam-S-Daniel/adam-agentskills/issues/20)).
- Change: new checklist item 7 (`grep` with no match, `diff`, `cmp` inside `$(...)` under `set -euo pipefail`; guard with `|| true` only where no result is legitimate; a test-harness helper must never abort the run), a matching line in "How to Use", and a new `PURPOSE.md`. `adam-coding-anywhere` bumped 1.2.2 -> 1.2.3.
- Eval: local `scripts/local_eval.py` run in skills-evals at c1bf71c with `--registry adam-agentskills=<this tree at b79ee1f>`, 3 trials per arm, judged (a local exhibit, not badge input; Claude Code 2.1.289, agent and judge models from the fixture pins), exit 0, 0 errored trials. `with_skill`: objective 8/11 in every trial, judge 6.05 (n=2: one judge call errored). `without_skill`: objective mean 5.67/11 (5-6), judge 4.95 (n=2: one judge call errored). `with_skill` passed commit-signing-safe-for-ci (3/3 vs 0/3), decoy-optional-cleanup-untouched (3/3 vs 0/3) and process-substitution-error-propagates (3/3 vs 2/3). Judge Restraint 7.5 vs 2.0. In the single-trial unjudged pre-run (same configuration) `with_skill` failed commit-signing-safe-for-ci and passed grep-q-avoids-broken-pipe, so per-check differences at this n may be run variance. In all 3 trials of the judged run both arms failed grep-q-avoids-broken-pipe, jq-guaranteed-or-replaced and version-read-does-not-depend-on-unguarded-jq. A seed case for item 7 belongs in that fixture, in that repo.
- Outcome: pending merge.

## 2026-10-04 — adam-coding-anywhere/skills-doctor — edit

- Motivation: three Claude Code changes touched this skill's subject ([#9](https://github.com/Adam-S-Daniel/adam-agentskills/issues/9), [#13](https://github.com/Adam-S-Daniel/adam-agentskills/issues/13), [#25](https://github.com/Adam-S-Daniel/adam-agentskills/issues/25)): synced skills are shown by short name and `anthropic-skills`/`claude-ai` became reserved namespaces (2.1.228-2.1.282), a built-in `/skill-doctor` appeared one letter away from this skill (2.1.261), and `digest_skill_dir` followed a symlinked skill root that the bootstrap hook's `digest_dir` refuses.
- Change: `digest_skill_dir` returns None (reported as unmeasurable) for a symlinked skill directory or any symlink inside it, mirroring the hook (ADR 0008, ADR 0012), with regression tests; SKILL.md states how synced skills are named and shown, the reserved namespaces, the pre-2.1.280 `manifest.json` trash bug, and that `/skill-doctor` is a different tool. The skill's scope is not narrowed. `adam-coding-anywhere` bumped 1.2.2 -> 1.2.3 (one bump covers both entries).
- Eval: local `scripts/local_eval.py` run of `evals/skills-doctor/bucketed-account-store` in skills-evals at c1bf71c with `--registry adam-agentskills=<this tree at b79ee1f>`, 3 trials per arm, judged (local exhibit, not badge input; Claude Code 2.1.289), exit 0, 0 errored trials. `with_skill`: objective 4/5 in every trial, judge 9.6 (n=2: one judge call errored; which-copy-the-model-reads 9.5). `without_skill`: objective mean 4.33/5 (4-5), judge 7.4 (n=3; which-copy-the-model-reads 3.0). Both arms mostly failed the-account-store-was-read-not-called-empty (0/3 vs 1/3). Pytest on the script: see the PR body.
- Outcome: pending merge.

## 2026-10-04 — adam-coding-local/migrate-claude-memory — edit

- Motivation: the portability review of [PR #41](https://github.com/Adam-S-Daniel/adam-agentskills/pull/41) found hidden subprocess diagnostics and a native Windows runtime outside the decoder's POSIX path model; the log did not establish a script stderr cause.
- Change: reject native Windows Git Bash/MSYS, Cygwin, and win32 Bash with a fixed reason and exit 3 before HOME access; document Linux/WSL native POSIX support; preserve subprocess diagnostics and probe runtime, invalid-byte filename, and permission capabilities without changing decoding.
- Eval: exempt ([skills-evals DESIGN.md deliberate non-coverage table](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md)); `/tmp/c3-venv/bin/python -m pytest plugins/adam-coding-local/skills/migrate-claude-memory/tests/test_memory_inventory.py -q --basetemp=/tmp/c3r3-implementation-fixtures` exited 0 with 67 passed and 0 skipped on Linux. Twelve isolated mutation/behavior proofs produced pytest exit 1 for all 33 selected cases (32 failures and one Linux runtime-probe setup error), with 0 passed and 0 skipped; these cover all newly added tests, invalid UTF-8 handling, and the existing alias/ambiguity/mount/suffix safety checks. Worktree hashes stayed unchanged, and the script/test copies and fixtures were deleted. No paid model eval was run for this repair.
- Outcome: pending corrective review in [PR #41](https://github.com/Adam-S-Daniel/adam-agentskills/pull/41).

## 2026-10-04 — adam-coding-local/migrate-claude-memory — edit

- Motivation: corrective review of the [issue #14 decoder follow-up](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14) found false orphan labels for underscore, space, and Unicode aliases; a further review found reached prefixes with undecodable suffixes were also ignored.
- Change: enumerate and normalize every existing entry at every level using ASCII-alphanumeric UTF-16 munging, including supplementary characters and consecutive punctuation. Preserve uncertainty for any matching branch that cannot be examined or completed. Known removable roots and detected device boundaries remain unresolved, including populated mounts; document same-device mount, custom-name, long-name, and concurrent-change limits. Temporary-home tests isolate host ancestor listings and device IDs while checking every real entry inside each fixture. The existing 2.0.1 version bump remains sufficient relative to the main branch.
- Eval: exempt ([skills-evals DESIGN.md deliberate non-coverage table](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md)); focused pytest: exit 0, 50 passed. Sixteen isolated script mutations produced exit 1 for 37 failing test executions covering all 31 added cases and four corrected existing cases, with the two supplementary-character cases repeated under a second mutation. No paid evaluation run.
- Outcome: pending independent review and merge; the broader issue #14 remains open. This entry corrects the earlier same-day dotted-alias-only claim below.

## 2026-10-04 — adam-coding-local/migrate-claude-memory — edit

- Motivation: [issue #14](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14) tracks naming changes that prompted this scoped follow-up: failed path decoding was labeled `ORPHANED` and the cleanup instructions suggest deleting those stores.
- Change: inventory distinguishes `EXISTING`, supported missing `ORPHANED` paths, and `UNRESOLVED` paths; ambiguity, dotted aliases, inaccessible parents, and symlinks cannot become orphan claims. Instructions prohibit deletion based on unresolved status, and maintenance context and temporary-home regression tests accompany the change. `adam-coding-local` bumped 2.0.0 -> 2.0.1.
- Eval: exempt ([skills-evals DESIGN.md deliberate non-coverage table](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md)); focused pytest: exit 0, 19 passed. No paid evaluation run.
- Outcome: pending merge; broader issue #14 work remains outside this change.

## 2026-10-02 — adam-coding-anywhere/github-actions-repo-settings — edit

- Motivation: the fleet branch-naming standard (repo-settings [ADR 0007](https://github.com/Adam-S-Daniel/repo-settings/blob/persistent-branch-standard/docs/decisions/0007-persistent-branches-use-the-persistent-prefix-and-a-deletion-ruleset.md), PR [#62](https://github.com/Adam-S-Daniel/repo-settings/pull/62)) names persistent results branches `persistent/<purpose>`, and the skill's bot-write policy text still pointed at an unprefixed results branch.
- Change: the schema and the example fleet config say a branch meant to persist across PRs is `persistent/<purpose>` and is protected by a deletion-only `persistent/**` ruleset declared per repo as `extra_rulesets`; the example now names skills-evals' branch `persistent/eval-results`. The skill's engine copy does not implement `extra_rulesets`, so the text points at the fleet manifest rather than claiming support. `adam-coding-anywhere` bumped 1.2.1 -> 1.2.2.
- Eval: none — documentation text in an asset; no eval exists for this skill.
- Outcome: pending merge.

## 2026-09-30 — adam-coding-anywhere/skills-doctor — edit

- Motivation: Codex Cloud support made the bootstrap hook's destination and exit status mode-dependent, exposing two skills-doctor tests that inferred behavior from shell source lines.
- Change: [The tests](../plugins/adam-coding-anywhere/skills/skills-doctor/scripts/test_check_provenance.py) now exercise durable-session behavior and the doctor's duplicate precedence; [the hook integration test](../scripts/test_generate_skills_lock.py) checks that duplicate rows win over a project-owned collision. The bundle version moves from 1.2.0 to 1.2.1 so plugin update can deliver the changed test content. Skill instructions are unchanged.
- Eval: none — test-only edit; focused pytest passed 11/11, and the canonical suite passed all changed-code tests (two unrelated login-shell fixture failures reproduced from an origin/main archive).
- Outcome: pending merge.

## 2026-09-28 — adam-coding-anywhere/skills-doctor — remove (`--account-drift`)

- Motivation: `--account-drift` consumed skills-evals' Tier-3 artifact (`propagation/account/latest.json` on `eval-results`), whose publisher [skills-evals#211](https://github.com/Adam-S-Daniel/skills-evals/pull/211) retired, so the flag read an artifact nobody publishes; ADR 0014 already retired the uploads it audited.
- Change: `--account-drift`, `account_drift`, `DriftReport` and `registry_copy` removed with their tests; SKILL.md and PURPOSE.md say the mode is gone and the shadow remedy is to remove a stale account copy; `--account-channel` and the shadow checks stay; `adam-coding-anywhere` bumped 1.1.1 -> 1.2.0. ([#35](https://github.com/Adam-S-Daniel/adam-agentskills/pull/35))
- Eval: none — removal of a report mode; pytest covers the remaining paths.
- Outcome: pending merge.

## 2026-09-28 — adam-coding-anywhere/skills-doctor — edit

- Motivation: its uploader-binding tests imported the retired sync-skills (`_uploader()` in `scripts/test_check_provenance.py`), which broke in a flat checkout once `plugins/adam-coding-local/skills/sync-skills/` was deleted.
- Change: the three binding tests and the `_uploader()` helper removed; `UPLOAD_SKIP_DIRS`/`UPLOAD_SKIP_DIR_PREFIXES`/`UPLOAD_SKIP_EXTS` frozen at the retired uploader's values with a new pinning test; comments and report wording updated to say the constants mirror the retired uploader (ADR 0014) rather than a live one; `adam-coding-anywhere` bumped 1.1.0 -> 1.1.1. ([#34](https://github.com/Adam-S-Daniel/adam-agentskills/pull/34))
- Eval: none — test and wording change only.
- Outcome: pending merge.

## 2026-09-28 — adam-coding-local/sync-skills — remove

- Motivation: [issue #23](https://github.com/Adam-S-Daniel/adam-agentskills/issues/23) step 2 — the claude.ai ZIP-upload channel is replaced by the repo-synced plugin channel (ADR 0013, E6), confirmed on every surface including local Cowork.
- Change: skill deleted (`sync-skills/`, `account-skills.txt`, `scripts/account_zip_selection.py`, both account-store workflows, `account-state.json`, the ADR 0006 drift loop); `adam-coding-local` bumped 1.1.0 -> 2.0.0. New [ADR 0014](decisions/0014-retire-the-account-zip-upload-channel.md). ([#34](https://github.com/Adam-S-Daniel/adam-agentskills/pull/34))
- Eval: exempt — removal.
- Outcome: pending merge.

## 2026-09-28 — adam-coding-anywhere/vendor-release-impact-issues — create (eval result; corrects the 2026-09-25 entry)

- Motivation: the 2026-09-25 entry says "Eval: none run yet" and "Outcome: pending merge"; both are now stale, and the log is append-only.
- Change: none to skill content. The owner chose to merge before the eval, because the paid run reads the skill from the registry's default branch ([#22](https://github.com/Adam-S-Daniel/adam-agentskills/pull/22) merged 2026-09-26, then the eval ran).
- Eval: [run 36263643613](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/36263643613), exit 0 ([report](https://github.com/Adam-S-Daniel/skills-evals/blob/persistent/eval-results/results/vendor-release-impact-issues/20260926T184529Z/report.md)). `with_skill`: objective 6/7, judge 7.2 (Correctness 5, Hygiene 9, Restraint 10). `without_skill`: objective 4/7, judge 5.4. The skill fixed the upstream-backlink and title-placeholder failures. Both arms failed the publish-time check; the check itself was at fault (it required all three releases' times, in the attribution form only), and is fixed in [skills-evals#204](https://github.com/Adam-S-Daniel/skills-evals/pull/204). The rubric now scores coverage. The `with_skill` arm filed 2 of the 5 planted findings.
- Outcome: merged 2026-09-26.

## 2026-09-25 — adam-coding-anywhere/vendor-release-impact-issues — create

- Motivation: filing 32 vendor-release impact issues hit every trap the skill names ([_agent-guidance#171](https://github.com/Adam-S-Daniel/_agent-guidance/pull/171)).
- Change: new skill (SKILL.md + PURPOSE.md); `adam-coding-anywhere` 1.1.0 ([#22](https://github.com/Adam-S-Daniel/adam-agentskills/pull/22)).
- Eval: none run yet. Fixture `evals/vendor-release-impact-issues/` is in [skills-evals#197](https://github.com/Adam-S-Daniel/skills-evals/pull/197); objective-only scoring passes 7/7 on a workspace that follows the skill and fails 6/6 trap checks on one that doesn't. The graduation gate's green `with_skill` arm needs a paid run, or the owner's waiver recorded here, before merge.
- Outcome: pending merge.

## 2026-09-25 — adam-coding-local/launch-top-level-claude-session — edit, rename (was `launch-wsl-claude-session`)

- Motivation: a "new top-level session" launched from Claude's own shell tool
  inherited `CLAUDE_CODE_CHILD_SESSION=1` and was classified as nested —
  excluded from `--resume`, history and `claude agents`, transcript unsaved;
  and a new `wt.exe` tab could not find bare `claude` (`0x80070002`).
- Change: every launcher (new native `scripts/launch-claude-session.ps1`,
  plus both WSL launchers) clears `CLAUDE_CODE_CHILD_SESSION` and sets
  `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` in the launched process, runs
  claude by a runtime-resolved full path, and supports a bare or named
  `--remote-control` and `--prompt-file`; SKILL.md description broadened to
  "start a new Claude Code session" requests, with a verify-via-`--resume`
  step. `adam-coding-local` bumped to 1.1.0. Renamed
  `launch-wsl-claude-session` → `launch-top-level-claude-session` (owner
  approved, 2026-09-25): the skill now launches native Windows sessions too,
  and the rename lands before the skill was ever uploaded to the claude.ai
  account store or locked by a consumer `skills.lock` (this repo's lock pins
  only the two `*-anywhere` bundles). Script file names are unchanged.
- Eval: exempt (DESIGN.md non-coverage table, `defer`, machine-bound; its
  row renamed in a companion skills-evals PR). Script behaviour is covered
  by pytest dry-run and execute-the-launched-command tests in the skill's
  `tests/`.
- Outcome: pending merge.

## 2026-09-24 — adam-coding-anywhere/skills-doctor, adam-coding-local/sync-skills, adam-coding-local/sync-cc-settings-between-wsl-and-windows — edit

- Motivation: the retired registry is being made private, so every link into
  it (issues, PRs) would 404 for a public reader (ADR 0013; adversarial
  review of the new repo).
- Change: links into the retired registry and bare `#N` references to its
  issues and PRs rewritten as plain text ("old-registry issue 157");
  `account-skills.txt` comments name the new plugins; no behaviour change.
  `adam-coding-anywhere` and `adam-coding-local` bumped to 1.0.1.
- Eval: none run — wording only.
- Outcome: pending merge.

## 2026-09-24 — all skills — rename (re-homed into `adam-agentskills`)

- Motivation: [ADR 0013](decisions/0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)
  — the old registry's history carried personal data, and plugins are now
  grouped by audience and runtime.
- Change: every skill moved, directory basename unchanged, into a fresh repo
  with no shared history. `adam` → `adam-anything-anywhere` (finding-unknowns,
  writing-adrs) and `adam-coding-anywhere` (debug-github-workflows,
  disarm-inherited-reach, github-actions-repo-settings,
  review-bash-ci-reliability, skills-doctor, workflow-path-audit);
  `adam-local` → `adam-coding-local` (sync-skills,
  sync-cc-settings-between-wsl-and-windows, launch-wsl-claude-session,
  migrate-claude-memory, windows-elevation-from-wsl) and
  `adam-non-coding-local` (ocr-pdfs, pdf-ocr-audit, rename-pdfs,
  compare-pdfpairs); `fastmail` → `adam-non-coding-local` (fastmail,
  add-from-address, add-received-from-addresses). `adam-writing-style` moved
  to the private registry's `adam-private-anything-anywhere`. No content
  change except privacy scrubs: example usernames and paths replaced with
  `<user>` placeholders, fake UUIDs in tests, a hostname removed,
  `example.com` in fastmail, and the removed school skill's name dropped from
  prose.
- Eval: none run — no behavioural content change.
- Outcome: pending merge.

## 2026-09-24 — adam-local/rename-pdfs — edit

- Motivation: example filenames contained real personal details; a public
  repo must not.
- Change: replaced them with fictional examples; no behaviour change (PR
  old-registry PR 190).
- Eval: an eval exists (`evals/rename-pdfs/` in skills-evals, issue #82,
  Class A "workspace transforms"), but it lives in a separate repo and
  could not be run from this worktree/task.
- Outcome: pending merge.

## 2026-09-24 — adam-local/launch-wsl-claude-session — edit

- Motivation: prompts containing `;` opened the session with the prompt
  truncated at the `;` plus a stray Windows Terminal tab failing with
  `0x80070002`, because `wt.exe` treats an unescaped `;` as a new-tab
  separator even inside a quoted argument.
- Change: both launchers escape `;` as `\;` for `wt.exe`; the `.ps1` also
  quotes each argument, since `Start-Process -ArgumentList <array>` does not;
  dry-run hooks plus regression tests; one SKILL.md gotcha bullet
  (old-registry PR 188).
- Eval: exempt (DESIGN.md non-coverage table) — "defer: machine-bound
  (WSL/WPF/browser surfaces)".
- Outcome: open as of 2026-09-24.

## 2026-09-24 — adam-local/sync-skills — edit

- Motivation: ADR 0012 adds `plugins/adam-personal/skills/<name>` as git
  symlinks to the bundles' skill directories; on a symlink-capable checkout
  `sync_skills.py` found `fastmail` through the link first (it sorts ahead of
  `plugins/fastmail`) and would zip and upload it through that second path.
- Change: `_skill_dir` and `get_all_skills` skip symlinked skill entries;
  script only, SKILL.md unchanged
  (old-registry PR 177).
- Eval: exempt (DESIGN.md non-coverage table) — "defer: machine-bound
  (WSL/WPF/browser surfaces)".
- Outcome: open as of 2026-09-24.

## 2026-09-23 — adam-local/sync-skills — edit

- Motivation: the SKILL.md uploaded to the claude.ai account still named the
  retiring Windows laptop in two places, the same host-specific text b2a3a5b took
  out of windows-elevation-from-wsl.
- Change: both passages say "a Windows host" instead; nothing else changes
  (old-registry PR 173).
- Eval: exempt (DESIGN.md non-coverage table) — "defer: machine-bound
  (WSL/WPF/browser surfaces)".
- Outcome: open as of 2026-09-23.

## 2026-09-23 — adam/skills-doctor — edit

- Motivation: correcting entry for the four 2026-09-18 entries below, which
  had no PR numbers because their session could not open PRs.
- Change: none. Those entries landed as
  old-registry PR 162 (bucket
  layout, sync-skills and skills-doctor) and
  old-registry PR 163 (ADR 0010,
  both skills).
- Eval: one paid A/B run of skills-evals
  `evals/skills-doctor/bucketed-account-store`,
  [run 35812851203](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/35812851203):
  with_skill 5/5 objective, judge 10.0 (claude-sonnet-5, $0.46);
  without_skill hit the 600 s agent timeout, so no delta. Further runs are
  held until the roster seats claude-opus-5-5 beside claude-sonnet-5 with a
  claude-fable-5-1 judge.
- Outcome: merged 2026-09-23 — both PRs.

## 2026-09-22 — adam-local/bell-schedule — remove

- Motivation: owner directive, 2026-09-22 ("I want to remove [the bell-schedule skill]
  altogether"). The skill had been broken since 2026-08-14 — it names two
  payload scripts, `scripts/next_break.py` and `scripts/test_next_break.py`,
  that never existed in its directory (ADR 0002).
- Change: removed from the registry (`plugins/adam-local/skills/bell-schedule/`
  deleted, its two `skills_waivers.yml` waivers retired, `account-skills.txt`
  and the README table entry dropped, ADR 0011 records the decision); the
  claude.ai account copy was deleted by hand in the UI on 2026-09-23 and
  confirmed gone from the laptop's synced account manifest.
  old-registry PR #172.
- Eval: exempt (DESIGN.md non-coverage table — "skip, wall-clock/calendar-bound;
  low value to freeze").
- Outcome: pending merge.

## 2026-09-22 — adam-local/sync-cc-settings-between-wsl-and-windows — edit

- Motivation: review old-registry issue 170
  found three data-corrupting merge bugs: arrays collapsed (`["x"]` to `"x"`,
  `[]` to `null`), `[s]kip` deleted the key from both files, and OS-bound keys
  such as `hooks` were copied across OSes without asking.
- Change: arrays survive every path; skip keeps each file's own value; a
  per-file list of command-, path- and OS-bound keys (plus
  `syncClaudeAiSkills`/`syncClaudeAiPlugins` and
  `permissions.additionalDirectories`) is never copied across;
  `permissions.ask` is unioned; `-DryRun` prints key names and markers, never
  values. SKILL.md corrected and its known limitations documented; first
  pytest tests, driven through `pwsh` (old-registry PR #171).
- Eval: exempt (DESIGN.md non-coverage table: defer, machine-bound)
- Outcome: pending merge

## 2026-09-18 — adam/skills-doctor — edit

- Motivation: terminal sessions sync the claude.ai account store from CLI
  2.1.273+, so the drifting channel reaches every surface rather than only the
  ones with no lock coverage, and ADR 0010's answer makes a laptop and a cloud
  session load different sets on purpose
  (old-registry issue 158).
- Change: new `--account-channel` mode reporting the settings-chain verdict for
  `syncClaudeAiSkills`/`syncClaudeAiPlugins` (only the boolean `false` counts
  as an opt-out; absent is reported as still syncing), every account skill
  whose bare name another copy here also delivers and which owns the short
  name, and what an opt-out left in `.trash/`. Reports only, never repairs.
  Branch `claude/own-the-terminal-skill-channel`, stacked on
  `claude/account-mirror-bucket-layout`; no PR number — see the 2026-09-18
  entries below for why.
- Eval: skills-evals `evals/skills-doctor/bucketed-account-store` (the fixture
  the entry below added). `--arm objective-only` on the pristine seed exits 1,
  1/5 checks; on a reference answer exits 0, 5/5. The A/B arms were not run —
  no model access for them in this session, so no delta is claimed.
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote.

## 2026-09-18 — adam-local/sync-skills — edit

- Motivation: the documented `CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'` refresh
  is wrong on both branches of ADR 0010 — a syncing terminal refreshes itself,
  and a converged laptop has no mirror to refresh
  (old-registry issue 158).
- Change: the verify and record steps move to a cloud session, which always has
  the mirror and cannot opt out, and say why; the same correction lands in the
  freshness error, the `--record-account-state` refusal, `--report-issue`'s
  help and the tracking-issue body it writes. The upload half still names the
  laptop, because it still needs a browser. Branch
  `claude/own-the-terminal-skill-channel`.
- Eval: exempt (DESIGN.md non-coverage table) — "defer: machine-bound
  (WSL/WPF/browser surfaces)".
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote.


## 2026-09-18 — adam/skills-doctor — edit

- Motivation: Claude Code 2.1.273+ buckets the claude.ai account store at
  `synced/<organizationUuid>_<accountUuid>/`, so `--account-drift` read the
  flat path, found nothing, and printed "holds no skills — nothing to
  compare … 0 drifted" at exit 0 over 21 skills on disk — a false clean, and
  the shadow comparison (old-registry #122) went silent the same way
  (old-registry issue 157).
- Change: `resolve_account_store()` finds the bucket and refuses rather than
  guessing on a multi-account machine; `DriftReport.blocked` keeps "could not
  run" (exit 2) distinct from "0 drifted"; SKILL.md's account-store recipe and
  a new trap bullet. Branch `claude/account-mirror-bucket-layout` — the PR
  could not be opened from the session that did the work (`POST /pulls`
  returned 403 `Resource not accessible by integration`), so no number exists
  to cite yet; append a correcting entry with it once one does.
- Eval: first fixture added — skills-evals
  `evals/skills-doctor/bucketed-account-store`, branch
  `claude/skills-doctor-first-fixture`. Objective-only both ways:
  `--arm objective-only` on the pristine seed exits 1, 1/5 checks passed (the
  restraint check, correctly); `--arm objective-only --workspace <reference
  answer>` exits 0, 5/5. The A/B arms were NOT run — this session had no model
  access for them, so no with_skill/without_skill delta is claimed.
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote,
  PR pending the permission above.

## 2026-09-18 — adam-local/sync-skills — edit

- Motivation: same bucket move
  (old-registry issue 157).
  `--verify` exited 1 naming a manifest path no current CLI has, and told the
  operator to refresh a mirror that was already there; `--record-account-state`
  read the same constant, so the account half of ADR 0006's drift loop could
  not be re-recorded from any current CLI.
- Change: `resolve_account_mirror()` finds the bucket, keeps reading a flat
  mirror when there is none, and refuses — naming the candidates — when a
  machine has several and neither `oauthAccount` nor
  `$CLAUDE_CODE_ACCOUNT_UUID` resolves one; `--record-account-state` now writes
  nothing on a refusal, because a recording taken against an unread store
  claims every declared skill was never uploaded. SKILL.md gains a "where the
  mirror is" paragraph. Branch `claude/account-mirror-bucket-layout`, PR
  pending for the reason in the entry above.
- Eval: exempt (DESIGN.md non-coverage table) — listed under "defer:
  machine-bound (WSL/WPF/browser surfaces); faking the surface costs more than
  the churn justifies today".
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote.


## 2026-09-04 — adam-local/windows-elevation-from-wsl — create

- Motivation: wsl-automation's repo-specific "PowerShell invoked from WSL is
  never elevated" lesson; _agent-guidance#112/#113 proposed promoting it to
  base.md, and _agent-guidance#114 assessed it against ADR 0002 as a skill
  (conditional on one host, loud failure mode, nothing enforces it).
- Change: new skill, adam-local 1.1.0 -> 1.2.0
  (old-registry PR 152)
- Eval: skills-evals `evals/windows-elevation-from-wsl`
  ([skills-evals#59](https://github.com/Adam-S-Daniel/skills-evals/pull/59)),
  3 trials per arm on claude-sonnet-5, run exit 0 each time — with_skill
  7/7 objective checks in all three (judge 9.4 / 9.6 / 9.4); without_skill
  6/7 in all three, every miss `exported-before-handoff` (judge 10.0 under
  the first rubric, 7.2 / 7.4 after it was capped on the export). The
  delta is the export-before-overwrite step; the baseline already stops at
  one denial and hands over an elevated-prompt line.
- Outcome: opened 2026-09-04 as old-registry #152; merge is a human step (skill
  graduation), so the merge date is not recorded here

---

## 2026-08-29 — adam/disarm-inherited-reach — create

- Motivation: a fleet incident during the guidance-centralization work
  surfaced a procedure worth packaging (per old-registry PR #144: "the procedure a fleet
  incident turned out to need").
- Change: new skill
  (old-registry PR 144)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-29

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: account-store drift was judged by timestamps, which reports
  false drift; content is the fact of the matter.
- Change: account drift became a content check, not a timestamp one
  (old-registry PR 142)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: the hosted-session OR rule was stated outside the surface
  table, where readers had already stopped reading.
- Change: the OR moved into the surface table
  (old-registry PR 141)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25


- Motivation: a fleet incident during the guidance-centralization work
  surfaced a procedure worth packaging (per old-registry PR #144: "the procedure a fleet
  incident turned out to need").
- Change: new skill
  (old-registry PR 144)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-29

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: account-store drift was judged by timestamps, which reports
  false drift; content is the fact of the matter.
- Change: account drift became a content check, not a timestamp one
  (old-registry PR 142)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: the hosted-session OR rule was stated outside the surface
  table, where readers had already stopped reading.
- Change: the OR moved into the surface table
  (old-registry PR 141)
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25
