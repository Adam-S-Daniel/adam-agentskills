# One-way doors in this registry

A one-way door is a change that cannot be undone by reverting a commit,
because something outside this repository has already acted on it. This page
is the surface list and the review procedure that `AGENTS.md`'s "One-way doors
get an adversarial round" points to. Give a reviewer this page and the diff,
and nothing else.

## The surfaces

Each item says what breaks downstream when it changes.

1. **The marketplace name** (`adam-agentskills` in
   `.claude-plugin/marketplace.json`). Every machine and claude.ai account
   that added the marketplace, and every `<plugin>@adam-agentskills`
   install, is keyed by it. So is a `renames` map, if one is ever added:
   Claude Code follows it for good and migrates users' settings, so an
   entry can never be taken back.
2. **Plugin names.** There is no `renames` map, so Claude Code cannot follow
   a rename. Plugins the owner enabled on claude.ai and in the Desktop app
   (the account's skills come from this repo-synced marketplace, ADR 0014)
   drop out until enabled again by hand, and each machine needs a fresh
   `claude plugin install`.
3. **Skill basenames and plugin membership.** A consumer `skills.lock` keys
   each skill as `<bundle>/<skill>`, and a skill is invoked as
   `/<plugin>:<skill>`. A rename, a removal and a move between plugins all
   change the key, so the next lock bump drops it from every consumer.
   A skill's frontmatter `name` counts as its basename.
4. **The registry's identity and default layout.** Consumer locks name
   `Adam-S-Daniel/adam-agentskills` and the bootstrap hook reads
   `plugins/<bundle>/skills/<skill>/`. Renaming the repository or moving the
   layout breaks every consumer at once.
5. **Reachability of pinned commits.** A lock pins a commit sha. A history
   rewrite, a force-push or a squash merge can strand a pinned commit, and a
   consumer then fails with `cannot resolve ref`. Merges here use merge
   commits only.
6. **The bootstrap hook and the lock schema.** `.claude/hooks/skills-bootstrap.sh`
   and the `skills.lock` format are copied into consumer repositories; a
   change that an old copy cannot read breaks those repositories until each
   is updated.
7. **Personal data in public history.** This repository is public, so
   personal or sensitive data committed or written in a pull request is
   published for good (its predecessor was retired for this reason, ADR
   0013). The required `privacy` job is the check for this one; ordinary
   commits and pull request text are not a one-way door.
8. **Records keyed by a skill's name elsewhere.** skills-evals'
   `evals/<skill>/` fixtures and results, the explorer data, the
   eval-protection data and `docs/skill-impact.md` are all keyed by
   `<plugin>/<skill>` or the basename. A rename or removal orphans them
   unless the change cleans them up or keeps them on purpose.

`scripts/shipped_names.json` is the ledger: every marketplace, plugin and
`<plugin>/<skill>` name present on 2026-11-09, every name retired before or
since, and any new name a pull request chose to append. It is append-only,
so a retired name is never reused by accident. Names present on `main` count
as shipped whether or not the ledger lists them yet.

Not one-way doors: hook handler names in `hooks/hooks.json`, the
`persistent/*` branches (protected by a ruleset already), and the claude.ai
account store's old uploaded skills, which this repository no longer writes
(ADR 0014; the owner removes leftovers by hand).

## What CI enforces

`scripts/check_one_way_doors.py`, in the required `pytest` job, compares the
base branch with the result of merging the pull request now (not with a
merge-base, which a criss-cross history can make lie):

- **A shipped name that disappears** (items 1 to 3: a removal, a rename or a
  move between plugins; a plugin also counts as renamed when its directory
  stops being `plugins/<name>`, a manifest `name` changes, it is listed twice
  or `metadata.pluginRoot` moves it), **or a retired name that comes back** (a skill by
  basename, in any plugin), fails the check unless the pull request body has
  `Adversarial-review: pass <link>` and no `Adversarial-review: block` line.
  The pass line must be visible paragraph text, not inside an HTML comment
  or a code block, and its link must be on github.com or claude.ai. A block
  line counts anywhere but an HTML comment (code, headings, tables and HTML
  blocks included), in any case, at the start of a line after list, quote,
  heading, table or emphasis markers; one quoted mid-sentence does not. A
  name that disappears must also be in `scripts/shipped_names.json`. A
  changed SKILL.md whose frontmatter Claude Code could read differently (a
  `---` inside a line, a duplicate key, a key-shaped line inside a value, or
  any other line at column 0 that is not a key) counts as losing its name,
  and a SKILL.md that is a symlink or gitlink, sits beside a case variant
  (`skill.md`, as a file or a directory) or carries a second byte-order mark
  does not count as present. A `SKILL.md` directly in a plugin's `skills/`
  makes Claude Code load only that one, so every skill of the plugin counts
  as gone.
- **A new path under `plugins/` or `.claude-plugin/` that a
  case-insensitive file system could merge with another** (two names, or a
  name and a directory, that fold to one; or any non-ASCII name) needs the
  same line: on a Windows or macOS checkout one replaces the other.
- **A changed `renames` map in the marketplace, or any changed
  `.gitattributes`** (`git archive`, and so every lock, applies its
  `export-ignore`, `export-subst`, `ident`, `text`/`eol`, `filter` and
  `working-tree-encoding` rules, directly or through a macro) needs the same
  line.
- **An evals-loop pull request** (`draft-skill/`, `eval-improve/`,
  `retire-skill/`) fails unless it stays inside ADR 0021: opened by the App
  `scripts/loop_prs.json` names, one skill in one plugin, only the
  bookkeeping paths (a create or removal may append only its own skill to
  the ledger), a `skills.lock` that changes only its own skill's entry and is
  re-pinned to a commit in its own history where that skill and every
  `.gitattributes` match the merge result, no change to
  `requirements-dev.txt`, and (unless it is a draft) no older ready loop pull
  request from that App open. Draft or not, it must be text only: in
  SKILL.md's frontmatter only the `description` lines may change (a new skill
  may have only `name`, `description`, `license` and `compatibility`), with
  no comment outside the description, a description that is a plain line,
  one complete quoted line or a `>`/`|` block, no shell line Claude Code
  would run, and nothing Claude Code could read differently; and in the
  skill directory only `SKILL.md`, `PURPOSE.md` or
  lowercase `.md` files outside dot-directories and not named like a Windows device (`aux.md`), as regular files, with no
  two paths that are one file to a case-insensitive file system. Text-only changes auto-merge in any
  plugin (owner decision 2026-11-03); a person who wants anything else takes
  it to a branch outside the loop's prefixes, since a draft could be marked
  ready and armed before a new run of the check registers. `plugin-runtime-review` gates only a plugin's own `hooks/` and
  `bin/`, so it does not hold these.

