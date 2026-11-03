"""resume_build: escaping, merge, template mapping, and real LuaLaTeX builds (page limit, paper sizes, fixture persona)."""
import os
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
    """A tmp dir holding a copy of the fixture yamls; W.BASE_YAML and W.BUILD_DIR point into it."""
    d = tmp_path / "Default_Resume_Files"
    shutil.copytree(FIXTURE, d)
    monkeypatch.setattr(W, "BASE_YAML", d / "base.yaml")
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
    src = rb.render(role(env, introduction=evil, skills=[{"label": r"\x", "items": evil}]), paper="a4")
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

def test_merge_and_mapping(env):
    src = rb.render(env / "Operations-Analyst.yaml", paper="a4")
    assert r"\documentclass[11pt,a4paper]{awesome-cv}" in src
    assert r"\name{Alex}{Example}" in src and r"\position{Operations Analyst}" in src          # base + role
    assert r"\email{alex.example@example.com}" in src and r"\cvsection{Profile}" in src        # base heading override
    assert r"\cvsection{Core Skills}" in src and r"\cvsection{Experience}" in src
    # grouped employer: first role carries employer + location, the later role has an empty title and location
    assert r"\cventry{Senior Operations Analyst}{Harborline Freight Group}{Hamburg, Germany}{2022 - Present}" in src
    assert r"\cventry{Operations Analyst}{}{}{2020 - 2022}" in src
    assert r"\cventry{Supply Planning Analyst}{Brightwater Foods}{Berlin, Germany}{2018 - 2020}" in src   # plain entry
    assert r"\textbf{dock utilisation}" in src
    assert r"\cventry{Dock scheduling model}{}{}{\href{https://example.com/alex/dock-scheduling}" in src
    assert r"\cvskill{Analysis}{Excel, SQL, Power BI, forecasting, KPI design}" in src
    assert "Thesis: forecast error" in src and "Trail running" in src                          # flags are yes


def test_header_urls_and_plain_fields(env):
    base = yaml.safe_load((env / "base.yaml").read_text())
    base.update(homepage="example.com/~me_x", github="a_b", linkedin="**x**", phone="**1**", email="a_b@example.com",
                date_of_birth="**d**", sections={"summary": "**S**", "skills": "A_B"})
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    src = rb.render(env / "Operations-Analyst.yaml", paper="a4")
    assert r"\homepage{https://example.com/\string~me\_x}" in src and r"\github{a\_b}" in src
    assert r"\linkedin{**x**}" in src and r"\mobile{**1**}" in src and r"\email{a\_b@example.com}" in src
    assert r"\cvsection{**S**}" in src and r"\cvsection{A\_B}" in src and "textbf{x}" not in src


@pytest.mark.parametrize("field, value", [("homepage", "run:x"), ("homepage", "file:///etc/passwd")])
def test_bad_homepage_scheme_is_a_fail(env, field, value):
    base = yaml.safe_load((env / "base.yaml").read_text())
    base[field] = value
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "homepage" in detail and "only http" in detail


def test_bad_repo_scheme_is_a_fail(env):
    path = role(env, projects=[{"title": "P", "repo": "run://x", "bullets": []}])
    status, _, detail = rb.build(path, env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "projects[0].repo" in detail


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


def test_flags_and_empty_projects(env):
    src = rb.render(env / "Project-Coordinator.yaml", paper="letter")
    assert "letterpaper" in src
    assert "Thesis: forecast error" not in src and "Trail running" not in src                 # flags are no
    assert r"\cvsection{Projects}" not in src                                                   # projects: []
    assert r"\cventry{Rhein-Spree University of Applied Sciences}" not in src                  # degree is the position slot
    assert r"\cventry{BSc Business Administration}{Rhein-Spree University of Applied Sciences}" in src


def test_role_overrides_base_and_sections_merge(env):
    base = yaml.safe_load((env / "base.yaml").read_text())
    base["accent_color"] = "#aa3311"
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    src = rb.render(role(env, sections={"skills": "Tools"}, title="Other Title"), paper="a4")
    assert r"\position{Other Title}" in src and r"\cvsection{Tools}" in src and r"\cvsection{Profile}" in src
    assert r"\definecolor{acvaccent}{HTML}{AA3311}" in src


@pytest.mark.parametrize("changes, message", [
    ({"title": ""}, "title is missing"),
    ({"experience": "x"}, "experience must be a list"),
    ({"experience": [{"company": "X"}]}, "experience[0].role is missing"),
    ({"skills": [{"items": "a"}]}, "skills[0].label is missing"),
])
def test_bad_role_yaml_is_a_fail(env, changes, message):
    status, pages, detail = rb.build(role(env, **changes), env / "out.pdf", max_pages=1, paper="a4")
    assert (status, pages) == ("FAIL", None) and message in detail
    assert not (env / "out.pdf").exists()


def test_fail_without_base_or_with_bad_paper(env):
    (env / "base.yaml").unlink()
    assert rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")[0:2] == ("FAIL", None)
    shutil.copy(FIXTURE / "base.yaml", env / "base.yaml")
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="legal")
    assert status == "FAIL" and "a4 or letter" in detail


