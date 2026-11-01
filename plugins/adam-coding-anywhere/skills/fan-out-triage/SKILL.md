---
name: fan-out-triage
description: Triage a large list of items (release-note bullets, issues, alerts, findings; dozens to thousands) against one or more repos by fanning the work out to parallel subagents, without letting ID drift or a thin inventory corrupt the result. Use when asked to go through a changelog, backlog or report and decide which items affect your code, especially if you are about to split the list across agents. Not for a list you can read in one sitting, or for reviewing one dependency bump.
---

# Fan-out triage

Bulk triage fails in three quiet ways: the agents are judged against a thin
picture of what the repos use, an agent cites the wrong item ID while its
reasoning is right, and a missing output file gets read as "still working".
This skill fixes the order of work and the output contract so each failure is
caught by a check, not by luck.

Working files live under `triage/` in the repo being triaged.

## 1. Inventory first

Before any item is read, find every place the repos depend on the thing being
triaged: commands, flags, config keys, hooks, environment variables, pinned
versions. Use Explore agents or grep. Write `triage/inventory.md`, one line
per dependency, each with a real `path:line` reference:

```
acme publish --registry   ci/release.sh:4
```

Re-open every cited line before you save; a made-up reference is worse than a
gap. Every triage agent reads this one file, so its quality caps the triage's.

## 2. Cheap pass in the main loop

Grep the items for the high-signal terms from the inventory. This sizes the
job and tells you which themes matter. It decides nothing.

## 3. Chunk on natural boundaries

Split by source file or version, a few dozen items per chunk at most. Issue
one `Agent` call per chunk, all in a single message. Use the Workflow tool
only if the owner opted in to multi-agent orchestration; otherwise it must not
be launched. A chunk's agent writes `triage/chunks/<source-basename>.tsv`.

## 4. Output contract

One row per item, fields separated by ` | `, no header:

```
ID | first 8 words of the item | target | strength | reason
```

- `target` is the repo file the item affects, or `-` when none.
- `strength` is `strong` (our existing use breaks or changes), `weak` (an
  opportunity or a nearby surface) or `none`.
- `reason` names the inventory line it rests on. A strong row's reason also
  names the target file.
- The chunk file ends with `# items read: N`, where N is the number of items
  in its source, counted by the agent with `wc -l` or a parser, not estimated.

Merge the chunks into `triage/results.tsv` (rows plus one final
`# items read: N` for the whole list).

## 5. Machine-check every ID

Run `python3 scripts/check_ids.py --items <source files> --results
triage/results.tsv` from this skill's directory (adjust the path to where the
skill is installed). It fails on an ID that is not in the source, a "first 8
words" column that does not match that ID's own text, a duplicate or missing
item, and a read count that is not the item count. Run it on every chunk file
as it arrives and again on the merge. An off-by-two ID with a sensible reason
passes a human skim; only the words column catches it.

## 6. Watch progress by activity

A background agent often writes its file only at the end, so an absent file
says nothing. Check each agent's transcript modification time against a
staleness threshold you set before launching (say, 10 minutes). Past it, treat
the agent as stalled and relaunch that chunk.

## 7. Accept a candidate only after reading it

The main loop reads the source item itself and opens the file the agent named
before it accepts a `strong` row or quotes the item anywhere (an issue, a PR
body). An item that merely shares a keyword with the inventory is not a hit:
check that the changed behavior is one the cited line uses.
