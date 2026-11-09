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
   install, is keyed by it.
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

`scripts/shipped_names.json` lists every marketplace, plugin and
`<plugin>/<skill>` name that has shipped. It is append-only: a retired name
stays listed, so it is never reused by accident.

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
  `Adversarial-review: pass <link>` and no `Adversarial-review: block` line
  (a block counts anywhere a reader could see it, in any case).
  The link must be on github.com or claude.ai, and the line must be visible
  text, not inside an HTML comment or a code block. A name that disappears
  must also be in `scripts/shipped_names.json`.
- **An evals-loop pull request** (`draft-skill/`, `eval-improve/`,
  `retire-skill/`) fails unless it stays inside ADR 0021: opened by the App
  `scripts/loop_prs.json` names, one skill in one plugin, only the
  bookkeeping paths, no new or changed `hooks` or `allowed-tools` in the
  skill's frontmatter while armed to auto-merge, and (unless it is a draft) no
  older ready loop pull request from that App open. Text-only changes auto-merge in any plugin
  (owner decision 2026-11-03); a skill's own `hooks/` or `bin/` is held by
  the required `plugin-runtime-review` gate.

A pull request retargeted to a new base is checked again by the `edited`
run; until that run registers (seconds), the previous result still shows.

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
  consumer. A lock that loses a skill key is a one-way door downstream;
  _agent-guidance#488 holds such a bump for the same review instead of
  auto-merging it.
