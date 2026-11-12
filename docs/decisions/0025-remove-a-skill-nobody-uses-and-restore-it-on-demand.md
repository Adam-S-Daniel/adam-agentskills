# 0025. Remove a skill nobody uses, and restore it on demand

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** Adam Daniel (approved 2026-11-12 that usage-based removals auto-merge like retire removals)
- **Extends:** [ADR 0021](0021-automated-skill-changes-merge-without-review.md)

## Context

ADR 0021 lets skills-evals' evals loop remove a skill on a `retire` verdict (no
benefit on any roster model) by an auto-merging `retire-skill/<skill>` pull
request. That says nothing about a skill that may help when used but that nobody
uses. Every listed skill still costs context: its name and description sit in the
skill listing of every session, and the listing is re-read on every turn.

## Decision

1. **The evals loop removes an unused skill the same way it removes a retired
   one.** skills-evals' `scripts/retire_skill.py`, with reason `unused`, opens
   the same `retire-skill/<skill>` pull request: adversarial review, then
   auto-merge, within ADR 0021's limits. Only the evidence in the pull request
   differs.
2. **The formula.** Let V = $10, the value of one use; P = 119,000 prefix
   tokens; R = USD of context re-read per session per surface (cloud 3.50,
   local 0.81); d = the skill's description tokens (bytes / 4 of the
   frontmatter `name` plus `description`). A listed skill costs d * R / P per
   session. The skill is removed when the cost accrued over its zero-use
   sessions with a full listing reaches V, that is, after
   N* = V * P / (d * R) such sessions. Example: d = 100 on cloud gives
   N* = 10 * 119,000 / (100 * 3.50) = 3,400 sessions. Removal also requires an
   8-week floor, a cloud census that covers that floor, and zero uses.
3. **Exposure counts only sessions whose skill listing was full.** A truncated
   listing may have hidden the skill, so those sessions prove nothing.
4. **Exemptions.** Skills the fleet guidance tells agents to use
   (`finding-unknowns`, `adam-writing-style`, `disarm-inherited-reach`,
   `workflow-path-audit`) and cms-platform skills (there is no census of the
   `jodidaniel` owner's sessions) are listed in skills-evals'
   `evals/usage-unknown.yml` and are never removed on usage. The local-only
   plugins (`adam-coding-local`, `adam-non-coding-local`) are never removed on
   usage either; only their evals are paused.
5. **Pause before remove.** Below N*, after at least 500 full-listing sessions
   with zero uses, only the skill's evals are paused (resumed on its first use)
   and it gets no improvement pull requests.
6. **Restore.** `scripts/restore_skill.py <skill>` puts a removed skill back
   from the commit before its removal: the directory byte for byte, its
   `scripts/plugin_layout.json` entry at its old position, the regenerated
   README table, the plugin's MINOR version raised, and a `restore` entry in
   `docs/skill-impact.md` naming the removal commit. The basename is reused and
   must already be in `scripts/shipped_names.json`, which stays append-only and
   is not modified. `scripts/check_skill_gates.py` accepts a `restore` entry for
   a skill that reappears and that main records a `remove` entry for, with no new green arm, since the content already
   graduated.

## Consequences

- The thresholds live in skills-evals' `evals/removal-policy.yml`, not here;
  changing V, P, R, the floor or the pause point is a change there, and this ADR
  records only the decision and the formula's shape.
- A wrong removal costs one restore pull request. The restored skill returns in
  a MINOR release; consumers that locked the plugin follow the `skills.lock`
  re-pin the script prints.
- The one-way-door check treats a shipped name that arrives again as a reused
  name, so a restore pull request carries an `Adversarial-review: pass <link>`
  line like any other reuse.
- A removed skill that is used only on surfaces the census does not see is a
  possible false positive; the exemption list and the restore path are the
  mitigations.
