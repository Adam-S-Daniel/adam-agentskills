# adam-agentskills

Adam Daniel's reusable agent skills, packaged as **Claude Code plugins** and as
cross-agent skills that follow the
[Agent Skills specification](https://agentskills.io/specification).

Skills are grouped into four **plugins** under `plugins/<plugin>/skills/<skill>/`,
named by audience and runtime (`[adam|adam-private]-[coding|non-coding|anything]-[local|anywhere]`;
see [ADR 0013](docs/decisions/0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)),
and the repo root is a Claude Code **plugin marketplace**, `adam-agentskills`
(`.claude-plugin/marketplace.json`). The marketplace also publishes one
**federated plugin** — `cms-platform`, whose plugin root is that repo itself, so
it keeps its skills, its cadence and its review path there while this stays the
one marketplace to add. The exact same `SKILL.md` files are consumed
unchanged by Codex, Cursor, VS Code, and any other agent that reads the Agent Skills
format — so a skill is authored once and installs everywhere.

This repo is the **canonical upstream registry** for reusable skills. For where
skills live across repos, the public/private rule, and how a skill graduates into
this registry, see [`STRATEGY.md`](STRATEGY.md).

## Install — Claude Code

One command, on **Claude Code v2.1.275 or later** — it resolves the source,
offers to add the marketplace, then installs the plugin:

```bash
/plugin install adam-coding-anywhere --marketplace Adam-S-Daniel/adam-agentskills
```

On Claude Code versions before v2.1.275, add the marketplace first and install
by `plugin@marketplace`:

```bash
/plugin marketplace add Adam-S-Daniel/adam-agentskills
/plugin install adam-coding-anywhere@adam-agentskills
```

Either way the marketplace is added once, so the other plugins install from it
by name:

```bash
/plugin install adam-anything-anywhere@adam-agentskills
/plugin install adam-coding-local@adam-agentskills        # opt-in, machine-bound
/plugin install adam-non-coding-local@adam-agentskills    # opt-in, local Cowork
/plugin install cms-platform@adam-agentskills   # federated — fetched from Adam-S-Daniel/cms-platform
# …or browse and pick interactively:
/plugin
```

Skills are namespaced by plugin — invoke them as `/<plugin>:<skill>`, e.g.
`/adam-anything-anywhere:finding-unknowns`. To refresh later, update the
catalog and then the installed plugin caches explicitly:

```bash
claude plugin marketplace update adam-agentskills
claude plugin update adam-coding-anywhere@adam-agentskills
```

Starting with Claude Code v2.1.232, installing by `plugin@marketplace` also
refreshes the registered marketplace catalog automatically.
Run `claude plugin update` for each installed plugin that should be refreshed;
update `cms-platform@adam-agentskills` separately to refresh the federated
plugin from its own repository. Catalog refresh alone does not update installed
plugin caches. In Claude Code v2.1.268 and later, installs, enables, and
disables made in the interactive plugin menu apply when the menu closes. The
menu reloads plugins when it is safe; otherwise changes may wait for the next
session. Shell installs and updates can also need a new session or
`/reload-plugins`. See the [v2.1.232 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.232),
[v2.1.268 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.268),
and [plugin update guidance](https://code.claude.com/docs/en/discover-plugins#keep-plugins-updated).

### Plugins

Membership follows two questions: **who** the skill is for (coding work,
non-coding work, or anything) and **where** it can run (only on the owner's
machines, or anywhere). Each skill lives in exactly one real directory; no
plugin contains a symlink.

| Plugin | Default | For | Surfaces |
| --- | --- | --- | --- |
| `adam-anything-anywhere` | enabled | general thinking and writing skills | claude.ai web, iOS, Chrome, Desktop, and Claude Code |
| `adam-coding-anywhere` | enabled | CI, GitHub and skill-delivery skills | Claude Code terminals and cloud sessions |
| `adam-coding-local` | opt-in | machine-bound coding skills (WSL/Windows homes, a signed-in browser) | Claude Code on the owner's machines |
| `adam-non-coding-local` | opt-in; on in Windows, off in WSL/Linux on the owner's machines ([below](#which-bundles-on-which-os)) | PDF and Fastmail skills | the Claude Desktop app's local Cowork |

Sensitive skills live in the private sibling registry,
`Adam-S-Daniel/adam-agentskills-private`, as `adam-private-*` plugins.

`cms-platform` is the exception to that layout: it is **federated**, not
vendored — its entry names `Adam-S-Daniel/cms-platform` as the plugin root, so
its skills are never copied into this repo and never drift from it. It is
opt-in because it is platform-scoped (useful in cms-platform's own consumer
sites, noise everywhere else) and every enabled skill costs always-on context.

**Publishing and delivering are independent axes, on purpose.** The marketplace
says a plugin *exists* and *where it lives*; a consumer's
[`skills.lock`](skills.lock) says which plugins *that repo* installs and pins.
So `cms-platform` is published here and delivered by nothing in this repo's own
lock — which carries only the cloud-safe pair, `adam-anything-anywhere` and
`adam-coding-anywhere` — and that is the intended state rather than an
omission: the platform plugin is opt-in per consumer, and which plugins
another repo installs is not this repo's business.
Nothing cross-checks the two files against each other, and nothing should; a
gate coupling them would be enforcing a rule that isn't true. Written down here
because a deliberate gap nobody recorded is indistinguishable from an oversight.

**A federated `source` carries exactly `source` and `repo`** — never a
`ref`, `commit`, `version` or `path` key — and `scripts/check_consistency.py`
fails the build on one rather than leaving a reader to spot it. Checked against
the CLI's own plugin-source schema rather than assumed: a `github` plugin source
declares `repo` plus optional `ref` and `sha`, and nothing else. `path` belongs
to the separate *marketplace* source schema and `version` to the `npm`/`pip`
variants, so on a github source those two — and `commit`, and `branch` — are
undeclared and silently discarded. `ref`/`sha` may well be honoured, but this
repo refuses them too, as policy: pinning a federated plugin to a revision is
`skills.lock`'s job, where the pin is an immutable commit plus a sha256 per
skill that this repo can actually verify. A key that reads as a pin while
nothing here stands behind it is worse than no key at all.

This marketplace has **no `renames` map**: it is new, and the plugin names of
the retired `Adam-S-Daniel/agentskills` registry never existed in it. A plugin
name is a one-way door once enabled anywhere — renaming it means every app
that enabled it loses it.

**Migrating from the retired `agentskills` registry:** re-pin each consumer
repo's `skills.lock` to `Adam-S-Daniel/adam-agentskills` and the new plugin
names, then on each of the owner's durable machines re-run
`bash setup.sh --owner-machine` (in each home, Windows Git Bash and WSL).
That registers the new marketplaces, enables the new plugins and writes
`false` for the retired registry's plugins (`adam`, `adam-local`, `fastmail`,
`adam-personal`, `adam-private`), so nothing loads twice; it leaves the old
marketplace entries themselves in place — remove those with
`claude plugin marketplace remove`. Re-running it also cleans up the retired global sync-skills
pre-push hook ([ADR 0014](docs/decisions/0014-retire-the-account-zip-upload-channel.md)), which
otherwise points at a script that no longer exists and **blocks every `git push` from any repo**.

Available skills:

<!-- BEGIN GENERATED PLUGIN TABLE -->
| Plugin | Invocation | Description |
| --- | --- | --- |
| `adam-anything-anywhere` | `/adam-anything-anywhere:finding-unknowns` | Surface and resolve the ambiguities in a task before, during, and after implementation — the blind-spot pass, the self-interview, reference-driven specs, implementation notes, and a post-hoc explainer or quiz. |
| `adam-anything-anywhere` | `/adam-anything-anywhere:writing-adrs` | Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:debug-github-workflows` | Debugging GitHub Actions workflow failures. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:disarm-inherited-reach` | Sever a scratch tree's inherited push path to the real repository the moment the tree exists, before anything runs in it. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:github-actions-repo-settings` | Configure and enforce GitHub repository security settings as code: require actions to be pinned to full-length commit SHAs, require approval for all outside collaborators' fork pull-request workflow runs, and protect the default branch via a repository ruleset. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:review-bash-ci-reliability` | Review bash scripts for CI/CD reliability issues. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:skills-doctor` | Diagnose skill DELIVERY health for the current session: name the surface, diff the expected set in `skills.lock` against what actually loaded (the session's own skill listing, `~/.claude/skills/`, the account `synced/manifest.json`, `claude plugin list`), attribute every skill to the registry and bundle it came from by reading the bootstrap hook's own install record rather than guessing, and flag silent shadowing and dangling payload references. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:vendor-release-impact-issues` | File or rewrite GitHub issues that track how a vendor's or upstream project's release notes may affect your repos. |
| `adam-coding-anywhere` | `/adam-coding-anywhere:workflow-path-audit` | Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. |
| `adam-coding-local` | `/adam-coding-local:launch-top-level-claude-session` | Launch a new, top-level, interactive Claude Code session in a new Windows Terminal tab — native Windows or inside WSL — in a chosen folder, optionally remote-controllable and optionally seeded with an initial prompt or a handoff file. |
| `adam-coding-local` | `/adam-coding-local:migrate-claude-memory` | Inventory, clean up, and migrate Claude Code auto-memory stores under ~/.claude/projects/<munged-path>/memory/ on this machine. |
| `adam-coding-local` | `/adam-coding-local:sync-cc-settings-between-wsl-and-windows` | Sync Claude Code settings.json between a Windows home and a WSL home. |
| `adam-coding-local` | `/adam-coding-local:windows-elevation-from-wsl` | Handle "Access is denied" from powershell.exe or pwsh.exe run inside WSL — Register-ScheduledTask / Set-ScheduledTask on a RunLevel=HighestAvailable task, a service change (Set-Service, Stop-Service, New-Service), an LSA rights grant such as "Log on as a batch job" (SeBatchLogonRight, secedit, ntrights), an HKLM registry write, or any other change to Windows state from a WSL session. |
| `adam-non-coding-local` | `/adam-non-coding-local:add-from-address` | Add one or more email addresses to a Fastmail account as selectable "From" (sending) identities by triggering the add-from-address GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `adam-non-coding-local` | `/adam-non-coding-local:add-received-from-addresses` | Discover which of a Fastmail account's own alias addresses are worth being able to send from, and add them as "From" identities, by triggering the add-received-from-addresses GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `adam-non-coding-local` | `/adam-non-coding-local:compare-pdfpairs` | Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to determine whether they would produce identical printouts and whether their embedded text differs — e.g. to safely delete redundant "-signed" or "-needsocr" duplicates. |
| `adam-non-coding-local` | `/adam-non-coding-local:fastmail` | Automate Fastmail email workflows via a local browser session. |
| `adam-non-coding-local` | `/adam-non-coding-local:ocr-pdfs` | OCR scanned PDFs with OCRmyPDF, preserve the originals, and review page appearance and searchable text before replacing any files. |
| `adam-non-coding-local` | `/adam-non-coding-local:pdf-ocr-audit` | Audit PDF files to determine whether OCR (optical character recognition) is needed to make them fully text-searchable. |
| `adam-non-coding-local` | `/adam-non-coding-local:rename-pdfs` | Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names, proposing each name from the PDF's own content and prompting for per-file confirmation or edit before applying. |
| `cms-platform` | `/cms-platform:<skill>` — skills live in [Adam-S-Daniel/cms-platform](https://github.com/Adam-S-Daniel/cms-platform) | The cms-platform site machinery's own skills, federated from that repo rather than mirrored here: Decap /admin config rendering, AWS bootstrap and PR preview environments, Playwright e2e, CI watcher loops, stuck-PR triage, and the platform release/consumer-bump flow. |
<!-- END GENERATED PLUGIN TABLE -->

## Install — Codex, Cursor, and local use

These tools discover skills from per-agent directories rather than a marketplace.
Run `setup.sh` once **in each environment** (Windows Git Bash *and* WSL — they have
separate `$HOME`s):

```bash
bash setup.sh
```

On Windows, run it from Git Bash, or from PowerShell by full path:
`& 'C:\Program Files\Git\bin\bash.exe' setup.sh`. A bare `bash`
in PowerShell is WSL's launcher (`C:\Windows\System32\bash.exe`), so it sets up
the WSL home and leaves the Windows one untouched.

That is all anyone else needs. It links every skill under `plugins/*/skills/*` into the standard skill homes:

- `~/.agents/skills/` — Codex (and the generic agents dir)
- `~/.agent/skills/`
- `~/.cursor/skills/`

**Claude Code is deliberately not in that list** — it's served by the marketplace
above. Linking the same skills into `~/.claude/skills` too would double-load them
(once as a namespaced plugin, once as a personal skill), so `setup.sh` now removes
any such links it created in earlier versions. Background and rationale:
[`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](docs/2026-06-05-skill-discovery-and-centralized-strategy.md).

> **Gemini / Antigravity was retired as a target (2026-08-14).** That is an owner
> **scope decision, not** a finding that those paths were dead — Gemini/Antigravity is
> four separately-versioned products, three of which read skills from three
> *different* directories, and the Antigravity IDE genuinely does read
> `~/.gemini/antigravity/skills`. So this removes a link that was doing real work.
> Because un-listing a home leaves the old links behind — still feeding an
> unmanaged copy of the skill set, and dangling as soon as a skill is renamed —
> `setup.sh` also **sweeps** `~/.gemini/skills/` and
> `~/.gemini/antigravity/skills/`: it removes only links that resolve into this
> repo's `plugins/` tree, then removes each directory only if that left it empty.
> Your own files and links there are untouched. Re-run `bash setup.sh` on any
> machine set up before this change.

On Windows it uses directory junctions (`mklink /J`) — no admin required. The script
is idempotent and migrates the old whole-directory links left by earlier versions.

### Owner machines only: `--owner-machine`

`bash setup.sh --owner-machine` (or `AGENTSKILLS_OWNER_MACHINE=1 bash setup.sh`)
additionally configures a machine the way the registry's owner runs it, and
is **not** for anyone else's machine:

- removes the retired global sync-skills pre-push hook (`hook.sync-skills-reminder`
  and `hook.sync-skills-private-reminder` in `git config --global`) if a
  previous run left it registered — idempotent, and a no-op on a machine that
  never had it ([ADR 0014](docs/decisions/0014-retire-the-account-zip-upload-channel.md));
- converges `~/.claude/settings.json`: registers this marketplace and the
  owner's **private** one, enables the owner's plugins
  (`adam-anything-anywhere`, `adam-coding-anywhere`, `adam-coding-local`,
  `adam-private-anything-anywhere`), writes `false` for the account-synced
  copies and for the retired registry's plugins, and sets
  `syncClaudeAiSkills: false` — which turns off claude.ai account skills in
  that machine's terminals ([ADR 0010](docs/decisions/0010-let-pinned-channels-own-the-terminal.md),
  [ADR 0013](docs/decisions/0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md));
  and applies the per-OS bundle policy below.

Without the flag neither step runs, and the script says how to opt in.

#### Which bundles on which OS

The two `-anywhere` bundles and `adam-coding-local` are the same on every
durable machine. `adam-non-coding-local` is not, so the default for any new
machine of the owner's is:

| Bundle | Windows | WSL / Linux | macOS |
| --- | --- | --- | --- |
| `adam-anything-anywhere`, `adam-coding-anywhere`, `adam-coding-local` | on, from the marketplace | on, from the marketplace | on, from the marketplace |
| `adam-non-coding-local` | **on**, left as it arrives | **off** | untouched (no decision) |

Why: its seven skills (add-from-address, add-received-from-addresses, fastmail,
ocr-pdfs, pdf-ocr-audit, rename-pdfs, compare-pdfpairs) are Windows, browser
and document workflows. `/skill-doctor` on WSL measured them at about 1.3k
tokens of always-on descriptions per turn and zero uses in 7 days (2026-10-05).

On WSL and Linux, `setup.sh --owner-machine` writes `false` for
`adam-non-coding-local@synced` (the claude.ai account sync, which brings the
bundle in even with `syncClaudeAiSkills: false`) and for
`adam-non-coding-local@adam-agentskills` (a marketplace install), because
disabling one source leaves the other loading. It installs nothing, and it
writes `false` on every run, so a manual `claude plugin enable` on Linux lasts
only until the next run. On Windows it writes nothing and never turns off a
copy it finds. Rationale and alternatives:
[ADR 0015](docs/decisions/0015-turn-the-non-coding-local-bundle-off-on-linux-and-wsl.md).

> Codex reads `~/.agents/skills`; that link is what makes these skills available in
> Codex. See the [Codex skills docs](https://developers.openai.com/codex/skills).

### Agent Plugins v1 — the root `plugin.json`

Each plugin ships **two** manifests, on purpose:

| File | Read by |
| --- | --- |
| `plugins/<plugin>/plugin.json` | [Agent Plugins 1.0.0](https://agent-plugins.org) clients — Codex, VS Code, Cursor, GitHub Copilot |
| `plugins/<plugin>/.claude-plugin/plugin.json` | Claude Code |

Claude Code is **not** an Agent Plugins conformant client and is absent from the
spec's client roster, so it keeps its own manifest; the two coexist rather than
one replacing the other. Neither declares the skills — the spec discovers them
by convention at `<plugin-root>/skills/`, which this repo's layout already
satisfies. Because both are shipped, both can drift, so
`scripts/check_agent_plugins.py` validates the root manifests against the
schema **vendored** at `schemas/agent-plugins-1.0.0-plugin.schema.json` (the
spec repo publishes no tags or releases, so there is nothing to pin a fetch to)
and cross-checks `name` + `version` between each pair. It runs in CI.

The schema is closed, and requires only `$schema` and `name`. The
marketplace-only keys `category` and `defaultEnabled` are **invalid** in a root
manifest, and there is no `skills` key at all.

Measured minimum client versions:

- **Codex ≥ 0.147.0** — established by source-diffing release tags
  `rust-v0.146.0` vs `rust-v0.147.0`. It accepts **only** the exact canonical
  `$schema` string; anything else is rejected as "unsupported Agent Plugins
  schema".
- **VS Code ≥ 1.131.0** — established by tag-bisecting
  `src/vs/platform/agentPlugins/common/agentPluginParser.ts`. Never announced
  in the release notes.
- **Cursor** — reads it (verified in the newest CLI build); no changelog names
  it and no minimum is established.
- **GitHub Copilot** — has read a root `plugin.json` as its own long-standing
  format; declaring `$schema` is what opts into Agent Plugins v1 semantics
  (GA 2026-08-12). No minimum established; measured working on 1.0.79 and 1.0.80.

**Codex needs no extra marketplace file.** Its marketplace search path includes
`.claude-plugin/marketplace.json` alongside `.agents/plugins/marketplace.json`,
so the file this repo already has is the one it reads — verified live
(`codex plugin add <plugin>@<marketplace>` installed every skill of the
plugin; measured against the retired `agentskills` registry, whose
marketplace file had the same layout).

<!-- Do NOT add .agents/plugins/marketplace.json. Codex 0.147.0's
     MARKETPLACE_MANIFEST_RELATIVE_PATHS is [".agents/plugins/marketplace.json",
     ".agents/plugins/api_marketplace.json", ".claude-plugin/marketplace.json",
     ".cursor-plugin/marketplace.json"] — this repo's existing
     .claude-plugin/marketplace.json is already read. A second marketplace file
     would create a second source of truth for zero gain. -->


## Hosted agents — Claude Code on the web, claude.ai

Hosted sessions start with **no user plugins and no marketplace adds** — but
`~/.claude` is not empty: the claude.ai account store is already present at
`~/.claude/skills/synced/` and loads from turn one (see "The claude.ai account
store" below). The repo clone can additionally *write* into `~/.claude`; that
write is the delivery channel for ephemeral surfaces. What works where:

- **Claude Code on the web / cloud sessions**: files committed to the repo being
  worked on — `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, `.claude/skills/`,
  `.claude/memory/` — are all picked up. Repo-declared `extraKnownMarketplaces` +
  `enabledPlugins` are honored for *local* teammate sessions, but as of 2026-07
  they do **not** install anything in cloud sessions (verified by experiment —
  see [ADR 0001](docs/decisions/0001-consolidate-plugins-into-bundles.md),
  "Experiment evidence"; matches anthropics/claude-code#32606).
- **The bootstrap hook** — [`.claude/hooks/skills-bootstrap.sh`](.claude/hooks/skills-bootstrap.sh),
  armed as a `SessionStart` hook in `.claude/settings.json`. It fetches the
  registry and copies the locked plugins' skill directories into `~/.claude/skills`, so
  the skills are live for **turn one** of a hosted session and are inherited by
  any subagent that session spawns. A committed hook, not a vendored mirror, is
  therefore all a consumer repo needs (experiment
  [E2](docs/experiments/E2-sessionstart-skill-bootstrap.md), incl. why
  `claude plugin install` is *not* a substitute).
  What it installs is pinned and integrity-checked by
  [`skills.lock`](skills.lock) — registry, an immutable commit SHA, and a sha256
  per skill — because fetching instruction text at session start is a
  supply-chain surface; re-pin it with
  `python3 scripts/generate_skills_lock.py --repin`, which inherits the lock's
  registry, bundles and federated `sources` instead of taking them off the
  command line (a bare re-run drops every source the command line does not
  repeat, and exits 0). The hook is a no-op on a durable
  machine (the marketplace install is authoritative there), it skips any skill
  the project already owns in `.claude/skills/` (personal skills shadow project
  ones), and it always exits 0 — a failure downgrades to a one-line
  `skills: DEGRADED — …` notice naming the knob to fix.
  It also **removes** a skill that later leaves the lock, so a withdrawn or
  renamed one stops loading instead of living on under a clean verdict — but
  only one it installed itself and nobody has edited since, tracked in
  `~/.claude/skills/.skills-bootstrap-installed.json` and scoped to the
  registries and bundles the locks still declare. A hand-placed skill, the
  skills of a repo that is **not in this session**, and the account-sync
  `synced/` store are never touched; an edited one is kept and named in the
  verdict rather than deleted.

  In a session opened on several repos it reads **every** repo's lock and
  installs the union, so a repo in the same session is no longer "another
  repo" — its skills are this run's too. Two locks naming one skill directory
  at the same digest collapse to one install; at different digests neither
  installs and the verdict names the locks that disagree. See
  [ADR 0007](docs/decisions/0007-install-the-union-of-every-discovered-lock.md),
  and [`docs/multi-repo-delivery.md`](docs/multi-repo-delivery.md) for the
  wiring such a session needs before any of it runs.
- **Codex Cloud**: invoke the same script during environment setup and
  maintenance with `bash .claude/hooks/skills-bootstrap.sh --codex-cloud` from
  the project root (or set `CLAUDE_PROJECT_DIR` to that root). This explicit
  mode reads only that project's `skills.lock`, verifies the same pinned
  sources and digests, and installs into `~/.agents/skills/`. A project
  without its own lock opts out cleanly, even when a child or the script's
  registry has one. A degraded delivery exits nonzero so setup stops; the
  no-argument Claude hook stays fail-soft. Codex Cloud mode does not write to
  `~/.claude`.
- **The claude.ai account store** is the *only* channel that reaches claude.ai
  chat, Cowork, Claude in Chrome, and mobile, and it also loads in Claude Code
  on the web / cloud sessions alongside whatever the repo delivers; where both
  carry the same skill NAME the hook's copy wins
  ([E5](docs/experiments/E5-account-store-vs-hook-precedence.md)). It can't be
  repo-scoped ([ADR 0002](docs/decisions/0002-limit-account-store-to-repo-independent-skills.md)).
  The account's skills now come from this repo as a repo-synced personal
  marketplace — enabled once on claude.ai and once in the Desktop app, and
  updated by hand ("Check for updates" plus a Desktop restart) — rather than
  from ZIP uploads. The uploader and its drift loop were retired once that
  channel was confirmed on every surface:
  [ADR 0014](docs/decisions/0014-retire-the-account-zip-upload-channel.md)
  (background: [ADR 0013](docs/decisions/0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md),
  [E6](docs/experiments/E6-account-plugin-channel.md)).
- **Memory**: hosted sessions see a repo's git-tracked `.claude/memory/` (see the
  Memory section in [`STRATEGY.md`](STRATEGY.md) and the
  [portable-memory guide](https://github.com/Adam-S-Daniel/claude-memory-map/blob/main/docs/portable-memory.md);
  migrate existing machine-local stores with the `migrate-claude-memory` plugin).

## Repo layout

```
.claude-plugin/marketplace.json       # catalog: 4 local plugins + 1 federated (no renames map)
plugins/
  <plugin>/                           # adam-anything-anywhere | adam-coding-anywhere |
                                      # adam-coding-local | adam-non-coding-local —
                                      # LOCAL plugins only; cms-platform is federated
    plugin.json                       # Agent Plugins 1.0.0 manifest
    .claude-plugin/plugin.json        # Claude Code plugin manifest
    skills/<skill>/SKILL.md           # one real dir per skill (+ scripts/, tests/, hooks/)
schemas/                              # vendored Agent Plugins schema (pinned by sha256)
scripts/                              # checks (consistency, privacy denylist, …) and their tests
docs/decisions/                       # ADRs (see 0013 for the current plugin layout)
setup.sh                              # link skills into per-agent dirs (non-Claude-Code)
```

Validate the marketplace and any plugin with `claude plugin validate <path>`.

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow
> https://agentskills.io/specification and validate with `claude plugin validate`.
> Skills live in plugins grouped by audience and runtime: add a new skill as
> `plugins/<plugin>/skills/<skill>/SKILL.md` in the right plugin —
> `adam-anything-anywhere` for general skills usable everywhere,
> `adam-coding-anywhere` for cloud-safe coding skills, `adam-coding-local` for
> machine-bound coding skills, `adam-non-coding-local` for local non-coding
> ones — no new plugin.json or marketplace entry needed.
> Do **not** use `claude plugin init` — it
> scaffolds into `.claude/skills`, which is not this repo's marketplace layout.
> Never rename skill directories or plugins. Open a PR against `main` in
> https://github.com/Adam-S-Daniel/adam-agentskills. Then fetch and pull in WSL and Windows
> under `~/repos` and `%USERPROFILE%\repos`, and run `bash setup.sh --owner-machine` in both WSL and
> Windows Git Bash so the skills are linked into the standard locations
> (`.agents/skills/`, `.agent/skills/`, `.cursor/skills/`) — Claude Code itself
> uses the marketplace, not `.claude/skills`.
