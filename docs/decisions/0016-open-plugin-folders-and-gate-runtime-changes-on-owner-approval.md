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
- changes the gate's own workflow, script or tests, or
  `.github/workflows/actions.lock`, which chooses the commit the gate's
  tag-pinned actions run at (added 2026-10-20 with the lockfile migration).

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

### Update 2026-11-13: plugin-root `settings.json` after Claude Code 2.1.323

Claude Code changed this after the ADR was accepted; the decision stands, and
the Context bullet above ("takes only `agent` and `subagentStatusLine`") is
no longer accurate. Quoted from the release notes:

- [2.1.323](https://github.com/anthropics/claude-code/releases/tag/v2.1.323):
  "Removed the `subagentStatusLine` setting: subagent rows always use the
  default look, and settings files that still hold the key load as before."
- [2.1.323](https://github.com/anthropics/claude-code/releases/tag/v2.1.323):
  "Deprecated a plugin's `agent` setting: it still applies, and now warns at
  startup, in `claude plugin list` and in `claude plugin validate`."

Checked on 2.1.323 with `claude plugin validate` against two scratch plugins,
one whose root `settings.json` holds `agent`, one `subagentStatusLine`: both
exit 0. The `agent` plugin prints "A plugin's `agent` setting is deprecated
and will stop applying in a future release. Tell people to set `agent` in
their own settings or pass --agent." The `subagentStatusLine` plugin prints
nothing about the key. So `agent` still applies today but is on its way out,
and `subagentStatusLine` is accepted and ignored. The gate is unchanged: it
guards a plugin-root `settings.json` because `agent` still replaces the main
thread's system prompt, tools and model while it applies.

### Update 2026-11-17: two changes the owner's approval could not affect no longer wait

The owner asked that pull requests like
[#178](https://github.com/Adam-S-Daniel/adam-agentskills/pull/178), a refresh
of the bundled guidance fallback, stop needing his deployment review. A survey
of every run that entered `plugin-runtime-review` (49 runs on 21 pull requests,
2026-10-08 to 2026-11-17; every pull request was approved in the end, none
rejected) found two kinds where the approval could not change what runs. The gate now lets both through; everything else
gates as before.

- **A copy of text the hook already fetches live.** The fleet-guidance hook
  installs `_agent-guidance`'s `agents-md/base.md` from `main` at every session
  start, with no review here, and falls back to the bundled
  `fleet-guidance.md` only when that fetch fails
  ([ADR 0023](0023-deliver-the-fleet-guidance-from-a-plugin-hook-that-fetches-it.md)).
  A pull request that makes the fallback byte-identical to the upstream branch
  tip gives a session nothing the live fetch would not. Such copies are listed
  in `scripts/plugin_runtime_upstream_copies.json`, and a change to a listed
  path is excused from the `hook-file`, `hook-referenced` and `plugin-path`
  reasons when its bytes at head equal the upstream file at the tip of the
  named branch. The limits:
  - The list is read from the base checkout, is a gate file itself, and
    accepts `.md` paths only. A script is never excused this way, however
    verbatim: the bundled `fleet-memory.sh` and `github-write-marker.sh` are
    code that runs on the owner's machine, and this gate is the one place the
    owner sees a change to them before it does.
  - Different bytes, a removal, a rename or an upstream that cannot be read
    gates as before, with the extra reason `copy-differs-from-upstream` or
    `upstream-unreadable`.
  - The branch is resolved to a commit first, so a tag of the same name
    cannot stand in for it. A test holds each listed copy to the repository,
    branch and path its hook fetches.
  - Covered: #130 and #178 (2 of the 21 pull requests).
- **A lockfile change that leaves the gate's own pins alone.**
  `.github/workflows/actions.lock` is a gate file because it chooses the commit
  the gate workflow's actions run at. It now gates only when its format
  version, the gate workflow's own list, or an entry for an action the gate
  workflow uses differs between base and head. "Uses" is read from the
  lockfile's list and from the workflow's own `uses:` lines, at base and at
  head, and matches the action's `owner/repo` under any version or letter
  case, plus any entry such an entry names. A missing lockfile gates, and a
  shape the script cannot read fails closed. An entry only other workflows
  use does not gate: those workflows already run the pull request's own code
  on `pull_request`. Covered: #106 and #144, and #89 as the gate workflow
  stood then (it did not use `setup-python` yet; the same bump would gate
  today).

Still gated, by design: hook scripts and hook configs, `bin/`, plugin settings
and package files, runtime manifest keys, skill frontmatter and load-time
commands, the body of a skill that grants a shell, the gate's workflow, script,
tests and copy list, `scripts/cloud-env-setup.sh` and
`scripts/codex-session-start.sh`, and a Dependabot bump of an action the gate
workflow itself uses. Those were the other 16 pull requests.

### Update 2026-11-18: own-action pins need GitHub's word, and the gate checks the pull request did not move

Two safeguards from the narrower rule the owner set aside
([#181](https://github.com/Adam-S-Daniel/adam-agentskills/pull/181), closed as
superseded by [#179](https://github.com/Adam-S-Daniel/adam-agentskills/pull/179))
come back on top of the lockfile rule above, at the owner's request.

- **An own-action pin must be a real release.** The lockfile is the pull
  request's word, and Dependabot can propose an own-action bump soon after a
  release (#180 drops the cooldown for own actions). So in a lockfile change the rule above lets through, every entry
  added or changed for an `adam-s-daniel/*` action (by key, or by the owner's
  numeric id 4205216) must be exactly `adam-s-daniel/<repo>@<tag>` with gh
  actions-lock's four keys and `ref` equal to the tag, and GitHub must confirm
  that the repository has that `repo_id`, the owner's id and is not a fork,
  that the tag resolves to the locked commit, that the commit is on the
  default branch, and that the tag's release is published, not a prerelease
  and immutable. Anything else gates as `gate-itself` with the extra reason
  `own-action-unverified` and a fixed message; an API error fails closed.
  Removing an own entry, and third-party entries, ask GitHub nothing.
- **The pull request must still be the one the event named.** The file list
  carries no sha: it describes the pull request when it is listed. The gate
  now re-reads the pull request after listing and fails closed unless its
  head sha, base sha and base branch equal the event's (`GITHUB_BASE_REF`,
  which the runner sets on `pull_request_target`) and the base branch is the
  repository's default branch. A pull request into any other branch therefore
  always waits for the owner.

Not carried over from #181, because the owner asked for these two only: its
byte-for-byte check that the lockfile is in the form gh actions-lock writes,
and its comparison from the merge base as well as from the base.

### Update 2026-11-19: a Dependabot bump of the gate workflow's own actions no longer waits

The owner's policy for every fleet repo: third-party action bumps merge
automatically once the Dependabot cooldown has passed, a bump that resolves a
security alert merges at once, and bumps of the owner's own actions
(`Adam-S-Daniel/*`, `jodidaniel/*`) merge with no cooldown. The 2026-11-17
update let lockfile-only bumps through, but a bump of `actions/checkout` or
`actions/setup-python`, which `plugin-runtime-review.yml` itself uses, still
waited: Dependabot edits that workflow's `uses:` lines, and the workflow is a
gate file. Every such bump would have needed the owner, against the policy.

Such a pull request no longer waits when all of these hold, and otherwise
gates as `gate-itself` with the extra reason `dependabot-bump-unverified` and a
fixed message (a pull request that is not Dependabot's gets no extra reason):

- **Dependabot's own.** GitHub names `dependabot[bot]` (by login and account
  id) as the pull request's author, its branch is a
  `dependabot/github_actions/` branch in this repository, and every commit on
  it is authored by that account and committed and signed by GitHub itself
  (`web-flow`, by login, id and email, with a `valid` signature), the last
  being the head the event named. The commit author alone proves nothing: it
  is the account that owns the author email, which anyone can type, and a
  verified signature vouches only for the committer.
- **Only refs move.** Among the gate's files only the gate workflow and
  `actions.lock` changed, and the lockfile change moves a pin of the gate's
  actions. The workflow's text differs only on `uses:` lines, it parses to the
  same tree once each `uses:` value is cut to its action's name, and every
  action it uses is a third-party `owner/repo@ref`.
- **The pins are GitHub's word.** `actions.lock` keeps its format version,
  lists for the gate workflow exactly the actions it uses and pins each one;
  an existing pin of those actions is unchanged; each new one has gh
  actions-lock's four keys, names the repository the base pinned (same
  `owner_id` and `repo_id`, which GitHub confirms), its tag resolves on
  GitHub to the locked commit, it is a higher version than every base pin of
  that action, and its release is published, not a prerelease, and at least 7
  days old. Every entry those actions' entries name must be unchanged.
  Own-action pins in the same change still need the 2026-11-18 checks.

The release checks repeat Dependabot's cooldown inside the gate, so even a
branch that only looks like Dependabot's (say, a GitHub-side rebase that
keeps the author and re-signs as `web-flow`) can bring in nothing but what
Dependabot itself would propose. One consequence: a security update to one of
the gate workflow's own actions whose release is under 7 days old still waits
for the owner. Every other workflow's security bumps are not affected.

Any other change in the same pull request is judged as before, so a plugin
change carried by a Dependabot branch still waits.

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
  `scripts/plugin_runtime_upstream_copies.json`,
  `.github/workflows/plugin-runtime-review.yml`
