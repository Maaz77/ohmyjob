"""`ohmyjob resume master`: the merged draft of the # Resume section, the PDF texts and --apply."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resume_build.py"
sys.path.insert(0, str(ROOT / "scripts"))
from test_pj import make_pdf  # noqa: E402


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OHMYJOB_WORKSPACE": str(root), "PYTHONPATH": str(ROOT / "scripts")}
    return subprocess.run([sys.executable, str(SCRIPT), "master", *args], capture_output=True, text=True, env=env)


def test_merges_the_yamls_into_one_draft(workspace):
    proc = run(workspace)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == f"DRAFT {workspace / '.ohmyjob' / 'master-resume.md'}"
    assert lines[-1] == "SUMMARY 2 yaml, 0 pdf" and len(lines) == 2
    draft = (workspace / ".ohmyjob" / "master-resume.md").read_text(encoding="utf-8")
    assert draft.startswith("# Resume\n\n- Name: Alex Example\n- Email: alex.example@example.com\n")
    assert "## Work Experience" in draft and "## Skills" in draft and "## Education" in draft
    # the same degree in both files is one entry; the same bullet (also reworded, ratio >= 90) is kept once
    assert draft.count("### BSc Business Administration | ") == 1
    bullet = "Led 3 analysts in moving daily route planning from spreadsheets to a shared **forecasting model**."
    assert draft.count(bullet) == 1
    assert re.search(r"^### Senior Operations Analyst \| Harborline Freight Group \| Hamburg, Germany \| 2022 - Present$", draft, re.M)
    assert re.search(r"^- \*\*Analysis:\*\* Excel, SQL", draft, re.M)
    assert "# " not in "\n".join(l for l in draft.splitlines()[1:] if re.match(r"^# ", l))  # one level-1 heading only


def test_same_job_in_two_files_becomes_one_entry_with_the_longer_bullet(workspace):
    base = yaml.safe_load((workspace / "Default_Resume_Files" / "Operations-Analyst.yaml").read_text())
    job = {"role": "Analyst", "company": "Acme GmbH", "location": "Bonn", "dates": "2020 - 2021",
           "bullets": ["Cut costs by 10% with a new forecasting report for planners."]}
    other = {"role": "analyst", "company": "ACME GmbH", "location": "Bonn, Germany", "dates": "2020 – 2021",
             "bullets": ["Cut costs by 10% with a new forecasting report for the planners.", "Trained 4 colleagues."]}
    for name, entry in (("A-One", job), ("B-Two", other)):
        data = {**base, "experience": [entry]}
        (workspace / "Default_Resume_Files" / f"{name}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    for stale in ("Operations-Analyst", "Project-Coordinator"):
        (workspace / "Default_Resume_Files" / f"{stale}.yaml").unlink()
    assert run(workspace).returncode == 0
    draft = (workspace / ".ohmyjob" / "master-resume.md").read_text(encoding="utf-8")
    assert draft.count("### ") >= 1 and len(re.findall(r"^### .*Acme GmbH", draft, re.M | re.I)) == 1
    assert "for the planners." in draft and "for planners." not in draft
    assert "- Trained 4 colleagues." in draft
    assert "Bonn, Germany" in draft


def test_pdfs_without_a_yaml_become_text_files_and_templates_are_skipped(workspace):
    folder = workspace / "Default_Resume_Files"
    (folder / "Nurse-ICU.pdf").write_bytes(make_pdf("Registered nurse, intensive care, 8 years"))
    (folder / "Template.pdf").write_bytes(make_pdf("template"))
    (folder / "Marked.yaml").write_text("# OHMYJOB-TEMPLATE: x\nname: {first: Mock, last: Person}\n")
    (folder / "Operations-Analyst.pdf").write_bytes(make_pdf("has a yaml, so no text"))
    proc = run(workspace)
    assert proc.returncode == 0, proc.stdout
    lines = proc.stdout.splitlines()
    text = workspace / ".ohmyjob" / "master-resume-pdf" / "Nurse-ICU.txt"
    assert lines[1] == f"PDF {text}" and lines[-1] == "SUMMARY 2 yaml, 1 pdf" and len(lines) == 3
    assert "intensive care" in text.read_text(encoding="utf-8")
    assert "Mock" not in (workspace / ".ohmyjob" / "master-resume.md").read_text(encoding="utf-8")


def test_fatal_without_a_usable_file(workspace):
    for path in (workspace / "Default_Resume_Files").glob("*"):
        path.unlink()
    proc = run(workspace)
    assert proc.returncode == 1 and proc.stdout.startswith("FATAL: no usable resume file") and len(proc.stdout.splitlines()) == 1


def test_apply_replaces_only_the_resume_section_and_backs_up(workspace):
    profile = workspace / "Profile.md"
    before = profile.read_text(encoding="utf-8")
    assert run(workspace, "--apply").stdout.startswith("FATAL: no draft yet")
    assert run(workspace).returncode == 0
    draft = (workspace / ".ohmyjob" / "master-resume.md").read_text(encoding="utf-8")
    proc = run(workspace, "--apply")
    assert proc.returncode == 0, proc.stdout
    [line] = proc.stdout.splitlines()
    backup = Path(line.removeprefix("OK "))
    assert re.fullmatch(r"Profile-\d{8}T\d{6}Z\.md", backup.name) and backup.parent == workspace / ".ohmyjob" / "backup"
    assert backup.read_text(encoding="utf-8") == before
    after = profile.read_text(encoding="utf-8")
    assert after.split("# Resume")[0] == before.split("# Resume")[0]
    assert "# Follow Up Questions" + before.split("# Follow Up Questions")[1] == "# Follow Up Questions" + after.split("# Follow Up Questions")[1]
    assert draft.strip() in after and "Musterstrasse" in after
    assert after.count("# Resume") == 1


def test_apply_refuses_a_draft_with_another_level_one_heading(workspace):
    assert run(workspace).returncode == 0
    draft = workspace / ".ohmyjob" / "master-resume.md"
    draft.write_text(draft.read_text(encoding="utf-8") + "\n# Extra\n", encoding="utf-8")
    before = (workspace / "Profile.md").read_text(encoding="utf-8")
    proc = run(workspace, "--apply")
    assert proc.returncode == 1 and proc.stdout.startswith("FATAL:")
    assert (workspace / "Profile.md").read_text(encoding="utf-8") == before


def test_file_prefix_reads_the_name_of_the_applied_resume(workspace):
    import workspace as W

    assert run(workspace).returncode == 0 and run(workspace, "--apply").returncode == 0
    assert W.file_prefix(workspace) == "Alex"