def test_fail_without_lualatex(env, monkeypatch):
    monkeypatch.setenv("OHMYJOB_LUALATEX", "")
    monkeypatch.setenv("PATH", str(env))
    status, _, detail = rb.build(env / "Operations-Analyst.yaml", env / "o.pdf", max_pages=1, paper="a4")
    assert status == "FAIL" and "lualatex not found" in detail


def test_templates_are_valid_input(env):
    shutil.copy(ROOT / "templates" / "base.yaml", env / "base.yaml")
    src = rb.render(ROOT / "templates" / "role.yaml", paper="a4")
    assert r"\name{Firstname}{Lastname}" in src and r"\cvsection{Experience}" in src


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
    assert not any((env.parent / "build").iterdir())


def text_of(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext is not installed")


@needs_tex
@needs_pdftotext
def test_special_characters_print_literally(env):
    text = r"\input{/etc/hostname} \write18{touch PWNED} {x} $y$ 100% a_b # ~ ^ & **bold** \\ ]"
    base = yaml.safe_load((env / "base.yaml").read_text())
    base.update(github="a_b-user", linkedin="x_y", address="1 A&B Str. #5", homepage="example.com/~x_y#top?a=1&b=2",
                phone="**1** 23", date_of_birth="**1** May")
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    path = role(env, introduction=text, skills=[{"label": "Sk_ill", "items": text}])
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
    base = yaml.safe_load((env / "base.yaml").read_text())
    base["name"] = {"first": "\u674e", "last": "Example"}
    (env / "base.yaml").write_text(yaml.safe_dump(base, allow_unicode=True))
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
    path = role(env, experience=[{"role": "R", "company": "C", "location": "L", "dates": "2020", "bullets": [bullet]}],
                skills=[{"label": "Stakeholders Mgmt", "items": items}])
    for paper in ("a4", "letter"):
        out = env / f"b-{paper}.pdf"
        assert rb.build(path, out, max_pages=1, paper=paper)[0] == "OK"
        got = text_of(out)
        line = first_line_with(got, "\u2022")
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
    base = yaml.safe_load((env / "base.yaml").read_text())
    base["photo"] = "me.png"
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    assert rb.render(env / "Operations-Analyst.yaml", paper="a4", photo="photo.png").count(r"\photo[circle,edge,left]{photo.png}") == 1
    assert rb.build(env / "Operations-Analyst.yaml", env / "p.pdf", max_pages=1, paper="a4")[0] == "OK"
    base["photo"] = "missing.png"
    (env / "base.yaml").write_text(yaml.safe_dump(base))
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
    (workspace / "ohmyjob.json").write_text('{"pipeline": {"resume_max_pages": 1, "resume_paper": "letter"}}')
    long = yaml.safe_load(yaml_path.read_text())
    long["experience"] = long["experience"] * 8
    yaml_path.write_text(yaml.safe_dump(long))
    run = subprocess.run(exe + [str(yaml_path)], capture_output=True, text=True, cwd=workspace)
    assert run.stdout == "OVERFLOW 2 pages\n" and run.returncode == 1
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
    base = yaml.safe_load((env / "base.yaml").read_text())
    cases = [({"title": ""}, "title is missing"), ({"experience": "x"}, "experience must be a list"),
             ({"projects": [{"title": "P", "repo": "run://x", "bullets": []}]}, "projects[0].repo")]
    for changes, message in cases:
        status, _, detail = rb.build(role(env, **changes), env / "o.pdf", max_pages=1, paper="a4")
        assert status == "FAIL" and message in detail and "lualatex" not in detail
    status, _, detail = rb.build(good, env / "o.pdf", max_pages=1, paper="legal")
    assert status == "FAIL" and "a4 or letter" in detail
    bad = env / "bad.yaml"
    bad.write_bytes(b"title: \xff\n")
    assert "UTF-8" in rb.build(bad, env / "o.pdf", max_pages=1, paper="a4")[2]
    base["homepage"] = "run:x"
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    assert "only http" in rb.build(good, env / "o.pdf", max_pages=1, paper="a4")[2]
    (env / "base.yaml").unlink()
    assert "base.yaml not found" in rb.build(good, env / "o.pdf", max_pages=1, paper="a4")[2]
    base["homepage"] = "example.com"
    (env / "base.yaml").write_text(yaml.safe_dump(base))
    assert "lualatex not found" in rb.build(good, env / "o.pdf", max_pages=1, paper="a4")[2]
