"""Tests for pj.py and pj_hooks.py on the fictional workspace of tests/fixtures. No LaTeX runs.

Scripts read their paths at import, so every command runs in a subprocess that inherits OHMYJOB_WORKSPACE from the
`workspace` fixture. The workers are simulated by calling the hooks with the payloads Claude Code sends. The compile is
a fake (PJ_COMPILE_CMD), or a fake resume_build module put first on the path (the real one needs lualatex).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

FAKE_COMPILE = '''import os, sys
from pathlib import Path
pdf, yaml_file = sys.argv[1], sys.argv[2]
pages = int(os.environ.get("FAKE_PAGES", "1"))
Path(pdf).write_bytes(b"%PDF-1.4 fake")
print(f"Done: {pdf}, the document is {pages} pages")
'''

# Stands in for WS-A's resume_build: the same names, no LaTeX.
FAKE_RESUME_BUILD = '''import json, os, re
from pathlib import Path
BULLET_BUDGET = 125
SKILLS_BUDGET = 120
def rendered_len(text):
    return len(" ".join(re.sub(r"\\*\\*", "", str(text)).replace("\\\\", "").split()))
def build(role_yaml, out_pdf, *, max_pages=None, paper=None):
    Path(os.environ["FAKE_BUILD_LOG"]).write_text(json.dumps([str(role_yaml), str(out_pdf), max_pages, paper]))
    if os.environ.get("FAKE_BUILD_RAISE"):
        import subprocess
        raise subprocess.TimeoutExpired("lualatex", 180)
    pages = int(os.environ.get("FAKE_PAGES", "1"))
    if max_pages and pages > max_pages:
        return "OVERFLOW", pages, ""
    Path(out_pdf).write_bytes(b"%PDF fake")
    return "OK", pages, ""
'''

OPS, COORD = "Operations-Analyst.pdf", "Project-Coordinator.pdf"
OP_KEY, QUILLON_KEY = "4399000001", "969845171025937"


def job_md(url: str, company: str, title: str, location: str = "Berlin, Germany", score: str = "", resume: str = "",
           bold: bool = False) -> str:
    def line(k: str, v: str) -> str:
        return f"- **{k}:** {v}" if bold else f"- {k}: {v}"

    return "\n".join([
        "# Job Discovery & Scoring", "", "## 1. Overview",
        line("Job Title", title), line("Company", company), line("Job URL", url), line("Source", "linkedin"),
        line("Date Found", "2026-10-20 09:00"), line("Location", location), line("Match Score", score),
        line("Matched Resume", resume), "", "## 2. Job Description", "",
        "We need someone who builds weekly reports in SQL and Power BI.", "",
        "## 3. Match Reasoning and Discussions", "", "### Why Matched?", "", "### Gaps?", ""])


def make_pdf(text: str, pages: int = 1) -> bytes:
    """A PDF of `pages` pages whose text pypdf can read back (no LaTeX)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    kids = " ".join(f"{5 + i} 0 R" for i in range(pages))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 4 0 R >> >> >>"] * pages
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


class Env:
    def __init__(self, root: Path, tmp: Path):
        self.root, self.tmp = root, tmp
        for name in (OPS, COORD):
            (root / "Default_Resume_Files" / name).write_bytes(make_pdf("Resume"))
        shutil.rmtree(root / "Applications")
        (root / "Applications").mkdir()
        import tracker as T

        T.create(root / "Job_Tracker.xlsx")
        fake = tmp / "fake_compile.py"
        fake.write_text(FAKE_COMPILE, encoding="utf-8")
        self.fakedir = tmp / "fakebuild"
        self.fakedir.mkdir()
        (self.fakedir / "resume_build.py").write_text(FAKE_RESUME_BUILD, encoding="utf-8")
        self.env = {**os.environ, "OHMYJOB_WORKSPACE": str(root), "PYTHONPATH": f"{self.fakedir}{os.pathsep}{SCRIPTS}",
                    "PJ_COMPILE_CMD": f"{sys.executable} {fake}", "FAKE_BUILD_LOG": str(tmp / "build.log")}
        self.env.pop("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", None)
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def folder(self, job_id: str, company: str, title: str, url: str | None = None, **kw) -> Path:
        url = url or f"https://www.linkedin.com/jobs/view/{job_id}"
        path = self.root / "Applications" / f"{job_id}_{company.replace(' ', '-')}_{title.replace(' ', '-')}"
        path.mkdir()
        (path / "job.md").write_text(job_md(url, company, title, **kw), encoding="utf-8")
        return path

    def row(self, job_id: str, company: str, title: str, status: str, url: str | None = None) -> None:
        import tracker as T

        t = T.Tracker(self.root / "Job_Tracker.xlsx")
        t.add_row({"Job URL": url or f"https://www.linkedin.com/jobs/view/{job_id}", "Company": company,
                   "Job Title": title, "Status": status})
        t.save()

    def rows(self) -> dict:
        import tracker as T

        return {r["_key"]: r for r in T.Tracker(self.root / "Job_Tracker.xlsx").rows()}

    def run(self, code: str, *args: str, env: dict | None = None, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-c", code, *args], input=stdin, capture_output=True, text=True,
                              env=env or self.env, cwd=self.root)

    def pj(self, *args: str, env: dict | None = None) -> str:
        proc = self.run("import sys, pj; pj.main(sys.argv[1:])", *args, env=env)
        assert proc.stderr == "", proc.stderr
        return proc.stdout.strip()

    def hook(self, command: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
        payload = {"session_id": "s1", "cwd": str(self.root), **payload}
        return subprocess.run([sys.executable, str(SCRIPTS / "pj_hooks.py"), command], input=json.dumps(payload),
                              capture_output=True, text=True, env=env or self.env, cwd=self.root)

    def guard(self, agent: str, agent_id: str, tool: str, ti: dict) -> subprocess.CompletedProcess:
        return self.hook("guard", {"agent_type": agent, "agent_id": agent_id, "tool_name": tool, "tool_input": ti})

    def bind(self, agent: str, agent_id: str, brief: str) -> None:
        proc = self.guard(agent, agent_id, "Read", {"file_path": brief})
        assert proc.returncode == 0, proc.stderr

    def report(self, agent: str, agent_id: str, obj: dict, how: str = "handback") -> subprocess.CompletedProcess:
        if how == "handback":
            return self.hook("handback", {"agent_type": agent, "agent_id": agent_id, "tool_name": "SubagentHandback",
                                          "tool_input": {"message": json.dumps(obj)}})
        text = "Here is my report:\n" + json.dumps(obj) if obj else None
        return self.hook("capture", {"agent_type": agent, "agent_id": agent_id, "hook_event_name": "SubagentStop",
                                     "last_assistant_message": text})

    def workers(self, out: str) -> list[tuple[str, str]]:
        return [re.match(r"^job=(\d+) brief=(.+)$", w["prompt"]).groups() for w in json.loads(out)["workers"]]

    def run_dir(self) -> Path:
        base = self.root / ".ohmyjob" / "run" / "prep"
        return base / (base / "CURRENT").read_text().strip()

    def edit_state(self, fn) -> None:
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        fn(state)
        path.write_text(json.dumps(state))


@pytest.fixture
def pe(workspace, tmp_path):
    return Env(workspace, tmp_path)


SCREEN = "ohmyjob:screen-worker"
BUILDER = "ohmyjob:resume-worker"
MASTER = "ohmyjob:prep-master"
LOW = {"junk": False, "agency": False, "score": 4, "dealbreaker": None, "matched_resume": None, "why": [], "gaps": [], "note": "Low fit"}
MATCH = {"junk": False, "agency": False, "dealbreaker": None, "matched_resume": OPS, "why": ["SQL and Power BI reporting"],
         "gaps": ["- Fewer years than asked"], "note": ""}
ALLOW = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


# --------------------------------------------------------------------------- pairing and the queue


def test_job_from_another_board_pairs_and_gets_its_build_command(pe):
    url = "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268"
    from job_key import job_key

    key = job_key(url)
    assert key == QUILLON_KEY
    pe.folder(key, "Quillon Systems", "Project Coordinator", url=url, score="7", resume=COORD)
    pe.row(key, "Quillon Systems", "Project Coordinator", "Matched", url=url)
    start = json.loads(pe.pj("start"))
    assert (start["screen"], start["build"]) == (0, 1)
    assert pe.rows()[key]["Status"] == "Matched"  # paired, not deleted or re-created
    jobs = dict(pe.workers(pe.pj("assign-build")))
    assert list(jobs) == [key]
    pe.bind(BUILDER, "r1", jobs[key])
    build = lambda k: pe.guard(BUILDER, "r1", "Bash", {"command": f"ohmyjob prep build {k}"})  # noqa: E731
    ok = build(key)
    assert ok.returncode == 0 and json.loads(ok.stdout) == ALLOW
    assert build("1005").returncode == 2


def test_bold_job_md_and_missing_row_are_handled(pe):
    pe.folder("1001", "Acme", "Operations Analyst", bold=True)  # no tracker row: created at Found, with its Source
    start = json.loads(pe.pj("start"))
    assert start["screen"] == 1
    assert pe.rows()["1001"]["Status"] == "Found" and pe.rows()["1001"]["Source"] == "linkedin"
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 8, **MATCH}).returncode == 0
    assert pe.pj("record").startswith("OK recorded")
    md = (pe.root / "Applications" / "1001_Acme_Operations-Analyst" / "job.md").read_text()
    assert "- **Match Score:** 8\n- **Matched Resume:** Operations-Analyst.pdf\n" in md


