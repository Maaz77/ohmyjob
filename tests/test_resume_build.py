"""resume_build: escaping, merge, template mapping, and real LuaLaTeX builds (page limit, paper sizes, fixture persona)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import resume_build as rb
import workspace as W

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "workspace" / "Default_Resume_Files"
LUALATEX = os.environ.get("OHMYJOB_LUALATEX") or shutil.which("lualatex")
needs_tex = pytest.mark.skipif(not LUALATEX, reason="lualatex is not installed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tmp dir holding a copy of the fixture yamls; W.BUILD_DIR points into it."""
    d = tmp_path / "Default_Resume_Files"
    shutil.copytree(FIXTURE, d)
    monkeypatch.setattr(W, "BUILD_DIR", tmp_path / "build")
    return d


def role(d: Path, **changes) -> Path:
    data = yaml.safe_load((d / "Operations-Analyst.yaml").read_text())
    data.update(changes)
    path = d / "Test-Role.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


# --------------------------------------------------------------------------- escaping

def test_tex_escapes_every_special():
    assert rb.tex(r"a & b % c # d _ e $ f") == r"a \& b \% c \# d \_ e \$ f"
    assert rb.tex("~ ^") == r"\textasciitilde{} \textasciicircum{}"
    assert rb.tex(r"\x{y}") == r"\textbackslash{}x\{y\}"


def test_tex_bold_markup_and_whitespace():
    assert rb.tex("a **key & term** b") == r"a \textbf{key \& term} b"
    assert rb.tex("2 ** 3 and **") == "2 ** 3 and **"
    assert rb.tex("line\n\nbreak\x00\u200b  here") == "line break here"
    assert rb.tex(None) == "" and rb.tex(5) == "5"


def test_rendered_len_ignores_markers():
    assert rb.rendered_len("a **bc** d") == len("a bc d")
    assert rb.rendered_len("  x \n y ") == 3


def test_budgets_are_one_line_ints():
    assert isinstance(rb.BULLET_BUDGET, int) and isinstance(rb.SKILLS_BUDGET, int)
    assert 100 <= rb.BULLET_BUDGET <= 140 and 90 <= rb.SKILLS_BUDGET <= 120


def test_render_never_passes_yaml_text_as_commands(env):
    evil = r"\input{/etc/passwd} \write18{id} \end{document} {x} $y$ 100% a_b"
    skills = [{"label": r"\x", "items": evil}]
    assert r"\cvskill{\textbackslash{}x}{\textbackslash{}input" in rb.render(role(env, skills=skills, document="cv"), paper="a4")
    src = rb.render(role(env, introduction=evil, skills=skills), paper="a4")
    assert r"\textbackslash{}input\{/etc/passwd\}" in src
    assert "\\input{/etc" not in src and "\\write18" not in src
    assert src.count(r"\end{document}") == 1
    assert src.count("\\input") == 0


def test_link_filter_is_url_safe():
    assert rb.link("") == ""
    out = rb.link("https://example.com/a_b}\\x%20#readme?a=1&b=2")
    assert out.startswith(r"\href{https://example.com/a\_b%7D%5Cx\%20\#readme?a=1\&b=2}{") and "}\\x" not in out
    assert rb.link("example.com/r") == r"\href{https://example.com/r}{example.com/r}"
    assert rb.web_url("http://example.com/~me") == r"http://example.com/\string~me"
    assert rb.web_url("example.com:8080/x") == "https://example.com:8080/x"


