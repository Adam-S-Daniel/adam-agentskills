# Skill delivery in a multi-repo session

A Claude Code on the web session created with several repositories clones them
side by side and sets the project directory to their **parent**. Getting the
fleet's pinned skills into that session takes two independent things, and it is
worth being blunt about which of them a repo can and cannot ship:

| half | what it is | who ships it |
|---|---|---|
| **the union** | the hook installs every discovered repo's locked skills, not one repo's | this repo — see [ADR 0007](decisions/0007-install-the-union-of-every-discovered-lock.md) |
| **the wiring** | something has to make a `SessionStart` hook fire at all | **the environment** — it cannot be committed by a repo |

Without the second, the first never runs. That is not a gap in the union; it is
[ADR 0005](decisions/0005-resolve-hooks-only-from-cwd-and-user-settings.md)'s
finding, and it is why every claim elsewhere in these docs of the form "the hook
installs nothing in a multi-repo session" is still true until the wiring below is
placed.

## First: which surface are you on

**This whole document is about the CLOUD. A laptop needs none of it**, and the
two surfaces differ in what delivers skills, where they land, and what you type
to invoke one. Getting that wrong is the single easiest way to misread every
section below.

| | laptop (durable machine) | Claude Code on the web (cloud/ephemeral) |
|---|---|---|
| what delivers skills | the **marketplace plugin install** — `setup.sh`, `/plugin install adam-coding-anywhere@adam-agentskills` | the **`skills-bootstrap` hook**, from `skills.lock` |
| where they land | `~/.claude/plugins/` | `~/.claude/skills/<name>/` |
| scope | **user-level, so cwd-independent** | per session, rebuilt each time |
| multi-repo session | already works — nothing to do | needs the wiring below |
| invoke a skill as | `/adam-coding-anywhere:skills-doctor` — plugin-namespaced | `/skills-doctor` — **bare, no prefix** |

**The invocation difference is not cosmetic and is easy to get backwards.**
Plugin skills are namespaced `<plugin>:<skill>`; a skill installed as a
directory under `~/.claude/skills/` has no plugin to be namespaced by, so it is
just its own name. Measured, not assumed: a probe skill planted at
`~/.claude/skills/zz-invocation-probe/` and a hook-installed
`~/.claude/skills/finding-unknowns/` were both reported by a fresh CLI session
as `/zz-invocation-probe` and `/finding-unknowns`. So `/adam-coding-anywhere:skills-doctor`
**does not exist in a cloud session**, and typing it there gets
`Unknown command`.

### Which surface the hook thinks it is on, for every value it can see

**THE RULE IS AN `OR`, AND THE ENTRYPOINT IS ONLY HALF OF IT:**

```
install  ⇔  entrypoint is remote  OR  $CLAUDE_CODE_REMOTE_SESSION_ID is set
```

Neither half is readable off the variable's name — `ssh-remote` contains
"remote" and is a durable workstation; `claude-in-teams` does not and is hosted.
So here is the whole allowlist, read out of the bundled binary (**2.1.245**,
GIT_SHA `28b7e8c4`) rather than remembered, and measured against the hook one
value at a time, **in both states of the session-id variable**.

| classification | `CLAUDE_CODE_ENTRYPOINT` | session id **unset** | session id **set** |
|---|---|---|---|
| **remote** (10) | `remote`, `remote_baku`, `remote_cowork`, `remote_cowork_trigger`, `remote_desktop`, `remote_mobile`, `remote_trigger` | installs | installs |
| | `claude_in_slack`, `claude-in-slack`, `claude-in-teams` | installs — named exactly, see below | installs |
| **durable** (16) | `cli`, `mcp`, `sdk-cli`, `sdk-ts`, `sdk-py`, `bench`, `claude-vscode`, `claude-code-github-action`, `local-agent`, `local_agent`, `claude-desktop`, `claude-desktop-3p`, `claude-security`, `claude-coworker`, `claude-coworker-terminal`, **`ssh-remote`** | skips: `marketplace install is authoritative` | **installs** |

**Read the right-hand column before blaming the entrypoint.** Every one of the
26 values installs when `CLAUDE_CODE_REMOTE_SESSION_ID` is present — `cli` and
`ssh-remote` included. So "this is a durable machine, the hook cannot have run"
is not a safe inference from the entrypoint alone, and a workstation that
somehow carries that variable behaves like a hosted session. Check both.

