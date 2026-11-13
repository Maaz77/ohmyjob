#!/usr/bin/env python3
"""Job-prep commands for prep-master (/ohmyjob:prep-jobs): screen the "Found" jobs, build resumes for the "Matched" jobs.

Run as `ohmyjob prep <command>`.

  start                    queue the Found rows (screen) and the Matched rows (build); rows without a folder,
                           folders without a row and duplicate postings are settled here
  next                     record the finished jobs, then print workers for the free slots (screens first, then
                           builds); waits up to NEXT_SECONDS for a free slot; prints "done": true when no job is left
  assign-screen [--max N]  write the briefs of the next screen jobs, print the screen-worker prompts
  assign-build [--max N]   copy the matched yaml, write the briefs of the next build jobs, print the resume-worker prompts
  build <job>              (resume-worker only) check the job's yaml, then compile its PDF
  record                   record every assigned job: job.md, then the tracker (one save), then deletions
  end                      print the run summary, delete the run folder

Every command prints JSON or a few lines. An error prints a line that starts with FATAL.
Jobs are keyed by job_key.job_key: the LinkedIn job id, or for a job found on another site a 15-digit number made from its address. A folder is paired with its row by the Job URL line of its job.md, which holds the job's address whatever the site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import aa_lib as L  # noqa: E402  (run lock, state, markdown and JSON helpers, timings)
import workspace as W  # noqa: E402  (paths, agent names, config)
import yaml  # noqa: E402
from job_key import job_key  # noqa: E402
from tracker import Tracker, TrackerError  # noqa: E402
import work_auth as WA  # noqa: E402  (the work authorization blocker, run before a screen worker is started)

RUN_BASE = W.RUN_PREP
DEFAULT_RESUMES = W.DEFAULT_RESUMES

MASTER, SCREENER, BUILDER = W.PREP_MASTER, W.SCREEN_WORKER, W.RESUME_WORKER
WAVE = 10              # default --max of assign-screen / assign-build ('next' uses W.slots())
MAX_NAME = 50          # resume file name stem, max characters
MAX_BUILDS = 4         # first build + 3 overflow-ladder rebuilds
INTRO_SLACK = 0        # the pre-made resumes fill the page: one more intro line overflows it
GROUNDS = ("core-unsupported", "wrong-discipline", "dealbreaker")
BLANK_LOCATIONS = {"", "not specified", "none", "n a"}  # as norm() writes them ("N/A" -> "n a")


class JobError(Exception):
    """A job that cannot be recorded. The other jobs are recorded; this one is listed."""


def emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def fatal(message: str) -> None:
    print(f"FATAL: {message}")
    sys.exit(3)


# --------------------------------------------------------------------------- run state


def current_run_dir() -> Optional[Path]:
    marker = RUN_BASE / "CURRENT"
    if not marker.exists():
        return None
    run_dir = RUN_BASE / marker.read_text(encoding="utf-8").strip()
    return run_dir if (run_dir / "state.json").exists() else None


def need_run() -> Path:
    run_dir = current_run_dir()
    if run_dir is None:
        fatal("no active run; run 'start' first")
    return run_dir


def job_path(run_dir: Path, key: str) -> Path:
    return run_dir / "jobs" / key


def result_path(run_dir: Path, key: str, phase: str) -> Path:
    return job_path(run_dir, key) / f"{phase}.json"


def sha(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# --------------------------------------------------------------------------- folders and job.md


def folders_by_key() -> dict[Optional[str], list[Path]]:
    """Every folder in Applications/, keyed by the Job URL line of its job.md (None: no URL)."""
    out: dict[Optional[str], list[Path]] = {}
    if not W.APPLICATIONS.is_dir():
        return out
    for path in sorted(W.APPLICATIONS.iterdir()):
        if not path.is_dir():
            continue
        md = path / "job.md"
        url = L.job_overview(md.read_text(encoding="utf-8")).get("Job URL") if md.exists() else None
        out.setdefault(job_key(url) if url else None, []).append(path)
    return out


def has_resume_files(folder: Path) -> bool:
    return any(p.suffix in (".pdf", ".yaml") for p in folder.iterdir() if p.is_file())


def resume_name(folder_name: str) -> str:
    """<Prefix>_<Company_JobTitle>: the folder name without its leading job id, capped at MAX_NAME so any upload form accepts it."""
    return (W.file_prefix() + "_" + re.sub(r"^\d+_", "", folder_name))[:MAX_NAME].rstrip("_-. ")


def norm(text: Any) -> str:
    return " ".join(re.sub(r"[^\w+#]+", " ", str(text or "").casefold()).split())


def delete_folder(folder: Path, allow_yaml: bool) -> None:
    """The one irreversible act. Only a folder directly inside Applications/, never one with a built PDF
    (and, unless allow_yaml, never one with any resume file)."""
    folder = folder.resolve()
    if folder.parent != W.APPLICATIONS.resolve() or not folder.is_dir():
        raise JobError(f"refused to delete {folder}: not a folder directly inside Applications/")
    files = [p for p in folder.iterdir() if p.is_file()]
    if any(p.suffix == ".pdf" for p in files) or (not allow_yaml and any(p.suffix == ".yaml" for p in files)):
        raise JobError(f"refused to delete {folder.name}: it holds a built resume")
    shutil.rmtree(folder)


def check_deletable(folder: Path, allow_yaml: bool) -> None:
    folder = folder.resolve()
    if folder.parent != W.APPLICATIONS.resolve() or not folder.is_dir():
        raise JobError(f"{folder.name}: not a folder directly inside Applications/")
    files = [p for p in folder.iterdir() if p.is_file()]
    if any(p.suffix == ".pdf" for p in files) or (not allow_yaml and any(p.suffix == ".yaml" for p in files)):
        raise JobError(f"{folder.name}: holds a built resume (.pdf/.yaml), so it is not deleted")


def fill_section(text: str, title_re: str, bullets: list[str]) -> str:
    """Write bullets under an existing, empty heading. Raises JobError if the heading is missing or not empty."""
    lines = text.splitlines()
    pattern = re.compile(title_re, re.I)
    for _lvl, title, start, end in L.md_sections(text):
        if pattern.fullmatch(title):
            body = [line for line in lines[start + 1:end] if line.strip()]
            if body == [f"- {b}" for b in bullets]:
                return text  # already written by an earlier, interrupted record
            if body:
                raise JobError(f"job.md: '{title}' is not empty")
            new = lines[:start + 1] + [""] + [f"- {b}" for b in bullets] + [""] + lines[end:]
            return "\n".join(new).rstrip("\n") + "\n"
    raise JobError(f"job.md has no '{title_re}' heading")


def fill_overview(text: str, field: str, value: str) -> str:
    """Write the value after the blank '- <field>: ' line of the Overview, on that same line."""
    lines = text.splitlines()
    for lvl, title, start, end in L.md_sections(text):
        if lvl == 2 and re.fullmatch(r"(1\.\s*)?Overview", title, re.I):
            for i in range(start + 1, end):
                match = re.match(rf"^(\s*-\s*\**{re.escape(field)}\s*(?::\**|\**:))\s*(.*?)\s*$", lines[i])
                if match and match.group(2) in ("", value):
                    lines[i] = f"{match.group(1)} {value}"
                    return "\n".join(lines).rstrip("\n") + "\n"
            raise JobError(f"job.md: no blank '- {field}:' line in the Overview")
    raise JobError("job.md has no Overview section")


def append_h1(text: str, title: str, block: str) -> str:
    """Append block under the level-1 heading `title`, created at the end of the document if absent."""
    lines = text.rstrip("\n").splitlines()
    for lvl, head, _start, end in L.md_sections(text.rstrip("\n")):
        if lvl == 1 and head.strip().lower() == title.lower():
            return "\n".join(lines[:end] + ["", block.rstrip("\n")] + lines[end:]).rstrip("\n") + "\n"
    return "\n".join(lines) + f"\n\n# {title}\n\n" + block.rstrip("\n") + "\n"


def clean_items(items: Any) -> list[str]:
    out = []
    for item in items or []:
        text = " ".join(str(item).split())
        text = re.sub(r"^[-*•]\s*", "", text)
        if text:
            out.append(text)
    return out


# --------------------------------------------------------------------------- resume yaml checks


def rendered(text: Any) -> str:
    """About what the PDF shows: no **bold** marks, whitespace squeezed."""
    return " ".join(str(text or "").replace("**", "").split())


CATALOG_FILE_CAP = 6000  # characters of one ready-made PDF's text in the catalog


def is_template(path: Path) -> bool:
    """Template.* and any resume file that still holds the template marker are never used."""
    stem = path.stem.casefold()
    return stem == "template" or stem.startswith("template-") or W.has_template_marker(path)


def usable_resumes() -> list[dict]:
    """The resumes the screener may choose from: a <name>.yaml with a <name>.pdf (tailorable) and a <name>.pdf with no
    yaml (ready-made, used as is). Never Template.* nor a file that holds the template marker."""
    out = []
    SKIPPED.clear()
    folder = DEFAULT_RESUMES
    if not folder.is_dir():
        return out
    for pdf in sorted(folder.glob("*.pdf")):
        yml = pdf.with_suffix(".yaml")
        if yml.is_file():
            if not is_template(yml) and not is_template(pdf):
                out.append({"file": pdf.name, "kind": "tailorable", "source": yml})
        elif not is_template(pdf):
            copy = mock_copy(pdf)
            if copy:
                SKIPPED.append(f"{pdf.name}: skipped, it is {copy}; replace it with your own resume")
            else:
                out.append({"file": pdf.name, "kind": "ready-made", "source": pdf})
    return out


SKIPPED: list[str] = []  # PDFs usable_resumes() left out as copies of the mock template (start lists them as issues)


def mock_name() -> Optional[str]:
    """The mock persona's name in the plugin's templates/Template.yaml ("First Last"), read from the file."""
    try:
        name = (yaml.safe_load((W.TEMPLATES_DIR / "Template.yaml").read_text(encoding="utf-8")) or {})["name"]
        return f"{name['first']} {name['last']}"
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return None


def mock_copy(pdf: Path) -> Optional[str]:
    """Why a PDF is the mock resume (compressed PDF text hides the marker from a byte search), else None: its bytes
    equal Default_Resume_Files/Template.pdf, or its text holds the mock name."""
    template = DEFAULT_RESUMES / "Template.pdf"
    try:
        if template.is_file() and pdf.read_bytes() == template.read_bytes():
            return "a copy of Template.pdf"
    except OSError:
        pass
    name = mock_name()
    if name and " ".join(name.casefold().split()) in " ".join(pdf_text(pdf).casefold().split()):
        return f"a resume of the mock persona {name}"
    return None


def pdf_text(path: Path) -> str:
    """The text of a ready-made PDF (pypdf), cut to CATALOG_FILE_CAP characters."""
    from pypdf import PdfReader

    try:
        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001  (a damaged PDF is still a usable file, only without text)
        return f"(the text of this PDF could not be read: {exc})"
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:CATALOG_FILE_CAP] or "(this PDF holds no extractable text)"


def write_catalog() -> list[dict]:
    """Write the text of every usable resume to W.CATALOG_DIR (catalog.md for the screener, index.json for the
    scripts) and return the entries. The screener reads the text, never the PDFs."""
    entries = usable_resumes()
    if W.CATALOG_DIR.exists():
        shutil.rmtree(W.CATALOG_DIR)
    blocks = []
    for entry in entries:
        if entry["kind"] == "tailorable":
            data = yaml.safe_load(entry["source"].read_text(encoding="utf-8")) or {}
            body = (f"- kind: tailorable (a worker adapts a copy of it to the job)\n- title: {data.get('title')}\n"
                    + (f"- introduction: {rendered(data.get('introduction'))}\n" if "introduction" in data else "")
                    + (f"- experience: {experience_line(data)}\n" if experience_line(data) else "")
                    + (f"- skills: {' | '.join(skills_line(row) for row in data.get('skills') or [])}" if "skills" in data
                       else f"- document: {data.get('document') or 'resume'}"))
        else:
            body = f"- kind: ready-made (used as is, never edited)\n- text of the PDF:\n\n{pdf_text(entry['source'])}"
        blocks.append(f"### {entry['file']}\n{body}")
    L.write_text(W.CATALOG_DIR / "catalog.md", "# Resumes to choose from\n\n" + "\n\n".join(blocks) + "\n")
    L.write_json(W.CATALOG_DIR / "index.json", {e["file"]: e["kind"] for e in entries})
    return entries


def catalog() -> dict[str, str]:
    """{file name: "tailorable" | "ready-made"} as written by the last `start`."""
    return L.read_json(W.CATALOG_DIR / "index.json", {})


def catalog_path() -> Path:
    return (W.CATALOG_DIR / "catalog.md").resolve()


def resume_build() -> Any:
    """The resume_build module (WS-A): BULLET_BUDGET, SKILLS_BUDGET, rendered_len(), build(). Imported only where needed."""
    try:
        import resume_build as rb
    except ImportError as exc:
        fatal(f"resume_build.py cannot be imported ({exc})")
    return rb


def strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


def roles_of(entry: Any) -> list:
    """The roles of an experience entry: a grouped entry (employer + roles:) holds several, a plain entry is one."""
    if isinstance(entry, dict) and isinstance(entry.get("roles"), list):
        return entry["roles"]
    return [entry]


def check_yaml(new: Any, src: dict, overflowed: bool, profile: str) -> list[str]:
    """The mechanical rules of the resume (skeleton, counts, title, line budget). The judgment rules stay with the worker."""
    if not isinstance(new, dict):
        return ["The yaml does not parse to a mapping."]
    errors = []
    # resume_build escapes every backslash, so a LaTeX command would print literally in the PDF
    if any("\\" in text for text in strings(new)):
        errors.append("No backslash anywhere in the yaml: it would print as a backslash. **bold** is the only markup.")
        return errors
    if set(new) != set(src):
        errors.append(f"The top-level keys changed (now {sorted(new)}, must be {sorted(src)}).")
        return errors
    for key in src:
        if key not in ("title", "introduction", "experience", "projects", "skills") and new[key] != src[key]:
            errors.append(f"'{key}' must stay unchanged.")
    title = str(new.get("title") or "").strip()
    if not title:
        errors.append("The title is empty.")
    seniority = r"\b(senior|junior|sr|jr)\b"
    added = {w.casefold() for w in re.findall(seniority, title, re.I)} - {w.casefold() for w in re.findall(seniority, str(src.get("title") or ""), re.I)}
    if added:
        errors.append(f"The title '{title}' must not add a seniority word ({', '.join(sorted(added))}) that the matched resume's title does not carry.")
    if "introduction" in src:  # a document type without a summary (the CV) has none
        intro = rendered(new.get("introduction"))
        if title and not intro.casefold().startswith(title.casefold()):
            errors.append(f"The introduction must open with the resume title '{title}' (it opens with '{intro[:40]}').")
        if len(intro) > len(rendered(src.get("introduction"))) + INTRO_SLACK:
            errors.append(f"The introduction is {len(intro)} characters; keep it no longer than the matched yaml's "
                          f"({len(rendered(src.get('introduction')))}): the page has no spare line.")
    src_bullets = {rendered(b) for e in src.get("experience") or [] for r in roles_of(e) for b in r.get("bullets") or []}
    src_bullets |= {rendered(b) for p in src.get("projects") or [] for b in p.get("bullets") or []}

    def bullets_ok(where: str, bullets: Any, src_count: Optional[int]) -> None:
        # min(2, source) to 4 bullets, or up to as many as the pre-made resume has there (some roles hold 5 or 6);
        # a project swapped in from Profile.md has no source, so it needs 2
        low, high = (min(2, src_count) if src_count is not None else 2), max(4, src_count or 0)
        if not isinstance(bullets, list) or not low <= len(bullets) <= high:
            errors.append(f"{where}: needs {low} to {high} bullets.")
            return
        for b in bullets:
            text = rendered(b)
            if text in src_bullets:
                continue
            rb = resume_build()
            length = rb.rendered_len(str(b))
            if length > rb.BULLET_BUDGET:
                errors.append(f"{where}: a changed bullet renders {length} characters (one line is {rb.BULLET_BUDGET}): '{text[:60]}...'")

    exp, src_exp = new.get("experience") or [], src.get("experience") or []
    if not isinstance(exp, list) or len(exp) != len(src_exp):
        errors.append(f"Experience must keep its {len(src_exp)} entries.")
    else:
        for i, (entry, src_entry) in enumerate(zip(exp, src_exp)):
            grouped = isinstance(src_entry.get("roles"), list)
            # a grouped entry (one employer, several roles) keeps its employer line and its roles in order
            for field in (("employer", "location", "dates") if grouped else ("role", "company", "location", "dates")):
                if not isinstance(entry, dict) or entry.get(field) != src_entry.get(field):
                    errors.append(f"Experience entry {i + 1}: '{field}' must stay '{src_entry.get(field)}' (entries keep their order).")
            if not isinstance(entry, dict):
                continue
            src_roles, roles = roles_of(src_entry), roles_of(entry)
            if grouped and (not isinstance(entry.get("roles"), list) or len(roles) != len(src_roles)):
                errors.append(f"Experience '{src_entry.get('employer')}': must keep its {len(src_roles)} roles under 'roles'.")
                continue
            for role, src_role in zip(roles, src_roles):
                if not isinstance(role, dict):
                    errors.append(f"Experience '{src_entry.get('employer') or src_entry.get('company')}': a role is not a mapping.")
                    continue
                if grouped:
                    for field in ("role", "dates"):
                        if role.get(field) != src_role.get(field):
                            errors.append(f"Experience '{src_entry.get('employer')}': role '{field}' must stay "
                                          f"'{src_role.get(field)}' (roles keep their order).")
                name = f"{src_role.get('role')} @ {src_entry.get('employer') or src_entry.get('company')}"
                bullets_ok(f"Experience '{name}'", role.get("bullets"), len(src_role.get("bullets") or []))
    projects, src_projects = new.get("projects") or [], src.get("projects") or []
    allowed = {len(src_projects)} | ({len(src_projects) - 1} if overflowed and src_projects else set())
    if not isinstance(projects, list) or len(projects) not in allowed:
        errors.append(f"Projects must keep {len(src_projects)} projects (one fewer only after a build overflowed).")
    else:
        src_repo = {p.get("title"): p.get("repo") for p in src_projects}
        src_count = {p.get("title"): len(p.get("bullets") or []) for p in src_projects}
        for p in projects:
            if not isinstance(p, dict):
                errors.append("A project is not a mapping.")
                continue
            if p.get("title") in src_repo:
                if p.get("repo") != src_repo[p.get("title")]:
                    errors.append(f"Project '{p.get('title')}': keep its repo link unchanged.")
            elif p.get("repo") and str(p.get("repo")) not in profile:
                errors.append(f"Project '{p.get('title')}': its repo link is not in Profile.md; leave it out.")
            bullets_ok(f"Project '{p.get('title')}'", p.get("bullets"), src_count.get(p.get("title")))
    if "skills" not in src:  # the resume layout prints no skills
        return errors
    skills = new.get("skills")
    if not isinstance(skills, list) or not all(isinstance(s, dict) and isinstance(s.get("label"), str) and "items" in s for s in skills):
        errors.append("Skills: each row needs 'label' and 'items'.")
        return errors
    # one rendered row per skills row, as for bullets: a longer row wraps and can push the page over
    src_rows = {skills_line(row) for row in src.get("skills") or []}
    if len(skills) > len(src.get("skills") or []):
        errors.append(f"Skills: keep at most {len(src.get('skills') or [])} rows (regroup instead of adding a row).")
    for row in skills:
        text = skills_line(row)
        if text in src_rows:
            continue
        rb = resume_build()
        length = rb.rendered_len(items_text(row))  # the budget covers the items column only, not the label
        if length > rb.SKILLS_BUDGET:
            errors.append(f"Skills: a changed row has {length} characters of items (one row holds {rb.SKILLS_BUDGET}): "
                          f"'{text[:60]}...'. Move the less relevant items out, or regroup them.")
    return errors


def items_text(row: dict) -> str:
    items = row.get("items")
    return ", ".join(str(i) for i in items) if isinstance(items, list) else str(items or "")


def skills_line(row: Any) -> str:
    """About what the PDF shows for one skills row: "Label: items"."""
    if not isinstance(row, dict):
        return ""
    return f"{rendered(row.get('label'))}: {rendered(items_text(row))}"


def experience_line(data: Any) -> str:
    """About what the PDF shows for Experience: "Role, Employer (dates)" for every role, newest first. The screener
    judges a tailorable resume on its roles too, not only its title and skills (a ready-made PDF shows its whole text)."""
    out = []
    for entry in (data or {}).get("experience") or []:
        if not isinstance(entry, dict):
            continue
        employer = rendered(entry.get("employer") or entry.get("company"))
        for role in entry.get("roles") or [entry]:
            if not isinstance(role, dict) or not role.get("role"):
                continue
            dates = rendered(role.get("dates") or entry.get("dates"))
            out.append(f"{rendered(role.get('role'))}, {employer}" + (f" ({dates})" if dates else ""))
    return " | ".join(out)


# --------------------------------------------------------------------------- worker reports


SCREEN_KEYS = {"job": (str,), "junk": (bool,), "agency": (bool,), "score": (int, type(None)), "dealbreaker": (str, type(None)),
               "matched_resume": (str, type(None)), "why": (list,), "gaps": (list,), "note": (str,)}
BUILD_KEYS = {"job": (str,), "status": (str,), "ground": (str, type(None)), "reason": (str,), "title": (str,),
              "edits_md": (str,), "ladder": (str,)}


def typed(obj: dict, schema: dict) -> list[str]:
    errors = []
    for key, types in schema.items():
        if key not in obj:
            errors.append(f"Key '{key}' is missing.")
        elif not isinstance(obj[key], types) or (int in types and isinstance(obj[key], bool)):
            errors.append(f"Key '{key}' has the wrong type.")
    return errors


def screen_errors(obj: Optional[dict], key: str) -> list[str]:
    if obj is None:
        return ["The report holds no JSON object."]
    errors = typed(obj, SCREEN_KEYS)
    if errors:
        return errors
    if obj["job"] != key:
        return [f"'job' must be '{key}'."]
    score = obj["score"]
    if obj["junk"]:
        return [] if obj["note"].strip() else ["Give the junk reason in 'note'."]
    if obj["agency"] and (score is None or score < 8):
        if not obj["note"].strip():
            errors.append("Give the agency reason in 'note'.")
        return errors
    if score is None or not 0 <= score <= 10:
        return ["'score' must be a whole number from 0 to 10."]
    if obj["dealbreaker"] and score >= 6:
        errors.append("A dealbreaker caps the score below 6.")
    if score >= 6:
        names = set(catalog())
        if obj["matched_resume"] not in names:
            errors.append(f"'matched_resume' must be one of: {', '.join(sorted(names))}.")
        if not clean_items(obj["why"]) or not clean_items(obj["gaps"]):
            errors.append("A match needs bullets in both 'why' and 'gaps'.")
    elif not obj["note"].strip():
        errors.append("Give the reason for the low score in 'note'.")
    return errors


def build_errors(obj: Optional[dict], key: str, job: dict) -> list[str]:
    if obj is None:
        return ["The report holds no JSON object."]
    errors = typed(obj, BUILD_KEYS)
    if errors:
        return errors
    if obj["job"] != key:
        return [f"'job' must be '{key}'."]
    status = obj["status"]
    yaml_path = Path(job["yaml"])
    if status == "built":
        if not job.get("built_hash") or job["built_hash"] != sha(yaml_path) or not Path(job["pdf"]).exists():
            errors.append("The current yaml has no successful build. Run the build command again, or report 'failed'.")
        if not obj["edits_md"].strip() or not obj["ladder"].strip():
            errors.append("A built resume needs 'edits_md' and 'ladder'.")
        if re.search(r"^\s{0,3}#{1,2}\s", obj["edits_md"], re.M):
            errors.append("'edits_md' must not hold '#' or '##' headings: use bullets and bold labels.")
    elif status == "rejected":
        if obj["ground"] not in GROUNDS:
            errors.append(f"'ground' must be one of: {', '.join(GROUNDS)}.")
        if not obj["reason"].strip():
            errors.append("Give the deciding reason in 'reason'.")
        if job.get("builds") or sha(yaml_path) != job.get("src_hash"):
            errors.append("A rejection is decided before any edit. You edited or built this resume: "
                          "finish it and report 'built', or report 'failed'.")
    elif status == "failed":
        if not obj["reason"].strip():
            errors.append("Give what went wrong in 'reason'.")
    else:
        errors.append("'status' must be built, failed or rejected.")
    return errors


def process_report(agent_id: Optional[str], message: str) -> tuple[str, str, dict]:
    """Check a worker report and save it. Returns ("retry", reason, {}), ("saved", "", info) or ("skip", "", {})."""
    run_dir = current_run_dir()
    if run_dir is None:
        return "skip", "", {}
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        key = (state.get("agents") or {}).get(agent_id or "")
        if key is None:
            return "skip", "", {}
        job = state["jobs"][key]
        if job.get("agent_id") != agent_id or job["done"]:
            return "skip", "", {}
        phase = job["phase"]
        obj = L.extract_json_object(message)
        errors = screen_errors(obj, key) if phase == "screen" else build_errors(obj, key, job)
        out = result_path(run_dir, key, phase)
        if errors:
            if job.get("report_blocks", 0) < L.MAX_CAPTURE_BLOCKS:
                job["report_blocks"] = job.get("report_blocks", 0) + 1
                L.save_state(run_dir, state)
                return "retry", "Fix your report: " + " ".join(errors), {}
            L.write_json(out, {"invalid": True, "errors": errors, "raw": message[-2000:]})
            return "saved", "", {"job": key, "status": "invalid"}
        L.write_json(out, obj)
        return "saved", "", {"job": key, "status": obj.get("status") or ("junk" if obj.get("junk") else "agency" if obj.get("agency") else obj.get("score"))}


# --------------------------------------------------------------------------- briefs


def fit_preferences() -> str:
    """The `# Fit Preferences` section of Profile.md, verbatim (without its heading)."""
    return (L.md_section_body(W.PROFILE.read_text(encoding="utf-8"), "Fit Preferences", 1) or "").strip()


def write_screen_brief(run_dir: Path, key: str, job: dict) -> Path:
    md = (W.APPLICATIONS / job["folder"] / "job.md").read_text(encoding="utf-8")
    overview = L.md_section_body(md, r"(1\.\s*)?Overview", 2) or ""
    overview = "\n".join(line for line in overview.splitlines() if not re.search(r"Match Score|Matched Resume", line))
    resumes = [f"- {name} ({kind})" for name, kind in sorted(catalog().items())]
    text = f"""# Screening brief: {job['company']} | {job['title']}