def test_screen_brief_puts_the_fit_preferences_first(pe):
    pe.folder("1001", "Acme", "Operations Analyst")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    text = Path(brief).read_text(encoding="utf-8")
    prefs = (pe.root / "Profile.md").read_text(encoding="utf-8").split("# Fit Preferences", 1)[1].split("# Resume", 1)[0]
    head = text.split("## Profile.md (full text)", 1)[0]
    assert "CANDIDATE FIT PREFERENCES" in head and prefs.strip() in head
    assert "Seniority / experience level" in head and "Dealbreakers" in head
    assert text.index("CANDIDATE FIT PREFERENCES") < text.index("## Job: Overview")


def test_next_fills_a_free_slot_at_once(pe):
    for job_id, company in [("1001", "Acme"), ("1002", "Beta"), ("1003", "Gamma")]:
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    env = {**pe.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "3"}  # 2 slots: the master takes the third
    assert json.loads(pe.pj("start"))["screen"] == 3
    first = dict(pe.workers(pe.pj("next", env=env)))
    assert len(first) == 2
    done = sorted(first)[0]
    pe.bind(SCREEN, f"s{done}", first[done])
    assert pe.report(SCREEN, f"s{done}", {"job": done, **LOW}).returncode == 0
    second = dict(pe.workers(pe.pj("next", env=env)))  # one finished: one new worker at once
    assert len(second) == 1 and list(second)[0] not in first
    assert pe.rows()[done]["Status"] == "Skipped-LowFit"
    for job, brief in [*first.items(), *second.items()]:
        if job != done:
            pe.bind(SCREEN, f"s{job}", brief)
            assert pe.report(SCREEN, f"s{job}", {"job": job, **LOW}, how="capture").returncode == 0
    assert json.loads(pe.pj("next", env=env)) == {"done": True}


def test_next_prints_a_dropped_worker_again(pe):
    for job_id, company in [("1001", "Acme"), ("1002", "Beta")]:
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    pe.pj("start")
    workers = json.loads(pe.pj("next"))["workers"]
    assert len(workers) == 2 and workers[0]["subagent_type"] == SCREEN
    started = pe.guard(MASTER, "m1", "Agent", {**workers[1]})  # the master starts only the second
    assert started.returncode == 0, started.stderr
    pe.edit_state(lambda st: [j.update(assigned_at=j["assigned_at"] - 120) for j in st["jobs"].values()])
    assert json.loads(pe.pj("next"))["workers"] == [workers[0]]


def test_master_may_start_only_its_workers(pe):
    spawn = lambda t: pe.guard(MASTER, "m", "Agent", {"subagent_type": t, "prompt": "job=1 brief=x"}).returncode  # noqa: E731
    assert spawn(SCREEN) == 0 and spawn(BUILDER) == 0
    assert spawn("ohmyjob:application-worker") == 2
    assert spawn("general-purpose") == 2
    assert spawn("Explore") == 2


def test_master_commands_are_anchored(pe):
    guard = lambda cmd: pe.guard(MASTER, "m", "Bash", {"command": cmd})  # noqa: E731
    for cmd in ("ohmyjob prep record", "ohmyjob prep next", "ohmyjob prep assign-build --max 10"):
        proc = guard(cmd)
        assert proc.returncode == 0 and json.loads(proc.stdout) == ALLOW
    assert guard("ohmyjob prep record && cat Profile.md").returncode == 2
    assert guard("ohmyjob prep start\nrm -rf Applications").returncode == 2
    assert guard("ohmyjob prep build 5").returncode == 2


def test_worker_time_limit_counts_from_the_first_guarded_call(pe):
    import aa_lib

    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("next"))
    pe.bind(SCREEN, "s1", brief)
    state = json.loads((pe.run_dir() / "state.json").read_text())
    assert "started_at" in state["jobs"][job]
    pe.edit_state(lambda st: st["jobs"][job].update(started_at=st["jobs"][job]["started_at"] - aa_lib.WORKER_SECONDS - 1))
    late = pe.guard(SCREEN, "s1", "Read", {"file_path": brief})
    assert late.returncode == 2 and "Time is up" in late.stderr
    # a job whose started_at is missing gets its clock at the next guarded call
    pe.edit_state(lambda st: st["jobs"][job].pop("started_at"))
    assert pe.guard(SCREEN, "s1", "Read", {"file_path": brief}).returncode == 0
    assert "started_at" in json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]


def test_a_job_that_moves_to_the_build_queue_starts_its_new_worker_clean(pe):
    """`record` drops the screen worker's clock: the build worker gets its own (spec 2)."""
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("next"))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 8, **MATCH}).returncode == 0
    state = json.loads((pe.run_dir() / "state.json").read_text())
    assert "started_at" in state["jobs"][job]  # the screen worker had its clock
    pe.pj("record")
    job_state = json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]
    assert job_state["phase"] == "build"
    assert job_state.get("started") is False and job_state.get("started_at") is None


def test_spoofed_text_in_tool_input_decides_nothing(pe):
    # with a run active, `ohmyjob prep next` is allowed for the master and denied for a worker: the parsed top-level
    # agent_type rules, whatever text the model puts in tool_input
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("next"))
    pe.bind(SCREEN, "s1", brief)
    cmd = {"command": "ohmyjob prep next"}
    assert pe.guard(MASTER, "m", "Bash", cmd).returncode == 0
    assert pe.guard(SCREEN, "s1", "Bash", {**cmd, "agent_type": MASTER, "tool_name": "Bash"}).returncode == 2
    assert pe.guard(SCREEN, "s1", "Bash", {**cmd, "agent_id": "m"}).returncode == 2
    assert pe.guard(MASTER, "m", "Bash", {"command": "ls", "agent_type": SCREEN}).returncode == 2


