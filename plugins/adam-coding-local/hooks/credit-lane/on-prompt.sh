#!/usr/bin/env bash
# on-prompt.sh - UserPromptSubmit hook for the API-credit lane (ADR 0018).
#
# Fast path, pure bash, no Python: when this session's state file exists and
# its next_check_epoch is still ahead, exit 0 at once. The state file exists
# only after on-session-start.sh ran in Claude Code, so the fast path needs no
# harness check.
#
# Otherwise: check the harness, run the gate, update the state, and print a
# one-line additionalContext ONLY when the lane flipped open or closed. Always
# exits 0.
#
# The current epoch is CLAUDE_CREDIT_NOW_EPOCH when set (tests), else the clock.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input="$(cat)"

# credit_home_fast - credit_home without starting a process, or nothing when
# that needs one (gate.py then resolves it on the slow path).
credit_home_fast() {
  local version="" cached="" drive rest
  if [[ -n "${CLAUDE_CREDIT_HOME:-}" ]]; then
    printf '%s' "$CLAUDE_CREDIT_HOME"; return 0
  fi
  if [[ -n "${MSYSTEM:-}" && "${USERPROFILE:-}" == [A-Za-z]:\\* ]]; then
    drive="${USERPROFILE:0:1}" rest="${USERPROFILE:2}"
    printf '/%s%s/.config/claude-credit' "${drive,,}" "${rest//\\//}"; return 0
  fi
  [[ -r /proc/version ]] && read -r version < /proc/version
  if [[ "${version,,}" == *microsoft* ]]; then
    [[ -r "${HOME:-}/.config/claude-credit/windows-home" ]] || return 1
    read -r cached < "$HOME/.config/claude-credit/windows-home" || [[ -n "$cached" ]] || return 1
    [[ "$cached" == /* ]] || return 1
    printf '%s/.config/claude-credit' "$cached"; return 0
  fi
  printf '%s/.config/claude-credit' "${HOME:-}"
}

session_re='"session_id"[[:space:]]*:[[:space:]]*"([A-Za-z0-9_-]{1,128})"'
if [[ "$input" =~ $session_re ]]; then
  session="${BASH_REMATCH[1]}"
  if home="$(credit_home_fast)" && [[ -n "$home" ]]; then
    state_file="$home/sessions/$session.state"
    if [[ -f "$state_file" ]] && read -r _state next < "$state_file" \
        && [[ "${next:-}" =~ ^[0-9]{1,12}$ ]]; then
      now="${CLAUDE_CREDIT_NOW_EPOCH:-${EPOCHSECONDS:-}}"
      [[ "$now" =~ ^[0-9]+$ ]] || now="$(date +%s)"
      if (( now < next )); then
        exit 0
      fi
    fi
  fi
fi

# shellcheck source=../lib/harness.sh
. "$HERE/../lib/harness.sh"
[[ "$(harness_detect "$input")" == claude-code ]] || exit 0
py="$(hook_python)" || exit 0
printf '%s' "$input" | "$py" "$HERE/gate.py" hook --event prompt 2>/dev/null
exit 0
