---
name: github-actions-repo-settings
description: >
  Configure and enforce GitHub repository security settings as code: require
  actions to be pinned to full-length commit SHAs, require approval for all
  outside collaborators' fork pull-request workflow runs, and protect the
  default branch via a repository ruleset. The settings-as-code engine, its
  schema, and the fleet fan-out live in the repo-settings repo, which this skill
  points to; it also carries manual `gh api` recipes. Trigger
  when: setting up a new repo, running a security audit, onboarding a repo to org
  standards, enforcing settings across a fleet, or when asked to configure or
  harden Actions security settings. Trigger on mentions of "actions settings",
  "repo security settings", "repo settings as code", "settings drift", "fork
  approval", "outside collaborators", "actions policy", "branch protection",
  "ruleset", "harden repo", "actions event policy", or "pull_request_target
  policy".
compatibility: >-
  Requires the GitHub CLI (gh). Settings-as-code also needs a checkout of the
  repo-settings repo (Python 3 with PyYAML); the manual recipes need only gh.
  Runs in any environment.
---

# GitHub Actions Repo Settings

Configure and enforce GitHub repository security settings. Two ways to use this:

1. **Settings-as-code (recommended)** -- the `repo-settings` repo holds the
   engine, schema, and fleet config that introspect, diff, and apply desired
   state for a single repo or a whole fleet. See sections 1-2.
2. **Manual API recipes** -- one-off `gh api` calls for each setting, plus a UI
   fallback. See sections 3-6.

## Settings enforced

| # | Setting | Purpose | API |
|---|---------|---------|-----|
| 1 | **Require actions pinned to a full-length SHA** | Prevents mutable tag refs; mitigates supply-chain attacks | `repos/{repo}/actions/permissions` |
| 2 | **Require approval for all outside collaborators** | Manual approval before fork PRs from non-collaborators run workflows | `.../actions/permissions/fork-pr-contributor-approval` |
| 3 | **Default-branch protection (ruleset)** | Require PRs, block force-push/deletion on the default branch | `repos/{repo}/rulesets` |

Setting 3 uses a **repository ruleset** rather than classic branch protection,
so the fleet speaks the same primitive as repos managed by other ruleset-based
systems (e.g. cms-platform).

These three rows are what the manual recipes below cover. repo-settings manages
more: merge settings, Dependabot security settings, workflow permissions,
labels, extra rulesets, and Actions event policies. Its `repo-settings/schema.md`
is the authoritative list.

## Key API facts (verified against the live API)

- **Fork-PR approval enum values are the short forms** returned by the GET:
  `first_time_contributors` and **`all_external_contributors`** (= the UI's
  "all outside collaborators"). Older docs showing
  `require_approval_for_all_outside_collaborators` are **wrong**.
- **Private repos** cannot use fork-PR approval (API `422`) and cannot use
  rulesets/branch protection without GitHub Pro (API `403`). Only SHA pinning
  applies to a private repo on a free plan.