Before anything in the job runs a pull request's code (the pip install
reads `requirements-dev.txt`, the skill gates run `scripts/`), a bash-only
step refuses a loop pull request that changes any path outside the ones its
lanes write. The check runs twice in the job: before the test suites, and again as the
job's last step with the pull request's body, draft state, arming and base
read afresh, so the run that reports last has read them last. A pull
request retargeted to a new base is checked again by the `edited` run;
until that run registers (seconds), the previous result still shows.
The check is not re-run when the base branch moves: a pull request stacked
on another one (it deletes a skill the other adds) passes against the old
base and is not re-checked once the other merges, unless the ruleset
requires branches to be up to date (it does not today; #137).

A skill body that runs a shell command on load (an exclamation mark then a
backtick span, or a fence opened with three backticks and `!`) is refused in
a loop pull request's SKILL.md by this check, matched the way Claude Code
matches it (after any Unicode space or a byte-order mark, a fence anywhere in
a line, and with `$ARGUMENTS`-style placeholders substituted first). The
required `agent-checks` job's `skill-shell` check covers other pull
requests, with a narrower match, and `disableSkillShellExecution` turns
execution off where it is set.

The line is a record, not proof: anyone who can edit the body can write it.
What makes it worth something is the review behind the link.

Not checked mechanically: skills a plugin would load from anywhere other than
`plugins/<plugin>/skills/<skill>/SKILL.md` (a `commands/` file, or a `skills`
or `commands` path in a manifest or marketplace entry; nothing here uses
them today), and the skills inside a remote plugin such as `cms-platform`
(only its name and source are). Items 4 to 8 have no mechanical check. A pull request that touches one of
them needs the same review and the same line, by the rule below.

## The adversarial review

- **Who.** A separately prompted agent with a fresh context, never a fork of
  the agent that made the change. It may be agent-generated, so removals stay
  autonomous (owner decision 2026-11-09). A person may run it too.
- **Input.** Only the diff and this page. Not the author's reasoning.
- **Job.** Break the change: find what it breaks downstream on each surface
  above. Where the change is one-way, run it against a live migration in a
  scratch environment, with a negative control
  ([E4](experiments/E4-federated-bundle-delivery.md) explains why).
- **When it is done.** Run rounds until two consecutive rounds find nothing
  new of equal or higher severity than the worst finding so far. A round's
  fix can introduce a new defect (of the four rounds that hardened
  `skills-bootstrap.sh`, two found defects the previous fix introduced), so
  a fix starts the count again.
- **Record.** Put the result in the pull request body:

  ```
  ## Adversarial review

  <rounds run, findings and how each was resolved>

  Adversarial-review: pass <link to the review or run>
  ```

  Write `Adversarial-review: block <link>` instead when a finding is still
  open; the check then fails until a later pass replaces it. Edit the body,
  then re-run the `pytest` job, which reads the body from the API.

## Automatic paths that cross these doors

- **The evals loop's pull requests** (ADR 0021): removals, accepted
  improvements and new skills merge by auto-merge. A removal needs the review
  line like any other change; skills-evals' removal reviewer writes it
  (skills-evals#917).
- **The consumer lock bumper** (`_agent-guidance`'s nightly bump of every
  consumer `skills.lock`) carries a merged rename or removal into every
  consumer. A lock that loses a skill key is a one-way door downstream.
  Today the bumper refuses only a lock or bundle that loses every skill;
  once _agent-guidance#488 lands it will hold any bump that drops a skill
  key for the same review instead of auto-merging it.
