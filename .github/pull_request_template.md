## TLDR

<!-- One to three plain-English sentences: what changes and why, for someone who has not seen the code. -->

## Details

<!-- What changes, and the reason, with whatever a reviewer needs to check it. -->

## Checklist

- [ ] Examples, fixtures and docs use invented data only (example.com, fictional names); no real personal details, usernames, hostnames or account IDs.
- [ ] A skill-content change has an entry in `docs/skill-impact.md`, and the plugin's version is bumped in both manifests.
- [ ] Tests run: `python3 -m pytest scripts/ plugins/*/skills/*/tests/ plugins/*/skills/*/scripts/ -q` (say the count).