- job: {key}

## CANDIDATE FIT PREFERENCES (judge the posting against these FIRST)
{fit_preferences() or '(Profile.md has no "Fit Preferences" section)'}

## Profile.md (full text)
{W.PROFILE.read_text(encoding='utf-8').strip()}

## Resumes (choose matched_resume from these file names; the names say the field)
{chr(10).join(resumes)}

Read the catalog text of these resumes (title, introduction, experience and skills of a tailorable one, the whole text of a ready-made one) once, before you score: {catalog_path()}

## Job: Overview
{overview.strip()}

## Job: Description
{(L.job_description(md) or '(no Job Description section in job.md)').strip()}
"""
    path = job_path(run_dir, key) / "brief.md"
    L.write_text(path, text)
    return path.resolve()


def pdf_pages(path: Path) -> int:
    """The page count of a PDF (pypdf)."""
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)  # a damaged PDF is reported by the caller, not on stderr
    return len(PdfReader(str(path)).pages)


def pages_text(pages: int) -> str:
    return f"exactly {pages} page{'s' if pages != 1 else ''}"


def write_build_brief(run_dir: Path, key: str, job: dict) -> Path:
    md = (W.APPLICATIONS / job["folder"] / "job.md").read_text(encoding="utf-8")
    reasoning = L.md_section_body(md, r"(3\.\s*)?Match Reasoning and Discussions", 2) or "(none)"
    text = f"""# Resume brief: {job['company']} | {job['title']}

