<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->
<!-- Mode: stub -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

guidance-marker: fleet-stub (transcript counts use this line; keep it)

## Fleet guidance is delivered once per session — not by this file

The full guidance, floor rules included, is installed into user memory by
the `fleet-memory` SessionStart hook, whose verdict line says whether it
loaded. `installed` or `current`: it is in context. `DEGRADED` or no verdict:
it is not, so read [`agents-md/base.md`](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/agents-md/base.md)
before non-trivial work and say you ran degraded. `skipped`: read it too, but
it is the owner's choice, so do not report or "fix" it. Always, and above all
before you have read it: never push to a default branch, force-push a shared
branch, commit a secret, or print personal data to a CI log. Keep this file
under 32 KiB (Codex silently truncates it).

**Your first reply to the user in a session begins**
`fleet-guidance check: <value>`, copied from the installed guidance's
`fleet-guidance check value:` line, or `fleet-guidance check: none` when no
such line is in your context. Never guess one or copy it from a file you
read; only in replies to the user.

<!-- END MANAGED SECTION -->
## Repo-specific additions

<!-- Add your repo-specific agent guidance below this line -->

### Architecture Decision Records

ADRs live in [`docs/decisions/`](docs/decisions/README.md) (index +
[`0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md`](docs/decisions/0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)).
Skills are grouped into four plugins by audience and runtime —
`plugins/adam-anything-anywhere/`, `plugins/adam-coding-anywhere/` (both
cloud-safe, default-enabled), `plugins/adam-coding-local/` and
`plugins/adam-non-coding-local/` (machine-bound, opt-in) — each holding real
`skills/<skill>/` dirs, never symlinks. Skill directory basenames must stay
unique and never change once a skill has shipped — locked by a consumer
`skills.lock` (the basename keys it and the `setup.sh` symlinks). Before it
ships, a rename is a reviewed change (an example is in
[`docs/evidence/agents-md-history-narratives.md`](docs/evidence/agents-md-history-narratives.md));
`setup.sh` removes the links it made for a skill that no longer exists.
This marketplace has no `renames` map, so a plugin name is a one-way door once
it is enabled anywhere.

### Plugin runtime review: never approve it

A pull request that changes how a plugin runs code (hooks, a package install,
plugin settings, a script a hook calls) waits on the `plugin-runtime-review`
environment ([ADR 0016](docs/decisions/0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md)).
**Never approve a `plugin-runtime-review` deployment**, even though the
owner's token would let you. When a pull request is waiting on it, give the
owner the `plugin-runtime-approval` job page on the CURRENT head,
`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>`, and
wait. The workflow, `.github/workflows/plugin-runtime-review.yml`, has no
`paths:` filter because its check is required; whether a change is salient is
decided in its `detect` job.