Two more things this table exists to stop, beyond the `OR` above:

- **`ssh-remote` is durable.** A guard written `*remote*`, or a `grep remote`,
  classifies it as hosted and starts writing into a workstation's
  `~/.claude/skills`. The guard is anchored (`case … in remote*`) for exactly
  this one value, and a test pins it.
- **Three hosted spellings do not begin with `remote`.** `claude_in_slack`,
  `claude-in-slack` and `claude-in-teams` are named exactly in the guard. They
  are matched exactly and not by a `claude*` prefix, which would wrongly capture
  `claude-desktop`, `claude-vscode`, `claude-desktop-3p`, `claude-security`,
  `claude-coworker` and `claude-coworker-terminal` — every one of them a durable
  machine.
`test_every_known_entrypoint_is_classified` pins all 26 outcomes, and
`test_the_pinned_entrypoint_list_still_matches_the_binary` re-reads the
allowlist from a bundled binary when one is present and fails if it has moved.
A spelling a future release adds is **not** covered — the list is a snapshot.
An unseen `remote_*` gets the right answer from the prefix arm for free;
anything else new arrives classified as durable, which is the safe direction: a
missed install, never an unwanted write to a workstation.

**Why the laptop needs nothing, stated so nobody adds wiring it does not want.**
Marketplace plugins install at user level, so they are already loaded whatever
`cwd` is — the multi-repo shape that breaks cloud delivery is not a problem
there at all. And the hook itself declines to act: on a non-remote entrypoint it
emits `skills: skipped — durable session (…), marketplace install is
authoritative` and installs nothing. A laptop that has never run `setup.sh` gets
no skills, and the fix for that is the marketplace, never the setup script
below — a laptop has no environment setup script in the first place, because
that field belongs to a **cloud environment**.

## Why the wiring cannot live in a repo

Claude Code resolves hooks from the settings chain rooted at `cwd` and at
`$HOME`:

```
managed/policy → --settings → <cwd>/.claude/settings.local.json
              → <cwd>/.claude/settings.json → $HOME/.claude/settings.json
```

`--add-dir` is a tool-access grant. An additional directory contributes skills
(with live reload), commands and agents — and `CLAUDE.md` only when
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` is set — but **never hooks, and
never the rest of settings**. So in a session whose project dir is the parent of
eleven repos, all eleven `.claude/settings.json` files are read and none is
applied. No repo's hook fires, whatever its command string says.

Measured, not inferred — re-confirmed on Claude Code 2.1.241 with controls: a
byte-identical settings file fires its hooks as `<cwd>` and fires nothing as an
`--add-dir` root, across three event types, while `--add-dir` was demonstrably
honoured in the same runs. `skills-doctor` reports the state as `hook-not-wired`.

The committed wiring cannot simply be re-pointed either. This repo's
`.claude/settings.json` runs
`bash "$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh"`, and in the
multi-repo shape `$CLAUDE_PROJECT_DIR` is the parent — which has no
`.claude/hooks/` at all.

## The wiring (cloud only)

Put this launcher in the **cloud environment's setup script** — the *Setup
script* field in the environment settings dialog at claude.ai/code. The block
is the whole field: paste all of it, replacing what is there. It runs
[`scripts/cloud-env-setup.sh`](../scripts/cloud-env-setup.sh), which holds
everything below, so the setup changes by reviewed pull request and the paste
almost never changes. It runs the `main` copy only: from the attached
checkout's `origin/main` (never the checked-out branch, which could be an
unreviewed one), or, when this repo is not attached, downloaded from `main`.
A rename or move of the script makes that download 404 and setup is skipped
with one line, so moving it needs a new paste.

**What ran is recorded.** The launcher passes its version, its own SHA-256 and
where the script came from (with `origin/main`'s commit for a checkout); the
script adds its own SHA-256, the fleet-guidance pin, the `_agent-guidance`
commit it installed and the installer's outcome. It writes that record to
`/root/.claude/cloud-env-setup.json`, prints one `cloud-env-setup:` line, and,
where the environment has the usage sink with proxy signing
(`USAGE_SINK_BUCKET`, `USAGE_SINK_REGION`, `USAGE_SINK_SIGNING=proxy`), sends
it as a `setups` record, which adam-agentskills-private's ingest appends to
`events/<YYYY-MM>/cloud-setup.jsonl` on `persistent/usage-events`. That script writes the user-scope settings
file, `/root/.claude/settings.json`, the one link of the chain that every
hosted session reads, project threads included.

The environment runs its setup once and caches the result for about 7 days,
keyed on the pasted text. A merged change to `scripts/cloud-env-setup.sh`
therefore reaches an environment when that cache expires, not at once; hooks
the script wires still run from the checkout every session. To apply a change
sooner, edit the pasted launcher trivially (for example, a comment) so the
cache misses.

There is no laptop counterpart to this section and no laptop version of the
script. A durable machine has no environment setup script, and would have
nothing for one to fix: see *First: which surface are you on* above.

```bash
#!/bin/bash
# Runs main's scripts/cloud-env-setup.sh from adam-agentskills, which holds the
# whole setup: change that by pull request, not here. Bump the version on any
# edit here. Never exits nonzero: that would stop sessions from starting.
export CLOUD_SETUP_LAUNCHER_VERSION=1 CLOUD_SETUP_LAUNCHER_SHA256=
[ -f "$0" ] && CLOUD_SETUP_LAUNCHER_SHA256=$(sha256sum < "$0" | cut -c1-64)
r=/home/user/adam-agentskills f=scripts/cloud-env-setup.sh; s=$(mktemp) || exit 0
if git -C "$r" show "refs/remotes/origin/main:$f" > "$s" 2>/dev/null && [ -s "$s" ]; then
  export CLOUD_SETUP_SOURCE=checkout CLOUD_SETUP_COMMIT=$(git -C "$r" rev-parse "refs/remotes/origin/main^{commit}")