- job: {key}
- Your yaml (Read it, then edit only this file): {job['yaml']}
- Matched pre-made resume it was copied from: {Path(job['source']).name}
- Build command: ohmyjob prep build {key}
- Builds allowed: {MAX_BUILDS}
- Pages: the tailored resume must have {pages_text(job['pages'])} (the page count of {Path(job['source']).with_suffix('.pdf').name}). Not fewer, not more.

## Job: Overview
{(L.md_section_body(md, r"(1\.\s*)?Overview", 2) or '').strip()}

## Job: Description
{(L.job_description(md) or '(no Job Description section in job.md)').strip()}

## Screener's notes (a fast first read; your own read of the JD decides)
{reasoning.strip()}

## Profile.md (full text: the source pool)
{W.PROFILE.read_text(encoding='utf-8').strip()}
"""
    path = job_path(run_dir, key) / "brief.md"
    L.write_text(path, text)
    return path.resolve()


# --------------------------------------------------------------------------- tracker writes


def row_for(tracker: Tracker, key: str) -> dict:
    rows = tracker.rows_for_key(key)
    if len(rows) != 1:
        raise JobError(f"expected 1 tracker row for job {key}, found {len(rows)}")
    return rows[0]


def write_row(tracker: Tracker, key: str, status: str, note: Optional[str], score: Any = "keep") -> None:
    row = row_for(tracker, key)
    if score != "keep":
        tracker.set_cell(row["_index"], "Match Score", score if score is not None else "")
    tracker.set_status(row["_index"], status, note)


def discard(state: dict, tracker: Tracker, plans: list[tuple]) -> None:
    """Rows first (one save), folders second, never the reverse. plans: (key, status, note, allow_yaml[, score])."""
    ready = []
    for key, status, note, allow_yaml, *score in plans:
        job = state["jobs"][key]
        try:
            check_deletable(W.APPLICATIONS / job["folder"], allow_yaml)
            write_row(tracker, key, status, note, *score)
            ready.append((key, status, note, allow_yaml))
        except JobError as exc:
            job["error"] = str(exc)
            state["issues"].append(f"{job['folder']}: not discarded ({exc}); its row is unchanged")
    if not ready:
        return
    try:
        tracker.save()
    except Exception as exc:  # noqa: BLE001  (a stale or locked tracker is a stop, never a reason to overwrite)
        for key, *_ in ready:
            state["jobs"][key].update({"error": str(exc), "done": True})
            state["issues"].append(f"{state['jobs'][key]['folder']}: tracker save failed ({exc}); row unchanged, folder kept")
        raise TrackerError(str(exc)) from exc
    for key, status, note, allow_yaml in ready:
        job = state["jobs"][key]
        state["counts"]["rows_discarded"] += 1
        try:
            delete_folder(W.APPLICATIONS / job["folder"], allow_yaml)
            state["counts"]["folders_deleted"] += 1
            job["outcome"] = status
            job["note"] = note
        except JobError as exc:
            job["error"] = str(exc)
            state["issues"].append(f"{job['folder']}: row set to {status} but the folder stays ({exc})")
        job["done"] = True


# --------------------------------------------------------------------------- commands


def cmd_start(args: argparse.Namespace) -> None:
    # A run that stopped early leaves its folder. Its jobs are still at Found or Matched, so they are queued again.
    if W.has_template_marker(W.PROFILE):  # the mock guard: Profile.md still holds the mock data
        fatal(f"{W.PROFILE.name} still has the template marker ({W.TEMPLATE_MARKER}): replace the mock data with yours, then delete that line")
    if not W.PROFILE.is_file():
        fatal("Profile.md not found. Run /ohmyjob:setup")
    if not usable_resumes():
        fatal("no usable resume in Default_Resume_Files/: run /ohmyjob:build-resumes for a <Field>.yaml without its PDF, "
              "delete the template marker line from a copy of Template.yaml, or add a ready-made <Field>.pdf")
    if RUN_BASE.exists():
        shutil.rmtree(RUN_BASE)
    try:
        tracker = Tracker(W.TRACKER)
    except TrackerError as exc:
        fatal(str(exc))
    write_catalog()
    skipped = list(SKIPPED)
    today = L.today()
    rows = tracker.rows()
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(row["_key"], []).append(row)
    folders = folders_by_key()
    state: dict = {"run_id": f"{today}_{uuid.uuid4().hex[:6]}", "started": L.now_iso(), "status": "running",
                   "jobs": {}, "screen": [], "build": [], "agents": {}, "issues": skipped, "start_deleted": [],
                   "counts": {"rows_discarded": 0, "folders_deleted": 0}}
    changed = False

    def add_job(key: str, folder: Path, phase: str, row: dict) -> None:
        ov = L.job_overview((folder / "job.md").read_text(encoding="utf-8"))
        state["jobs"][key] = {
            "folder": folder.name, "phase": phase, "company": row.get("Company") or ov.get("Company", ""),
            "title": row.get("Job Title") or ov.get("Job Title", ""),
            "location": str(ov.get("Location") or row.get("Location") or ""), "assigned": False, "done": False,
        }
        state[phase].append(key)

    for row in rows:
        status = (row["Status"] or "").strip()
        if status not in ("Found", "Matched"):
            continue
        key, label = row["_key"], f"{row['Company']} | {row['Job Title']}"
        if len(by_key[key]) > 1:
            state["issues"].append(f"{label}: more than one tracker row has job id {key}; left as is")
            continue
        found = folders.get(key, [])
        if not found:
            tracker.set_status(row["_index"], "Deleted", f"Deleted {today}: no folder in Applications/")
            state["start_deleted"].append(f"{label}: {status} row had no folder, set to Deleted")
            changed = True
            continue
        if len(found) > 1:
            state["issues"].append(f"{label}: {len(found)} folders carry job id {key}; left as is")
            continue
        if status == "Found" and has_resume_files(found[0]):
            state["issues"].append(f"{found[0].name}: Found row, but the folder holds a built resume; left at Found")
            continue
        add_job(key, found[0], "screen" if status == "Found" else "build", row)

    for key, paths in folders.items():
        if key is None:
            state["issues"] += [f"{p.name}: job.md has no Job URL line; left as is" for p in paths]
            continue
        if key in by_key:
            continue
        if len(paths) > 1 or has_resume_files(paths[0]):
            state["issues"].append(f"{paths[0].name}: no tracker row, and the folder is not a fresh scrape; left as is")
            continue
        ov = L.job_overview((paths[0] / "job.md").read_text(encoding="utf-8"))
        tracker.add_row({"Job URL": ov.get("Job URL"), "Company": ov.get("Company"), "Job Title": ov.get("Job Title"),
                         "Date Found": ov.get("Date Found"), "Location": ov.get("Location"), "Source": ov.get("Source"),
                         "Status": "Found"})
        state["issues"].append(f"{paths[0].name}: had no tracker row; row created at Found")
        changed = True
        add_job(key, paths[0], "screen", {})

    if changed:
        try:
            tracker.save()
        except Exception as exc:  # noqa: BLE001
            fatal(f"could not save the tracker: {exc}")

    # Duplicate postings: same company and title. A Resume Built row wins; else a stated location, else the lower id.
    built = {}
    for row in tracker.rows():
        if (row["Status"] or "").strip() == "Resume Built":
            built.setdefault((norm(row["Company"]), norm(row["Job Title"])), row["_key"])
    groups: dict[tuple, list[str]] = {}
    for key, job in state["jobs"].items():
        groups.setdefault((norm(job["company"]), norm(job["title"])), []).append(key)
    plans = []
    for ident, keys in groups.items():
        if ident in built:
            kept, drop = built[ident], keys
        elif len(keys) > 1:
            keys = sorted(keys, key=lambda k: (norm(state["jobs"][k]["location"]) in BLANK_LOCATIONS, int(k) if k.isdigit() else 0))
            kept, drop = keys[0], keys[1:]
        else:
            continue
        for key in drop:
            plans.append((key, "Deleted", f"Deleted {today}: duplicate of job {kept}", True))
    if plans:
        try:
            discard(state, tracker, plans)
        except TrackerError as exc:
            fatal(f"could not save the tracker: {exc}")
        for key, _s, note, _a in plans:
            job = state["jobs"][key]
            if job.get("done"):
                state["start_deleted"].append(f"{job['company']} | {job['title']}: {note}")
    for phase in ("screen", "build"):
        state[phase] = [k for k in state[phase] if not state["jobs"][k].get("done")]

    run_dir = RUN_BASE / state["run_id"]
    with L.locked(run_dir):
        L.save_state(run_dir, state)
    L.write_text(RUN_BASE / "CURRENT", state["run_id"])
    emit({"run_id": state["run_id"], "screen": len(state["screen"]), "build": len(state["build"]),
          "deleted": state["start_deleted"], "issues": state["issues"]})


def open_jobs(state: dict, phase: str) -> tuple[list[str], list[str]]:
    """(not yet assigned, assigned but not recorded) jobs of a phase."""
    keys = [k for k in state[phase] if state["jobs"][k]["phase"] == phase]  # a screened-in job moved on to "build"
    todo = [k for k in keys if not state["jobs"][k]["assigned"]]
    pending = [k for k in keys if state["jobs"][k]["assigned"] and not state["jobs"][k]["done"]]
    return todo, pending


def worker(run_dir: Path, key: str, job: dict) -> dict:
    screen = job["phase"] == "screen"
    return {"subagent_type": SCREENER if screen else BUILDER,
            "description": f"{'Screen' if screen else 'Resume'}: {job['company']}"[:60],
            "prompt": f"job={key} brief={job_path(run_dir, key) / 'brief.md'}"}


def work_authorization(run_dir: Path, key: str, job: dict) -> bool:
    """The pre-pass of work_auth.py. A posting that states a requirement the profile says the candidate cannot meet is
    answered here, in the shape of a screen report, and no worker is started (True). A requirement the profile cannot
    decide is kept as a gap line for record_screen (False)."""
    md = (W.APPLICATIONS / job["folder"] / "job.md").read_text(encoding="utf-8")
    verdict = WA.check(L.job_overview(md).get("Location") or "", L.job_description(md) or "", W.PROFILE.read_text(encoding="utf-8"))
    if verdict is None:
        return False
    if not verdict.blocked:
        job["work_auth_gap"] = verdict.text
        return False
    L.write_json(result_path(run_dir, key, "screen"), {"job": key, "junk": False, "agency": False, "score": None, "dealbreaker": None,
                                                       "matched_resume": None, "why": [], "gaps": [], "note": verdict.text})
    job["work_auth"] = "blocked"
    return True


def assign_screen(run_dir: Path, limit: int) -> tuple[list, dict]:
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        todo, _pending = open_jobs(state, "screen")
        workers = []
        for key in todo:
            if len(workers) == limit:
                break
            job = state["jobs"][key]
            job.update({"assigned": True, "assigned_at": time.time()})
            if not (W.APPLICATIONS / job["folder"] / "job.md").is_file():
                job["done"] = True
                state["issues"].append(f"{job['folder']}: folder or job.md gone before screening; row unchanged")
                continue
            if work_authorization(run_dir, key, job):
                continue
            write_screen_brief(run_dir, key, job)
            workers.append(worker(run_dir, key, job))
        L.save_state(run_dir, state)
    return workers, state


def cmd_assign_screen(args: argparse.Namespace) -> None:
    workers, state = assign_screen(need_run(), args.max)
    emit({"subagent_type": SCREENER, "workers": workers, "left": len(open_jobs(state, "screen")[0])})


def assign_build(run_dir: Path, limit: int) -> tuple[list, list, dict]:
    """A job builds as soon as it is Matched, while other jobs are still screening."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        todo, _pending = open_jobs(state, "build")
        workers, failed = [], []
        for key in todo:
            if len(workers) + len(failed) == limit:
                break
            job = state["jobs"][key]
            job.update({"assigned": True, "assigned_at": time.time()})
            folder = W.APPLICATIONS / job["folder"]
            if not (folder / "job.md").is_file():
                job["done"] = True
                state["issues"].append(f"{job['folder']}: folder or job.md gone before the build; row unchanged")
                continue
            matched = L.job_overview((folder / "job.md").read_text(encoding="utf-8")).get("Matched Resume") or ""
            source = DEFAULT_RESUMES / (Path(matched).stem + ".yaml") if matched.strip() else None
            if source is None or not source.is_file():
                # a blocker, not a rejection: the job goes to Resume Failed with the reason, no worker needed
                L.write_json(result_path(run_dir, key, "build"), {
                    "job": key, "status": "failed", "ground": None, "title": "", "edits_md": "", "ladder": "",
                    "reason": f"blocker: the matched pre-made resume '{matched or '(blank)'}' has no yaml in Default_Resume_Files/"})
                failed.append(job["folder"])
                continue
            try:
                pages = pdf_pages(source.with_suffix(".pdf"))
            except Exception as exc:  # noqa: BLE001  (no PDF next to the yaml, or a damaged one)
                L.write_json(result_path(run_dir, key, "build"), {
                    "job": key, "status": "failed", "ground": None, "title": "", "edits_md": "", "ladder": "",
                    "reason": f"blocker: the page count of {source.with_suffix('.pdf').name} cannot be read ({exc})"})
                failed.append(job["folder"])
                continue
            target = folder / f"{resume_name(job['folder'])}.yaml"
            shutil.copyfile(source, target)
            job.update({"yaml": str(target.resolve()), "pdf": str(target.with_suffix(".pdf").resolve()),
                        "source": str(source.resolve()), "pages": pages, "src_hash": sha(target), "builds": 0, "built_hash": None,
                        "overflowed": False})
            write_build_brief(run_dir, key, job)
            workers.append(worker(run_dir, key, job))
        L.save_state(run_dir, state)
    return workers, failed, state


