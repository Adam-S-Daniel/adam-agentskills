# Purpose

`rename-pdfs` exists to turn scanner-junk PDF filenames into a consistent,
descriptive, date-prefixed convention, as the natural follow-up to
`ocr-pdfs`. It walks a folder interactively, proposing one name at a time
from the document's own content and confirming with the user before
applying it — never a silent batch rename.

## Why legacy OCR backup pairs are still skipped

The [OCR skill](../ocr-pdfs/SKILL.md) now preserves originals and writes
searchable PDFs to a separate output folder. Existing folders may still
contain paired `*-needsocr.pdf` backups from the legacy convention. Renaming
one member alone would break that pairing, so the exclusion remains unless
the user explicitly requests coordinated names for both files.

## Why example data must be fictional

This skill's `SKILL.md` teaches the naming convention through worked
filename examples (a per-person specifier, a credential suffix like `MD`, a
date-range filename for a superbill). This registry (`adam-agentskills`) is a
public repo, so every example in it is public too. An earlier revision used
real personal details in its examples; they were replaced with fictional
ones that demonstrate the same naming patterns. Any future edit to these
examples should keep using invented names, amounts, and organizations —
never real ones, even the maintainer's own — and should not describe what
the real ones were.
