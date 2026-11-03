#!/usr/bin/env python3
"""`ohmyjob resume master`: merge the user's resume files into one Markdown draft of the `# Resume` section of Profile.md.

No model runs here. The script does all the exact work:
  * every usable yaml of Default_Resume_Files (never Template*, never a file with the template marker) is merged:
    the header lines, and the same job / degree / award ... found in several files becomes one entry; bullets, details
    and skill items are united and near-duplicates (rapidfuzz ratio >= 90) keep the longer text;
  * every usable PDF without a yaml is turned into plain text for the model to read (pypdf);
  * the draft is written to .ohmyjob/master-resume.md, the PDF texts to .ohmyjob/master-resume-pdf/<name>.txt.
Output: `DRAFT <path>`, one `PDF <path>` per text, `SUMMARY <n> yaml, <m> pdf`; or one `FATAL: ...` line.

`ohmyjob resume master --apply` replaces ONLY the `# Resume` section of Profile.md with the draft (after saving the
old Profile.md as .ohmyjob/backup/Profile-<UTC timestamp>.md) and prints `OK <backup path>`.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import yaml
from rapidfuzz import fuzz

import workspace as W

DRAFT_NAME = "master-resume.md"
PDF_DIR_NAME = "master-resume-pdf"
SIMILAR = 90  # rapidfuzz ratio from which two lines count as the same
PDF_TEXT_CAP = 60000  # characters of one PDF's text

HEADINGS = {
    "summary": "Summary", "experience": "Work Experience", "education": "Education", "skills": "Skills",
    "projects": "Projects", "honors": "Honors & Awards", "certificates": "Certificates",
    "extracurricular": "Extracurricular Activity", "presentation": "Presentation", "writing": "Writing",
    "committees": "Program Committees",
}


class MasterError(Exception):
    """A problem the user must fix; printed as one FATAL line."""


def draft_path() -> Path:
    return W.STATE_DIR / DRAFT_NAME


def pdf_dir() -> Path:
    return W.STATE_DIR / PDF_DIR_NAME


# --------------------------------------------------------------------------- small helpers


def clean(value) -> str:
    """One line of text from any yaml scalar."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())


def norm(value) -> str:
    """The key form of a text: ASCII, lower case, letters and digits only."""
    text = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def norm_dates(value) -> str:
    return re.sub(r"\s+", "", norm(value))


def union(*groups) -> list[str]:
    """The texts of all groups in first-seen order; a near-duplicate (ratio >= SIMILAR) keeps the longer text."""
    out: list[str] = []
    for group in groups:
        for item in group:
            text = clean(item)
            if not text:
                continue
            for i, old in enumerate(out):
                if norm(old) == norm(text) or fuzz.ratio(old.casefold(), text.casefold()) >= SIMILAR:
                    if len(text) > len(old):
                        out[i] = text
                    break
            else:
                out.append(text)
    return out