def cmd_assign_build(args: argparse.Namespace) -> None:
    workers, failed, state = assign_build(need_run(), args.max)
    emit({"subagent_type": BUILDER, "workers": workers, "failed_without_worker": failed,
          "left": len(open_jobs(state, "build")[0])})


def cmd_next(args: argparse.Namespace) -> None:
    """The worker queue: record what finished, fill the free slots, else wait (at most L.NEXT_SECONDS)."""
    run_dir = need_run()
    deadline = time.time() + L.NEXT_SECONDS
    while True:
        record(run_dir, ready_only=True)
        with L.locked(run_dir):
            state = L.load_state(run_dir)
            # printed but never started (the master dropped it; hook_guard marks a started one): print it again
            now = time.time()
            lost = [k for k, j in state["jobs"].items() if j["assigned"] and not j["done"] and not j.get("started")
                    and now - j.get("assigned_at", 0) > L.LOST_SECONDS]
            for key in lost:
                state["jobs"][key]["assigned_at"] = now
            if lost:
                L.save_state(run_dir, state)
        running = sum(1 for j in state["jobs"].values() if j["assigned"] and not j["done"])
        free = W.slots() - running
        workers = [worker(run_dir, k, state["jobs"][k]) for k in lost]
        if free > 0:
            new = assign_screen(run_dir, free)[0]
            if len(new) < free:
                new += assign_build(run_dir, free - len(new))[0]
            workers += new
        if workers:
            return emit({"workers": workers})
        with L.locked(run_dir):
            state = L.load_state(run_dir)
        if not any(any(open_jobs(state, phase)) for phase in ("screen", "build")):
            return emit({"done": True})
        if time.time() > deadline:
            return emit({"workers": [], "running": running})
        time.sleep(3)


