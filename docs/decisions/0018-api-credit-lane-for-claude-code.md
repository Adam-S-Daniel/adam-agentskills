# 0018. Delegate to an API-credit lane when weekly usage runs ahead of pace

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Adam Daniel

Builds on [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md)
(runtime changes wait for the owner) and
[ADR 0017](0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md)
(Claude-only hooks live in `hooks/claude-code.json`).

## Context

The owner has a $200 Claude Platform API credit grant each month. Each grant
expires 30 days after it arrives: the current one arrived 2026-10-08 and
expires 2026-11-07, and the next arrives 2026-11-05, so two grants overlap for
a few days each month. Unspent credit is lost at expiry.

The owner's subscription has weekly usage windows. When a week's usage runs
ahead of pace, work that Claude Code would delegate to a subagent can run as a
`claude -p` child billed to the API credit instead.

The owner's rule, in their words: **use the credit when weekly usage is more
than half elapsed and ahead of pace; at 98% or more, spend everything left.**

The owner's usage collector already writes the subscription windows and the
Platform cost report to `<Windows home>/.config/ai-usage/usage.json`, and
already signs workload identity federation (WIF) assertions with a TPM-held
key.

## Decision

**Scope: local Claude Code only.** The lane is for the owner's laptop, where
Windows and WSL Claude Code sessions run side by side. It lives in
`adam-coding-local`, which only those machines enable.

**Signal source.** The gate reads the collector's `usage.json`. It does not
call any API itself. Data older than 15 minutes, or a source not marked `ok`,
closes the lane.

**State location.** `credit_home` is `<Windows home>/.config/claude-credit/`,
shared by Windows and WSL processes. WSL resolves the Windows home once with
`cmd.exe` and `wslpath` and caches it in `~/.config/claude-credit/windows-home`.
It holds the owner's `config.json` (never in this repo; `config.example.json`
shows the shape with invented values), the ledger, the slots, a closed-until
marker and per-session state.

**The gate** (`hooks/credit-lane/gate.py`, standard library only) evaluates
every weekly window (`period_seconds == 604800`, today `seven_day` and
`seven_day_fable`):

- `elapsed = 1 - (resets_at - now) / period_seconds`, clamped to [0, 1];
- the lane is OPEN when, for any weekly window, `elapsed > 0.5` and
  `used_pct / 100 > elapsed` (ahead of pace), or `used_pct >= 98`, which sets
  `spend_all` and opens regardless of elapsed time.

Every error is a closed verdict with a reason; the gate never raises to its
caller.

**Grants.** Each grant expires on its own date. `config.json` lists known
grants; `next_grant` (`day_of_month`, `usd`, `lifetime_days`) lets the gate
assume grants it has not been told about, one each month at 00:00
America/Los_Angeles (computed from the US DST rule, since Windows Python has
no tz database). An explicit grant whose `granted` lies within 15 days of an
assumed arrival is that grant arriving late, so it replaces the assumed one;
the 2026-10-08 grant replaces the assumed 2026-10-05 one, while the assumed
2026-11-05 grant stands beside it.

**Spend.** Each lane run writes one ledger file. Spend is attributed first in,
first out: to the active grant that expires earliest and still has money.
A grant's spend is the larger of its ledger total and its share of the
Platform cost report since it was granted (the report lags; the ledger misses
spend outside the lane), with the report's daily totals attributed first in,
first out as well. `remaining_grant_usd` sums every active grant.

**Weekly allowance.** For each active grant, its balance at the start of the
current `seven_day` window is divided by `max(1, ceil(days until it expires /
7))`, and the allowance is the sum. Measuring the balance at the week's start
keeps spending during the week from shrinking that week's own allowance. The
`ceil` gives the last partial week before a grant expires everything left, but
only as a ceiling: the lane still opens only on the two conditions above.
**Leftover credit is deliberately NOT spent just because a grant is about to
expire.** While the subscription still has room, an expiring balance is let go
(owner decision, 2026-10-08), so there is no end-of-grant rule.
When the lane would open but this week's ledger spend has reached the
allowance, it stays closed with reason `weekly-allowance-used`. `spend_all`
ignores the allowance; an empty grant still closes the lane.

**When to look again.** `next_check_at` is the first moment any weekly window
reaches half elapsed, when that is the only reason the lane is closed;
otherwise 15 minutes on.

