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

# Claude Code replaces each non-ASCII-alphanumeric UTF-16 code unit with a
# hyphen. Validate a UTF-8 locale before interpreting filesystem names.
decode_locale=""
for candidate_locale in C.UTF-8 C.utf8 en_US.UTF-8; do
  if [[ "$(LC_ALL="$candidate_locale" locale charmap 2>/dev/null)" == "UTF-8" ]]; then
    decode_locale="$candidate_locale"
    break
  fi
done

# Known mount roots are never cleanup candidates, including unavailable drives.
# Device changes elsewhere are checked separately while traversing directories.
_decode_mount_root() {
  case "$1" in
    /mnt/*|/media|/media/*|/run/media|/run/media/*|/Volumes|/Volumes/*) return 0 ;;
  esac
  return 1
}

# Bash dynamic scope returns a byte count without spawning one process per character.
_decode_byte_length() {
  local LC_ALL=C
  bytes=${#1}
}

# Return the actual munger's component encoding, rejecting invalid UTF-8 names.
_decode_encode() {
  local LC_ALL=C name="$1" char code bytes i
  if [[ "$name" != *[$'\x80'-$'\xff']* ]]; then
    encoded="${name//[^a-zA-Z0-9]/-}"
    return 0
  fi
  LC_ALL="$decode_locale"
  encoded=""
  for (( i=0; i<${#name}; i++ )); do
    char="${name:i:1}"
    printf -v code '%d' "'$char"
    _decode_byte_length "$char"
    if (( (code >= 48 && code <= 57) || (code >= 65 && code <= 90) || (code >= 97 && code <= 122) )); then
      encoded+="$char"
    elif (( code < 128 && bytes == 1 )); then
      encoded+="-"
    elif (( (code >= 128 && code < 2048 && bytes == 2) || (code >= 2048 && code < 65536 && bytes == 3) )); then
      encoded+="-"
    elif (( code >= 65536 && code <= 1114111 && bytes == 4 )); then
      encoded+="--"
    else
      return 1
    fi
  done
}

# Decoding is lossy. Enumerate every actual entry at every level, not just
# slash/hyphen splits. Only one missing plain ASCII-alphanumeric final leaf
# beneath a completely examined readable parent can support ORPHANED.
decode_munged_path() {
  local LC_ALL=C munged="$1"
  decoded_path=""
  decode_status="UNRESOLVED"
  decode_count=0
  decode_uncertain=0
  [[ -n "$decode_locale" && ${#munged} -le 200 && "$munged" =~ ^-[A-Za-z0-9-]*$ ]] || return 0
  # Reject known removable roots even when no corresponding entry is present.
  case "$munged" in
    -mnt-*|-media|-media-*|-run-media|-run-media-*|-Volumes|-Volumes-*) return 0 ;;
  esac
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

_decode_try() {
  local LC_ALL=C current="$1" remaining="$2" parent device parent_device
  (( decode_count < 2 )) || return 0
  if _decode_mount_root "$current" || [[ -L "$current" || ! -d "$current" || ! -r "$current" || ! -x "$current" ]]; then
    decode_uncertain=1
    return 0
  fi
  parent="${current%/*}"
  [[ -n "$parent" ]] || parent="/"
  if ! device=$(stat -c %d -- "$current" 2>/dev/null) ||
     ! parent_device=$(stat -c %d -- "$parent" 2>/dev/null) ||
     [[ ! "$device" =~ ^[0-9]+$ || ! "$parent_device" =~ ^[0-9]+$ || "$device" != "$parent_device" ]]; then
    decode_uncertain=1
    return 0
  fi
  if [[ -z "$remaining" ]]; then
    _decode_candidate "$current" "EXISTING"
    return 0
  fi

  local child name encoded rest matched=0
  local -a children=()
  # Checked enumeration includes dotfiles and captures failure instead of
  # silently treating an unreadable directory as an empty directory.
  if ! find "$current" -mindepth 1 -maxdepth 1 -print0 > "$find_tmp" 2>/dev/null; then
    decode_uncertain=1
    return 0
  fi
  while IFS= read -r -d '' child; do
    children+=("$child")
  done < "$find_tmp"
  for child in "${children[@]}"; do
    name="${child##*/}"
    if ! _decode_encode "$name"; then
      # An uninterpretable entry may collide with this slug.
      decode_uncertain=1
      continue
    fi
    if [[ "$remaining" == "$encoded" ]]; then
      rest=""
    elif [[ "$remaining" == "$encoded-"* ]]; then
      rest="${remaining:${#encoded}+1}"
      # A trailing hyphen cannot represent an empty child component.
      [[ -n "$rest" ]] || continue
    else
      continue
    fi
    matched=1
    if [[ -L "$child" || ! -d "$child" ]]; then
      decode_uncertain=1
    else
      _decode_try "$child" "$rest"
    fi
  done

  if [[ "$remaining" =~ ^[A-Za-z0-9]+$ ]]; then
    child="${current%/}/$remaining"
    if [[ ! -e "$child" && ! -L "$child" ]]; then
      _decode_candidate "$child" "ORPHANED"
    fi
  elif (( ! matched )); then
    # A reachable prefix with an unsupported suffix is another possible
    # original path, even when another branch already supports a candidate.
    decode_uncertain=1
  fi
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
