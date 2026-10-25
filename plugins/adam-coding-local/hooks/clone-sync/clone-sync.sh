#!/usr/bin/env bash
# clone-sync.sh - fast-forward this machine's clean clones of a GitHub repo
# to its remote default branch (ADR 0017).
#
# Usage:
#   clone-sync.sh --repo OWNER/REPO   the clones whose origin is that repo
#   clone-sync.sh --all               every clone with a GitHub origin
#   clone-sync.sh --repo-of DIR       the clones of whatever repo DIR's origin
#                                     is (the hooks pass their cwd, so the
#                                     git work happens here, in the
#                                     background, not in the hook)
#
# Candidate clones are the directories matched by each glob in
# CLONE_SYNC_ROOTS (`:`-separated). The default is $HOME/repos/* plus, when
# present, /mnt/d/repos/*/* (WSL's view of the Windows clones) or
# /d/repos/*/* (Git Bash's). Anything under .claude/worktrees is skipped.
#
# A clone matches when its origin is GitHub OWNER/REPO, case-insensitively,
# in https or ssh form. A matching clone is fast-forwarded ONLY when it is on
# the remote default branch, has no staged or unstaged tracked changes, has
# no merge, rebase, cherry-pick, revert or bisect in progress, and is not a
# linked worktree. Untracked files are allowed: `git merge --ff-only` itself
# refuses a fast-forward that would overwrite one. This script never stashes,
# resets, checks out, cleans or forces anything.
#
# Output is one line per clone: `<status> <path>`, where status is
# `updated`, `current`, `skipped:<reason>` or `error:<reason>`. Nothing else
# is printed; in particular a remote URL never is, since one can carry a
# credential.
#
# A global lock (an atomic mkdir under ${XDG_CACHE_HOME:-$HOME/.cache}/
# clone-sync/, stale after 15 minutes) keeps runs from overlapping; a run
# that finds it held exits 0 quietly. Each network call is bounded by
# `timeout` when it exists (CLONE_SYNC_GIT_TIMEOUT seconds, default 120).
#
# Exit status: 0, or 1 when any clone reported error:, or 2 on a usage error.
set -u

usage() {
  echo "usage: clone-sync.sh (--repo OWNER/REPO | --all | --repo-of DIR)" >&2
  exit 2
}

MODE="" WANT="" REPO_OF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || usage; MODE=repo; WANT="$2"; shift 2 ;;
    --all) MODE=all; shift ;;
    --repo-of) [[ $# -ge 2 ]] || usage; MODE=repo-of; REPO_OF="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$MODE" ]] || usage

SLUG_RE='^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$'
if [[ "$MODE" == repo ]]; then
  [[ "$WANT" =~ $SLUG_RE ]] || usage
  WANT="${WANT,,}"
fi

# Never wait on a credential prompt in a background run.
export GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never

IS_WSL=0
if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
  IS_WSL=1
fi
GIT_TIMEOUT="${CLONE_SYNC_GIT_TIMEOUT:-120}"
[[ "$GIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || GIT_TIMEOUT=120
HAVE_TIMEOUT=0
command -v timeout >/dev/null 2>&1 && HAVE_TIMEOUT=1

# git_for <dir> - the git to use for a clone. In WSL, a clone under /mnt/ is a
# Windows checkout: git.exe is faster there and reads the Windows system
# config (core.autocrlf), so it does not report every file as modified. Both
# gits share the index, so either is safe.
git_for() {
  if [[ "$IS_WSL" == 1 && "$1" == /mnt/* ]] && command -v git.exe >/dev/null 2>&1; then
    printf 'git.exe\n'
  else
    printf 'git\n'
  fi
}

# in_clone <dir> <git> <args...> - run git inside the clone, output with any
# CR removed (git.exe may print CRLF). The cd keeps git.exe working: WSL
# translates a /mnt/ working directory, not a /mnt/ argument.
in_clone() {
  local dir="$1" git="$2"
  shift 2
  (cd "$dir" 2>/dev/null && "$git" "$@") | tr -d '\r'
  return "${PIPESTATUS[0]}"
}

# net_git <dir> <git> <args...> - in_clone, bounded by the timeout.
net_git() {
  local dir="$1" git="$2"
  shift 2
  if [[ "$HAVE_TIMEOUT" == 1 ]]; then
    (cd "$dir" 2>/dev/null && timeout "$GIT_TIMEOUT" "$git" "$@") >/dev/null 2>&1
  else
    (cd "$dir" 2>/dev/null && "$git" "$@") >/dev/null 2>&1
  fi
}

# github_slug <url> - print "owner/repo" (lowercase) when <url> is a GitHub
# remote in https, http, ssh, git or scp form; print nothing otherwise.
github_slug() {
  local url="$1" rest host path
  case "$url" in
    https://*|http://*|ssh://*|git://*|git+ssh://*)
      rest="${url#*://}"
      host="${rest%%/*}"
      [[ "$rest" == */* ]] || return 0
      path="${rest#*/}"
      host="${host##*@}"   # drop any user[:token]@
      host="${host%%:*}"   # drop any :port
      ;;
    *@*:*|github.com:*)
      rest="${url#*@}"
      host="${rest%%:*}"
      path="${rest#*:}"
      ;;
    *) return 0 ;;
  esac
  case "${host,,}" in
    github.com|www.github.com|ssh.github.com) ;;
    *) return 0 ;;
  esac
  path="${path%/}"
  path="${path%.git}"
  [[ "$path" =~ $SLUG_RE ]] || return 0
  printf '%s\n' "${path,,}"
}

