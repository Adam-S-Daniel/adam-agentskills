#!/usr/bin/env python3
"""Tests for scripts/check_skills.py.

Hermetic and deterministic: every fixture is a throwaway registry tree built under
pytest's `tmp_path`, and the tool is exercised through its public functions
(`check_skills.run` / `check_skills.main`) rather than a subprocess. No network, no
sleeps, no wall-clock dependence.

The test config deliberately declares its OWN field list, limits and pattern rather than
reading `skills_registries.yml`, so re-tuning a shipped value cannot break a test of the
machinery. The exception is the "shipped config" section at the bottom, which reads
`skills_registries.yml` on purpose — those tests exist to pin the CONTRACT itself (the six
spec fields, the shapes they must take) rather than the code that enforces it.

Run: python3 -m pytest scripts/test_check_skills.py -q
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_skills  # noqa: E402


# =================================================================================
# Fixture builders
# =================================================================================

GOOD_FRONTMATTER = "---\nname: {name}\ndescription: A well formed skill.\n---\n"


def write_skill(
    registry_root: Path,
    rel_skill_dir: str,
    *,
    frontmatter: str = None,
    name: str = None,
    body: str = "",
    newline: str = "\n",
) -> Path:
    """Create <registry_root>/<rel_skill_dir>/SKILL.md and return the skill directory."""
    skill_dir = registry_root / rel_skill_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = GOOD_FRONTMATTER.format(name=name or skill_dir.name)
    text = frontmatter + body
    (skill_dir / "SKILL.md").write_bytes(text.replace("\n", newline).encode("utf-8"))
    return skill_dir


def registry_entry(name: str, path: str, layout: str = "skills/*/SKILL.md", **extra) -> dict:
    entry = {"name": name, "path": path, "layout": layout}
    entry.update(extra)
    return entry


def write_config(path: Path, registries, **extra) -> Path:
    data = {
        "required_fields": ["name", "description"],
        "name_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        "max_lengths": {"name": 64, "description": 200},
        "known_fields": ["name", "description", "compatibility", "metadata"],
        "field_types": {"name": "str", "description": "str", "compatibility": "str",
                        "metadata": "map-of-str"},
        "payload_dirs": ["scripts", "references", "assets", "templates", "hooks",
                         "tests", "examples"],
        "registries": list(registries),
    }
    data.update(extra)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run_tool(tmp_path: Path, registries, *, waivers=None, repo_root: Path = None, **extra):
    config = write_config(tmp_path / "config.yml", registries, **extra)
    waivers_path = tmp_path / "waivers.yml"
    waivers_path.write_text(yaml.safe_dump({"waivers": list(waivers or [])}), encoding="utf-8")
    return check_skills.run(config, waivers_path, {}, repo_root=repo_root or tmp_path)


def klasses(report) -> list:
    return sorted(finding.klass for finding in report.errors)


def messages(report, klass: str) -> list:
    return [f.message for f in report.errors if f.klass == klass]


@pytest.fixture
def local_registry(tmp_path):
    """A single registry declared as `path: .`, resolved against the tmp repo root."""
    return [registry_entry("alpha", ".")]


# =================================================================================
# Clean baseline
# =================================================================================

def test_a_symlinked_skill_directory_is_not_scanned_twice(tmp_path):
    """A skill entry may be a symlink to another plugin's skill in a sibling
    registry the census reads. On a symlink-capable checkout the layout glob follows it;
    the census must count the skill once, through its real directory."""
    write_skill(tmp_path, "plugins/alpha/skills/good-skill")
    link = tmp_path / "plugins" / "personal" / "skills" / "good-skill"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(os.path.join("..", "..", "alpha", "skills", "good-skill"), link,
                   target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    assert (link / "SKILL.md").is_file(), "the fixture link does not resolve"
    report = run_tool(tmp_path, [registry_entry("alpha", ".", "plugins/*/skills/*/SKILL.md")])
    assert report.errors == []
    assert report.skills_scanned == 1


def test_good_tree_produces_no_findings_and_exits_zero(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert report.waived == []
    assert report.exit_code == 0
    assert report.skills_scanned == 1
    assert "OK: 1 skills across 1 registries — 0 findings, 0 waived." in \
        check_skills.render_text(report, False)


# =================================================================================
# Per-file frontmatter checks — each fires on a bad fixture, not on a good one
# =================================================================================

def test_frontmatter_missing(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/no-frontmatter", frontmatter="# Just a heading\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FRONTMATTER_MISSING]
    assert [f.path for f in fired] == ["skills/no-frontmatter/SKILL.md"]


def test_frontmatter_missing_when_block_is_unterminated(tmp_path, local_registry):
    write_skill(tmp_path, "skills/unterminated", frontmatter="---\nname: unterminated\n")
    report = run_tool(tmp_path, local_registry)
    assert check_skills.K_FRONTMATTER_MISSING in klasses(report)


def test_yaml_parse_reports_first_line_and_position(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(
        tmp_path, "skills/bad-yaml",
        frontmatter='---\nname: bad-yaml\ndescription: a "quoted: thing" that: breaks\n---\n',
    )
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_YAML_PARSE]
    assert [f.path for f in fired] == ["skills/bad-yaml/SKILL.md"]
    message = fired[0].message
    assert "\n" not in message                      # first line only
    assert "frontmatter line" in message and "column" in message
    assert "SKILL.md line" in message


def test_frontmatter_not_map(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/listy", frontmatter="---\n- name: listy\n---\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FRONTMATTER_NOT_MAP]
    assert [f.path for f in fired] == ["skills/listy/SKILL.md"]
    assert "list" in fired[0].message


def test_missing_field_fires_for_absent_and_whitespace_only(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/no-desc", frontmatter="---\nname: no-desc\n---\n")
    write_skill(tmp_path, "skills/blank-desc",
                frontmatter='---\nname: blank-desc\ndescription: "   "\n---\n')
    report = run_tool(tmp_path, local_registry)
    fired = {f.path for f in report.errors if f.klass == check_skills.K_MISSING_FIELD}
    assert fired == {"skills/no-desc/SKILL.md", "skills/blank-desc/SKILL.md"}


def test_name_dir_mismatch(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/on-disk", name="in-frontmatter")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NAME_DIR_MISMATCH]
    assert [f.path for f in fired] == ["skills/on-disk/SKILL.md"]
    assert "in-frontmatter" in fired[0].message and "on-disk" in fired[0].message


def test_name_pattern(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/Bad_Name")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NAME_PATTERN]
    assert [f.path for f in fired] == ["skills/Bad_Name/SKILL.md"]


def test_length_limit_states_actual_length_and_limit(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    long_description = "x" * 250
    write_skill(tmp_path, "skills/verbose",
                frontmatter=f"---\nname: verbose\ndescription: {long_description}\n---\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_LENGTH_LIMIT]
    assert [f.path for f in fired] == ["skills/verbose/SKILL.md"]
    assert "250" in fired[0].message and "200" in fired[0].message


def test_length_limit_is_config_driven_not_hardcoded(tmp_path, local_registry):
    write_skill(tmp_path, "skills/verbose",
                frontmatter=f"---\nname: verbose\ndescription: {'x' * 250}\n---\n")
    relaxed = run_tool(tmp_path, local_registry, max_lengths={"description": 400})
    assert check_skills.K_LENGTH_LIMIT not in klasses(relaxed)
    strict = run_tool(tmp_path, local_registry, max_lengths={"description": 10})
    assert check_skills.K_LENGTH_LIMIT in klasses(strict)


def test_non_spec_field(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(
        tmp_path, "skills/extra",
        frontmatter="---\nname: extra\ndescription: Has an extra key.\nbogus: 1\n---\n",
    )
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NON_SPEC_FIELD]
    assert [f.path for f in fired] == ["skills/extra/SKILL.md"]
    assert "bogus" in fired[0].message


# =================================================================================
# field-type — the spec fixes a SHAPE for each field, not just a name
# =================================================================================

def frontmatter_with(name: str, extra: str = "") -> str:
    """Well-formed `name`/`description` frontmatter plus whatever `extra` YAML is under
    test. `extra` must be a complete, newline-terminated block."""
    return f"---\nname: {name}\ndescription: A well formed skill.\n{extra}---\n"


NESTED_COMPATIBILITY = "compatibility:\n  tools:\n    - gh\n  environment: any\n"


def test_field_type_fires_when_a_mapping_is_given_for_a_string_field(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/nested-compat",
                frontmatter=frontmatter_with("nested-compat", NESTED_COMPATIBILITY))
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FIELD_TYPE]
    assert [f.path for f in fired] == ["skills/nested-compat/SKILL.md"]
    # The actual type is what makes the message actionable, not just "must be a string".
    assert "compatibility" in fired[0].message
    assert "dict" in fired[0].message and "expected a string" in fired[0].message


def test_field_type_fires_when_a_list_is_given_for_a_string_field(tmp_path, local_registry):
    write_skill(tmp_path, "skills/listy-compat", frontmatter=frontmatter_with(
        "listy-compat", "compatibility:\n  - gh\n  - git\n"))
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FIELD_TYPE]
    assert len(fired) == 1
    assert "list" in fired[0].message and "expected a string" in fired[0].message


def test_field_type_silent_for_a_conforming_string(tmp_path, local_registry):
    write_skill(tmp_path, "skills/flat-compat", frontmatter=frontmatter_with(
        "flat-compat", "compatibility: Requires the GitHub CLI (gh). Runs anywhere.\n"))
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_field_type_fires_for_a_non_string_value_inside_metadata(tmp_path, local_registry):
    # `version: 1.0` is a YAML float, which is exactly how an unquoted version string
    # silently stops being a string.
    write_skill(tmp_path, "skills/meta-float",
                frontmatter=frontmatter_with("meta-float", "metadata:\n  version: 1.0\n"))
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FIELD_TYPE]
    assert len(fired) == 1
    assert "metadata" in fired[0].message
    assert "version" in fired[0].message and "float" in fired[0].message


def test_field_type_fires_when_metadata_is_not_a_mapping_at_all(tmp_path, local_registry):
    write_skill(tmp_path, "skills/meta-list",
                frontmatter=frontmatter_with("meta-list", "metadata:\n  - version\n"))
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FIELD_TYPE]
    assert len(fired) == 1
    assert "list" in fired[0].message and "expected a mapping" in fired[0].message


def test_field_type_silent_for_metadata_of_all_strings(tmp_path, local_registry):
    write_skill(tmp_path, "skills/meta-ok", frontmatter=frontmatter_with(
        "meta-ok", 'metadata:\n  version: "1.0.0"\n  tools: "Bash, Read, Write"\n'))
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_a_non_string_field_does_not_also_emit_a_bogus_length_limit(tmp_path, local_registry):
    """A character limit measured against a mapping reports its ENTRY COUNT as though it
    were a character count. The limit of 1 below is under that count on purpose: before
    length checks were restricted to strings, this fixture produced a second, meaningless
    `length-limit` finding stacked on top of the real one."""
    write_skill(tmp_path, "skills/nested-compat",
                frontmatter=frontmatter_with("nested-compat", NESTED_COMPATIBILITY))
    report = run_tool(tmp_path, local_registry, max_lengths={"compatibility": 1})
    assert klasses(report) == [check_skills.K_FIELD_TYPE]


def test_field_types_are_config_driven_not_hardcoded(tmp_path, local_registry):
    """A field absent from `field_types:` is not type-checked; adding it there — and
    nowhere in the .py — is what turns the check on."""
    write_skill(tmp_path, "skills/listy-license",
                frontmatter=frontmatter_with("listy-license", "license:\n  - MIT\n"))
    fields = ["name", "description", "license"]
    unchecked = run_tool(tmp_path, local_registry, known_fields=fields,
                         field_types={"name": "str"})
    assert check_skills.K_FIELD_TYPE not in klasses(unchecked)
    checked = run_tool(tmp_path, local_registry, known_fields=fields,
                       field_types={"name": "str", "license": "str"})
    assert check_skills.K_FIELD_TYPE in klasses(checked)


def test_a_declared_field_absent_from_the_file_is_not_a_finding(tmp_path, local_registry):
    write_skill(tmp_path, "skills/minimal")     # no compatibility, no metadata
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_an_unknown_declared_shape_is_a_usage_error_not_a_silent_no_op(tmp_path,
                                                                      local_registry):
    write_skill(tmp_path, "skills/good-skill")
    with pytest.raises(SystemExit):
        run_tool(tmp_path, local_registry, field_types={"compatibility": "strr"})


# =================================================================================
# dangling-payload-ref
# =================================================================================

FENCED_BODY = "\nRun it:\n\n```bash\npython scripts/x.py --schedule regular\n```\n"


def test_dangling_payload_ref_fires_for_absent_fenced_script(tmp_path, local_registry):
    write_skill(tmp_path, "skills/needy", body=FENCED_BODY)
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_DANGLING_PAYLOAD_REF]
    assert len(fired) == 1
    assert "scripts/x.py" in fired[0].message


def test_dangling_payload_ref_silent_when_the_file_exists(tmp_path, local_registry):
    skill_dir = write_skill(tmp_path, "skills/needy", body=FENCED_BODY)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "x.py").write_text("print('hi')\n", encoding="utf-8")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_dangling_payload_ref_strips_a_leading_dot_slash(tmp_path, local_registry):
    write_skill(tmp_path, "skills/dotslash", body="\n```bash\npython ./scripts/z.py\n```\n")
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1
    assert "'scripts/z.py'" in found[0]


def test_dangling_payload_ref_strips_trailing_punctuation(tmp_path, local_registry):
    write_skill(tmp_path, "skills/punct", body=(
        "\n```bash\n"
        "python scripts/w.py.\n"
        "bash scripts/v.sh,\n"
        "cat scripts/u.txt;\n"
        "```\n"
    ))
    report = run_tool(tmp_path, local_registry)
    found = sorted(messages(report, check_skills.K_DANGLING_PAYLOAD_REF))
    assert len(found) == 3
    assert any("'scripts/w.py'" in m for m in found)
    assert any("'scripts/v.sh'" in m for m in found)
    assert any("'scripts/u.txt'" in m for m in found)


def test_dangling_payload_ref_dedupes_within_one_skill(tmp_path, local_registry):
    write_skill(tmp_path, "skills/repeat", body=(
        "\n```bash\npython scripts/x.py\npython scripts/x.py\npython ./scripts/x.py\n```\n"))
    report = run_tool(tmp_path, local_registry)
    assert len(messages(report, check_skills.K_DANGLING_PAYLOAD_REF)) == 1


@pytest.mark.parametrize("command, filename", [
    ("python3 ocr_pdfs.py", "ocr_pdfs.py"),
    (r".\Compare-OcrPdfs.ps1", "Compare-OcrPdfs.ps1"),
    ("bash ./convert.sh", "convert.sh"),
    ("node review.js", "review.js"),
    ('python3 "review.py"', "review.py"),
    ("pwsh -File './review.ps1'", "review.ps1"),
    ("./review.sh --input scans", "review.sh"),
    ("review.sh --input scans", "review.sh"),
    ("python3 -u 'review.py' input.py", "review.py"),
    ('node "review script.js" input.js', "review script.js"),
    ("pwsh -NoProfile -File review.ps1 input.ps1", "review.ps1"),
    ("printf ready && python3 ocr_pdfs.py --input scans", "ocr_pdfs.py"),
    (r"printf ready && .\Compare-OcrPdfs.ps1 -InputFolder scans", "Compare-OcrPdfs.ps1"),
    ("printf ready | review.sh", "review.sh"),
])
def test_bare_script_in_code_block_requires_a_local_payload(
    tmp_path, local_registry, command, filename
):
    skill_dir = write_skill(tmp_path, "skills/bare-script", body=f"\n```\n{command}\n```\n")
    missing = run_tool(tmp_path, local_registry)
    found = messages(missing, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1 and f"'{filename}'" in found[0]

    (skill_dir / filename).write_text("# local helper\n", encoding="utf-8")
    assert run_tool(tmp_path, local_registry).errors == []


def test_bare_script_in_prose_is_a_dismissed_candidate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/prose-script", body="\nAnother skill uses `review.py`.\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["review.py"] == check_skills.PROSE_ONLY_RULE


@pytest.mark.parametrize("language, command, filename", [
    ("powershell", '.\\Compare-OcrPdfs.ps1 `\n'
     '  -FolderPath "C:\\documents\\inbox" `\n'
     '  -FrameDelay 500 `\n  -StartAt 0', "Compare-OcrPdfs.ps1"),
    ("powershell", '.\\Compare-OcrPdfs.ps1 `\r\n'
     '  -FolderPath "C:\\documents\\inbox" `\r\n'
     '  -FrameDelay 500 `\r\n  -StartAt 0', "Compare-OcrPdfs.ps1"),
    ("bash", 'python3 ocr_pdfs.py \\\n'
     '  --csv /path/to/audit.csv \\\n'
     '  --log /path/to/progress.log \\\n  --workers 2\n'
     'python3 ocr_pdfs.py --dry-run\npython3 ocr_pdfs.py --resume', "ocr_pdfs.py"),
])
def test_original_multiline_ocr_invocations_still_require_payloads(
    tmp_path, local_registry, language, command, filename
):
    skill_dir = write_skill(tmp_path, "skills/multiline-ocr",
                            body=f"\n```{language}\n{command}\n```\n")
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1 and f"'{filename}'" in found[0]
    (skill_dir / filename).write_text("# local helper\n", encoding="utf-8")
    assert run_tool(tmp_path, local_registry).errors == []


@pytest.mark.parametrize("language, command, filename", [
    ("bash", "cd cms-platform/e2e && npx playwright test "
     "--project=chromium-light decap-config-render-parity.test.js",
     "decap-config-render-parity.test.js"),
    ("", "  → patch-preview-config.sh → repoint admin/config.yml for this PR",
     "patch-preview-config.sh"),
    ("bash", 'printf "%s" "review.py"', "review.py"),
    ("bash", "python3 main.py input.py", "input.py"),
    ("bash", "bash main.sh input.sh", "input.sh"),
    ("bash", "node main.js input.js", "input.js"),
    ("powershell", "pwsh -File main.ps1 input.ps1", "input.ps1"),
    ("powershell", "pwsh -Command Write-Output -File output.ps1", "output.ps1"),
    ("powershell", "pwsh -File main.ps1 -File output.ps1", "output.ps1"),
    ("bash", "python3 -c 'print(1)' input.py", "input.py"),
    ("bash", "node --eval 'console.log(1)' input.js", "input.js"),
    ("bash", "bash -c 'printf ready' input.sh", "input.sh"),
    ("", "python3 review.py && (", "review.py"),
    ("bash", "value=$((1 + 2))\npython3 review.py", "review.py"),
])
def test_bare_script_outside_supported_invocation_positions_is_dismissed(
    tmp_path, local_registry, language, command, filename
):
    skill_dir = write_skill(tmp_path, "skills/arguments",
                            body=f"\n```{language}\n{command}\n```\n")
    for helper in ("main.py", "main.sh", "main.js", "main.ps1"):
        (skill_dir / helper).write_text("# local helper\n", encoding="utf-8")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)[filename] == check_skills.BARE_SCRIPT_NOT_INVOKED_RULE


def test_repo_relative_script_argument_keeps_its_structural_dismissal(
    tmp_path, local_registry
):
    write_skill(tmp_path, "skills/repo-path", body=(
        "\n```bash\ncd project && node e2e/review.js\n```\n"))
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["e2e/review.js"] == "not-payload-dir"


@pytest.mark.parametrize("commands", [
    "printf '%s' review.py\npython3 review.py",
    "python3 review.py\nprintf '%s' review.py",
])
def test_bare_script_invocation_wins_over_arguments_and_prose_when_deduped(
    tmp_path, local_registry, commands
):
    body = f"\nAnother skill uses `review.py`.\n\n```bash\n{commands}\n```\n"
    write_skill(tmp_path, "skills/repeated-invocation", body=body)
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1 and "'review.py'" in found[0]
    candidates = check_skills.extract_candidates(body, ["scripts"])
    matching = [candidate for candidate in candidates if candidate.value == "review.py"]
    assert len(matching) == 1
    assert matching[0].origin == "fenced" and matching[0].dismissed_by is None


def test_qualified_payload_arguments_still_gate_without_being_invoked(
    tmp_path, local_registry
):
    write_skill(tmp_path, "skills/qualified-argument",
                body="\n```bash\ncat scripts/review.py\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert len(messages(report, check_skills.K_DANGLING_PAYLOAD_REF)) == 1


@pytest.mark.parametrize("filename, rule", [
    ("/opt/tools/review.py", "absolute-path"),
    (r"..\other\review.ps1", "parent-traversal"),
    ("https://example.com/review.py", "url-scheme"),
    ("<helper>.py", "placeholder"),
    ("*.ps1", "glob-metacharacter"),
    ("other-skill/review.py", "not-payload-dir"),
    ("results.csv", "no-slash"),
])
def test_bare_script_extension_does_not_expand_external_or_artifact_paths(
    tmp_path, local_registry, filename, rule
):
    write_skill(tmp_path, "skills/external-script", body=f"\n```\n{filename}\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)[check_skills.normalise_candidate(filename)] == rule


# ---------------------------------------------------------------------------------
# Precision guards: the classes that deliberately do NOT gate. Each is a real
# false-positive shape observed across the three live registries.
# ---------------------------------------------------------------------------------

def test_fenced_reference_to_an_existing_directory_is_not_a_finding(tmp_path, local_registry):
    skill_dir = write_skill(tmp_path, "skills/dirref", body="\n```bash\nls assets/img\n```\n")
    (skill_dir / "assets" / "img").mkdir(parents=True)
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_bare_directory_with_a_trailing_slash_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/baredir", body="\n```bash\nls scripts/\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/"] == "trailing-slash"


def test_angle_bracket_placeholder_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/placeholder", body="\n```bash\nbash scripts/<name>.sh\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/<name>.sh"] == "placeholder"


def test_prose_backtick_reference_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/prosey",
                body="\nThe sibling skill ships `scripts/absent.py`, which lives elsewhere.\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/absent.py"] == check_skills.PROSE_ONLY_RULE


def test_prose_markdown_link_target_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/linky", body="\nSee [the helper](scripts/y.py) for detail.\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/y.py"] == check_skills.PROSE_ONLY_RULE


def test_a_reference_in_both_prose_and_a_fenced_block_still_gates(tmp_path, local_registry):
    write_skill(tmp_path, "skills/both", body=(
        "\nThe entry point is `scripts/x.py`.\n\n```bash\npython scripts/x.py\n```\n"))
    report = run_tool(tmp_path, local_registry)
    assert len(messages(report, check_skills.K_DANGLING_PAYLOAD_REF)) == 1


def test_an_indented_fence_inside_a_list_item_still_gates(tmp_path, local_registry):
    # Fences nested in a list item are indented; treating only column-0 fences as fenced
    # would silently drop this whole class.
    write_skill(tmp_path, "skills/nested", body=(
        "\n- **The tool** — run it like so:\n\n  ```bash\n  node scripts/tool.js --fix\n  ```\n"))
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1 and "'scripts/tool.js'" in found[0]


NON_CANDIDATES = {
    "https://example.com/scripts/x.py": "url-scheme",
    "/etc/passwd": "absolute-path",
    "~/.claude/skills/foo": "home-relative",
    "../other/scripts/x.py": "parent-traversal",
    "scripts/*.py": "glob-metacharacter",
    "${VAR}/scripts/x.py": "placeholder",
    "scripts/<name>.sh": "placeholder",
    ".github/workflows/ci.yml": "not-payload-dir",
    "scripts": "no-slash",
    "scripts/": "trailing-slash",
}


def _dismissed(report) -> dict:
    """Map every dismissed candidate value -> the rule that dismissed it."""
    out = {}
    for _skill, candidates in report.dismissed:
        for candidate in candidates:
            out[candidate.value] = candidate.dismissed_by
    return out


def test_dangling_payload_ref_dismisses_non_candidates_with_the_expected_rule(
    tmp_path, local_registry
):
    # Inside a fenced block, so it is the STRUCTURAL rules under test, not prose-only.
    body = "\n```bash\n" + "\n".join(NON_CANDIDATES) + "\n```\n"
    write_skill(tmp_path, "skills/tricky", body=body)
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []

    dismissed = _dismissed(report)
    for raw, expected_rule in NON_CANDIDATES.items():
        value = check_skills.normalise_candidate(raw)
        assert dismissed.get(value) == expected_rule, (raw, value, dismissed.get(value))


def test_dismissed_candidates_are_listed_for_audit(tmp_path, local_registry):
    write_skill(tmp_path, "skills/tricky",
                body="\nSee `scripts/*.py` for the glob and `scripts/absent.py` for the ref.\n")
    report = run_tool(tmp_path, local_registry)
    text = check_skills.render_text(report, True)
    assert "DISMISSED PAYLOAD CANDIDATES" in text
    assert "glob-metacharacter" in text and "scripts/*.py" in text
    # The recall gap the precision trade gives up is named, not silently dropped.
    assert check_skills.PROSE_ONLY_RULE in text and "scripts/absent.py" in text
    # ...and stays out of the way unless asked for.
    assert "DISMISSED PAYLOAD CANDIDATES" not in check_skills.render_text(report, False)


def test_code_block_tokenisation_keeps_only_the_path_token(tmp_path):
    fenced, prose = check_skills.split_code_regions(
        "before `scripts/inline.py`\n"
        "```bash\npython scripts/next_break.py --schedule regular\n```\nafter\n"
    )
    assert fenced == ["python", "scripts/next_break.py", "--schedule", "regular"]
    assert prose == ["scripts/inline.py"]
    candidates = check_skills.extract_candidates(
        "```bash\npython scripts/next_break.py --schedule regular\n```\n", ["scripts"]
    )
    qualifying = [c.value for c in candidates if c.dismissed_by is None]
    assert qualifying == ["scripts/next_break.py"]


def test_commonmark_block_forms_the_regex_scanner_could_not_see(tmp_path):
    """~~~ fences, info strings, and 4-space indented code blocks are all code."""
    fenced, _prose = check_skills.split_code_regions(
        "~~~python\nrun scripts/tilde.py\n~~~\n\n    run scripts/indented.py\n"
    )
    assert "scripts/tilde.py" in fenced
    assert "scripts/indented.py" in fenced


def test_link_nested_inside_emphasis_is_still_found(tmp_path):
    _fenced, prose = check_skills.split_code_regions("*See [it](scripts/nested.py) here.*\n")
    assert prose == ["scripts/nested.py"]


def test_inline_walk_recurses_into_nested_children():
    """The commonmark preset emits a FLAT inline stream, so no document exercises the
    recursive descent — but `children` can nest (plugins, future presets), and a
    non-recursive walk would silently drop those. Driven with synthetic tokens because
    that is the only way to reach the branch."""
    from markdown_it.token import Token

    span = Token("code_inline", "code", 0)
    span.content = "scripts/deep.py"
    link = Token("link_open", "a", 1)
    link.attrSet("href", "scripts/deep-link.py")
    inner = Token("inline", "", 0)
    inner.children = [span, link]
    root = Token("inline", "", 0)
    root.children = [inner]

    collected = []
    check_skills._walk_inline(root, collected)
    assert collected == ["scripts/deep.py", "scripts/deep-link.py"]


# =================================================================================
# Cross-registry basename census
# =================================================================================

def test_crlf_only_difference_classifies_as_mirror(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared", body="\nSame content.\n")
    write_skill(right, "skills/shared", body="\nSame content.\n", newline="\r\n")
    report = run_tool(tmp_path, [registry_entry("left", "left"), registry_entry("right", "right")])

    groups = {group.basename: group for group in report.duplicate_groups}
    assert groups["shared"].verdict == "mirror"
    sizes = {loc.size for loc in groups["shared"].locations}
    assert len(sizes) == 2, "fixture must actually differ in raw bytes"
    assert len({loc.sha256 for loc in groups["shared"].locations}) == 2
    assert len({loc.sha256_nocr for loc in groups["shared"].locations}) == 1
    assert "mirror" in messages(report, check_skills.K_UNDECLARED_DUPLICATE)[0]


def test_genuinely_different_copies_classify_as_fork(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared", body="\nOne body.\n")
    write_skill(right, "skills/shared", body="\nA different body.\n")
    report = run_tool(tmp_path, [registry_entry("left", "left"), registry_entry("right", "right")])
    assert report.duplicate_groups[0].verdict == "fork"
    assert "fork" in messages(report, check_skills.K_UNDECLARED_DUPLICATE)[0]


def test_duplicates_within_one_registry_are_not_a_cross_registry_finding(tmp_path):
    solo = tmp_path / "solo"
    write_skill(solo, "a/skills/shared")
    write_skill(solo, "b/skills/shared")
    report = run_tool(tmp_path, [registry_entry("solo", "solo", layout="*/skills/*/SKILL.md")])
    assert report.duplicate_groups == []
    assert check_skills.K_UNDECLARED_DUPLICATE not in klasses(report)


def test_undeclared_duplicate_is_keyed_by_basename_for_waivers(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared")
    write_skill(right, "skills/shared")
    waiver = {"klass": check_skills.K_UNDECLARED_DUPLICATE, "registry": "left",
              "path": "shared", "reason": "mirrored on purpose",
              "issue": "https://example.com/issues/1"}
    report = run_tool(
        tmp_path,
        [registry_entry("left", "left"), registry_entry("right", "right")],
        waivers=[waiver],
    )
    assert report.errors == []
    assert len(report.waived) == 1
    assert report.exit_code == 0


# =================================================================================
# Registry resolution
# =================================================================================

def test_existing_but_empty_registry_scans_zero_skills_and_is_not_an_error(tmp_path):
    empty = tmp_path / "empty"
    (empty / "plugins" / "adam-private" / "skills").mkdir(parents=True)
    (empty / "plugins" / "adam-private" / "skills" / ".gitkeep").write_text("", encoding="utf-8")
    report = run_tool(
        tmp_path,
        [registry_entry("private", "empty", layout="plugins/*/skills/*/SKILL.md")],
    )
    assert report.errors == []
    assert report.exit_code == 0
    assert report.registries[0].status == "scanned"
    assert report.registries[0].skills_scanned == 0
    # Not silently passing: the empty registry is still named and counted in the report.
    text = check_skills.render_text(report, False)
    assert "private" in text and "0 skills" in text
    assert "OK: 0 skills across 1 registries" in text


def test_missing_required_registry_is_an_error(tmp_path):
    report = run_tool(tmp_path, [registry_entry("gone", "nowhere")])
    assert klasses(report) == [check_skills.K_REGISTRY_UNRESOLVED]
    assert report.unresolved_required == 1
    assert report.exit_code == 1
    assert report.registries[0].status == "unresolved"


def test_optional_registry_without_a_reason_is_still_an_error(tmp_path):
    report = run_tool(tmp_path, [registry_entry("gone", "nowhere", optional=True)])
    assert klasses(report) == [check_skills.K_REGISTRY_UNRESOLVED]
    assert report.exit_code == 1


def test_missing_optional_registry_with_a_reason_is_skipped(tmp_path):
    report = run_tool(
        tmp_path,
        [registry_entry("gone", "nowhere", optional=True, reason="cloned only in CI")],
    )
    assert report.errors == []
    assert report.exit_code == 0
    assert report.registries[0].status == "skipped"
    assert report.skips == ["SKIPPED: gone — cloned only in CI"]
    text = check_skills.render_text(report, False)
    assert "SKIPPED: gone — cloned only in CI" in text
    assert "SKIPPED (1)" in text


def test_registry_override_wins_over_the_configured_path(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    write_skill(elsewhere, "skills/moved")
    config = write_config(tmp_path / "config.yml", [registry_entry("alpha", "nowhere")])
    waivers = tmp_path / "waivers.yml"
    waivers.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    report = check_skills.run(config, waivers, {"alpha": "elsewhere"}, repo_root=tmp_path)
    assert report.errors == []
    assert report.registries[0].overridden is True
    assert report.registries[0].skills_scanned == 1


def test_unknown_registry_override_is_rejected(tmp_path):
    config = write_config(tmp_path / "config.yml", [registry_entry("alpha", ".")])
    waivers = tmp_path / "waivers.yml"
    waivers.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        check_skills.run(config, waivers, {"typo": "."}, repo_root=tmp_path)


# =================================================================================
# Waivers
# =================================================================================

def _non_spec_waiver(**extra):
    waiver = {
        "klass": check_skills.K_NON_SPEC_FIELD,
        "registry": "alpha",
        "path": "skills/extra/SKILL.md",
        "reason": "tolerated until the field is spec'd",
        "issue": "https://example.com/issues/55",
    }
    waiver.update(extra)
    return waiver


def _write_extra_field_skill(tmp_path):
    write_skill(
        tmp_path, "skills/extra",
        frontmatter="---\nname: extra\ndescription: Has an extra key.\nbogus: 1\n---\n",
    )


def test_waiver_suppresses_its_finding_and_reprints_it(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    report = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver()])
    assert report.errors == []
    assert len(report.waived) == 1
    assert report.waived[0][0].klass == check_skills.K_NON_SPEC_FIELD
    assert report.exit_code == 0
    text = check_skills.render_text(report, False)
    assert "WAIVED (1)" in text
    assert "https://example.com/issues/55" in text
    assert "bogus" in text, "a waived finding is re-printed, never hidden"


def test_waiver_match_substring_must_appear_in_the_message(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    hit = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver(match="bogus")])
    assert hit.errors == [] and len(hit.waived) == 1

    miss = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver(match="something-else")])
    assert sorted(klasses(miss)) == [check_skills.K_NON_SPEC_FIELD, check_skills.K_STALE_WAIVER]
    assert miss.exit_code == 1


def test_waiver_matching_nothing_is_a_stale_waiver_and_fails_the_run(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    report = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver()])
    assert klasses(report) == [check_skills.K_STALE_WAIVER]
    assert report.stale_waiver_count == 1
    assert report.exit_code == 1
    assert "skills/extra/SKILL.md" in report.errors[0].message


def test_waiver_is_scoped_to_its_registry_and_path(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    wrong_registry = run_tool(tmp_path, local_registry,
                              waivers=[_non_spec_waiver(registry="beta")])
    assert check_skills.K_NON_SPEC_FIELD in klasses(wrong_registry)
    assert check_skills.K_STALE_WAIVER in klasses(wrong_registry)

    wrong_path = run_tool(tmp_path, local_registry,
                          waivers=[_non_spec_waiver(path="skills/other/SKILL.md")])
    assert check_skills.K_NON_SPEC_FIELD in klasses(wrong_path)
    assert check_skills.K_STALE_WAIVER in klasses(wrong_path)


def test_malformed_waiver_is_reported_not_ignored(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    broken = _non_spec_waiver()
    del broken["issue"]
    report = run_tool(tmp_path, local_registry, waivers=[broken])
    assert klasses(report) == [check_skills.K_WAIVER_INVALID]
    assert "issue" in report.errors[0].message
    assert report.exit_code == 1


def test_absent_waivers_file_means_nothing_is_waived(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    config = write_config(tmp_path / "config.yml", local_registry)
    report = check_skills.run(config, tmp_path / "no-such-waivers.yml", {}, repo_root=tmp_path)
    assert report.waivers_present is False
    assert check_skills.K_NON_SPEC_FIELD in klasses(report)


# =================================================================================
# Exit codes and output modes (through main())
# =================================================================================

def _absolute_registry_config(tmp_path, registry_root: Path) -> Path:
    return write_config(tmp_path / "config.yml",
                        [registry_entry("alpha", str(registry_root))])


def _empty_waivers(tmp_path) -> Path:
    path = tmp_path / "waivers.yml"
    path.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    return path


def test_main_exits_zero_on_a_clean_tree(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/good-skill")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
    ])
    assert code == 0
    assert "OK: 1 skills across 1 registries — 0 findings, 0 waived." in capsys.readouterr().out


def test_main_exits_one_on_a_single_finding(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/on-disk", name="in-frontmatter")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "ERRORS (1)" in out
    assert check_skills.K_NAME_DIR_MISMATCH in out


def test_main_json_mode_emits_exactly_one_json_object(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/on-disk", name="in-frontmatter")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
        "--json",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)  # raises if anything else was printed
    assert code == payload["exit_code"] == 1
    assert payload["skills_scanned"] == 1
    assert payload["registries_scanned"] == 1
    assert [f["klass"] for f in payload["findings"]] == [check_skills.K_NAME_DIR_MISMATCH]
    assert "dismissed_candidates" not in payload


def test_main_json_mode_includes_dismissed_candidates_when_asked(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/tricky", body="\nSee `scripts/*.py`.\n")
    check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
        "--json", "--list-findings",
    ])
    payload = json.loads(capsys.readouterr().out)
    rules = [c["dismissed_by"]
             for entry in payload["dismissed_candidates"] for c in entry["candidates"]]
    assert "glob-metacharacter" in rules


# =================================================================================
# The shipped config itself must stay loadable and self-consistent
# =================================================================================

def test_shipped_config_and_waivers_are_loadable(tmp_path):
    config = check_skills.load_yaml_mapping(check_skills.DEFAULT_CONFIG, "config")
    assert isinstance(config.get("registries"), list) and config["registries"]
    for entry in config["registries"]:
        assert entry.get("name") and entry.get("layout")
        if not entry.get("optional"):
            continue
        assert str(entry.get("reason") or "").strip(), \
            f"optional registry {entry['name']} needs a non-empty reason"
    waivers, findings, present = check_skills.load_waivers(check_skills.DEFAULT_WAIVERS)
    assert present is True
    assert findings == []
    assert isinstance(waivers, list)


# The six fields https://agentskills.io/specification recognises — the whole contract.
SPEC_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")


def _shipped_contract() -> dict:
    config = check_skills.load_yaml_mapping(check_skills.DEFAULT_CONFIG, "config")
    return {key: config[key] for key in ("known_fields", "field_types", "max_lengths")}


def test_shipped_known_fields_are_exactly_the_six_spec_fields():
    assert sorted(_shipped_contract()["known_fields"]) == sorted(SPEC_FIELDS)


def test_a_key_outside_the_spec_six_fires_non_spec_field(tmp_path, local_registry):
    """The regression this locks in: `version`, `tools` and `triggers` were once listed as
    known_fields, which silently exempted the only skill that used them. Run against the
    SHIPPED contract, so re-adding any of them here would fail."""
    write_skill(tmp_path, "skills/legacy", frontmatter=frontmatter_with(
        "legacy", "version: 1.0.0\ntools:\n  - Bash\ntriggers:\n  - do the thing\n"))
    report = run_tool(tmp_path, local_registry, **_shipped_contract())
    fired = messages(report, check_skills.K_NON_SPEC_FIELD)
    assert klasses(report) == [check_skills.K_NON_SPEC_FIELD] * 3
    for key in ("version", "tools", "triggers"):
        assert any(f"'{key}'" in message for message in fired), key


def test_every_spec_field_is_accepted_in_its_spec_shape(tmp_path, local_registry):
    """The other direction: a skill using all six fields, each in the shape the spec
    requires, is clean. Note `allowed-tools` is a space-separated STRING, not a list."""
    write_skill(tmp_path, "skills/full", frontmatter=(
        "---\nname: full\ndescription: Uses every field the spec defines.\n"
        "license: MIT\ncompatibility: Runs in any environment.\n"
        "allowed-tools: Bash Read Write\n"
        'metadata:\n  version: "1.0.0"\n---\n'))
    report = run_tool(tmp_path, local_registry, **_shipped_contract())
    assert report.errors == []


def test_every_length_limited_field_is_also_declared_a_string():
    """Length checks skip non-strings, so a `max_lengths:` entry on a field that is not
    declared `str` would silently stop being enforced with nothing else reporting it."""
    contract = _shipped_contract()
    for name in contract["max_lengths"]:
        assert contract["field_types"].get(name) == check_skills.TYPE_STR, name


def test_shipped_field_types_declare_only_recognised_shapes():
    for name, shape in _shipped_contract()["field_types"].items():
        assert shape in check_skills.KNOWN_FIELD_TYPES, (name, shape)


# =================================================================================
# Missing dependency — the "cannot run" exit, distinct from a verdict
# =================================================================================


@pytest.mark.parametrize("dependency", ["markdown_it", "bashlex"])
def test_a_missing_dependency_exits_2_and_names_the_remedy(tmp_path, dependency):
    """A hosted session has none of `requirements-dev.txt` installed, so the import of
    `markdown_it` is the first thing that fails there. It used to fail as a bare
    ModuleNotFoundError traceback, which reads as "this script is broken" rather than
    "this environment is missing a declared dependency" — and a skills-doctor run that
    reads it that way falls back to eyeballing the payload check, which over-reports
    every repo-relative reference as a dangling payload (measured: 21 false positives,
    against 0 real findings from this tool).

    A subprocess, because the behaviour under test happens at module-import time and
    cannot be observed from a process that has already imported the module.
    """
    blocked = tmp_path / "blockmod"
    blocked.mkdir()
    (blocked / f"{dependency}.py").write_text(
        f'raise ImportError("No module named {dependency}", name="{dependency}")\n')

    env = dict(os.environ, PYTHONPATH=str(blocked))
    proc = subprocess.run(
        [sys.executable, str(Path(check_skills.__file__))],
        capture_output=True, text=True, env=env)

    # 2 is "nothing was checked", never 1 ("checked, and here are the findings").
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert dependency in proc.stderr
    assert "requirements-dev.txt" in proc.stderr
    # The remedy has to survive the distro PyYAML that ships without installer
    # metadata, or the named command fails and the reader is no better off.
    assert "--ignore-installed PyYAML" in proc.stderr
    # Nothing may be reported as a result, because nothing ran.
    assert proc.stdout == ""


# =================================================================================
# Advisories: british-spelling and dangling-reference (warn-only, owner decision D5)
# =================================================================================
#
# The tables mirror _agent-guidance's test/test-check-agent-markdown.js (PR #255), so
# the two checkers stay in step. Every WARN row is run through `main` twice: without
# --strict it must exit 0 (the warn-only contract) and with --strict it must exit 1 —
# the negative control that shows each rule really fires and really is the thing
# --strict gates, so a table row cannot pass by the checker doing nothing.
#
# A body is written to the registry ROOT's AGENTS.md, which has no frontmatter, so the
# expected line numbers are the body's own.

ADVISORY_SPELLING_WARN = [
    ("-our family", "Check the behaviour here.\n", [("british-spelling", 1)]),
    ("-our, capitalized", "Colour matters.\n", [("british-spelling", 1)]),
    ("-our derived form", "An honourable exit.\n", [("british-spelling", 1)]),
    ("favour", "We favour tests.\n", [("british-spelling", 1)]),
    ("-ise verb", "Please organise the files.\n", [("british-spelling", 1)]),
    ("-isation noun", "The organisation owns it.\n", [("british-spelling", 1)]),
    ("recognise", "We recognise the form.\n", [("british-spelling", 1)]),
    ("analyse", "Analyse the log.\n", [("british-spelling", 1)]),
    ("catalogue", "See the catalogue.\n", [("british-spelling", 1)]),
    ("centre", "Centre the text.\n", [("british-spelling", 1)]),
    ("licence", "The licence file.\n", [("british-spelling", 1)]),
    ("summarise", "Summarise the diff.\n", [("british-spelling", 1)]),
    ("artefact", "Keep the artefact.\n", [("british-spelling", 1)]),
    ("authorise", "Authorise the push.\n", [("british-spelling", 1)]),
    ("in a heading", "# The behaviour\n", [("british-spelling", 1)]),
    ("in a list item", "- first\n- the colour red\n", [("british-spelling", 2)]),
    ("in a table cell", "| a | b |\n|---|---|\n| x | colour |\n", [("british-spelling", 3)]),
    ("in link text", "[behaviour](https://example.com/x)\n", [("british-spelling", 1)]),
    ("on the second line of a paragraph", "fine line\nthe colour here\n",
     [("british-spelling", 2)]),
    ("after a code fence closes", "```\ncolour\n```\n\ncolour\n", [("british-spelling", 5)]),
    ("two words, one line", "behaviour and colour\n",
     [("british-spelling", 1), ("british-spelling", 1)]),
]

ADVISORY_SPELLING_QUIET = [
    ("American spellings", "The behavior, color, honor, organize and analyze.\n"),
    ("exercise/promise/otherwise are not flagged", "Exercise the promise otherwise.\n"),
    ("analyses (American plural) is not flagged", "Two analyses agree.\n"),
    ("cancelled (API value) is not flagged", "The run was cancelled.\n"),
    ("fenced code", "```\nthe colour and behaviour\n```\n"),
    ("tilde-fenced code", "~~~\nthe colour\n~~~\n"),
    ("fenced code with a language", "```yaml\ncolour: red\n```\n"),
    ("indented code", "text\n\n    colour = 1\n"),
    ("inline code", "Set `colour` to red.\n"),
    ("block quote", "> The vendor writes behaviour here.\n"),
    ("nested block quote", "> > colour\n"),
    ("lazy block quote continuation", "> quoted\ncolour continues the quote\n"),
    ("URL in prose", "See https://example.com/colour/behaviour for it.\n"),
    ("autolink", "See <https://example.com/organisation>.\n"),
    ("link target", "[docs](https://example.com/behaviour)\n"),
    ("HTML comment", "<!-- colour -->\n"),
    ("whole-word match only", "xcolour colourx behavioral.\n"),
]

ADVISORY_REF_WARN = [
    ("quoted name, no such heading", '# Top\n\nSee "Missing" above.\n', [("dangling-reference", 3)]),
    ("curly quotes", "# Top\n\nsee “Missing” below.\n", [("dangling-reference", 3)]),
    ("backticked heading marker", "# Top\n\nsee `## Missing` below.\n",
     [("dangling-reference", 3)]),
    ("bold name", "# Top\n\nsee **Missing** above.\n", [("dangling-reference", 3)]),
    ("italic name", "# Top\n\nsee *Missing* above.\n", [("dangling-reference", 3)]),
    ("link text", "# Top\n\nsee [Missing](#missing) below.\n", [("dangling-reference", 3)]),
    ("with 'the' and 'section'", '# Top\n\nSee the "Missing" section above.\n',
     [("dangling-reference", 3)]),
    ("with 'also'", '# Top\n\nSee also "Missing" below.\n', [("dangling-reference", 3)]),
    ("inside a list item", '# Top\n\n- one\n- (see "Missing" above)\n',
     [("dangling-reference", 4)]),
    ("on a later line of the paragraph", '# Top\n\nline one\nand see "Missing"\nabove.\n',
     [("dangling-reference", 4)]),
    ("a '## ' inside a fence is not a heading (real parser)",
     '# Top\n\n```\n## Ghost\n```\n\nsee "Ghost" above.\n', [("dangling-reference", 7)]),
    ("an indented heading-looking line is code",
     '# Top\n\n    ## Ghost\n\nsee "Ghost" above.\n', [("dangling-reference", 5)]),
    ("a near miss is still dangling", '## The rule\n\nsee "The rules" above.\n',
     [("dangling-reference", 3)]),
    ("two refs, one good one bad", '## Good\n\nsee "Good" above, see "Bad" below.\n',
     [("dangling-reference", 3)]),
]

ADVISORY_REF_QUIET = [
    ("quoted name matches an h2", '## Setup\n\ntext\n\nsee "Setup" above.\n'),
    ("match is case-insensitive", '## Setup\n\nsee "setup" above.\n'),
    ("match ignores trailing colon and spacing", '## Setup:\n\nsee "Setup" above.\n'),
    ("matches an h3", '### Deep\n\nsee "Deep" above.\n'),
    ("a later heading satisfies 'below'", 'see "Later" below.\n\n## Later\n'),
    ("backticked name with ## marker", '## Setup\n\nsee `## Setup` above.\n'),
    ("a heading with inline code compares by rendered text",
     '## The `foo` flag\n\nsee "The foo flag" above.\n'),
    ("a setext heading counts", 'Setup\n=====\n\nsee "Setup" above.\n'),
    ("undelimited name is not guessed at", "## Top\n\nsee the rules above and the notes below.\n"),
    ("'above' without 'see' is not a reference",
     '## Top\n\nthe "Missing" thing is described above.\n'),
    ("inside a block quote", '## Top\n\n> see "Missing" above.\n'),
    ("inside a fence", '## Top\n\n```\nsee "Missing" above\n```\n'),
    ("inside inline code", '## Top\n\n`see "Missing" above`\n'),
]


def _advisory_args(tmp_path, registry_root: Path, *extra: str) -> list:
    return ["--config", str(_absolute_registry_config(tmp_path, registry_root)),
            "--waivers", str(_empty_waivers(tmp_path)), *extra]


def _registry_with_root_doc(tmp_path, body: str, name: str = "AGENTS.md") -> Path:
    registry_root = tmp_path / "reg"
    registry_root.mkdir()
    (registry_root / name).write_text(body, encoding="utf-8")
    return registry_root


def _scan(body: str, allowed=frozenset()) -> list:
    british = check_skills.load_british_words(check_skills.DEFAULT_SPELLINGS)
    found = check_skills.scan_advisories("alpha", "AGENTS.md", body, british, allowed)
    return [(a.rule, a.line) for a in found]


@pytest.mark.parametrize("name, body, want", ADVISORY_SPELLING_WARN + ADVISORY_REF_WARN,
                         ids=[row[0] for row in ADVISORY_SPELLING_WARN + ADVISORY_REF_WARN])
def test_an_advisory_warns_but_only_strict_fails_the_exit_status(
        tmp_path, capsys, name, body, want):
    assert _scan(body) == want
    registry_root = _registry_with_root_doc(tmp_path, body)
    assert check_skills.main(_advisory_args(tmp_path, registry_root)) == 0
    assert "ADVISORIES (%d, warn-only)" % len(want) in capsys.readouterr().out
    assert check_skills.main(_advisory_args(tmp_path, registry_root, "--strict")) == 1


@pytest.mark.parametrize("name, body", ADVISORY_SPELLING_QUIET + ADVISORY_REF_QUIET,
                         ids=[row[0] for row in ADVISORY_SPELLING_QUIET + ADVISORY_REF_QUIET])
def test_text_the_advisories_exempt_stays_quiet_even_under_strict(tmp_path, name, body):
    assert _scan(body) == []
    registry_root = _registry_with_root_doc(tmp_path, body)
    assert check_skills.main(_advisory_args(tmp_path, registry_root, "--strict")) == 0


def test_the_allowlist_silences_a_word_case_insensitively_and_ignores_comments(tmp_path):
    allowed = tmp_path / "allow.txt"
    allowed.write_text("# vendor quote\nGREY  # a name\n\n", encoding="utf-8")
    words = check_skills.load_allowlist(allowed, required=True)
    assert words == {"grey"}
    assert _scan("A Grey area and colour.\n", words) == [("british-spelling", 1)]
    assert _scan("A Grey area and colour.\n") == [("british-spelling", 1)] * 2


def test_allowlisting_one_word_does_not_silence_another():
    assert _scan("colour behaviour\n", frozenset({"colour"})) == [("british-spelling", 1)]


def test_an_explicit_allowlist_replaces_the_default_and_a_missing_one_exits_two(
        tmp_path, capsys):
    registry_root = _registry_with_root_doc(tmp_path, "colour\n")
    allowed = tmp_path / "elsewhere.txt"
    allowed.write_text("colour\n", encoding="utf-8")
    assert check_skills.main(
        _advisory_args(tmp_path, registry_root, "--strict", "--allowlist", str(allowed))) == 0
    with pytest.raises(SystemExit) as raised:
        check_skills.main(_advisory_args(
            tmp_path, registry_root, "--allowlist", str(tmp_path / "nope.txt")))
    assert raised.value.code == 2
    assert "does not exist" in capsys.readouterr().err


def test_the_shipped_allowlist_and_word_list_both_load():
    check_skills.load_allowlist(check_skills.DEFAULT_ALLOWLIST, required=True)
    assert check_skills.load_british_words(check_skills.DEFAULT_SPELLINGS)


def test_the_fixed_list_holds_the_words_the_owner_named_and_no_generic_ise():
    words = check_skills.load_british_words(check_skills.DEFAULT_SPELLINGS)
    for word in ["behaviour", "colour", "honour", "favour", "organise", "organisation",
                 "recognise", "analyse", "catalogue", "centre", "licence", "artefact",
                 "authorise", "summarise", "normalised", "neighbouring"]:
        assert word in words, f"{word} should be listed"
    for word in ["exercise", "promise", "otherwise", "advise", "revise", "analyses",
                 "cancelled", "judgement", "license"]:
        assert word not in words, f"{word} must not be listed"


@pytest.mark.parametrize("missing", ["our_stems", "our_suffixes", "ise_stems",
                                     "ise_suffixes", "explicit"])
def test_a_word_list_file_missing_a_section_is_a_usage_error(tmp_path, missing):
    data = yaml.safe_load(check_skills.DEFAULT_SPELLINGS.read_text(encoding="utf-8"))
    del data[missing]
    broken = tmp_path / "words.yml"
    broken.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        check_skills.load_british_words(broken)
    assert missing in str(raised.value)


def test_a_heading_in_another_file_does_not_satisfy_a_reference(tmp_path):
    registry_root = tmp_path / "reg"
    registry_root.mkdir()
    (registry_root / "AGENTS.md").write_text('# A\n\nsee "Other" above.\n', encoding="utf-8")
    (registry_root / "CLAUDE.md").write_text("## Other\n", encoding="utf-8")
    report = check_skills.run(
        write_config(tmp_path / "config.yml", [registry_entry("alpha", str(registry_root))]),
        _empty_waivers(tmp_path), {}, repo_root=tmp_path)
    assert [(a.path, a.rule, a.line) for a in report.advisories] == [
        ("AGENTS.md", "dangling-reference", 3)]


def test_advisories_scan_skills_and_registry_root_agent_docs_and_nothing_else(tmp_path):
    bad = "colour\n"
    registry_root = tmp_path / "reg"
    # In scope: a SKILL.md (frontmatter and all) and the root AGENTS.md / CLAUDE.md.
    write_skill(registry_root, "skills/good-skill", body=bad)
    (registry_root / "AGENTS.md").write_text(bad, encoding="utf-8")
    (registry_root / "CLAUDE.md").write_text(bad, encoding="utf-8")
    # Out of scope: other Markdown, PURPOSE.md, a nested AGENTS.md, a skill's reference.
    (registry_root / "README.md").write_text(bad, encoding="utf-8")
    (registry_root / "docs").mkdir()
    (registry_root / "docs" / "notes.md").write_text(bad, encoding="utf-8")
    (registry_root / "skills" / "good-skill" / "PURPOSE.md").write_text(bad, encoding="utf-8")
    (registry_root / "skills" / "good-skill" / "references").mkdir()
    (registry_root / "skills" / "good-skill" / "references" / "AGENTS.md").write_text(
        bad, encoding="utf-8")
    report = check_skills.run(
        write_config(tmp_path / "config.yml", [registry_entry("alpha", str(registry_root))]),
        _empty_waivers(tmp_path), {}, repo_root=tmp_path)
    # SKILL.md: 4 frontmatter lines, so the body's line 1 is file line 5.
    assert [(a.path, a.line) for a in report.advisories] == [
        ("AGENTS.md", 1), ("CLAUDE.md", 1), ("skills/good-skill/SKILL.md", 5)]


def test_a_british_word_in_the_frontmatter_description_is_flagged_at_its_file_line(tmp_path):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/good-skill",
                frontmatter="---\nname: good-skill\ndescription: Tidy the colour.\n---\n")
    report = check_skills.run(
        write_config(tmp_path / "config.yml", [registry_entry("alpha", str(registry_root))]),
        _empty_waivers(tmp_path), {}, repo_root=tmp_path)
    assert [(a.rule, a.line) for a in report.advisories] == [("british-spelling", 3)]


def test_advisories_never_hide_or_mask_a_real_finding(tmp_path):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/on-disk", name="in-frontmatter", body="colour\n")
    report = check_skills.run(
        write_config(tmp_path / "config.yml", [registry_entry("alpha", str(registry_root))]),
        _empty_waivers(tmp_path), {}, repo_root=tmp_path)
    assert klasses(report) == [check_skills.K_NAME_DIR_MISMATCH]
    assert len(report.advisories) == 1
    assert report.exit_code == 1


def test_exit_status_by_default_and_strict(tmp_path):
    config = write_config(tmp_path / "config.yml", [registry_entry(
        "alpha", str(_registry_with_root_doc(tmp_path, "colour\n")))])
    waivers = _empty_waivers(tmp_path)
    assert check_skills.run(config, waivers, {}, repo_root=tmp_path).exit_code == 0
    assert check_skills.run(config, waivers, {}, repo_root=tmp_path, strict=True).exit_code == 1


def test_a_clean_tree_exits_zero_under_strict(tmp_path):
    registry_root = _registry_with_root_doc(tmp_path, "# Fine\n\nAll good.\n")
    write_skill(registry_root, "skills/good-skill", body="# Fine\n\nAll good.\n")
    assert check_skills.main(_advisory_args(tmp_path, registry_root, "--strict")) == 0


def test_annotation_escapes_percent_cr_lf_and_property_colon_and_comma():
    advisory = check_skills.Advisory(
        "alpha", "a,b:c.md", 3, "dangling-reference", 'see 50% above\r\nnext: line, here')
    line = check_skills.github_annotation(advisory)
    assert line == ("::warning file=a%2Cb%3Ac.md,line=3,title=dangling-reference::"
                    "see 50%25 above%0D%0Anext: line, here")
    assert "\r" not in line and "\n" not in line
    # An explicit file replaces the advisory's own path, escaped the same way.
    assert check_skills.github_annotation(advisory, "x:y,z.md").startswith(
        "::warning file=x%3Ay%2Cz.md,line=3,")


def test_annotations_print_only_under_github_actions(tmp_path, capsys, monkeypatch):
    registry_root = _registry_with_root_doc(tmp_path, "ok\n\nthe colour\n")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    check_skills.main(_advisory_args(tmp_path, registry_root))
    assert "::warning" not in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert check_skills.main(_advisory_args(tmp_path, registry_root)) == 0
    out = capsys.readouterr().out
    # The registry lives outside this checkout, so the file is `<registry>/<path>`.
    assert re.search(
        r"^::warning file=alpha/AGENTS\.md,line=3,title=british-spelling::British spelling "
        r'"colour"', out, re.MULTILINE), out


def test_json_mode_carries_advisories_as_data_and_never_prints_annotations(
        tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    registry_root = _registry_with_root_doc(tmp_path, "the colour\n")
    code = check_skills.main(_advisory_args(tmp_path, registry_root, "--json"))
    payload = json.loads(capsys.readouterr().out)   # raises if anything else was printed
    assert code == payload["exit_code"] == 0
    assert payload["strict"] is False
    assert [(a["rule"], a["path"], a["line"]) for a in payload["advisories"]] == [
        ("british-spelling", "AGENTS.md", 1)]
    strict_code = check_skills.main(_advisory_args(tmp_path, registry_root, "--json", "--strict"))
    capsys.readouterr()
    assert strict_code == 1


def test_ci_never_passes_strict_to_the_census():
    """D5: CI warns. A `--strict` in any workflow step that runs check_skills.py would
    turn the advisories into a gate, so the workflows are parsed, not grepped."""
    workflows = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    steps = []
    for path in sorted(workflows.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (doc.get("jobs") or {}).values():
            steps.extend(step for step in job.get("steps") or []
                         if "check_skills.py" in str(step.get("run", "")))
    assert steps, "no workflow step runs check_skills.py any more"
    for step in steps:
        assert "--strict" not in step["run"]
        assert "continue-on-error" not in step