**Identity: WIF from the TPM key, child-only.** `Sign-CreditJwt.ps1` opens
the TPM key named in `wif.key_name` and signs an RS256 assertion exactly as
the collector does (kid is the RFC 7638 thumbprint, a fresh `jti` each time).
The federation rule is inference-only and bound to a dedicated workspace.
The wrapper puts `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`,
`ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID` and
`ANTHROPIC_IDENTITY_TOKEN_FILE` in the CHILD's environment only, and removes
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` and
`ANTHROPIC_PROFILE` there, since each outranks the federation variables in
Claude Code's resolution order
([WIF reference](https://platform.claude.com/docs/en/manage-claude/wif-reference)).
A background loop re-signs every 30 minutes, because each assertion is
single-use.

**Fail-closed auth check.** Before spending, the wrapper runs
`claude auth status --json` in the child's environment and proceeds only on a
signed-in, first-party, non-subscription method. The claude.ai subscription,
another provider, or anything unrecognized exits 75 without running the task.

**Two cross-OS slots.** At most `config.slots` (2) runs at once across Windows
and WSL, as `O_EXCL`-created files in `credit_home/slots/` with a heartbeat
every 60 seconds. A slot whose heartbeat is older than 3 minutes is stale and
may be taken over. Liveness is by heartbeat age only; a pid means nothing
across operating systems.

**The wrapper** (`bin/claude-credit`, on the Claude Code Bash tool's PATH)
refuses outside Claude Code (`CLAUDECODE=1`, exit 64), forces
`--output-format json`, prints the child's result text, records its cost and
prints `lane: api · $<cost> · $<left> left this week`. A billing failure
(credit exhausted, spend limit, insufficient credit, 402) writes a
closed-until marker for the next grant's arrival (or 24 hours) and exits 76.
Closed or busy exits 75. Both tell the session to use the Agent tool.

**Token cost: zero unless open, silent unless the state changes.**
`hooks/claude-code.json` adds a SessionStart hook that runs the gate once and
prints two to four lines of context only when the lane is open, and a
UserPromptSubmit hook whose fast path is pure bash: until the session's
`next_check_epoch`, it exits without starting Python. After that it re-runs
the gate and prints one line only when the lane flips open or closed.

**Claude-only placement, per ADR 0017.** The hooks are named by
`.claude-plugin/plugin.json`'s `hooks` key; the root Agent Plugins
`plugin.json` is unchanged, so Codex never loads them. The handlers carry no
`commandWindows`.

`adam-coding-local` moves from 2.1.0 to 2.2.0
([ADR 0009](0009-bump-bundle-versions-on-every-release.md)).

## Consequences

- Delegated work moves off the subscription only while a week runs hot, and
  the weekly allowance keeps one hot week from using up the whole grant. Credit
  left at expiry is lost by design when the subscription had room.
- A session pays no tokens for the lane while it is closed, and one short
  block when it opens.
- Every file here is gated by the plugin-runtime review (ADR 0016): the hooks
  sit under `hooks/`, and `bin/` is gated since ADR 0017.
- The lane depends on the collector keeping `usage.json` fresh; when it stops,
  the lane closes.

## Limits

- **Unverified assumption:** that Claude Code honors the federation variables
  over the claude.ai login stored on the machine. The binary's own messages
  say workload identity federation outranks the default profile, but no live
  run has confirmed what `claude auth status` reports under WIF. The wrapper's
  auth check is the guard: anything it does not recognize fails closed. The
  first live run verifies it.
- What `claude auth status --json` prints was read from the Claude Code
  2.1.295 binary: `authMethod` is one of `none`, `third_party`, `claude.ai`,
  `api_key_helper`, `oauth_token`, `api_key`. The wrapper accepts
  `oauth_token` (how a profile-resolved credential reports) and `api_key`,
  plus federation spellings a later release might use, and only with no
  `subscriptionType` and a first-party provider.
- `CLAUDECODE=1` is set by Claude Code for its Bash tool's commands (read from
  the same binary). Any process can set it; the check stops accidents, not a
  determined caller.

## Alternatives considered

- **Spend the credit on a fixed schedule.** It ignores whether the
  subscription is actually under pressure.
- **Let the session call the API directly with a key.** A long-lived key on
  disk is worse than a TPM-held signing key and a short-lived assertion, and
  an environment-wide key would bill the parent session too.
- **Run the gate on every prompt.** It costs a Python start per prompt for an
  answer that changes at most every 15 minutes.

## References

- [WIF reference](https://platform.claude.com/docs/en/manage-claude/wif-reference)
- [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md),
  [ADR 0017](0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md),
  [ADR 0009](0009-bump-bundle-versions-on-every-release.md)
- `plugins/adam-coding-local/hooks/credit-lane/`,
  `plugins/adam-coding-local/bin/claude-credit`,
  `plugins/adam-coding-local/hooks/claude-code.json`,
  `scripts/test_credit_lane.py`
