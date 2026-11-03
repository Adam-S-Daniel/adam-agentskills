# Purpose

Answers the lesson "red CI after push" seen repeatedly on a Jekyll + Decap
site (54 mined sessions): runs ending `cancelled`, a `failure` at job setup,
required checks that never reported, and version-bump PRs left BLOCKED.
The common failure was declaring a push finished without reading each check's
conclusion, then guessing at the cause.

Triage: not mechanical, because red CI exists only after the push; no
pre-commit, pre-push or tool-call check can see it. Where the drafting flow
would have asked a person which repo shape to cover, this skill covers the
cms-platform consumer shape and says so in its description.

All fixtures use invented data (example.com, example-org).
