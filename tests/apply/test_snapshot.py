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

    def test_field_marks_are_stripped_and_the_name_rules_see_the_label(self):
        text = ('button "[x] Yes" [ref_1] aria-pressed="true"\nbutton "[req] [invalid] Next" [ref_2]\n'
                'button "[disabled] Submit application" [ref_3]\ntextbox "[req] Search jobs = python" [ref_4]\n'
                'label "Question" [ref_5]\n radio "[x] Question / A" [ref_6]\n')
        refs = L.parse_refs(text)
        self.assertEqual([refs[r]["name"] for r in ("ref_1", "ref_2", "ref_3")], ["Yes", "Next", "Submit application"])
        self.assertEqual((refs["ref_4"]["name"], refs["ref_4"]["value"]), ("Search jobs", "python"))
        self.assertTrue(L.is_search_field(refs["ref_4"]))
        self.assertEqual(L.final_controls(refs, filled=False), ["Submit application"])
        self.assertEqual(L.line_name(text.splitlines()[0]), "Yes")
        self.assertEqual(L.question_labels(text), {"ref_6": "Question"})
        self.assertFalse(L.looks_like_form({"ref_4": refs["ref_4"]}))

    def test_values_split_off_labels_markers_and_marks_only_names(self):
        text = ('textbox "[req] Phone = 555" [ref_1] type="text"\ntextbox "Search jobs = python" [ref_2]\n'
                'textbox "Zip = 1" [ref_3]\ntextbox "City = x" [ref_4]\nlink "[AA alert] Submit failed" [ref_5]\n'
                'checkbox "[x]" [ref_6]\nbutton "Submitted by = bob" [ref_7]\n')
        refs = L.parse_refs(text)
        self.assertEqual((refs["ref_1"]["name"], refs["ref_1"]["value"]), ("Phone", "555"))
        self.assertNotIn("value", refs["ref_7"])  # only text-type names carry a value
        self.assertEqual(refs["ref_6"]["name"], "")  # a mark can end the name
        self.assertTrue(refs["ref_5"]["marker"])
        self.assertEqual(L.final_controls(refs, filled=True), [])  # the banner is no control
        self.assertFalse(L.looks_like_form({r: refs[r] for r in ("ref_2", "ref_3", "ref_4")}))
        # three ordinary fields whose values hold search words: a form only because the value is split off
        shaped = L.parse_refs('textbox "Name = search jobs" [ref_1]\ntextbox "City = filter" [ref_2]\ntextbox "Job = search" [ref_3]\n')
        self.assertTrue(L.looks_like_form(shaped))
        self.assertFalse(L.is_search_field(shaped["ref_1"]))
        # a radio's label part, and a nameless label that takes the label part of the next line
        q = L.question_labels('label [ref_1]\n textbox "Phone = Yes" [ref_2]\nlabel "Q?" [ref_3]\n radio "Q? / Yes" [ref_4]\n')
        self.assertEqual(q, {"ref_2": "Phone", "ref_4": "Q?"})
        self.assertNotIn("555", L.without_values(text))
        self.assertIn("Submitted by = bob", L.without_values(text))

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

    def test_click_notes_step_words(self):
        # the real regression: Next on step 1 of an Easy Apply dialog
        self.assertIsNone(L.note_problem("Goes to the next Easy Apply step", True))
        self.assertIsNone(L.note_problem("Opens the Easy Apply dialog", True))
        # the weak apply-word branch still works, and a final word wins over a progression phrase
        self.assertIsNotNone(L.note_problem("Clicks Apply", True))
        self.assertIsNotNone(L.note_problem("Submits the application", True))
        self.assertIsNotNone(L.note_problem("Goes to the next step and submits the application", True))

    def test_helper_titles(self):
        self.assertTrue(L.HELPER_ON_RE.match("[AA on 1.6.0] Feed | LinkedIn"))
        self.assertTrue(L.HELPER_ON_RE.match("[AA on] Feed | LinkedIn"))  # an older extension: on, version unknown
        self.assertEqual(L.helper_version("[AA on 1.6.0] Feed | LinkedIn"), "1.6.0")
        self.assertIsNone(L.helper_version("[AA on] Feed | LinkedIn"))
        self.assertIsNone(L.helper_version("Feed | LinkedIn"))
        self.assertFalse(L.HELPER_ON_RE.match("[AA onward] x"))
        # Chrome trims a title's trailing space: a page without a title of its own (robots.txt) shows the bare tag
        self.assertTrue(L.HELPER_ON_RE.match("[AA on 1.6.0]"))
        self.assertEqual(L.helper_version("[AA on 1.6.0]"), "1.6.0")
        self.assertTrue(L.CHILD_TITLE_RE.match("[AA from 49]"))
        self.assertFalse(L.HELPER_ON_RE.match("[AA on 1.6.0]x"))
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


