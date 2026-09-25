# Ecosystem state (as of 2026-07-16)

- **Old-registry issue #18 is fully closed out**: the last checkbox (eval quality signal)
  shipped via skills-evals eval.yml (weekly Mon 07:00 UTC + dispatch) and the
  README badge (old-registry #43). PENDING MANUAL STEP: create a spend-capped key
  (dedicated Console workspace, ~$10/mo limit) and
  `gh secret set ANTHROPIC_API_KEY -R Adam-S-Daniel/skills-evals`, then one
  workflow_dispatch. Until then the badge shows "no data". Arms pinned to
  claude-sonnet-5 (ceiling-effect avoidance), judge claude-opus-4-8.
- **Local convergence** (`bash setup.sh --owner-machine` only; a plain run just
  links skills): setup.sh deep-merges marketplace registration
  (adam-agentskills + adam-agentskills-private, autoUpdate:true — field
  verified in the 2.1.211 binary) and the ADR 0013 `enabledPlugins` set
  (`adam-anything-anywhere`, `adam-coding-anywhere`, `adam-coding-local`
  @adam-agentskills and `adam-private-anything-anywhere@adam-agentskills-private`
  on; their `@synced` copies and the retired registry's plugins off) into ~/.claude/settings.json, idempotently.
  PENDING (2026-09-24): every durable machine re-runs
  `bash setup.sh --owner-machine` in each home after the ADR 0013 move; the
  retired marketplace entries stay registered until removed by hand.
- **adam-agentskills-private**: holds `adam-private-anything-anywhere`
  (`adam-writing-style` moved there, ADR 0013).
- **Consumers** (2026-09-24): each consumer's `skills.lock` still needs
  re-pinning to `Adam-S-Daniel/adam-agentskills` and the new plugin names;
  the old registry is archived after that.
- **Fleet**: AGENTS.md "## Skills ecosystem" section synced to all 17 repos
  across both accounts (2026-07-16, _agent-guidance#23); the original
  fleet-settings-block plan and its drift column were dropped per E1 (see
  docs/decisions/0001).
- skills-evals harness is dual-layout (glob plugins/*/skills/<skill>) and
  fully hermetic (47 tests, verified under unshare -rn); real evals are the
  only network path.
- **Account-store drift loop, both halves landed 2026-08-21:** adam-agentskills
  reads `eval-results:propagation/account/latest.json` daily (ADR 0006);
  skills-evals' `account-store-drift.yml` (#52, merged) owns the tracking
  issue's lifecycle off the same artifact and the same `freshness_verdict`.
  The middle step — the upload itself — stays manual: browser-session auth,
  no headless write path.

Cloud/ephemeral skill delivery has since moved past E1's NO-GO — see
AGENTS.md's "Skills ecosystem" section for the current (E2-derived)
`skills.lock` + `skills-bootstrap` SessionStart hook mechanism.
