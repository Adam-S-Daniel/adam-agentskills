# Purpose

`ocr-pdfs` guides the local workflow from audited scanned PDFs to reviewed,
searchable copies while preserving the original documents.

## Why the workflow uses the installed CLI

[The missing-helper incident](https://github.com/Adam-S-Daniel/adam-agentskills/issues/26)
was carried over from
old-registry issue 189:
the instructions named a Python batch runner and a PowerShell WPF reviewer,
but neither script was shipped. Searches of the public and private
registries and their git histories found neither file to recover.

The skill therefore describes direct OCRmyPDF calls and review in an
installed PDF viewer. It does not promise automatic renaming, restart
indices, path remapping, or review key bindings from unavailable tools.
The existing [compare-pdfpairs skill](../compare-pdfpairs/SKILL.md) remains an optional comparison route
for documents already following its filename-pair convention.

The registry census checks bare script filenames in runnable Markdown code
blocks so an absent helper cannot hide merely because its reference omits
`scripts/`. Prose examples, external paths, and document artifacts remain
outside that payload check.