class TestCheckResultParked(unittest.TestCase):
    """A parked result is a win only when the end of the flow is evidenced."""

    def run_check(self, refs, final_button, open_items):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            L.snap_dir(run_dir).mkdir(parents=True, exist_ok=True)
            L.refs_path(run_dir, 7).write_text(json.dumps(refs), encoding="utf-8")
            result = {"status": "parked", "final_button": final_button, "open": open_items, "filled": ["Name"]}
            return L.check_result(run_dir, {"tabs": {"7": {"filled": True}}}, {"tab": 7}, result)[0]

    DISMISS = {"ref_1": {"role": "button", "name": "Dismiss", "attrs": {}, "line": 'button "Dismiss"'}}
    SUBMIT = {"ref_2": {"role": "button", "name": "Submit application", "attrs": {}, "line": 'button "Submit application"'}}

    def test_no_final_control_is_blocked_even_with_open_items(self):
        self.assertEqual(self.run_check(self.DISMISS, "", ["q1", "q2"]), "BLOCKED")

    def test_no_final_control_and_no_open_is_still_fail(self):
        self.assertEqual(self.run_check(self.DISMISS, "", []), "FAIL")

    def test_final_control_in_snapshot_passes(self):
        self.assertEqual(self.run_check(self.SUBMIT, "", ["q1"]), "PASS")

    def test_a_named_final_button_alone_is_a_claim_not_evidence(self):
        # the snapshot holds no final control, so the worker's own 'final_button' does not carry the park (ADR 0001)
        self.assertEqual(self.run_check(self.DISMISS, "Submit application", ["q1"]), "BLOCKED")


class TestConsentIsNoFinalControl(unittest.TestCase):
    PAGE = ('dialog "Cookie Preferences" [ref_1]\n button "Confirm My Choices" [ref_2]\n'
            'link "Apply now »" [ref_3] href="/talentcommunity/apply/1/"\n')

    def check(self, page):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            L.write_text(L.snapshot_path(run_dir, 7), page)
            L.write_json(L.refs_path(run_dir, 7), L.parse_refs(page))
            result = {"status": "parked", "final_button": "Apply now »", "open": ["x"], "filled": [], "uploaded": None}
            verdict = L.check_result(run_dir, {"tabs": {"7": {"filled": True}}}, {"tab": 7}, result)[0]
            return verdict, L.final_controls(L.parse_refs(page), True, page)

    def test_cookie_panel_button_is_no_final_control(self):
        verdict, controls = self.check(self.PAGE)
        self.assertEqual(verdict, "BLOCKED")
        self.assertNotIn("Confirm My Choices", controls)

    def test_submit_outside_a_consent_dialog_is_listed(self):
        _verdict, controls = self.check(self.PAGE + 'button "Submit application" [ref_4]\n')
        self.assertIn("Submit application", controls)


BAE_PAGE = (
    'combobox "All" [ref_97]\ncombobox "All" [ref_100]\n'
    'textbox "Select how often (in days) to receive an alert" [ref_106] type="number"\n'
    'textbox "Select how often (in days) to receive an alert" [ref_112] type="number"\n'
    'link "Apply now »" [ref_114] href="/talentcommunity/apply/1458057333/?locale=en_GB"\n')
