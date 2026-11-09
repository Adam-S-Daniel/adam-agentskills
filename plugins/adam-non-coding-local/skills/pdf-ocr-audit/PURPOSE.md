# Purpose — pdf-ocr-audit

Maintenance context only; never loaded at inference.

## What it is for

Audits PDFs to decide whether OCR is needed to make them text-searchable, using the bundled check_ocr.py script and a fixed table output.

## Changes

- Prompt-audit fix (adam-agentskills issue #56): see docs/skill-impact.md.
- Issue #128 sweep: the large-folder bullet no longer lists iOS scanner names as needing OCR (they usually already have text, as the filename table says); see docs/skill-impact.md.
