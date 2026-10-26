# 0021. Automated skill changes merge without review

- **Status:** Accepted
- **Date:** 2026-10-26
- **Deciders:** Adam Daniel: on a decision card on 2026-10-25, that removal
  pull requests from the evals loop merge automatically with no human review,
  despite the fleet rule, with exception ADRs; and on 2026-10-26, that the
  loop merges accepted changes automatically, with no human in the loop.

## Context

Three kinds of pull request reach this registry from skills-evals' eval
loop, and all used to wait for a person:

- **A removal.** skills-evals' `scripts/retire_skill.py` acts on a `retire`
  verdict: every fixture crossed with every roster model shows no benefit at
  two fixed looks. It opens `retire-skill/<skill>`, which deletes the skill
  directory, raises the plugin's patch version and adds a
  `docs/skill-impact.md` `remove` entry
  ([skills-evals ADR 0014](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/decisions/0014-removal-pull-requests-merge-without-review.md)).
- **An improvement.** skills-evals' improvement loop proposes a `SKILL.md`
  edit, measures it against the current skill on the skill's fixtures, and
  accepts it only when train improved and validation held. Its
  `routine-improve-gate.yml` validates the result without running it and
  opens `eval-improve/<skill>`
  ([skills-evals ADR 0005](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md),
  "Automatic merge addendum (2026-10-26)").
- **A new skill.** skills-evals' draft loop drafts a skill for a recurring
  lesson and keeps it only when it beat no skill on its own fixtures. Its
  `routine-draft-skill-gate.yml` opens `draft-skill/<skill>` here, and a
  companion pull request with the fixtures in skills-evals, to merge after
  this one.

This repository's rules assume a person in between. `AGENTS.md`'s "One-way
doors get an adversarial round" requires an independent adversarial round
before a change to a shipped skill basename merges, and a removal is
irreversible in every consumer that installed the skill. "Skill changes get
recorded, and evals gate them" expects a person to write the skill-impact
entry and run the touch gate. The fleet guidance says "PR + auto-merge is not
a sanctioned bot-write path", with named exceptions only.

The eval-loop thresholds design of 2026-10-25 defaulted both to "Adam
merges". Adam chose the opposite for both.

## Decision

1. **All three merge by auto-merge, with a merge commit, once this
   repository's required checks pass.** No person reviews or approves them.
   The ruleset is unchanged: it requires no approving review, so a pull
   request that passes its checks merges without any bypass actor.
2. **This is the one exception to the adversarial round.** For a removal,
   the retirement verdict stands in for it; for an improvement or a new
   skill, the acceptance measurement does. Any other change to a shipped skill still
   gets the round.
3. **Only the skill and its bookkeeping change.** Such a pull request
   touches the one skill's own directory, `docs/skill-impact.md`, the plugin's
   two manifests (the version only), `README.md`'s generated table and
   `skills.lock`, and nothing else.
4. **Never through plugin-runtime-review.** A skill whose plugin runs code
   (a `hooks/` or `bin/` directory, a settings or package file, or a
   manifest `hooks` or `settings` key) is never merged automatically: the
   improvement and draft-skill gates open a draft instead. `retire_skill.py` does not check
   this yet, so its auto mode must not be switched on until it does. No
   automation approves a `plugin-runtime-review` deployment.
5. **One loop pull request at a time.** All three write the bundle
   version, `docs/skill-impact.md` and `skills.lock`, so a second open one
   would conflict once the first merged. While any `eval-improve/*`,
   `draft-skill/*` or `retire-skill/*` pull request is open, the
   improvement gate opens nothing and the draft-skill gate opens drafts.
6. **The record is written by the automation.** The pull request carries its
   own `docs/skill-impact.md` entry with the eval numbers, a `PURPOSE.md`
   stub when the skill has none, the version bump ADR 0009 requires, and the
   agent marker and `agent-authored` label the fleet requires of agent-made
   contributions. The loop's own measurement is the touch gate's eval run.
7. **The private registry is never changed automatically.**
   adam-agentskills-private is not a target of either path; both name this
   repository alone.
8. **Adam turns them off in skills-evals**: `improve_merge_mode: draft` in
   `evals/improve-policy.yml` (improvements and new skills),
   `retirement_mode: proposal` in `evals/gate-policy.yml` (removals).

## Consequences

- **A change can land that no person has read.** The required checks, the
  scope limits above and the eval evidence are the only gate. A bad change
  is undone by an ordinary pull request; its skill-impact entry says where
  it came from.
- **Consumers re-pin via the nightly skills-lock bump.** Each merge moves a
  bundle digest, so every consumer `skills.lock` that pins the bundle is
  stale until the nightly bump re-pins it. An improvement pull request
  re-pins this repository's own `skills.lock` in a second commit.
- **A removal is still a one-way door downstream.** A consumer that
  installed the skill loses it on its next update.
- **The repo-settings half is still owed.** The fleet rule "PR + auto-merge
  is not a sanctioned bot-write path" needs its own exception ADR in
  repo-settings before either path runs. The improvement path also needs the
  skills-evals GitHub App's secrets and this repository's `eval-improve`
  label; until then it opens nothing.
- **A pull request that fails a check stays open** and holds the one slot
  until a person fixes or closes it.

## Alternatives considered

- **Adam merges every loop pull request** (the thresholds design's default).
  Rejected by Adam on 2026-10-25 for removals and 2026-10-26 for
  improvements and new skills (the loop merges accepted changes).
- **A ruleset bypass that pushes straight to `main`.** Rejected: the fleet
  rule forbids a bot pushing to a protected default branch, and it would skip
  the required checks that are the only gate left.