NAMELESS_SUBMIT = 'button "Easy Apply to this job" [ref_1]\nbutton [ref_2] type="submit"\n generic "Submit application" [ref_3]\n'


class TestFalseParkGate(unittest.TestCase):
    """F1: a page that only looks like a form is not proof that a form was reached."""

    def check(self, page, tab_state, **report):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            L.write_text(L.snapshot_path(run_dir, 7), page)
            L.write_json(L.refs_path(run_dir, 7), L.parse_refs(page))
            result = {"status": "parked", "final_button": "Apply now »", "open": ["x"], "filled": [], "uploaded": None,
                      **report}
            return L.check_result(run_dir, {"tabs": {"7": tab_state}}, {"tab": 7}, result)

    def test_bae_page_is_shaped_like_a_form_but_parks_blocked(self):
        self.assertTrue(L.looks_like_form(L.parse_refs(BAE_PAGE)))
        self.assertEqual(self.check(BAE_PAGE, {"filled": True, "acted": False})[0], "BLOCKED")
        # a tick elsewhere made the tab "acted": the Apply link is still no last step
        self.assertEqual(self.check(BAE_PAGE, {"filled": True, "acted": True})[0], "BLOCKED")

    def test_a_tab_that_never_showed_a_form_parks_blocked_at_apply_even_with_filled_fields(self):
        verdict = self.check('button "Apply now" [ref_1]\n', {"filled": False, "acted": False}, filled=["x"])
        self.assertEqual(verdict[0], "BLOCKED")

    def test_a_link_final_after_an_answer_has_its_own_message(self):
        verdict, reasons = self.check(BAE_PAGE, {"filled": True, "acted": True})
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("a link that opens another page", reasons[0])

    def test_a_final_word_from_a_child_name_does_not_pass_a_job_page(self):
        page = 'button [ref_1] type="submit"\n generic "Send me jobs like this" [ref_2]\n'
        verdict, reasons = self.check(page, {"filled": True}, final_button="Send")
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("stopped before any final control", reasons[0])

    def test_a_site_filled_form_parked_at_an_own_name_submit_passes(self):
        page = 'button "Submit application" [ref_1]\n'
        self.assertEqual(self.check(page, {"filled": True}, final_button="Submit application")[0], "PASS")

    def test_a_nameless_submit_is_a_final_control_under_its_child_name(self):
        refs = L.parse_refs(NAMELESS_SUBMIT)
        self.assertEqual(L.final_controls(refs, False, NAMELESS_SUBMIT), ["Submit application"])
        self.assertEqual(self.check(NAMELESS_SUBMIT, {"acted": True}, final_button="")[0], "PASS")

    def test_a_prefilled_easy_apply_with_an_upload_passes(self):
        verdict = self.check(NAMELESS_SUBMIT, {"filled": True}, final_button="", uploaded="resume.pdf")
        self.assertEqual(verdict[0], "PASS")

    def test_parked_with_nothing_done_and_no_final_control_is_blocked(self):
        verdict, reasons = self.check('button "Dismiss" [ref_1]\n', {}, final_button="Submit", open=[])
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("stopped before any final control", reasons[0])  # NO_FINAL_MARKER: the handover reads it
        # and with work done on the tab too: the snapshot shows no final control, so 'final_button' is a claim (ADR 0001)
        self.assertEqual(self.check('button "Dismiss" [ref_1]\n', {"acted": True}, final_button="Submit", open=["x"])[0], "BLOCKED")


