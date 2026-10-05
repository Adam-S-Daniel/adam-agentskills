"""Windows PowerShell 5.1 reads BOM-less scripts as the ANSI codepage."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_plugin_powershell_scripts_are_ascii_or_utf8_with_bom():
    failures = []
    for path in sorted((ROOT / "plugins").rglob("*.ps1")):
        content = path.read_bytes()
        if not content.startswith(b"\xef\xbb\xbf") and any(byte > 127 for byte in content):
            failures.append(str(path.relative_to(ROOT)))
    assert not failures, "BOM-less non-ASCII PowerShell scripts: " + ", ".join(failures)