- **`Adam-S-Daniel` is a user account; `jodidaniel` is an organization.** We
  still apply everything **per-repo** (we don't rely on org-level rulesets),
  though on the `jodidaniel` **org** an owner *can* also set org-level Actions
  policies and rulesets that layer on top of — and may override or further
  restrict — the per-repo settings; on the `Adam-S-Daniel` user account there is
  no such org layer, so it's purely repo-level. The account/org split also
  matters for automation auth: a fine-grained PAT is
  scoped to a **single** owner, so one PAT cannot administer both. Cross-account
  automation should use a **GitHub App installed on both** (see section 2).
- Writing any of these needs **repo-admin** (fine-grained PAT with
  "Administration: read and write" + "Actions: read and write", or a GitHub App
  with the same permissions). The default Actions `GITHUB_TOKEN` **cannot**
  change repo settings. Repository Actions event policies
  (`repos/{repo}/actions/policies`) need Administration: write even for GET.

---

## 1. Settings-as-code: use repo-settings

The engine (`scripts/repo_settings.py` there), its schema (`repo-settings/schema.md`),
the fleet config (`repo-settings/fleet.yml`) and the fan-out workflow
(`.github/workflows/repo-settings.yml`) live in
[`Adam-S-Daniel/repo-settings`](https://github.com/Adam-S-Daniel/repo-settings).
This skill does not ship a copy.

The repo is **private**, so a session needs access to it. Locally, find its
checkout on the machine in front of you (by the workstation layout, WSL
`~/repos/repo-settings` or Windows `D:\repos\Adam-S-Daniel\repo-settings`;
check, never assume); in a cloud session, attach it with the session's add-repo
tool.

**Read before acting there:** its `README.md`, the repo-specific additions in its
`AGENTS.md`, `repo-settings/schema.md`, and `docs/decisions/`.

Run these from the repo-settings checkout (`--config` paths are relative to it).
They drive everything through `gh`, so they use your local `gh auth`, or
`GH_TOKEN` in CI; run `pip install pyyaml` once:

```bash
RS=<repo-settings checkout>/scripts/repo_settings.py
python3 "$RS" generate --repo <owner/name>   # current state -> YAML
python3 "$RS" diff  --config repo-settings/fleet.yml [--owner X]
python3 "$RS" apply --config repo-settings/fleet.yml [--owner X] [--dry-run]
python3 "$RS" coverage --config repo-settings/fleet.yml --owner X
```

`diff` exits 0 for no drift, 1 for drift, 2 for an error.

Rules that bite:

- **Changes land as a PR to `fleet.yml`**; the push-to-main run applies them.
- **`git pull` before any local `apply`.** Apply converges to the checkout it
  runs from, so a stale checkout silently reverts later changes.
- **`workflow_dispatch` runs the dispatched ref's engine and config**, so
  dispatch from `main`.
- **The engine is idempotent by name** and never touches rulesets, labels, or
  Actions policies it does not own.

If repo-settings is not reachable, do **not** recreate or vendor the engine. Use
the manual recipes in sections 4-8, and say that the fleet config was not
consulted.

---

## 2. Enforcing a baseline across a fleet (central fan-out)

The fan-out workflow is repo-settings'
`.github/workflows/repo-settings.yml`. Its one-time GitHub App setup is that
repo's README, "CI fan-out — one-time setup". Onboarding a repo means a PR
adding it to `fleet.yml` (with `manage: false` when it should be left alone).

**Authenticate with a GitHub App, not a PAT.** A fine-grained PAT is scoped to a
single owner, so it cannot administer repos across both `Adam-S-Daniel` and the
`jodidaniel` org. One GitHub App installed on both accounts mints a short-lived
installation token per owner, so each account is handled with its own
least-privilege token and there is nothing to rotate.

### How the fleet was classified

Non-standard settings are sometimes deliberate, so the fleet was classified with
an adversarial, per-repo workflow-safety audit before applying the ruleset. The
one failure mode that matters for the PR-required ruleset: a workflow step where
`github-actions[bot]` / `GITHUB_TOKEN` (a non-admin) pushes/force-pushes/deletes
on the repo's **own default branch** -- that push is blocked and the job fails.
Rules of thumb used:

- **public, no workflow pushes to the default branch** -> full baseline;
- **private** -> SHA pinning only (fork approval + ruleset unavailable);
- **scratch/experimental** -> Actions hardening only, no ruleset;
- **fork, or owned by another settings system** -> excluded (`manage: false`);
- **a workflow pushes to its own default branch** -> hold the ruleset until the
  workflow is converted to open a PR (e.g. `peter-evans/create-pull-request`).
  (Incident: see PURPOSE.md.) For a fleet-standard bot that must keep writing to every managed
  default branch, the sanctioned alternative is a declared `bypass_actors`
  entry in the fleet config (see repo-settings' schema) -- the agents-md-sync App is the
  standing example (repo-settings ADR 0001).

---

## 3. CMS platform (cms-platform) and its consumers

`cms-platform` and the sites that consume it (`adamdaniel.ai`,
`jodidaniel.com`) manage their **own** settings-as-code from the platform: a
`repo-settings.yml` manifest + `scripts/audit-repo-settings.js`, propagated to
consumers when sites are scaffolded/re-synced, using **rulesets** (history:
see PURPOSE.md).

**These repos are excluded from the fan-out** (`manage: false`). Reason: the
fan-out and the platform would otherwise be two independent sources of truth for
branch protection, and GitHub enforces the **union** of all rulesets/protections
-- so a second system layering its own ruleset would create drift the platform's
audit is blind to. Branch protection for these three repos is owned by the
platform.

**The platform also manages the two Actions-permissions settings this skill
enforces** (`sha_pinning_required`, fork-PR `approval_policy`): its
`repo-settings.yml` carries an `actions_permissions` block, and
`audit-repo-settings.js` reads and writes `actions/permissions` and
`.../fork-pr-contributor-approval`, skipping the fork endpoint on private repos
(HTTP 422) (history: see PURPOSE.md). Do **not** let the fan-out manage these
repos to cover for it.

**Divergence to be aware of:** the platform's `main` ruleset uses
`bypass_actors: []` (nobody, not even the owner, direct-pushes to main -- safe
there because every change lands via PR + auto-merge). The fan-out default uses
`admin_bypass: true` (owner can still direct-push). Both use
`required_approving_review_count: 0`, which is **required** -- the platform's bot
auto-merge chain deadlocks if any approval is required.

---

## 4. Manual recipe -- Setting 1 (SHA pinning)

### Check
```bash
gh api "repos/{owner}/{repo}/actions/permissions" --jq '.sha_pinning_required'
```

### Enable (repo)
```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --method PUT \
  --field enabled=true \
  --field allowed_actions=all \
  --field sha_pinning_required=true
```

`enabled` and `allowed_actions` are **required** in the PUT body -- read them
first and preserve them to avoid unintended changes:

```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --jq '{enabled, allowed_actions}'
```

## 5. Manual recipe -- Setting 2 (fork-PR approval)

### Check
```bash
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --jq '.approval_policy'
```

### Enable "all outside collaborators" (repo)
```bash
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --method PUT \
  --input - <<< '{"approval_policy":"all_external_contributors"}'
```

Returns `422` on a private repo (not applicable). Valid values:
`first_time_contributors`, `all_external_contributors`.

## 6. Manual recipe -- Setting 3 (default-branch ruleset)

### Check
```bash
gh api "repos/{owner}/{repo}/rulesets" --jq '.[]|{id,name,target,enforcement}'
gh api "repos/{owner}/{repo}/rulesets/{id}"   # full rule detail
```

### Create a default-branch protection ruleset
```bash
gh api "repos/{owner}/{repo}/rulesets" --method POST --input - <<'JSON'
{
  "name": "default branch protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      } }
  ],
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ]
}
JSON
```

Returns `403` "Upgrade to GitHub Pro" on a private repo (not available).
`actor_id: 5` is the Admin repository role (owner keeps direct-push); use an
empty `bypass_actors: []` for no bypass.

## 7. Bulk verification

Preferred: the repo-settings `diff`, run from that checkout (see section 1):

```bash
python3 "$RS" diff --config repo-settings/fleet.yml
```

Or manually:

```bash
repos=$(gh repo list {owner} --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner')
for repo in $repos; do
  sha=$(gh api "repos/$repo/actions/permissions" --jq '.sha_pinning_required' 2>/dev/null)
  fork=$(gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" --jq '.approval_policy' 2>/dev/null)
  rs=$(gh api "repos/$repo/rulesets" --jq '[.[].name]|join(",")' 2>/dev/null)
  echo "$repo: sha=$sha fork=$fork rulesets=[$rs]"
done
```

## 8. Fallback -- GitHub UI

If API endpoints change: **Settings > Actions > General** for settings 1-2;
**Settings > Rules > Rulesets** for setting 3.

## 9. Permissions required

- **Repository settings**: `repo` scope (PAT) or repository admin access.
- **GitHub App**: `administration` (write) for rulesets, `actions` (write) for
  Actions permissions.
- **Repository Actions event policies**: Administration (write), GET included.

## 10. Related

- After enabling `sha_pinning_required`, existing workflows with unpinned
  actions will fail. The pin format this repo setting demands is not a skill
  you have to load -- it is always-on managed guidance: see the fleet guidance's
  **"Pinning GitHub Actions"** section (installed into user memory by the
  fleet-memory hook) for the full 40-character SHA, the 7-day
  cooling-off before adopting a release, dereferencing annotated tags, and the
  `./local` / `docker://` refs that have nothing to pin. The SHA stands alone:
  do NOT annotate it with a trailing `# vX.Y.Z` version comment. That
  convention was retired -- see ADR 0004 in the registry -- because nothing
  keeps such a comment honest and a stale one is read and believed.
- **`workflow-path-audit`** -- ensure workflows only run on salient path changes.
