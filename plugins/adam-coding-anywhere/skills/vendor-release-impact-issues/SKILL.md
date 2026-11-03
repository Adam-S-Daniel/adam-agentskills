---
name: vendor-release-impact-issues
description: "File or rewrite GitHub issues that track how a vendor's or upstream project's release notes may affect your repos. Each issue covers one group of related changes for one affected repo, and carries exact quotes with release links and publish times, the files, pins or settings touched, the checks to run, and a fixed block for behavior that contradicts the notes. Use when asked to go through a CLI, SDK, agent, GitHub Action or library changelog and open issues for what affects you, or when a routine does. Not for reviewing a single dependency bump or Dependabot PR, or for writing your own project's release notes."
---

# Vendor release impact issues

This skill is the contract for GitHub issues that track how a vendor's release
notes may affect your repos. The hard parts are getting exact text and
timestamps, getting every claim right for the repo it is filed in, and avoiding
GitHub side effects that are hard to undo.

**If you were only asked to assess impact, report your findings and ask before
filing anything.**

A repo or fleet spec for these issues may add rules to this skill. It may not
relax [Hygiene](#hygiene) or the title lint.

## Order of work

An issue update replaces the whole body, so a format change after filing means
re-sending every quote. Settle the format before filing.

1. **Inventory** every file, pin, setting, test and doc claim in scope that
   depends on the vendor.
2. **Extract the release text exactly.** Index the bullets as `<version>/<n>`
   and record each release's publish time.
3. **Triage** the bullets against the inventory, grouping related changes.
4. **Fix the format**, and write it down if anyone else will file more.
5. **Break the circular link.** If a tracking PR lists the issues and the
   issues link it: open the PR with placeholder slots, file the issues, then
   fill in the slots.
6. **File from generated bodies**, then [verify](#verify-what-github-stored).

## When to file

- **One issue per change group per affected repo.** Write each issue for its
  own repo.
- **Name the surface or don't file.** An issue needs a file, test, doc line,
  setting or pin in the target repo that the change touches.
- **Search the target repo's open and closed issues first.** Link an existing
  issue instead of filing a duplicate.

## Title

`<Product> [<version or range>] <what changed>: <what it touches here>`.
Include the version when one release carries the change. For example:
`ExampleCLI 4.2.0 hooks report source "fork": session hooks match startup|resume`.

- Keep titles under about 110 characters, with no publish times.
- **No `<placeholder>` text.** GitHub stores it, but some tools strip anything
  shaped like a tag when they read a title back. The GitHub MCP server did, so
  the title looked wrong without being wrong, and backticks didn't help.
  - Lint titles for `<[A-Za-z/!]` and write `NAME`, `N` or `…` instead.
  - Check titles with a raw REST read, not the tool that wrote them.

## Body, in this order

1. **Lead line:** where it was found (review, group, tracking PR link), ending
   "Not yet verified against this repo."
2. **`## <Product> change(s)`:** every quote in the group, exact and oldest
   first. Put each quote in a blockquote, followed by its release link and
   publish time:

   ~~~markdown
   > Changed session hooks to report source "fork" for forked sessions
   >
   > — [v4.2.0](https://github.com/example-vendor/example-cli/releases/tag/v4.2.0), published 2026-07-18T01:20Z
   ~~~

   **Use a `text` fence instead of a blockquote** when the quote contains
   anything GitHub acts on:
   - `#123`, `GH-123` or `owner/repo#123`;
   - an `@name`;
   - a URL, or a markdown link, to an issue, PR or commit.

   GitHub's auto-generated release notes (`… by @user in https://…/pull/123`)
   and conventional-changelog bullets always qualify. In a blockquote they
   notify people, post backlinks, and link your own unrelated issue #123. A
   quote must stay exact, so fencing is the only way to neutralize them:

   ~~~markdown
   4.3.0 (published 2026-07-29T01:42Z):
   ```text
   Support plugin manifests from additional marketplaces. (#1234, #1240)
   ```
   ~~~

3. **`## Why it may matter here`:** one bullet per affected file, claim, test
   or pin, saying what the change does to it.
4. **`## To check`:** checkboxes for the measurements to take, each with its
   minimum version, and for the docs, tests and pins to update. Respect the
   repo's one-way doors and required gates.
5. **The [fixed block](#the-fixed-block)**, then the footer your repo or
   harness requires.

## Release publish times

Every vendor version that **your** text names gets the release's publish time,
in UTC to the minute (`YYYY-MM-DDTHH:MMZ`). Never stamp or change text you did
not write.

- **Stamped:**
  - quote attributions, which you write;
  - versions named in your "Why" and "To check" prose.
- **Never stamped:** release-note quotes, text quoted from files or docs, and
  titles.
- **Mark, don't match.** While drafting, write each version your prose names as
  a marker, such as `{{4.2.0}}`. Have the generator render the first use of each
  version as `4.2.0 [2026-07-18T01:20Z]` and later uses bare. The square
  brackets let a stamp sit inside parentheses.
  - A version matcher run over finished text can't tell your words from a
    quote. One put a stamp inside a quoted "4.2.0+".
- **Lint before rendering.** Remove code spans, then `"quoted"` spans. Any
  version left that isn't a marker is an error: mark it, or quote it. Match
  `v`-prefixed forms too.
- **No possessive after a stamp.** Write "the flag added in 4.2.0 […]".

Where the time comes from:

- **The releases API's `published_at`,** truncated to the minute. Not
  `created_at`, which is the date of the tagged commit.
- **On the release page,** only the timestamp element's `datetime` attribute is
  UTC. The visible date is in the viewer's time zone, or relative.
- **Not a tag's commit time, and not a package registry's publish time.** They
  are close, but they are different facts. In one measurement, npm publishes
  trailed GitHub releases by 4–6 minutes.
- **No GitHub release:** use the time from the source the notes came from, and
  name the source: `published 2026-07-18T01:24Z (npm)`.
- **"Latest version on date D" depends on the time zone.** Pick the start version
  that satisfies every reasonable reading, and write your reasoning down.

## Exact quotes

- **Never retype a quote.** Generate bodies with a script that copies each
  bullet by its index from the extracted text.
- **Machine-check subagent citations.** If a subagent triaged, have it echo the
  first words of each bullet beside its index, and check each pair against the
  source.
- **Check one release against a raw source** (the vendor's `CHANGELOG.md` in
  git, or a feed) before trusting text from a fetch tool that may summarize.

## Hygiene

- **Links:** link vendor release pages and docs pages. Never link vendor
  issues, PRs or commits. Put such references in code spans, like
  `` `example-vendor/example-cli#6235` ``. A link or a bare cross-repo
  reference posts a "mentioned this" event on the vendor's issue.
- **Mentions:** put any `@name` token in a code span, including import syntax
  such as `@AGENTS.md`.
- **Closing keywords:** these close the referenced issue when a PR whose body
  contains them merges to the default branch, or when a commit whose message
  contains them lands there:
  - the keywords `close`/`closes`/`closed`, `fix`/`fixes`/`fixed` and
    `resolve`/`resolves`/`resolved`, in any case;
  - followed by `#N`, `owner/repo#N` or an issue URL.

  Backticks and fences don't neutralize them. Keep vendor quotes out of PR
  bodies and commit messages.
- **Labels:** use whatever the repo uses.

## Audit every claim before posting

Ask these of every sentence under "Why" and "To check":

- **Is it in a file I read?** Name the file, and open the fixture or config
  the claim depends on. Repo prose goes stale like vendor docs.
- **Is the version order right?** For example, don't tell someone to look for
  a diagnostic on a version older than the release that added it.
- **Does the suggested fix break a rule of the target repo?** Check its
  `AGENTS.md` or `CONTRIBUTING`.
- **Is it measured?** Say "may" wherever you have not measured the behavior.

## Filing

- **Generate every body from one spec file:** groups, bullet indexes, affected
  repos, and issue numbers once filed. The tracking file and the issues then
  cannot disagree.
- **Print each body just before posting it.**
- **Record `<body index> <issue number>` after each create**, somewhere that
  survives the session, such as a file committed to the tracking branch. A
  session cut off mid-run can then resume without duplicates.
- **Probe first.** Write one issue and read it back before sending the rest.

## Verify what GitHub stored

A write that reports success can store something else. Re-read every issue with
a raw REST `GET /repos/{owner}/{repo}/issues/{n}`, not with the tool that wrote
it, and check:

- the title, exactly;
- the body, against the text you generated (normalize a footer your harness
  rewrites);
- that the required footer is present.

Prove the comparison can fail by running it on a state you know is bad, and
seeing it report mismatches, before trusting a clean result. If one write path
drops content, switch paths and verify again.

## The fixed block

Every issue carries the same block. Fill in `PRODUCT` and `DISCREPANCY-LOG`,
the place your repos record vendor discrepancies. With no such log, point to a
comment on the issue itself.

~~~markdown
## If behavior and the notes disagree

While working this issue, if PRODUCT does something the quoted changes don't
describe, or doesn't do what they say:

- Record it in DISCREPANCY-LOG: the version observed, the exact quote, a
  minimal repro, and the evidence.
- Read the vendor docs only if the notes are ambiguous, and quote the passage
  you relied on.
- Link the record from this issue before closing it.
~~~

## Closing

- **Completed:** when the checks are done. Link the PR.
- **Not planned:** when the change turned out not to matter. Give one line
  saying why.

Either way, record any discrepancy first and link it from the issue.
