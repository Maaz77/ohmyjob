#!/usr/bin/env python3
"""Build a resume PDF: read ONE full resume yaml, render the Awesome-CV LaTeX template, compile with LuaLaTeX.

CLI: `ohmyjob resume build <resume.yaml> [--out <pdf>]` prints one line: `OK <pdf>`, `OVERFLOW <N> pages` or
`FAIL <reason>`. `ohmyjob resume build-all [--dir <dir>]` builds every *.yaml of the dir (default
Default_Resume_Files) next to itself and prints one line per file. Import API (used by pj.py): `build()`, `render()`, `ignored_keys()`, `rendered_len()`, `BULLET_BUDGET`, `SKILLS_BUDGET`.

Everything in the yaml is data from a model: `tex()` escapes every LaTeX special, so yaml text never reaches LaTeX as a
command. LuaLaTeX runs with shell escape off, in a throw-away dir under W.BUILD_DIR.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import jinja2
import yaml

import workspace as W

# ----------------------------------------------------------------------------------------------------------------
# Character budgets, checked by pj.py before a build. Both mean ONE rendered line. Measured with the resume's own
# preamble (awesome-cv, upstream geometry: 1.4 cm side margins, \textwidth = 517.8 pt on A4, 534.6 pt on letter).
# Method: a LuaLaTeX calibration document typesets each sample as
#   \settowidth{\w}{\fontsize{9pt}{1em}\sffamily\fontseries{l}\selectfont <sample>}\typeout{B=\the\w;N=<characters>}
# for 13 bullets (bold markers removed) and 6 skill rows, and the widths are divided by the character counts: 9 pt
# Source Sans 3 Light averages 3.67 pt per character (3.83 for the worst bullet; skill rows 3.59, worst 3.96). A bullet
# line is the cvitems \linewidth (\textwidth minus the itemize indent), 507.2 pt on A4: 138 characters at 3.67.
# BULLET_BUDGET is one rendered line with a 10% margin for the wider bold terms and uneven text: 124 characters. The
# items column of a cvskill row is 435.9 pt on A4 (\linewidth typeset inside the second argument of \cvskill, after a
# 15 character label and the column padding), i.e. 121 characters at 3.59. SKILLS_BUDGET is that one line with the
# same margin: 109 characters. Letter paper is wider than A4 (columns 16.8 pt wider), so the budgets hold there too.
# Re-measure when the class, the fonts or the geometry change (tests compile a full-budget line).
BULLET_BUDGET = 124
SKILLS_BUDGET = 109

LATEX_TIMEOUT = 180  # seconds for one lualatex run (a cold font cache can take a minute)
PAPERS = {"a4": "a4paper", "letter": "letterpaper"}
CLASS_FILE = Path(__file__).resolve().parents[1] / "resume" / "awesome-cv.cls"
TEMPLATE_DIR = CLASS_FILE.parent
TEMPLATE_NAME = "resume.tex.j2"
# Sections, their order and headings per document, exactly as in upstream's examples/resume/*.tex and examples/cv/*.tex.
DOCUMENTS = {
    "resume": {"summary": "Summary", "experience": "Work Experience", "honors": "Honors & Awards",
               "certificates": "Certificates", "education": "Education"},
    "cv": {"education": "Education", "skills": "Skills", "experience": "Experience",
           "extracurricular": "Extracurricular Activity", "honors": "Honors & Awards", "certificates": "Certificates",
           "presentation": "Presentation", "writing": "Writing", "committees": "Program Committees"},
}
HONOR_KEYS = ("honors", "certificates", "committees")  # cvhonor lists {award, event, location, date}
ENTRY_KEYS = ("extracurricular", "presentation", "writing")  # cventry lists {role, title, location, date, items}
# yaml key -> the section that shows it (`introduction` is the Summary paragraph)
KEY_SECTION = {"introduction": "summary"}
OLD_KEYS = ("projects", "interests")  # still accepted so old files build; never printed
ACCENT_NAMES = ("emerald", "skyblue", "red", "pink", "orange", "nephritis", "concrete", "darknight")


class BuildError(Exception):
    """The yaml cannot be turned into a resume; the message names the field."""


# ----------------------------------------------------------------------------------------------------------------
# text escaping

_TEX_SPECIAL = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "$": r"\$",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_TEX_SPECIAL_RE = re.compile("[" + re.escape("".join(_TEX_SPECIAL)) + "]")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_JUNK = {"​", "‌", "‍", "⁠", "﻿"}


def _clean(value) -> str:
    """Text as one line without control characters, surrogates or zero-width marks."""
    if value is None:
        return ""
    text = "".join(" " if c in "\t\r\n  " else c for c in str(value)
                   if c not in _JUNK and unicodedata.category(c) not in ("Cc", "Cs") or c in "\t\r\n")
    return re.sub(r"\s+", " ", text).strip()


def _escape(text: str) -> str:
    return _TEX_SPECIAL_RE.sub(lambda m: _TEX_SPECIAL[m.group(0)], text)


def tex(value) -> str:
    """LaTeX-safe text. Escapes \\ { } & % # _ $ ~ ^; `**word**` becomes bold (the only markup that survives)."""
    text = _clean(value)
    out, pos = [], 0
    for m in _BOLD_RE.finditer(text):
        out.append(_escape(text[pos:m.start()]))
        out.append(r"\textbf{" + _escape(m.group(1)) + "}")
        pos = m.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def plain(value) -> str:
    """Escape-only text for headings and header fields: no markup at all (`**` stays literal)."""
    return _escape(_clean(value))