# origin_slug <dir> - the GitHub slug of <dir>'s origin, or nothing.
origin_slug() {
  local dir="$1" git url
  git="$(git_for "$dir")"
  url="$(in_clone "$dir" "$git" config --get remote.origin.url 2>/dev/null)" || return 0
  github_slug "$url"
}

ERRORS=0
report() {
  printf '%s %s\n' "$1" "$2"
  [[ "$1" == error:* ]] && ERRORS=1
  return 0
}

# sync_clone <dir> - every precondition, then fetch and fast-forward.
sync_clone() {
  local dir="$1" git gitdir branch head default local_sha remote_sha rc
  git="$(git_for "$dir")"
  if [[ -f "$dir/.git" ]]; then
    report skipped:linked-worktree "$dir"
    return
  fi
  gitdir="$dir/.git"

  if [[ -e "$gitdir/MERGE_HEAD" || -d "$gitdir/rebase-merge" || -d "$gitdir/rebase-apply" \
        || -e "$gitdir/CHERRY_PICK_HEAD" || -e "$gitdir/REVERT_HEAD" \
        || -e "$gitdir/BISECT_LOG" ]]; then
    report skipped:operation-in-progress "$dir"
    return
  fi

  head="$(in_clone "$dir" "$git" symbolic-ref --quiet --short HEAD 2>/dev/null)" || head=""
  if [[ -z "$head" ]]; then
    report skipped:detached-head "$dir"
    return
  fi

  default="$(in_clone "$dir" "$git" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)" || default=""
  default="${default#origin/}"
  if [[ -z "$default" ]]; then
    for branch in main master; do
      if in_clone "$dir" "$git" show-ref --verify --quiet "refs/remotes/origin/$branch" >/dev/null 2>&1; then
        default="$branch"
        break
      fi
    done
  fi
  if [[ -z "$default" ]]; then
    report skipped:no-default-branch "$dir"
    return
  fi
  if [[ "$head" != "$default" ]]; then
    report skipped:not-default-branch "$dir"
    return
  fi

  # `git diff --quiet` exits 1 on a difference and >1 on an error; both mean
  # "do not touch it".
  if ! in_clone "$dir" "$git" diff --quiet >/dev/null 2>&1 \
      || ! in_clone "$dir" "$git" diff --cached --quiet >/dev/null 2>&1; then
    report skipped:dirty "$dir"
    return
  fi

  net_git "$dir" "$git" fetch --quiet --no-tags origin "$default"
  rc=$?
  if [[ $rc -eq 124 ]]; then
    report error:fetch-timeout "$dir"
    return
  elif [[ $rc -ne 0 ]]; then
    report error:fetch-failed "$dir"
    return
  fi

  local_sha="$(in_clone "$dir" "$git" rev-parse --verify --quiet HEAD 2>/dev/null)" || local_sha=""
  remote_sha="$(in_clone "$dir" "$git" rev-parse --verify --quiet "refs/remotes/origin/$default" 2>/dev/null)" || remote_sha=""
  if [[ -z "$local_sha" || -z "$remote_sha" ]]; then
    report error:no-commit "$dir"
    return
  fi
  if [[ "$local_sha" == "$remote_sha" ]] \
      || in_clone "$dir" "$git" merge-base --is-ancestor "$remote_sha" "$local_sha" >/dev/null 2>&1; then
    report current "$dir"
    return
  fi
  if ! in_clone "$dir" "$git" merge-base --is-ancestor "$local_sha" "$remote_sha" >/dev/null 2>&1; then
    report skipped:diverged "$dir"
    return
  fi
  if in_clone "$dir" "$git" merge --ff-only --quiet "refs/remotes/origin/$default" >/dev/null 2>&1; then
    report updated "$dir"
  else
    report error:ff-failed "$dir"
  fi
}