elif curl -q -fsS --proto '=https' --connect-timeout 5 --max-time 30 --max-filesize 1048576 \
    -o "$s" "https://raw.githubusercontent.com/Adam-S-Daniel/adam-agentskills/main/$f"; then
  export CLOUD_SETUP_SOURCE=download CLOUD_SETUP_COMMIT=
else echo "cloud-env-setup: no checkout and download failed; setup skipped"; rm -f "$s"; exit 0; fi
bash "$s" || echo "cloud-env-setup: exited nonzero"
rm -f "$s"; exit 0
```

`skillListingBudgetFraction` doubles Claude Code's skill-listing budget from its
default 1% of the context window (in characters). A cloud session lists the
account's skills, the bundles and the plugins together, and at 1% the end of the
listing loses its descriptions, so those skills cannot trigger. A cloud session
on 2026-11-03 listed eight `anthropic-skills:` entries by name only.
The Windows desktop settings carry the same value.

Both groups use `startup|resume|fork`: since Claude Code
[2.1.214](https://github.com/anthropics/claude-code/releases/tag/v2.1.214) a
forked session reports source `fork` instead of `resume`, so
`startup|resume` leaves it without the bundles and fleet guidance.

As checked against the [current hooks documentation on 2026-10-06](https://code.claude.com/docs/en/hooks#sessionstart),
startup and launch-time resume (`--continue` or `--resume`) hooks run while
the UI loads; the first response waits for those hooks to finish, including
a supported `SessionStart` `reloadSkills` rescan. Interactive `/resume` waits
for the hooks before switching conversations.

[`DirectoryAdded`](https://code.claude.com/docs/en/hooks#directoryadded) receives the added absolute path in `directory`, with
`slash_command` or `register_repo_root` as its source; it runs in the
background and cannot control a decision (the SDK exposes `systemMessage` only
in debug logs). Since `reloadSkills` is documented only for `SessionStart`,
reusing the bootstrap hook here is not justified; the owner decision remains
pending in [issue #8](https://github.com/Adam-S-Daniel/adam-agentskills/issues/8).

`$CLAUDE_PROJECT_DIR` **inside** the hook command is correct and must stay: that
one is expanded at hook time, by Claude Code, which does set it. Only the outer
`project=` cannot rely on it.

### Where the fleet guidance comes from now

The wiring no longer delivers the fleet guidance: `_agent-guidance` is moving
the owner-only guidance out of the consumer repos
([ADR 0043](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0043-the-owner-only-guidance-leaves-the-consumer-repos.md)),
so there is no per-repo `fleet-memory` hook for it to run, and the `Stop`
record of which guidance loaded moves to the owner's plugin. The owner
gets the guidance from the account-synced `adam-anything-anywhere` plugin
([ADR 0023](decisions/0023-deliver-the-fleet-guidance-from-a-plugin-hook-that-fetches-it.md)):
its SessionStart hook fetches `main`'s HEAD commit and `base.md` and installs
it. The environment's setup script also installs it, in the owner block at
the end of `scripts/cloud-env-setup.sh`: that is the guaranteed route (ADR 0043), because
it does not depend on the account sync reaching the surface. The block fetches
`fleet-memory.sh` at a pinned `_agent-guidance` commit and runs it only if its
SHA-256 matches, so the setup script never executes unreviewed code; the
guidance text itself comes from `main`'s HEAD, stamped with that commit's
committer time. Where the plugin's hook and the block both run, the
[freshest-stamp rule](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0016-the-freshest-delivery-wins-the-shared-global-block.md)
(ADR 0016) arbitrates and the second reports `current`. The block never fails
the setup: any problem prints one `fleet-guidance: setup skipped` line and the
script goes on. Bump the pin (two variables, commented in the block) when
`fleet-memory.sh` changes in `_agent-guidance` (fetch first), and merge it; each
environment picks it up when its setup cache expires. A test holds the pin to the bundled copy
of `fleet-memory.sh` in this repo, so the two move together.

The environment snapshot is cached after setup (about 7 days), so this route
serves `base.md` as of setup time; the account-synced plugin refreshes it every
session. "The second reports `current`" holds only when both deliveries carry
the same digest; a newer plugin copy wins on its stamp and replaces this one. Anyone else's session reads the
repo stub alone, which is the intended state.

What stays here is two groups, `skills-bootstrap` and the `agent-checks` guard,
and they are separate GROUPS, not one, because a group carries one `matcher`
and one `timeout` and they need different ones. `skills-bootstrap` fetches two
registries and needs its 90 seconds; the guard is a local check and wants 10.
`exec` is also why they cannot share a command: it replaces the shell, so the
first hook found would be the only one that ever ran.

Things about it that are deliberate:

- **It resolves the hook at session start, not at setup time, and only in the
  project dir's children.** In a single-repo session the project dir is the
  repo, which has no children carrying hooks, so these commands do nothing and
  the repo's own committed wiring fires once, as it does today. If no attached
  repo ships the hook the command exits 0 and nothing happens.
- **`timeout: 90`.** The hook's own budget for fetching every source is 60
  seconds, so a shorter harness timeout kills it mid-fetch and loses the
  fail-soft verdict it exists to print.
- **User scope, not `<project>/.claude/settings.json`.** Until 2026-10-31
  this wrote the project-scope file, out of caution that a user-scope file
  might displace the harness's own hooks in `~/.claude/launcher-settings.json`.
  Two measurements settled it in a project thread on Claude Code 2.1.312: the
  project-scope file's `Stop` hook never fired while the user-scope one did,
  and the harness's own `Stop` hooks fired beside it in the same turns. So the
  project-scope file left project threads with no fleet guidance, no bundles,
  no guard and no record, and the user-scope one costs nothing. `skills-doctor`
  reads the user scope too, so it still sees the hook as wired.
- **It is safe in a repo that never adopted delivery.** Since ADR 0007 the
  `$SELF_ROOT` fallback applies only when `CLAUDE_PROJECT_DIR` is unset, so a
  session whose project dir and children carry no lock installs nothing rather
  than silently inheriting the bundle of whichever repo happened to ship the
  hook.

**Which copy of the hook runs, and why a stale one can deliver NOTHING.** The
first match wins — the glob is alphabetical, so the alphabetically-first attached
repo supplies the hook for the whole session. The fleet's copies are synced and
sha-pinned by `_agent-guidance`, so they are normally byte-identical; a repo
sitting on an older pin supplies an older copy.

The obvious cost of an older copy is missing union discovery, so only that repo's
lock is read. **That is not the expensive one.** Before the entrypoint guard was
widened (ADR 0007's PR), the hook tested `CLAUDE_CODE_ENTRYPOINT != "remote"` —
an exact match against ONE spelling, while seven legal values begin with
`remote`. On any of the other six it took the early return and reported
`skills: skipped — durable session`, a sentence that reads like a correct
decision while installing nothing.

Measured across both hook copies at three spellings, same lock, same project dir,
only the entrypoint varying:

| entrypoint | pre-widening copy | post-widening copy |
|---|---|---|
| `remote` | 22/22 — OK | 22/22 — OK |
| `remote_mobile` | **0 — "skipped, durable session"** | 22/22 — OK |
| `remote_cowork` | **0 — "skipped, durable session"** | 22/22 — OK |

`remote_mobile` is not hypothetical: it is what `CLAUDE_CODE_ENTRYPOINT` actually
held in the hosted session these notes were measured in. So the wiring above can
be placed correctly, fire correctly, resolve a hook correctly, and still deliver
nothing — because the copy it resolved is older than the guard fix. **Check the
hook copy the glob will actually pick, not just that the wiring is in place**,
and treat `skipped — durable session` in a session you know is remote as that
diagnosis rather than as a durable machine.

### Git hooks in cloud sessions (ADR 0020)

The fleet's commit-time checks run as config-based git hooks, which need Git
2.54+. The cloud image ships Ubuntu's Git 2.43 (checked 2026-10-20), which
ignores them, so without this block a cloud session has no local hooks and CI is
the only check. The script above already includes it, before its final
`exit 0`. It builds Git once (about a minute on 4 cores, measured 2026-10-20);
the snapshot keeps it for later sessions.

- **`--system`, not the default global scope.** The session's `~/.gitconfig`
  holds settings the harness writes (commit signing, proxy). Whether it is
  rewritten when a session starts is not established, so the hooks go in the
  system file, which nothing else writes. `sysconfdir=/etc` keeps
  the built Git and Ubuntu's reading the same system file.
- **`/usr/local/bin` precedes `/usr/bin` on the session's `PATH`**, so the
  built Git is the one agents run.
- **The installer comes from whichever attached repo ships it**; it is the same
  file in each. If none does, nothing is installed.
- **The `PreToolUse` group in the wiring above runs the `--no-verify` guard**
  (`.claude/hooks/agent-checks-guard.sh`), which `_agent-guidance`'s sync
  delivers to every fleet repo with the runner and the CI check (its ADR 0021).
  A repo's own registration of the guard does not fire in a multi-repo session
  for the same reason its `SessionStart` hooks do not, so the wiring runs the
  first attached copy. With none attached it does nothing.

## What crosses into a multi-repo session, and what does not

Hooks do not. **Skills do** — and that asymmetry is worth knowing before anyone
reaches for a workaround, because it is the one thing a repo can still deliver
into this shape on its own.

Measured in a hosted two-repo session on Claude Code **2.1.245**, project dir
`/home/user` with `adamdaniel.ai` and `adam-agentskills` beneath it: the project skill
committed at `adamdaniel.ai/.claude/skills/embeddable-tool-pages/` was live in
that session's skill roster, while neither repo's `SessionStart` hook fired at
all and no `skills:` verdict was printed. Established by elimination rather than
by inference — the only other skill sources present were `~/.claude/skills/`
(the claude.ai account-sync channel's `synced/`, plus one built-in), and that
skill is in neither, so it reached the session from a child of `cwd`.

So a repo that needs one or two specific skills in a multi-repo session **can**
commit them under `.claude/skills/` and skip the hook entirely. That is a real
escape hatch and a bad default. A vendored copy is not digest-verified, does not
track the registry, and is precisely the drift ADR 0001 declined to take on
fleet-wide; adamdaniel.ai's own guidance says "do NOT re-vendor" for exactly
this reason. Reach for it for a **site-owned** skill — which is what
`embeddable-tool-pages` is — never for a bundle. The wiring above is what
delivers a bundle.

One further measurement from the same session: the hosted container ships
**no `~/.claude/settings.json` at all**. The harness's own hooks live in
`~/.claude/launcher-settings.json`, a different file, and the 2026-10-31 test
above showed the two merge. That is why the script can write the user-scope
file outright rather than merging into an existing one.

## Checking it worked

**Who sees the verdict depends on which hook the session is running**, and the
answer changed on 2026-08-25.

The hook emits its line as `hookSpecificOutput.additionalContext`, which Claude
Code's hooks reference defines for `SessionStart` as *"text to inject into
Claude's context"*. The field that renders to a **person** is the top-level
`systemMessage`, and for most of this hook's life it set only the first. So "no
`skills:` line" was a thing the agent could report and a human simply could not
observe — reading its absence as "the hook never ran" was sound only when the
agent was the one looking. That is not academic: the absence was once put to an
operator as something they should have noticed across months of sessions, when
they were never in a position to notice it.

The hook now emits **both**, bound to one scrubbed value so the operator and the
agent cannot be told different things. Printed at every start, deliberately: it
is the ABSENCE of the line that diagnoses a hook which never fired, so a verdict
shown only when `DEGRADED` would go silent in exactly the case worth noticing.

**A consumer does not get this the moment it merges here.** Sessions run the
hook their `skills_bootstrap` pin names, so the verdict stays invisible to the
operator until that pin moves and the sync delivers. Until then, treat the three
checks below as the way to see it.

The verdict, when there is one:

```
skills: 23/23 from Adam-S-Daniel/adam-agentskills@eb25bd6 across 3 locks (adam-agentskills, cms-platform, adamdaniel.ai) — OK
```

With several locks it names how many contributed and which repos they came from.
With exactly one lock the line is unchanged from what it has always been.
`DEGRADED` names the knob to fix.

**So check it one of these ways instead**, in rough order of directness:

- **Ask the agent.** "What does your `skills:` verdict say?" It is in the
  context window; it can quote it verbatim, `DEGRADED` reason and all.
- **Look at the filesystem** — a cloud check, and the most decisive one, because
  it inspects the install rather than a report about it. `ls ~/.claude/skills/`:
  the hook writes each locked skill as a directory there. Two things in that
  listing are NOT the hook's doing and are the easy false positive —
  `synced/` is the claude.ai account-sync channel, and a skill can also arrive
  from a repo's own `.claude/skills/`. The unambiguous signal is the hook's own
  install record, `~/.claude/skills/.skills-bootstrap-installed.json`: it exists
  only if the hook ran, and it names what the hook itself put there.
  On a **laptop** this bullet does not apply — look in `~/.claude/plugins/`
  instead, or run `claude plugin list`, because the marketplace is what
  installed them and `~/.claude/skills/` is not where they live.
- **Run the skills-doctor skill** for the long form, once a plugin carrying it
  has actually loaded — **`/skills-doctor` in a cloud session**, where the hook
  installs it as a bare directory, and `/adam-coding-anywhere:skills-doctor` on
  a laptop, where the marketplace namespaces it by plugin. Typing the laptop spelling in the
  cloud gets `Unknown command`, which reads like the skill is missing when it is
  present under its other name. Note also the ordering trap: the diagnostic that
  explains why no skills loaded is itself a skill
  (`plugins/adam-coding-anywhere/skills/skills-doctor`), so it is unavailable in exactly the
  case you most want it.

**Check the SECOND session too, not just the first.** The setup script runs only
when no cached environment exists; afterwards the filesystem is snapshotted and
later sessions skip it. Anthropic's documentation says a snapshot keeps what the
script wrote to disk, which should carry `/root/.claude/settings.json`
forward — but the repos beneath it are re-cloned per session, and whether that
leaves this file untouched is NOT established here. Skills present on the first
session and absent on the second is that failure, and the remedy is to move the
wiring into something that runs every session rather than once: a `SessionStart`
hook in a settings file the session does read. Recorded as an open question
rather than a caveat, because one session cannot answer it.

**The trade that was weighed before adding `systemMessage`:** it prints a line
at every session start, in every repo carrying the bundle, forever. That is real
noise. It wins anyway, on two counts — the line is one sentence, and the failure
it replaces is unbounded (an operator who cannot tell a working install from a
dead one has no way to stop being wrong about it). Showing it only on `DEGRADED`
was the obvious compromise and is the wrong one: a hook that never fires prints
no `DEGRADED` either, so the compromise goes quiet in the only case that
actually needed a signal.

## What a repo still has to do

Delivery stays opt-in and double-keyed: a repo contributes only if it has
committed its own `skills.lock`. A repo without one contributes nothing.

Two locks that name the same skill directory at the **same** digest collapse to
one install, which is the ordinary case when several repos pin one registry. Two
that name it at **different** digests install neither and say so, naming the
locks that disagree — the fix is to re-pin one of them, not to pick a winner.
See ADR 0007 for why.