def cmd_build(args: argparse.Namespace) -> None:
    """Check the yaml, then compile. The run lock is not held during the compile, so other workers go on."""
    run_dir = need_run()
    key = args.job
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        job = state["jobs"].get(key)
        if not job or job["phase"] != "build" or not job.get("yaml") or job["done"]:
            fatal(f"job {key} is not an open build job of this run")
        if job["builds"] >= MAX_BUILDS:
            print(f"LIMIT: all {MAX_BUILDS} builds are used. Stop and report status 'failed' with what went wrong.")
            return
        yaml_path = Path(job["yaml"])
        try:
            new = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"CHECK-FAIL (not counted as a build): the yaml does not parse: {str(exc)[:300]}")
            return
        src = yaml.safe_load(Path(job["source"]).read_text(encoding="utf-8"))
        errors = check_yaml(new, src, job["overflowed"], W.PROFILE.read_text(encoding="utf-8"))
        if errors:
            print("CHECK-FAIL (not counted as a build). Fix these, then run the build command again:\n- " + "\n- ".join(errors))
            return
        job["builds"] += 1
        builds, digest = job["builds"], sha(yaml_path)
        L.save_state(run_dir, state)
    pdf = Path(job["pdf"])
    try:
        status, pages, detail = compile_pdf(yaml_path, pdf, job["pages"])
    except subprocess.TimeoutExpired:
        status, pages, detail = "FAIL", None, "the compile timed out"
    except Exception as exc:  # noqa: BLE001  (a build crash is a failed build, not a crashed worker)
        status, pages, detail = "FAIL", None, f"the build crashed: {exc}"
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        job = state["jobs"][key]
        job["command"] = f"ohmyjob resume build '{yaml_path.relative_to(W.ROOT) if yaml_path.is_relative_to(W.ROOT) else yaml_path}'"
        if status == "OK" and pdf.exists():
            if pages is not None and pages < job["pages"]:
                pdf.unlink(missing_ok=True)
                line = (f"CHECK-FAIL: the PDF has {pages} page{'s' if pages != 1 else ''}, the source resume has {job['pages']}. "
                        f"Keep the original length ({pages_text(job['pages'])}): restore content you cut or shortened. "
                        f"Builds used {builds}/{MAX_BUILDS}.")
            else:
                job["built_hash"] = digest
                line = f"OK: {pages_text(job['pages'])}, {pdf.name}. Builds used {builds}/{MAX_BUILDS}."
        elif status == "OVERFLOW":
            job["overflowed"] = True
            line = (f"OVERFLOW: {pages if pages is not None else 'more than the limit'} pages, the source resume has {job['pages']}. "
                    f"Builds used {builds}/{MAX_BUILDS}. Take the smallest overflow-ladder step, then build again.")
        else:
            line = f"FAIL: the compile failed. Builds used {builds}/{MAX_BUILDS}. Last output:\n" + "\n".join(str(detail).strip().splitlines()[-15:])
        L.save_state(run_dir, state)
    print(line)