# --- the lock -----------------------------------------------------------------
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/clone-sync"
LOCK="$CACHE_DIR/lock"
mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0
if ! mkdir "$LOCK" 2>/dev/null; then
  # Held. A lock older than 15 minutes belongs to a run that died without
  # cleaning up; take it over. Two runs racing over a stale lock can both
  # proceed, which costs a duplicate fetch, never a wrong fast-forward: git
  # serializes the ref and index updates itself.
  if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +15 2>/dev/null)" ]]; then
    rmdir "$LOCK" 2>/dev/null
    mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# --- which repo ---------------------------------------------------------------
if [[ "$MODE" == repo-of ]]; then
  # A Windows harness passes C:\... paths; Git Bash's tools want /c/...
  if [[ "$REPO_OF" == [A-Za-z]:* ]] && command -v cygpath >/dev/null 2>&1; then
    REPO_OF="$(cygpath -u "$REPO_OF")"
  fi
  if [[ ! -d "$REPO_OF" ]]; then
    exit 0
  fi
  WANT="$(origin_slug "$REPO_OF")"
  [[ -n "$WANT" ]] || exit 0
fi

# --- the candidates -----------------------------------------------------------
ROOTS=()
if [[ -n "${CLONE_SYNC_ROOTS:-}" ]]; then
  IFS=: read -r -a ROOTS <<< "$CLONE_SYNC_ROOTS"
else
  ROOTS=("$HOME/repos/*")
  [[ -d /mnt/d/repos ]] && ROOTS+=("/mnt/d/repos/*/*")
  [[ -d /d/repos ]] && ROOTS+=("/d/repos/*/*")
fi

shopt -s nullglob
for pattern in "${ROOTS[@]}"; do
  [[ -n "$pattern" ]] || continue
  # The pattern is a glob on purpose. An empty IFS keeps pathname expansion
  # but turns off word splitting, so a home with a space in it survives.
  IFS=''
  # shellcheck disable=SC2206
  matches=($pattern)
  unset IFS
  for dir in "${matches[@]}"; do
    dir="${dir%/}"
    case "$dir" in */.claude/worktrees/*) continue ;; esac
    [[ -d "$dir" && -e "$dir/.git" ]] || continue
    slug="$(origin_slug "$dir")"
    if [[ "$MODE" == all ]]; then
      [[ -n "$slug" ]] || continue
    else
      [[ "$slug" == "$WANT" ]] || continue
    fi
    sync_clone "$dir"
  done
done

exit "$ERRORS"