def listify(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def items_of(value) -> list[str]:
    return [clean(v) for v in listify(value) if clean(v)]


# --------------------------------------------------------------------------- merging the yamls


class Pool:
    """Everything the yamls say, merged. Entries are dicts keyed by their normalized identity."""

    def __init__(self) -> None:
        self.header: dict[str, str] = {}
        self.intro: list[str] = []
        self.sections: dict[str, dict] = {key: {} for key in
                                          ("experience", "education", "skills", "projects", "honors", "certificates",
                                           "extracurricular", "presentation", "writing", "committees")}

    def entry(self, section: str, key: tuple, fields: dict, lists: dict[str, list[str]]) -> None:
        known = self.sections[section]
        if key not in known:
            known[key] = {**{k: clean(v) for k, v in fields.items()}, **{k: list(v) for k, v in lists.items()}}
            return
        item = known[key]
        for k, v in fields.items():
            if len(clean(v)) > len(item.get(k, "")):
                item[k] = clean(v)
        for k, v in lists.items():
            item[k] = union(item.get(k, []), v)

    def add(self, data: dict) -> None:
        name = data.get("name")
        if isinstance(name, dict):
            full = " ".join(p for p in (clean(name.get("first")), clean(name.get("last"))) if p)
        else:
            full = clean(name)
        links = []
        for label, base in (("homepage", ""), ("github", "https://github.com/"), ("linkedin", "https://www.linkedin.com/in/")):
            value = clean(data.get(label))
            if value:
                links.append(value if label == "homepage" or value.startswith("http") else base + value)
        for key, value in (("Name", full), ("Email", clean(data.get("email"))), ("Phone", clean(data.get("phone"))),
                           ("Address", clean(data.get("address")))):
            self.header.setdefault(key, value) if value else None
        self.header["Links"] = ", ".join(union(self.header.get("Links", "").split(", "), links))
        self.intro = union(self.intro, [clean(data.get("introduction"))])
        for item in listify(data.get("experience")):
            if not isinstance(item, dict):
                continue
            if "roles" in item:
                for sub in listify(item["roles"]):
                    if isinstance(sub, dict):
                        self.job(clean(sub.get("role")), clean(item.get("employer")), clean(item.get("location")),
                                 clean(sub.get("dates")) or clean(item.get("dates")), sub.get("bullets"))
            else:
                self.job(clean(item.get("role")), clean(item.get("company")), clean(item.get("location")),
                         clean(item.get("dates")), item.get("bullets"))
        for item in listify(data.get("education")):
            if isinstance(item, dict):
                self.entry("education", (norm(item.get("degree")), norm(item.get("school")), norm_dates(item.get("dates"))),
                           {k: item.get(k) for k in ("degree", "school", "location", "dates")},
                           {"bullets": items_of(item.get("details"))})
        for row in listify(data.get("skills")):
            if isinstance(row, dict) and (clean(row.get("label")) or clean(row.get("items"))):
                parts = [p for p in re.split(r"\s*,\s*", clean(row.get("items"))) if p]
                self.entry("skills", (norm(row.get("label")),), {"label": row.get("label")}, {"items": parts})
        for item in listify(data.get("projects")):
            if isinstance(item, dict) and clean(item.get("title")):
                self.entry("projects", (norm(item.get("title")),), {"title": item.get("title"), "repo": item.get("repo")},
                           {"bullets": items_of(item.get("bullets"))})
        for section in ("honors", "certificates", "committees"):
            for item in listify(data.get(section)):
                if isinstance(item, dict) and clean(item.get("award")):
                    self.entry(section, (norm(item.get("award")), norm(item.get("event")), norm_dates(item.get("date"))),
                               {k: item.get(k) for k in ("award", "event", "location", "date")}, {})
        for section in ("extracurricular", "presentation", "writing"):
            for item in listify(data.get(section)):
                if isinstance(item, dict) and (clean(item.get("role")) or clean(item.get("title"))):
                    self.entry(section, (norm(item.get("role")), norm(item.get("title")), norm_dates(item.get("date"))),
                               {k: item.get(k) for k in ("role", "title", "location", "date")},
                               {"bullets": items_of(item.get("items"))})

    def job(self, role: str, company: str, location: str, dates: str, bullets) -> None:
        if role or company:
            self.entry("experience", (norm(company), norm(role), norm_dates(dates)),
                       {"role": role, "company": company, "location": location, "dates": dates},
                       {"bullets": items_of(bullets)})


# --------------------------------------------------------------------------- the Markdown draft


def line(*parts: str) -> str:
    parts = [clean(p) for p in parts]
    while parts and not parts[-1]:
        parts.pop()
    return " | ".join(parts)


def render(pool: Pool) -> str:
    out = ["# Resume", ""]
    for key in ("Name", "Email", "Phone", "Address", "Links"):
        if pool.header.get(key):
            out.append(f"- {key}: {pool.header[key]}")
    out.append("")

    def section(key: str, body: list[str]) -> None:
        if body:
            out.extend([f"## {HEADINGS[key]}", "", *body, ""])

    section("summary", [p for text in pool.intro for p in (text, "")][:-1])
    blocks = lambda sec, head: [  # noqa: E731
        x for e in pool.sections[sec].values()
        for x in (f"### {line(*[e.get(f, '') for f in head])}", *[f"- {b}" for b in e["bullets"]], "")][:-1]
    section("experience", blocks("experience", ("role", "company", "location", "dates")))
    section("education", blocks("education", ("degree", "school", "location", "dates")))
    section("skills", [f"- **{e['label']}:** {', '.join(e['items'])}" if e["label"] else f"- {', '.join(e['items'])}"
                       for e in pool.sections["skills"].values()])
    for key in ("honors", "certificates", "committees"):
        section(key, [f"- {line(e['award'], e['event'], e['location'], e['date'])}" for e in pool.sections[key].values()])
    for key in ("extracurricular", "presentation", "writing"):
        section(key, blocks(key, ("role", "title", "location", "date")))
    section("projects", blocks("projects", ("title", "repo")))
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- the files


def is_template(path: Path) -> bool:
    return path.stem.casefold().startswith("template") or W.has_template_marker(path)


def usable_files() -> tuple[list[Path], list[Path]]:
    """(yamls, PDFs without a yaml) of Default_Resume_Files that may feed the master resume."""
    folder = W.DEFAULT_RESUMES
    yamls = sorted(p for p in (folder.glob("*.yaml") if folder.is_dir() else []) if p.is_file() and not is_template(p))
    pdfs = sorted(p for p in (folder.glob("*.pdf") if folder.is_dir() else [])
                  if p.is_file() and not p.with_suffix(".yaml").exists() and not is_template(p))
    return yamls, pdfs


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    try:
        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001  (a damaged PDF is still listed, only without text)
        return f"(the text of this PDF could not be read: {type(exc).__name__}: {exc})\n"
    text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
    return (text[:PDF_TEXT_CAP] or "(this PDF holds no extractable text)") + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build_draft() -> list[str]:
    """Write the draft and the PDF texts; return the output lines."""
    yamls, pdfs = usable_files()
    if not yamls and not pdfs:
        raise MasterError(f"no usable resume file in {W.DEFAULT_RESUMES} (Template* and files that still hold the "
                          f"template marker are skipped): add your resume as a .yaml or a .pdf there")
    pool = Pool()
    for path in yamls:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise MasterError(f"{path} cannot be read as a yaml ({type(exc).__name__}): fix or move it, then run again") from None
        if isinstance(data, dict):
            pool.add(data)
    write_atomic(draft_path(), render(pool))
    if pdf_dir().exists():
        shutil.rmtree(pdf_dir())
    lines = [f"DRAFT {draft_path()}"]
    for path in pdfs:
        target = pdf_dir() / f"{path.stem}.txt"
        write_atomic(target, pdf_text(path))
        lines.append(f"PDF {target}")
    lines.append(f"SUMMARY {len(yamls)} yaml, {len(pdfs)} pdf")
    return lines


_H1 = re.compile(r"^#\s+(.*?)\s*$")


def replace_section(profile: str, draft: str) -> str:
    """Profile.md with its `# Resume` section (up to the next level-1 heading) replaced by the draft."""
    lines = profile.splitlines()
    start = next((i for i, l in enumerate(lines) if (m := _H1.match(l)) and m.group(1).strip().lower() == "resume"), None)
    new = draft.strip("\n").splitlines()
    if start is None:
        return "\n".join(lines).rstrip("\n") + ("\n\n" if lines else "") + "\n".join(new) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if _H1.match(lines[i])), len(lines))
    tail = lines[end:]
    merged = lines[:start] + new + ([""] + tail if tail else [])
    return "\n".join(merged) + "\n"


