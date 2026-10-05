---
name: ocr-pdfs
description: OCR scanned PDFs with OCRmyPDF, preserve the originals, and review page appearance and searchable text before replacing any files.
metadata:
  version: "1.0.1"
  tools: "Bash, Read, Write, WebSearch"
  triggers: "ocr my pdfs; run OCR on scanned PDFs; batch OCR pipeline; process scanned documents; make PDFs searchable; ocr-pdfs"
---

# OCR PDFs

Use [OCRmyPDF](https://ocrmypdf.readthedocs.io/en/latest/cookbook.html)
(backed by Tesseract) to add searchable text to scanned PDFs. This skill
uses the installed CLI directly; it does not ship a batch runner or a
Windows review application.

## Choose the inputs

Use the existing audit, or the sibling [pdf-ocr-audit skill](../pdf-ocr-audit/SKILL.md), to identify
PDFs that need OCR. Read the CSV with a CSV parser rather than splitting
lines or commas: filenames can contain both. Confirm that each selected
path exists in the current environment; paths from an earlier session may
need remapping.

Choose a separate output folder and preserve each input's relative path
under it so equal basenames from different folders cannot collide. Keep
originals untouched. If an output already exists, review it before deciding
whether to skip or rerun that input; existence alone does not prove that
OCR completed successfully.

## Check the environment

```bash
command -v ocrmypdf tesseract
ocrmypdf --version
tesseract --list-langs
```

If a dependency is missing, follow the
[OCRmyPDF installation instructions](https://ocrmypdf.readthedocs.io/en/latest/installation.html)
for this machine and Python version. Use a compatible stable release that
has been available for at least seven days. Install the Tesseract language
packs needed by the documents; do not assume that English fits every input.

## Run OCR and record results

Create the output parent directory, then run one selected input at a time.
Replace these example paths with the resolved input and distinct output:

```bash
ocrmypdf --skip-text --output-type pdf --optimize 0 \
  "/path/to/input/document.pdf" \
  "/path/to/ocr-output/document.pdf"
```

`--skip-text` leaves pages that already have text out of OCR. Use `-l` with
the installed language codes when the input requires another language.
Avoid rotation, deskewing, or cleanup options unless the user requests
those image changes.

For a batch, repeat this command for the selected paths. Record each
input/output pair and the command's actual exit code in a local progress
report. A nonzero exit is a failed item: keep its original, exclude any
partial output from the successful set, and report the failure. Resume
from that report rather than an assumed file index. Do not log extracted
document text or publish file paths.

## Review before replacement

Open each successful output and its original in the installed PDF viewer.
Check page count, order, orientation, legibility, and missing or altered
content; also search or select representative OCR text, including names,
numbers, and accented characters where present. Rerun [pdf-ocr-audit](../pdf-ocr-audit/SKILL.md) on
the output set to confirm that text is present, while recognizing that a
text layer alone does not prove its accuracy.

If the inputs already use the `name.pdf` / `name-needsocr.pdf` pairing
convention, the sibling [compare-pdfpairs skill](../compare-pdfpairs/SKILL.md) can additionally compare
rendered pages and extracted text. Follow that skill's own instructions
and prerequisites; it is not a WPF reviewer supplied by this skill.

Report successful, failed, and unreviewed items separately. Keep the
originals until the user has reviewed the proposed replacements or
deletions and explicitly authorized them.
