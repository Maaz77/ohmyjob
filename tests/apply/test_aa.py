"""Tests for aa.py and hooks.py. They run against a private copy of tests/fixtures/workspace ("Alex Example").

Each test class builds a workspace with two jobs at "Resume Built" (a LinkedIn job and an Indeed job with an Apply URL),
a tracker, and a tailored resume (yaml + pdf) in each job folder, and points OHMYJOB_WORKSPACE at it. The scripts of
the repo run as subprocesses. The Haiku check is replaced by tests/apply/fake_judge.py (AA_JUDGE_CMD).
Page samples are in tests/apply/samples/ (all constructed, in the format of the Claude in Chrome tools).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURE = REPO / "tests" / "fixtures" / "workspace"
SAMPLES = Path(__file__).resolve().parent / "samples"
FAKE_JUDGE = Path(__file__).resolve().parent / "fake_judge.py"
AA = SCRIPTS / "aa.py"
HOOKS = SCRIPTS / "hooks.py"
ATS = REPO / "ats"
C = "mcp__claude-in-chrome__"
W = "ohmyjob:application-worker"
M = "ohmyjob:application-master"
PROMPT_RE = re.compile(r"^folder=(\S+) tab=(\d+) brief=(.+)$")
MARKER_FEED = "https://www.linkedin.com/feed/#aa-popups-as-tabs"
MARKER_ROBOTS = "https://www.linkedin.com/robots.txt#aa-popups-as-tabs"
VERSION = json.loads((REPO / "chrome-extension" / "manifest.json").read_text())["version"]
ON = f"[AA on {VERSION}]"
LINKEDIN_JOB = "4399000001_Lumenfield-Logistics_Operations-Analyst"
INDEED_JOB = "969845171025937_Quillon-Systems_Project-Coordinator"
BLOCKER = "questions for the candidate"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def make_project(jobs: tuple = (LINKEDIN_JOB, INDEED_JOB)) -> Path:
    """A workspace from the fixture: the given jobs at "Resume Built" with a tailored yaml and pdf each, and a tracker."""
    from tracker import Tracker  # noqa: E402

    root = Path(tempfile.mkdtemp(prefix="aa_test_")) / "workspace"
    shutil.copytree(FIXTURE, root)
    for name in ("Pending-Review", "Needs-Attention", "Submitted"):
        (root / name).mkdir()
    tracker = Tracker.create(root / "Job_Tracker.xlsx")
    for folder in sorted(p.name for p in (root / "Applications").iterdir()):
        if folder not in jobs:
            shutil.rmtree(root / "Applications" / folder)
            continue
        job_dir = root / "Applications" / folder
        _id, company, role = folder.split("_")
        stem = f"Alex_{company}_{role}"  # long on purpose: a worker's UPLOAD_HANG makes it short (retry-plan)
        shutil.copy2(root / "Default_Resume_Files" / "Operations-Analyst.yaml", job_dir / f"{stem}.yaml")
        (job_dir / f"{stem}.pdf").write_bytes(b"%PDF-1.4\n% test resume\n")
        md = (job_dir / "job.md").read_text(encoding="utf-8")
        url = re.search(r"Job URL:\**\s*(\S+)", md).group(1)
        tracker.add_row({"Job URL": url, "Status": "Resume Built", "Company": company.replace("-", " "),
                         "Job Title": folder.split("_")[2].replace("-", " "), "Location": "Berlin, Germany"})
    tracker.save()
    return root


class Base(unittest.TestCase):
    root: Path
    jobs: tuple = (LINKEDIN_JOB, INDEED_JOB)

    @classmethod
    def setUpClass(cls):
        cls.root = make_project(cls.jobs)
        cls.judge_log = cls.root.parent / "fake_judge.log"
        cls.env = {**os.environ, "OHMYJOB_WORKSPACE": str(cls.root), "AA_STAGGER": "0",
                   "AA_JUDGE_CMD": f"{sys.executable} {FAKE_JUDGE}", "AA_FAKE_JUDGE": str(cls.judge_log)}
        cls.env.pop("CLAUDE_PROJECT_DIR", None)
        cls.env.pop("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root.parent, ignore_errors=True)

    def run_aa(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(AA), *args], capture_output=True, text=True, env=self.env, cwd=self.root)

    def aa(self, *args: str) -> str:
        proc = self.run_aa(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def aa_json(self, *args: str) -> dict:
        return json.loads(self.aa(*args))

    def judge_calls(self) -> list[dict]:
        if not self.judge_log.exists():
            return []
        return [json.loads(line) for line in self.judge_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def assign(self, *tabs: int) -> list[tuple[str, int, str]]:
        """Run assign and next; return (folder, tab, brief path) for each worker."""
        self.assertEqual(self.aa_json("assign", "--tabs", ",".join(str(t) for t in tabs))["queued"], len(tabs))
        workers = self.aa_json("next")["workers"]
        out = []
        for w in workers:
            self.assertTrue(w["description"].startswith("Apply: "), w)
            match = PROMPT_RE.match(w["prompt"])
            self.assertIsNotNone(match, w["prompt"])
            out.append((match.group(1), int(match.group(2)), match.group(3)))
        return out

    def hook(self, command: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
        payload = {"session_id": "s1", "cwd": str(self.root), **payload}
        return subprocess.run([sys.executable, str(HOOKS), command], input=json.dumps(payload), capture_output=True,
                              text=True, env=env or self.env, cwd=self.root)

    def guard(self, tool: str, tool_input: dict, agent_type: str | None = None, agent_id: str | None = None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
        if agent_type:
            payload["agent_type"] = agent_type
        if agent_id:
            payload["agent_id"] = agent_id
        return self.hook("guard", payload)

    def click(self, tab: int, ref: str, agent: str, note: str = "Clicks the control"):
        return self.guard(C + "computer", {"tabId": tab, "action": "left_click", "ref": ref, "action_summary": note}, W, agent)

    def assertDenied(self, proc, text: str = ""):
        self.assertEqual(proc.returncode, 2, f"expected deny, got {proc.returncode}: {proc.stdout} {proc.stderr}")
        if text:
            self.assertIn(text.lower(), proc.stderr.lower())

    def assertAllowed(self, proc):
        self.assertEqual(proc.returncode, 0, f"expected allow, got {proc.returncode}: {proc.stderr}")

    def assertExplicitAllow(self, proc):
        """The decision of a validated call of an ohmyjob application agent: permissionDecision allow."""
        self.assertAllowed(proc)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "allow", proc.stdout)

    def assertNoDecision(self, proc):
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, ""), proc.stdout + proc.stderr)

    def run_dir(self) -> Path:
        base = self.root / ".ohmyjob" / "run" / "apply"
        return base / (base / "CURRENT").read_text().strip()

    def state(self) -> dict:
        return json.loads((self.run_dir() / "state.json").read_text())

    def post(self, tool: str, tool_input: dict, text: str, agent_type: str, agent_id: str):
        proc = self.hook("snapshot", {
            "hook_event_name": "PostToolUse", "tool_name": C + tool, "tool_input": tool_input,
            "tool_response": [{"type": "text", "text": text}], "agent_type": agent_type, "agent_id": agent_id,
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def page(self, tab: int, name: str, agent_id: str = "A1"):
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample(name), W, agent_id)

    def handback(self, message: str, agent_id: str = "A1") -> subprocess.CompletedProcess:
        return self.hook("handback", {"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback",
                                      "tool_input": {"message": message}, "agent_type": W, "agent_id": agent_id})

    def clear_watchdog(self) -> None:
        """Drop the progress watchdog counters of every job (a test that repeats one denied call on purpose)."""
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        for job in state["jobs"].values():
            job.pop("watchdog", None)
            job.pop("stopped_by", None)
        path.write_text(json.dumps(state))

    def set_job(self, folder: str, **fields) -> None:
        """Change a job entry in state.json (test set-up only)."""
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        state["jobs"][folder].update(fields)
        path.write_text(json.dumps(state))

    def master_setup(self, marker: str = MARKER_FEED) -> str:
        """Master steps: tab group, blank first tab 49, the marker page of 'start', preflight."""
        self.assertExplicitAllow(self.guard(C + "tabs_context_mcp", {"createIfEmpty": True}, M, "M1"))
        self.post("tabs_context_mcp", {"createIfEmpty": True},
                  '{"availableTabs":[{"tabId":49,"title":"New Tab","url":"chrome://newtab/"}],"tabGroupId":7}\n', M, "M1")
        self.assertExplicitAllow(self.guard(C + "navigate", {"tabId": 49, "url": marker}, M, "M1"))
        self.post("tabs_context_mcp", {},
                  '{"availableTabs":[{"tabId":49,"title":"' + ON + ' Feed | LinkedIn","url":"' + marker + '"}],'
                  '"tabGroupId":7}\n', M, "M1")
        line = self.aa("preflight", "--tab", "49")
        self.assertTrue(line.startswith("OK"), line)
        return line


class TestFlow(Base):
    def test_full_flow(self):
        start = self.aa_json("start")
        self.assertEqual(start["count"], 2, start)

        line = self.master_setup()
        self.assertIn("pop-up helper: on", line)
        self.assertIn("Haiku check: OK", line)
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": "https://example.com/"}, M, "M1"))
        self.assertDenied(self.guard(C + "read_page", {"tabId": 49}, M, "M1"), "not available to the master")
        # the master may start only application-worker agents; other sessions are not limited
        self.assertDenied(self.guard("Agent", {"subagent_type": "executor", "prompt": "x"}, M, "M1"), "starts only")
        self.assertDenied(self.guard("Agent", {"prompt": "x"}, M, "M1"), "starts only")
        self.assertAllowed(self.guard("Agent", {"subagent_type": W, "prompt": "x"}, M, "M1"))
        self.assertNoDecision(self.guard("Agent", {"subagent_type": "Explore", "prompt": "x"}))

        # one new tab for each job, all briefs at once
        self.assertIn("FATAL", self.run_aa("assign", "--tabs", "101").stdout)
        self.assertIn("FATAL", self.run_aa("assign", "--tabs", "49,101").stdout)
        (folder, tab1, brief1), (folder2, tab2, _brief2) = self.assign(101, 102)
        self.assertEqual((tab1, tab2), (101, 102))
        brief = Path(brief1).read_text()
        for heading in ("## Job: Overview", "## Job: Job Description", "## Profile.md (without `# Fit Preferences`, which belongs to screening)",
                        "## Tailored resume for this job"):
            self.assertIn(heading, brief)
        # 4.3: the brief carries only the title and introduction of the tailored yaml, and no "how to use" section
        self.assertNotIn("## How to use this brief", brief)
        self.assertIn("title: Operations Analyst", brief)
        self.assertIn("introduction: ", brief)
        self.assertNotIn("experience:", brief)  # the rest of the yaml repeats Profile.md
        # 4.4: the ATS notes come from ats_notes(), one line each with its hosts
        self.assertIn("(hosts: *.myworkdayjobs.com)", brief)
        self.assertIn(str(ATS / "workday.md"), brief)
        self.assertIn("- Name: Alex Example", brief)
        self.assertIn("# Work Authorization", brief)
        self.assertNotIn("\n# Fit Preferences\n", brief)  # the heading names it in backticks: match the section line
        self.assertNotIn("Night shifts, on-call rotas", brief)
        self.assertIn("Follow Up Questions", brief)
        self.assertIn("```yaml", brief)
        self.assertNotIn("Match Reasoning", brief)
        self.assertNotIn("# Resume Preparation", brief)

        # each worker binds to its tab on its first call
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 102, "url": "https://x.myworkdayjobs.com/a"}, W, "A2"))
        self.assertDenied(self.guard(C + "read_page", {"tabId": 102}, W, "A1"), "own tab")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": "https://www.linkedin.com/"}, W, "A1"), "own tab")
        self.assertDenied(self.guard(C + "read_page", {"tabId": 101}, W, "B2"), "own tab")
        self.assertDenied(self.guard(C + "navigate", {"url": "https://www.linkedin.com/"}, W, "A1"), "tabid")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": "javascript:alert(1)"}, W, "A1"), "http")
        # a careers page that embeds a Greenhouse form (?gh_jid=N) goes to the form itself, also through LinkedIn's safety link
        proc = self.guard(C + "navigate", {"tabId": 102, "url": "https://www.linkedin.com/safety/go/?url=https%3A%2F%2F"
                                           "careers%2Eexample%2Ecom%2Fjob%2F%3Fgh_jid%3D123%26src%3Dli&urlhash=x"}, W, "A2")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["url"],
                         "https://boards.greenhouse.io/embed/job_app?token=123")
        # the same form behind Greenhouse's own ?gh_src=... marker: the posting id is the last part of the path
        proc = self.guard(C + "navigate", {"tabId": 102, "url": "https://careers.example.com/careers/apply/"
                                           "software-engineer-payments-and-risk/6717520?gh_src=73vnei"}, W, "A2")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["url"],
                         "https://boards.greenhouse.io/embed/job_app?token=6717520")
        # ?gh_src=... with no number at the end of the path: the page is left alone
        proc = self.guard(C + "navigate", {"tabId": 102, "url": "https://careers.example.com/jobs/some-role?gh_src=73vnei"}, W, "A2")
        self.assertAllowed(proc)
        self.assertNotIn("updatedInput", proc.stdout)
        # LinkedIn's "Apply on company website" redirect is skipped: the tab goes straight to the company address
        proc = self.guard(C + "navigate", {"tabId": 102, "url": "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fjobs%2Eexample%2Eorg"
                                           "%2Fjobs%2F6ad%3Futm_source%3Dlinkedin&urlhash=LCFO&isSdui=true"}, W, "A2")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["url"],
                         "https://jobs.example.org/jobs/6ad?utm_source=linkedin")
        proc = self.guard(C + "navigate", {"tabId": 102, "url": "https://www.linkedin.com/safety/go/?url=javascript%3Aalert(1)"}, W, "A2")
        self.assertAllowed(proc)
        self.assertNotIn("updatedInput", proc.stdout)  # not an http address: left to LinkedIn's own page
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 102, "url": "https://x.myworkdayjobs.com/a"}, W, "A2"))

        # read_page defaults are forced
        proc = self.guard(C + "read_page", {"tabId": 101}, W, "A1")
        self.assertAllowed(proc)
        updated = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((updated["filter"], updated["max_chars"]), ("interactive", 90000))
        proc = self.guard(C + "computer", {"tabId": 101, "action": "screenshot"}, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["scale"], 0.5)

        # clicks need a known ref and an English note
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "left_click", "coordinate": [10, 10]}, W, "A1"), "coordinates")
        self.assertDenied(self.click(101, "ref_30", "A1"), "unknown ref")
        self.page(101, "constructed_job_page.txt")
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "left_click", "ref": "ref_30"}, W, "A1"), "action_summary")
        self.assertAllowed(self.click(101, "ref_30", "A1", "Opens the Easy Apply dialog"))

        self.page(101, "constructed_form_page.txt")
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "key", "text": "Return"}, W, "A1"), "enter")
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "key", "text": "cmd+Enter"}, W, "A1"), "enter")
        self.assertAllowed(self.guard(C + "computer", {"tabId": 101, "action": "key", "text": "Tab"}, W, "A1"))
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "a\nb"}, W, "A1"), "line breaks")
        # Space can press a focused button; typing needs a clicked text field
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "key", "text": "space"}, W, "A1"), "space")
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "Alex"}, W, "A1"), "type only")
        self.assertAllowed(self.click(101, "ref_41", "A1", "Focuses the First name field"))
        self.assertAllowed(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "Alex"}, W, "A1"))
        self.assertAllowed(self.guard(C + "computer", {"tabId": 101, "action": "key", "text": "Tab"}, W, "A1"))
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "x"}, W, "A1"), "type only")
        # consent and declaration boxes: the worker ticks them
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_43", "value": True}, W, "A1"))
        self.assertAllowed(self.click(101, "ref_43", "A1", "Ticks the privacy notice box"))
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_56", "value": True}, W, "A1"))
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_61", "value": True}, W, "A1"))
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_64", "value": "I agree"}, W, "A1"))
        # consent buttons: a privacy dialog yes; a cookie banner only its private choice; never a sign-in permission
        self.page(101, "constructed_form_page.txt")  # after a click on a box the page is read again
        self.assertAllowed(self.click(101, "ref_69", "A1", "Accepts the data privacy agreement"))
        self.assertDenied(self.click(101, "ref_46", "A1"), "cookie")
        self.assertAllowed(self.click(101, "ref_47", "A1", "Rejects all cookies"))
        self.assertDenied(self.click(101, "ref_70", "A1"), "permission")
        # the resume upload: only the tailored PDF of this job
        self.assertDenied(self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_45", "paths": ["/tmp/x.pdf"]}, W, "A1"), "upload only")
        pdf = self.state()["jobs"][folder]["pdf"]
        self.assertAllowed(self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_45", "paths": [pdf]}, W, "A1"))
        # "file_path" becomes "paths", which the tool needs; "files" is dropped
        proc = self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_45", "file_path": pdf}, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"], {"tabId": 101, "ref": "ref_45", "paths": [pdf]})
        proc = self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_45", "paths": [pdf],
                                              "files": [{"name": "x.pdf", "data": "QUJD"}]}, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"], {"tabId": 101, "ref": "ref_45", "paths": [pdf]})
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_65", "value": "Germany"}, W, "A1"))
        self.page(101, "constructed_form_page.txt")  # after a fill on a choice the page is read again
        # final, apply-after-fill and unnamed submit buttons
        self.assertDenied(self.click(101, "ref_49", "A1"), "final control")
        self.assertDenied(self.click(101, "ref_48", "A1"), "showed an application form")
        self.assertDenied(self.click(101, "ref_50", "A1"), "without a name")
        self.assertAllowed(self.click(101, "ref_66", "A1", "Goes to the next step"))
        self.page(101, "constructed_form_page.txt")
        # the note of a click: a note that says "submit" stops the click, also on a harmless button
        self.assertDenied(self.click(101, "ref_66", "A1", "Submits the application"), "your note")
        self.assertAllowed(self.click(101, "ref_66", "A1", "Goes to the next step; this does not submit the form"))
        # browser_batch: read_page must be alone, and every item is checked
        batch = {"actions": [{"name": "form_input", "input": {"tabId": 101, "ref": "ref_41", "value": "Alex"}},
                             {"name": "read_page", "input": {"tabId": 101}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "inside browser_batch")
        batch = {"actions": [{"name": "form_input", "input": {"tabId": 101, "ref": "ref_41", "value": "Alex"}},
                             {"name": "computer", "input": {"tabId": 101, "action": "left_click", "ref": "ref_49",
                                                            "action_summary": "Clicks the button"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "item 2")
        batch = {"actions": [{"name": "form_input", "input": {"tabId": 101, "ref": "ref_41", "value": "Alex"}},
                             {"name": "computer", "input": {"tabId": 101, "action": "screenshot"}}]}
        proc = self.guard(C + "browser_batch", batch, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["actions"][1]["input"]["scale"], 0.5)
        # browser_batch knows only the short tool names: the guard writes them so
        batch = {"actions": [{"name": C + "form_input", "input": {"tabId": 101, "ref": "ref_41", "value": "Alex"}}]}
        proc = self.guard(C + "browser_batch", batch, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["actions"][0]["name"], "form_input")
        self.assertDenied(self.guard(C + "browser_batch", {"actions": [{"input": {"tabId": 101}}]}, W, "A1"), "short tool name")

        # reads: only briefs and ATS notes for the worker; others are not limited
        self.assertAllowed(self.guard("Read", {"file_path": brief1}, W, "A1"))
        self.assertAllowed(self.guard("Read", {"file_path": str(ATS / "workday.md")}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(self.root / "Profile.md")}, W, "A1"))
        self.assertNoDecision(self.guard("Read", {"file_path": str(self.root / "Profile.md")}))
        # JavaScript is blocked for every agent; the master cannot close a job tab
        self.assertDenied(self.guard(C + "javascript_tool", {"tabId": 101, "text": "1"}))
        self.assertDenied(self.guard(C + "tabs_close_mcp", {"tabId": 101}, M, "M1"), "job application")

        # hand-back (the way Claude Code delivers reports in auto mode): a report without JSON is refused
        stop = {"hook_event_name": "SubagentStop", "agent_type": W, "agent_id": "A1", "stop_hook_active": False}
        self.assertDenied(self.handback("done"), "one json object")
        result = {
            "folder": folder, "tab": 101, "status": "parked", "where": "Review step",
            "final_button": "Submit application", "url": "https://www.linkedin.com/jobs/view/1/apply/",
            "filled": ["First name = Alex"], "uploaded": Path(pdf).name,
            "drafted": [{"q": "Why us?", "a": "Short draft."}],
            "open": ["Years of Python"], "declarations": ["I agree to the privacy notice"], "blocker": None,
        }
        # parked without the final button and without open questions: refused
        self.page(101, "constructed_mid_page.txt")
        self.assertDenied(self.handback(json.dumps({**result, "open": []})), "before the final step")
        # parked on the final step: saved, and the master gets one short line
        self.page(101, "constructed_final_page.txt")
        proc = self.handback(json.dumps(result))
        self.assertAllowed(proc)
        short = json.loads(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["message"])
        self.assertEqual((short["folder"], short["status"]), (folder, "parked"))
        self.assertTrue((self.run_dir() / "jobs" / folder / "result.json").exists())
        # the stop after a hand-back: no text, or the result is saved already: no block
        for text in ("", "done"):
            proc = self.hook("capture", {**stop, "last_assistant_message": text})
            self.assertEqual((proc.returncode, proc.stdout.strip()), (0, ""), proc.stdout + proc.stderr)
        # hand-backs of other agents are not checked
        other = self.hook("handback", {"tool_name": "SubagentHandback", "tool_input": {"message": "x"}, "agent_type": "Explore"})
        self.assertEqual((other.returncode, other.stdout.strip()), (0, ""))

        # second job: blocked, reported as final text (Claude Code without hand-backs)
        proc = self.hook("capture", {**stop, "agent_id": "A2", "last_assistant_message": "done"})
        self.assertEqual(json.loads(proc.stdout)["decision"], "block")
        blocked = {**result, "folder": folder2, "tab": 102, "status": "blocked", "blocker": "ACCOUNT_WALL: a Workday account wall",
                   "open": [], "final_button": ""}
        proc = self.hook("capture", {**stop, "agent_id": "A2", "last_assistant_message": "```json\n" + json.dumps(blocked) + "\n```"})
        self.assertEqual(proc.stdout.strip(), "", proc.stdout)

        # finish: every job is checked and recorded in one command
        report = self.aa("finish")
        self.assertIn("Pending-Review: 1 | Needs-Attention: 1 | Deleted (closed): 0 | not finished: 0", report)
        self.assertIn("Workday account wall", report)
        self.assertNotIn("WARN", report)
        moved = self.root / "Pending-Review" / folder
        md = (moved / "job.md").read_text()
        self.assertIn("# Application Assistant", md)
        self.assertIn("Pending Review", md)
        self.assertIn("Years of Python", md)
        self.assertIn("Short draft.", md)
        self.assertIn("ticked or accepted by the worker (check it): I agree to the privacy notice", md)
        self.assertTrue((self.root / "Needs-Attention" / folder2).is_dir())

        from tracker import Tracker  # noqa: E402

        rows = Tracker(self.root / "Job_Tracker.xlsx").rows_for_key(folder.split("_")[0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Status"], "Pending Review")
        self.assertIn("Application Assistant", rows[0]["Notes"] or "")

        # cleanup after finish: the master may still read and close its own tabs, workers may not act
        self.assertAllowed(self.guard(C + "tabs_context_mcp", {}, M, "M1"))
        self.assertDenied(self.guard(C + "read_page", {"tabId": 101}, W, "A1"), "not active")
        self.post("tabs_context_mcp", {},
                  '{"availableTabs":[{"tabId":49,"url":"https://www.linkedin.com/feed/"},'
                  '{"tabId":101,"url":"https://www.linkedin.com/jobs/view/1"},{"tabId":102,"url":"https://x.myworkdayjobs.com/a"},'
                  '{"tabId":104,"url":"https://company.example/apply"}],"tabGroupId":7}\n', M, "M1")
        plan = self.aa_json("cleanup-plan")
        self.assertEqual((sorted(plan["close"]), sorted(plan["keep"])), ([49, 104], [101, 102]))
        # tab groups: the plan page goes to the home tab only; the extension confirms it in that tab's title
        plan = self.aa_json("group-plan")
        self.assertEqual((plan["tab"], plan["groups"]), (49, {"Pending Review": 1, "Needs Attention": 1}))
        self.assertIn("%22Pending%20Review%22%3A%5B101%5D", plan["url"])
        # cleanup-plan reads the tabs_context of AFTER the group page loaded: an older one is refused
        self.assertTrue(self.aa("cleanup-plan").startswith("RETRY"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 49, "url": plan["url"]}, M, "M1"))
        self.assertDenied(self.guard(C + "navigate", {"tabId": 104, "url": plan["url"]}, M, "M1"), "home tab")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": plan["url"] + ";x"}, M, "M1"))
        forged = "https://www.linkedin.com/robots.txt?aa-groups#aa-groups=%7B%22x%22%3A%5B1%5D%7D"
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": forged}, M, "M1"), "group-plan")
        self.assertAllowed(self.guard(C + "tabs_close_mcp", {"tabId": 49}, M, "M1"))
        self.assertDenied(self.guard(C + "tabs_close_mcp", {"tabId": 101}, M, "M1"), "job application")
        # the extension tags the home tab with the number of tabs it moved; fewer than planned gives its own WARN
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA grouped 1] robots.txt",'
                  '"url":"https://www.linkedin.com/robots.txt"}],"tabGroupId":7}\n', M, "M1")
        self.assertIn("close", self.aa_json("cleanup-plan"))
        self.assertIn("WARN only 1 of 2 job tabs", self.aa("end"))


class TestCleanupWaitsForTheGroups(Base):
    def test_cleanup_retries_until_the_tag_and_closes_an_old_home_tab(self):
        self.aa_json("start")
        self.master_setup()
        (f1, t1, _b1), (f2, t2, _b2) = self.assign(301, 302)
        for agent, tab, fold in (("G1", t1, f1), ("G2", t2, f2)):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://www.linkedin.com/jobs/view/1"}, W, agent))
            self.assertAllowed(self.handback(json.dumps({"folder": fold, "tab": tab, "status": "blocked", "where": "x", "final_button": "",
                               "url": "https://www.linkedin.com/jobs/view/1", "filled": [], "uploaded": None, "drafted": [], "open": [],
                               "declarations": [], "blocker": "CLOSED: the posting is closed"}), agent_id=agent))
        self.aa("finish")
        self.aa_json("group-plan")
        old_home = '{"tabId":40,"title":"' + ON + ' Feed | LinkedIn","url":"https://www.linkedin.com/feed/"}'
        state_path = self.run_dir() / "state.json"
        state = json.loads(state_path.read_text())
        state["initial_tabs"].append(40)  # open before this run started: a run that stopped left it
        state_path.write_text(json.dumps(state))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[' + old_home + ',{"tabId":49,"title":"robots.txt","url":"https://www.linkedin.com/robots.txt"}]}\n', M, "M1")
        self.assertTrue(self.aa("cleanup-plan").startswith("RETRY"))
        # the home tab (49) comes first in the tab list, so the order below shows the plan moved it to the end
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA grouped 2] robots.txt","url":"https://www.linkedin.com/robots.txt"},' + old_home + ']}\n', M, "M1")
        # the home tab (49) is closed last: the other tabs of the run go with its tab group
        self.assertEqual(self.aa_json("cleanup-plan")["close"], [40, 49])


class TestCleanupWithoutTheHomeTab(Base):
    def test_cleanup_stops_waiting_for_the_tag_once_the_home_tab_is_closed(self):
        self.aa_json("start")
        self.master_setup()
        (f1, t1, _b1), (f2, t2, _b2) = self.assign(301, 302)
        for agent, tab, fold in (("G1", t1, f1), ("G2", t2, f2)):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://www.linkedin.com/jobs/view/1"}, W, agent))
            self.assertAllowed(self.handback(json.dumps({"folder": fold, "tab": tab, "status": "blocked", "where": "x", "final_button": "",
                               "url": "https://www.linkedin.com/jobs/view/1", "filled": [], "uploaded": None, "drafted": [], "open": [],
                               "declarations": [], "blocker": "CLOSED: the posting is closed"}), agent_id=agent))
        self.aa("finish")
        self.aa_json("group-plan")
        # the home tab is gone: it can never get the "[AA grouped N]" tag, so cleanup must go on instead of retrying
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":77,"title":"stray","url":"https://example.com/x"}]}\n', M, "M1")
        self.assertEqual(self.aa_json("cleanup-plan")["close"], [77])


class TestRetry(Base):
    def test_upload_hang_renames_the_resume_and_gives_a_new_worker_once(self):
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _brief), _other = self.assign(101, 102)
        job_dir = self.root / "Applications" / folder
        old_pdf = next(job_dir.glob("Alex_*.pdf"))
        hang = {"folder": folder, "tab": tab, "status": "blocked", "where": "step 1", "final_button": "", "url": "u",
                "filled": [], "uploaded": None, "drafted": [], "open": [], "declarations": [],
                "blocker": "UPLOAD_HANG: file_upload timed out"}
        result = self.run_dir() / "jobs" / folder / "result.json"
        result.write_text(json.dumps(hang))
        workers = self.aa_json("retry-plan")["workers"]
        self.assertEqual(len(workers), 1, workers)
        short = "Alex_" + folder.split("_", 2)[1]
        self.assertTrue((job_dir / (short + ".pdf")).exists() and (job_dir / (short + ".yaml")).exists())
        self.assertFalse(old_pdf.exists() or result.exists())
        self.assertIn(f"folder={folder} tab={tab} ", workers[0]["prompt"])
        self.assertIn(str(job_dir / (short + ".pdf")), Path(PROMPT_RE.match(workers[0]["prompt"]).group(3)).read_text())
        entry = self.state()["tabs"][str(tab)]
        self.assertEqual((entry["agent_id"], entry["filled"], entry.get("started")), (None, False, False))
        # once for each job: a second hang is a normal blocker
        result.write_text(json.dumps(hang))
        self.assertEqual(self.aa_json("retry-plan")["workers"], [])

    def test_next_fills_a_free_slot_at_once(self):
        self.aa_json("start")
        self.master_setup()
        self.env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = "2"  # one slot: the master takes the other
        try:
            self.assertEqual(self.aa_json("assign", "--tabs", "101,102")["queued"], 2)
            first = self.aa_json("next")["workers"]
            self.assertEqual(len(first), 1)
            result = self.run_dir() / "jobs" / first[0]["folder"] / "result.json"
            result.write_text(json.dumps({"folder": first[0]["folder"], "status": "parked"}))
            second = self.aa_json("next")["workers"]
            self.assertEqual(len(second), 1)
            self.assertNotEqual(second[0]["folder"], first[0]["folder"])
            (self.run_dir() / "jobs" / second[0]["folder"] / "result.json").write_text("{}")
            self.assertEqual(self.aa_json("next"), {"done": True})
        finally:
            del self.env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"]

    def test_next_prints_a_dropped_worker_again(self):
        self.aa_json("start")
        self.master_setup()
        self.aa_json("assign", "--tabs", "101,102")
        workers = self.aa_json("next")["workers"]
        self.assertEqual(len(workers), 2)
        for w in workers[1:]:                                    # the master starts only the first worker
            self.assertAllowed(self.guard("Agent", {"subagent_type": W, **w}, M, "M1"))
        state = json.loads((self.run_dir() / "state.json").read_text())
        for job in state["jobs"].values():
            job["started_at"] -= 120
        (self.run_dir() / "state.json").write_text(json.dumps(state))
        again = self.aa_json("next")["workers"]
        self.assertEqual([w["folder"] for w in again], [workers[0]["folder"]])
        for w in workers:
            (self.run_dir() / "jobs" / w["folder"] / "result.json").write_text("{}")
        self.assertEqual(self.aa_json("next"), {"done": True})


def L_compact(text: str) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import aa_lib

    return aa_lib.compact_page(text, "interactive")


class TestContinue(Base):
    """/continue-applying: the jobs in Needs-Attention/ go on in the tabs that the candidate fixed by hand."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(SCRIPTS))
        from tracker import Tracker  # noqa: E402

        tracker = Tracker(cls.root / "Job_Tracker.xlsx")
        cls.folders = []
        for i, path in enumerate(sorted((cls.root / "Applications").iterdir())):
            dest = cls.root / "Needs-Attention" / path.name
            shutil.move(str(path), str(dest))
            md = dest / "job.md"
            md.write_text(md.read_text(encoding="utf-8").rstrip() + "\n\n# Application Assistant\n\n"
                          "**Run:** 2026-10-23 | Needs Attention | BLOCKED at: Workday sign-in\n\n"
                          f"Tab {701 + i} in the run window | URL: https://x.myworkdayjobs.com/a\n\n"
                          "**Blocker:** Workday account wall\n", encoding="utf-8")
            tracker.set_status(tracker.rows_for_key(path.name.split("_")[0])[0]["_index"], "Needs Attention", None)
            cls.folders.append(path.name)
        tracker.save()

    def test_continue_in_the_fixed_tabs(self):
        first, second = self.folders
        self.assertEqual(self.aa_json("start", "--continue")["count"], 2)
        self.master_setup(MARKER_ROBOTS)
        # the adopt page: only the exact one, only in the home tab
        plan = self.aa_json("adopt-plan")
        self.assertEqual((plan["tab"], plan["jobs"]), (49, 2))
        self.assertIn("%5B701%2C702%5D", plan["url"])
        # the home tab shows start's home_url (robots.txt#aa-popups-as-tabs): the adopt page must be a NEW page, so it
        # differs from it before the '#' (a change after '#' alone loads nothing, and the extension would never see it)
        self.assertEqual(self.state()["home_url"], MARKER_ROBOTS)
        self.assertNotEqual(plan["url"].split("#")[0], MARKER_ROBOTS.split("#")[0])
        self.assertTrue(plan["url"].startswith("https://www.linkedin.com/robots.txt?aa-adopt#aa-adopt="))
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": "https://www.linkedin.com/robots.txt#aa-adopt=%5B701%5D"}, M, "M1"),
                          "adopt-plan")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 49, "url": plan["url"].replace("701", "703")}, M, "M1"), "adopt-plan")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 49, "url": plan["url"]}, M, "M1"))
        # before tabs_context confirms the adopt page, 'adopted' waits (else cleanup could close a fixed tab)
        self.assertTrue(self.aa("adopted").startswith("RETRY"))
        self.assertTrue(self.aa("adopted").startswith("RETRY"))
        # tab 701 came back; tab 702 was closed
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA adopted 1] robots.txt",'
                  '"url":"https://www.linkedin.com/robots.txt"},{"tabId":701,"title":"My Information",'
                  '"url":"https://x.myworkdayjobs.com/a/apply"}],"tabGroupId":7}\n', M, "M1")
        # the job whose tab is closed (finished by hand) leaves the run
        adopted = self.aa_json("adopted")
        self.assertEqual((adopted["count"], adopted["left_alone"]), (1, 1))
        self.assertEqual(self.aa_json("assign", "--continue")["queued"], 1)
        out = self.aa_json("next")
        self.assertEqual(len(out["workers"]), 1)
        self.assertTrue(out["workers"][0]["prompt"].startswith(f"folder={first} tab=701 brief="))
        brief = Path(out["workers"][0]["prompt"].split("brief=")[1]).read_text()
        self.assertIn("## Continue from the open page", brief)
        self.assertIn("Workday account wall", brief)
        # a page that says the application was sent: no new start (the candidate may have sent it by hand)
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'heading "Your application has been submitted"\n\nTab Context:\n- tab 701', W, "C1")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 701, "url": self.state()["jobs"][first]["url"]}, W, "C1"),
                          "already submitted")
        # a page without a form (a careers site): the candidate may have filled part of the
        # application by hand, so the Haiku check decides about Apply, with "filled" unknown
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'button "Apply" [ref_5]\nbutton "Save and Continue" [ref_6]\n\nTab Context:\n- tab 701', W, "C1")
        self.assertAllowed(self.click(701, "ref_5", "C1", "Opens the application"))
        self.assertEqual(self.judge_calls()[-1]["name"], "Apply")
        judged = json.loads((self.run_dir() / "judge.json").read_text())
        self.assertIn("unknown", [e.get("payload", {}).get("filled") for e in judged.values()])
        self.assertAllowed(self.click(701, "ref_6", "C1", "Goes to the next step"))
        # with the fill state unknown, the answer is not cached for the name: a review step gets its own check
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'heading "Review your application" [ref_20]\nbutton "Edit" [ref_21]\nbutton "Apply" [ref_22]'
                  '\n\nTab Context:\n- tab 701', W, "C1")
        n = len(self.judge_calls())
        self.assertAllowed(self.click(701, "ref_22", "C1", "Opens the application"))
        self.assertEqual(len(self.judge_calls()), n + 1)
        # a page with the shape of a form: Apply is final, and stays final after a page change, also on another site
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'textbox "First name" [ref_1]\ntextbox "Last name" [ref_2]\ntextbox "Email" [ref_3]\n'
                  'button "Apply" [ref_5]\n\nTab Context:\n- tab 701', W, "C1")
        self.assertDenied(self.click(701, "ref_5", "C1", "Opens the application"), "showed an application form")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 701, "url": "https://other.example/apply"}, W, "C1"))
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'button "Apply" [ref_5]\n\nTab Context:\n- tab 701', W, "C1")
        self.assertDenied(self.click(701, "ref_5", "C1", "Opens the application"), "showed an application form")
        # starting the job over from its LinkedIn page is a fresh start, as in /apply-jobs
        job_url = self.state()["jobs"][first]["url"]
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 701, "url": job_url}, W, "C1"))
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  'button "Easy Apply" [ref_8]\n\nTab Context:\n- tab 701', W, "C1")
        self.assertAllowed(self.click(701, "ref_8", "C1", "Opens the Easy Apply form"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 701, "url": "https://x.myworkdayjobs.com/a/apply"}, W, "C1"))
        # parked at the final step: the folder moves to Pending-Review/; the job without its tab stays as it is
        self.post("read_page", {"tabId": 701, "filter": "interactive"},
                  sample("constructed_final_page.txt").replace("101", "701"), W, "C1")
        report = {"folder": first, "tab": 701, "status": "parked", "where": "Review step", "final_button": "Submit application",
                  "url": "https://x.myworkdayjobs.com/a/apply", "filled": [], "uploaded": None, "drafted": [], "open": [],
                  "declarations": [], "blocker": None}
        self.assertAllowed(self.handback(json.dumps(report), agent_id="C1"))
        summary = self.aa("finish")
        self.assertIn("Left alone: 1 jobs", summary)
        self.assertNotIn(second, summary)
        self.assertTrue((self.root / "Pending-Review" / first / "job.md").exists())
        self.assertTrue((self.root / "Needs-Attention" / second / "job.md").exists())
        group = self.aa_json("group-plan")
        self.assertEqual(group["groups"], {"Pending Review": 1})
        # the home tab is on the adopt page: a group page that differs only after '#' does not load
        self.assertNotEqual(group["url"].split("#")[0], plan["url"].split("#")[0])
        # cleanup never closes a tab that the candidate fixed, also one that did not get a worker
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA grouped 1] robots.txt","url":"https://www.linkedin.com/robots.txt"},'
                  '{"tabId":701,"url":"https://x.myworkdayjobs.com/a"}],"tabGroupId":7}\n', M, "M1")
        cleanup = self.aa_json("cleanup-plan")
        self.assertEqual((cleanup["close"], cleanup["keep"]), ([49], [701]))

        from tracker import Tracker  # noqa: E402

        tracker = Tracker(self.root / "Job_Tracker.xlsx")
        self.assertEqual(tracker.rows_for_key(first.split("_")[0])[0]["Status"], "Pending Review")
        self.assertEqual(tracker.rows_for_key(second.split("_")[0])[0]["Status"], "Needs Attention")


