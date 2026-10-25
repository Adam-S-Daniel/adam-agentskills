# History moved out of AGENTS.md

Dated incident narratives that `AGENTS.md` used to carry inline. They explain
why a rule exists; they give an agent nothing to do, so `AGENTS.md` keeps the
rule and a one-clause pointer here (prompt-audit issue #56, 2026-10). Nothing
was dropped.

## Skill rename before upload or lock

`launch-wsl-claude-session` became `launch-top-level-claude-session` on
2026-09-25, before it had been uploaded or locked.

## Hardcoded `~/repos/<name>` broke sync-skills

That assumption once broke sync-skills: it guessed `~/repos/<name>` ahead of
the checkout it was actually running from, a decoy outranked the real clone,
and `--all` enumerated nothing.

## The deleted `test_account_zip_selection.py` example

This repo's own earlier example of a test that cannot fail,
`test_account_zip_selection.py`, left base.md when it was condensed and was
deleted with the account-zip channel (ADR 0014). The fleet guidance's worked
example is `test_foo.py`.

## Hosted-session PyYAML install measurements

- Measured on `remote_mobile`, 2026-08-25: without `--ignore-installed
  PyYAML`, pip refuses with `Cannot uninstall PyYAML 6.0.1, RECORD file not
  found. Hint: The package was installed by debian.` and installs nothing
  else either.
- Verified 2026-08-30 on a hosted session that started with `yaml` at
  Debian's 6.0.1 and `pytest`, `jsonschema` and `markdown_it` all absent: pip
  lands the pinned wheel in `/usr/local/lib/python3.11/dist-packages`, Debian's
  lives in `/usr/lib/python3/dist-packages`, the former precedes the latter on
  `sys.path`, `import yaml` gives 6.0.3 (`requirements-dev.txt`'s pin), and the
  full suite was green (1943 passed, 11 skipped; counts change every PR).

## Dangling test citation during a long run

Measured 2026-08-25: a "pre-existing" red in
`test_every_test_this_repo_cites_by_name_exists` turned out to be a dangling
test citation written 90 seconds into the run.

## `claude plugin eval` assessment

skills-evals' `DESIGN.md` ("`claude plugin eval`", assessed 2026-08-30)
decided to monitor Claude Code 2.1.269+'s `claude plugin eval` rather than
wrap it: no scriptable grader, so it cannot host the objective scorers.
Whether to revisit it is the owner's call.
