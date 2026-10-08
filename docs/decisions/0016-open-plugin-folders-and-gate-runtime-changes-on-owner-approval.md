# 0016. Open plugin folders and gate runtime changes on owner approval

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Adam Daniel

Amends [ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)'s
closed-folder clause only; the rest of ADR 0013 stands.

## Context

`check_consistency.py` refused anything in a local plugin folder except
`.claude-plugin/`, `plugin.json` and `skills/`, and refused any manifest key
outside the metadata set.

**Where that rule came from.** It was written for the retired `adam-personal`
view plugin ([ADR 0012](0012-serve-the-account-skills-as-one-repo-synced-plugin.md)),
which claude.ai served to Cowork, chat and iOS. Earlier, the repo root had
been that plugin's root, so a component file anywhere in the repo would have
loaded on every surface the account reaches. Closing the folder kept skills
the whole of what that plugin shipped. ADR 0013 then applied the rule to every
plugin, after the view plugin was gone, without stating a reason.

**The distinction that remains.** A skill is text a model reads; the model
decides what to do with it. Hooks, package installs and plugin settings act
on their own:

- **Hooks run code with no model in between.** A hook fires on its event and
  runs its command
  ([plugin components](https://code.claude.com/docs/en/plugins/components)).
- **`package.json` plus a lockfile at a plugin root installs dependencies.**
  Claude Code installs the registry dependencies into every version copy of
  the plugin. The install is constrained — exact pins, https only,
  `--ignore-scripts`, a 60-second limit — and cannot be disabled, and the
  installed code runs when a hook or server loads it
  ([plugin loading](https://code.claude.com/docs/en/plugins/loading)).
- **`settings.json` at a plugin root takes only `agent` and
  `subagentStatusLine`.** `agent` replaces the main thread's system prompt,
  tools and model in every session while the plugin is enabled
  ([plugin components](https://code.claude.com/docs/en/plugins/components)).

claude.ai and Cowork do not install a plugin that has a top-level `bin/`, so
a plugin meant for those surfaces must not ship one.

A Windows trap sits next to hooks: a hook command that starts with a bare
`bash` resolves to `C:\Windows\System32\bash.exe`, which is WSL, not Git
Bash. The hook then runs in another operating system against another
filesystem, or fails.

## Decision

**Plugin folders are open.** Any plugin may ship any component — `hooks/`,
`bin/`, `.mcp.json`, `agents/`, `settings.json`, `package.json`, and
component keys in its manifests. `check_consistency.py` no longer checks the
folder or the manifest keys. Its other checks stay, including the symlink
refusal ([ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md)) and the
marketplace and source checks. The Agent Plugins schema still closes the
root `plugin.json`; `check_agent_plugins.py` keeps enforcing it.

**No hook runs through a bare `bash`.** `check_consistency.py` reports a
`type: command` hook whose shell-form `command` starts with `bash`,
`bash.exe` or a quoted form of either, and an exec-form hook (one with
`args`) whose `command` is `bash` or `bash.exe`. It reads `hooks/hooks.json`,
every file a manifest's `hooks` key names, and inline hooks in either
manifest or the marketplace entry. Call the script path directly instead,
for example `"${CLAUDE_PLUGIN_ROOT}"/skills/x/scripts/y.sh`. The rule does
not restrict which hook events or matchers exist.

**A pull request that changes how a plugin runs code waits for the owner.**
The workflow `.github/workflows/plugin-runtime-review.yml` runs
`scripts/plugin_runtime_gate.py`, which gates a pull request that:

- changes a file under a plugin root's `hooks/`;
- changes a plugin root's `settings.json`, `package.json`, `bun.lock`,
  `bun.lockb`, `npm-shrinkwrap.json` or `package-lock.json`;
- changes the `hooks` or `settings` value of either manifest, or of a
  marketplace entry, or adds an entry carrying one;
- changes a file that a hook names after `${CLAUDE_PLUGIN_ROOT}`, at base or
  head;
- changes the gate's own workflow, script or tests.

When gated, the job `plugin-runtime-approval` runs in the GitHub environment
`plugin-runtime-review`, whose required reviewer is the owner, and waits for
the owner's approval. Otherwise it selects no environment and succeeds at
once.

The design choices:

- **`pull_request_target`, used safely.** It runs the base branch's workflow
  and script, so a pull request cannot edit the gate out of itself. That is
  also why a change to the gate is gated: it takes effect only after merge.
  It is the only trigger, so no push, dispatch or schedule run can publish a
  passing context on a pull request's head sha. The pull request's content
  is read through the API as data: it is never checked out, installed or
  executed, JSON is parsed with a JSON parser only, and files are capped at
  1 MiB.
- **Fail closed.** An API error, a file that does not parse, a diff at the
  API's 3000-file cap, a file the list says exists but cannot be read, or
  anything unexpected gates the pull request. A failed `detect` job selects
  the environment too.
- **The environment is checked, not assumed.** GitHub silently creates an
  unprotected environment when a job names one that does not exist, and the
  job then runs without waiting. So when gated, the approval job reads the
  environment through the API and fails unless it has a required-reviewers
  rule naming the owner.
- **`prevent_self_review: false`.** The owner is the sole reviewer, and
  agents open pull requests as the owner, so self-review prevention would
  leave no one able to approve.
- **The limit.** Because agents act with the owner's identity, this stops
  accidents and agents that follow the rules. It does not stop an agent
  determined to approve the deployment with the owner's token. `AGENTS.md`
  tells agents never to approve it. Required checks also match by name, not
  by workflow, so the gate relies on no other workflow publishing
  `plugin-runtime-approval`. A test in `scripts/test_plugin_runtime_gate.py`
  catches such a job in CI on the pull request's own code, which stops
  accidents, not deliberate impersonation, matching the limit above.

## Consequences

- Plugins can ship hooks, MCP servers, agents and settings, and each change
  to how one runs code gets an owner decision rather than a CI refusal.
- `plugin-runtime-approval` becomes a required check through a separate
  repo-settings pull request after this merges. The workflow has no
  `concurrency:` and no `paths:` filter for that reason.
- The gate can only be exercised on a pull request opened after this merges,
  since `pull_request_target` runs the base branch's workflow.
- The environment lives outside code: repo-settings' engine has no
  environment support. It was created on 2026-10-08 through the API, with the
  owner as required reviewer and no branch policy. If it is deleted, gated
  pull requests fail the protection check instead of passing.
- Detection over-approximates on purpose. A hook that passes the bare plugin
  root (`cd "${CLAUDE_PLUGIN_ROOT}" && ...`) or a path with a variable in it
  gates changes to everything under the directory it names.

## Alternatives considered

- **Keep plugin folders closed.** The reason for the rule left with the view
  plugin, and it blocked components the owner wants to ship.
- **Gate inside `ci.yml` on `pull_request`.** A pull request's own copy of
  the workflow would run, so the pull request could edit the gate away.
- **Rely on CODEOWNERS review.** The owner is also the identity agents use, so
  a review requirement on paths gives no separate decision point, and it
  cannot see a hook's script outside `hooks/`.

## References

- [ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md) —
  the closed-folder clause this amends
- [ADR 0012](0012-serve-the-account-skills-as-one-repo-synced-plugin.md) —
  where the closed folder started
- [ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md) — the symlink
  refusal, unchanged
- [Plugin loading](https://code.claude.com/docs/en/plugins/loading) and
  [plugin components](https://code.claude.com/docs/en/plugins/components)
- `scripts/plugin_runtime_gate.py`, `scripts/test_plugin_runtime_gate.py`,
  `.github/workflows/plugin-runtime-review.yml`