class TestContinueSkips(TestContinue):
    """A job whose last run saw a submission message is never continued; nor is a tab named by two jobs."""

    def test_continue_in_the_fixed_tabs(self):
        pass  # the flow itself runs in TestContinue

    def test_skips(self):
        first, second = self.folders
        md = self.root / "Needs-Attention" / second / "job.md"
        text = md.read_text(encoding="utf-8")
        md.write_text(text.replace("**Run:** 2026-10-23 | Needs Attention | BLOCKED at: Workday sign-in",
                                   "**Run:** 2026-10-23 | Needs Attention | CHECK THIS JOB NOW: the page shows a submission message"),
                      encoding="utf-8")
        start = self.aa_json("start", "--continue")
        md.write_text(text, encoding="utf-8")
        self.assertEqual(start["count"], 1)
        self.assertIn("submission message", start["skipped"][0]["reason"])
        # a tab that two jobs name goes to neither (a restart can mix up the ids)
        md.write_text(text.replace("Tab 702 in the run window", "Tab 701 in the run window"), encoding="utf-8")
        self.aa_json("start", "--continue")
        self.master_setup(MARKER_ROBOTS)
        plan = self.aa_json("adopt-plan")
        self.assertEqual(plan["jobs"], 2)  # the page brings back every parked tab; the jobs are matched afterwards
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA adopted 1] robots.txt",'
                  '"url":"https://www.linkedin.com/robots.txt"},{"tabId":701,"url":"https://x.myworkdayjobs.com/a"}],'
                  '"tabGroupId":7}\n', M, "M1")
        out = self.aa_json("adopted")
        md.write_text(text, encoding="utf-8")
        # both jobs name tab 701 and fit its page equally well: it goes to neither (4.6)
        self.assertEqual((out["count"], out["left_alone"]), (0, 2))