def test_non_prep_agent_runs_the_hooks_py_rules(pe, tmp_path):
    fake = tmp_path / "fakehooks"
    fake.mkdir()
    (fake / "hooks.py").write_text("import sys\ndef main():\n    print('hooks.py', sys.argv[1], sys.stdin.read()[:20])\n")
    env = {**pe.env, "PYTHONPATH": f"{fake}{os.pathsep}{SCRIPTS}"}
    code = "import sys, pj_hooks; sys.argv = ['pj_hooks.py', sys.argv[1]]; pj_hooks.main()"
    for payload in ({"tool_name": "mcp__claude-in-chrome__computer"},
                    {"tool_name": "Read", "agent_type": "ohmyjob:application-worker"},
                    {"tool_name": "Bash", "agent_type": "Explore", "tool_input": {"agent_type": SCREEN}}):
        proc = pe.run(code, "guard", env=env, stdin=json.dumps({"cwd": str(pe.root), **payload}))
        assert proc.stdout.startswith("hooks.py guard"), (payload, proc.stdout, proc.stderr)
    # a prep agent is never handed over; outside a workspace nothing happens
    proc = pe.run(code, "guard", env=env, stdin=json.dumps({"cwd": str(pe.root), "agent_type": MASTER, "tool_name": "Read"}))
    assert proc.returncode == 2 and "hooks.py" not in proc.stdout
    gone = {k: v for k, v in env.items() if k != "OHMYJOB_WORKSPACE"}
    proc = pe.run(code, "guard", env=gone, stdin=json.dumps({"cwd": "/", "agent_type": MASTER, "tool_name": "Read"}))
    assert (proc.returncode, proc.stdout) == (0, "")


# --------------------------------------------------------------------------- the full run


