#!/usr/bin/env bash
# on-session-start.sh - SessionStart hook for the API-credit lane (ADR 0018).
#
# Claude Code only (hooks/claude-code.json; Codex never loads that file, and
# harness.sh checks again). Runs the gate once, stores the session's state in
# credit_home/sessions/<session_id>.state as `<open|closed> <next_check_epoch>`,
# and prints a short additionalContext ONLY when the lane is open. Closed, it
# prints nothing, so a closed lane costs the session no tokens. Also removes
# session state files older than 7 days. Always exits 0.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input="$(cat)"

# shellcheck source=../lib/harness.sh
. "$HERE/../lib/harness.sh"

[[ "$(harness_detect "$input")" == claude-code ]] || exit 0
py="$(hook_python)" || exit 0
printf '%s' "$input" | "$py" "$HERE/gate.py" hook --event start 2>/dev/null
exit 0