@pytest.mark.parametrize("bad", ["run:x", "file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "https:x", "run://x"])
def test_only_http_links_are_allowed(bad):
    with pytest.raises(rb.BuildError, match="only http"):
        rb.web_url(bad)


def test_plain_has_no_markup():
    assert rb.plain("a **b** _c_ ~") == r"a **b** \_c\_ \textasciitilde{}"


# --------------------------------------------------------------------------- merge and mapping

def test_mapping(env):
    src = rb.render(env / "Operations-Analyst.yaml", paper="a4")
    assert r"\documentclass[11pt, a4paper]{awesome-cv}" in src
    assert r"\geometry{left=1.4cm, top=.8cm, right=1.4cm, bottom=1.8cm, footskip=.5cm}" in src
    assert r"\colorlet{awesome}{awesome-red}" in src and r"\setbool{acvSectionColorHighlight}{true}" in src
    assert r"\renewcommand{\acvHeaderSocialSep}{\quad\textbar\quad}" in src and "pagestyle" not in src
    assert r"\name{Alex}{Example}" in src and r"\position{Operations Analyst}" in src          # one full yaml
    assert r"\email{alex.example@example.com}" in src and r"\cvsection{Profile}" in src        # heading override
    assert r"\makecvheader[C]" in src and r"Alex Example~~~\textperiodcentered" not in src
    assert "{Alex Example~~~\u00b7~~~R\u00e9sum\u00e9}" in src and r"\makecvfooter" in src
    # upstream's resume: Summary, Work Experience, Education (this fixture has no honors or certificates)
    heads = re.findall(r"\\cvsection\{([^}]*)\}", src)
    assert heads == ["Profile", "Work Experience", "Education"]
    # grouped employer: first role carries employer + location, the later role has an empty title and location
    assert r"\cventry{Senior Operations Analyst}{Harborline Freight Group}{Hamburg, Germany}{2022 - Present}" in src
    assert r"\cventry{Operations Analyst}{}{}{2020 - 2022}" in src
    assert r"\cventry{Supply Planning Analyst}{Brightwater Foods}{Berlin, Germany}{2018 - 2020}" in src   # plain entry
    assert r"\textbf{dock utilisation}" in src
    assert "cvskill" not in src and "Trail running" not in src and "dock-scheduling" not in src   # not in a resume
    assert "Thesis: forecast error" in src                                                      # education_details yes


def test_resume_and_cv_sections_follow_upstream(env):
    data = yaml.safe_load((ROOT / "templates" / "Template.yaml").read_text())
    full = dict(data, skills=[{"label": "A", "items": "b"}], extracurricular=[{"role": "R", "title": "T"}],
                presentation=[{"role": "R"}], writing=[{"role": "R"}], committees=[{"award": "A"}])
    path = env / "full.yaml"
    path.write_text(yaml.safe_dump(full))
    src = rb.render(path, paper="a4")
    assert re.findall(r"\\cvsection\{([^}]*)\}", src) == [
        "Summary", "Work Experience", "Honors \\& Awards", "Certificates", "Education"]
    assert r"\begin{cvparagraph}" in src and src.count(r"\begin{cvhonors}") == 2
    assert r"\cvhonor{2nd Place}{Rhein-Spree Logistics Case Competition}{Berlin, Germany}{2017}" in src
    assert "\\makecvheader[C]" in src and "R\u00e9sum\u00e9}" in src
    assert "Curriculum Vitae" not in src
    full["document"] = "cv"
    path.write_text(yaml.safe_dump(full))
    src = rb.render(path, paper="letter")
    assert "[11pt, letterpaper]{awesome-cv}" in src
    assert re.findall(r"\\cvsection\{([^}]*)\}", src) == [
        "Education", "Skills", "Experience", "Extracurricular Activity", "Honors \\& Awards", "Certificates",
        "Presentation", "Writing", "Program Committees"]
    assert "\\makecvheader\n" in src and "[C]" not in src.split("begin{document}")[1]
    assert "Curriculum Vitae}" in src and r"\begin{cvskills}" in src and r"\cvskill{A}{b}" in src
    assert r"\cventry{R}{T}{}{}{}" in src and r"\cvhonor{A}{}{}{}" in src
    assert "cvparagraph" not in src                                                              # introduction: resume only


def test_empty_sections_are_left_out_and_document_is_checked(env):
    src = rb.render(role(env, honors=[], certificates=None, education=[], introduction=""), paper="a4")
    assert re.findall(r"\\cvsection\{([^}]*)\}", src) == ["Work Experience"]
    status, _, detail = rb.build(role(env, document="letter"), env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "document must be resume or cv" in detail
    assert rb.render(role(env, document="CV"), paper="a4").count("Curriculum Vitae") == 1
    status, _, detail = rb.build(role(env, honors=[{"event": "E"}]), env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "honors[0].award is missing" in detail


def test_keys_the_document_does_not_show_are_warnings(env):
    data = yaml.safe_load((ROOT / "templates" / "Template.yaml").read_text())
    assert rb.ignored_keys(data) == []
    assert rb.ignored_keys(yaml.safe_load((ROOT / "templates" / "Template-CV.yaml").read_text())) == []
    old = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    assert [w.split(" ")[0] for w in rb.ignored_keys(old)] == ["skills", "projects", "interests"]
    assert rb.ignored_keys(dict(data, document="cv"))[0].startswith("introduction is not shown in a cv")
    assert rb.ignored_keys(dict(data, accent_color="#aa3311"))[0].startswith("accent_color")
    # old files still build (a warning, never a failure)
    assert rb.render(env / "Operations-Analyst.yaml", paper="a4")


def test_header_urls_and_plain_fields(env):
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    base.update(homepage="example.com/~me_x", github="a_b", linkedin="**x**", phone="**1**", email="a_b@example.com",
                date_of_birth="**d**", quote="Be _it_", sections={"summary": "**S**", "experience": "A_B"})
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    src = rb.render(env / "Operations-Analyst.yaml", paper="a4")
    assert r"\homepage{https://example.com/\string~me\_x}" in src and r"\github{a\_b}" in src
    assert r"\linkedin{**x**}" in src and r"\mobile{**1**}" in src and r"\email{a\_b@example.com}" in src
    assert r"\cvsection{**S**}" in src and r"\cvsection{A\_B}" in src and "textbf{x}" not in src
    assert '\\quote{``Be \\_it\\_"}' in src


@pytest.mark.parametrize("field, value", [("homepage", "run:x"), ("homepage", "file:///etc/passwd")])
def test_bad_homepage_scheme_is_a_fail(env, field, value):
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    base[field] = value
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "homepage" in detail and "only http" in detail


def test_stale_pdf_is_removed_on_failure(env):
    out = env / "o.pdf"
    out.write_bytes(b"%PDF old")
    bad = role(env, title="")
    assert rb.build(bad, out, max_pages=1, paper="a4")[0] == "FAIL" and not out.exists()


def test_non_utf8_yaml_is_a_fail(env):
    bad = env / "bad.yaml"
    bad.write_bytes(b"title: \xff\xfe\n")
    status, _, detail = rb.build(bad, env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "UTF-8" in detail


def test_ohmyjob_lualatex_beats_path(env, monkeypatch, tmp_path):
    marker = tmp_path / "ran"
    fake = tmp_path / "fakebin" / "lualatex"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OHMYJOB_LUALATEX", str(fake))
    status, _, _ = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and marker.exists()
    assert not any((env.parent / "build").iterdir())


def test_timeout_is_a_fail(env, monkeypatch, tmp_path):
    slow = tmp_path / "lualatex"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    monkeypatch.setenv("OHMYJOB_LUALATEX", str(slow))
    monkeypatch.setattr(rb, "LATEX_TIMEOUT", 0.3)
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "timed out" in detail
    assert not any((env.parent / "build").iterdir())


def test_flags_and_old_keys(env):
    src = rb.render(env / "Project-Coordinator.yaml", paper="letter")
    assert "[11pt, letterpaper]" in src
    assert "Thesis: forecast error" not in src and "Trail running" not in src                 # flags are no
    assert r"\cvsection{Projects}" not in src and r"\cvsection{Interests}" not in src          # never shown
    assert r"\cventry{Rhein-Spree University of Applied Sciences}" not in src                  # degree is the position slot
    assert r"\cventry{BSc Business Administration}{Rhein-Spree University of Applied Sciences}" in src


def test_accent_color_is_an_upstream_colour_only(env):
    path = role(env, accent_color="emerald", title="Other Title")
    src = rb.render(path, paper="a4")
    assert r"\position{Other Title}" in src and r"\colorlet{awesome}{awesome-emerald}" in src
    assert r"\colorlet{awesome}{awesome-red}" in rb.render(role(env, accent_color="awesome-red"), paper="a4")
    for odd in ("#aa3311", "AA3311", "teal"):
        src = rb.render(role(env, accent_color=odd), paper="a4")
        assert r"\colorlet{awesome}{awesome-red}" in src and "definecolor" not in src


@pytest.mark.parametrize("changes, message", [
    ({"title": ""}, "title is missing"),
    ({"experience": "x"}, "experience must be a list"),
    ({"experience": [{"company": "X"}]}, "experience[0].role is missing"),
    ({"skills": [{"items": "a"}]}, "skills[0].label is missing"),
    ({"email": ""}, "email is missing"),
    ({"phone": ""}, "phone is missing"),
    ({"name": {"first": "A"}}, "name.last is missing"),
    ({"name": "Alex"}, "name must be a mapping"),
    ({"interests": True}, "show_interests"),
    ({"education": "x"}, "education must be a list"),
])
def test_bad_role_yaml_is_a_fail(env, changes, message):
    status, pages, detail = rb.build(role(env, **changes), env / "out.pdf", max_pages=1, paper="a4")
    assert (status, pages) == ("FAIL", None) and message in detail
    assert not (env / "out.pdf").exists()


def test_fail_without_yaml_or_with_bad_paper(env):
    (env / "Operations-Analyst.yaml").unlink()
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert (status, detail[:16]) == ("FAIL", "cannot read resu")
    shutil.copy(FIXTURE / "Operations-Analyst.yaml", env / "Operations-Analyst.yaml")
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="legal")
    assert status == "FAIL" and "a4 or letter" in detail


def test_fail_without_lualatex(env, monkeypatch):
    monkeypatch.setenv("OHMYJOB_LUALATEX", "")
    monkeypatch.setenv("PATH", str(env))
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "lualatex not found" in detail


def test_templates_are_valid_input(env):
    src = rb.render(ROOT / "templates" / "Template.yaml", paper="a4")
    assert r"\name{Alex}{Example}" in src and r"\cvsection{Work Experience}" in src and "Curriculum" not in src
    src = rb.render(ROOT / "templates" / "Template-CV.yaml", paper="a4")
    assert r"\name{Alex}{Example}" in src and r"\cvsection{Program Committees}" in src and "Curriculum Vitae" in src


# --------------------------------------------------------------------------- real builds

@needs_tex
@pytest.mark.parametrize("persona", ["Operations-Analyst", "Project-Coordinator"])
@pytest.mark.parametrize("paper", ["a4", "letter"])
def test_fixture_persona_builds_on_one_page(env, persona, paper):
    out = env / f"{persona}-{paper}.pdf"
    assert rb.build(env / f"{persona}.yaml", out, max_pages=1, paper=paper) == ("OK", 1, str(out))
    assert out.read_bytes().startswith(b"%PDF")
    assert not any((env.parent / "build").iterdir())                                           # build dir cleaned up


@needs_tex
def test_page_limit_1_2_and_0(env):
    long = role(env, introduction="x " * 5, experience=[
        {"role": f"Role {i}", "company": f"Company {i}", "location": "City", "dates": "2020",
         "bullets": [f"Bullet number {j} for role {i} with some words to fill the line." for j in range(8)]}
        for i in range(7)])
    status, pages, detail = rb.build(long, env / "one.pdf", max_pages=1, paper="a4")
    assert (status, pages) == ("OVERFLOW", 2) and not (env / "one.pdf").exists()
    assert rb.build(long, env / "two.pdf", max_pages=2, paper="a4") == ("OK", 2, str(env / "two.pdf"))
    assert rb.build(long, env / "zero.pdf", max_pages=0, paper="a4")[0:2] == ("OK", 2)
    # the page count of the source applies to a CV too; without a source count nothing is checked
    cv = role(env, document="cv", experience=yaml.safe_load(long.read_text())["experience"])
    assert rb.build(cv, env / "cv.pdf", max_pages=1, paper="a4")[:2] == ("OVERFLOW", 2)
    assert rb.build(cv, env / "cv.pdf", paper="a4") == ("OK", 2, str(env / "cv.pdf"))
    assert not any((env.parent / "build").iterdir())


def text_of(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext is not installed")


@needs_tex
@needs_pdftotext
def test_special_characters_print_literally(env):
    text = r"\input{/etc/hostname} \write18{touch PWNED} {x} $y$ 100% a_b # ~ ^ & **bold** \\ ]"
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    base.update(github="a_b-user", linkedin="x_y", address="1 A&B Str. #5", homepage="example.com/~x_y#top?a=1&b=2",
                phone="**1** 23", date_of_birth="**1** May")
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    path = role(env, introduction=text, honors=[{"award": "Sk_ill", "event": text}])
    out = env / "special.pdf"
    assert rb.build(path, out, max_pages=1, paper="a4")[0] == "OK"
    got = text_of(out)
    assert r"\input{/etc/hostname}" in got and r"\write18{touch PWNED}" in got and "100% a_b # ~ ^ &" in got
    assert "A&B Str. #5" in got and "Sk_ill" in got
    assert not list(env.parent.rglob("PWNED"))


@needs_tex
@needs_pdftotext
def test_unicode_text_and_plain_hyphens(env):
    path = role(env, introduction="Caf\u00e9 M\u00fcller, \u00d1and\u00fa na\u00efve \u2014 r\u00e9sum\u00e9 \u00df \u00e5")
    out = env / "u.pdf"
    assert rb.build(path, out, max_pages=1, paper="a4")[0] == "OK"
    got = text_of(out)
    for word in ("Caf\u00e9", "M\u00fcller", "\u00d1and\u00fa", "na\u00efve", "r\u00e9sum\u00e9", "\u00df"):
        assert word in got
    assert "\u00ad" not in got and "Rhein-Spree" in got and "2020 - 2022" in got  # ATS text layer: ASCII hyphen


@needs_tex
def test_missing_glyph_is_a_fail(env):
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    base["name"] = {"first": "\u674e", "last": "Example"}
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base, allow_unicode=True))
    status, pages, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert (status, pages) == ("FAIL", None) and "Missing character" in detail and not (env / "o.pdf").exists()


def first_line_with(got: str, needle: str) -> str:
    return next(l for l in got.splitlines() if needle in l)


def fill(words: str, n: int) -> str:
    return (words * (n // len(words) + 1))[:n].rstrip()


@needs_tex
@needs_pdftotext
def test_budget_sized_bullet_and_skills_row_fit_one_line(env):
    # wide-ish text with capitals, digits and a bold term: BULLET_BUDGET characters must still be one line, and
    # SKILLS_BUDGET characters must be one row (one line) next to a 15 character label
    items = fill("Excel, SQL, Power BI, Wwmm, KPI design, ", rb.SKILLS_BUDGET)
    bullet = fill("Built a Weekly Report for 6 Depots, WMW 18% Q4 ", rb.BULLET_BUDGET)
    assert len(bullet) == rb.BULLET_BUDGET and len(items) <= rb.SKILLS_BUDGET
    path = role(env, document="cv", experience=[{"role": "R", "company": "C", "location": "L", "dates": "2020",
                                                 "bullets": [bullet]}],
                skills=[{"label": "Stakeholders Mgmt", "items": items}])
    for paper in ("a4", "letter"):
        out = env / f"b-{paper}.pdf"
        assert rb.build(path, out, max_pages=1, paper=paper)[0] == "OK"
        got = text_of(out)
        line = first_line_with(got, "Built a Weekly")
        assert line.strip().endswith(bullet.split()[-1]) and bullet.split()[1] in line
        row = first_line_with(got, "Stakeholders")
        assert row.strip().endswith(items.split()[-1].rstrip(","))


@needs_tex
def test_photo_builds(env, tmp_path):
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xc8\x40\x40" * 8 for _ in range(8))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    (env / "me.png").write_bytes(png)
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    base["photo"] = "me.png"
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    assert rb.render(env / "Operations-Analyst.yaml", paper="a4", photo="photo.png").count(r"\photo[circle,edge,left]{photo.png}") == 1
    assert rb.build(env / "Operations-Analyst.yaml", env / "p.pdf", max_pages=1, paper="a4")[0] == "OK"
    base["photo"] = "missing.png"
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "p2.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "photo" in detail


@needs_tex
def test_latex_error_is_a_fail_with_the_log_line(env, monkeypatch):
    monkeypatch.setattr(rb, "TEMPLATE_DIR", env)
    broken = env / "bad.j2"
    broken.write_text("\\documentclass{awesome-cv}\\begin{document}\\undefinedmacro\\end{document}")
    monkeypatch.setattr(rb, "TEMPLATE_NAME", "bad.j2")
    monkeypatch.setattr(rb, "_ENV", rb._ENV.overlay(loader=rb.jinja2.FileSystemLoader(str(env))))
    status, pages, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert (status, pages) == ("FAIL", None) and "Undefined control sequence" in detail
    assert not (env / "o.pdf").exists()


@needs_tex
def test_cli_prints_one_line(workspace):
    exe = [sys.executable, str(ROOT / "scripts" / "resume_build.py"), "build"]
    yaml_path = workspace / "Default_Resume_Files" / "Project-Coordinator.yaml"
    out = workspace / "Pending-Review" / "X.pdf"
    run = subprocess.run(exe + [str(yaml_path), "--out", str(out)], capture_output=True, text=True, cwd=workspace)
    assert run.stdout == f"OK {out}\n" and run.returncode == 0 and out.exists()
    assert "WARNING Project-Coordinator.yaml: skills is not shown in a resume" in run.stderr
    long = yaml.safe_load(yaml_path.read_text())
    long["experience"] = long["experience"] * 8
    yaml_path.write_text(yaml.safe_dump(long))
    run = subprocess.run(exe + [str(yaml_path)], capture_output=True, text=True, cwd=workspace)
    assert run.stdout == f"OK {yaml_path.with_suffix('.pdf')}\n" and run.returncode == 0  # no page limit of its own
    yaml_path.write_bytes(b"title: \xff\n")
    run = subprocess.run(exe + [str(yaml_path)], capture_output=True, text=True, cwd=workspace)
    assert run.stdout.startswith("FAIL ") and "Traceback" not in run.stderr and run.stdout.count("\n") == 1
    yaml_path.write_text("title: [")
    run = subprocess.run(exe + [str(yaml_path)], capture_output=True, text=True, cwd=workspace)
    assert run.stdout.startswith("FAIL ") and run.stdout.count("\n") == 1


# --------------------------------------------------------------------------- validation does not need TeX

def test_validation_fails_name_the_real_problem_without_lualatex(env, monkeypatch, tmp_path):
    """Where TeX is missing (CI), bad input must still be reported as bad input, not as "lualatex not found"."""
    monkeypatch.delenv("OHMYJOB_LUALATEX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty dir: no lualatex
    good = env / "Operations-Analyst.yaml"
    base = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    cases = [({"title": ""}, "title is missing"), ({"experience": "x"}, "experience must be a list"),
             ({"honors": [{"event": "E"}]}, "honors[0].award is missing")]
    for changes, message in cases:
        status, _, detail = rb.build(role(env, **changes), env / "o.pdf", max_pages=1, paper="a4")
        assert status == "FAIL" and message in detail and "lualatex" not in detail
    status, _, detail = rb.build(good, env / "o.pdf", max_pages=1, paper="legal")
    assert status == "FAIL" and "a4 or letter" in detail
    bad = env / "bad.yaml"
    bad.write_bytes(b"title: \xff\n")
    assert "UTF-8" in rb.build(bad, env / "o.pdf", max_pages=1, paper="a4")[2]
    base["homepage"] = "run:x"
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    assert "only http" in rb.build(good, env / "o.pdf", max_pages=1, paper="a4")[2]
    base["homepage"] = "example.com"
    (env / "Operations-Analyst.yaml").write_text(yaml.safe_dump(base))
    assert "lualatex not found" in rb.build(good, env / "o.pdf", max_pages=1, paper="a4")[2]


# --------------------------------------------------------------------------- the new format, build-all, TEXMF

def test_one_full_yaml_no_merge(env):
    """Everything comes from the one file; a missing header key is an error, not filled in from another file."""
    data = yaml.safe_load((env / "Operations-Analyst.yaml").read_text())
    assert "show_interests" in data and "education_details" in data and isinstance(data["interests"], str)
    src = rb.render(env / "Operations-Analyst.yaml", paper="a4")
    assert "Thesis: forecast error" in src
    data["education_details"] = "no"
    (env / "x.yaml").write_text(yaml.safe_dump(data))
    src = rb.render(env / "x.yaml", paper="a4")
    assert "Thesis: forecast error" not in src
    assert not hasattr(W, "BASE_YAML") and not hasattr(rb, "_merge")


def test_texmf_variables_follow_ohmyjob_data(env, monkeypatch, tmp_path):
    seen = tmp_path / "seen.txt"
    fake = tmp_path / "lualatex"
    fake.write_text(f'#!/bin/sh\necho "$TEXMFVAR|$TEXMFCACHE" > {seen}\nexit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv("OHMYJOB_LUALATEX", str(fake))
    data = tmp_path / "data"
    monkeypatch.setenv("OHMYJOB_DATA", str(data))
    assert rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")[0] == "FAIL"
    want = str(data / "texmf-var")
    assert seen.read_text().strip() == f"{want}|{want}" and (data / "texmf-var").is_dir()
    monkeypatch.delenv("OHMYJOB_DATA")
    monkeypatch.setenv("TEXMFVAR", "/keep/var")
    rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert seen.read_text().startswith("/keep/var|")


MARKER = "# OHMYJOB-TEMPLATE: copy this file to <Field>.yaml, replace the mock data, then delete this line"


@pytest.mark.parametrize("name", ["Template", "Template-CV"])
def test_template_files_have_the_marker_and_explain_every_key(name):
    lines = (ROOT / "templates" / f"{name}.yaml").read_text().splitlines()
    assert lines[0] == MARKER
    data = yaml.safe_load("\n".join(lines))
    assert data["document"] == ("cv" if name.endswith("CV") else "resume")
    text = "\n".join(lines)
    for key in data:  # every key is explained by a comment, and an optional one is shown commented out
        assert f"\n{key}:" in text and sum(l.startswith("#") for l in lines) > 20, key


@needs_tex
@pytest.mark.parametrize("paper", ["a4", "letter"])
def test_template_yaml_builds_on_one_page(env, paper):
    src = ROOT / "templates" / "Template.yaml"
    out = env / f"t-{paper}.pdf"
    assert rb.build(src, out, max_pages=1, paper=paper) == ("OK", 1, str(out))


@needs_tex
@pytest.mark.parametrize("paper", ["a4", "letter"])
def test_template_cv_builds_on_each_paper(env, paper):
    src = ROOT / "templates" / "Template-CV.yaml"
    out = env / f"cv-{paper}.pdf"
    status, pages, _ = rb.build(src, out, paper=paper)
    assert status == "OK" and pages >= 1 and out.exists()


def cli(workspace, *args):
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "resume_build.py"), *args],
                         capture_output=True, text=True, cwd=workspace)
    return run.returncode, run.stdout


@needs_tex
def test_build_all_lines_and_exit_codes(workspace):
    d = workspace / "Default_Resume_Files"
    # all OK: the two fixture resumes -> exit 0
    code, out = cli(workspace, "build-all")
    assert out.splitlines() == [
        "Operations-Analyst.yaml: OK", "WARNING Operations-Analyst.yaml: skills is not shown in a resume",
        "WARNING Operations-Analyst.yaml: projects is no longer shown in any document",
        "WARNING Operations-Analyst.yaml: interests is no longer shown in any document",
        "Project-Coordinator.yaml: OK", "WARNING Project-Coordinator.yaml: skills is not shown in a resume",
        "SUMMARY 2 OK, 0 OVERFLOW, 0 FAIL"]
    assert code == 0 and (d / "Operations-Analyst.pdf").exists() and (d / "Project-Coordinator.pdf").exists()
    # a FAIL -> exit 1, lines sorted by file name; Template*.yaml is never built; no page limit
    long = yaml.safe_load((d / "Project-Coordinator.yaml").read_text())
    long["experience"] = long["experience"] * 8
    (d / "Project-Coordinator.yaml").write_text(yaml.safe_dump(long))
    (d / "Broken.yaml").write_text("title: [")
    shutil.copy(ROOT / "templates" / "Template.yaml", d / "Template.yaml")
    shutil.copy(ROOT / "templates" / "Template-CV.yaml", d / "Template-CV.yaml")
    code, out = cli(workspace, "build-all")
    lines = [l for l in out.splitlines() if not l.startswith("WARNING")]
    assert lines[0].startswith("Broken.yaml: FAIL ") and lines[1] == "Operations-Analyst.yaml: OK"
    assert lines[2] == "Project-Coordinator.yaml: OK"
    assert lines[3] == "SUMMARY 2 OK, 0 OVERFLOW, 1 FAIL" and len(lines) == 4 and code == 1
    assert not (d / "Template.pdf").exists() and not (d / "Template-CV.pdf").exists() and not (d / "Broken.pdf").exists()
    # one file by name (with or without .yaml); a Template or an unknown name is refused
    (d / "Operations-Analyst.pdf").unlink()
    code, out = cli(workspace, "build-all", "Operations-Analyst")
    assert code == 0 and out.splitlines()[0] == "Operations-Analyst.yaml: OK" and out.splitlines()[-1].startswith("SUMMARY 1 OK")
    assert (d / "Operations-Analyst.pdf").exists()
    for name in ("Template.yaml", "Template-CV", "Nope.yaml"):
        code, out = cli(workspace, "build-all", name)
        assert code == 1 and out.startswith("FATAL: no resume yaml named ") and out.count("\n") == 1
    # --dir
    other = workspace / "elsewhere"
    other.mkdir()
    shutil.copy(FIXTURE / "Operations-Analyst.yaml", other / "A.yaml")
    code, out = cli(workspace, "build-all", "--dir", str(other))
    assert code == 0 and out.splitlines()[0] == "A.yaml: OK" and out.splitlines()[-1] == "SUMMARY 1 OK, 0 OVERFLOW, 0 FAIL"


def test_paper_is_a4_unless_the_yaml_sets_it(env):
    assert r"\documentclass[11pt, a4paper]{awesome-cv}" in rb.render(env / "Operations-Analyst.yaml")
    assert "[11pt, letterpaper]{awesome-cv}" in rb.render(role(env, paper="Letter"))
    status, _, detail = rb.build(role(env, paper="legal"), env / "o.pdf")
    assert status == "FAIL" and "paper must be a4 or letter" in detail


def test_build_all_empty_dir(workspace, tmp_path):
    code, out = cli(workspace, "build-all", "--dir", str(tmp_path))
    assert code == 1 and out.startswith("FATAL: no resume yaml files") and out.count("\n") == 1
