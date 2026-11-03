#!/usr/bin/env python3
"""Build a resume PDF: merge base.yaml with a role yaml, render the Awesome-CV LaTeX template, compile with LuaLaTeX.

CLI (one output line): `ohmyjob resume build <role.yaml> [--out <pdf>]` prints `OK <pdf>`, `OVERFLOW <N> pages` or
`FAIL <reason>`. Import API (used by pj.py): `build()`, `render()`, `rendered_len()`, `BULLET_BUDGET`, `SKILLS_BUDGET`.

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
# preamble (awesome-cv, A4, 1.5 cm side margins: \textwidth = 512.1 pt). Method: a LuaLaTeX calibration document
# typesets each sample as
#   \settowidth{\w}{\fontsize{9pt}{1em}\sffamily\fontseries{l}\selectfont <sample>}\typeout{B=\the\w;N=<characters>}
# for 13 bullets (1243 characters, bold markers removed) and 4 skill rows, and the widths are divided by the character
# counts: 9 pt Source Sans 3 Light averages 3.63 pt per character here (3.67 over a wider sample, 3.83 for the worst
# bullet; skill rows 3.59; the 10 pt bold labels 4.63). A bullet line is \textwidth minus the 2ex itemize indent
# (503.5 pt measured): 138 characters at 3.63. BULLET_BUDGET is one rendered line with a 10% margin for the wider
# bold terms and uneven text: 124 characters. A skills row sits in the X column of the cvskills table: \textwidth minus
# a 15 character label (69 pt) and 4 x 1ex of column padding (about 20 pt) leaves 423 pt, i.e. 118 characters.
# SKILLS_BUDGET is that one line with the same margin: 106 characters. Letter paper is wider than A4, so the budgets
# hold there too. Re-measure when the class, the fonts or the margins change (tests compile a full-budget line).
BULLET_BUDGET = 124
SKILLS_BUDGET = 106

LATEX_TIMEOUT = 180  # seconds for one lualatex run (a cold font cache can take a minute)
PAPERS = {"a4": "a4paper", "letter": "letterpaper"}
CLASS_FILE = Path(__file__).resolve().parents[1] / "resume" / "awesome-cv.cls"
TEMPLATE_DIR = CLASS_FILE.parent
TEMPLATE_NAME = "resume.tex.j2"
DEFAULT_HEADINGS = {"summary": "Summary", "experience": "Experience", "projects": "Projects",
                    "education": "Education", "skills": "Skills", "interests": "Interests"}
ACCENT_NAMES = {"emerald", "skyblue", "red", "pink", "orange", "nephritis", "concrete", "darknight"}


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


def _context(base: dict, role: dict, paper: str, photo: str = "") -> dict:
    if paper not in PAPERS:
        raise BuildError(f"paper must be a4 or letter, not {paper!r}")
    name = _dict(base.get("name"), "base.yaml name")
    accent = _str(base.get("accent_color"), "accent_color").lstrip("#")
    accent_hex = accent.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", accent) else ""
    accent_name = "awesome-" + (accent if accent in ACCENT_NAMES else "skyblue")
    headings = dict(DEFAULT_HEADINGS)
    for source in (base.get("sections"), role.get("sections")):
        for key, val in _dict(source or {}, "sections").items():
            if key in headings and _str(val, f"sections.{key}"):
                headings[key] = _str(val, f"sections.{key}")
    education = []
    show_details = _yes(role.get("education_details"))
    for i, d in enumerate(_list(base.get("education"), "education")):
        f = f"education[{i}]"
        d = _dict(d, f)
        education.append({
            "degree": _str(d.get("degree"), f"{f}.degree", required=True), "school": _str(d.get("school"), f"{f}.school"),
            "location": _str(d.get("location"), f"{f}.location"), "dates": _str(d.get("dates"), f"{f}.dates"),
            "details": _bullets(d.get("details"), f"{f}.details") if show_details else [],
        })
    projects = []
    for i, p in enumerate(_list(role.get("projects"), "projects")):
        f = f"projects[{i}]"
        p = _dict(p, f)
        projects.append({"title": _str(p.get("title"), f"{f}.title", required=True),
                         "repo": link(_str(p.get("repo"), f"{f}.repo"), f"{f}.repo"),
                         "bullets": _bullets(p.get("bullets"), f"{f}.bullets")})
    skills = []
    for i, s in enumerate(_list(role.get("skills"), "skills")):
        f = f"skills[{i}]"
        s = _dict(s, f)
        items = s.get("items")
        if isinstance(items, list):
            items = ", ".join(_str(x, f"{f}.items") for x in items)
        skills.append({"label": _str(s.get("label"), f"{f}.label", required=True), "set": _str(items, f"{f}.items")})
    return {
        "paper_option": PAPERS[paper], "accent_hex": accent_hex, "accent_name": accent_name, "headings": headings,
        "name": {"first": _str(name.get("first"), "name.first", required=True),
                 "last": _str(name.get("last"), "name.last", required=True)},
        "title": _str(role.get("title"), "title", required=True),
        "address": _str(base.get("address"), "address"), "phone": _str(base.get("phone"), "phone"),
        "email": _str(base.get("email"), "email"), "homepage": web_url(_str(base.get("homepage"), "homepage"), "homepage"),
        "github": _str(base.get("github"), "github"), "linkedin": _str(base.get("linkedin"), "linkedin"),
        "extra": _str(base.get("extra"), "extra"), "date_of_birth": _str(base.get("date_of_birth"), "date_of_birth"),
        "photo": photo,
        "introduction": _str(role.get("introduction"), "introduction"),
        "experience": _experience(role.get("experience")),
        "projects": projects, "education": education, "skills": skills,
        # base.yaml `interests` is the text; the role yaml's `interests: yes|no` says whether it is printed
        "interests": _str(base.get("interests"), "interests") if _yes(role.get("interests")) else "",
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


def _inputs(role_yaml):
    if not Path(W.BASE_YAML).exists():
        raise BuildError("base.yaml not found (run /ohmyjob:profile)")
    return _load(W.BASE_YAML, "base.yaml"), _load(role_yaml, "role yaml")


def render(role_yaml, *, paper=None, photo: str = "") -> str:
    """LaTeX source for base.yaml (W.BASE_YAML) merged with `role_yaml`. Raises BuildError."""
    base, role = _inputs(role_yaml)
    return _ENV.get_template(TEMPLATE_NAME).render(**_context(base, role, _paper(paper), photo))


# ----------------------------------------------------------------------------------------------------------------
# compile

def _lualatex():
    exe = os.environ.get("OHMYJOB_LUALATEX") or shutil.which("lualatex")
    if not exe:
        raise BuildError("lualatex not found (run /ohmyjob:setup)")
    return exe


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
        base, role = _inputs(role_yaml)
        W.BUILD_DIR.mkdir(parents=True, exist_ok=True)
        bdir = Path(tempfile.mkdtemp(prefix="b-", dir=W.BUILD_DIR))
        photo = ""
        if _str(base.get("photo"), "photo"):
            src = Path(_str(base.get("photo"), "photo"))
            src = src if src.is_absolute() else Path(W.BASE_YAML).parent / src
            if src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".pdf") or not src.is_file():
                raise BuildError(f"photo {src.name} must be an existing jpg, png or pdf file")
            photo = "photo" + src.suffix.lower()
            shutil.copyfile(src, bdir / photo)
        (bdir / "resume.tex").write_text(
            _ENV.get_template(TEMPLATE_NAME).render(**_context(base, role, paper, photo)), encoding="utf-8", errors="replace")
        shutil.copyfile(CLASS_FILE, bdir / CLASS_FILE.name)
        exe = _lualatex()  # only now: bad input is reported as such even where TeX is not installed
        try:
            proc = subprocess.run(
                [exe, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "-jobname=resume", "resume.tex"],
                cwd=bdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=LATEX_TIMEOUT)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ohmyjob resume")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a PDF from a role yaml")
    b.add_argument("yaml", type=Path)
    b.add_argument("--out", type=Path, default=None, help="PDF path (default: the yaml path with .pdf)")
    args = ap.parse_args(argv)
    out = args.out or args.yaml.with_suffix(".pdf")
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
