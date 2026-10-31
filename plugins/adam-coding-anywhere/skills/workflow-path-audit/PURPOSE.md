# Purpose — workflow-path-audit

Maintenance context only; never loaded at inference.

## What it is for

Audits GitHub Actions workflows so every `pull_request`/`push` workflow filters on the paths its steps depend on, and skips with success when nothing salient changed, while keeping required checks always-firing.

## Changes

- Prompt-audit fix (adam-agentskills issue #56): the salience grep uses a here-string (a `printf | grep -q` pipe under pipefail turns a match into a miss past the 64 KiB pipe buffer); required checks are read from `rules/branches/main` (the union of rulesets); the checkout placeholder no longer shows only the legacy SHA pin form.
