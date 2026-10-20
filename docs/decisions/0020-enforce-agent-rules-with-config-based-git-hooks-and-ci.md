# 0020. Enforce agent rules with config-based git hooks, backed by CI

- **Status:** Accepted (pilot in this repo)
- **Date:** 2026-10-20
- **Deciders:** Adam Daniel

## Context

Rules for agents live in always-on guidance (`base.md`, about 25 KB, loaded in
every session) and in skills. Both fail silently: an agent that skims past a
rule, or never loads the skill, breaks it with nothing to stop it. The fleet
strategy already says a rule may leave the always-on text only when a hard
check catches it anyway (Actions pinning left for that reason, ADR 0003).

Git hooks fire for every harness that commits: Claude Code, Codex, and a person
at a terminal. They are also easy to skip (`--no-verify`), so on their own they
are feedback, not enforcement.

Two incidents constrain the design. The retired sync-skills pre-push hook was a
global hook pointing at an absolute script path; when the script moved, every
push from every repo on the machine failed (ADR 0001, ADR 0014). And
cms-platform's secrets-scan pre-commit fails closed when `gitleaks` is missing:
the commit was refused, the following push still succeeded with the commit
before it, and the agent believed its work had landed.

Git 2.54 added config-based hooks: `hook.<name>.event` and
`hook.<name>.command` in any git config file, several per event, running next to
a repo's own `.git/hooks` or `core.hooksPath` hooks. Measured 2026-10-20: the
owner's Windows Git is 2.55 and WSL Git is 2.54; the cloud container ships
Ubuntu's 2.43, and Git 2.56 builds from source there in about a minute.

## Decision

1. **One runner, two callers.** `.githooks/agent-checks` (bash and git only, no
   hook framework) holds the checks. Local git hooks call it with the event
   name; CI calls it as `agent-checks ci <base> <head>`.
2. **CI is the enforcement.** The `agent-checks` CI job runs the runner over
   every commit a change adds. A rule that is not checked in CI is not
   enforced.
3. **Hooks are installed once per machine, scoped to the fleet.**
   `scripts/install-agent-hooks.sh` writes `~/.config/git/agent-hooks.inc`
   (one hook per event: run `.githooks/agent-checks <event>` if the repo has
   it, else do nothing) and adds `includeIf "hasconfig:remote.*.url:..."`
   entries for the Adam-S-Daniel and jodidaniel owners to the global config
   (`--system` in a cloud container). `setup.sh --owner-machine` runs it.
4. **Nothing points at an absolute path.** Git ignores a missing include file
   and the hook command is a no-op in a repo without the runner, so no state
   the installer leaves behind can break a commit or a push. A clone whose
   remote is outside the two owners never reads the include, so it never runs
   that repo's own runner.
5. **Each refusal is the rule's documentation.** The message says what is
   wrong, why, and how to fix it, so the rule need not also sit in always-on
   guidance. Guidance keeps a line only for rules that shape a plan rather
   than a commit, rules only CI can catch, or work that often skips local
   hooks (commits through the GitHub API, Codex Cloud).
6. **No path-scoped rules.** `.claude/rules/` files are read only by Claude
   Code and are unverified in multi-repo cloud sessions; the pre-commit check
   refuses them.

The first checks: pre-commit refuses staged secret or credential files and
`.claude/rules/` files; pre-push refuses a push to the default branch and a push
while changes are staged but in no commit (the refused-commit trap above), and
re-checks paths over every pushed commit.

## Consequences

- A machine on Git older than 2.54 runs no local hooks; the installer warns,
  and CI still checks. The cloud setup script needs a newer Git for local hooks
  there (see `docs/multi-repo-delivery.md`).
- Agents can still skip hooks with `--no-verify`; CI catches what they skip. A
  Claude Code guard against `--no-verify` is the next step, not part of this
  pilot.
- Commits made through the GitHub API or MCP tools never pass through local
  hooks; CI is their only check.
- Fleet-wide rollout (delivering the runner to every repo through
  `_agent-guidance`'s sync, and making the CI check required through
  repo-settings) is a later change, after this pilot shows the hooks behave.

## Alternatives considered

- **`core.hooksPath=.githooks` per clone.** Works on any Git, but must be set
  in every clone on every machine, and it replaces a repo's other hooks rather
  than running beside them.
- **A hook framework** (pre-commit.com, lefthook, husky). Each adds a
  dependency (pip and network, a binary, npm) that a fresh container or a
  Windows machine may lack, which is the gitleaks failure again.
- **Global hooks pointing at a script in a checkout.** The sync-skills design;
  it breaks every push when the checkout moves.