def rendered_len(text) -> int:
    """Number of characters the text prints: the `**` bold markers and extra whitespace do not count."""
    return len(_BOLD_RE.sub(r"\1", _clean(text)))


_URL_SAFE_RE = re.compile(r"[A-Za-z0-9.:/?=+@,;!*()\[\]-]")
_URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):(?!\d+(?:[/?#]|$))")
_URL_ESCAPES = {"#": r"\#", "&": r"\&", "%": r"\%", "_": r"\_", "~": r"\string~"}


def web_url(value, field="address") -> str:
    """An http(s) address as LaTeX hyperlink target text; "" when empty. A bare `example.com/x` gets `https://`; any
    other scheme (`run:`, `file:`, `javascript:` ...) is refused, so the PDF holds no link action but a web link.
    `# & % _ ~` keep their meaning in the URL and are written escaped; other unsafe characters are percent-encoded."""
    text = _clean(value).replace(" ", "")
    if not text:
        return ""
    m = _URL_SCHEME_RE.match(text)
    if m:
        if m.group(1).lower() not in ("http", "https") or not text[m.end():].startswith("//"):
            raise BuildError(f"{field}: only http(s) addresses are allowed, not {text[:40]!r}")
    else:
        text = "https://" + text
    return "".join(_URL_ESCAPES.get(c) or (c if _URL_SAFE_RE.fullmatch(c) else "".join(f"%{b:02X}" for b in c.encode()))
                   for c in text)


def link(value, field="address") -> str:
    """A web address as a LaTeX hyperlink (empty when there is none), shown without its scheme."""
    target = web_url(value, field)
    if not target:
        return ""
    shown = re.sub(r"^https?://", "", _clean(value).replace(" ", ""), flags=re.I)
    return r"\href{" + target + "}{" + plain(shown) + "}"


def cvitems(bullets) -> str:
    """The `cvitems` list for a cventry description (empty without bullets)."""
    items = [tex(b) for b in (bullets or []) if _clean(b)]
    if not items:
        return ""
    return "\\begin{cvitems}" + "".join("\\item{} " + i for i in items) + "\\end{cvitems}"


# ----------------------------------------------------------------------------------------------------------------
# yaml -> template context

def _yes(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("yes", "y", "true", "on", "1")


def _load(path: Path, what: str) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise BuildError(f"{what} {Path(path).name} is not UTF-8 text")
    except OSError as exc:
        raise BuildError(f"cannot read {what} {path}: {exc.strerror or exc}")
    except yaml.YAMLError as exc:
        raise BuildError(f"{what} {Path(path).name} is not valid yaml: {str(exc).splitlines()[0] if str(exc) else exc}")
    if not isinstance(data, dict):
        raise BuildError(f"{what} {Path(path).name} must be a yaml mapping")
    return data


def _str(value, field: str, required=False) -> str:
    if isinstance(value, (list, dict)):
        raise BuildError(f"{field} must be text")
    text = _clean(value)
    if required and not text:
        raise BuildError(f"{field} is missing")
    return text


def _list(value, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BuildError(f"{field} must be a list")
    return value


def _dict(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise BuildError(f"{field} must be a mapping")
    return value


def _bullets(value, field: str) -> list:
    out = []
    for i, b in enumerate(_list(value, field)):
        out.append(_str(b, f"{field}[{i}]"))
    return [b for b in out if b]


def _experience(raw) -> list:
    entries = []
    for i, item in enumerate(_list(raw, "experience")):
        f = f"experience[{i}]"
        item = _dict(item, f)
        if "roles" in item:  # grouped employer: first role carries employer + location, later roles one row each
            employer = _str(item.get("employer"), f"{f}.employer", required=True)
            location = _str(item.get("location"), f"{f}.location")
            roles = _list(item.get("roles"), f"{f}.roles")
            if not roles:
                raise BuildError(f"{f}.roles is empty")
            for j, r in enumerate(roles):
                rf = f"{f}.roles[{j}]"
                r = _dict(r, rf)
                entries.append({
                    "position": _str(r.get("role"), f"{rf}.role", required=True),
                    "title": employer if j == 0 else "", "location": location if j == 0 else "",
                    "dates": _str(r.get("dates", item.get("dates") if j == 0 else ""), f"{rf}.dates"),
                    "bullets": _bullets(r.get("bullets"), f"{rf}.bullets"),
                })
        else:
            entries.append({
                "position": _str(item.get("role"), f"{f}.role", required=True),
                "title": _str(item.get("company"), f"{f}.company"),
                "location": _str(item.get("location"), f"{f}.location"),
                "dates": _str(item.get("dates"), f"{f}.dates"),
                "bullets": _bullets(item.get("bullets"), f"{f}.bullets"),
            })
    return entries


def _document(data: dict) -> str:
    document = _str(data.get("document"), "document").lower() or "resume"
    if document not in DOCUMENTS:
        raise BuildError(f"document must be resume or cv, not {document!r}")
    return document


def _honors(raw, key: str) -> list:
    out = []
    for i, h in enumerate(_list(raw, key)):
        f = f"{key}[{i}]"
        h = _dict(h, f)
        out.append({"award": _str(h.get("award"), f"{f}.award", required=True), "event": _str(h.get("event"), f"{f}.event"),
                    "location": _str(h.get("location"), f"{f}.location"), "date": _str(h.get("date"), f"{f}.date")})
    return out


def _entries(raw, key: str) -> list:
    out = []
    for i, e in enumerate(_list(raw, key)):
        f = f"{key}[{i}]"
        e = _dict(e, f)
        out.append({"role": _str(e.get("role"), f"{f}.role", required=True), "title": _str(e.get("title"), f"{f}.title"),
                    "location": _str(e.get("location"), f"{f}.location"), "date": _str(e.get("date"), f"{f}.date"),
                    "items": _bullets(e.get("items"), f"{f}.items")})
    return out


def _accent(data: dict) -> tuple[str, bool]:
    """(upstream colour name, whether the yaml named a colour upstream does not have)."""
    raw = _str(data.get("accent_color"), "accent_color").lower()
    raw = raw[len("awesome-"):] if raw.startswith("awesome-") else raw
    if not raw:
        return "red", False
    return (raw, False) if raw in ACCENT_NAMES else ("red", True)


def ignored_keys(data: dict) -> list[str]:
    """WARNING texts for yaml keys this document type does not print (never a failure)."""
    document = _document(data)
    shown = {KEY_SECTION.get(k, k) for k in ("introduction", "experience", "education", "skills", *HONOR_KEYS, *ENTRY_KEYS)}
    shown &= set(DOCUMENTS[document])
    out = []
    for key in ("introduction", "skills", "experience", "education", *HONOR_KEYS, *ENTRY_KEYS, *OLD_KEYS):
        value = data.get(key)
        if key == "interests" and not _yes(data.get("show_interests")):
            continue
        if value in (None, "", [], {}) or KEY_SECTION.get(key, key) in shown:
            continue
        out.append(f"{key} is not shown in a {document}" if key not in OLD_KEYS
                   else f"{key} is no longer shown in any document")
    if _accent(data)[1]:
        out.append(f"accent_color {_str(data.get('accent_color'), 'accent_color')!r} is not an Awesome-CV colour "
                   f"({', '.join(ACCENT_NAMES)}); using red")
    return out


def _context(data: dict, paper: str, photo: str = "") -> dict:
    if paper not in PAPERS:
        raise BuildError(f"paper must be a4 or letter, not {paper!r}")
    if isinstance(data.get("interests"), bool):
        raise BuildError("interests must be text; the yes/no flag is show_interests")
    document = _document(data)
    name = _dict(data.get("name"), "name")
    headings = dict(DOCUMENTS[document])
    for key, val in _dict(data.get("sections") or {}, "sections").items():
        if key in headings and _str(val, f"sections.{key}"):
            headings[key] = _str(val, f"sections.{key}")
    education = []
    show_details = _yes(data.get("education_details"))
    for i, d in enumerate(_list(data.get("education"), "education")):
        f = f"education[{i}]"
        d = _dict(d, f)
        education.append({
            "degree": _str(d.get("degree"), f"{f}.degree", required=True), "school": _str(d.get("school"), f"{f}.school"),
            "location": _str(d.get("location"), f"{f}.location"), "dates": _str(d.get("dates"), f"{f}.dates"),
            "details": _bullets(d.get("details"), f"{f}.details") if show_details else [],
        })
    skills = []
    for i, s in enumerate(_list(data.get("skills"), "skills")):
        f = f"skills[{i}]"
        s = _dict(s, f)
        items = s.get("items")
        if isinstance(items, list):
            items = ", ".join(_str(x, f"{f}.items") for x in items)
        skills.append({"label": _str(s.get("label"), f"{f}.label", required=True), "set": _str(items, f"{f}.items")})
    introduction = _str(data.get("introduction"), "introduction")
    experience = _experience(data.get("experience"))
    lists = {k: _honors(data.get(k), k) for k in HONOR_KEYS}
    lists.update({k: _entries(data.get(k), k) for k in ENTRY_KEYS})
    present = {"summary": introduction, "experience": experience, "education": education, "skills": skills, **lists}
    order = [k for k in DOCUMENTS[document] if present.get(k)]  # a section without entries is left out
    return {
        "paper_option": PAPERS[paper], "document": document, "accent": _accent(data)[0], "headings": headings,
        "name": {"first": _str(name.get("first"), "name.first", required=True),
                 "last": _str(name.get("last"), "name.last", required=True)},
        "title": _str(data.get("title"), "title", required=True),
        "address": _str(data.get("address"), "address", required=True), "phone": _str(data.get("phone"), "phone", required=True),
        "email": _str(data.get("email"), "email", required=True), "homepage": web_url(_str(data.get("homepage"), "homepage"), "homepage"),
        "github": _str(data.get("github"), "github"), "linkedin": _str(data.get("linkedin"), "linkedin"),
        "extra": _str(data.get("extra"), "extra"), "date_of_birth": _str(data.get("date_of_birth"), "date_of_birth"),
        "quote": _str(data.get("quote"), "quote"), "photo": photo,
        "introduction": introduction, "experience": experience, "education": education, "skills": skills,
        "lists": lists, "order": order,
    }


_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False, keep_trailing_newline=True,
    trim_blocks=True, lstrip_blocks=True, undefined=jinja2.StrictUndefined,
    block_start_string="<%", block_end_string="%>", variable_start_string="<<", variable_end_string=">>",
    comment_start_string="<#", comment_end_string="#>",
)
_ENV.filters.update(tex=tex, plain=plain, cvitems=cvitems)


def _paper(paper):
    return paper if paper is not None else W.load_config()["pipeline"]["resume_paper"]


def _inputs(resume_yaml) -> dict:
    return _load(resume_yaml, "resume yaml")


def render(resume_yaml, *, paper=None, photo: str = "") -> str:
    """LaTeX source for one full resume yaml. Raises BuildError."""
    return _ENV.get_template(TEMPLATE_NAME).render(**_context(_inputs(resume_yaml), _paper(paper), photo))


# ----------------------------------------------------------------------------------------------------------------
# compile

def _lualatex():
    exe = os.environ.get("OHMYJOB_LUALATEX") or shutil.which("lualatex")
    if not exe:
        raise BuildError("lualatex not found (run /ohmyjob:setup)")
    return exe


def _latex_env() -> dict:
    """The environment of a lualatex run. With $OHMYJOB_DATA set, the font and format caches (TEXMFVAR, TEXMFCACHE)
    live in $OHMYJOB_DATA/texmf-var, so nothing is written to the user's home."""
    env = dict(os.environ)
    data = os.environ.get("OHMYJOB_DATA")
    if data:
        cache = Path(data) / "texmf-var"
        cache.mkdir(parents=True, exist_ok=True)
        env["TEXMFVAR"] = env["TEXMFCACHE"] = str(cache)
    return env


def _log_error(log: str) -> str:
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!"):
            return " ".join(" ".join(lines[i:i + 3]).split())
    return " ".join(" ".join(lines[-3:]).split()) or "lualatex failed without a log"


def build(role_yaml: Path, out_pdf: Path, *, max_pages: int | None = None, paper: str | None = None) -> tuple[str, int | None, str]:
    """Build `out_pdf`. Returns ("OK" | "OVERFLOW" | "FAIL", pages, detail). max_pages None reads
    pipeline.resume_max_pages from ohmyjob.json, 0 means no limit; paper None reads pipeline.resume_paper.
    On OVERFLOW and FAIL no PDF is written."""
    bdir = None
    try:
        Path(out_pdf).unlink(missing_ok=True)  # a PDF of an earlier build must not outlive a failed one
    except OSError:
        pass
    try:
        if max_pages is None:
            max_pages = W.load_config()["pipeline"]["resume_max_pages"]
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 0:
            raise BuildError(f"max_pages must be a whole number >= 0, not {max_pages!r}")
        paper = _paper(paper)
        data = _inputs(role_yaml)
        if _document(data) != "resume":
            max_pages = 0  # the page limit is for a resume; a CV has none
        W.BUILD_DIR.mkdir(parents=True, exist_ok=True)
        bdir = Path(tempfile.mkdtemp(prefix="b-", dir=W.BUILD_DIR))
        photo = ""
        if _str(data.get("photo"), "photo"):
            src = Path(_str(data.get("photo"), "photo"))
            src = src if src.is_absolute() else Path(role_yaml).parent / src
            if src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".pdf") or not src.is_file():
                raise BuildError(f"photo {src.name} must be an existing jpg, png or pdf file")
            photo = "photo" + src.suffix.lower()
            shutil.copyfile(src, bdir / photo)
        (bdir / "resume.tex").write_text(
            _ENV.get_template(TEMPLATE_NAME).render(**_context(data, paper, photo)), encoding="utf-8", errors="replace")
        shutil.copyfile(CLASS_FILE, bdir / CLASS_FILE.name)
        exe = _lualatex()  # only now: bad input is reported as such even where TeX is not installed
        try:
            proc = subprocess.run(
                [exe, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "-jobname=resume", "resume.tex"],
                cwd=bdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=LATEX_TIMEOUT,
                env=_latex_env())
        except subprocess.TimeoutExpired:
            return "FAIL", None, f"lualatex timed out after {LATEX_TIMEOUT}s"
        except OSError as exc:
            return "FAIL", None, f"cannot run lualatex: {exc.strerror or exc}"
        log = proc.stdout.decode("utf-8", "replace")
        pdf = bdir / "resume.pdf"
        lost = re.search(r"Missing character: [^\n]*", log)
        if lost:  # the font has no glyph (CJK, Arabic ...): the text would vanish from the PDF
            return "FAIL", None, lost.group(0).strip()
        if proc.returncode != 0 or not pdf.exists():
            return "FAIL", None, _log_error(log)
        m = re.search(r"Output written on .*\((\d+) pages?", log)
        if not m:
            return "FAIL", None, "no page count in the lualatex log"
        pages = int(m.group(1))
        if max_pages and pages > max_pages:
            return "OVERFLOW", pages, f"{pages} pages, limit {max_pages}"
        out_pdf = Path(out_pdf)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_pdf.with_name(out_pdf.name + ".tmp")
        shutil.copyfile(pdf, tmp)
        os.replace(tmp, out_pdf)
        return "OK", pages, str(out_pdf)
    except (BuildError, W.WorkspaceError, jinja2.TemplateError) as exc:  # ConfigError is a WorkspaceError
        return "FAIL", None, str(exc)
    except ValueError as exc:
        return "FAIL", None, f"bad input: {exc}"
    except OSError as exc:
        return "FAIL", None, f"{exc.strerror or exc}: {exc.filename or ''}".strip()
    finally:
        if bdir is not None:
            shutil.rmtree(bdir, ignore_errors=True)


# ----------------------------------------------------------------------------------------------------------------
# CLI

def _one_line(text: str) -> str:
    return " ".join(str(text).split())[:300]


def build_all(directory: Path) -> int:
    """Build every *.yaml of `directory` (sorted) into the PDF with the same name; one line per file."""
    files = sorted(Path(directory).glob("*.yaml"))
    if not files:
        print(f"FATAL: no resume yaml files in {directory}")
        return 1
    counts = {"OK": 0, "OVERFLOW": 0, "FAIL": 0}
    for path in files:
        try:
            status, pages, detail = build(path, path.with_suffix(".pdf"))
        except Exception as exc:  # one broken file must not stop the others
            status, pages, detail = "FAIL", None, f"{type(exc).__name__}: {exc}"
        counts[status] += 1
        tail = {"OK": "OK", "OVERFLOW": f"OVERFLOW {pages} pages"}.get(status) or f"FAIL {_one_line(detail)}"
        print(f"{path.name}: {tail}")
        try:
            for text in ignored_keys(_load(path, "resume yaml")):
                print(f"WARNING {path.name}: {text}")
        except BuildError:
            pass
        try:
            if W.has_template_marker(path):
                print(f"WARNING {path.name}: still has the template marker")
        except OSError:
            pass
    print(f"SUMMARY {counts['OK']} OK, {counts['OVERFLOW']} OVERFLOW, {counts['FAIL']} FAIL")
    return 0 if counts["OK"] == len(files) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ohmyjob resume")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a PDF from a role yaml")
    b.add_argument("yaml", type=Path)
    b.add_argument("--out", type=Path, default=None, help="PDF path (default: the yaml path with .pdf)")
    a = sub.add_parser("build-all", help="build every *.yaml of a folder into a PDF next to it")
    a.add_argument("--dir", type=Path, default=None, help="folder (default: Default_Resume_Files)")
    args = ap.parse_args(argv)
    if args.cmd == "build-all":
        try:
            return build_all(args.dir or W.DEFAULT_RESUMES)
        except Exception as exc:
            print(f"FATAL: {type(exc).__name__}: {exc}")
            return 1
    out = args.out or args.yaml.with_suffix(".pdf")
    try:
        for text in ignored_keys(_load(args.yaml, "resume yaml")):
            print(f"WARNING {args.yaml.name}: {text}", file=sys.stderr)  # stdout stays one line
    except BuildError:
        pass  # build() reports the real problem
    try:
        status, pages, detail = build(args.yaml, out)
    except Exception as exc:  # the CLI contract is one line, never a traceback
        status, pages, detail = "FAIL", None, f"{type(exc).__name__}: {exc}"
    if status == "OK":
        print(f"OK {out}")
    elif status == "OVERFLOW":
        print(f"OVERFLOW {pages} pages")
    else:
        print(f"FAIL {_one_line(detail)}")
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