class TestContinueCustomTab(TestContinue):
    """4.6: a tab that the candidate put in the group "Needs Attention" is found by its page, not by a tab id."""

    def test_continue_in_the_fixed_tabs(self):
        pass  # the flow itself runs in TestContinue

    def test_custom_tabs(self):
        first, second = self.folders
        md = self.root / "Needs-Attention" / second / "job.md"
        text = md.read_text(encoding="utf-8")
        md.write_text(text.replace("URL: https://x.myworkdayjobs.com/a", "URL: https://y.example.com/apply"), encoding="utf-8")
        self.aa_json("start", "--continue")
        md.write_text(text, encoding="utf-8")
        self.master_setup(MARKER_ROBOTS)
        self.assertTrue(self.aa_json("adopt-plan")["url"])
        # 701: after a restart the old id is another page (not job 1's host): the candidate's own tab, no job takes it.
        # 777, 778: tabs the candidate opened for job 2 and job 1, named in no note. 888: an unrelated tab.
        tabs = [(49, "[AA adopted 4] robots.txt", "https://www.linkedin.com/robots.txt"),
                (701, "Docs", "https://other.example/page"), (777, "Apply", "https://y.example.com/apply/step2"),
                (778, "Apply", "https://x.myworkdayjobs.com/b"), (888, "News", "https://example.org/")]
        context = ('{"availableTabs":[' + ",".join(f'{{"tabId":{i},"title":"{t}","url":"{u}"}}' for i, t, u in tabs)
                   + '],"tabGroupId":7}\n')
        self.post("tabs_context_mcp", {}, context, M, "M1")
        adopted = self.aa_json("adopted")
        self.assertEqual((adopted["count"], adopted["left_alone"]), (2, 0))
        self.assertEqual(self.aa_json("assign", "--continue")["queued"], 2)
        prompts = {w["folder"]: w["prompt"] for w in self.aa_json("next")["workers"]}
        self.assertTrue(prompts[first].startswith(f"folder={first} tab=778 brief="))
        self.assertTrue(prompts[second].startswith(f"folder={second} tab=777 brief="))
        self.assertEqual(self.state()["extra_tabs"], [701, 888])
        # a tab of no job is never closed, and goes back to the group "Needs Attention" with the others
        self.assertEqual(self.aa_json("cleanup-plan"), {"close": [49], "keep": [701, 777, 778, 888]})
        state = self.state()
        state["status"] = "done"
        (self.run_dir() / "state.json").write_text(json.dumps(state))
        self.assertEqual(self.aa_json("group-plan")["groups"], {"Needs Attention": 2})
        self.assertIn("Extra tabs: 2", self.aa("finish"))


