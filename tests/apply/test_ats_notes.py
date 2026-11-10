"""The ATS notes: each note's `hosts:` line, the workspace overlay, and the key of a page (aa_lib.ats_notes/ats_key).

The plugin ships four generic notes; the workspace's own ats/ folder holds the notes this user learned, and a note
there replaces the plugin note with the same stem.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
ATS = REPO / "ats"
FIXTURE = REPO / "tests" / "fixtures" / "workspace"


class AtsNotes(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ats_test_")) / "workspace"
        shutil.copytree(FIXTURE, self.root)
        self.user_ats = self.root / "ats"
        self.user_ats.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def run_py(self, code: str, *args: str) -> str:
        env = {**os.environ, "OHMYJOB_WORKSPACE": str(self.root)}
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), *args],
                              capture_output=True, text=True, env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def notes(self) -> list[dict]:
        return json.loads(self.run_py("import json, sys; sys.path.insert(0, sys.argv[1])\n"
                                      "import aa_lib as L\n"
                                      "print(json.dumps(L.ats_notes()))"))

    def key(self, url: str) -> str:
        return self.run_py("import sys; sys.path.insert(0, sys.argv[1])\n"
                           "import aa_lib as L\n"
                           "print(L.ats_key(sys.argv[2]))", url)

    def test_every_shipped_note_has_a_hosts_line_after_its_h1(self):
        for path in sorted(ATS.glob("*.md")):
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].startswith("# "), path.name)
            self.assertTrue(lines[1].startswith("hosts:"), f"{path.name}: line 2 is {lines[1]!r}")

    def test_the_shipped_patterns(self):
        hosts = {n["key"]: n["hosts"] for n in self.notes()}
        self.assertEqual(hosts["greenhouse"], ["*.greenhouse.io"])
        self.assertEqual(hosts["linkedin-easy-apply"], ["linkedin.com", "*.linkedin.com"])
        self.assertEqual(hosts["workday"], ["*.myworkdayjobs.com"])
        self.assertEqual(hosts["phenom"], [])  # Phenom sites use the employer's own host

    def test_a_workspace_note_replaces_the_plugin_note_of_the_same_stem(self):
        (self.user_ats / "workday.md").write_text(
            "# Workday (my own lessons)\nhosts: *.myworkdayjobs.com, workday.example\n\n- Learned thing.\n",
            encoding="utf-8")
        notes = {n["key"]: n for n in self.notes()}
        self.assertEqual(notes["workday"]["title"], "Workday (my own lessons)")
        self.assertEqual(notes["workday"]["path"], str((self.user_ats / "workday.md").resolve()))
        self.assertEqual(notes["workday"]["hosts"], ["*.myworkdayjobs.com", "workday.example"])
        self.assertEqual(len([n for n in self.notes() if n["key"] == "workday"]), 1)

    def test_a_workspace_note_of_a_new_host_is_added(self):
        (self.user_ats / "jobs.lever.co.md").write_text(
            "# Lever\nhosts: jobs.lever.co, *.lever.co\n\n- Learned thing.\n", encoding="utf-8")
        self.assertIn("jobs.lever.co", [n["key"] for n in self.notes()])
        self.assertEqual(self.key("https://jobs.lever.co/acme/1"), "jobs.lever.co")

    def test_the_key_is_the_first_matching_note_else_the_host_without_www(self):
        self.assertEqual(self.key("https://boards.greenhouse.io/acme"), "greenhouse")
        self.assertEqual(self.key("https://acme.wd3.myworkdayjobs.com/x"), "workday")
        self.assertEqual(self.key("https://www.linkedin.com/jobs/view/1"), "linkedin-easy-apply")
        self.assertEqual(self.key("https://careers.cisco.com/job/1"), "careers.cisco.com")
        self.assertEqual(self.key("https://www.example.com/x"), "example.com")
        self.assertEqual(self.key(""), "unknown")

    def test_a_note_without_a_hosts_line_matches_no_host(self):
        (self.user_ats / "odd.md").write_text("# Odd\n\n- No hosts line.\n", encoding="utf-8")
        notes = {n["key"]: n for n in self.notes()}
        self.assertEqual(notes["odd"]["hosts"], [])
        self.assertEqual(notes["odd"]["title"], "Odd")
        self.assertEqual(self.key("https://odd.example/x"), "odd.example")


if __name__ == "__main__":
    unittest.main()
