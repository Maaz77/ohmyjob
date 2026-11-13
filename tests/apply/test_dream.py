"""Dream (spec 4.11): the traces of the last runs become the ATS notes of this workspace.

`ohmyjob dream start|next|finish|end` is the master's queue; `reject` puts everything back. The guards of both
dream agents are tested here too.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

import test_aa as T
from test_aa import HOOKS, SCRIPTS

import aa_lib as L  # noqa: E402  (test_aa puts scripts/ on the path)

DREAM = SCRIPTS / "dream.py"
MASTER = "ohmyjob:dream-master"
WORKER = "ohmyjob:dream-host-worker"


def ledger_line(folder: str, ats: str, trace: str | None = None, **fields) -> dict:
    return {"id": f"run/{folder}", "run": "run", "date": L.today(), "folder": folder, "ats": ats, "host": ats,
            "bin": "wall", "outcome": "wall", "verdict": "BLOCKED", "code": "ACCOUNT_WALL",
            "blocker": "ACCOUNT_WALL: it wants an account", "turns": 20, "tokens": {"input": 100, "output": 10},
            "trace": trace, **fields}


class Base(T.Base):
    """A workspace with a ledger and two traces, ready for a dream."""

    def setUp(self):
        self.traces = self.root / ".ohmyjob" / "traces"
        self.traces.mkdir(parents=True, exist_ok=True)
        (self.root / "ats").mkdir(exist_ok=True)
        for name, ats in (("a.json", "workday"), ("b.json", "jobs.example.com")):
            L.write_text(self.traces / name, json.dumps({"ats": ats, "code": "ACCOUNT_WALL", "steps": []}))
        (self.traces / "ledger.jsonl").write_text(
            json.dumps(ledger_line("1_Acme_Role", "workday", "a.json")) + "\n"
            + json.dumps(ledger_line("2_Beta_Role", "jobs.example.com", "b.json")) + "\n"
            + json.dumps(ledger_line("3_Gamma_Role", "", None)) + "\n", encoding="utf-8")

    def tearDown(self):
        for path in (self.root / ".ohmyjob" / "dream", self.root / ".ohmyjob" / "run" / "dream", self.traces):
            T.shutil.rmtree(path, ignore_errors=True)
        for path in list((self.root / "ats").glob("*.md")):
            path.unlink()
        (self.root / "Dream_Report.md").unlink(missing_ok=True)

    def dream(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(DREAM), *args], capture_output=True, text=True,
                              env=self.env, cwd=self.root)

    def ok(self, *args: str) -> str:
        proc = self.dream(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def json_ok(self, *args: str) -> dict:
        return json.loads(self.ok(*args))

    def note(self, key: str) -> Path:
        return self.root / "ats" / f"{key}.md"

    def write_note(self, key: str, text: str) -> None:
        self.note(key).write_text(text, encoding="utf-8")

    def result(self, key: str, **fields) -> None:
        """What a dream worker would have reported for one key."""
        run = next((self.root / ".ohmyjob" / "run" / "dream").glob("*_dream"))
        L.write_json(run / key / f"{key}.result.json", {"ats": key, "note_changed": True, **fields})


class TestDreamFlow(Base):
    def test_start_groups_the_new_lines_and_copies_the_plugin_note(self):
        out = self.json_ok("start")
        self.assertEqual(out["count"], 2)
        self.assertEqual([group["ats"] for group in out["groups"]], ["jobs.example.com", "workday"])
        self.assertEqual(out["no_host"], 1)  # the line without a host gets no worker
        # the plugin ships workday.md: the workspace gets its own copy, which the worker may change
        self.assertTrue(self.note("workday").exists())
        self.assertIn("hosts:", self.note("workday").read_text(encoding="utf-8"))
        self.assertFalse(self.note("jobs.example.com").exists())
        bundle = json.loads(Path(out["groups"][1]["bundle"]).read_text(encoding="utf-8"))
        self.assertEqual(bundle["ats"], "workday")
        self.assertEqual(len(bundle["traces"]), 1)

    def test_next_hands_out_one_worker_per_key_and_finish_writes_the_report(self):
        self.json_ok("start")
        first = self.json_ok("next")
        self.assertEqual(len(first["workers"]), 2)
        self.assertFalse(first["done"])
        self.assertRegex(first["workers"][0]["prompt"], r"^ats=\S+ note=\S+ bundle=\S+$")
        self.assertEqual(self.json_ok("next")["workers"], [])  # both are running
        for key in ("workday", "jobs.example.com"):
            self.write_note(key, f"# {key}\nhosts: {key}\n\n- 2026-11-10: sign in with Google first.\n")
            self.result(key, lessons_added=[{"text": "sign in with Google first", "evidence": [f"run/{key}"]}])
        self.assertTrue(self.json_ok("next")["done"])
        finish = self.json_ok("finish")
        self.assertEqual(finish["rejected"], [])
        report = (self.root / "Dream_Report.md").read_text(encoding="utf-8")
        self.assertIn("# Dream Report", report)
        self.assertIn("## Per ATS", report)
        self.assertIn("sign in with Google first", report)
        self.assertIn("lines without a host", report)

    def test_end_moves_the_watermark_and_deletes_the_run_folder(self):
        self.json_ok("start")
        self.json_ok("next")
        for key in ("workday", "jobs.example.com"):
            self.write_note(key, f"# {key}\nhosts: {key}\n")
            self.result(key)
        self.json_ok("finish")
        out = self.ok("end")
        self.assertIn("Dream finished: 2 ATS keys", out)
        self.assertEqual(json.loads((self.root / ".ohmyjob" / "dream" / "state.json").read_text())["watermark"], 3)
        self.assertFalse((self.root / ".ohmyjob" / "run" / "dream" / "CURRENT").exists())
        # a second dream has nothing new to do
        self.assertEqual(self.json_ok("start")["count"], 0)

    def test_a_note_that_breaks_a_rule_is_put_back(self):
        self.json_ok("start")
        self.json_ok("next")
        before = self.note("workday").read_text(encoding="utf-8")
        self.write_note("workday", "# Workday\nhosts: *.myworkdayjobs.com\n\n- write to alex@example.com\n")
        self.write_note("jobs.example.com", "\n".join(["# Example"] + [f"- line {i}" for i in range(60)]))
        for key in ("workday", "jobs.example.com"):
            self.result(key)
        finish = self.json_ok("finish")
        self.assertEqual(sorted(finish["rejected"]), ["jobs.example.com", "workday"])
        self.assertEqual(self.note("workday").read_text(encoding="utf-8"), before)
        self.assertFalse(self.note("jobs.example.com").exists())  # a new note that is wrong is removed
        report = (self.root / "Dream_Report.md").read_text(encoding="utf-8")
        self.assertIn("an email address", report)
        self.assertIn("at most 40", report)

    def test_reject_puts_the_notes_the_report_and_the_watermark_back(self):
        self.write_note("workday", "# Workday\nhosts: *.myworkdayjobs.com\n\n- the old lesson.\n")
        (self.root / "Dream_Report.md").write_text("# old report\n", encoding="utf-8")
        self.json_ok("start")
        self.json_ok("next")
        self.write_note("workday", "# Workday\nhosts: *.myworkdayjobs.com\n\n- a new lesson.\n")
        self.write_note("jobs.example.com", "# Example\nhosts: jobs.example.com\n\n- a new note.\n")
        for key in ("workday", "jobs.example.com"):
            self.result(key)
        self.json_ok("finish")
        self.ok("end")
        self.assertIn("a new lesson", self.note("workday").read_text(encoding="utf-8"))
        self.ok("reject")
        self.assertIn("the old lesson", self.note("workday").read_text(encoding="utf-8"))
        self.assertFalse(self.note("jobs.example.com").exists())
        self.assertEqual((self.root / "Dream_Report.md").read_text(encoding="utf-8"), "# old report\n")
        self.assertEqual(json.loads((self.root / ".ohmyjob" / "dream" / "state.json").read_text())["watermark"], 0)

    def test_a_worker_that_never_reports_is_given_up_on(self):
        self.json_ok("start")
        self.json_ok("next")
        run = next((self.root / ".ohmyjob" / "run" / "dream").glob("*_dream"))
        state = json.loads((run / "state.json").read_text())
        for group in state["groups"].values():
            group["spawned_at"] = time.time() - 21 * 60
        L.write_json(run / "state.json", state)
        self.assertTrue(self.json_ok("next")["done"])
        self.json_ok("finish")
        self.assertIn("no report in 20 minutes", (self.root / "Dream_Report.md").read_text(encoding="utf-8"))

    def test_a_worker_that_ends_without_a_report_is_marked_done(self):
        self.json_ok("start")
        self.json_ok("next")
        run = next((self.root / ".ohmyjob" / "run" / "dream").glob("*_dream"))
        state = json.loads((run / "state.json").read_text())
        key = sorted(state["groups"])[0]
        state["groups"][key]["agent_id"] = "D5"
        L.write_json(run / "state.json", state)
        proc = subprocess.run([sys.executable, str(T.HOOKS), "capture"],
                              input=json.dumps({"session_id": "s1", "cwd": str(self.root),
                                                "hook_event_name": "SubagentStop", "agent_type": WORKER,
                                                "agent_id": "D5", "last_assistant_message": "I could not do it."}),
                              capture_output=True, text=True, env=self.env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        group = json.loads((run / "state.json").read_text())["groups"][key]
        self.assertTrue(group["done"])
        self.assertIn("without a usable JSON report", group["error"])

    def test_dream_needs_no_git_and_no_run(self):
        self.assertEqual(self.dream("next").returncode, 3)
        self.assertIn("no dream is running", self.dream("finish").stdout)


class TestDreamKeys(unittest.TestCase):
    def test_a_key_becomes_a_safe_file_name(self):
        import dream  # noqa: E402

        self.assertEqual(dream.safe_key("Jobs.Example.com"), "jobs.example.com")
        self.assertEqual(dream.safe_key("../../etc/passwd"), "etc-passwd")
        self.assertEqual(dream.safe_key(""), "unknown")
        self.assertEqual(dream.safe_key("a b/c"), "a-b-c")

    def test_the_metrics_sum_the_token_dicts(self):
        import dream  # noqa: E402

        lines = [{"ats": "x", "outcome": "parked", "turns": 10, "tokens": {"input": 100, "output": 10}, "date": "2026-01-01"},
                 {"ats": "x", "outcome": "parked", "turns": 20, "tokens": {"input": 300, "output": 10}, "date": "2026-01-02"}]
        metrics = dream.ats_metrics(lines, "x", Path("/nope.md"))
        self.assertEqual(metrics["median_tokens_parked"], 210)
        self.assertEqual(metrics["median_turns_parked"], 15)
        self.assertEqual(metrics["wall_rate"], "0%")


class TestDreamGuards(T.Base):
    """The guards of the two dream agents: the master's commands, the worker's one note."""

    def guard(self, tool: str, tool_input: dict, agent_type: str, agent_id: str = "D1"):
        return self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                                   "agent_type": agent_type, "agent_id": agent_id})

    def test_the_master_runs_only_its_four_commands(self):
        for command in ("ohmyjob dream start", "ohmyjob dream next", "ohmyjob dream finish", "ohmyjob dream end"):
            self.assertExplicitAllow(self.guard("Bash", {"command": command}, MASTER))
        for command in ("ohmyjob dream reject", "ohmyjob dream seed", "ohmyjob apply next", "ohmyjob dream next; ls",
                        "ohmyjob dream next && ls", "cat ats/workday.md"):
            self.assertDenied(self.guard("Bash", {"command": command}, MASTER), "ohmyjob dream")

    def test_the_master_starts_only_dream_workers_and_reads_nothing(self):
        self.assertExplicitAllow(self.guard("Agent", {"subagent_type": WORKER, "prompt": "ats=x"}, MASTER))
        self.assertDenied(self.guard("Agent", {"subagent_type": "ohmyjob:application-worker"}, MASTER), "starts only")
        self.assertDenied(self.guard("Read", {"file_path": str(T.ATS / "workday.md")}, MASTER), "no other tool")

    def test_a_worker_reads_its_bundle_the_notes_and_the_worker_rules(self):
        self.assertExplicitAllow(self.guard("Read", {"file_path": str(T.ATS / "workday.md")}, WORKER))
        self.assertExplicitAllow(self.guard(
            "Read", {"file_path": str(T.REPO / "agents" / "application-worker.md")}, WORKER))
        self.assertDenied(self.guard("Read", {"file_path": str(self.root / "Profile.md")}, WORKER), "reads only")
        self.assertDenied(self.guard("Bash", {"command": "ls"}, WORKER), "Read, Edit and Write")

    def test_a_worker_writes_only_the_note_of_the_group_it_claimed(self):
        run = self.root / ".ohmyjob" / "run" / "dream" / "d1"
        run.mkdir(parents=True)
        (run.parent / "CURRENT").write_text("d1", encoding="utf-8")
        bundles = {}
        groups = {}
        for key in ("workday", "greenhouse"):
            (run / key).mkdir()
            bundles[key] = run / key / f"{key}.json"
            L.write_json(bundles[key], {"ats": key})
            groups[key] = {"note": str(self.root / "ats" / f"{key}.md"), "bundle": str(bundles[key]),
                           "done": False, "spawned": True, "spawned_at": time.time()}
        L.write_json(run / "state.json", {"groups": groups})
        (self.root / "ats").mkdir(exist_ok=True)
        # before a Read of a bundle nothing may be written
        self.assertDenied(self.guard("Write", {"file_path": groups["workday"]["note"]}, WORKER, "D9"), "writes only")
        # reading the bundle claims that group, and only that group's note may be written
        self.assertExplicitAllow(self.guard("Read", {"file_path": str(bundles["workday"])}, WORKER, "D9"))
        self.assertExplicitAllow(self.guard("Edit", {"file_path": groups["workday"]["note"]}, WORKER, "D9"))
        self.assertDenied(self.guard("Write", {"file_path": groups["greenhouse"]["note"]}, WORKER, "D9"), "writes only")
        # the claim does not open any other file to it
        self.assertDenied(self.guard("Read", {"file_path": str(self.root / "Profile.md")}, WORKER, "D9"), "reads only")
        self.assertDenied(self.guard("Read", {"file_path": str(bundles["greenhouse"])}, WORKER, "D9"), "reads only")
        self.assertExplicitAllow(self.guard("Read", {"file_path": str(T.ATS / "workday.md")}, WORKER, "D9"))
        # another worker cannot read the claimed bundle, and cannot write that note
        self.assertDenied(self.guard("Read", {"file_path": str(bundles["workday"])}, WORKER, "D8"), "reads only")
        self.assertDenied(self.guard("Write", {"file_path": groups["workday"]["note"]}, WORKER, "D8"), "writes only")
        # the report lands under the claimed group, whatever `ats` the message names
        proc = self.hook("handback", {"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback",
                                      "tool_input": {"message": json.dumps({"ats": "greenhouse", "note_changed": True})},
                                      "agent_type": WORKER, "agent_id": "D9"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((run / "workday" / "workday.result.json").exists())
        self.assertFalse((run / "greenhouse" / "greenhouse.result.json").exists())
        self.assertTrue(json.loads((run / "state.json").read_text())["groups"]["workday"]["done"])
        T.shutil.rmtree(self.root / ".ohmyjob" / "run" / "dream", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