class TestMatchTabs(unittest.TestCase):
    """match_tabs: a tab a note names counts only when its page fits; the candidate's own tabs by host and path."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS))
        import aa  # noqa: E402

        cls.match = staticmethod(aa.match_tabs)

    def job(self, tab, *urls):
        return {"prev_tab": tab, "urls": list(urls)}

    def tab(self, i, url):
        return {"id": i, "url": url}

    def test_rules(self):
        li = "https://www.linkedin.com/jobs/view/"
        jobs = {"a": self.job(5, "https://x.com/1", li + "11/"), "b": self.job(None, "https://y.com/2", li + "22/")}
        # the named tab, on its host: matched; a job without a note: matched by host
        self.assertEqual(self.match(jobs, [self.tab(5, "https://x.com/z"), self.tab(9, "https://y.com/q")]), {"a": 5, "b": 9})
        # the named tab shows another page now (a Chrome restart): not job a's, and job a has no other tab
        self.assertEqual(self.match(jobs, [self.tab(5, "https://z.com/")]), {})
        # two tabs on one job's host, or one tab that two jobs fit: no match
        self.assertEqual(self.match({"b": jobs["b"]}, [self.tab(8, "https://y.com/a"), self.tab(9, "https://y.com/b")]), {})
        same = {"a": self.job(None, "https://x.com/p"), "b": self.job(None, "https://x.com/p")}
        self.assertEqual(self.match(same, [self.tab(8, "https://x.com/p")]), {})
        # two jobs name one tab: only a job whose host fits gets it; when both fit, neither
        both = {"a": self.job(5, "https://x.com/1"), "b": self.job(5, "https://y.com/2")}
        self.assertEqual(self.match(both, [self.tab(5, "https://x.com/1")]), {"a": 5})
        both["b"] = self.job(5, "https://x.com/1")
        self.assertEqual(self.match(both, [self.tab(5, "https://x.com/1")]), {})
        # a shared ATS host: the path breaks the tie (the company and the job id), also after a redirect deeper in the site
        gh = {"a": self.job(None, "https://ats.io/acme/jobs/1"), "b": self.job(None, "https://ats.io/beta/jobs/2")}
        tabs = [self.tab(8, "https://ats.io/beta/jobs/2/apply"), self.tab(9, "https://ats.io/acme/jobs/1/apply")]
        self.assertEqual(self.match(gh, tabs), {"a": 9, "b": 8})
        # the paths tie: the company in the tab title breaks it, with or without a legal suffix
        co = {"a": {**self.job(None, "https://ats.io/jobs"), "company": "Acme, Inc."},
              "b": {**self.job(None, "https://ats.io/jobs"), "company": "Beta GmbH"}}
        tabs = [{**self.tab(8, "https://ats.io/jobs"), "title": "Beta | Careers"},
                {**self.tab(9, "https://ats.io/jobs"), "title": "Senior Dev - Acme"}]
        self.assertEqual(self.match(co, tabs), {"a": 9, "b": 8})
        # a tab with no sign but its host still goes to the one job left (a matched tab leaves the round)
        tabs = [self.tab(8, "https://ats.io/acme/jobs/1/apply"), self.tab(9, "https://ats.io/other")]
        self.assertEqual(self.match(gh, tabs), {"a": 8, "b": 9})
        # a title of another company decides nothing, and a tie stays open
        tabs = [{**self.tab(8, "https://ats.io/jobs"), "title": "Gamma"}, {**self.tab(9, "https://ats.io/jobs"), "title": "Delta"}]
        self.assertEqual(self.match(co, tabs), {})
        # a LinkedIn page matches by its job id, not by its host
        self.assertEqual(self.match({"a": jobs["a"]}, [self.tab(7, li + "22/apply"), self.tab(8, li + "11/apply")]), {"a": 8})
        # a job that moved to a host none of its pages name: the one free tab with its company in the title
        moved = {"a": {**self.job(None, li + "11/"), "company": "Acme, Inc."}}
        tabs = [{**self.tab(8, "https://new-ats.io/x"), "title": "Apply | Acme Careers"}, {**self.tab(9, "https://z.io"), "title": "Other"}]
        self.assertEqual(self.match(moved, tabs), {"a": 8})
        # two tabs carry the name: no guess
        tabs.append({**self.tab(7, "https://q.io"), "title": "Acme jobs"})
        self.assertEqual(self.match(moved, tabs), {})


class TestSelection(Base):
    """`start --selected` and `start --top-k-match`: which jobs a run queues."""

    def setUp(self):
        # These tests change tracker rows, so each one gets its own workspace (Base shares one per class).
        self.root = make_project(self.jobs)
        self.env = {**self.env, "OHMYJOB_WORKSPACE": str(self.root)}

    def tearDown(self):
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def set_score(self, folder: str, score) -> None:
        from tracker import Tracker

        tracker = Tracker(self.root / "Job_Tracker.xlsx")
        for row in tracker.rows():
            if row["_key"] == folder.split("_")[0]:
                tracker.set_cell(row["_index"], "Match Score", score)
        tracker.save()

    def test_selected_queues_only_the_named_folders(self):
        start = self.aa_json("start", "--selected", "Lumenfield")
        self.assertEqual([j["folder"] for j in start["jobs"]], [LINKEDIN_JOB])

    def test_selected_takes_several_entries_in_the_order_typed(self):
        start = self.aa_json("start", "--selected", "Quillon, Lumenfield")
        self.assertEqual([j["folder"] for j in start["jobs"]], [INDEED_JOB, LINKEDIN_JOB])

    def test_selected_reports_no_match_and_ambiguous(self):
        start = self.aa_json("start", "--selected", "Nothing-Here, s, Lumenfield")
        reasons = {s["job"]: s["reason"] for s in start["skipped"]}
        self.assertEqual(reasons["Nothing-Here"], "no folder matches")
        self.assertTrue(reasons["s"].startswith("ambiguous: "), reasons)
        self.assertEqual([j["folder"] for j in start["jobs"]], [LINKEDIN_JOB])

    def test_selected_refuses_a_job_that_is_not_at_resume_built(self):
        from tracker import Tracker

        tracker = Tracker(self.root / "Job_Tracker.xlsx")
        for row in tracker.rows():
            if row["_key"] == LINKEDIN_JOB.split("_")[0]:
                tracker.set_status(row["_index"], "Pending Review", None)
        tracker.save()
        start = self.aa_json("start", "--selected", "Lumenfield")
        self.assertEqual(start["count"], 0)
        self.assertEqual(start["skipped"][0]["reason"], "not at Resume Built")

    def test_top_k_keeps_the_best_match_scores(self):
        self.set_score(LINKEDIN_JOB, 9)
        self.set_score(INDEED_JOB, 4)
        start = self.aa_json("start", "--top-k-match", "1")
        self.assertEqual([j["folder"] for j in start["jobs"]], [LINKEDIN_JOB])
        start = self.aa_json("start", "--top-k-match", "2")
        self.assertEqual([j["folder"] for j in start["jobs"]], [LINKEDIN_JOB, INDEED_JOB])

    def test_top_k_puts_a_job_without_a_score_last(self):
        self.set_score(LINKEDIN_JOB, None)
        self.set_score(INDEED_JOB, 1)
        start = self.aa_json("start", "--top-k-match", "1")
        self.assertEqual([j["folder"] for j in start["jobs"]], [INDEED_JOB])

    def test_the_options_are_checked_before_anything_is_written(self):
        for args, text in (
            (("--continue", "--selected", "Lumenfield"), "--continue takes no --selected"),
            (("--continue", "--top-k-match", "2"), "--continue takes no --selected"),
            (("--selected", "Lumenfield", "--top-k-match", "2"), "give --selected or --top-k-match, not both"),
            (("--top-k-match", "0"), "whole number of 1 or more"),
            (("--top-k-match", "abc"), "whole number of 1 or more"),
        ):
            proc = self.run_aa("start", *args)
            self.assertNotEqual(proc.returncode, 0, args)
            self.assertIn(text, proc.stdout + proc.stderr, args)


class TestContinueRetry(TestContinue):
    """Without the extension's tag on the home tab, 'adopted' stops after 2 RETRYs."""

    def test_continue_in_the_fixed_tabs(self):
        pass  # the flow itself runs in TestContinue

    def test_retry_limit(self):
        self.aa_json("start", "--continue")
        self.master_setup(MARKER_ROBOTS)
        self.aa_json("adopt-plan")
        self.assertTrue(self.aa("adopted").startswith("RETRY"))
        self.assertTrue(self.aa("adopted").startswith("RETRY"))
        self.assertIn("reload it in chrome://extensions", self.run_aa("adopted").stdout)


class TestQuestions(Base):
    """A worker never guesses a fact about the candidate: it stops on the question; the candidate answers; Profile.md keeps the answer."""

    def setUp(self):
        (self.root / ".ohmyjob" / "questions.json").unlink(missing_ok=True)  # each test starts with no waiting question

    def report(self, folder: str, tab: int, **fields) -> dict:
        return {"folder": folder, "tab": tab, "status": "blocked", "where": "Application questions", "final_button": "",
                "url": "https://x.example/apply", "filled": [], "uploaded": None, "drafted": [], "open": [],
                "declarations": [], "blocker": "questions for the candidate", "questions": [], **fields}

    def test_questions_wait_for_the_candidate_and_go_into_profile(self):
        self.aa_json("start")
        self.master_setup()
        (folder, t1, _b1), (folder2, t2, _b2) = self.assign(801, 802)
        for agent, tab in (("Q1", t1), ("Q2", t2)):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://x.example/apply"}, W, agent))
        # the blocker "questions for the candidate" needs the questions (the details are in test_question_details)
        self.assertDenied(self.handback(json.dumps(self.report(folder, t1)), agent_id="Q1"), "list the questions")
        question = "How many years of Go do you have? [0-1, 2-3, 4+]"
        self.assertAllowed(self.handback(json.dumps(self.report(folder, t1, questions=[question])), agent_id="Q1"))
        # the same question from another job, and one that Profile.md answers already (notice period): asked once
        known = "Notice period?"
        self.assertAllowed(self.handback(json.dumps(self.report(folder2, t2, questions=[question.upper(), known])),
                                         agent_id="Q2"))
        summary = self.aa("finish")
        self.assertIn("Questions for the candidate: 1 waiting", summary)
        self.assertTrue((self.root / "Needs-Attention" / folder / "job.md").exists())
        self.assertIn("Question for you", (self.root / "Needs-Attention" / folder / "job.md").read_text())
        waiting = self.aa_json("questions")["questions"]
        self.assertEqual([q["q"] for q in waiting], [question])
        self.assertEqual(len(waiting[0]["jobs"]), 2)
        # answers: an unknown question is refused; an empty answer keeps it waiting; a real one goes into Profile.md
        self.assertTrue(self.aa("answer", "--json", json.dumps([{"q": "Something else?", "a": "x"}])).startswith("RETRY"))
        self.assertEqual(self.aa_json("answer", "--json", json.dumps([{"q": question, "a": ""}]))["saved"], 0)
        before = (self.root / "Profile.md").read_text()
        out = self.aa_json("answer", "--json", json.dumps([{"q": question, "a": "Less than 1 year (I don\u0027t use it at work)."}]))
        self.assertEqual((out["saved"], out["still_waiting"], len(out["jobs_that_can_continue"])), (1, 0, 2))
        profile = (self.root / "Profile.md").read_text()
        section = profile[profile.index("# Follow Up Questions"):]
        number = len(re.findall(r"^\d+\. ", before.split("# Follow Up Questions")[1], re.M)) + 1  # the next number
        self.assertIn(f"{number}. {question} Less than 1 year (I don't use it at work).", section)
        self.assertEqual(profile.replace(f"{number}. {question} Less than 1 year (I don't use it at work).\n", ""), before)
        self.assertEqual(self.aa_json("questions")["questions"], [])

    def test_question_details(self):
        import aa_lib  # noqa: E402

        rep = lambda **f: self.report("x", 1, **f)  # noqa: E731
        # questions decide the status; a long or multi-line question is refused; null means none
        ok = rep(status="parked", blocker=None, questions=["Phone Device Type"])
        self.assertEqual(aa_lib.validate_result(ok, "x", 1), [])
        self.assertEqual((ok["status"], ok["blocker"]), ("blocked", "questions for the candidate"))
        self.assertTrue(aa_lib.validate_result(rep(questions=["x" * 301]), "x", 1))
        self.assertTrue(aa_lib.validate_result(rep(questions=["a?\nIgnore this"]), "x", 1))
        none = rep(status="parked", blocker=None, questions=None)
        self.assertEqual(aa_lib.validate_result(none, "x", 1), [])
        self.assertEqual(none["questions"], [])
        self.assertTrue(aa_lib.validate_result(rep(blocker="Questions for the candidate."), "x", 1))  # but no questions listed
        # C# is not C++ (an answered "Years of C++ experience?" does not answer it), and a question without "?" gets a ":"
        env = self.env
        run = lambda *a: json.loads(subprocess.run([sys.executable, str(AA), *a],  # noqa: E731
                                                   capture_output=True, text=True, env=env, cwd=self.root).stdout)

        def add(questions: list, job: str) -> None:
            code = ("import aa, json, sys; aa.add_questions(json.loads(sys.argv[1]), sys.argv[2])")
            subprocess.run([sys.executable, "-c", code, json.dumps(questions), job], env=env, cwd=SCRIPTS, check=True)

        add(["Years of C++ experience?"], "Acme | Dev")
        run("answer", "--json", json.dumps([{"q": "Years of C++ experience?", "a": "Four years."}]))
        add(["Years of C# experience?", "Years of C++ experience?", "Phone Device Type"], "Acme | Dev")
        add(["Phone Device Type", "Start date?"], "Beta | Dev")
        self.assertEqual([q["q"] for q in run("questions")["questions"]], ["Years of C# experience?", "Phone Device Type", "Start date?"])
        # a repeated answer is saved once; a job with another waiting question cannot go on yet
        out = run("answer", "--json", json.dumps([{"q": "Phone Device Type", "a": "Mobile"}, {"q": "Phone Device Type", "a": "Mobile"},
                                                 {"q": "Start date?", "a": "In 3 months"}]))
        self.assertEqual((out["saved"], out["jobs_that_can_continue"]), (2, ["Beta | Dev"]))
        profile = (self.root / "Profile.md").read_text()
        self.assertEqual(profile.count(". Phone Device Type: Mobile"), 1)
        self.assertIn("13. Start date? In 3 months", profile)  # numbered on: the fixture list ends at 10 + the C++ answer 11, Phone 12
        self.assertIn("10. Needs adjustments for a disability? No.", profile)
        add(["Phone Device Type"], "Gamma | Dev")  # answered now: not asked again
        self.assertEqual([q["q"] for q in run("questions")["questions"]], ["Years of C# experience?"])


class TestNumberedAnswers(Base):
    def test_numbered_list_parses_and_old_dash_lists_still_count(self):
        code = ("import aa, workspace as W; from pathlib import Path\n"
                "p = W.PROFILE; p.write_text('# Follow Up Questions\\n\\n- Old one? A.\\n3) Paren one? B.\\n7. Dot one? C.\\n')\n"
                "print(sorted(aa.answered_questions()))\n"
                "aa.save_answers([('New one?', 'D.'), ('Other', 'E.')])\n"
                "print(p.read_text())")
        out = subprocess.run([sys.executable, "-c", code], env=self.env, cwd=SCRIPTS, capture_output=True, text=True, check=True).stdout
        self.assertIn("['dot one', 'old one', 'paren one']", out)
        self.assertTrue(out.rstrip().endswith("7. Dot one? C.\n8. New one? D.\n9. Other: E."), out)


class TestGaps(Base):
    """`gaps` names the countries of the jobs that `start` would queue for which Work Authorization has no line;
    `authorize` adds the line."""
    jobs = (LINKEDIN_JOB,)

    def setUp(self):
        self.profile = self.root / "Profile.md"
        self.job_md = self.root / "Applications" / LINKEDIN_JOB / "job.md"
        for path in (self.profile, self.job_md):
            self.addCleanup(path.write_text, path.read_text(encoding="utf-8"), encoding="utf-8")

    def set_up(self, location: str, section: str) -> None:
        text = self.job_md.read_text(encoding="utf-8")
        self.job_md.write_text(re.sub(r"(- Location: ).*", lambda m: m.group(1) + location, text), encoding="utf-8")
        profile = self.profile.read_text(encoding="utf-8")
        head, rest = profile.split("# Work Authorization", 1)
        tail = rest[rest.index("\n# "):] if "\n# " in rest else ""
        self.profile.write_text(head + section + tail, encoding="utf-8")

    def test_a_country_with_no_line_is_a_gap(self):
        self.set_up("Dublin, Ireland", "# Work Authorization\n\n- Germany: citizen. x\n- Security clearance: none.\n")
        self.assertEqual(self.aa_json("gaps")["gaps"], [{"country": "Ireland", "jobs": ["Lumenfield Logistics | Operations Analyst"]}])
        self.assertEqual(self.aa_json("gaps", "--selected", "Lumenfield")["gaps"][0]["country"], "Ireland")

    def test_an_eu_line_covers_the_country(self):
        self.set_up("Dublin, Ireland", "# Work Authorization\n\n- EU: citizen. x\n")
        self.assertEqual(self.aa_json("gaps")["gaps"], [])

    def test_no_clear_country_is_no_gap(self):
        self.set_up("Remote", "# Work Authorization\n\n- Germany: citizen. x\n")
        self.assertEqual(self.aa_json("gaps")["gaps"], [])

    def test_authorize_writes_a_line_that_the_check_reads(self):
        import work_auth as WA

        self.set_up("Dublin, Ireland", "# Work Authorization\n\n- Germany: citizen. x\n- Security clearance: none.\n")
        out = self.aa("authorize", "--json", json.dumps([{"country": "Ireland", "status": "needs sponsorship", "words": "Need a visa."}]))
        self.assertEqual(json.loads(out), {"saved": 1})
        text = self.profile.read_text(encoding="utf-8")
        self.assertIn("- Ireland: needs sponsorship. Need a visa.\n- Security clearance: none.", text)
        self.assertEqual(WA.candidate_level(WA.profile_rows(text), "IE")[0], WA.NEEDS)
        self.assertEqual(self.aa_json("gaps")["gaps"], [])

    def test_authorize_creates_a_missing_section(self):
        profile = self.profile.read_text(encoding="utf-8")
        self.profile.write_text(profile.split("# Work Authorization")[0].rstrip() + "\n", encoding="utf-8")
        self.aa("authorize", "--json", json.dumps([{"country": "Ireland", "status": "citizen", "words": ""}]))
        self.assertTrue(self.profile.read_text(encoding="utf-8").endswith("# Work Authorization\n\n- Ireland: citizen.\n"))

    def test_authorize_refuses_a_bad_status_a_duplicate_and_bad_json(self):
        self.set_up("Dublin, Ireland", "# Work Authorization\n\n- Germany: citizen. x\n")
        before = self.profile.read_text(encoding="utf-8")
        for data in ('[{"country": "Ireland", "status": "maybe", "words": ""}]',
                     '[{"country": "Germany", "status": "citizen", "words": ""}]',
                     '[{"country": "Ireland", "status": "citizen"}, {"country": "ireland", "status": "authorized"}]',
                     "not json"):
            self.assertTrue(self.aa("authorize", "--json", data).startswith("RETRY:"), data)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), before)


