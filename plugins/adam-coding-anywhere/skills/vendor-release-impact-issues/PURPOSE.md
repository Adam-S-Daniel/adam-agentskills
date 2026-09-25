# Purpose — vendor-release-impact-issues

Maintenance context only; never loaded at inference.

## What it is for

A session reviewed two coding agents' release notes (about 2,700 bullets over
eleven weeks) against five repos and filed 32 issues about the changes that
might affect them. The issue contract it ended up with is the fleet spec,
`_agent-guidance`'s `docs/reference/agent-changelog-issues.md`, added in
https://github.com/Adam-S-Daniel/_agent-guidance/pull/171. This skill makes that
contract generic, for any vendor and any repo, and adds the rules the review
below found missing. The fleet spec may add rules but not relax the skill's
hygiene. A routine will repeat the review for every new batch of vendor
releases, and each run needs the contract.

## The incidents it packages

All from the 2026-09-25 session that seeded `_agent-guidance`'s
`agent-claude-code.CHANGELOG.md` and `agent-codex.CHANGELOG.md`.

- **A title that looked wrong but wasn't.** The GitHub MCP server read the
  title "… named `anthropic-skills:<name>` …" back as
  "… `anthropic-skills:` …". The issue was renamed to use `NAME`. The rename
  event's `from` value later showed that GitHub had stored `<name>` intact:
  the tool's read path had stripped it. This is why titles are linted and
  then checked with a raw REST read.
- **Quotes that would have linked to the wrong issues.** Codex release bullets
  end in `(#47101, …)`. Unfenced in an issue body, each one links to that
  number in the repo being filed in. The adversarial review widened the rule:
  GitHub's auto-generated notes (`by @user in https://…/pull/N`) would notify
  people and post backlinks, so any quote containing something GitHub acts on
  is fenced.
- **Backlinks on a vendor's public tracker.** A skills-evals README cited an
  upstream issue by number. Linking it from an issue would have posted a
  "mentioned this" event on the vendor's issue. References go in code spans.
- **Claims that didn't survive review.** Drafts contained:
  - an assertion no file supported;
  - a check for a diagnostic on a version older than the release that added
    it;
  - a suggested rename that broke the target repo's one-way-door rule for
    skill directory names.

  A README's prediction that native `AGENTS.md` loading "would break" a test
  leg was wrong once the fixture was opened. All of these were caught before
  posting, and they are why the claim audit exists.
- **Two full rewrites of 32 issues.** The fixed discrepancy block was specified
  after filing, and publish times were requested after that. An update
  replaces the whole body, so each change re-sent about 80 KB of exact quotes.
  This is why the order of work puts the format before filing.
- **Publish times needed calibration.** "The latest version on 7/10" hinged on
  a release at 00:52Z, the evening of 7/10 in US Eastern time. Tag commits
  were within about a minute of release times for one vendor. npm publishes
  trailed releases by 4–6 minutes for the other.
- **A version matcher run over finished text touched text we didn't write.**
  It stamped a version inside a quoted "2.1.273+", missed a `v`-prefixed
  version, and left awkward possessives after stamps. The owner asked for
  stamps that never touch text the process did not author. The result is the
  "mark, don't match" rule: authored prose marks versions as `{{2.1.214}}`, and
  a lint fails on any unmarked version outside code and quotes. Converting the
  32 existing bodies to markers produced byte-identical text.
- **A silent write loss.** In a Claude Code cloud session,
  `mcp__github__issue_write` returned success on both update (all 32 bodies)
  and create while storing each body without the attribution footer. A
  comparison against a fresh REST read caught it. The comparison was first
  shown to fail (31 of 32 mismatched), then shown to pass after the repair.
  - A REST `PATCH` through the session proxy kept a footer. It needed an
    explicit `Content-Type: application/json`, and returned 415 without it.
  - The org connector (`github-mcp`) returned 403 on these repos: its GitHub
    App lacked Issues write access.
  - SKILL.md states only the general rule: verify with a raw REST read, and
    switch write paths if one drops content.

## What it deliberately leaves out

- **How to fetch release text through a restrictive proxy, and how to triage
  thousands of bullets with subagents.** Both came up in the same session but
  are separate concerns. They are proposed as their own skills:
  https://github.com/Adam-S-Daniel/adam-agentskills/issues/17 and
  https://github.com/Adam-S-Daniel/adam-agentskills/issues/18.
- **The fleet-specific discrepancy log paths.** The skill describes the fixed
  block generically. The fleet spec in `_agent-guidance` names the files.
