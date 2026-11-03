"""Parser and rule tests on constructed samples in the format of the Claude in Chrome tools (tests/apply/samples/)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import aa_lib as L  # noqa: E402
import lexicon as X  # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


class TestParsers(unittest.TestCase):
    def test_job_page(self):
        text = sample("constructed_job_page.txt")
        refs = L.parse_refs(text)
        self.assertEqual(refs["ref_6"], {"role": "button", "name": "Close jump menu", "attrs": {"type": "button"},
                                         "line": 'button "Close jump menu" [ref_6] type="button"'})
        self.assertEqual(refs["ref_8"]["name"], "Search")
        self.assertEqual(refs["ref_8"]["attrs"]["placeholder"], "Search")
        self.assertEqual(refs["ref_19"]["role"], "link")
        self.assertTrue(refs["ref_19"]["attrs"]["href"].startswith("https://www.linkedin.com/company/"))
        self.assertNotIn("Viewport", " ".join(r["line"] for r in refs.values()))
        self.assertEqual(L.final_controls(refs, filled=False), [])
        self.assertEqual(L.final_controls(refs, filled=True), ["Easy Apply"])  # an Apply word is final after a fill
        self.assertEqual(L.page_url(text, 101), "https://www.linkedin.com/jobs/view/1/")

    def test_form_page(self):
        text = sample("constructed_form_page.txt")
        refs = L.parse_refs(text)
        self.assertEqual(refs["ref_49"]["name"], "Submit application")
        self.assertEqual(refs["ref_49"]["attrs"]["type"], "submit")
        self.assertEqual(refs["ref_64"]["role"], "combobox")
        self.assertIn("Submit application", L.final_controls(refs, filled=False))
        self.assertIn("Apply now", L.final_controls(refs, filled=True))
        self.assertEqual(refs["ref_50"]["name"], "")

    def test_label_is_the_parent_of_its_box(self):
        text = sample("constructed_label_parent.txt")
        refs = L.parse_refs(text)
        self.assertEqual(refs["ref_1"]["name"], "")
        self.assertEqual(refs["ref_3"]["name"], "small")
        self.assertEqual(L.final_controls(refs, filled=False), ["Submit order"])
        self.assertIn("Agree to terms and conditions", L.context_names(text, "ref_7"))
        self.assertNotIn("Agree to terms and conditions", L.context_names(text, "ref_5", span=0))

    def test_batch_output_after_fill(self):
        text = sample("constructed_batch_after_fill.txt")
        refs = L.parse_refs(text)
        # the first element line follows the form_input lines on the same line, so it does not parse
        self.assertNotIn("ref_1", refs)
        self.assertEqual(refs["ref_4"]["name"], "Submit order")

    def test_tabs_context_and_navigate(self):
        tabs = L.parse_tabs_context(sample("constructed_tabs_context.txt"))
        self.assertEqual(tabs, [{"id": 501, "title": "New Tab", "url": "chrome://newtab/"}])
        executed, by_tab = L.tab_context(sample("constructed_navigate.txt"))
        self.assertEqual(executed, 501)
        self.assertEqual(by_tab[501]["url"], "https://www.linkedin.com/jobs/view/4399000001")

    def test_rules(self):
        self.assertTrue(X.FINAL.find("Submit application"))
        self.assertFalse(X.FINAL.find("Next"))
        self.assertFalse(X.FINAL.find("Review your application"))
        self.assertTrue(L.LINKEDIN_SIGNED_OUT_RE.search("https://www.linkedin.com/authwall?trk=x"))
        self.assertFalse(L.LINKEDIN_SIGNED_OUT_RE.search("https://www.linkedin.com/feed/"))
        self.assertTrue(L.navigable_href("https://x.example/privacy"))
        self.assertFalse(L.navigable_href("#"))
        self.assertFalse(L.navigable_href("javascript:void(0)"))

    def test_click_notes(self):
        self.assertEqual(L.note_problem("Submits the application", False), "Submits")
        self.assertIsNotNone(L.note_problem("Sends my application to Acme", False))
        self.assertIsNone(L.note_problem("Goes to the next step; this does not submit the form", False))
        self.assertIsNone(L.note_problem("Opens the Easy Apply dialog", False))
        self.assertIsNotNone(L.note_problem("Clicks Apply", True))
        self.assertIsNone(L.note_problem("Uploads the resume", True))

    def test_helper_titles(self):
        self.assertTrue(L.HELPER_ON_RE.match("[AA on 1.6.0] Feed | LinkedIn"))
        self.assertTrue(L.HELPER_ON_RE.match("[AA on] Feed | LinkedIn"))  # an older extension: on, version unknown
        self.assertEqual(L.helper_version("[AA on 1.6.0] Feed | LinkedIn"), "1.6.0")
        self.assertIsNone(L.helper_version("[AA on] Feed | LinkedIn"))
        self.assertIsNone(L.helper_version("Feed | LinkedIn"))
        self.assertFalse(L.HELPER_ON_RE.match("[AA onward] x"))
        self.assertEqual(L.CHILD_TITLE_RE.match("[AA from 701] Sign in - Google Accounts").group(1), "701")
        self.assertIsNone(L.CHILD_TITLE_RE.match("Sign in - Google Accounts"))

    def test_extension_version_is_the_manifest_version(self):
        manifest = json.loads((Path(__file__).resolve().parents[2] / "chrome-extension" / "manifest.json").read_text())
        self.assertEqual(L.extension_version(), manifest["version"])


class TestJobOverview(unittest.TestCase):
    """job.md is read in both forms: "- Key: value" and "- **Key:** value"."""

    FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workspace" / "Applications"

    def overview(self, folder: str) -> dict:
        return L.job_overview((self.FIXTURE / folder / "job.md").read_text(encoding="utf-8"))

    def test_plain_and_bold_keys_give_the_same_clean_values(self):
        plain = self.overview("4399000001_Lumenfield-Logistics_Operations-Analyst")
        bold = self.overview("969845171025937_Quillon-Systems_Project-Coordinator")
        self.assertEqual(plain["Source"], "linkedin")
        self.assertEqual(bold["Source"], "indeed")
        self.assertEqual(bold["Company"], "Quillon Systems")
        self.assertEqual(bold["Apply URL"], "https://careers.quillon.example/jobs/project-coordinator-48213")
        self.assertEqual(bold["Job URL"], "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268")
        self.assertEqual(set(plain) - {"Date Posted"}, set(bold) - {"Apply URL", "Date Posted"})

    def test_blank_values_are_empty_in_both_forms(self):
        for folder in ("4399000001_Lumenfield-Logistics_Operations-Analyst",
                       "969845171025937_Quillon-Systems_Project-Coordinator"):
            fields = self.overview(folder)
            self.assertEqual(fields["Match Score"], "", folder)
            self.assertEqual(fields["Matched Resume"], "", folder)
            self.assertFalse(any(v.startswith("**") for v in fields.values()), folder)


class TestKeysAndAddresses(unittest.TestCase):
    ENTER_LIKE = ["Return", "Enter", "enter", "shift+Enter", "cmd+Enter", "ctrl+Return", "NumpadEnter", "KP_Return", "KP_Enter",
                  "ISO_Enter", "space", "Space", "kp_space", "spacebar", " ", "\r", "\n", "\r\n", "Tab\nTab", "Tab space",
                  "ctrl+m", "ctrl+j", "Control+M"]

    def test_workers_press_only_keys_of_the_allowlist(self):
        for keys in ["Tab", "tab", "shift+Tab", "Tab Tab", "Escape", "BackSpace", "Delete", "ArrowDown", "Home", "PageDown",
                     "ctrl+a", "cmd+v", "ctrl+z"]:
            self.assertTrue(L.keys_allowed(keys), keys)
        for keys in self.ENTER_LIKE + ["", "F5", "alt+Tab", "ctrl+Enter", "a", "ctrl+w", "cmd+q", "Tab\x00", None, 5]:
            self.assertFalse(L.keys_allowed(keys), repr(keys))

    def test_other_callers_may_not_press_anything_that_activates_a_control(self):
        for keys in self.ENTER_LIKE:
            self.assertIsNotNone(L.activation_key(keys), repr(keys))
        for keys in ["Tab", "Escape", "ctrl+f", "F5", "ArrowDown", "cmd+t", "ctrl+a"]:
            self.assertIsNone(L.activation_key(keys), keys)

    def test_navigate_addresses(self):
        for url in ["https://example.com/a", "http://example.com", "HTTPS://EXAMPLE.COM", "example.com/jobs",
                    "https://example.com/a b"]:
            self.assertIsNone(L.url_problem(url), url)
        for url in ["javascript:alert(1)", "java\tscript:alert(1)", "javascript\n:alert(1)", "\x01javascript:alert(1)",
                    "data:text/html,x", "file:///etc/passwd", "https://example.com/\x00", "ht\ntps://example.com"]:
            self.assertIsNotNone(L.url_problem(url), repr(url))

    def test_writes_survive_a_lone_surrogate(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            L.write_text(Path(d) / "a.txt", 'button "bad \ud800 title" [ref_1]')
            L.write_json(Path(d) / "a.json", {"title": "bad \udfff"})
            self.assertIn("ref_1", (Path(d) / "a.txt").read_text(encoding="utf-8"))
            self.assertIn("title", json.loads((Path(d) / "a.json").read_text(encoding="utf-8")))


class TestOneClickApply(unittest.TestCase):
    def test_one_click_variants_are_final_but_the_openers_are_not(self):
        for name in ["1-Click Apply", "1 Click Apply", "Apply with 1-Click", "One-Click Apply", "One click apply",
                     "Quick Apply", "Instant Apply", "Apply instantly", "Apply in one click"]:
            self.assertTrue(X.FINAL.find(name), name)
        for name in ["Apply", "Apply now", "Easy Apply", "Apply on company website", "Apply Now"]:
            self.assertIsNone(X.FINAL.find(name), name)


class TestResponseShapes(unittest.TestCase):
    def test_replace_tool_text_keeps_the_shape(self):
        self.assertEqual(L.replace_tool_text("old", "new"), "new")
        blocks = [{"type": "text", "text": "a"}, {"type": "image", "source": "x"}, {"type": "text", "text": "b"}]
        self.assertEqual(L.replace_tool_text(blocks, "new"), [{"type": "text", "text": "new"}, {"type": "image", "source": "x"}])
        self.assertEqual(L.replace_tool_text({"content": blocks}, "new")["content"][0]["text"], "new")
        self.assertEqual(L.replace_tool_text({"other": 1}, "new"), [{"type": "text", "text": "new"}])


class TestGoogleRules(unittest.TestCase):
    def test_scopes_from_addresses(self):
        basic = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&scope=openid%20email%20profile&redirect_uri=y"
        self.assertEqual(L.oauth_scopes(basic), {"openid", "email", "profile"})
        nested = ("https://accounts.google.com/v3/signin/accountchooser?continue=https%3A%2F%2Faccounts.google.com"
                  "%2Fsignin%2Foauth%2Fconsent%3Fscope%3Demail%2Bhttps%253A%252F%252Fwww.googleapis.com%252Fauth%252Fdrive")
        self.assertEqual(L.oauth_scopes(nested), {"email", "https://www.googleapis.com/auth/drive"})
        self.assertIsNone(L.oauth_scopes("https://accounts.google.com/signin/oauth/id?part=x"))
        self.assertTrue(L.is_gsi_url("https://accounts.google.com/gsi/select?client_id=x"))
        entry: dict = {}
        self.assertTrue(L.track_google(entry, basic))
        self.assertEqual(L.google_access(entry), "basic")
        L.track_google(entry, "https://site.example/cb")
        L.track_google(entry, nested)          # a new visit starts again
        self.assertEqual(L.google_access(entry), "more")
        gsi: dict = {}
        L.track_google(gsi, "https://accounts.google.com/gsi/select?client_id=x")
        self.assertEqual(L.google_access(gsi), "basic")
        self.assertIsNone(L.google_access({}))
        self.assertTrue(L.EMAIL_RE.search('link "Test User test.user@example.com"'))


class TestHandbackInput(unittest.TestCase):
    def test_handback_message_is_a_valid_result(self):
        tool_input = json.loads(sample("constructed_handback_input.json"))
        self.assertEqual(list(tool_input), ["message"])
        obj = L.extract_json_object(tool_input["message"])
        folder = "4399000001_Lumenfield-Logistics_Operations-Analyst"
        self.assertEqual(L.validate_result(obj, folder, 501), [])
        self.assertEqual(obj["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