class TestStagger(Base):
    def test_first_page_loads_are_spaced_out(self):
        """Each worker's first navigation waits for its own start slot; later navigations do not wait."""
        import time as _time

        self.aa_json("start")
        self.master_setup()
        self.assign(101, 102)
        env = {**self.env, "AA_STAGGER": "1.5"}

        def nav(tab: int, agent: str) -> float:
            started = _time.time()
            proc = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "navigate", "agent_type": W,
                                       "agent_id": agent, "tool_input": {"tabId": tab, "url": "https://www.linkedin.com/jobs/view/1"}},
                             env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return _time.time() - started

        self.assertLess(nav(101, "S1"), 1.0)      # the first worker starts at once
        self.assertGreater(nav(102, "S2"), 1.0)   # the second one waits for its slot
        self.assertLess(nav(101, "S1"), 1.0)      # a later page load of a worker does not wait


class TestPageReads(Base):
    def test_worker_gets_a_compact_page(self):
        """The whole page is kept for the refs; the worker gets long links shortened and long option lists cut."""
        self.aa_json("start")
        self.master_setup()
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        page = "\n".join(['dialog "Apply" [ref_1]', ' combobox "Country" [ref_2]']
                         + [f'  option "Country {i}" [ref_{100 + i}]' for i in range(200)]
                         + [f' link [ref_{400 + i}] href="https://www.linkedin.com/ambry/?x={"A" * 800}"' for i in range(30)]
                         + [' generic "Upload resume" [ref_500]', ' textbox [ref_501] type="file"'])
        proc = self.hook("snapshot", {"hook_event_name": "PostToolUse", "tool_name": C + "read_page",
                                      "tool_input": {"tabId": 101, "filter": "all"},
                                      "tool_response": [{"type": "text", "text": page + "\n\nTab Context:\n- tab 101"}],
                                      "agent_type": W, "agent_id": "A1"})
        out = json.loads(proc.stdout)["hookSpecificOutput"]["updatedToolOutput"][0]["text"]
        self.assertLess(len(out), 6000)
        self.assertIn('href="https://www.linkedin.com/ambry/?…"', out)
        self.assertIn("188 more options", out)
        self.assertIn('textbox [ref_501] type="file"', out)
        self.assertTrue(out.endswith("Tab Context:\n- tab 101"))
        # every ref of the whole page is known to the guard, also the options that the worker does not see
        pdf = self.state()["jobs"][self.state()["queue"][0]]["pdf"]
        self.assertAllowed(self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_501", "paths": [pdf]}, W, "A1"))
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_2", "value": "Country 199"}, W, "A1"))
        self.assertAllowed(self.click(101, "ref_299", "A1", "Chooses Country 199 in the list"))
        self.post("read_page", {"tabId": 101, "filter": "interactive"},
                  'link "Download" [ref_7] href="https://www.linkedin.com/ambry/?x=' + "B" * 300 + '"\n'
                  'link "Apply on company website" [ref_8] href="https://www.linkedin.com/safety/go/?url=' + "C" * 300 + '"', W, "A1")
        proc = self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/ambry/?…"}, W, "A1")
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["url"],
                         "https://www.linkedin.com/ambry/?x=" + "B" * 300)
        # a shortened address never expands to a link that is not http or https
        self.post("read_page", {"tabId": 101, "filter": "interactive"},
                  'link "Apply now" [ref_2] href="javascript:document.forms[0].submit()"', W, "A1")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": "javascript…"}, W, "A1"), "shortened")
        # a page without refs (a "submitted" message) replaces the saved page: the end check must see it
        self.post("read_page", {"tabId": 101, "filter": "interactive"},
                  "Your application has been submitted.\n\nTab Context:\n- tab 101", W, "A1")
        self.assertIn("has been submitted", (self.run_dir() / "snap" / "tab_101.txt").read_text())
        # two different links behind the same shortened address: the worker must read the link itself
        self.post("read_page", {"tabId": 101, "filter": "interactive"},
                  'link [ref_7] href="https://www.linkedin.com/ambry/?x=' + "B" * 300 + '"\n'
                  'link [ref_9] href="https://www.linkedin.com/ambry/?x=' + "D" * 300 + '"', W, "A1")
        self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/ambry/?…"}, W, "A1"), "shortened")
        self.assertIn("C" * 300, L_compact('link [ref_8] href="https://www.linkedin.com/safety/go/?url=' + "C" * 300 + '"'))
        # a long form under one container: the cut lists the elements below it
        form = "\n".join(['main [ref_1]'] + [f' textbox "Question {i}" [ref_{10 + i}]' for i in range(800)])
        cut = L_compact(form)
        self.assertIn("the page is cut here", cut)
        self.assertIn('textbox "Question 799" [ref_809]', cut)


class TestLanguages(Base):
    def test_buttons_in_other_languages(self):
        self.aa_json("start")
        self.master_setup()
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://lavora.esempio.it/candidatura"}, W, "L1"))
        self.page(101, "constructed_form_page_it.txt", agent_id="L1")
        before = len(self.judge_calls())
        # word lists, no Haiku check: Italian, German and Japanese final buttons
        self.assertDenied(self.click(101, "ref_149", "L1"), "final control")
        self.assertDenied(self.click(101, "ref_159", "L1"), "final control")
        self.assertDenied(self.click(101, "ref_162", "L1"), "final control")    # 送信する
        self.assertAllowed(self.click(101, "ref_150", "L1", "Goes to the next step"))   # Avanti
        self.assertAllowed(self.click(101, "ref_161", "L1", "Goes to the next step"))   # Weiter
        self.assertDenied(self.click(101, "ref_146", "L1"), "cookie")
        self.assertDenied(self.click(101, "ref_151", "L1"), "Haiku check blocked")
        self.assertAllowed(self.click(101, "ref_152", "L1", "Opens Sign in with Google"))
        self.assertDenied(self.click(101, "ref_153", "L1"), "deletes")
        self.assertDenied(self.click(101, "ref_158", "L1"), "permission")
        self.assertAllowed(self.click(101, "ref_157", "L1", "Opens the privacy notice"))  # a link to a page
        self.assertAllowed(self.click(101, "ref_148", "L1", "Opens the application form"))  # apply before a fill
        self.assertAllowed(self.click(101, "ref_160", "L1", "Opens the application form"))  # 応募する: apply
        self.assertEqual(len(self.judge_calls()), before + 1, "the word lists decided these without Haiku, bar the sign-up name")
        # names that no list knows: the Haiku check (fake) decides, and its answer is cached
        self.assertAllowed(self.click(101, "ref_147", "L1", "Rejects all cookies"))      # Rifiuta tutti
        self.assertDenied(self.click(101, "ref_155", "L1"), "haiku check blocked")       # Trasmetti la domanda
        n = len(self.judge_calls())
        self.page(101, "constructed_form_page_it.txt", agent_id="L1")
        self.assertAllowed(self.click(101, "ref_147", "L1", "Rejects all cookies"))
        self.assertDenied(self.click(101, "ref_155", "L1"), "haiku check blocked")
        self.assertEqual(len(self.judge_calls()), n, "the second click used the cache")
        # a failed check blocks the click; after two failures the hook stops asking
        self.assertDenied(self.click(101, "ref_156", "L1"), "could not check")
        self.assertDenied(self.click(101, "ref_156", "L1"), "could not check")
        n = len(self.judge_calls())
        self.assertDenied(self.click(101, "ref_156", "L1"), "could not check")
        self.assertEqual(len(self.judge_calls()), n)
        # after the first fill, Apply words are final too
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_141", "value": "Alex"}, W, "L1"))
        self.assertDenied(self.click(101, "ref_148", "L1"), "showed an application form")
        self.assertDenied(self.click(101, "ref_160", "L1"), "showed an application form")


class TestApplyGate(Base):
    """Apply opens the form until the tab shows one, and is final after ."""

    def read(self, tab: int, page: str, agent: str) -> None:
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, page + f"\n\nTab Context:\n- tab {tab}", W, agent)

    def test_apply_before_and_after_the_form(self):
        self.aa_json("start")
        self.master_setup()
        (folder, _t1, _b1), (folder2, _t2, _b2) = self.assign(501, 502)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 501, "url": "https://app.jobs.example/home"}, W, "G1"))
        # a job search box is not an application form (a careers site): Apply still opens the form
        self.read(501, 'textbox "Search jobs..." [ref_11] type="text"\nbutton "Apply" [ref_58] type="button"\n'
                       + "".join(f'textbox [ref_{90 + i}] type="hidden"\n' for i in range(5)), "G1")
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 501, "ref": "ref_11", "value": "Operations Analyst"}, W, "G1"))
        self.assertAllowed(self.click(501, "ref_58", "G1", "Opens the application form"))
        # the same button again needs a new read: its name can change after a click (Next becomes Submit)
        self.assertDenied(self.click(501, "ref_58", "G1", "Opens the application form"), "read_page first")
        # a form that the site filled already: no fill by the worker, but Apply is final
        self.read(501, 'textbox "First name" [ref_1]\ntextbox "Email" [ref_2] type="email"\ntextbox "Phone" [ref_3]\n'
                       'checkbox "I confirm I have read the above." [ref_4] type="checkbox"\n'
                       'option "Acknowledge/Confirm" [ref_5]\nbutton "Next" [ref_6] type="button"\n'
                       'button "Apply" [ref_7] type="submit"', "G1")
        self.assertDenied(self.click(501, "ref_7", "G1", "Opens the next page"), "showed an application form")
        # declaration choices with a final word are not final controls; ticking twice is fine
        self.assertAllowed(self.click(501, "ref_4", "G1", "Ticks the declaration box"))
        self.assertAllowed(self.click(501, "ref_4", "G1", "Ticks the declaration box"))
        self.assertAllowed(self.click(501, "ref_5", "G1", "Selects Acknowledge/Confirm"))
        self.read(501, 'textbox "First name" [ref_1]\ntextbox "Email" [ref_2] type="email"\ntextbox "Phone" [ref_3]\n'
                       'checkbox "I confirm I have read the above." [ref_4] type="checkbox"\n'
                       'option "Acknowledge/Confirm" [ref_5]\nbutton "Next" [ref_6] type="button"\n'
                       'button "Apply" [ref_7] type="submit"', "G1")
        # a double click presses a button twice
        self.assertDenied(self.guard(C + "computer", {"tabId": 501, "action": "double_click", "ref": "ref_6",
                                                      "action_summary": "Goes to the next step"}, W, "G1"), "left_click")
        self.assertAllowed(self.click(501, "ref_6", "G1", "Goes to the next step"))
        # the next step has no fields: Apply stays final, and the worker parks there
        self.read(501, 'button "Back" [ref_8]\nbutton "Apply" [ref_9] type="submit"', "G1")
        self.assertDenied(self.click(501, "ref_9", "G1", "Opens the next page"), "showed an application form")
        report = {"folder": folder, "tab": 501, "status": "parked", "where": "Review", "final_button": "Apply",
                  "url": "https://app.jobs.example/apply", "filled": [], "uploaded": None, "drafted": [], "open": [],
                  "declarations": [], "blocker": None}
        self.assertAllowed(self.handback(json.dumps(report), agent_id="G1"))
        # parked at an Apply button with no form seen: not the final step, so not Pending Review 
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 502, "url": "https://experts.example/apply"}, W, "G2"))
        self.read(502, 'button "Apply Now" [ref_25] type="button"', "G2")
        report2 = {**report, "folder": folder2, "tab": 502, "final_button": "Apply Now", "url": "https://experts.example/apply"}
        self.assertAllowed(self.handback(json.dumps(report2), agent_id="G2"))
        summary = self.aa("finish")
        self.assertIn(f"PARKED {folder} ", summary)
        self.assertIn("before the application form", summary)
        self.assertTrue((self.root / "Pending-Review" / folder).is_dir())
        self.assertTrue((self.root / "Needs-Attention" / folder2).is_dir())
        self.assertIn("before the application form", (self.root / "Needs-Attention" / folder2 / "job.md").read_text())


class TestBadReports(Base):
    def test_bad_reports_end_in_needs_attention(self):
        self.aa_json("start")
        (folder, _t1, _b1), (folder2, _t2, _b2) = self.assign(301, 302)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 301, "url": "https://www.linkedin.com/jobs/view/1"}, W, "R1"))
        # two bad hand-backs are refused; the third is saved as invalid, so the run goes on
        for _ in range(2):
            self.assertDenied(self.handback("no json", agent_id="R1"), "one json object")
        self.assertAllowed(self.handback("no json", agent_id="R1"))
        # the second worker never reports
        report = self.aa("finish")
        self.assertIn("Pending-Review: 0 | Needs-Attention: 2 | Deleted (closed): 0 | not finished: 0", report)
        self.assertIn("not valid", report)
        self.assertIn("returned no result", report)
        self.assertTrue((self.root / "Needs-Attention" / folder).is_dir())
        self.assertTrue((self.root / "Needs-Attention" / folder2).is_dir())