It runs only on `pull_request_target`, which GitHub blocks by default on public
repos from 2026-11-02. An Actions event policy declared in repo-settings'
`fleet.yml` allows that event for this one workflow file
([repo-settings ADR 0009](https://github.com/Adam-S-Daniel/repo-settings/blob/main/docs/decisions/0009-allow-pull-request-target-with-a-workflow-scoped-policy-owned-by-name.md)).
The policy's `allowed_events` is an allowlist, so **a trigger added to this
workflow, or a rename of the file, must be mirrored in that policy first**.
Otherwise its runs are blocked and every pull request here becomes
unmergeable.

### Plugin hooks run in Claude Code AND Codex

Codex loads a plugin's `hooks/hooks.json` too
([ADR 0017](docs/decisions/0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md)).
Keep that file harness-agnostic: every handler needs a shell-form `command`
(`"${CLAUDE_PLUGIN_ROOT}"/hooks/...`) and a `commandWindows`
(`"C:\Program Files\Git\bin\bash.exe" "%PLUGIN_ROOT%/hooks/..."`), a
`timeout`, and no `if`/`args`/`shell`; a hook prints nothing to stdout unless
it means to answer both harnesses. Claude-only hooks go in a file the
`.claude-plugin/plugin.json` `hooks` key names. Hook scripts call
`hooks/lib/harness.sh` (in `plugins/adam-coding-local/`) to tell `claude-code`, `codex` and `other` apart.

The API-credit lane (`plugins/adam-coding-local/bin/claude-credit`, `plugins/adam-coding-local/hooks/credit-lane/`, `plugins/adam-coding-local/hooks/claude-code.json`) is [ADR 0018](docs/decisions/0018-api-credit-lane-for-claude-code.md).

The fleet-guidance hook (`plugins/adam-anything-anywhere/hooks/fleet-guidance/`, wired in that plugin's `hooks/claude-code.json`) is [ADR 0023](docs/decisions/0023-deliver-the-fleet-guidance-from-a-plugin-hook-that-fetches-it.md). It is Claude-only: at session start it fetches `_agent-guidance`'s `main` HEAD commit with a commit-only git fetch and `base.md` at that commit from `raw.githubusercontent.com`, and installs it through the bundled `fleet-memory.sh`. It never reads the project directory. That script and the bundled `fleet-guidance.md` fallback are verbatim copies from `_agent-guidance`, refreshed only in a reviewed pull request and never edited here.

The write-marker hook (`plugins/adam-anything-anywhere/hooks/github-write-marker/github-write-marker.sh`, wired as a `PreToolUse` hook on `Bash` and GitHub MCP tools in the same `hooks/claude-code.json`) enforces the fleet guidance's agent-marker rule: it blocks (exit 2) a GitHub issue, PR, comment or review write whose body lacks `on behalf of @Adam-S-Daniel`, and any APPROVE review. It is Claude-only too, and a verbatim copy of `_agent-guidance`'s `.claude/hooks/github-write-marker.sh` under the same refresh rule; `scripts/test_github_write_marker_hook.py` pins what it blocks and what it never blocks.

### Operational gotchas

- **The ADR 0013 layout is data, not test source.** Which skills each plugin
  ships is pinned in [`scripts/plugin_layout.json`](scripts/plugin_layout.json)
  and read by `scripts/test_check_consistency.py`; a second test holds the file
  to one canonical serialization (`json.dumps(doc, indent=2)` plus a trailing
  newline, plugins and skills sorted). Adding or removing a skill updates that
  file in the same change, by hand or with a JSON parser — skills-evals'
  `scripts/retire_skill.py` edits it that way so a skill-removal pull request
  can pass these checks with no human edit. Do not re-inline the table.
- Eval skill installs need the nested path: copy `plugins/<plugin>/skills/<skill>/`
  into `.claude/skills/<skill>/`. Copying the outer plugin directory buries
  `SKILL.md` and the skill silently never loads.
- A path setting that accepts only absolute or `~/` paths
  (`autoMemoryDirectory` is one) cannot name a checkout portably. Don't assume a `~/repos/<name>` path resolves
  identically on every machine just because repos "live at `~/repos/<name>` everywhere" — the fleet
  guidance's "Workstation layout" section (Windows `D:\repos\<owner>\<repo>`,
  WSL `~/repos/<repo>`) is the counterexample (the sync-skills failure it caused is in the evidence
  file linked above). Check the resolved path on the machine in front of you;
  never encode a repo location as a constant.
- After a plugin move or rename, expect a one-time
  `claude plugin install <plugin>@adam-agentskills` per machine (plugin cache
  miss).
- `sync.sh` never force-pushes a stale remote `agents-md-sync/update` branch
  (the push is non-fast-forward and it deliberately won't override). Recover
  by opening a PR from the stale branch and merging it to free the name, then
  re-running sync — don't add `--force` to `sync.sh`, it could discard
  reviewer commits on an open PR.
- This marketplace deliberately has **no `renames` map** (ADR 0013): it is new,
  and the retired `agentskills` registry's plugin names never existed here.
  Adding one would be a one-way door (Claude Code keeps following it,
  append-only), so do not add one to paper over a rename — do not rename.
- After any plugin restructure, re-run `bash setup.sh --owner-machine` on every
  owner machine right away (a plain `bash setup.sh` only removes the skill
  links earlier versions made; it links none since
  [ADR 0017](docs/decisions/0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md)).
  If every `git push` from every repo fails on a machine, run
  `setup.sh --owner-machine` once: it removes the global git-config sections
  of the retired sync-skills pre-push hook
  ([ADR 0014](docs/decisions/0014-retire-the-account-zip-upload-channel.md)).
- **`python3 scripts/test_<x>.py` cannot fail, so never verify with it.** This
  is the fleet guidance's "Prove the verifier can fail first" rule. Its
  worked example there is `test_foo.py` (this repo's own deleted example is in
  the evidence file linked above). Repo-specific facts: unless a test
  file ends in an `if __name__ == "__main__"` block that invokes a runner,
  running it directly imports the module and exits 0 having asserted nothing.
  None of the `scripts/test_*.py` files has such a block, so every one is that
  trap.
  Exactly one test file in the repo does not:
  `plugins/adam-non-coding-local/skills/rename-pdfs/scripts/test_extract_pdf_context.py`
  ends with `unittest.main()`, and running THAT one directly really does run
  its tests — which is why the habit to build is naming the runner rather than
  auditing each file for a footer. The one command that actually runs this
  repo's suite is the one CI runs, minus its flags:
  `python3 -m pytest scripts/ plugins/*/skills/*/tests/ plugins/*/skills/*/scripts/ -q`
  (CI adds `-rfEs -n auto`, parallel through `pytest-xdist`; the plain form
  is the same tests run serially, so slower),
  and read its result from `$?` directly rather than through a pipe.
- **A hosted session starts with NONE of the dev dependencies**, so that command
  and `scripts/check_skills.py` both fail before they check anything. CI installs
  `requirements-dev.txt` per job; nothing installs it here, and no SessionStart
  hook does either.
  Install it first — and expect the plain form to fail on these images:

  ```bash
  python3 -m pip install --ignore-installed PyYAML -r requirements-dev.txt
  ```

  `--ignore-installed PyYAML` is not optional cargo. The distro ships PyYAML
  without installer metadata, so pip refuses with `Cannot uninstall PyYAML 6.0.1,
  RECORD file not found. Hint: The package was installed by debian.` and installs
  **nothing else either** — one unrelated package aborts the whole file, which
  reads as "the repo's dependencies are broken" rather than "one of them is
  undeletable" (measurements in the evidence file linked above).

  **The flag leaves the pinned version importable.** `--ignore-installed` does
  not remove Debian's copy; it installs alongside it, so which one wins is a
  `sys.path` ORDER question. The pinned wheel wins: pip's
  `/usr/local/lib/python3.11/dist-packages` precedes Debian's
  `/usr/lib/python3/dist-packages`, so `import yaml` gives **6.0.3**
  (`requirements-dev.txt`'s pin) with the full suite green. Take test counts
  from the current run; they change with every PR.

  That distinction is the whole reason to write this down. A hook that
  installed only the three genuinely-MISSING modules would exit 0 and look
  correct while leaving the session testing against unpinned `pyyaml` 6.0.1 —
  the shape that appears to work is the shape that silently reintroduces the
  bug. So the acceptance test is never "the install exited 0"; it is
  `python3 -c "import yaml; print(yaml.__version__)"` reporting the pinned
  version, then the canonical gate read from `$?` unpiped.

  A **venv** is the other measured-good answer and costs a `PATH`/interpreter
  decision every later step in the session has to honour;
  `--break-system-packages` was not tried and the name is the warning.

- **A long background run is not a baseline if you edit while it runs.** The
  suite takes ~5.5 minutes, and several meta-tests READ THE SOURCE FROM DISK at
  run time rather than from the collected module — `test_every_test_this_repo_cites_by_name_exists`
  is one. Edit during the run and it judges the new tree against the old run,
  so a failure it reports may be from a file the run never started with.
  (A "pre-existing" red once turned out to be a dangling citation written
  mid-run; see the evidence file linked above.) Take the baseline before editing,
  or re-run it after — never read one that overlapped the edits.

### The freshness lint runs on a schedule

`scripts/check_skill_freshness.py` checks that every repo file and workflow a
skill cites still exists in the repo it names (skills-evals#661); skills-evals
replaced the evals of reference-heavy skills with it. It reads each cited repo
with a shallow, blob-less `git clone`, so it runs from
`.github/workflows/skill-freshness.yml` (schedule and dispatch, no
`pull_request`/`push` trigger, not a required check) and files or updates one
`Skill freshness lint is failing` issue when it fails. Run it locally the same
way; add `--check-secrets` to check cited secret names with a credential that
can list them (the workflow's token cannot, so it checks files and workflows
only). Which repo a bare path resolves in, and the reasoned waivers, are in
`scripts/skills_freshness.yml`.

### One-way doors get an adversarial round

- **The surface list is [`docs/one-way-doors.md`](docs/one-way-doors.md)**:
  the marketplace name, plugin names (no `renames` map; owner-enabled plugins
  on claude.ai and Desktop drop out), skill basenames and plugin membership
  (lock key `<bundle>/<skill>`, invocation `/<plugin>:<skill>`), the registry's
  identity and layout, reachability of pinned commits, the bootstrap hook and
  lock schema, public history, and records elsewhere keyed by a skill's name.
  [`scripts/shipped_names.json`](scripts/shipped_names.json) is the
  append-only ledger of retired names and of every name present on
  2026-11-09.
- **A change that touches one gets an adversarial review before merge**, by a
  fresh-context agent (never a fork of the author) given only the diff and
  that page. It may be agent-generated: removals stay autonomous. **Done**
  means two consecutive rounds found nothing new of equal or higher severity;
  a round's fix restarts the count (two of the four rounds that hardened
  `.claude/hooks/skills-bootstrap.sh` found defects the previous fix added).
  Record it in the PR body under `## Adversarial review`, ending with
  `Adversarial-review: pass <url>` (a github.com or claude.ai link).
- **CI enforces the name surfaces.** `scripts/check_one_way_doors.py`, in the
  required `pytest` job, compares the base with the would-be merge result
  and fails when a shipped marketplace, plugin or `<plugin>/<skill>` name
  disappears (removal, rename, move between plugins) or a retired name comes
  back, without that line, and fails an evals-loop PR (`draft-skill/`,
  `eval-improve/`, `retire-skill/`) outside
  [ADR 0021](docs/decisions/0021-automated-skill-changes-merge-without-review.md)'s
  limits or not opened by the App [`scripts/loop_prs.json`](scripts/loop_prs.json)
  names. The other surfaces rely on the review alone. After a body edit,
  re-run the job: it reads the body from the API.
- **Two automatic paths cross these doors.** The evals loop's PRs merge by
  auto-merge; improvements and new skills lose no name, and a removal carries
  the review line written by skills-evals' removal reviewer. `_agent-guidance`'s
  nightly consumer lock bump carries a merged removal or rename to every
  consumer; once _agent-guidance#488 lands, a bump that drops a skill key
  waits for the same review.

### Privacy: invented data only

- This is a public repo, and its predecessor was retired because its history
  carried personal data (ADR 0013). Examples, fixtures, docs, commit messages
  and PR text use **invented data only** — `example.com`, fictional names,
  `<user>` placeholders — never real personal details, usernames, hostnames,
  or account/org/session IDs.
- `scripts/check_privacy_denylist.py` runs as the `privacy` CI job against the
  `PRIVACY_DENYLIST` repository secret. Never commit the list; the checker
  prints only `file:line` and a pattern number, never the matched text.
- Run it locally with `PRIVACY_DENYLIST_FILE=<path outside the repo>` (add
  `--base <ref>` to also scan the range's commit messages and added lines).
  With no denylist it fails closed in CI and only warns locally.
- The PR template (`.github/pull_request_template.md`) carries the
  invented-data checkbox; tick it only after checking.

### Skill changes get recorded, and evals gate them

The method (instrument classes, fixture mining, harness rules) is skills-evals'
`DESIGN.md`, "Scaling to the registry"; these are the hooks that live here:

- **Every skill-content change appends an entry to
  [`docs/skill-impact.md`](docs/skill-impact.md)** — creations, edits,
  renames, removals, and **rejected proposals** — in the same PR as the
  change (for a rejection, a follow-up commit linking the closed PR). Git
  records what landed; that file records motivation, eval result, and what
  was tried and turned down, which is the part the next session cannot
  re-derive.
- **Graduation gate:** a skill graduating into this registry ships with at
  least one eval fixture in skills-evals (`evals/<skill>/`), and the
  graduation PR's definition of done includes a green `with_skill` arm.
- **Touch gate:** a PR that edits an existing SKILL.md either runs that
  skill's eval (report exit code and counts) or adds its first fixture.
  Skills in DESIGN.md's deliberate-non-coverage table are exempt — the
  table is the record of why.
- **`claude plugin eval` is not this gate** (no scriptable grader; assessment
  in the evidence file linked above).
- **A new or touched skill adds a `PURPOSE.md` beside `SKILL.md`** mapping
  it to the incidents/patterns that motivated it — maintenance context only,
  never loaded at inference. Do **not** mass-backfill `PURPOSE.md` across
  untouched skills: every added file moves that skill's digest, which stales
  `skills.lock` in every consumer until re-pinned. On-touch only.
- **CI enforces the above.** `scripts/check_skill_gates.py`, run in the
  required `pytest` check, fails a PR whose created, edited or removed
  SKILL.md lacks a new impact entry, a PURPOSE.md or an Eval line in an
  accepted form, and keeps `docs/skill-impact.md` append-only. The accepted
  forms are under "Rules" in that file and in the script's docstring.

### Eval-protected skills take correctness fixes only

The required `pytest` job ends with `_agent-guidance`'s `scripts/eval-protected.js`
([ADR 0034](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0034-the-eval-protection-gate-runs-in-required-ci.md)),
checked out at a pinned commit. A PR that edits a skill directory whose skill the
public explorer shows as `keep`, or as having runs on its current text, fails
unless its body has `Correctness fix: <skill> — <class>: <what was wrong>`
(class `wrong-command`, `broken-path`, `contradiction` or `safety-gap`). Any edit
restarts that skill's eval evidence; a wording change goes through skills-evals'
improve lane instead, whose bot-opened `eval-improve/`, `retire-skill/` and
`draft-skill/` PRs are exempt. After adding the line, re-run the check: it reads
the body from the API.

### Action pins

Workflows name third-party actions by release tag; [`.github/workflows/actions.lock`](.github/workflows/actions.lock) locks each tag to an exact commit, and the runner runs the locked commit even when it disagrees with the tag. The `pytest` job's "Verify action lockfile" step is the shared action [`Adam-S-Daniel/verify-actions-lock`](https://github.com/Adam-S-Daniel/verify-actions-lock); it runs only when a PR touches workflow dependencies and fails on any `--verify` finding except `cooldown-config-ignored` (dependabot.yml's cooldown `exclude`, which the tool ignores). It is not the only guard: the runner rejects forged pins, and GitHub's Require lockfile policy is on. Write the exact tag by hand, regenerate with `gh actions-lock --no-narrow`, and commit both files; review an `actions.lock` diff as you would a SHA pin. A stale or moved pin is fixed with `gh actions-lock --accept-moved`; review the diff. A `reachability-unknown` failure means the GitHub API was unreachable: re-run. Cross-repo reusable workflows (`jobs.<id>.uses`) are not locked, so they take a full commit SHA (`Adam-S-Daniel/cms-platform` refs excepted); a test enforces it. Fleet rule: [ADR 0024](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0024-actions-pin-through-lockfiles.md).