def compile_pdf(yaml_path: Path, pdf: Path, pages: int) -> tuple[str, Optional[int], str]:
    """("OK" | "OVERFLOW" | "FAIL", pages, detail). PJ_COMPILE_CMD (tests) is a fake: `<cmd> <pdf> <yaml>`."""
    fake = os.environ.get("PJ_COMPILE_CMD", "").split()
    if not fake:
        return resume_build().build(yaml_path, pdf, max_pages=pages)  # OVERFLOW above the source's page count
    proc = subprocess.run([*fake, str(pdf), str(yaml_path)], capture_output=True, text=True, timeout=600)
    output = (proc.stdout + proc.stderr).strip()
    found = re.search(r"is (\d+) pages", output)
    counted = int(found.group(1)) if found else None
    if counted is not None and counted > pages:
        pdf.unlink(missing_ok=True)  # like the real build: OVERFLOW leaves no PDF
        return "OVERFLOW", counted, output
    if proc.returncode == 0 and pdf.exists():
        return "OK", counted if counted is not None else 1, output
    if "must fit on" in output:
        return "OVERFLOW", counted, output
    return "FAIL", None, output


def record_screen(state: dict, tracker: Tracker, key: str, result: Optional[dict], discards: list, writes: list) -> None:
    job = state["jobs"][key]
    if result is None or result.get("invalid"):
        why = "no report" if result is None else "invalid report: " + "; ".join(result.get("errors") or [])
        state["issues"].append(f"{job['folder']}: screen {why}; left at Found")
        job["done"] = True
        return
    score = result["score"]
    if result["junk"] or score is None or score < (8 if result["agency"] else 6):
        note = ("Junk posting: " if result["junk"] else "") + " ".join(result["note"].split())
        discards.append((key, "Skipped-LowFit", note, False, score))
        return
    md_path = W.APPLICATIONS / job["folder"] / "job.md"
    original = md_path.read_text(encoding="utf-8")
    text = fill_overview(original, "Match Score", str(score))
    text = fill_overview(text, "Matched Resume", result["matched_resume"])
    text = fill_section(text, r"Why Matched\??", clean_items(result["why"]))
    text = fill_section(text, r"Gaps\??", clean_items(result["gaps"]) + clean_items(job.get("work_auth_gap") and [job["work_auth_gap"]]))
    row = row_for(tracker, key)
    ready_made = catalog().get(result["matched_resume"]) == "ready-made"
    if ready_made:
        # no resume-worker: the PDF is copied as it is and the job goes straight to Resume Built
        source = DEFAULT_RESUMES / result["matched_resume"]
        target = W.APPLICATIONS / job["folder"] / f"{resume_name(job['folder'])}.pdf"
        if not source.is_file():
            raise JobError(f"the ready-made resume {result['matched_resume']} is gone from Default_Resume_Files/")
        if target.exists():
            raise JobError(f"{target.name} already exists in the job folder")
        text = append_h1(text, "Resume Preparation",
                         f"Ready-made resume used as it is: `{result['matched_resume']}` (copied to `{target.name}`). "
                         "No resume-worker ran.\n")
        shutil.copyfile(source, target)
        writes.append((target, None))
    md_path.write_text(text, encoding="utf-8")
    writes.append((md_path, original))
    tracker.set_cell(row["_index"], "Match Score", score)
    tracker.set_status(row["_index"], "Resume Built" if ready_made else "Matched", None)
    job.update({"outcome": "Resume Built" if ready_made else "Matched", "score": score, "ready_made": ready_made})