class TestGoogle(Base):
    def test_job_board_signup_wall_takes_the_google_option(self):
        """A board whose Apply link goes to its sign-up page: navigate there, then only the Google option passes."""
        self.aa_json("start")
        self.assign(201, 202)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": "https://board.example.org/jobs/1"}, W, "G1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": "https://board.example.org/signup?redirect=%2Fjobs%2F1&reason=apply"}, W, "G1"))
        modal = ('dialog "Sign up to apply" [ref_1]\n button "Continue with Email" [ref_2] type="button"\n'
                 ' button "Sign up with Google" [ref_3] type="button"\n button "Continue with Google" [ref_4] type="button"\n'
                 ' button "Sign up" [ref_5] type="button"\n')
        self.post("read_page", {"tabId": 201}, modal, W, "G1")
        self.assertAllowed(self.click(201, "ref_3", "G1", "Opens Sign up with Google"))
        self.post("read_page", {"tabId": 201}, modal, W, "G1")
        self.assertAllowed(self.click(201, "ref_4", "G1", "Opens Continue with Google"))
        self.post("read_page", {"tabId": 201}, modal, W, "G1")
        self.assertDenied(self.click(201, "ref_5", "G1"), "Haiku check blocked")

    def test_sign_in_with_google(self):
        self.aa_json("start")
        self.assign(201, 202)
        click = self.click

        # the site's sign-in gateway: Google options yes, an email account no
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": "https://careers.example.com/login"}, W, "G1"))
        self.post("read_page", {"tabId": 201}, sample("constructed_signin_gateway.txt"), W, "G1")
        self.assertAllowed(click(201, "ref_81", "G1", "Opens Sign up with Google"))
        self.assertAllowed(click(201, "ref_82", "G1", "Opens Continue with Google"))
        self.assertDenied(click(201, "ref_83", "G1"), "Haiku check blocked")
        self.assertDenied(click(201, "ref_85", "G1"), "deletes")
        # Google in the same tab; the sign-in address asks for the basic profile only
        basic = ("https://accounts.google.com/o/oauth2/v2/auth?client_id=x&response_type=code"
                 "&scope=openid%20email%20profile&redirect_uri=https%3A%2F%2Fcareers.example.com%2Fcb")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": basic}, W, "G1"))
        # Google's account list: in the interactive view the account link has no name
        self.post("read_page", {"tabId": 201}, sample("constructed_google_chooser_interactive.txt"), W, "G1")
        self.assertDenied(self.guard(C + "computer", {"tabId": 201, "action": "type", "text": "x"}, W, "G1"), "google")
        self.assertDenied(self.guard(C + "form_input", {"tabId": 201, "ref": "ref_1", "value": "x"}, W, "G1"), "google")
        self.assertDenied(click(201, "ref_1", "G1", "Chooses the account"), "filter 'all'")
        # with filter "all" the name and the email address show inside the link
        self.post("read_page", {"tabId": 201, "filter": "all"}, sample("constructed_google_chooser_all.txt"), W, "G1")
        self.assertDenied(click(201, "ref_2", "G1"))                                   # Use another account
        self.assertDenied(click(201, "ref_17", "G1"))                                  # the name text alone
        self.assertDenied(click(201, "ref_22", "G1"), "account list")                  # another button
        self.assertAllowed(click(201, "ref_1", "G1", "Chooses the account"))          # the link that holds the email
        self.assertDenied(click(201, "ref_18", "G1"), "only one account click")
        # the email text itself is an account click too (a new Google visit starts the counters again)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": "https://careers.example.com/login"}, W, "G1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 201, "url": basic}, W, "G1"))
        self.post("read_page", {"tabId": 201, "filter": "all"}, sample("constructed_google_chooser_all.txt"), W, "G1")
        self.assertAllowed(click(201, "ref_18", "G1", "Chooses the account"))
        # the consent page, in Italian: one button, because the access is basic
        self.post("read_page", {"tabId": 201}, sample("constructed_google_consent_it.txt").replace("tabId 203", "tabId 201"),
                  W, "G1")
        self.assertAllowed(click(201, "ref_6", "G1", "Continues the Google sign-in"))
        self.assertDenied(click(201, "ref_6", "G1"), "already")

        # a sign-in that asks for Drive access: never (in any language)
        drive = ("https://accounts.google.com/o/oauth2/v2/auth?client_id=x&scope=openid%20"
                 "https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive&redirect_uri=https%3A%2F%2Fx.example%2Fcb")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": "https://x.example/login"}, W, "G2"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": drive}, W, "G2"))
        # an account entry with the email address in its own name is an account click too
        self.post("read_page", {"tabId": 202}, sample("constructed_google_chooser.txt").replace("tabId 201", "tabId 202"),
                  W, "G2")
        self.assertAllowed(click(202, "ref_3", "G2", "Chooses the account"))
        self.post("read_page", {"tabId": 202}, sample("constructed_google_consent_it.txt").replace("tabId 203", "tabId 202"),
                  W, "G2")
        self.assertDenied(click(202, "ref_6", "G2"), "more google access")
        # a page with choices for more access (granular consent): never
        self.post("read_page", {"tabId": 202}, sample("constructed_google_checkboxes.txt").replace("tabId 204", "tabId 202"),
                  W, "G2")
        self.assertDenied(click(202, "ref_6", "G2"))
        # a password or code page: never
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": "https://x.example/again"}, W, "G2"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": "https://accounts.google.com/v3/signin/challenge/pwd?x=1"}, W, "G2"))
        self.post("read_page", {"tabId": 202}, 'button "Next" [ref_9] type="button"\n', W, "G2")
        self.assertDenied(click(202, "ref_9", "G2"), "password")
        # Google without a scope in any seen address: the hook cannot see the access
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": "https://x.example/third"}, W, "G2"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 202, "url": "https://accounts.google.com/signin/oauth/id?part=x"}, W, "G2"))
        self.post("read_page", {"tabId": 202}, 'button "Cancel" [ref_5] type="button"\nbutton "Continue" [ref_6] type="button"\n',
                  W, "G2")
        self.assertDenied(click(202, "ref_6", "G2"), "cannot see")


class TestSigninTab(Base):
    def test_popup_opened_as_a_tab(self):
        self.aa_json("start")
        self.master_setup()
        self.assign(701, 702)
        click = self.click
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 701, "url": "https://careers.example.com/login"}, W, "P1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 702, "url": "https://www.linkedin.com/jobs/view/2"}, W, "P2"))
        # the worker clicked "Sign in with Google": the helper extension opened the pop-up as tab 777
        gsi = "https://accounts.google.com/gsi/select?client_id=x&ux_mode=popup&origin=https%3A%2F%2Fcareers.example.com"
        self.post("tabs_context_mcp", {}, json.dumps({"availableTabs": [
            {"tabId": 701, "title": "Login | Careers", "url": "https://careers.example.com/login"},
            {"tabId": 777, "title": "[AA from 701] Sign in - Google Accounts", "url": gsi},
            {"tabId": 778, "title": "[AA from 701] Sign in to your account", "url": "https://login.microsoftonline.com/common/oauth2/authorize"},
            {"tabId": 779, "title": "[AA from 49] Other", "url": "https://example.com/"},
        ], "tabGroupId": 7}) + "\n", W, "P1")
        tabs = self.state()["tabs"]
        self.assertEqual((tabs["777"]["role"], tabs["777"]["opener"]), ("signin", 701))
        self.assertNotIn("779", tabs)  # opened by a tab that is not a job tab
        # only the worker of tab 701 may use it, and only to read and click
        self.assertDenied(self.guard(C + "read_page", {"tabId": 777}, W, "P2"), "own tab")
        self.assertAllowed(self.guard(C + "read_page", {"tabId": 777}, W, "P1"))
        self.assertDenied(self.guard(C + "navigate", {"tabId": 777, "url": "https://example.com/"}, W, "P1"), "sign-in tab")
        self.assertDenied(self.guard(C + "read_page", {"tabId": 779}, W, "P1"), "own tab")
        chooser = ('main [ref_1]\n link "Test User test.user@example.com" [ref_3] href="#"\n'
                   ' link "Use another account" [ref_4] href="#"\n\nTab Context:\n- Executed on tabId: 777\n'
                   '- Available tabs:\n  • tabId 777: "[AA from 701] Sign in - Google Accounts" ("' + gsi + '")\n')
        self.post("read_page", {"tabId": 777}, chooser, W, "P1")
        self.assertDenied(self.guard(C + "form_input", {"tabId": 777, "ref": "ref_3", "value": "x"}, W, "P1"))
        self.assertAllowed(click(777, "ref_3", "P1", "Chooses the account"))
        self.post("read_page", {"tabId": 777}, 'button "Annulla" [ref_5] type="button"\nbutton "Continua" [ref_6] type="button"\n', W, "P1")
        self.assertAllowed(click(777, "ref_6", "P1", "Continues the Google sign-in"))   # /gsi/: basic profile
        # a sign-in pop-up of another site: not usable
        self.assertAllowed(self.guard(C + "read_page", {"tabId": 778}, W, "P1"))
        self.post("read_page", {"tabId": 778}, 'button "Accept" [ref_1] type="button"\n', W, "P1")
        self.assertDenied(click(778, "ref_1", "P1"), "not a google page")
        # the master closes sign-in tabs that stay open; job tabs stay
        self.aa("finish")
        self.post("tabs_context_mcp", {}, json.dumps({"availableTabs": [
            {"tabId": 49, "url": MARKER_FEED}, {"tabId": 701, "url": "https://careers.example.com/login"},
            {"tabId": 702, "url": "https://www.linkedin.com/jobs/view/2"}, {"tabId": 778, "url": "https://login.microsoftonline.com/x"},
        ], "tabGroupId": 7}) + "\n", M, "M1")
        plan = self.aa_json("cleanup-plan")
        self.assertEqual((sorted(plan["close"]), sorted(plan["keep"])), ([49, 778], [701, 702]))



