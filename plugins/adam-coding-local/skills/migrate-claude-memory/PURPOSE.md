# Purpose — migrate-claude-memory

Maintenance context only; never loaded at inference.

This local skill inventories machine-local auto-memory stores, helps a human
review cleanup candidates, and copies a selected store into portable
repository memory without deleting its source.

[Issue #14](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14)
tracks naming changes that prompted this scoped decoder follow-up. A failed
decode created a data-loss hazard: failed or lossy path decoding labeled stores
`ORPHANED`, while the cleanup instructions suggested deleting those stores.
The first decoder follow-up inspected only dotted aliases, but underscores,
spaces, Unicode, and other punctuation collide under the same munger. The
corrective review now enumerates every directory entry at every level and
normalizes non-ASCII-alphanumeric UTF-16 code units, including two hyphens for
supplementary characters. Matching aliases, mixed existing/missing candidates,
and candidate branches that cannot be fully examined remain `UNRESOLVED`.
A second review also found a reached existing prefix with an undecodable
suffix could be ignored while another branch supported an orphan; that
dead-end branch now preserves uncertainty too.
Only exactly one supported missing ASCII-alphanumeric final component beneath
a readable, fully examined parent chain can be `ORPHANED`.

Known removable roots and detected device boundaries are always unresolved,
including populated mounts and case-insensitive drive roots. This avoids
claims based on unavailable mounts without requiring privileged inspection.
The policy cannot recognize arbitrary former mount points sharing their
parent's device. Long hashed names and unsupported store formats remain
unresolved; custom aliases resembling standard names and concurrent changes
remain limits. A human must independently verify any orphan result.

The focused tests run the inventory with invented temporary homes and cover
classification, JSON and text output, aggregate counts, and preserved memory
contents. This skill remains local because its inputs live on the machine;
none of these tests inspect the operator's real memory stores.

The portability repair in [PR #41](https://github.com/Adam-S-Daniel/adam-agentskills/pull/41)
keeps the decoder's native POSIX path rule explicit. Native Windows Git
Bash/MSYS, Cygwin, and win32 Bash now stop with a fixed reason and exit 3
before looking up HOME or creating temporary files. Supported inventory runs
use Bash and GNU tools on native POSIX paths, such as Linux or WSL. The Windows
failure log did not expose script stderr, so the repair does not claim a
specific cause for those failures. Tests now preserve both subprocess streams,
including invalid bytes, and skip inventory cases only after the actual
script and an independent Bash runtime probe confirm this unsupported case.
Invalid UTF-8 filenames and unreadable directories are separately probed for
filesystem support so capable Linux runs retain those safety regressions.