def record_build(state: dict, tracker: Tracker, key: str, result: Optional[dict], discards: list, writes: list) -> None:
    job = state["jobs"][key]
    today = L.today()
    if result is None:
        state["issues"].append(f"{job['folder']}: no report from the resume-worker; left at Matched for the next run")
        job["done"] = True
        return
    if result.get("invalid"):
        result = {"status": "failed", "reason": "invalid report: " + "; ".join(result.get("errors") or [])}
    status = result["status"]
    if status == "rejected":
        discards.append((key, "Deleted", f"Deleted {today}: {' '.join(result['reason'].split())}", True, "keep"))
        job["ground"] = result.get("ground")
        return
    md_path = W.APPLICATIONS / job["folder"] / "job.md"
    original = md_path.read_text(encoding="utf-8")
    if status == "built":
        block = (f"## Resume Edits\n\n{result['edits_md'].strip()}\n\n"
                 f"- **Overflow ladder:** {' '.join(result['ladder'].split())}\n"
                 f"- **Builds:** {job.get('builds')} of {MAX_BUILDS}\n"
                 f"- **Compile command:** `{job.get('command')}`\n")
        row_status, note = "Resume Built", None
    else:
        reason = " ".join(result["reason"].split())
        block = f"**Run {today}: Resume Failed.** {reason}\n"
        row_status, note = "Resume Failed", f"Resume Failed {today}: {reason}"[:300]
    row = row_for(tracker, key)
    md_path.write_text(append_h1(original, "Resume Preparation", block), encoding="utf-8")
    writes.append((md_path, original))
    tracker.set_status(row["_index"], row_status, note)
    job.update({"outcome": row_status, "note": note or ""})


def cmd_record(args: argparse.Namespace) -> None:
    print(record(need_run()))