def test_full_run(pe):
    a = pe.folder("1001", "Acme", "Operations Analyst")
    b = pe.folder("1002", "Beta", "Data Analyst")
    c = pe.folder("1003", "Gamma Staffing", "Planner")
    e = pe.folder("1005", "Delta", "Chip Designer")  # no tracker row
    g1 = pe.folder("1007", "Acme", "Backend Planner", location="Not specified")
    g2 = pe.folder("1008", "Acme", "Backend Planner")  # kept: it states a location
    h = pe.folder("1009", "Eta", "Analyst")
    (h / "Alex_Eta_Analyst.pdf").write_bytes(b"%PDF")  # already built: never touched
    i = pe.folder("1010", "Iota", "Analyst", score="7", resume="Nope.pdf")
    for job_id, company, title, status in [("1001", "Acme", "Operations Analyst", "Found"), ("1002", "Beta", "Data Analyst", "Found"),
                                           ("1003", "Gamma Staffing", "Planner", "Found"), ("1004", "Ghost", "Analyst", "Found"),
                                           ("1007", "Acme", "Backend Planner", "Found"), ("1008", "Acme", "Backend Planner", "Found"),
                                           ("1009", "Eta", "Analyst", "Found"), ("1010", "Iota", "Analyst", "Matched")]:
        pe.row(job_id, company, title, status)

    start = json.loads(pe.pj("start"))
    assert (start["screen"], start["build"]) == (5, 1)  # 1001 1002 1003 1005 1008 / 1010
    rows = pe.rows()
    assert rows["1004"]["Status"] == "Deleted" and rows["1005"]["Status"] == "Found" and rows["1007"]["Status"] == "Deleted"
    assert "duplicate of job 1008" in rows["1007"]["Notes"]
    assert not g1.exists() and g2.exists() and h.exists() and rows["1009"]["Status"] == "Found"

    # screen wave: s1001 reports by handback, s1002 and the rest by capture (final text)
    jobs = dict(pe.workers(pe.pj("assign-screen")))
    assert sorted(jobs) == ["1001", "1002", "1003", "1005", "1008"]
    brief = Path(jobs["1001"]).read_text()
    assert OPS in brief and "bullets:" not in brief and "base.yaml" not in brief
    for job in jobs:
        pe.bind(SCREEN, f"s{job}", jobs[job])
    assert pe.guard(SCREEN, "s1001", "Read", {"file_path": jobs["1002"]}).returncode == 2
    assert pe.report(SCREEN, "s1001", {"job": "1001"}).returncode == 2  # bad report: retry
    ok = pe.report(SCREEN, "s1001", {"job": "1001", "score": 8, **MATCH})
    assert ok.returncode == 0, ok.stderr
    assert "saved by the hook" in ok.stdout and json.loads(ok.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    bad = pe.report(SCREEN, "s1002", {"job": "1002"}, how="capture")  # capture asks for a fix too
    assert json.loads(bad.stdout)["decision"] == "block"
    no_reason = pe.report(SCREEN, "s1002", {"job": "1002", **{**LOW, "junk": True, "score": None, "note": ""}}, how="capture")
    assert json.loads(no_reason.stdout)["decision"] == "block"  # a junk verdict needs its reason
    assert pe.report(SCREEN, "s1002", {"job": "1002", **{**LOW, "junk": True, "score": None, "note": "Asks for a training fee"}},
                     how="capture").returncode == 0
    assert pe.report(SCREEN, "s1003", {"job": "1003", **{**LOW, "agency": True, "score": None, "note": "Skipped, recruiter"}},
                     how="capture").returncode == 0
    assert pe.report(SCREEN, "s1005", {"job": "1005", "score": 6, **MATCH}).returncode == 0
    nothing = pe.report(SCREEN, "s1008", None, how="capture")  # a null last_assistant_message
    assert (nothing.returncode, nothing.stdout) == (0, "")
    assert pe.pj("record").startswith("OK recorded")
    rows = pe.rows()
    assert (rows["1001"]["Status"], rows["1001"]["Match Score"]) == ("Matched", 8)
    assert rows["1002"]["Status"] == "Skipped-LowFit" and rows["1002"]["Match Score"] in (None, "")
    assert rows["1002"]["Notes"] == "Junk posting: Asks for a training fee"
    assert rows["1003"]["Status"] == "Skipped-LowFit" and rows["1003"]["Match Score"] in (None, "")
    assert rows["1008"]["Status"] == "Found"
    assert not (b.exists() or c.exists())
    md = (a / "job.md").read_text()
    assert "- Match Score: 8\n- Matched Resume: Operations-Analyst.pdf\n" in md
    assert "### Why Matched?\n\n- SQL and Power BI reporting\n\n### Gaps?" in md
    assert md.rstrip().endswith("- Fewer years than asked")
    assert json.loads(pe.pj("assign-screen"))["workers"] == []

    # build wave: 1010 (earlier Matched, its resume yaml is missing: a blocker), 1001 and 1005 (screened in now)
    out = json.loads(pe.pj("assign-build"))
    jobs = {re.match(r"job=(\d+)", w["prompt"]).group(1): re.search(r"brief=(.+)$", w["prompt"]).group(1) for w in out["workers"]}
    assert sorted(jobs) == ["1001", "1005"] and len(out["failed_without_worker"]) == 1
    assert out["subagent_type"] == BUILDER
    yaml_a = a / "Alex_Acme_Operations-Analyst.yaml"
    assert yaml_a.read_text() == (pe.root / "Default_Resume_Files" / "Operations-Analyst.yaml").read_text()
    assert "exactly 1 page" in Path(jobs["1001"]).read_text()
    for job in jobs:
        pe.bind(BUILDER, f"r{job}", jobs[job])
    g = lambda agent_id, tool, ti: pe.guard(BUILDER, agent_id, tool, ti).returncode  # noqa: E731
    assert g("r1001", "Edit", {"file_path": str(yaml_a)}) == 0
    assert g("r1001", "Edit", {"file_path": str(a / "job.md")}) == 2
    assert g("r1001", "Bash", {"command": "ohmyjob prep build 1001"}) == 0
    assert g("r1001", "Bash", {"command": "ohmyjob prep build 1005"}) == 2
    assert g("r1001", "Bash", {"command": "ls"}) == 2
    assert g("r1001", "mcp__claude-in-chrome__navigate", {"url": "https://example.com"}) == 2

    original = yaml_a.read_text()
    yaml_a.write_text(original.replace("title: Operations Analyst", "title: Senior Operations Analyst", 1))
    assert "seniority word" in pe.pj("build", "1001")
    yaml_a.write_text(original.replace("Excel, SQL, Power BI, forecasting, KPI design", "SQL, Excel, Power BI, forecasting, KPI design"))
    assert "OVERFLOW: 2 pages" in pe.pj("build", "1001", env={**pe.env, "FAKE_PAGES": "2"})
    assert "Builds used 2/4" in pe.pj("build", "1001", env={**pe.env, "FAKE_PAGES": "2"})
    assert pe.pj("build", "1001").startswith("OK: exactly 1 page")
    assert (a / "Alex_Acme_Operations-Analyst.pdf").exists()
    built = {"job": "1001", "status": "built", "ground": None, "reason": "", "title": "Operations Analyst",
             "edits_md": "- **Skills:** reordered the analysis row.", "ladder": "fit one page on the third build; nothing dropped"}
    assert pe.report(BUILDER, "r1001", built).returncode == 0
    yaml_e = e / "Alex_Delta_Chip-Designer.yaml"
    reject = {"job": "1005", "status": "rejected", "ground": "wrong-discipline", "reason": "ASIC design role",
              "title": "", "edits_md": "", "ladder": ""}
    yaml_e.write_text(yaml_e.read_text() + "\n")
    refused = pe.report(BUILDER, "r1005", reject, how="capture")  # capture refuses it too
    assert json.loads(refused.stdout)["decision"] == "block"
    assert pe.report(BUILDER, "r1005", reject).returncode == 2  # a rejection after an edit is refused
    shutil.copyfile(pe.root / "Default_Resume_Files" / "Operations-Analyst.yaml", yaml_e)
    assert pe.report(BUILDER, "r1005", reject).returncode == 0
    assert pe.pj("record").startswith("OK recorded")
    rows = pe.rows()
    assert rows["1001"]["Status"] == "Resume Built" and rows["1005"]["Status"] == "Deleted"
    assert "ASIC design role" in rows["1005"]["Notes"] and rows["1010"]["Status"] == "Resume Failed"
    assert not e.exists() and i.exists()
    md = (a / "job.md").read_text()
    assert "\n# Resume Preparation\n\n## Resume Edits\n\n- **Skills:**" in md
    assert "`ohmyjob resume build 'Applications/1001_Acme_Operations-Analyst/" in md and str(pe.root) not in md
    assert "**Run " in (i / "job.md").read_text()
    summary = pe.pj("end")
    assert "Deletion check OK: folders deleted 4, rows set to Skipped-LowFit/Deleted 4" in summary
    assert "Folder check OK" in summary and "REJECTED Delta | Chip Designer (wrong-discipline)" in summary
    assert not (pe.root / ".ohmyjob" / "run" / "prep").exists()


def test_build_uses_resume_build_with_absolute_paths_and_survives_a_timeout(pe):
    pe.folder("1001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("1001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-build"))
    pe.bind(BUILDER, "r1", brief)
    env = {k: v for k, v in pe.env.items() if k != "PJ_COMPILE_CMD"}
    assert pe.pj("build", job, env=env).startswith("OK: exactly 1 page")
    role_yaml, pdf, max_pages, paper = json.loads(Path(pe.env["FAKE_BUILD_LOG"]).read_text())
    assert Path(role_yaml).is_absolute() and Path(pdf).is_absolute() and pdf.endswith(".pdf")
    assert (max_pages, paper) == (1, None)  # exactly the page count of the source resume's PDF
    out = pe.pj("build", job, env={**env, "FAKE_BUILD_RAISE": "1"})
    assert out.startswith("FAIL") and "Builds used 2/4" in out and "timed out" in out
    assert pe.pj("build", job, env={**env}).startswith("OK")  # the hook did not die; the count went on
    assert json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]["builds"] == 3


def source_pages(pe, pages):
    (pe.root / "Default_Resume_Files" / OPS).write_bytes(make_pdf("Resume", pages))


def test_the_page_rule_is_exactly_the_source_page_count(pe):
    source_pages(pe, 2)
    pe.folder("1001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("1001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-build"))
    text = Path(brief).read_text()
    assert "exactly 2 pages" in text and "Page limit" not in text
    assert json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]["pages"] == 2
    pe.bind(BUILDER, "r1", brief)
    out = pe.pj("build", job, env={**pe.env, "FAKE_PAGES": "3"})
    assert out.startswith("OVERFLOW: 3 pages, the source resume has 2")
    out = pe.pj("build", job, env={**pe.env, "FAKE_PAGES": "1"})
    assert out.startswith("CHECK-FAIL") and "keep the original length" in out.lower() and "exactly 2 pages" in out
    assert not (pe.root / "Applications" / "1001_Acme_Analyst" / "Alex_Acme_Analyst.pdf").exists()
    assert pe.pj("build", job, env={**pe.env, "FAKE_PAGES": "2"}).startswith("OK: exactly 2 pages")
    real = {k: v for k, v in pe.env.items() if k != "PJ_COMPILE_CMD"}  # resume_build.build gets the page count
    assert pe.pj("build", job, env={**real, "FAKE_PAGES": "1"}).startswith("CHECK-FAIL")  # the 4th and last build
    assert json.loads(Path(pe.env["FAKE_BUILD_LOG"]).read_text())[2] == 2


def test_a_source_pdf_that_cannot_be_read_is_a_blocker_not_a_crash(pe):
    (pe.root / "Default_Resume_Files" / OPS).write_bytes(b"%PDF broken")
    pe.folder("1001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("1001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    out = json.loads(pe.pj("assign-build"))
    assert out["workers"] == [] and out["failed_without_worker"] == ["1001_Acme_Analyst"]


def finish_screen(pe, job, brief, matched):
    pe.bind(SCREEN, f"s{job}", brief)
    assert pe.report(SCREEN, f"s{job}", {"job": job, "score": 8, **{**MATCH, "matched_resume": matched}}).returncode == 0


def test_a_resume_worker_starts_while_other_screen_workers_still_run(pe):
    for job_id, company in [("1001", "Acme"), ("1002", "Beta"), ("1003", "Gamma")]:
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    env = {**pe.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "5"}  # 4 slots shared by every worker type
    assert json.loads(pe.pj("start"))["screen"] == 3
    first = json.loads(pe.pj("next", env=env))["workers"]
    assert [w["subagent_type"] for w in first] == [SCREEN] * 3  # no wave limit: every free slot is used at once
    briefs = dict(pe.workers(json.dumps({"workers": first})))
    finish_screen(pe, "1001", briefs["1001"], OPS)  # 1002 and 1003 are still screening
    second = json.loads(pe.pj("next", env=env))["workers"]
    assert [w["subagent_type"] for w in second] == [BUILDER] and "job=1001" in second[0]["prompt"]
    state = json.loads((pe.run_dir() / "state.json").read_text())
    assert sum(1 for j in state["jobs"].values() if j["assigned"] and not j["done"]) == 3  # 2 screens + 1 resume, 4 slots


def test_slots_are_shared_screens_first_then_builds(pe):
    for job_id, company in [("1001", "Acme"), ("1002", "Beta"), ("1003", "Gamma")]:
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    env = {**pe.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "3"}  # 2 slots
    pe.pj("start")
    briefs = dict(pe.workers(json.dumps({"workers": json.loads(pe.pj("next", env=env))["workers"]})))
    assert sorted(briefs) == ["1001", "1002"]
    finish_screen(pe, "1001", briefs["1001"], OPS)
    nxt = json.loads(pe.pj("next", env=env))["workers"]  # one slot free: the waiting screen goes before the build
    assert [w["subagent_type"] for w in nxt] == [SCREEN] and "job=1003" in nxt[0]["prompt"]
    pe.bind(SCREEN, "s1002", briefs["1002"])
    assert pe.report(SCREEN, "s1002", {"job": "1002", **LOW}).returncode == 0
    nxt = json.loads(pe.pj("next", env=env))["workers"]  # no screen left to start: the build takes the slot
    assert [w["subagent_type"] for w in nxt] == [BUILDER] and "job=1001" in nxt[0]["prompt"]


@pytest.mark.parametrize("template", ["Template.yaml", "Template-CV.yaml"])
def test_a_copy_of_each_template_goes_through_the_whole_prep_path(pe, template):
    """Build, catalog, screening match, frozen keys and budgets (check_yaml), the resume-worker guard and the brief."""
    import yaml

    text = (REPO / "templates" / template).read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if "OHMYJOB-TEMPLATE" not in line) + "\n"
    defaults = pe.root / "Default_Resume_Files"
    (defaults / "Copy-Of-Template.yaml").write_text(text, encoding="utf-8")
    (defaults / "Copy-Of-Template.pdf").write_bytes(make_pdf("Analyst copy", pages=2))
    # the real build of the copy (no LaTeX here: render() is the build's first step and fails on a bad document)
    import importlib.util

    spec = importlib.util.spec_from_file_location("real_resume_build", SCRIPTS / "resume_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    assert "\\begin{document}" in rb.render(defaults / "Copy-Of-Template.yaml")
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    catalog = (pe.root / ".ohmyjob" / "catalog" / "catalog.md").read_text(encoding="utf-8")
    assert "### Copy-Of-Template.pdf" in catalog and "kind: tailorable" in catalog
    block = catalog.split("### Copy-Of-Template.pdf", 1)[1].split("###", 1)[0]
    assert ("- introduction:" in block) == (template == "Template.yaml")  # a CV has no introduction line
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    finish_screen(pe, job, brief, "Copy-Of-Template.pdf")
    assert pe.pj("record").startswith("OK recorded")
    [(job, brief)] = pe.workers(pe.pj("assign-build"))
    assert "exactly 2 pages" in Path(brief).read_text()
    pe.bind(BUILDER, "r1", brief)
    yaml_path = next((pe.root / "Applications").glob("1001_*/*.yaml"))
    assert pe.guard(BUILDER, "r1", "Edit", {"file_path": str(yaml_path)}).returncode == 0
    assert pe.pj("build", job, env={**pe.env, "FAKE_PAGES": "2"}).startswith("OK: exactly 2 pages")  # checks pass unchanged
    # the checks still bite: a frozen key, and (resume only) the introduction opening
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["address"] = "Elsewhere 1"
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert "'address' must stay unchanged" in pe.pj("build", job)


def test_a_cv_yaml_runs_through_check_yaml_without_an_introduction():
    import copy
    import importlib

    import yaml

    pj = importlib.import_module("pj")
    cv = yaml.safe_load((REPO / "templates" / "Template-CV.yaml").read_text(encoding="utf-8"))
    assert "introduction" not in cv and cv["document"] == "cv"
    assert pj.check_yaml(copy.deepcopy(cv), cv, False, "") == []
    longer = copy.deepcopy(cv)
    longer["experience"][1]["bullets"][0] = "Led " + "very " * 40 + "long work."  # a changed bullet over the budget
    assert any("renders" in e for e in pj.check_yaml(longer, cv, False, ""))
    longer = copy.deepcopy(cv)
    longer["skills"][0]["items"] = ", ".join(f"Tool{i}" for i in range(40))
    assert any("Skills: a changed row" in e for e in pj.check_yaml(longer, cv, False, ""))
    frozen = copy.deepcopy(cv)
    frozen["education"][0]["school"] = "Other"
    frozen["extracurricular"][0]["role"] = "Other"
    errors = pj.check_yaml(frozen, cv, False, "")
    assert "'education' must stay unchanged." in errors and "'extracurricular' must stay unchanged." in errors
    added = copy.deepcopy(cv)
    added["introduction"] = "A summary a CV does not have."
    assert any("top-level keys changed" in e for e in pj.check_yaml(added, cv, False, ""))


def test_stale_tracker_is_a_stop_not_an_overwrite(pe):
    import tracker as T

    for job_id, company in [("1001", "Acme"), ("1002", "Beta")]:
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    pe.pj("start")
    jobs = dict(pe.workers(pe.pj("assign-screen")))
    for job in jobs:
        pe.bind(SCREEN, f"s{job}", jobs[job])
        assert pe.report(SCREEN, f"s{job}", {"job": job, **LOW}).returncode == 0
    # another command changes the tracker between the load and the save of record
    code = ("import sys, pj, tracker\nreal = tracker.Tracker.save\n"
            "def save(self):\n    t = tracker.Tracker(self.path); t.set_cell(1, 'Notes', 'by hand'); real(t)\n    real(self)\n"
            "tracker.Tracker.save = save\npj.main(['record'])\n")
    proc = pe.run(code)
    assert "FATAL" in proc.stdout and "changed on disk" in proc.stdout, (proc.stdout, proc.stderr)
    rows = pe.rows()
    assert all(r["Status"] == "Found" for r in rows.values())
    assert any(r["Notes"] == "by hand" for r in rows.values())
    assert all((pe.root / "Applications" / p).exists() for p in os.listdir(pe.root / "Applications"))
    assert len(list((pe.root / "Applications").iterdir())) == 2
    assert len(rows) == 2  # the workbook still opens and holds both rows


def test_duplicates_keep_cpp_and_csharp_apart(pe):
    for job_id, title in (("2001", "C++ Developer"), ("2002", "C# Developer")):
        pe.folder(job_id, "Acme", title)
        pe.row(job_id, "Acme", title, "Found")
    pe.pj("start")
    assert {r["Status"] for r in pe.rows().values()} == {"Found"}


def test_end_prints_the_run_tokens(pe):
    sub = pe.root / "transcripts" / "sess" / "subagents"
    sub.mkdir(parents=True)

    def agent(agent_id: str, meta: dict, outputs: list[int], model: str) -> None:
        (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
        lines = [{"type": "assistant", "message": {"id": "msg1", "model": model, "usage": {
            "input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": o}}}
            for o in outputs]
        (sub / f"agent-{agent_id}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))

    agent("m1", {"agentType": "ohmyjob:prep-master"}, [3], "claude-sonnet-5-5")
    agent("w1", {"agentType": "ohmyjob:screen-worker", "parentAgentId": "m1"}, [5, 9], "claude-haiku-5-5")
    agent("x1", {"agentType": "ohmyjob:screen-worker", "parentAgentId": "other-run"}, [1000], "claude-haiku-5-5")
    pe.pj("start")
    pe.hook("guard", {"agent_type": MASTER, "agent_id": "m1", "tool_name": "Bash",
                      "transcript_path": str(pe.root / "transcripts" / "sess.jsonl"),
                      "tool_input": {"command": "ohmyjob prep end"}})
    out = pe.pj("end")
    assert re.search(r"screen-worker\s+claude-haiku-5-5\s+1\s+1\s+2\s+100\s+50\s+9\n", out), out
    assert re.search(r"TOTAL\s+2\s+2\s+4\s+200\s+100\s+12$", out), out


def test_wave_fills_past_a_missing_folder(pe):
    for job_id, company in (("2001", "Acme"), ("2002", "Beta")):
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    pe.pj("start")
    shutil.rmtree(next((pe.root / "Applications").glob("2001_*")))
    out = json.loads(pe.pj("assign-screen", "--max", "1"))
    assert ([w["prompt"].split()[0] for w in out["workers"]], out["left"]) == (["job=2002"], 0)


def test_record_survives_a_folder_removed_by_hand(pe):
    for job_id, company in (("2001", "Acme"), ("2002", "Beta")):
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    pe.pj("start")
    jobs = dict(pe.workers(pe.pj("assign-screen")))
    for job in jobs:
        pe.bind(SCREEN, f"s{job}", jobs[job])
        assert pe.report(SCREEN, f"s{job}", {"job": job, "score": 7, **MATCH}).returncode == 0
    shutil.rmtree(next((pe.root / "Applications").glob("2002_*")))
    assert pe.pj("record").startswith("OK recorded")
    rows = pe.rows()
    assert (rows["2001"]["Status"], rows["2002"]["Status"]) == ("Matched", "Found")
    assert "2002_Beta_Analyst: not recorded" in pe.pj("end")


def test_empty_catalog_stops_screening(pe):
    for p in (pe.root / "Default_Resume_Files").glob("*.pdf"):
        p.unlink()
    pe.folder("2001", "Acme", "Analyst")
    pe.row("2001", "Acme", "Analyst", "Found")
    out = pe.pj("start")
    assert out.startswith("FATAL") and "/ohmyjob:build-resumes" in out and "marker" in out


def test_no_build_report_leaves_matched(pe):
    pe.folder("2001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("2001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    assert len(json.loads(pe.pj("assign-build"))["workers"]) == 1
    pe.pj("record")
    assert pe.rows()["2001"]["Status"] == "Matched"


# --------------------------------------------------------------------------- pure functions

import importlib


@pytest.fixture
def pj(workspace, tmp_path, monkeypatch):
    """pj imported in this process, with the fake resume_build for the budgets."""
    fake = tmp_path / "fb"
    fake.mkdir()
    (fake / "resume_build.py").write_text(FAKE_RESUME_BUILD)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.syspath_prepend(str(fake))
    for name in ("resume_build", "pj"):
        sys.modules.pop(name, None)
    module = importlib.import_module("pj")
    yield module
    sys.modules.pop("pj", None)
    sys.modules.pop("resume_build", None)


def load(workspace: Path, name: str) -> dict:
    return yaml.safe_load((workspace / "Default_Resume_Files" / f"{name}.yaml").read_text())


def copy(data: dict) -> dict:
    return yaml.safe_load(yaml.safe_dump(data))


def test_yaml_rules(pj, workspace):
    src, profile = load(workspace, "Operations-Analyst"), (workspace / "Profile.md").read_text()
    assert pj.check_yaml(src, src, False, profile) == []  # the pre-made resumes pass as they are
    new = copy(src)
    new["experience"][1]["bullets"][0] = "x " * 70
    assert any("renders" in e for e in pj.check_yaml(new, src, False, profile))
    new = copy(src)
    new["projects"].pop()
    assert pj.check_yaml(new, src, False, profile)  # a project dropped before any overflow
    assert pj.check_yaml(new, src, True, profile) == []  # after an overflow
    new = copy(src)
    new["experience"][1]["dates"] = "2020"
    assert any("dates" in e for e in pj.check_yaml(new, src, False, profile))
    new = copy(src)
    new["title"] = "Data Scientist"
    assert any("must open with" in e for e in pj.check_yaml(new, src, False, profile))
    new = copy(src)
    new["title"] = "Senior Operations Analyst"
    new["introduction"] = "Senior Operations Analyst " + src["introduction"][:40]
    assert any("seniority word" in e for e in pj.check_yaml(new, src, False, profile))
    new = copy(src)
    new["interests"] = "no"
    assert any("'interests' must stay unchanged" in e for e in pj.check_yaml(new, src, False, profile))
    for bullet in ("Built \\input{/etc/passwd} pipelines.", "Built \\textbf{pipelines}.", "Saved 5\\% of cost."):
        new = copy(src)
        new["experience"][1]["bullets"][0] = bullet
        assert any("No backslash" in e for e in pj.check_yaml(new, src, False, profile))
    new = copy(src)
    new["education_details"] = "no"
    assert any("'education_details' must stay unchanged" in e for e in pj.check_yaml(new, src, False, profile))


def test_senior_title_in_the_source_is_kept(pj):
    src = {"title": "Senior Buyer", "introduction": "Senior Buyer with ten years.", "experience": [], "projects": [],
           "skills": [], "interests": "no", "education_details": "no"}
    assert pj.check_yaml(copy(src), src, False, "") == []


def test_empty_projects_floors_and_flat_skills(pj, workspace):
    src = load(workspace, "Project-Coordinator")
    assert src["projects"] == []
    assert pj.check_yaml(copy(src), src, False, "") == []
    new = copy(src)
    new["projects"] = [{"title": "X", "bullets": ["a", "b"]}]
    assert any("Projects must keep 0" in e for e in pj.check_yaml(new, src, True, ""))
    new = copy(src)
    new["experience"][0]["bullets"] = ["only one"]  # the floor is 2 for a source with 3
    assert any("needs 2 to 4 bullets" in e for e in pj.check_yaml(new, src, False, ""))
    ops = load(workspace, "Operations-Analyst")
    swapped = copy(ops)
    swapped["projects"][0] = {"title": "From Profile", "bullets": []}  # a project with no source needs 2 bullets
    assert any("needs 2 to 4 bullets" in e for e in pj.check_yaml(swapped, ops, False, ""))
    one = copy(ops)
    assert len(one["projects"][0]["bullets"]) == 1  # a source with one bullet: the floor is 1
    assert pj.check_yaml(one, ops, False, "") == []
    new = copy(src)
    new["skills"][0]["label"] = "A rather long label of thirty chars"
    new["skills"][0]["items"] = ", ".join(["Planning"] * 12)  # 106 characters of items: within the budget of 120
    assert pj.check_yaml(new, src, False, "") == []  # the label does not count against the budget
    new = copy(src)
    new["skills"][0]["items"] = ", ".join(["Planning"] * 30)
    assert any("Skills: a changed row has" in e for e in pj.check_yaml(new, src, False, ""))
    new = copy(src)
    new["skills"].append({"label": "Extra", "items": "Go"})
    assert any("keep at most 2 rows" in e for e in pj.check_yaml(new, src, False, ""))
    new = copy(src)
    new["skills"] = [{"groups": [{"label": "Old", "items": "format"}]}]
    assert any("each row needs" in e for e in pj.check_yaml(new, src, False, ""))


def test_grouped_roles(pj, workspace):
    src = load(workspace, "Operations-Analyst")
    new = copy(src)
    new["experience"][0]["roles"].reverse()
    assert any("roles keep their order" in e for e in pj.check_yaml(new, src, False, ""))
    new = copy(src)
    new["experience"][0] = new["experience"][0]["roles"][0] | {"company": "Harborline", "location": "Hamburg"}
    assert pj.check_yaml(new, src, False, "")  # the group cannot be flattened
    new = copy(src)
    new["experience"][0]["roles"][1]["bullets"] = ["Reconciled invoices and automated the KPI pack as one bullet."]
    assert any("Operations Analyst @ Harborline" in e for e in pj.check_yaml(new, src, False, ""))


def test_fill_is_idempotent(pj):
    md = job_md("https://www.linkedin.com/jobs/view/1", "A", "T")
    once = pj.fill_section(pj.fill_overview(md, "Match Score", "7"), r"Gaps\??", ["one"])
    assert pj.fill_section(pj.fill_overview(once, "Match Score", "7"), r"Gaps\??", ["one"]) == once
    with pytest.raises(pj.JobError):
        pj.fill_overview(once, "Match Score", "8")
    bold = job_md("https://www.linkedin.com/jobs/view/1", "A", "T", bold=True)
    assert "- **Match Score:** 7\n" in pj.fill_overview(bold, "Match Score", "7")


# --------------------------------------------------------------------------- review round 1


def test_a_screened_in_job_is_offered_again_as_a_build_worker(pe):
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [w] = json.loads(pe.pj("next"))["workers"]
    assert pe.guard(MASTER, "m1", "Agent", {**w}).returncode == 0  # the screen worker really started
    [(job, brief)] = pe.workers(json.dumps({"workers": [w]}))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 8, **MATCH}).returncode == 0
    [build] = json.loads(pe.pj("next"))["workers"]  # recorded, now a build worker; the master drops it
    assert build["subagent_type"] == BUILDER
    state = json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]
    assert state["started"] is False and "started_at" not in state
    pe.edit_state(lambda st: st["jobs"][job].update(assigned_at=st["jobs"][job]["assigned_at"] - 120))
    assert json.loads(pe.pj("next"))["workers"] == [build]


def test_the_stale_cutoff_counts_from_the_workers_start(pe):
    import aa_lib

    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("next"))
    pe.bind(SCREEN, "s1", brief)  # started just now, but assigned long ago
    pe.edit_state(lambda st: st["jobs"][job].update(assigned_at=st["jobs"][job]["assigned_at"] - aa_lib.STALE_SECONDS - 5))
    pe.pj("next", env={**pe.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2"})
    state = json.loads((pe.run_dir() / "state.json").read_text())["jobs"][job]
    assert state["done"] is False and "outcome" not in state


def test_paths_must_be_absolute_and_inside_the_job(pe):
    pe.folder("1001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("1001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-build"))
    pe.bind(BUILDER, "r1", brief)
    folder = next((pe.root / "Applications").glob("1001_*"))
    yaml_path = next(folder.glob("*.yaml"))
    read = lambda p: pe.guard(BUILDER, "r1", "Read", {"file_path": p}).returncode  # noqa: E731
    edit = lambda p: pe.guard(BUILDER, "r1", "Edit", {"file_path": p}).returncode  # noqa: E731
    assert read(str(yaml_path)) == 0 and edit(str(yaml_path)) == 0
    assert read(str(folder / ".." / "1001_Acme_Analyst" / yaml_path.name)) == 0  # `..` that lands on the file is the file
    assert read(str(folder / ".." / ".." / "Profile.md")) == 2
    assert edit(str(folder / ".." / ".." / "Profile.md")) == 2
    # a relative path would resolve against the hook's folder, not the agent's: refused even when it names the file
    hook_dir_env = {**pe.env}
    rel = pe.hook("guard", {"agent_type": BUILDER, "agent_id": "r1", "tool_name": "Edit", "cwd": str(folder),
                            "tool_input": {"file_path": yaml_path.name}}, env=hook_dir_env)
    assert rel.returncode == 2 and "absolute" in rel.stderr
    link = pe.root / "Applications" / "link.yaml"  # a symlink to the secret is the secret
    link.symlink_to(pe.root / "Profile.md")
    assert read(str(link)) == 2 and edit(str(link)) == 2
    assert pe.guard(BUILDER, "r1", "Write", {"file_path": str(yaml_path), "content": "x"}).returncode == 2


def test_handback_then_a_null_capture(pe):
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("next"))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, **LOW}).returncode == 0
    nothing = pe.report(SCREEN, "s1", None, how="capture")
    assert (nothing.returncode, nothing.stdout) == (0, "")
    again = pe.report(SCREEN, "s1", {"job": job, **LOW}, how="capture")  # the saved report is not asked for twice
    assert (again.returncode, again.stdout) == (0, "")
    assert pe.pj("record").startswith("OK recorded") and pe.rows()[job]["Status"] == "Skipped-LowFit"


def test_the_compile_command_in_job_md_works_after_the_run(pe):
    pe.folder("1001", "Acme", "Analyst", score="7", resume=OPS)
    pe.row("1001", "Acme", "Analyst", "Matched")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-build"))
    pe.bind(BUILDER, "r1", brief)
    assert pe.pj("build", job).startswith("OK")
    built = {"job": job, "status": "built", "ground": None, "reason": "", "title": "Operations Analyst",
             "edits_md": "- **Skills:** none.", "ladder": "nothing dropped"}
    assert pe.report(BUILDER, "r1", built).returncode == 0
    pe.pj("record")
    md = next((pe.root / "Applications").glob("1001_*/job.md")).read_text()
    assert "`ohmyjob resume build 'Applications/1001_Acme_Analyst/Alex_Acme_Analyst.yaml'`" in md


def test_resume_build_signature_is_the_one_pj_calls():
    import inspect

    import importlib.util  # importing the real module runs no LaTeX

    spec = importlib.util.spec_from_file_location("real_resume_build", SCRIPTS / "resume_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = inspect.signature(module.build).parameters
    assert list(params) == ["role_yaml", "out_pdf", "max_pages", "paper"]
    assert params["max_pages"].kind is inspect.Parameter.KEYWORD_ONLY and params["max_pages"].default is None
    assert params["paper"].kind is inspect.Parameter.KEYWORD_ONLY and params["paper"].default is None
    assert callable(module.rendered_len) and isinstance(module.BULLET_BUDGET, int) and isinstance(module.SKILLS_BUDGET, int)


def test_real_hooks_py_denies_a_main_session_javascript_call(pe):
    proc = pe.hook("guard", {"tool_name": "mcp__claude-in-chrome__javascript_tool", "tool_input": {"text": "1"}})
    assert proc.returncode == 2 and proc.stderr.strip(), (proc.stdout, proc.stderr)
    # a spoofed prep agent_type inside tool_input changes nothing
    proc = pe.hook("guard", {"tool_name": "mcp__claude-in-chrome__javascript_tool",
                             "tool_input": {"agent_type": MASTER, "text": "1"}})
    assert proc.returncode == 2


def test_a_fake_cwd_in_tool_input_does_not_pick_the_workspace(pe, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    payload = {"cwd": str(elsewhere), "agent_type": MASTER, "tool_name": "Read",
               "tool_input": {"cwd": str(pe.root), "file_path": "x"}}
    env = {k: v for k, v in pe.env.items() if k != "CLAUDE_PROJECT_DIR"}  # OHMYJOB_WORKSPACE still names the workspace
    proc = subprocess.run([sys.executable, str(SCRIPTS / "pj_hooks.py"), "guard"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, cwd=elsewhere)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


# --------------------------------------------------------------------------- v2: catalog, ready-made PDFs, mock guard


def test_the_mock_guard_refuses_while_profile_has_the_marker(pe):
    prof = pe.root / "Profile.md"
    prof.write_text("<!-- OHMYJOB-TEMPLATE: replace the mock data with yours, then delete this line -->\n" + prof.read_text())
    out = pe.pj("start")
    assert out.count("\n") == 0 and out.startswith("FATAL") and "Profile.md" in out
    assert not (pe.root / ".ohmyjob" / "run" / "prep").exists()


def test_catalog_lists_yaml_and_ready_made_resumes_but_never_templates(pe):
    d = pe.root / "Default_Resume_Files"
    (d / "Nurse-ICU.pdf").write_bytes(make_pdf("Registered nurse, ICU, ten years of ward experience"))
    (d / "Template.yaml").write_text((d / "Operations-Analyst.yaml").read_text())
    (d / "Template.pdf").write_bytes(b"%PDF")
    (d / "Marked.yaml").write_text("# OHMYJOB-TEMPLATE: copy me\n" + (d / "Operations-Analyst.yaml").read_text())
    (d / "Marked.pdf").write_bytes(b"%PDF")
    (d / "Marked-Only.pdf").write_bytes(b"%PDF OHMYJOB-TEMPLATE")
    (d / "Unbuilt.yaml").write_text((d / "Operations-Analyst.yaml").read_text())  # no PDF: not usable
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    index = json.loads((pe.root / ".ohmyjob" / "catalog" / "index.json").read_text())
    assert index == {OPS: "tailorable", COORD: "tailorable", "Nurse-ICU.pdf": "ready-made"}
    text = (pe.root / ".ohmyjob" / "catalog" / "catalog.md").read_text()
    assert "registered nurse" in text.lower() and "ten years of ward experience" in text
    assert "Operations analyst with six years" in text and "Template" not in text and "Marked" not in text
    # a tailorable resume is judged on its roles too, both yaml shapes (employer + roles, and role + company)
    assert ("- experience: Senior Operations Analyst, Harborline Freight Group (2022 - Present) | "
            "Operations Analyst, Harborline Freight Group (2020 - 2022) | "
            "Supply Planning Analyst, Brightwater Foods (2018 - 2020)") in text
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    body = Path(brief).read_text()
    assert "- Nurse-ICU.pdf (ready-made)" in body and f"- {OPS} (tailorable)" in body
    # the screener may read its brief and the catalog text, nothing else (no PDF)
    pe.bind(SCREEN, "s1", brief)
    cat = str(pe.root / ".ohmyjob" / "catalog" / "catalog.md")
    assert pe.guard(SCREEN, "s1", "Read", {"file_path": cat}).returncode == 0
    assert pe.guard(SCREEN, "s1", "Read", {"file_path": str(pe.root / "Default_Resume_Files" / "Nurse-ICU.pdf")}).returncode == 2
    assert pe.guard(MASTER, "m", "Read", {"file_path": cat}).returncode == 2
    other = pe.hook("guard", {"agent_type": BUILDER, "agent_id": "r9", "tool_name": "Read", "tool_input": {"file_path": cat}})
    assert other.returncode == 2


def test_only_template_files_means_no_usable_resume(pe):
    d = pe.root / "Default_Resume_Files"
    for p in list(d.glob("*")):
        p.unlink()
    (d / "Template.yaml").write_text("# OHMYJOB-TEMPLATE: x\ntitle: T\n")
    (d / "Template.pdf").write_bytes(b"%PDF")
    out = pe.pj("start")
    assert out.startswith("FATAL") and "build-resumes" in out and "marker" in out


def test_a_ready_made_match_is_copied_and_set_to_resume_built(pe):
    (pe.root / "Default_Resume_Files" / "Nurse-ICU.pdf").write_bytes(make_pdf("Registered nurse ICU"))
    folder = pe.folder("1001", "Acme", "ICU Nurse")
    pe.row("1001", "Acme", "ICU Nurse", "Found")
    assert json.loads(pe.pj("start"))["screen"] == 1
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    pe.bind(SCREEN, "s1", brief)
    bad = pe.report(SCREEN, "s1", {"job": job, "score": 9, **{**MATCH, "matched_resume": "Missing.pdf"}})
    assert bad.returncode == 2  # only catalog names are accepted
    ok = pe.report(SCREEN, "s1", {"job": job, "score": 9, **{**MATCH, "matched_resume": "Nurse-ICU.pdf"}})
    assert ok.returncode == 0, ok.stderr
    assert pe.pj("record").startswith("OK recorded: 1 Resume Built")
    row = pe.rows()[job]
    assert (row["Status"], row["Match Score"]) == ("Resume Built", 9)
    assert (folder / "Alex_Acme_ICU-Nurse.pdf").read_bytes() == (pe.root / "Default_Resume_Files" / "Nurse-ICU.pdf").read_bytes()
    assert not list(folder.glob("*.yaml"))
    md = (folder / "job.md").read_text()
    assert "- Matched Resume: Nurse-ICU.pdf" in md and "# Resume Preparation" in md and "Ready-made resume used as it is" in md
    assert json.loads(pe.pj("next")) == {"done": True}  # no build job, no resume-worker
    summary = pe.pj("end")
    assert "Resumes built: 1 (1 ready-made)" in summary and "Folder check OK" in summary


def test_a_ready_made_copy_is_undone_when_the_tracker_is_stale(pe):
    (pe.root / "Default_Resume_Files" / "Nurse-ICU.pdf").write_bytes(make_pdf("Registered nurse ICU"))
    folder = pe.folder("1001", "Acme", "ICU Nurse")
    pe.row("1001", "Acme", "ICU Nurse", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 9, **{**MATCH, "matched_resume": "Nurse-ICU.pdf"}}).returncode == 0
    code = ("import pj, tracker\nreal = tracker.Tracker.save\n"
            "def save(self):\n    t = tracker.Tracker(self.path); t.set_cell(1, 'Notes', 'by hand'); real(t)\n    real(self)\n"
            "tracker.Tracker.save = save\npj.main(['record'])\n")
    assert "FATAL" in pe.run(code).stdout
    assert not list(folder.glob("*.pdf")) and pe.rows()[job]["Status"] == "Found"


def test_a_yaml_match_still_goes_to_the_resume_worker(pe):
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    pe.pj("start")
    [(job, brief)] = pe.workers(pe.pj("assign-screen"))
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 8, **MATCH}).returncode == 0
    assert pe.pj("record").startswith("OK recorded: 1 Matched")
    assert pe.rows()[job]["Status"] == "Matched"
    out = json.loads(pe.pj("assign-build"))
    assert out["subagent_type"] == BUILDER and len(out["workers"]) == 1


def test_pdf_text_is_extracted_with_pypdf(pj, workspace):
    path = workspace / "x.pdf"
    path.write_bytes(make_pdf("Hello catalog"))
    assert "Hello catalog" in pj.pdf_text(path)
    path.write_bytes(b"not a pdf")
    assert "could not be read" in pj.pdf_text(path)


def test_header_and_education_are_frozen_in_the_one_file_yaml(pj, workspace):
    src = load(workspace, "Operations-Analyst")
    assert pj.check_yaml(copy(src), src, False, "") == []
    for key, value in (("name", {"first": "Sam", "last": "Other"}), ("email", "sam@example.com"), ("phone", "1"),
                       ("address", "Elsewhere 1"), ("linkedin", "sam"), ("sections", {"skills": "Other"}),
                       ("education", [{"degree": "PhD", "school": "X", "location": "Y", "dates": "1", "details": []}]),
                       ("education_details", "no")):
        new = copy(src)
        new[key] = value
        assert any(f"'{key}' must stay unchanged" in e for e in pj.check_yaml(new, src, False, "")), key
    new = copy(src)
    del new["education"]
    assert any("top-level keys changed" in e for e in pj.check_yaml(new, src, False, ""))


def test_copies_of_the_mock_template_pdf_are_never_ready_made(pe):
    d = pe.root / "Default_Resume_Files"
    (d / "Template.pdf").write_bytes(make_pdf("some template bytes"))
    (d / "Nurse.pdf").write_bytes((d / "Template.pdf").read_bytes())  # cp Template.pdf Nurse.pdf
    mock = yaml.safe_load((SCRIPTS.parent / "templates" / "Template.yaml").read_text())["name"]
    (d / "Mock-Name.pdf").write_bytes(make_pdf(f"{mock['first']} {mock['last']}, Operations Analyst"))
    (d / "Real.pdf").write_bytes(make_pdf("Sam Other, Registered Nurse"))
    pe.folder("1001", "Acme", "Analyst")
    pe.row("1001", "Acme", "Analyst", "Found")
    start = json.loads(pe.pj("start"))
    index = json.loads((pe.root / ".ohmyjob" / "catalog" / "index.json").read_text())
    assert index == {OPS: "tailorable", COORD: "tailorable", "Real.pdf": "ready-made"}
    assert any("Nurse.pdf: skipped" in i for i in start["issues"]) and any("Mock-Name.pdf: skipped" in i for i in start["issues"])