def apply() -> str:
    draft = draft_path()
    if not draft.is_file():
        raise MasterError("no draft yet: run `ohmyjob resume master` first")
    try:
        text = draft.read_text(encoding="utf-8")
        profile = W.PROFILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise MasterError(f"{W.PROFILE} not found. Run /ohmyjob:setup") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise MasterError(f"{draft} or {W.PROFILE} cannot be read as UTF-8 text ({type(exc).__name__})") from None
    heads = [i for i, l in enumerate(text.splitlines()) if _H1.match(l)]
    if heads != [0] or _H1.match(text.splitlines()[0]).group(1).strip().lower() != "resume":
        raise MasterError(f"{draft} must start with `# Resume` and hold no other level-1 heading (use ## and ###)")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = W.STATE_DIR / "backup" / f"Profile-{stamp}.md"
    n = 1
    while backup.exists():
        n += 1
        backup = W.STATE_DIR / "backup" / f"Profile-{stamp}-{n}.md"
    write_atomic(backup, profile)
    write_atomic(W.PROFILE, replace_section(profile, text))
    return str(backup)


def run(apply_draft: bool) -> int:
    try:
        if apply_draft:
            print(f"OK {apply()}")
        else:
            for text in build_draft():
                print(text)
    except (MasterError, W.WorkspaceError) as exc:
        print(f"FATAL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run("--apply" in sys.argv[1:]))
