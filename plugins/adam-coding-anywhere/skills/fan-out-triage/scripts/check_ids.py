#!/usr/bin/env python3
"""Check a triage results file against its source items.

Usage: check_ids.py --items FILE [FILE ...] --results FILE

Item lines look like `- [ID] text`, `ID | text` or `ID: text`. Result rows
are `ID | first 8 words | target | strength | reason`; lines starting with
`#` are comments, and the last one must be `# items read: N`. Exit 0 when
every check passes, 1 otherwise. Every problem is printed.
"""
import argparse
import re
import sys

ITEM = re.compile(
    r"^\s*(?:[-*]\s+)?\[?(?P<id>[A-Za-z0-9][A-Za-z0-9._#-]*)\]?\s*[|:]?\s+(?P<text>\S.*)$")
STRENGTHS = {"strong", "weak", "none"}


def words(text, limit=None):
    tokens = re.sub(r"[`*_]", "", text).split()
    return [t.lower() for t in (tokens[:limit] if limit else tokens)]


def load_items(paths):
    items = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.rstrip("\n")
                # Skip headings, blank lines and prose; items start with a
                # list marker, a bracketed ID or an ID followed by | or :.
                if not re.match(r"^\s*(?:[-*]\s+\[|\[|[A-Za-z0-9][A-Za-z0-9._#-]*\s*[|:]\s)", line):
                    continue
                match = ITEM.match(line)
                if match:
                    items[match["id"]] = match["text"]
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", nargs="+", required=True)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    items = load_items(args.items)
    problems = []
    seen = {}
    read_count = None
    with open(args.results, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                match = re.fullmatch(r"# items read: (\d+)", line)
                if match:
                    read_count = int(match[1])
                continue
            fields = line.split(" | ")
            if len(fields) != 5:
                problems.append(f"line {number}: expected 5 ' | ' fields, got {len(fields)}")
                continue
            item_id, head, target, strength, reason = (f.strip() for f in fields)
            if item_id not in items:
                problems.append(f"line {number}: ID {item_id} is not in the source")
                continue
            if item_id in seen:
                problems.append(f"line {number}: ID {item_id} repeats line {seen[item_id]}")
            seen[item_id] = number
            if words(head) != words(items[item_id], 8):
                problems.append(
                    f"line {number}: words for {item_id} do not match its source text; "
                    f"source starts: {' '.join(items[item_id].split()[:8])}")
            if strength not in STRENGTHS:
                problems.append(f"line {number}: strength {strength!r} is not strong, weak or none")
            if strength == "strong" and target not in reason:
                problems.append(f"line {number}: strong row's reason does not name {target}")
    for item_id in items:
        if item_id not in seen:
            problems.append(f"item {item_id} has no row")
    if read_count is None:
        problems.append("no '# items read: N' line")
    elif read_count != len(items):
        problems.append(f"items read says {read_count}, source has {len(items)}")
    for problem in problems:
        print(problem)
    print(f"{len(items)} items, {len(seen)} rows, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
