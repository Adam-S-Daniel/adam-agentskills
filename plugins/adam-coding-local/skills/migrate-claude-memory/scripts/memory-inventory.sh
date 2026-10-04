#!/usr/bin/env bash
# memory-inventory.sh — read-only inventory of Claude Code auto-memory stores
# under ~/.claude/projects/<munged-path>/memory/.
#
# Usage: bash scripts/memory-inventory.sh [--json]
#
# Never deletes or modifies anything on disk.
set -euo pipefail

usage() {
  echo "Usage: memory-inventory.sh [--json]" >&2
}

json_mode=0
for arg in "$@"; do
  case "$arg" in
    --json)
      json_mode=1
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[ -d "$HOME/.claude/projects" ] || { echo "ERROR: ~/.claude/projects not found — is this a machine with Claude Code auto-memory?" >&2; exit 1; }

# Decoding is lossy. Collect supported candidates rather than accepting the first
# existing path or inventing a path by replacing every hyphen with a slash.
# A missing candidate is supported only for one plain final component beneath an
# accessible existing parent. Ambiguous, unsupported, or inaccessible paths stay
# UNRESOLVED and must not become cleanup candidates.
decode_munged_path() {
  local munged="$1"
  decoded_path=""
  decode_status="UNRESOLVED"
  decode_count=0
  decode_uncertain=0
  [[ "$munged" == -* && "$munged" != *.* ]] || return 0
  _decode_try "/" "${munged:1}"
  if (( decode_count != 1 || decode_uncertain )); then
    decoded_path=""
    decode_status="UNRESOLVED"
  fi
}

_decode_candidate() {
  decode_count=$((decode_count + 1))
  if (( decode_count == 1 )); then
    decoded_path="$1"
    decode_status="$2"
  fi
}

# DFS over existing slash/hyphen prefixes. Dotted aliases are inspected only as
# evidence of ambiguity; this does not invent dot-substitution candidates.
_decode_try() {
  local current="$1" remaining="$2"
  (( decode_count < 2 )) || return 0
  if [[ ! -r "$current" || ! -x "$current" ]]; then
    decode_uncertain=1
    return 0
  fi
  if [[ -z "$remaining" ]]; then
    _decode_candidate "$current" "EXISTING"
    return 0
  fi

  local child name encoded
  for child in "$current"/* "$current"/.[!.]* "$current"/..?*; do
    name="${child##*/}"
    [[ "$name" == *.* ]] || continue
    encoded="${name//./-}"
    if [[ "$remaining" == "$encoded" || "$remaining" == "$encoded-"* ]]; then
      decode_uncertain=1
    fi
  done

  local n=${#remaining} i component candidate_dir
  for (( i=n; i>=0; i-- )); do
    (( decode_count < 2 )) || return 0
    if (( i < n )) && [[ "${remaining:i:1}" != "-" ]]; then
      continue
    fi
    # A trailing hyphen is a literal character, not an empty path component.
    (( i != n - 1 )) || continue
    component="${remaining:0:i}"
    [[ -n "$component" ]] || continue
    candidate_dir="${current%/}/$component"
    if [[ -L "$candidate_dir" ]]; then
      decode_uncertain=1
    elif [[ -d "$candidate_dir" ]]; then
      if (( i == n )); then
        _decode_candidate "$candidate_dir" "EXISTING"
      else
        _decode_try "$candidate_dir" "${remaining:i+1}"
      fi
    elif (( i == n )) && [[ "$remaining" != *-* ]]; then
      if [[ -e "$candidate_dir" ]]; then
        decode_uncertain=1
      else
        _decode_candidate "$candidate_dir" "ORPHANED"
      fi
    fi
  done
}

# json_escape <string>
# Escapes backslashes and double-quotes for embedding in a JSON string.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

shopt -s nullglob

find_tmp="$(mktemp)"
trap 'rm -f "$find_tmp"' EXIT

total_count=0
orphaned_count=0
unresolved_count=0
json_entries=()
text_blocks=()

for memory_dir in "$HOME"/.claude/projects/*/memory; do
  [ -d "$memory_dir" ] || continue

  # Count regular files directly inside memory_dir (maxdepth 1), safely.
  if ! find "$memory_dir" -maxdepth 1 -type f > "$find_tmp" 2>/dev/null; then
    echo "WARNING: 'find' failed while scanning $memory_dir — skipping (this is NOT the same as zero files)" >&2
    continue
  fi
  file_count=$(wc -l < "$find_tmp" | tr -d ' ')

  if [ "$file_count" -eq 0 ]; then
    continue
  fi

  total_count=$((total_count + 1))

  parent_dir="$(dirname "$memory_dir")"
  munged="$(basename "$parent_dir")"

  decode_munged_path "$munged"
  orphaned=0
  unresolved=0
  case "$decode_status" in
    ORPHANED)
      orphaned=1
      orphaned_count=$((orphaned_count + 1))
      ;;
    UNRESOLVED)
      unresolved=1
      unresolved_count=$((unresolved_count + 1))
      ;;
  esac

  size_bytes=$(du -sb "$memory_dir" 2>/dev/null | cut -f1) || true
  [ -n "$size_bytes" ] || size_bytes=0
  size_human=$(du -sh "$memory_dir" 2>/dev/null | cut -f1) || true
  [ -n "$size_human" ] || size_human="unknown"

  newest_epoch=$(find "$memory_dir" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f1) || true
  newest_mtime="unknown"
  if [ -n "$newest_epoch" ]; then
    newest_epoch_int="${newest_epoch%.*}"
    newest_mtime=$(date -d "@$newest_epoch_int" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown")
  fi

  if [ "$json_mode" -eq 1 ]; then
    if [ "$unresolved" -eq 1 ]; then
      path_json="null"
    else
      path_json="\"$(json_escape "$decoded_path")\""
    fi
    entry=$(printf '{"munged":"%s","path":%s,"orphaned":%s,"status":"%s","unresolved":%s,"file_count":%s,"total_size_bytes":%s,"newest_mtime":"%s"}' \
      "$(json_escape "$munged")" \
      "$path_json" \
      "$([ "$orphaned" -eq 1 ] && echo true || echo false)" \
      "$decode_status" \
      "$([ "$unresolved" -eq 1 ] && echo true || echo false)" \
      "$file_count" \
      "$size_bytes" \
      "$(json_escape "$newest_mtime")")
    json_entries+=("$entry")
  else
    block="Store: $munged"$'\n'
    if [ "$unresolved" -eq 1 ]; then
      block+="  Path: unknown  [UNRESOLVED: do not delete based on this result]"$'\n'
    elif [ "$orphaned" -eq 1 ]; then
      block+="  Path: $decoded_path  [ORPHANED: decoded workspace path does not exist]"$'\n'
    else
      block+="  Path: $decoded_path"$'\n'
    fi
    block+="  Files: $file_count"$'\n'
    block+="  Size: $size_human"$'\n'
    block+="  Newest file: $newest_mtime"
    text_blocks+=("$block")
  fi
done

if [ "$json_mode" -eq 1 ]; then
  printf '['
  first=1
  for entry in "${json_entries[@]+"${json_entries[@]}"}"; do
    if [ "$first" -eq 1 ]; then
      first=0
    else
      printf ','
    fi
    printf '%s' "$entry"
  done
  printf ']\n'
else
  for block in "${text_blocks[@]+"${text_blocks[@]}"}"; do
    printf '%s\n\n' "$block"
  done
  echo "$total_count stores, $orphaned_count orphaned (decoded workspace path does not exist), $unresolved_count unresolved (path could not be determined safely)"
fi