class TestWallEvidence(unittest.TestCase):
    """F3a: a one-time-code step is account-wall evidence, and the wall stays seen after a reload."""

    CODE_PAGE = 'heading "Confirm Your Identity" [ref_1]\n' + "".join(
        f'textbox "Digit {i}" [ref_{i + 1}] type="text"\n' for i in range(1, 7))

    def test_the_sticky_flag_ignores_sign_up_links_ads_and_captchas(self):
        for page in ('link "Register" [ref_1] href="/register"\n',
                     'generic "You must confirm your identity at the interview" [ref_1]\n',
                     'heading "Security code review engineer" [ref_1]\n',
                     'textbox "Enter the code shown" [ref_1] type="text"\n'):
            self.assertFalse(L.wall_evidence(page, L.parse_refs(page), signup=False), page)
        self.assertTrue(L.wall_evidence(self.CODE_PAGE, L.parse_refs(self.CODE_PAGE), signup=False))
        self.assertTrue(L.wall_evidence('textbox "Password" [ref_1] type="password"\n', {}, signup=False))

    def test_code_step_counts_and_a_plain_page_does_not(self):
        self.assertTrue(L.wall_evidence(self.CODE_PAGE, L.parse_refs(self.CODE_PAGE)))
        for page in ('textbox "Enter the verification code" [ref_1]\n', 'textbox "Password" [ref_1] type="password"\n',
                     'link "Sign up" [ref_1]\n'):
            self.assertTrue(L.wall_evidence(page, L.parse_refs(page)), page)
        self.assertFalse(L.wall_evidence('button "Next" [ref_1]\n', L.parse_refs('button "Next" [ref_1]\n')))


if __name__ == "__main__":
    unittest.main()


class TestWidgetAncestor(unittest.TestCase):
    PICKER = ('dialog "Apply to Example Co" [ref_1]\n'
              ' textbox "Start date" [ref_2] type="text"\n'
              ' listbox "Choose a date" [ref_3]\n'
              '  option "12 May" [ref_4]\n'
              '  button "Done" [ref_5]\n'
              ' button "Submit application" [ref_9]\n')

    def test_a_control_in_a_picker_names_its_widget(self):
        self.assertEqual(L.widget_ancestor(self.PICKER, "ref_5"), "Choose a date")

    def test_the_forms_own_submit_is_in_no_widget(self):
        self.assertIsNone(L.widget_ancestor(self.PICKER, "ref_9"))

    def test_an_unnamed_widget_gives_its_role(self):
        self.assertEqual(L.widget_ancestor('listbox [ref_20]\n button "Done" [ref_21]\n', "ref_21"), "listbox")

    def test_an_unknown_ref_is_none(self):
        self.assertIsNone(L.widget_ancestor(self.PICKER, "ref_777"))


class TestPickerIsNoFinalControl(unittest.TestCase):
    PAGE = ('dialog "Apply to Example Co" [ref_1]\n'
            ' listbox "Choose a date" [ref_3]\n'
            '  button "Done" [ref_5]\n'
            ' button "Next" [ref_8]\n')

    def check(self, page, **report):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            L.write_text(L.snapshot_path(run_dir, 7), page)
            L.write_json(L.refs_path(run_dir, 7), L.parse_refs(page))
            result = {"status": "parked", "final_button": "", "open": ["x"], "filled": ["Name"], "uploaded": None}
            result.update(report)
            state = {"tabs": {"7": {"filled": True, "acted": True}}}
            return (L.check_result(run_dir, state, {"tab": 7}, result)[0],
                    L.final_controls(L.parse_refs(page), True, page))

    def test_a_picker_done_is_no_final_control_and_the_park_is_blocked(self):
        verdict, controls = self.check(self.PAGE)
        self.assertNotIn("Done", controls)
        self.assertEqual(verdict, "BLOCKED")

    def test_a_claimed_final_button_outside_the_snapshot_is_no_evidence(self):
        verdict, _ = self.check(self.PAGE, final_button="Submit application")
        self.assertEqual(verdict, "BLOCKED")

    def test_a_real_submit_beside_a_picker_still_passes(self):
        verdict, controls = self.check(self.PAGE + ' button "Submit application" [ref_9]\n')
        self.assertEqual(controls, ["Submit application"])
        self.assertEqual(verdict, "PASS")