class TestIconButton(Base):
    def test_named_google_icon_button_passes_and_nameless_stays_blocked(self):
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://signup.example.net/signup"}, W, "A1"))
        self.post("read_page", {"tabId": tab}, 'form [ref_1]\n label "Continue with Google" [ref_2]\n  button "Continue with Google" [ref_3] type="submit"\n'
                  'form [ref_4]\n label [ref_5]\n  button [ref_6] type="submit"\n', W, "A1")
        self.assertAllowed(self.click(tab, "ref_2", "A1", "Opens Google sign-in"))
        self.assertAllowed(self.click(tab, "ref_3", "A1", "Opens Google sign-in"))
        self.assertDenied(self.click(tab, "ref_6", "A1", "Opens sign-in"), "without a name")
        # after a fill, a Google name goes to the Haiku check (the fake judge blocks this name)
        self.post("read_page", {"tabId": tab}, 'textbox "Email" [ref_7]\nbutton "Continue with Google" [ref_8] type="submit"\n', W, "A1")
        self.assertAllowed(self.guard(C + "form_input", {"tabId": tab, "ref": "ref_7", "value": "a@b.c"}, W, "A1"))
        self.assertDenied(self.click(tab, "ref_8", "A1", "Opens Google sign-in"), "Haiku check blocked")
        # a worker cannot load the master's pages
        self.assertDenied(self.guard(C + "navigate", {"tabId": tab, "url": "https://www.linkedin.com/feed/#aa-popups-as-tabs"}, W, "A1"), "master only")

    def icon_page(self, tab, lines):
        self.post("read_page", {"tabId": tab}, lines, W, "A1")

    def test_an_icon_name_is_evidence_for_the_model_never_an_allow(self):
        """Issue 44: the extension names an icon-only button "[icon] <name>". The name is a guess, so it earns no
        allow of its own (not even SAFE), and a final or Apply word in it refuses nothing by itself: the Haiku check
        decides, and it is told. A delete word is the exception and keeps its refusal (see the delete test)."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Next" [ref_1] type="button"\nbutton "[icon] Envoyer" [ref_2] type="button"\n'
                            'button "[icon] Delete" [ref_3] type="button"\nbutton "Next" [ref_4] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.click(tab, "ref_4", "A1", "Goes to the next step"))   # the site's own "Next": SAFE
        self.assertEqual(len(self.judge_calls()), before)
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Goes to the next step"))   # an invented "Next": asked
        self.assertEqual(self.judge_calls()[-1]["name"], "Next")                      # the mark is not in the name
        # a final word on an invented name is not a refusal by itself: the model (fake) blocks "Envoyer"
        self.assertDenied(self.click(tab, "ref_2", "A1", "Opens the next screen"), "Haiku check blocked")
        self.assertEqual([c["name"] for c in self.judge_calls()[before:]], ["Next", "Envoyer"])

    def test_a_delete_word_refuses_a_guessed_name_too(self):
        """The one word rule that does NOT stand down for a guess. CLICK_PROMPT still contradicts itself on this
        word (judge.py blocks "delete or withdraw data" and allows "Remove" nine lines on), so the model is no gate
        for it yet; issue 45 owns that prompt. A mislabelled icon button costs a parked worker, which is
        recoverable, while a deleted record is not."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Delete" [ref_1] type="button"\n')
        before = len(self.judge_calls())
        self.assertDenied(self.click(tab, "ref_1", "A1", "Removes a row"), "deletes or withdraws data")
        self.assertEqual(len(self.judge_calls()), before, "a delete word is refused, not asked")
        # Why the extension writes no name over a title attribute or an img alt (bridge.js iconName): a guessed
        # name is not empty, so it takes the place of the line inside the button as the name, and the word rules
        # read the name. The delete word of the icon's own tooltip would be gone, as it is here.
        self.icon_page(tab, 'button "[icon] Trash" [ref_2] type="button"\n image "Delete" [ref_3]\n')
        self.assertAllowed(self.click(tab, "ref_2", "A1", "Removes a row"))
        self.assertEqual(self.judge_calls()[-1]["name"], "Trash")

    def test_a_submit_button_named_only_from_its_icon_stays_refused(self):
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Add" [ref_1] type="submit"\nbutton "[icon] Add" [ref_2] type="button"\n'
                            'button "Next" [ref_3] type="submit"\nbutton [ref_4] type="submit"\n')
        self.assertDenied(self.click(tab, "ref_1", "A1"), "submit button")
        self.assertAllowed(self.click(tab, "ref_2", "A1", "Adds another row"))
        self.assertAllowed(self.click(tab, "ref_3", "A1", "Goes to the next step"))   # a site-written name passes today
        self.assertDenied(self.click(tab, "ref_4", "A1"), "without a name")

    def test_the_icon_flag_is_part_of_the_cached_verdict_key(self):
        """A name the site wrote and Haiku called context-free is cached per host, whatever stands around it. That
        verdict must not stand in for the same name when the extension invented it."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        before = len(self.judge_calls())
        self.icon_page(tab, 'button "Salva bozza" [ref_1] type="button"\n')
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Saves a draft"))   # the fake answers context_dependent false
        self.icon_page(tab, 'link "Help" [ref_5]\nbutton "Salva bozza" [ref_1] type="button"\n')   # other surroundings
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Saves a draft"))
        self.assertEqual(len(self.judge_calls()), before + 1, "the short key served the second click")
        self.icon_page(tab, 'link "More" [ref_6]\nbutton "[icon] Salva bozza" [ref_1] type="button"\n')
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Saves a draft"))
        self.assertEqual(len(self.judge_calls()), before + 2, "the invented name is asked again")

    def test_a_refused_google_click_announces_no_signin(self):
        """The lease is taken below the Haiku check. The state alone cannot show that ordering: guard() reloads the
        file after a denial, so a lease taken before the refusal is rolled back wherever the call stands. What the
        ordering really decides is the paper trail, which is not rolled back -- a "lease-take" or "google-option"
        line for a click that was refused would tell the next reader that a sign-in had started."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Sign in with Google (blocked)" [ref_1] type="button"\n'
                            'button "[icon] Continue with Google" [ref_2] type="submit"\n')
        log = lambda: (self.run_dir() / "guard.log").read_text(encoding="utf-8")  # noqa: E731
        self.assertDenied(self.click(tab, "ref_1", "A1"), "Haiku check blocked")
        self.assertNotIn("careers.example.com", self.state().get("signin_hosts", {}))
        self.assertNotIn("lease-take", log())
        self.assertNotIn("google-option", log())
        # an invented Google name costs one model call, and a submit type does not refuse it
        self.assertAllowed(self.click(tab, "ref_2", "A1", "Opens Google sign-in"))
        self.assertEqual(self.judge_calls()[-1]["name"], "Continue with Google")
        self.assertEqual(self.state()["signin_hosts"]["careers.example.com"]["status"], "pending")
        self.assertIn("lease-take", log())
        self.assertIn("google-option", log())

    def test_a_guessed_consent_name_is_asked_as_well(self):
        """Issue 44: an invented name never earns an automatic allow, and the consent pass is no exception. The
        site-written "Accept and submit" is refused as a final control, so the same words guessed off a class
        ("accept-and-submit" as a test id) must at least reach the Haiku check."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Accept and submit" [ref_1] type="button"\n'
                            'dialog "Privacy preferences" [ref_2]\n button "[icon] Save choices" [ref_3] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Accepts the terms"))
        # the consent-dialog allow needs the site's own name too: a guess reaches the check
        self.assertAllowed(self.click(tab, "ref_3", "A1", "Saves the consent choices with the options off"))
        self.assertEqual([c["name"] for c in self.judge_calls()[before:]], ["Accept and submit", "Save choices"])

    def test_only_the_extension_s_own_google_name_escapes_the_submit_refusal(self):
        """The exemption is for the one name the extension writes (bridge.js GOOGLE_NAME), not for every guess that
        holds a Google word: X.GOOGLE matches anywhere, so a class "google-apply-submit" would otherwise hand the
        one hard refusal of a guessed name -- the page's own "this button submits the form" -- to the model."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "[icon] Continue with Google" [ref_1] type="submit"\n'
                            'button "[icon] Google apply submit" [ref_2] type="submit"\n')
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Opens Google sign-in"))
        self.assertDenied(self.click(tab, "ref_2", "A1", "Goes on to the next step"), "submit button")

    def test_a_google_product_control_is_not_a_signin_option(self):
        """Issue 46: "Upload from Google Drive" took the Google branch: no check, and the sign-in lease."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.icon_page(tab, 'button "Upload from Google Drive" [ref_1] type="button"\n'
                            'button "Register to upload from Google Drive" [ref_2] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.click(tab, "ref_1", "A1", "Opens the Drive picker"))  # a name no list knows: asked
        self.assertNotIn("careers.example.com", self.state().get("signin_hosts", {}))
        # a sign-up name that names an app has no Google excuse: it is asked, not refused
        self.assertAllowed(self.click(tab, "ref_2", "A1", "Opens the Drive picker"))
        self.assertEqual([c["name"] for c in self.judge_calls()[before:]],
                         ["Upload from Google Drive", "Register to upload from Google Drive"])

    def test_name_comes_from_the_lines_inside_a_nameless_button(self):
        """read_page names a control from its own text, an aria-label or an alt, never from a child element, so a
        button that holds its text in a span (Vuetify, as on aviva.talent-community.com) arrives without a name."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://aviva.talent-community.com/app/signup"}, W, "A1"))
        self.post("read_page", {"tabId": tab},
                  'button [ref_10] type="submit"\n generic "Continue with Google" [ref_11]\n'
                  '  image "talent.signup.button.alt.google" [ref_12]\n generic "Sign up with Google" [ref_13]\n'
                  'button [ref_14] type="submit"\n generic "Sign up with Linkedin" [ref_15]\n'
                  'button [ref_16] type="button"\n generic "Submit application" [ref_17]\n', W, "A1")
        self.assertAllowed(self.click(tab, "ref_10", "A1", "Starts sign-in with the Google option"))
        self.assertDenied(self.click(tab, "ref_14", "A1", "Starts sign-up with LinkedIn"), "Haiku check blocked")
        # the blind spot the nameless button left: a final control is now seen even when its text is in a child
        self.assertDenied(self.click(tab, "ref_16", "A1", "Goes to the next step"), "final control")

    def test_a_ref_id_read_shows_what_the_interactive_view_hid(self):
        """The sign-up dialog of aviva.talent-community.com: a read with a ref_id is appended to the page already
        saved, so the nameless line of the interactive view stands in the file before the "all" read that shows the
        lines inside the button. The boxes of the dialog are filled first, as the worker fills them."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://aviva.talent-community.com/app/signup"}, W, "A1"))
        self.post("read_page", {"tabId": tab},
                  'dialog [ref_20]\n combobox "Country of residence" [ref_21]\n'
                  ' checkbox "Terms & Conditions" [ref_22] type="checkbox"\n button [ref_23] type="submit"\n', W, "A1")
        self.assertDenied(self.click(tab, "ref_23", "A1", "Signs up with Google"), "without a name")
        self.assertAllowed(self.guard(C + "form_input", {"tabId": tab, "ref": "ref_21", "value": "Germany"}, W, "A1"))
        self.assertAllowed(self.guard(C + "form_input", {"tabId": tab, "ref": "ref_22", "value": True}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  'dialog [ref_20]\n combobox "Country of residence" [ref_21]\n'
                  ' checkbox "Terms & Conditions" [ref_22] type="checkbox"\n button [ref_23] type="submit"\n', W, "A1")
        self.post("read_page", {"tabId": tab, "filter": "all", "ref_id": "ref_20"},
                  'dialog [ref_20]\n combobox "Country of residence" [ref_21]\n'
                  ' checkbox "Terms & Conditions" [ref_22] type="checkbox"\n button [ref_23] type="submit"\n'
                  '  generic "talent.signup.button.alt.google" [ref_24]\n  generic "Sign up with Google" [ref_25]\n', W, "A1")
        # the fills make this a Google name after a fill, so the Haiku check decides (the fake judge allows this name)
        self.assertAllowed(self.click(tab, "ref_23", "A1", "Signs up with Google"))

    def test_the_real_aviva_sign_up_dialog(self):
        """The pages of aviva.talent-community.com, as read_page returned them in a run: the interactive view shows
        `button [ref_415] type="submit"` with nothing inside, and the read of the dialog with a ref_id, which is
        appended to that page, shows `generic "Sign up with Google"` inside the same button."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://aviva.talent-community.com/app/signup"}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample("aviva_signup_dialog_interactive.txt"), W, "A1")
        # nothing named inside it yet: the worker is told to read the dialog with filter "all" first
        denied = self.click(tab, "ref_415", "A1", "Signs up with Google")
        self.assertDenied(denied, "without a name")
        self.assertIn("filter 'all'", denied.stderr + denied.stdout)
        self.post("read_page", {"tabId": tab, "filter": "all", "ref_id": "ref_411"}, sample("aviva_signup_dialog_all.txt"), W, "A1")
        for ref, value in (("ref_430", "Italy"), ("ref_439", True), ("ref_443", True)):
            self.assertAllowed(self.guard(C + "form_input", {"tabId": tab, "ref": ref, "value": value}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample("aviva_signup_dialog_interactive.txt"), W, "A1")
        self.post("read_page", {"tabId": tab, "filter": "all", "ref_id": "ref_411"}, sample("aviva_signup_dialog_all.txt"), W, "A1")
        self.assertAllowed(self.click(tab, "ref_415", "A1", "Signs up with Google"))
        # the read with a ref_id covered the dialog only: the page's own buttons keep the interactive line, with
        # nothing named inside them, so they stay refused
        self.assertDenied(self.click(tab, "ref_337", "A1", "Signs up with LinkedIn"), "without a name")
        # the country field of the dialog is an autocomplete: read_page calls it a listbox, but it is a text input,
        # so a click on it and then typing is allowed (form_input alone leaves such a field empty)
        self.assertAllowed(self.click(tab, "ref_430", "A1", "Opens the country of residence field"))
        self.assertAllowed(self.guard(C + "computer", {"tabId": tab, "action": "type", "text": "Italy"}, W, "A1"))
        # a listbox that is not a text input still refuses typing
        self.post("read_page", {"tabId": tab, "filter": "all"},
                  sample("aviva_signup_dialog_all.txt").replace('[ref_430] type="text"', "[ref_430]"), W, "A1")
        self.assertAllowed(self.click(tab, "ref_430", "A1", "Opens the country of residence field"))
        self.assertDenied(self.guard(C + "computer", {"tabId": tab, "action": "type", "text": "Italy"}, W, "A1"),
                          "right after you click a text field")


class TestConsentDialog(Base):
    def test_the_real_himalayas_consent_dialog(self):
        """himalayas.app covers the job page with a consent dialog that has no Reject all: the private way out is to
        turn the options off and save with "Confirm choices", whose name carries a final word."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        url = "https://himalayas.app/companies/triptease/jobs/software-engineer-mid-level-3069075102"
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": url}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample("himalayas_consent_dialog.txt"), W, "A1")
        # the save button of the dialog: a final word, but no application is behind it
        self.assertAllowed(self.click(tab, "ref_32", "A1", "Saves the consent choices with the toggles off"))
        # the least private choice of the same dialog stays refused
        self.assertDenied(self.click(tab, "ref_31", "A1", "Accepts all consent options"), "most private choice")
        # a button of the page itself, outside the dialog, is judged as before
        self.assertAllowed(self.click(tab, "ref_19", "A1", "Opens the application"))
        # the options of the dialog: `button ... type="checkbox"` toggles, which the Haiku check used to refuse
        self.post("read_page", {"tabId": tab, "filter": "all", "ref_id": "ref_25"},
                  sample("himalayas_consent_options.txt"), W, "A1")
        for ref in ("ref_41", "ref_46", "ref_49"):
            self.assertAllowed(self.click(tab, ref, "A1", "Turns the consent option off"))

    def test_the_consent_dialog_in_continue_mode(self):
        """Every job of /ohmyjob:continue-applying starts sticky (a form of the last run may stand filled on the
        page). That says nothing about a consent dialog, which is the shape this site blocks the job with."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        state["tabs"][str(tab)]["sticky"] = True  # what `assign --continue` writes for every job it queues
        path.write_text(json.dumps(state))
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample("himalayas_consent_dialog.txt"), W, "A1")
        self.assertAllowed(self.click(tab, "ref_32", "A1", "Saves the consent choices with the toggles off"))

    def test_apply_on_the_job_page_itself_does_not_go_to_the_haiku_check(self):
        """`sticky` says that a form of the last run may stand filled on the page. On the job's own address nothing of
        that run stands there, so an Apply click with nothing filled is allowed, as it is outside continue mode."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        url = "https://himalayas.app/companies/triptease/jobs/software-engineer-mid-level-3069075102"
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        state["tabs"][str(tab)]["sticky"] = True   # what `assign --continue` writes for every job it queues
        state["jobs"][folder]["url"] = url
        path.write_text(json.dumps(state))
        judged = lambda: (self.run_dir() / "guard.log").read_text(encoding="utf-8").count('"event": "judge"')  # noqa: E731
        # no navigate: a tab adopted in continue mode is the one the candidate left open, and the worker is told not
        # to navigate it. `on_navigate` therefore never runs, and only the test in check_click_name clears sticky.
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, sample("himalayas_consent_dialog.txt"), W, "A1")
        self.assertAllowed(self.click(tab, "ref_19", "A1", "Opens the application for this job"))
        self.assertEqual(judged(), 0, "the Haiku check must not be asked on the job's own page")
        # away from the job's address the tab keeps sticky, so an Apply name still goes to the Haiku check
        away = sample("himalayas_consent_dialog.txt").replace(url, "https://himalayas.app/dashboard")
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, away, W, "A1")
        self.assertAllowed(self.click(tab, "ref_19", "A1", "Opens the application for this job"))
        self.assertEqual(judged(), 1)
        # a final word under a dialog that is no consent dialog keeps its refusal
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  'dialog "Apply for this job" [ref_200]\n button "Confirm" [ref_201] type="button"\n', W, "A1")
        self.assertDenied(self.click(tab, "ref_201", "A1", "Goes on"), "final control")

    def test_a_dialog_that_only_sounds_like_a_cookie_banner_unlocks_nothing(self):
        """The allow is for a consent dialog, not for every dialog whose name holds one of its words: an employer
        called "Crumbl Cookies" and a job called "Vendor Manager" must not open a way to the final button."""
        self.aa_json("start")
        (folder, tab, _b), _second = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://jobs.example.com/apply"}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  'dialog "Apply to Crumbl Cookies" [ref_300]\n button "Submit application" [ref_301] type="submit"\n'
                  ' button [ref_302] type="submit"\n button "Accept all cookies" [ref_303] type="button"\n'
                  'dialog "Apply for Vendor Manager, cookie policy" [ref_310]\n'
                  ' button "Submit" [ref_311] type="submit"\n', W, "A1")
        self.assertDenied(self.click(tab, "ref_301", "A1", "Goes on with the application"), "final control")
        self.assertDenied(self.click(tab, "ref_302", "A1", "Goes on with the application"), "without a name")
        self.assertDenied(self.click(tab, "ref_311", "A1", "Goes on with the application"), "final control")
        # the cookie button of the same page keeps its own refusal: its name says what it is
        self.assertDenied(self.click(tab, "ref_303", "A1", "Accepts the cookies"), "most private choice")


class TestPreflight(Base):
    def test_preflight_warns_without_helper_and_haiku(self):
        self.aa_json("start")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 49, "url": MARKER_FEED}, M, "M1"))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"Feed | LinkedIn","url":"' + MARKER_FEED + '"}]}\n',
                  M, "M1")
        env = dict(self.env)
        env["AA_JUDGE_CMD"] = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        proc = subprocess.run([sys.executable, str(AA), "preflight", "--tab", "49"],
                              capture_output=True, text=True, env=env, cwd=self.root)
        line = proc.stdout.strip()
        self.assertTrue(line.startswith("RETRY"), line)  # the extension writes its tag a moment after the page loads
        for _ in range(3):
            proc = subprocess.run([sys.executable, str(AA), "preflight", "--tab", "49"],
                                  capture_output=True, text=True, env=env, cwd=self.root)
        line = proc.stdout.strip()
        self.assertTrue(line.startswith("OK"), line)
        self.assertIn("WARN pop-up helper: off", line)
        self.assertIn("WARN Haiku check: not available", line)


    def test_preflight_retry_sees_the_late_tag(self):
        self.aa_json("start")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 49, "url": MARKER_FEED}, M, "M1"))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"Feed | LinkedIn","url":"' + MARKER_FEED + '"}]}\n',
                  M, "M1")
        self.assertTrue(self.aa("preflight", "--tab", "49").startswith("RETRY"))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"' + ON + ' Feed | LinkedIn","url":"' + MARKER_FEED + '"}]}\n',
                  M, "M1")
        self.assertIn("pop-up helper: on", self.aa("preflight", "--tab", "49"))


    def test_home_only_skips_the_sign_in_check(self):
        self.aa_json("start")
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"New Tab","url":"chrome://newtab/"}]}\n', M, "M1")
        for _ in range(4):  # RETRY while the tag can still come, then OK without a sign-in verdict
            line = self.aa("preflight", "--tab", "49", "--home-only")
        self.assertTrue(line.startswith("OK sign-in check skipped"), line)


class TestEndTokens(Base):
    def test_end_prints_the_run_tokens(self):
        """The master and the workers it started (from the transcripts), plus the Haiku checks (claude -p)."""
        self.aa_json("start")
        sub = self.root / "transcripts" / "sess" / "subagents"
        sub.mkdir(parents=True)
        for agent_id, meta, model, out in (("m1", {"agentType": M}, "claude-sonnet-5-5", 3),
                                           ("w1", {"agentType": W, "parentAgentId": "m1"}, "claude-haiku-5-5", 9),
                                           ("x1", {"agentType": W, "parentAgentId": "older-run"}, "claude-haiku-5-5", 1000)):
            (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
            usage = {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": out}
            (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps({"type": "assistant", "message": {"id": "m", "model": model, "usage": usage}}))
        payload = {"hook_event_name": "PreToolUse", "tool_name": C + "tabs_context_mcp", "tool_input": {},
                   "agent_type": M, "agent_id": "m1", "transcript_path": str(self.root / "transcripts" / "sess.jsonl")}
        self.hook("guard", payload)
        run_dir = next((self.root / ".ohmyjob" / "run" / "apply").glob("20*"))
        subprocess.run([sys.executable, "-c", f"import judge; from pathlib import Path; judge.selftest(Path({str(run_dir)!r}))"],
                       env=self.env, cwd=SCRIPTS, check=True)
        out = self.aa("end")
        self.assertRegex(out, r"Run cost ≈ \$0\.000 \(API-equivalent")  # a few dollars of nothing: no worker got a tab
        self.assertRegex(out, r"overhead\s+master\s+\$0\.000\n")
        self.assertRegex(out, r"Ledger: \d+ lines in .*ledger\.jsonl\nRUN FOLDER DELETED$")

        # the same numbers go to the cost log that /ohmyjob:usage prints
        (logged,) = [json.loads(x) for x in (self.root / ".ohmyjob" / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(logged["command"], "/ohmyjob:apply-jobs")
        import aa_lib

        self.assertIn(f"Run cost ≈ {aa_lib.money(logged['total'])} ", out)

        # the Stop hook of the main session always prints the saved report once, then the file is gone
        report = self.root / ".ohmyjob" / "last_report.txt"
        table = report.read_text()
        stop = {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "All done."}
        shown = json.loads(self.hook("report", stop).stdout)
        self.assertEqual(shown, {"systemMessage": table.strip()})  # Claude Code shows it; no block, no second reply
        self.assertFalse(report.exists())
        self.assertEqual(self.hook("report", stop).stdout, "")  # no report: nothing

        # the real failing case: the reply only says the total in prose, the report is still printed in full
        report.write_text(table)
        prose = {**stop, "last_assistant_message": "The run cost about $0.00 at API-equivalent rates."}
        self.assertEqual(json.loads(self.hook("report", prose).stdout), {"systemMessage": table.strip()})
        self.assertFalse(report.exists())

        # an empty report: nothing printed, the file is deleted
        report.write_text("  \n")
        self.assertEqual(self.hook("report", stop).stdout, "")
        self.assertFalse(report.exists())

        # a forked skill's own Stop is not the main session
        report.write_text(table)
        self.assertEqual(self.hook("report", {**stop, "agent_type": "prep-master"}).stdout, "")
        self.assertTrue(report.exists())


class TestFinishErrorsAndCleanup(Base):
    def test_one_bad_job_does_not_stop_finish(self):
        # a run folder of an earlier run is deleted by start
        old = self.root / ".ohmyjob" / "run" / "apply" / "2026-10-17_abcdef"
        old.mkdir(parents=True)
        (old / "state.json").write_text(json.dumps({"run_id": "2026-10-17_abcdef"}))
        self.aa_json("start")
        self.assertFalse(old.exists())
        (folder, _t1, _b1), (folder2, _t2, _b2) = self.assign(601, 602)
        for agent, tab, fold in (("F1", 601, folder), ("F2", 602, folder2)):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://www.linkedin.com/jobs/view/1"}, W, agent))
            report = {"folder": fold, "tab": tab, "status": "blocked", "where": "LinkedIn job page", "final_button": "",
                      "url": "https://www.linkedin.com/jobs/view/1", "filled": [], "uploaded": None, "drafted": [],
                      "open": [], "declarations": [], "blocker": "LOAD_FAIL: the job page shows an error"}
            self.assertAllowed(self.handback(json.dumps(report), agent_id=agent))
        # an old copy of the first job is still in Needs-Attention 
        (self.root / "Needs-Attention" / folder).mkdir(parents=True)
        before = (self.root / "Applications" / folder / "job.md").read_text()
        report = self.aa("finish")
        self.assertIn("Pending-Review: 0 | Needs-Attention: 1 | Deleted (closed): 0 | not finished: 1", report)
        self.assertIn(f"NOT RECORDED {folder}", report)
        self.assertIn("destination already exists", report)
        self.assertEqual((self.root / "Applications" / folder / "job.md").read_text(), before)
        self.assertTrue((self.root / "Needs-Attention" / folder2 / "job.md").exists())

        from tracker import Tracker  # noqa: E402

        tracker = Tracker(self.root / "Job_Tracker.xlsx")
        self.assertEqual(tracker.rows_for_key(folder.split("_")[0])[0]["Status"], "Resume Built")
        self.assertEqual(tracker.rows_for_key(folder2.split("_")[0])[0]["Status"], "Needs Attention")

        # end: the run folder is deleted; after that no worker browser call passes the guard
        self.assertTrue(self.aa("end").endswith("RUN FOLDER DELETED"))
        self.assertEqual(list((self.root / ".ohmyjob" / "run" / "apply").iterdir()), [])
        self.assertDenied(self.guard(C + "read_page", {"tabId": 601}, W, "F1"), "no active application run")
        self.assertDenied(self.guard(C + "javascript_tool", {"tabId": 1, "text": "1"}))
        # the job that was not recorded is queued again by the next run
        again = self.aa_json("start")
        self.assertEqual([j["folder"] for j in again["jobs"]], [folder])


class TestStaleRefsAndPermissions(Base):
    def test_refs_are_void_after_navigate(self):
        self.aa_json("start")
        self.assign(401, 402)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 401, "url": "https://www.linkedin.com/jobs/view/1"}, W, "S1"))
        self.page(401, "constructed_job_page.txt", agent_id="S1")
        self.assertAllowed(self.click(401, "ref_31", "S1", "Saves the job"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 401, "url": "https://company.example/jobs/1"}, W, "S1"))
        self.assertDenied(self.click(401, "ref_31", "S1"), "unknown ref")
        # on a sign-in permission (OAuth) page, Accept is a permission, not a privacy consent
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 401, "url": "https://login.microsoftonline.com/common/oauth2/authorize?x=1"}, W, "S1"))
        self.post("read_page", {"tabId": 401}, 'button "Accept" [ref_1] type="button"\nbutton "Cancel" [ref_2] type="button"\n', W, "S1")
        self.assertDenied(self.click(401, "ref_1", "S1"), "permission")
        # the whole page of an OAuth server is refused, a back-out with it: the worker reports ACCOUNT_WALL
        self.assertDenied(self.click(401, "ref_2", "S1", "Cancels the sign-in"), "permission")


if __name__ == "__main__":
    unittest.main()


class TestHandover(Base):
    """Haiku first, Sonnet takes the same tab over once: the trigger is the verdict, not the blocker text."""

    def result(self, folder, tab, **fields):
        base = {"folder": folder, "tab": tab, "status": "parked", "where": "step 2 of 5", "final_button": "", "url": "u",
                "filled": ["First name"], "uploaded": None, "drafted": [], "open": ["Work authorisation"],
                "declarations": [], "blocker": None}
        (self.run_dir() / "jobs" / folder / "result.json").write_text(json.dumps({**base, **fields}))

    def handover(self) -> list:
        code = ("import sys, json; from pathlib import Path; import aa; "
                "print(json.dumps(aa.handover_workers(Path(sys.argv[1]))))")
        proc = subprocess.run([sys.executable, "-c", code, str(self.run_dir())], capture_output=True, text=True,
                              env={**self.env, "PYTHONPATH": str(SCRIPTS)}, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def setup_run(self):
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _brief), _other = self.assign(101, 102)
        return folder, tab

    def test_payloads_carry_a_model_and_the_job_stores_it(self):
        self.aa_json("start")
        self.master_setup()
        self.assertEqual(self.aa_json("assign", "--tabs", "101,102")["queued"], 2)
        workers = self.aa_json("next")["workers"]
        self.assertEqual({w["model"] for w in workers}, {"haiku"})
        self.assertEqual({j["model"] for j in self.state()["jobs"].values()}, {"haiku"})

    def test_a_park_with_no_final_control_is_handed_over_once(self):
        folder, tab = self.setup_run()
        self.result(folder, tab)
        workers = self.handover()
        self.assertEqual([(w["folder"], w["model"]) for w in workers], [(folder, "sonnet")])
        self.assertTrue(workers[0]["description"].startswith("Sonnet takes over: "))
        job = self.state()["jobs"][folder]
        self.assertEqual((job["model"], job["handover"], job.get("stopped_by")), ("sonnet", True, None))
        self.assertFalse((self.run_dir() / "jobs" / folder / "result.json").exists())
        self.assertEqual(self.state()["tabs"][str(tab)]["agent_id"], None)
        brief = Path(PROMPT_RE.match(workers[0]["prompt"]).group(3)).read_text()
        self.assertIn("## Continue from the open page", brief)
        self.assertIn("Work authorisation", brief)
        # once only
        self.result(folder, tab)
        self.assertEqual(self.handover(), [])

    def test_codes_that_are_not_handed_over(self):
        folder, tab = self.setup_run()
        for blocker in (BLOCKER, "OTHER: Ireland only", "CLOSED: no longer accepting", "already submitted"):
            self.result(folder, tab, status="blocked", blocker=blocker)
            self.assertEqual(self.handover(), [], blocker)

    def test_an_account_wall_that_a_page_showed_is_not_handed_over(self):
        folder, tab = self.setup_run()
        self.result(folder, tab, status="blocked", blocker="ACCOUNT_WALL: it wants an account")
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  f'textbox "Password" [ref_2] type="password"\n\nTab Context:\n- tab {tab}', W, "A1")
        self.assertEqual(self.handover(), [])  # verified: Sonnet would see the same page
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  f'link "Jobs" [ref_2]\n\nTab Context:\n- tab {tab}', W, "A1")
        # `wall_seen` is sticky: a reload that replaced the wall page does not make the wall unverified
        self.assertEqual(self.handover(), [])

    def test_an_account_wall_that_no_page_showed_is_handed_over(self):
        folder, tab = self.setup_run()
        self.result(folder, tab, status="blocked", blocker="ACCOUNT_WALL: it wants an account")
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  f'link "Jobs" [ref_2]\n\nTab Context:\n- tab {tab}', W, "A1")
        self.assertEqual(len(self.handover()), 1)

    def test_a_watchdog_stop_is_handed_over_but_not_a_sonnet_job(self):
        folder, tab = self.setup_run()
        self.set_job(folder, stopped_by="stall")
        self.assertEqual(len(self.handover()), 1)
        self.set_job(folder, stopped_by="stall")  # now a Sonnet job that handed over
        self.assertEqual(self.handover(), [])

    def test_the_handover_brief_tells_the_truth(self):
        import aa
        text = aa.handover_section({"where": "step 2 of 5", "blocker": "OTHER: stuck", "open": ["Visa"],
                                    "filled": ["First name"]})
        self.assertNotIn("fixed this job by hand", text)
        self.assertNotIn("ACCOUNT_WALL", text)
        for part in ("## Continue from the open page", "step 2 of 5", "OTHER: stuck", "Visa", "First name", "already submitted"):
            self.assertIn(part, text)
        self.assertIn("fixed this job by hand", aa.continue_section("**Run:** x"))


class TestClickPrompt(unittest.TestCase):
    def test_an_icon_name_is_flagged_to_the_model(self):
        import judge
        payload = {"name": "Add", "role": "button", "context": "x", "host": "h.example", "filled": False}
        self.assertNotIn("icon", judge.build_prompt("click", payload))
        self.assertIn("read off the control's icon", judge.build_prompt("click", {**payload, "icon": True}))


class TestFillsJudge(unittest.TestCase):
    def test_prompt_and_validation(self):
        import judge
        prompt = judge.build_prompt("fills", {"profile": "Name: Alex", "fills": [["First name", "Alex"]]})
        self.assertIn("Name: Alex", prompt)
        self.assertIn('["First name", "Alex"]', prompt)
        tail = "- Have you used Amazon SageMaker? Yes"
        long = judge.build_prompt("fills", {"profile": "x" * 25000 + "\n" + tail, "fills": []})
        self.assertIn(tail, long)  # the Follow Up Questions tail is never cut
        for verdict in ("ok", "suspect"):
            self.assertTrue(judge.valid("fills", {"verdict": verdict, "unsupported": []}))
        for bad in ({"verdict": "allow", "unsupported": []}, {"verdict": "ok"}, "ok", None):
            self.assertFalse(judge.valid("fills", bad))

    def test_a_failed_check_returns_an_error(self):
        import aa
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"AA_JUDGE_CMD": f"{sys.executable} {FAKE_JUDGE}"}):
            run_dir = Path(tmp)
            ok = aa.fills_check(run_dir, "Name: Alex", [("First name", "Alex")])
            self.assertEqual(ok, {"verdict": "ok", "unsupported": 0, "labels": [], "unlabelled": 0})
            bad = aa.fills_check(run_dir, "Name: Alex", [("Salary", "BADFILL")])
            self.assertEqual((bad["verdict"], bad["labels"]), ("suspect", ["Salary"]))
            self.assertNotIn("BADFILL", json.dumps(bad))
            boom = aa.fills_check(run_dir, "Name: Alex", [("Salary", "BOOM")])  # the judge errors out: no raise
            self.assertIn("error", boom)
            with mock.patch.object(aa.J, "ask", side_effect=RuntimeError("down")):
                self.assertIn("error", aa.fills_check(run_dir, "p", [("a", "b")]))


class FillsGateBase(Base):
    """The fills check gates a PASS: suspect or error blocks (FILL_REVIEW); anything else is left alone."""

    def run_one(self, value, **report):
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _brief), _other = self.assign(101, 102)
        self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1")
        # every case reads the form page: the finish gate needs a final control in the snapshot, because the
        # report's own 'final_button' is a claim and no evidence. Only the fill itself is optional here.
        self.page(tab, "constructed_form_page.txt")
        if value is not None:
            self.assertExplicitAllow(self.guard(C + "form_input", {"tabId": tab, "ref": "ref_41", "value": value}, W, "A1"))
        body = {"folder": folder, "tab": tab, "status": "parked", "where": "review", "final_button": "Submit application",
                "url": "https://careers.example.com/a", "filled": ["First name"], "uploaded": None, "drafted": [],
                "open": ["x"], "questions": [], "declarations": [], "blocker": None, **report}
        self.assertAllowed(self.handback(json.dumps(body), agent_id="A1"))
        out = self.aa("finish")
        return folder, self.state()["jobs"][folder], out


class TestGateSuspectBlocksWith(FillsGateBase):
    def test_suspect_blocks_with_labels_only(self):
        folder, job, out = self.run_one("BADFILL")
        self.assertEqual(job["verdict"], "BLOCKED")
        self.assertEqual(job["reasons"], ["FILL_REVIEW: 1 value(s) not supported by Profile.md: Salary"])
        self.assertIn("FIX BY HAND", out)
        self.assertEqual(sum(1 for line in out.splitlines() if folder in line), 1, out)
        self.assertNotIn("BADFILL", out + json.dumps(job))


class TestGateAnErrorFails(FillsGateBase):
    def test_an_error_fails_closed_with_its_own_wording(self):
        _f, job, _out = self.run_one("BOOM")
        self.assertEqual(job["verdict"], "BLOCKED")
        self.assertTrue(job["reasons"][0].startswith("FILL_REVIEW: the fills check could not run ("), job["reasons"])


class TestGateOk(FillsGateBase):
    def test_ok_passes(self):
        self.assertEqual(self.run_one("Alex")[1]["verdict"], "PASS")


class TestGateNoFills(FillsGateBase):
    def test_no_fills_pass(self):
        self.assertEqual(self.run_one(None)[1]["verdict"], "PASS")


class TestGateAJobThat(FillsGateBase):
    def test_a_job_that_was_not_pass_is_unchanged(self):
        _f, job, _out = self.run_one("BADFILL", status="blocked", blocker="CAPTCHA: a bot check")
        self.assertEqual((job["verdict"], job["reasons"]), ("BLOCKED", ["CAPTCHA: a bot check"]))


class TestNoApplyControlAddresses(unittest.TestCase):
    def run_it(self, description, blocker="NO_APPLY_CONTROL: no control"):
        import aa
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "f").mkdir()
            (Path(tmp) / "f" / "job.md").write_text(f"# Job\n\n## 2. Job Description\n{description}\n")
            orig = aa.L.APPLICATIONS
            aa.L.APPLICATIONS = Path(tmp)
            try:
                result = {"blocker": blocker}
                aa.add_description_emails({"folder": "f"}, result)
            finally:
                aa.L.APPLICATIONS = orig
        return result["blocker"]

    def test_addresses_in_the_description_are_appended_deduped_and_capped_at_three(self):
        text = "Mail a@x.com or b@x.com, a@x.com again, c@x.com, d@x.com."
        self.assertEqual(self.run_it(text),
                         "NO_APPLY_CONTROL: no control | addresses in the description: a@x.com, b@x.com, c@x.com")

    def test_a_description_without_an_address_leaves_the_blocker_alone(self):
        self.assertEqual(self.run_it("Apply on our site."), "NO_APPLY_CONTROL: no control")

    def test_a_different_blocker_code_is_left_alone(self):
        self.assertEqual(self.run_it("Mail a@x.com", blocker="CAPTCHA: a bot check"), "CAPTCHA: a bot check")

    def test_a_missing_result_or_job_md_does_not_raise_or_change_anything(self):
        import aa
        aa.add_description_emails({"folder": "f"}, None)
        result = {"blocker": "NO_APPLY_CONTROL: x"}
        aa.add_description_emails({"folder": "no-such-folder"}, result)
        self.assertEqual(result["blocker"], "NO_APPLY_CONTROL: x")


class TestGateTheGateNever(FillsGateBase):
    def test_the_gate_never_raises_and_fill_review_is_not_handed_over(self):
        import aa
        job = {"verdict": "PASS"}
        aa.fills_gate(Path("/nonexistent"), "f", job, None, {"error": "x"})  # no result dict: left alone
        self.assertEqual(job["verdict"], "PASS")
        self.assertIn("FILL_REVIEW", aa.NO_HANDOVER_CODES)
        folder, tab = None, None
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _b), _o = self.assign(101, 102)
        (self.run_dir() / "jobs" / folder / "result.json").write_text(json.dumps(
            {"folder": folder, "tab": tab, "status": "blocked", "where": "w", "open": [], "filled": [],
             "blocker": "FILL_REVIEW: 1 value(s) not supported by Profile.md: Salary"}))
        self.assertEqual(TestHandover.handover(self), [])