def record(run_dir: Path, ready_only: bool = False) -> str:
    """Every assigned, unrecorded job (ready_only: only those with a result, or stale): job.md first,
    the tracker (one save) second, deletions last."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        todo = [k for k, j in state["jobs"].items() if j["assigned"] and not j["done"]]
        if ready_only:
            todo = [k for k in todo if result_path(run_dir, k, state["jobs"][k]["phase"]).exists()
                    or time.time() - (state["jobs"][k].get("started_at") or state["jobs"][k].get("assigned_at", 0)) > L.STALE_SECONDS]
        if not todo:
            return "OK nothing to record"
        try:
            tracker = Tracker(W.TRACKER)
        except TrackerError as exc:
            fatal(str(exc))
        discards: list = []
        writes: list = []
        recorded = []
        for key in todo:
            job = state["jobs"][key]
            result = L.read_json(result_path(run_dir, key, job["phase"]), None)
            try:
                (record_screen if job["phase"] == "screen" else record_build)(state, tracker, key, result, discards, writes)
                recorded.append(key)
            except Exception as exc:  # noqa: BLE001  (JobError, or a folder removed by hand)
                job["error"] = str(exc)
                job["done"] = True
                state["issues"].append(f"{job['folder']}: not recorded ({exc}); row unchanged")
        try:
            if writes:
                tracker.save()
        except Exception as exc:  # noqa: BLE001
            for md_path, original in writes:
                if original is None:
                    md_path.unlink(missing_ok=True)  # a copied ready-made PDF
                else:
                    md_path.write_text(original, encoding="utf-8")
            for key in recorded:
                state["jobs"][key].pop("outcome", None)
            L.save_state(run_dir, state)
            fatal(f"could not save the tracker ({exc}); job.md files restored, nothing deleted")
        for key in recorded:
            job = state["jobs"][key]
            if job.get("outcome") in ("Matched",):
                # screened in: the job moves to the build queue of this run
                job.update({"phase": "build", "assigned": False, "done": False, "agent_id": None, "report_blocks": 0,
                            "started": False})
                job.pop("started_at", None)
                state["build"].append(key)
            elif job.get("outcome"):
                job["done"] = True
        if discards:
            try:
                discard(state, tracker, discards)
            except TrackerError as exc:  # stale or locked workbook: stop, the folders stay, the rows are unchanged
                L.save_state(run_dir, state)
                fatal(f"could not save the tracker ({exc}); nothing deleted")
            for key, *_ in discards:
                state["jobs"][key]["done"] = True
        L.save_state(run_dir, state)
    counts: dict[str, int] = {}
    for key in todo:
        outcome = state["jobs"][key].get("outcome") or "not recorded"
        counts[outcome] = counts.get(outcome, 0) + 1
    return "OK recorded: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))


def summary(state: dict) -> str:
    jobs = state["jobs"]
    by = lambda outcome: [j for j in jobs.values() if j.get("outcome") == outcome]  # noqa: E731
    screened = [j for j in jobs.values() if "score" in j or j.get("outcome") == "Skipped-LowFit"]
    lines = [f"Run {state['run_id']}",
             f"Screened: {len(screened)} | Matched: {len([j for j in jobs.values() if 'score' in j])} | "
             f"Skipped-LowFit: {len(by('Skipped-LowFit'))}",
             f"Resumes built: {len(by('Resume Built'))} ({len([j for j in by('Resume Built') if j.get('ready_made')])} ready-made) | failed: {len(by('Resume Failed'))} | rejected: "
             f"{len([j for j in by('Deleted') if j.get('ground')])}"]
    blocked = len([j for j in by("Skipped-LowFit") if j.get("work_auth")])
    unchecked = len([j for j in jobs.values() if j.get("work_auth_gap") and "score" in j])
    if blocked or unchecked:
        lines.append(f"Work authorization: {blocked} blocked, {unchecked} matched but not checked (see Gaps in their job.md)")
    for j in by("Resume Failed"):
        lines.append(f"- FAILED {j['folder']}: {j.get('note')}")
    for j in by("Deleted"):
        if j.get("ground"):
            lines.append(f"- REJECTED {j['company']} | {j['title']} ({j['ground']}): {j.get('note')}")
    lines += [f"- START {x}" for x in state.get("start_deleted") or []]
    lines += [f"- ISSUE {x}" for x in state.get("issues") or []]
    c = state["counts"]
    check = "OK" if c["folders_deleted"] <= c["rows_discarded"] else "MISMATCH"
    lines.append(f"Deletion check {check}: folders deleted {c['folders_deleted']}, rows set to Skipped-LowFit/Deleted {c['rows_discarded']}")
    missing = [j["folder"] for j in jobs.values() if j.get("outcome") in ("Matched", "Resume Built", "Resume Failed")
               and not (W.APPLICATIONS / j["folder"]).is_dir()]
    left = [j["folder"] for j in jobs.values() if j.get("outcome") in ("Skipped-LowFit", "Deleted")
            and (W.APPLICATIONS / j["folder"]).is_dir()]
    lines.append(f"Folder check {'OK' if not missing and not left else 'MISMATCH'}"
                 + (f": missing {missing}" if missing else "") + (f": still present {left}" if left else ""))
    return "\n".join(lines)


def cmd_end(args: argparse.Namespace) -> None:
    run_dir = need_run()
    with L.locked(run_dir):
        state = L.load_state(run_dir)
    if any(j["assigned"] and not j["done"] for j in state["jobs"].values()):
        cmd_record(args)
    with L.locked(run_dir):
        state = L.load_state(run_dir)
    master = L.read_json(RUN_BASE / "master.json", {})
    print(summary(state))
    table = L.usage_table(master.get("transcript"), master.get("agent_id"))
    print(table)
    L.save_report(table)
    try:  # one line when enough skips wait for /ohmyjob:retro; never an error, never a block
        import retro

        hint = retro.hint()
        if hint:
            print(hint)
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(RUN_BASE)


# --------------------------------------------------------------------------- hooks (entry: pj_hooks.py)


ALLOW = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def allow() -> None:
    """A validated call of an ohmyjob agent: a background agent cannot answer a permission prompt."""
    print(json.dumps(ALLOW))


def deny(reason: str) -> None:
    sys.stderr.write(reason + "\n")
    sys.exit(2)


def resolved(path: Any) -> Optional[Path]:
    try:
        return Path(str(path or "")).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def time_up(agent: str) -> str:
    minutes = L.WORKER_SECONDS // 60
    base = (f"Time is up: your {minutes} minutes are over. Make no more tool calls (the hook denies them all). ")
    if agent == SCREENER:
        return base + "Send your report now from what you have read."
    return (base + "Send your report now: 'built' only if your last build printed OK and you changed the yaml no more "
            f"since; else 'failed' with a 'reason' that starts with 'Timed out after {minutes} minutes:' and says what "
            "is left to do. The candidate finishes it by hand.")


def hook_guard(inp: dict) -> None:
    """PreToolUse (Read, Edit, Write, Bash, Agent, Chrome) of the prep agents: each one stays inside its own job.
    The agent is the parsed top-level agent_type, never text from tool_input."""
    tool, ti = str(inp.get("tool_name") or ""), inp.get("tool_input") or {}
    agent, agent_id = inp.get("agent_type"), str(inp.get("agent_id") or "")
    if not isinstance(ti, dict):
        ti = {}
    if agent == MASTER:
        if tool == "Agent" and ti.get("subagent_type") in W.MASTER_WORKERS[MASTER]:
            mark_started(str(ti.get("prompt") or ""))
            return allow()
        if tool == "Bash" and W.PREP_MASTER_CMD_RE.match(str(ti.get("command") or "").strip()):
            # where the run's transcripts are, for the token table of 'end' ('start' deletes it; the next command writes it again)
            L.write_json(RUN_BASE / "master.json", {"agent_id": agent_id, "transcript": inp.get("transcript_path")})
            return allow()
        deny("The master runs only its 'ohmyjob prep' commands and starts only screen-worker and resume-worker agents.")
    run_dir = current_run_dir()
    if run_dir is None:
        deny("No active /ohmyjob:prep-jobs run.")
    raw_path = str(ti.get("file_path") or "")
    if raw_path and not os.path.isabs(os.path.expanduser(raw_path)):
        deny("Use an absolute path.")  # a relative one would resolve against this hook's folder, not the agent's
    target = resolved(raw_path)
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        key = state["agents"].get(agent_id)
        if key is None:
            phase = "screen" if agent == SCREENER else "build"
            for k, job in state["jobs"].items():
                if (tool == "Read" and job["phase"] == phase and job["assigned"] and not job["done"]
                        and job.get("agent_id") is None and target == (job_path(run_dir, k) / "brief.md").resolve()):
                    job["agent_id"] = agent_id
                    job["started_at"] = time.time()  # the worker's clock starts at its first guarded call
                    state["agents"][agent_id] = k
                    L.save_state(run_dir, state)
                    return allow()
            deny("Read your brief first: the path in your task text.")
        job = state["jobs"][key]
        if "started_at" not in job:
            job["started_at"] = time.time()
            L.save_state(run_dir, state)
        if time.time() - job["started_at"] > L.WORKER_SECONDS:
            deny(time_up(agent))
    brief = (job_path(run_dir, key) / "brief.md").resolve()
    own = [Path(job[f]).resolve() for f in ("yaml", "pdf") if agent == BUILDER and job.get(f)]
    if tool == "Read":
        if target in [brief, *own] or (agent == SCREENER and target == catalog_path()):
            return allow()
        deny("You may read only your brief" + (", your yaml and your PDF." if agent == BUILDER else " and the resume catalog it names."))
    if agent == BUILDER and tool == "Edit":
        if own and target == own[0]:
            return allow()
        deny(f"You may edit only your yaml: {own[0] if own else '(none)'}")
    if agent == BUILDER and tool == "Bash":
        match = W.RESUME_BUILD_CMD_RE.match(str(ti.get("command") or "").strip())
        if match and match.group(1) == key:
            return allow()
        deny(f"The only command you may run is: ohmyjob prep build {key}")
    deny(f"{tool} is not allowed for {agent}.")


def mark_started(prompt: str) -> None:
    """The master's Agent call: its job's worker really starts ('next' prints a dropped worker again)."""
    match, run_dir = re.match(r"job=(\S+) ", prompt), current_run_dir()
    if match is None or run_dir is None:
        return
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        job = state["jobs"].get(match.group(1))
        if job is not None:
            job.update({"started": True, "started_at": time.time()})
            L.save_state(run_dir, state)


def hook_handback(inp: dict) -> None:
    """PreToolUse on SubagentHandback: check and save the report; the master gets one short line."""
    ti = inp.get("tool_input") or {}
    action, reason, info = process_report(inp.get("agent_id"), str(ti.get("message") or ""))
    if action == "retry":
        deny(reason + " Then call SubagentHandback again, with only the corrected JSON object as the message.")
    if action == "saved":
        short = json.dumps({"job": info["job"], "status": info["status"], "result": "saved by the hook"})
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                                  "updatedInput": {**ti, "message": short}}}))


def hook_capture(inp: dict) -> None:
    """SubagentStop of a worker: the report is its final text when Claude Code does not use hand-backs."""
    message = inp.get("last_assistant_message")
    message = message if isinstance(message, str) else ""
    run_dir = current_run_dir()
    if run_dir is None or not message.strip():
        return
    state = L.load_state(run_dir)
    key = state["agents"].get(str(inp.get("agent_id") or ""))
    if key is None or result_path(run_dir, key, state["jobs"][key]["phase"]).exists():
        return  # the hand-back hook saved the report already
    action, reason, _info = process_report(inp.get("agent_id"), message)
    if action == "retry":
        print(json.dumps({"decision": "block", "reason": reason + " Reply again with only the corrected JSON object."}))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="ohmyjob prep")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    sub.add_parser("next")
    for name in ("assign-screen", "assign-build"):
        sub.add_parser(name).add_argument("--max", type=int, default=WAVE)
    sub.add_parser("build").add_argument("job")
    sub.add_parser("record")
    sub.add_parser("end")
    args = parser.parse_args(argv)
    try:
        {"start": cmd_start, "next": cmd_next, "assign-screen": cmd_assign_screen,
         "assign-build": cmd_assign_build, "build": cmd_build, "record": cmd_record, "end": cmd_end}[args.cmd](args)
    except W.WorkspaceError as exc:
        fatal(str(exc))


if __name__ == "__main__":
    main()
