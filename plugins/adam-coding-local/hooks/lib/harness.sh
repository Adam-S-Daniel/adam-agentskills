#!/usr/bin/env bash
# harness.sh - which agent harness is running this hook (ADR 0017).
#
# Claude Code and OpenAI Codex both load this plugin's hooks/hooks.json, and
# Cursor may import Claude hooks too, so a hook cannot assume its runner.
# Source this file and call
#
#   harness_detect "$input"
#
# with the hook's stdin JSON (read ONCE by the caller; stdin cannot be read
# twice). It prints exactly one of:
#
#   claude-code  CLAUDE_PROJECT_DIR is set AND transcript_path, with
#                backslashes turned into slashes, contains /.claude/projects/
#   codex        transcript_path contains /.codex/, or PLUGIN_ROOT is set
#                without CLAUDE_PROJECT_DIR (Codex exports PLUGIN_ROOT and
#                never CLAUDE_PROJECT_DIR)
#   other        anything else, including Cursor (which sets
#                CLAUDE_PROJECT_DIR but keeps its transcripts elsewhere), an
#                empty or unparseable input, and a machine with no working
#                Python
#
# Each hook decides for itself what each harness gets. Run directly, this file
# reads stdin and prints the verdict, which is how the tests drive it.

# hook_python - print the first interpreter that really runs Python 3.6+.
# Chosen by RUNNING it, not by `command -v`: on Windows, python3 can be the
# Microsoft Store stub, which resolves, prints "Python was not found" and
# exits 49. Prints nothing (and returns 1) when there is none.
hook_python() {
  local candidate
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 6))' \
        </dev/null >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

harness_detect() {
  local input="${1-}" py parsed transcript
  py="$(hook_python)" || { printf 'other\n'; return 0; }
  # One line out: "ok<TAB><transcript_path>". Anything else - a parse error, a
  # non-object, a crash - is "could not tell", which is `other`.
  parsed="$(printf '%s' "$input" | "$py" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
path = data.get("transcript_path") if isinstance(data, dict) else None
path = path if isinstance(path, str) else ""
sys.stdout.write("ok\t" + path.replace("\\", "/").replace("\n", " ") + "\n")
' 2>/dev/null)" || parsed=""
  case "$parsed" in
    ok$'\t'*) transcript="${parsed#ok$'\t'}" ;;
    *) printf 'other\n'; return 0 ;;
  esac

  if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$transcript" == */.claude/projects/* ]]; then
    printf 'claude-code\n'
  elif [[ "$transcript" == */.codex/* ]] \
      || [[ -n "${PLUGIN_ROOT:-}" && -z "${CLAUDE_PROJECT_DIR:-}" ]]; then
    printf 'codex\n'
  else
    printf 'other\n'
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  harness_detect "$(cat)"
fi
