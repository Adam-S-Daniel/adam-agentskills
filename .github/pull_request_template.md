## TLDR

<!-- One to three plain-English sentences: what changes and why, for someone who has not seen the code. -->

## Details

<!-- What changes, and the reason, with whatever a reviewer needs to check it. -->

## Checklist

- [ ] Examples, fixtures and docs use invented data only (example.com, fictional names); no real personal details, usernames, hostnames or account IDs.
- [ ] A skill-content change has an entry in `docs/skill-impact.md`, and the plugin's version is bumped in both manifests.
- [ ] Tests run: `python3 -m pytest scripts/ plugins/*/skills/*/tests/ plugins/*/skills/*/scripts/ -q` (say the count).
- [ ] A change to a one-way door ([`docs/one-way-doors.md`](https://github.com/Adam-S-Daniel/adam-agentskills/blob/main/docs/one-way-doors.md): removing, renaming or moving a shipped marketplace, plugin or skill name, reusing a retired one, a `renames` map, `.gitattributes` export rules, the registry layout, the lock schema, the bootstrap hook, reachability of pinned commits, records keyed by a skill name) has an `## Adversarial review` section ending in `Adversarial-review: pass <url>` (a github.com or claude.ai link), and a removed name is in `scripts/shipped_names.json`; or the change touches none.
