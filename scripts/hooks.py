#!/usr/bin/env python3
"""Claude Code hooks for the application assistant (hooks/hooks.json -> scripts/hook.sh -> this file).

  hooks.py guard        PreToolUse   Chrome tools, Read, Edit, Write, Bash, Agent   enforces the rules
  hooks.py snapshot     PostToolUse  Chrome tools                saves page and tab data (application agents only)
  hooks.py handback     PreToolUse   SubagentHandback            checks and saves the worker result
  hooks.py capture      SubagentStop application-worker          the same, when the result is final text
  hooks.py report       Stop                                     shows the token table of the last run, once

Who is decided how (docs/CONTRACTS.md, "Spoofing rule"): the model controls tool_input, so every decision rests on
the parsed top-level fields of the hook input (agent_type, tool_name, cwd) and never on text inside tool_input.
  - ohmyjob:application-master / application-worker: the rules below. A validated call is answered with an explicit
    allow (a background agent cannot answer a permission prompt); anything else is denied (exit 2, reason on stderr).
  - ohmyjob:prep-master / screen-worker / resume-worker: handed to pj_hooks.py (its main), the prep rules.
  - the main session and every other agent: only Chrome calls are looked at (no JavaScript, no final-button click);
    everything else gets no decision.
The guard has onFailure "block": a crash of this script blocks the call.

A worker reports through the SubagentHandback tool when Claude Code uses hand-backs (auto mode). Otherwise its final
text is the report (SubagentStop). Both paths go through process_report().

Pages can be in any language: the word lists are in lexicon.py, and a button name that no list knows goes to the
Haiku check (judge.py). The guard calls Haiku outside the run lock, so the other workers do not wait for it.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# The ohmyjob modules read the workspace when they are imported, so they are imported only once main() knows the
# workspace (from the parsed cwd): see load_modules().
L: Any = None  # aa_lib
J: Any = None  # judge
X: Any = None  # lexicon
W: Any = None  # workspace

PREFIX = "mcp__claude-in-chrome__"
WORKER_TOOLS = {"navigate", "read_page", "form_input", "computer", "browser_batch", "file_upload", "tabs_context_mcp"}
MASTER_TOOLS = {"tabs_context_mcp", "tabs_create_mcp", "tabs_close_mcp", "navigate"}
BATCH_TOOLS = {"navigate", "form_input", "computer", "file_upload"}
SAFE_COMPUTER = {"wait", "scroll", "scroll_to", "hover"}
CLICKS = {"left_click", "right_click", "double_click", "triple_click"}
# The master opens one of two marker pages in its home tab (chrome-extension/, aa.py start): the LinkedIn feed when a
# queued job is on linkedin.com (it also shows whether LinkedIn is signed in), else a public LinkedIn page.
FEED_RE = re.compile(r"^(https?://)?(www\.)?linkedin\.com/feed/?(#aa-popups-as-tabs)?$", re.I)
ROBOTS_MARKER_RE = re.compile(r"^https://www\.linkedin\.com/robots\.txt#aa-popups-as-tabs$")
# At the end of a run the master loads this page in its home tab; the helper extension then puts the job
# tabs in the groups "Pending Review" and "Needs Attention" (aa.py group-plan).
GROUPS_RE = re.compile(r"^https://www\.linkedin\.com/robots\.txt\?aa-groups#aa-groups=[A-Za-z0-9%._~-]+$")
# /continue-applying: the page that brings the job tabs back from the group "Needs Attention" (aa.py adopt-plan).
ADOPT_RE = re.compile(r"^https://www\.linkedin\.com/robots\.txt\?aa-adopt#aa-adopt=[A-Za-z0-9%._~-]+$")
# The first page load of each worker is spaced out: many workers that open LinkedIn on one account at the same
# moment get "Something went wrong" pages. The guard holds a worker's first navigation until its slot; the wait
# stays under the guard's 150-second hook timeout in hooks/hooks.json.
STAGGER_SECONDS = float(os.environ.get("AA_STAGGER", "4"))
MAX_STAGGER_WAIT = 120.0
MAX_JUDGE_ROUNDS = 6  # Haiku checks for one tool call (a browser_batch can hold several new names)
ALLOW = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def load_modules() -> None:
    """Import the ohmyjob modules (they fix their paths at import, so OHMYJOB_WORKSPACE must be set first)."""
    global L, J, X, W
    import aa_lib
    import judge
    import lexicon
    import workspace

    L, J, X, W = aa_lib, judge, lexicon, workspace


class Deny(Exception):
    pass


def finish_deny(run_dir: Optional[Path], reason: str, **fields: Any) -> None:
    if L is not None:
        L.log(run_dir, "deny", reason=reason, **fields)
    sys.stderr.write(reason + "\n")
    sys.exit(2)


def finish_allow(updated: Optional[dict] = None) -> None:
    """An explicit allow for a validated call; `updated` is the whole new tool_input when the guard rewrites the call."""
    out = {"hookSpecificOutput": {**ALLOW["hookSpecificOutput"]}}
    if updated is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated
    print(json.dumps(out))
    sys.exit(0)


# --------------------------------------------------------------------------- guard context


class Ctx:
    def __init__(self, run_dir: Path, state: dict, agent_id: Optional[str]):
        self.run_dir = run_dir
        self.state = state
        self.agent_id = agent_id
        self.dirty = False
        self.wait = 0.0  # seconds to hold this call before it runs (the first page load of a worker)
        self._refs: dict[str, dict] = {}

    def entry(self, tab: Any) -> Optional[dict]:
        try:
            return self.state.get("tabs", {}).get(L.tab_key(tab))
        except (TypeError, ValueError):
            return None

    def my_tab(self) -> Optional[int]:
        for key, entry in self.state.get("tabs", {}).items():
            if entry.get("role") == "job" and self.agent_id and entry.get("agent_id") == self.agent_id:
                return int(key)
        return None

    def bind(self, tab: Any) -> bool:
        """A worker binds to its job tab with its first browser call. It may also use a sign-in
        tab that its own job tab opened (a pop-up that the helper extension opened as a tab)."""
        entry = self.entry(tab)
        if entry is None or not self.agent_id:
            return False
        role = entry.get("role")
        if role == "signin":
            mine = self.my_tab()
            if mine is None or str(entry.get("opener")) != str(mine):
                return False
            if entry.get("agent_id") is None:
                entry["agent_id"] = self.agent_id
                self.dirty = True
                L.log(self.run_dir, "bind-signin", tab=int(tab), opener=mine, agent_id=self.agent_id)
            return entry.get("agent_id") == self.agent_id
        if role != "job":
            return False
        if entry.get("agent_id") is None:
            mine = self.my_tab()
            if mine is not None and str(mine) != L.tab_key(tab):
                return False
            entry["agent_id"] = self.agent_id
            self.dirty = True
            L.log(self.run_dir, "bind", tab=int(tab), folder=entry.get("folder"), agent_id=self.agent_id)
            return True
        return entry.get("agent_id") == self.agent_id

    def url(self, tab: Any) -> Optional[str]:
        return (self.state.get("tab_urls") or {}).get(L.tab_key(tab))

    def host(self, tab: Any) -> Optional[str]:
        return L.host_of(self.url(tab))

    def set_url(self, tab: Any, url: str) -> None:
        self.state.setdefault("tab_urls", {})[L.tab_key(tab)] = url
        entry = self.entry(tab)
        if entry is not None:
            L.track_google(entry, url)
        self.dirty = True

    def refs(self, tab: Any) -> dict:
        key = L.tab_key(tab)
        if key not in self._refs:
            self._refs[key] = L.snapshot_refs(self.run_dir, tab)
        return self._refs[key]

    def filled(self, tab: Any) -> bool:
        """True once this tab showed an application form: a fill outside a search box, an upload, or a page with
        the shape of a form (snapshot; it also covers a form that the site filled already)."""
        entry = self.entry(tab) or {}
        return bool(entry.get("filled"))

    def mark_filled(self, tab: Any) -> None:
        entry = self.entry(tab)
        if entry is not None and not entry.get("filled"):
            entry["filled"] = True
            entry["filled_host"] = self.host(tab)
            self.dirty = True

    def on_navigate(self, tab: Any, url: str) -> None:
        full = url if "://" in url else "https://" + url
        entry = self.entry(tab)
        new_host = L.host_of(full)
        if entry is not None:
            job = self.job(tab) or {}
            restart = entry.get("sticky") and L.job_key(full) in {L.job_key(job.get("url")), L.job_key(job.get("apply_url"))}
            if restart:
                # /continue-applying: the worker starts this job over from its job (or apply) address, as /apply-jobs does
                entry["sticky"] = False
                entry["filled"] = False
                entry["filled_host"] = None
            elif entry.get("filled") and not entry.get("sticky") and entry.get("filled_host") != new_host:
                entry["filled"] = False
                entry["filled_host"] = None
            entry["focus_role"] = None
            entry["last_click"] = None
        # refs of the old page are void: clicks need a new read_page first
        refs_file = L.refs_path(self.run_dir, tab)
        if refs_file.exists():
            refs_file.unlink()
        self._refs.pop(L.tab_key(tab), None)
        self.set_url(tab, full)

    def focus(self, tab: Any) -> Optional[str]:
        return (self.entry(tab) or {}).get("focus_role")

    def focus_ref(self, tab: Any) -> Optional[str]:
        return (self.entry(tab) or {}).get("focus_ref")

    def set_focus(self, tab: Any, role: Optional[str], ref: Optional[str] = None) -> None:
        entry = self.entry(tab)
        if entry is not None and (entry.get("focus_role"), entry.get("focus_ref")) != (role, ref):
            entry["focus_role"] = role
            entry["focus_ref"] = ref
            self.dirty = True

    def set_last_click(self, tab: Any, ref: str) -> None:
        """The last button or link clicked in this tab; read_page clears it (snapshot)."""
        entry = self.entry(tab)
        if entry is not None and entry.get("last_click") != ref:
            entry["last_click"] = ref
            self.dirty = True

    def job(self, tab: Any) -> Optional[dict]:
        entry = self.entry(tab) or {}
        return self.state.get("jobs", {}).get(entry.get("folder") or "")

    def pdf_for(self, tab: Any) -> Optional[str]:
        job = self.job(tab)
        return job.get("pdf") if job else None

    def counter(self, tab: Any, name: str) -> int:
        return int((self.entry(tab) or {}).get(name) or 0)

    def add(self, tab: Any, name: str) -> None:
        entry = self.entry(tab)
        if entry is not None:
            entry[name] = int(entry.get(name) or 0) + 1
            self.dirty = True

    # ---- Haiku checks (judge.py): the result comes from the cache, or the guard asks for it

    def judged(self, kind: str, payload: dict) -> dict:
        key = J.key_for(kind, payload)
        entry = J.cached(self.run_dir, key) or {}
        if "result" in entry:
            return entry["result"]
        if int(entry.get("failures") or 0) >= J.MAX_FAILURES:
            raise Deny(judge_failed(kind, payload, entry.get("error") or "no answer"))
        raise L.NeedJudge(kind, payload)

    def judged_click(self, payload: dict) -> dict:
        """A name that Haiku called context-free is checked one time per run; else per page context."""
        short = {k: payload[k] for k in ("name", "role", "filled")}
        entry = J.cached(self.run_dir, J.key_for("click", short)) or {}
        if "result" in entry:
            return entry["result"]
        return self.judged("click", payload)


def judge_failed(kind: str, payload: dict, error: str) -> str:
    if kind == "click":
        return (f"The Haiku check could not check '{payload.get('name')}' ({error}). Do not click it. If this is "
                "the step with the final button, stop with status 'parked'; else list it in 'open' and stop.")
    return f"The Haiku check failed ({error})."


# --------------------------------------------------------------------------- rules


def element(ctx: Ctx, tab: Any, ref: Optional[str]) -> dict:
    if not ref:
        raise Deny("Click and fill only by a ref from read_page.")
    item = ctx.refs(tab).get(ref)
    if item is None:
        raise Deny(f"Unknown ref {ref}. Call read_page alone (not in browser_batch) first, then use its refs.")
    return item


BOX_ROLES = {"checkbox", "switch", "menuitemcheckbox"}


def check_click_name(ctx: Ctx, tab: Any, ref: str, item: dict) -> None:
    name = item.get("name") or ""
    role = item.get("role") or ""
    attrs = item.get("attrs") or {}
    filled = ctx.filled(tab)
    # /continue-applying: the candidate may have filled part of the application by hand, so the Haiku check decides about Apply
    sticky = bool((ctx.entry(tab) or {}).get("sticky"))
    choice = role in L.CHOICE_ROLES  # a checkbox, a radio button or an option never sends a form
    word = None if choice else X.FINAL.find(name)
    if word:
        raise Deny(f"'{name}' is a final control ('{word}'). Do not click it. Stop here with status 'parked'.")
    if role == "button" and not name.strip() and attrs.get("type") == "submit":
        raise Deny("This is a submit button without a name. Do not click it. If this is the step with the final button, "
                   "stop with status 'parked'; else stop with status 'blocked'.")
    apply_word = None if choice else X.APPLY.find(name)
    if filled and apply_word:
        raise Deny(f"'{name}' can send the application: this tab showed an application form. Do not click it. If the "
                   "form is filled up to this button, stop here with status 'parked'; else fill the form first.")
    context = L.context_names(L.snapshot_text(ctx.run_dir, tab), ref)
    consent = X.CONSENT.find(name)
    if consent:
        if X.COOKIE.find(name) or X.COOKIE.find(context):
            raise Deny("This is a cookie banner. Click its most private choice (Reject all, Decline, Only necessary).")
        if X.PERMISSION.find(name) or L.OAUTH_URL_RE.search(ctx.url(tab) or ""):
            raise Deny(f"'{name}' gives a sign-in permission. Not allowed. If you cannot go on, stop with status 'blocked'.")
    if X.DELETE.find(name):
        raise Deny(f"'{name}' deletes or withdraws data. Not allowed.")
    google = X.GOOGLE.find(name)
    if X.SIGNUP.find(name) and not google:
        raise Deny(f"'{name}' creates an account with an email and a password. Not allowed. Use a Google option if the "
                   "page has one, else stop with status 'blocked'.")
    # A name that no list knows: the Haiku check decides (button, or a link that runs a page action).
    # a Google name after a fill goes to the Haiku check: the extension names icon-only buttons by their logo
    if not name.strip() or consent or (google and not filled) or (apply_word and not filled and not sticky) or name in X.SAFE:
        return
    if role == "button" or (role == "link" and not L.navigable_href(attrs.get("href"))):
        payload = {"name": name[:300], "role": role, "context": context[:600], "host": ctx.host(tab) or "unknown",
                   "filled": filled or ("unknown" if sticky else False)}
        result = ctx.judged_click(payload)
        if result.get("verdict") != "allow":
            raise Deny(f"The Haiku check blocked '{name}' ({result.get('reason') or 'it can send the application'}). "
                       "Do not click it. If this is the step with the final button, stop here with status 'parked'.")


def check_google_click(ctx: Ctx, tab: Any, ref: str, item: dict) -> None:
    """On accounts.google.com, in any language: one click on the candidate's account (the entry that shows an email
    address, in its own name or in the text inside it), then one button on the consent page, only when
    the site asks for the basic profile."""
    entry = ctx.entry(tab) or {}
    role = item.get("role") or ""
    if L.GOOGLE_CHALLENGE_RE.search(ctx.url(tab) or ""):
        raise Deny("Google asks for a password, a code or a check. Stop with status 'blocked'.")
    refs = ctx.refs(tab)
    inside = L.subtree_names(L.snapshot_text(ctx.run_dir, tab), ref)
    if any(L.EMAIL_RE.search(n) for n in inside):
        # the account entry: the link that holds the email address, or the email text itself
        if ctx.counter(tab, "google_clicks") >= 1:
            raise Deny("Only one account click is allowed on Google. Stop with status 'blocked'.")
        ctx.add(tab, "google_clicks")
        return
    if not inside:
        raise Deny("On Google pages the account entries have no name in the interactive view. Call read_page alone "
                   "with filter 'all' on this page, then click the entry that shows your email address.")
    if role != "button":
        raise Deny("On Google pages, click only your account (the entry with your email address) and then one button "
                   "on the consent page. If Google wants more, stop with status 'blocked'.")
    if L.GOOGLE_CHOOSER_RE.search(ctx.url(tab) or "") and ctx.counter(tab, "google_clicks") == 0:
        raise Deny("This is Google's account list: click your account first (the entry with your email address).")
    access = L.google_access(entry)
    if access == "more":
        raise Deny("The site asks for more Google access than the basic profile. Do not continue. Stop with status 'blocked'.")
    if access is None:
        raise Deny("The hook cannot see which Google access the site asks for. Do not continue. Stop with status 'blocked'.")
    if any(r.get("role") in BOX_ROLES for r in refs.values()):
        raise Deny("This Google page has choices for more access. Do not continue. Stop with status 'blocked'.")
    if ctx.counter(tab, "google_continue") >= 1:
        raise Deny("A button on this Google page was clicked already. Stop with status 'blocked'.")
    ctx.add(tab, "google_continue")


def check_worker_action(ctx: Ctx, short: str, ti: dict, in_batch: bool = False) -> Optional[dict]:
    """Raise Deny (or L.NeedJudge), or return an updated input (or None for no change)."""
    if short not in WORKER_TOOLS:
        raise Deny(f"{short} is not available to the worker.")
    if in_batch and short not in BATCH_TOOLS:
        raise Deny(f"Do not put {short} inside browser_batch. Call it alone.")
    if short == "tabs_context_mcp":
        if ti.get("createIfEmpty"):
            raise Deny("Do not create tab groups or tabs.")
        return None
    if short == "browser_batch":
        actions = ti.get("actions") or []
        changed = False
        new_actions = []
        for i, action in enumerate(actions):
            raw = str(action.get("name") or "")
            if not raw:
                raise Deny(f"browser_batch item {i + 1}: give each item a 'name' (a short tool name, for example "
                           "form_input) and an 'input'.")
            # browser_batch knows the short names only ("form_input", not "mcp__claude-in-chrome__form_input")
            name = raw[len(PREFIX):] if raw.startswith(PREFIX) else raw
            try:
                updated = check_worker_action(ctx, name, action.get("input") or {}, in_batch=True)
            except Deny as exc:
                raise Deny(f"browser_batch item {i + 1} ({name}): {exc}") from None
            if updated is not None or name != raw:
                new_actions.append({**action, "name": name, "input": updated if updated is not None else action.get("input")})
                changed = True
            else:
                new_actions.append(action)
        return {**ti, "actions": new_actions} if changed else None
    tab = ti.get("tabId")
    if tab is None:
        raise Deny("Give your tabId in every browser call.")
    if not ctx.bind(tab):
        mine = ctx.my_tab()
        raise Deny(f"Use only your own tab ({mine if mine is not None else 'the tab id in your task'}). Never use another tab.")
    signin = (ctx.entry(tab) or {}).get("role") == "signin"
    google = L.is_google_host(ctx.host(tab))
    if signin and short in ("navigate", "form_input", "file_upload"):
        raise Deny("In the sign-in tab, only read the page and click. Do not navigate, fill or upload there.")

    if short == "navigate":
        url = str(ti.get("url") or "").strip()
        if url in ("back", "forward"):
            return None
        if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) and not re.match(r"^https?://", url, re.I):
            raise Deny("Navigate only to http or https addresses.")
        if "…" in url:
            prefix = url.split("…")[0].rstrip("?")
            full = {h for h in L.HREF_RE.findall(L.snapshot_text(ctx.run_dir, tab)) if h.startswith(prefix)}
            if "://" not in prefix or len(full) != 1:
                raise Deny("This link is shortened in your view. Read the link with read_page and its ref_id to see its full address.")
            url = full.pop()
            if not re.match(r"^https?://", url, re.I):
                raise Deny("Navigate only to http or https addresses.")
        if (ctx.entry(tab) or {}).get("sticky") and X.SUBMITTED.find(L.snapshot_text(ctx.run_dir, tab)):
            # /continue-applying: the candidate may have sent this application by hand and left the tab open
            raise Deny("This page says the application was sent. Do not start it again: stop with status 'blocked', "
                       "blocker 'already submitted'.")
        url = L.greenhouse_form_url(url) or L.linkedin_target(url)  # straight to the form or the company page
        if FEED_RE.match(url) and "#" in url or GROUPS_RE.match(url) or ADOPT_RE.match(url) or "robots.txt" in url and "#aa-" in url:
            raise Deny("This page is for the master only.")  # it would take over the run's tab groups
        entry = ctx.entry(tab)
        if entry is not None and entry.get("role") == "job" and not entry.get("started"):
            # the worker's first page load: take the next free start slot (see STAGGER_SECONDS)
            now = time.time()
            start = max(now, float(ctx.state.get("next_start") or 0))
            ctx.state["next_start"] = start + STAGGER_SECONDS
            entry["started"] = True
            ctx.dirty = True
            ctx.wait = min(start - now, MAX_STAGGER_WAIT)
        ctx.on_navigate(tab, url)
        return {**ti, "url": url} if url != str(ti.get("url") or "").strip() else None

    if short == "read_page":
        # the whole page; the snapshot hook keeps it for the refs and gives the worker a compact copy
        updated = dict(ti)
        if updated.get("filter") not in L.COMPACT_CAP:
            updated["filter"] = "interactive"
        updated["max_chars"] = L.FULL_READ
        return updated if updated != ti else None

    if short == "file_upload":
        if google:
            raise Deny("No uploads on Google pages.")
        expected = ctx.pdf_for(tab)
        paths = ti.get("paths") or ([ti["file_path"]] if ti.get("file_path") else [])
        if not expected or len(paths) != 1 or str(Path(str(paths[0])).expanduser().resolve()) != expected:
            raise Deny(f'Upload only this one file, as "paths": ["{expected}"]')
        element(ctx, tab, ti.get("ref"))
        ctx.mark_filled(tab)
        ctx.set_focus(tab, None)
        clean = {"tabId": tab, "ref": ti.get("ref"), "paths": [expected]}  # no "files", no "file_path"
        return clean if clean != ti else None

    if short == "form_input":
        if google:
            raise Deny("Do not type or fill on Google pages. If Google asks for a password or a code, stop with status 'blocked'.")
        ref = ti.get("ref")
        item = element(ctx, tab, ref)
        role = item.get("role") or ""
        if role in L.CLICKABLE_ROLES and role not in L.TICK_ROLES:
            raise Deny("form_input is for fields. Click buttons and links with computer left_click and the ref.")
        if not L.is_search_field(item):
            ctx.mark_filled(tab)
        ctx.set_focus(tab, role, ref)
        return None

    if short == "computer":
        action = ti.get("action")
        if action in ("screenshot", "zoom"):
            scale = ti.get("scale")
            if not isinstance(scale, (int, float)) or scale > 0.5:
                return {**ti, "scale": 0.5}
            return None
        if action in SAFE_COMPUTER:
            return None
        if action == "key":
            keys = str(ti.get("text") or "")
            if google or signin:
                raise Deny("Do not press keys on Google pages.")
            if L.ACTIVATE_KEY_RE.search(keys):
                raise Deny("Do not press Enter or Space: they can press a focused button. Click the control by its ref instead.")
            if L.TAB_KEY_RE.search(keys):
                ctx.set_focus(tab, None)
            return None
        if action == "type":
            if google or signin:
                raise Deny("Do not type on Google pages.")
            text = str(ti.get("text") or "")
            if "\n" in text or "\r" in text:
                raise Deny("No line breaks in a type action. Use form_input for text with line breaks.")
            if ctx.focus(tab) not in L.TYPE_ROLES:
                raise Deny("Type only right after you click a text field by its ref. Better: use form_input with the field's ref.")
            if not L.is_search_field(ctx.refs(tab).get(ctx.focus_ref(tab) or "") or {}):
                ctx.mark_filled(tab)
            return None
        if action in CLICKS:
            if not ti.get("ref"):
                raise Deny("Click only by a ref from read_page, never by coordinates.")
            note = ti.get("action_summary")
            if not isinstance(note, str) or not note.strip():
                raise Deny("Write action_summary on every click: a few English words that say what the click does.")
            item = element(ctx, tab, ti.get("ref"))
            role = item.get("role") or ""
            if action in ("double_click", "triple_click") and role not in L.TYPE_ROLES:
                raise Deny("Use left_click: a double or triple click presses a button more than once.")
            pressable = role not in L.CHOICE_ROLES and role not in L.TYPE_ROLES
            if pressable and (ctx.entry(tab) or {}).get("last_click") == ti["ref"]:
                # the same element can change its name after a click (Next becomes Submit); the check knows only the old name
                raise Deny("You clicked this control already and read no page since. Call read_page first, for the whole "
                           "page (without ref_id): its name can change after a click (Next can become Submit).")
            problem = L.note_problem(note, ctx.filled(tab))
            if problem:
                raise Deny(f"Your note says that this click can send the application ('{problem}'). Do not click it. "
                           "Stop here with status 'parked'.")
            if google:
                check_google_click(ctx, tab, ti["ref"], item)
                ctx.set_focus(tab, None)
                return None
            if signin:
                raise Deny("This sign-in tab is not a Google page. Do not use it. If you cannot go on in your own tab, "
                           f"stop with status 'blocked' (blocker: sign-in pop-up of {ctx.host(tab) or 'another site'}).")
            check_click_name(ctx, tab, ti["ref"], item)
            ctx.set_focus(tab, role, ti["ref"])
            if pressable:
                ctx.set_last_click(tab, ti["ref"])
            return None
        raise Deny(f"computer action '{action}' is not allowed.")

    raise Deny(f"{short} is not available to the worker.")


def check_master_action(ctx: Ctx, short: str, ti: dict) -> Optional[dict]:
    if short not in MASTER_TOOLS:
        raise Deny(f"{short} is not available to the master. Workers do the browser work.")
    if short == "tabs_close_mcp":
        if L.job_tab_owner(ctx.state, ti.get("tabId")):
            raise Deny("This tab holds a job application. It must stay open.")
    if short == "navigate":
        tab = ti.get("tabId")
        if tab is None:
            raise Deny("Give the tabId.")
        if L.job_tab_owner(ctx.state, tab):
            raise Deny("This tab holds a job application. Do not navigate it.")
        url = str(ti.get("url") or "").strip()
        if GROUPS_RE.match(url):
            if tab != ctx.state.get("home_tab"):
                raise Deny("Load the group page only in the home tab (the tab of the preflight check).")
            if ctx.state.get("status") != "done" or url != ctx.state.get("group_url"):
                raise Deny("Load only the group page that 'ohmyjob apply group-plan' printed, after 'ohmyjob apply finish'.")
        elif ADOPT_RE.match(url):
            if tab != ctx.state.get("home_tab") or url != ctx.state.get("adopt_url"):
                raise Deny("Load only the page that 'ohmyjob apply adopt-plan' printed, in the home tab.")
        elif not (FEED_RE.match(url) or ROBOTS_MARKER_RE.match(url)):
            raise Deny("The master navigates only to the home_url that 'ohmyjob apply start' printed, and to the pages "
                       "that 'ohmyjob apply adopt-plan' and 'ohmyjob apply group-plan' print.")
        ctx.on_navigate(tab, str(ti.get("url")))
    return None


def check_any_action(ctx: Optional[Ctx], short: str, ti: dict) -> None:
    """The main-session rules, for every Chrome caller that is not an ohmyjob agent (the main session, Explore and any
    other subagent): no final clicks, no JavaScript."""
    if short == "javascript_tool":
        raise Deny("JavaScript is not allowed in this workspace.")
    if short == "computer" and ti.get("action") in CLICKS and ti.get("ref") and ctx is not None:
        item = ctx.refs(ti.get("tabId")).get(ti.get("ref")) if ti.get("tabId") is not None else None
        name = (item or {}).get("name") or ""
        if X.FINAL.find(name):
            raise Deny(f"'{name}' is a final control. The candidate submits applications personally.")
    if short == "browser_batch":
        for action in ti.get("actions") or []:
            name = str(action.get("name") or "")
            name = name[len(PREFIX):] if name.startswith(PREFIX) else name
            check_any_action(ctx, name, action.get("input") or {})


def read_allowed(run_dir: Optional[Path], file_path: str) -> bool:
    """The worker may read its brief (run folder, jobs/) and the plugin's ATS notes (outside the workspace)."""
    if run_dir is None:
        return False
    try:
        target = Path(file_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    allowed = [W.ATS_DIR.resolve(), (run_dir / "jobs").resolve()]
    return any(target == base or base in target.parents for base in allowed)


TIME_UP = ("Time is up: your 17 minutes are over. Make no more tool calls (the hook denies them all). Send your "
           "final report now with status \"blocked\", a \"blocker\" that starts with \"TIMEOUT:\" and says what is "
           "left to do, and everything you filled. The candidate finishes it by hand in your tab.")


def worker_timed_out(state: dict, agent_id: Optional[str]) -> bool:
    """The worker's time limit (L.WORKER_SECONDS) is over: counted from the master's Agent call."""
    for entry in state.get("tabs", {}).values():
        if entry.get("role") == "job" and agent_id and entry.get("agent_id") == agent_id:
            started = (state.get("jobs", {}).get(entry.get("folder")) or {}).get("spawned_at")
            return started is not None and time.time() - started > L.WORKER_SECONDS
    return False


def master_command_ok(ti: dict) -> bool:
    """The master's Bash: exactly one `ohmyjob apply ...` command of the allowed list (W.APPLICATION_MASTER_CMD_RE)."""
    command = ti.get("command")
    return isinstance(command, str) and bool(W.APPLICATION_MASTER_CMD_RE.fullmatch(command.strip()))


def guard(inp: dict) -> None:
    tool = str(inp.get("tool_name") or "")
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    agent_type = inp.get("agent_type") if isinstance(inp.get("agent_type"), str) else None
    agent_id = inp.get("agent_id")
    run_dir = L.current_run_dir()

    if agent_type not in W.APPLICATION_AGENTS:
        # the main session and any other agent: only Chrome calls are checked; every other call gets no decision
        if not tool.startswith(PREFIX):
            sys.exit(0)
        ctx = Ctx(run_dir, L.load_state(run_dir), agent_id) if run_dir is not None else None
        try:
            check_any_action(ctx, tool[len(PREFIX):], ti)
        except Deny as exc:
            finish_deny(run_dir, str(exc), tool=tool, agent_type=agent_type)
        sys.exit(0)

    if agent_type == L.MASTER and run_dir is not None and agent_id:
        # where the run's transcripts are, for the token table of 'end'
        L.write_json(run_dir / "master.json", {"agent_id": agent_id, "transcript": inp.get("transcript_path")})

    # ---- ohmyjob application agents: validate, then allow explicitly (or deny)
    if tool == "Read":
        if agent_type != L.WORKER or not read_allowed(run_dir, str(ti.get("file_path") or "")):
            finish_deny(run_dir, "The worker may read only its brief and the ATS notes of the plugin (its ats/ folder); "
                                 "the master reads no files.", tool=tool, agent_type=agent_type)
        finish_allow()
    if tool == "Bash":
        if agent_type != L.MASTER or not master_command_ok(ti):
            finish_deny(run_dir, "Run only the 'ohmyjob apply ...' commands of your steps, one command and nothing else.",
                        tool=tool, agent_type=agent_type, command=str(ti.get("command"))[:200])
        finish_allow()
    if tool in ("Edit", "Write"):
        finish_deny(run_dir, "This agent edits no files.", tool=tool, agent_type=agent_type)
    if tool == "Agent":
        # `tools: Agent(...)` in an agent file restricts nothing for plugin agents: the type is checked here
        allowed = W.MASTER_WORKERS.get(agent_type or "", frozenset())
        if str(ti.get("subagent_type") or "") not in allowed:
            finish_deny(run_dir, f"{agent_type} starts only: {', '.join(sorted(allowed)) or 'no agents'}.",
                        tool=tool, agent_type=agent_type, subagent_type=ti.get("subagent_type"))
        match = re.match(r"folder=(\S+) ", str(ti.get("prompt") or ""))
        if match and run_dir is not None:
            # the worker really starts ('next' in aa.py prints a dropped worker again)
            with L.locked(run_dir):
                state = L.load_state(run_dir)
                job = state.get("jobs", {}).get(match.group(1))
                if job is not None:
                    job.update({"spawned": True, "spawned_at": time.time()})
                    L.save_state(run_dir, state)
        finish_allow()
    if not tool.startswith(PREFIX):
        sys.exit(0)
    short = tool[len(PREFIX):]

    if run_dir is None:
        finish_deny(None, "No active application run. Start it with /ohmyjob:apply-jobs.")
    if agent_type == L.WORKER and worker_timed_out(L.load_state(run_dir), agent_id):
        finish_deny(run_dir, TIME_UP, tool=tool, agent_id=agent_id)
    updated = None
    reason = None
    wait = 0.0
    for _round in range(MAX_JUDGE_ROUNDS):
        pending: Optional[L.NeedJudge] = None
        with L.locked(run_dir):
            state = L.load_state(run_dir)
            # after 'finish' the master still closes its own tabs
            cleanup = agent_type == L.MASTER and state.get("status") == "done" and (
                short in ("tabs_context_mcp", "tabs_close_mcp")
                or (short == "navigate" and GROUPS_RE.match(str(ti.get("url") or "").strip())))
            if state.get("status") != "running" and not cleanup:
                reason = f"The run is not active (status: {state.get('status')})."
            else:
                ctx = Ctx(run_dir, state, agent_id)
                try:
                    if agent_type == L.WORKER:
                        updated = check_worker_action(ctx, short, ti)
                    else:
                        updated = check_master_action(ctx, short, ti)
                except Deny as exc:
                    reason = str(exc)
                except L.NeedJudge as need:
                    pending = need
                if ctx.dirty and reason is None and pending is None:
                    L.save_state(run_dir, state)
                    wait = ctx.wait
        if pending is None:
            break
        # the Haiku check runs outside the run lock; the next round finds its result in the cache
        result, error = J.ask(run_dir, pending.kind, pending.payload)
        L.log(run_dir, "judge", kind=pending.kind, payload=pending.payload, result=result, error=error or None,
              agent_id=agent_id)
        if result is None:
            reason = judge_failed(pending.kind, pending.payload, error)
            break
        # a name is cached for the whole run only when its fill state is known: in /continue-applying an allowed
        # "Apply" that opened one form must not allow a final "Apply" on a review step of another tab
        if pending.kind == "click" and result.get("context_dependent") is False and pending.payload.get("filled") != "unknown":
            short_payload = {k: pending.payload[k] for k in ("name", "role", "filled")}
            J.store(run_dir, J.key_for("click", short_payload), {"result": result, "payload": short_payload})
    else:
        reason = "Too many new names in one call. Click one control at a time."
    if reason is not None:
        finish_deny(run_dir, reason, tool=short, agent_type=agent_type, agent_id=agent_id, tab=ti.get("tabId"))
    if wait > 0:
        time.sleep(wait)  # outside the run lock: the other workers go on meanwhile
    finish_allow(updated)


# --------------------------------------------------------------------------- snapshot


def save_page(run_dir: Path, tab: Any, text: str, partial: bool) -> bool:
    """Save a read_page result as the tab's page and refs. False for an error (nothing saved)."""
    refs = L.parse_refs(text)
    if not refs and "Tab Context:" not in text:
        return False  # an error, not a page: keep the last page and its refs (a page without refs, such as "submitted", is saved)
    path = L.snapshot_path(run_dir, tab)
    if partial and path.exists():
        # keep the whole page first: the labels and questions around a ref are looked up there (first match)
        text = path.read_text(encoding="utf-8") + "\n" + text
    L.write_text(path, text)
    if partial:
        merged = L.snapshot_refs(run_dir, tab)
        merged.update(refs)
        refs = merged
    L.write_json(L.refs_path(run_dir, tab), refs)
    return True


def observe_tabs(state: dict, tabs: dict[int, dict]) -> bool:
    """Record the URL and title of every tab that a tool result lists. A tab whose title starts
    with "[AA from N]" (the helper extension) and that job tab N opened becomes its sign-in tab."""
    urls = state.setdefault("tab_urls", {})          # best known URL per tab (guard)
    seen = state.setdefault("tab_urls_seen", {})     # URL that a tool output reported (preflight)
    titles = state.setdefault("tab_titles", {})
    registry = state.setdefault("tabs", {})
    changed = False
    for tid, info in tabs.items():
        key = str(tid)
        url = info.get("url") or ""
        title = info.get("title") or ""
        if url and (urls.get(key) != url or seen.get(key) != url):
            urls[key] = url
            seen[key] = url
            changed = True
        if title and titles.get(key) != title:
            titles[key] = title
            changed = True
        match = L.CHILD_TITLE_RE.match(title)
        if match and key not in registry:
            opener = registry.get(match.group(1)) or {}
            if opener.get("role") == "job":
                registry[key] = {"role": "signin", "opener": int(match.group(1)), "agent_id": None,
                                 "google_clicks": 0, "google_continue": 0}
                changed = True
        entry = registry.get(key)
        if entry is not None and url and L.track_google(entry, url):
            changed = True
    return changed


def snapshot(inp: dict) -> None:
    tool = str(inp.get("tool_name") or "")
    if not tool.startswith(PREFIX) or inp.get("agent_type") not in W.APPLICATION_AGENTS:
        sys.exit(0)
    run_dir = L.current_run_dir()
    if run_dir is None:
        sys.exit(0)
    short = tool[len(PREFIX):]
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    text = L.tool_text(inp.get("tool_response"))
    replace = None
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        changed = False
        page_saved = False
        if short == "read_page" and ti.get("tabId") is not None:
            page_saved = save_page(run_dir, ti["tabId"], text, partial=bool(ti.get("ref_id")))
            if inp.get("agent_type") == L.WORKER:
                compact = L.compact_page(text, ti.get("filter") or "interactive", cut_options=not ti.get("ref_id"))
                if compact != text:
                    replace = L.replace_tool_text(inp.get("tool_response"), compact)
        elif short == "navigate" and ti.get("tabId") is not None:
            L.write_text(L.snap_dir(run_dir) / f"navigate_{L.tab_key(ti['tabId'])}.txt", text)
            url = ti.get("url")
            if url and url not in ("back", "forward"):
                full = url if "://" in url else "https://" + url
                state.setdefault("tab_urls", {})[L.tab_key(ti["tabId"])] = full
                entry = state.get("tabs", {}).get(L.tab_key(ti["tabId"]))
                if entry is not None:
                    L.track_google(entry, full)
                changed = True
        elif short == "tabs_create_mcp":
            L.write_text(L.snap_dir(run_dir) / "tabs_create.txt", text)
        if short == "tabs_context_mcp":
            tabs_list = L.parse_tabs_context(text)
            L.write_json(L.snap_dir(run_dir) / "tabs_context.json", tabs_list)
            L.write_text(L.snap_dir(run_dir) / "tabs_context.txt", text)
            if state.get("initial_tabs") is None and inp.get("agent_type") == L.MASTER:
                state["initial_tabs"] = [t["id"] for t in tabs_list]
                changed = True
            tabs = {t["id"]: {"title": t.get("title") or "", "url": t.get("url") or ""} for t in tabs_list}
        else:
            # every other result ends with a Tab Context block: URLs and titles of all tabs in the group
            _executed, tabs = L.tab_context(text)
        if observe_tabs(state, tabs):
            changed = True
        entry = state.get("tabs", {}).get(L.tab_key(ti["tabId"])) if page_saved else None
        if entry is not None:
            if not entry.get("filled") and L.looks_like_form(L.snapshot_refs(run_dir, ti["tabId"])):
                # the tab shows an application form, also one that the site filled: Apply is final from now on
                entry["filled"] = True
                entry["filled_host"] = L.host_of(state["tab_urls"].get(L.tab_key(ti["tabId"])))
                changed = True
            if not ti.get("ref_id") and entry.get("last_click"):
                entry["last_click"] = None  # the worker saw the page after its click (a part read may miss that control)
                changed = True
        if changed:
            L.save_state(run_dir, state)
    if replace is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": replace}}))
    sys.exit(0)


# --------------------------------------------------------------------------- capture


def block_stop(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def process_report(run_dir: Path, agent_id: Optional[str], message: str, again: str, source: str) -> tuple[str, str, dict]:
    """Check a worker report and save it as result.json.

    Returns (action, reason, info): action "retry" (send the worker back with reason),
    "saved" (result.json written; info has folder and status) or "skip" (no job for this worker)."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        folder = None
        for _key, entry in state.get("tabs", {}).items():
            if entry.get("role") == "job" and agent_id and entry.get("agent_id") == agent_id:
                folder = entry.get("folder")
                break
        obj = L.extract_json_object(message)
        if folder is None and isinstance(obj, dict) and obj.get("folder") in state.get("jobs", {}):
            folder = obj["folder"]
        if folder is None:
            L.log(run_dir, "capture-skip", agent_id=agent_id, source=source, note="no tab bound to this worker")
            return "skip", "", {}
        job = state["jobs"][folder]
        result_path = L.job_dir(run_dir, folder) / "result.json"
        errors = L.validate_result(obj, folder, job["tab"])
        if errors:
            if job.get("capture_blocks", 0) < L.MAX_CAPTURE_BLOCKS:
                job["capture_blocks"] = job.get("capture_blocks", 0) + 1
                L.save_state(run_dir, state)
                reason = (
                    "Your report must be ONE JSON object with the keys folder, tab, status, where, "
                    "final_button, url, filled, uploaded, drafted, open, questions, declarations, blocker. Problems: "
                    + " ".join(errors) + " " + again
                )
                return "retry", reason, {"folder": folder}
            L.write_json(result_path, {"invalid": True, "errors": errors, "raw": message[-2000:], "agent_id": agent_id})
            L.log(run_dir, "capture-invalid", folder=folder, source=source, errors=errors)
            return "saved", "", {"folder": folder, "status": "invalid"}
        verdict, reasons = L.check_result(run_dir, state, job, obj)
        if verdict == "FAIL" and job.get("capture_blocks", 0) < L.MAX_CAPTURE_BLOCKS:
            job["capture_blocks"] = job.get("capture_blocks", 0) + 1
            L.save_state(run_dir, state)
            return "retry", "Before you stop: " + " ".join(reasons) + " Fix this in your tab. " + again, {"folder": folder}
        obj["captured"] = L.now_iso()
        obj["agent_id"] = agent_id
        L.write_json(result_path, obj)
        L.log(run_dir, "capture", folder=folder, source=source, status=obj.get("status"), check=verdict)
        return "saved", "", {"folder": folder, "status": obj.get("status")}


def saved_for(run_dir: Path, agent_id: Optional[str]) -> bool:
    """True if a result of this worker is already saved (for example by the handback hook)."""
    if not agent_id:
        return False
    state = L.load_state(run_dir)
    for folder in state.get("jobs", {}):
        result = L.read_json(L.job_dir(run_dir, folder) / "result.json", None)
        if isinstance(result, dict) and result.get("agent_id") == agent_id:
            return True
    return False


def handback(inp: dict) -> None:
    """PreToolUse on SubagentHandback: the worker's final report.

    A bad report is refused (exit 2): the worker stays in its tab, corrects it and calls again.
    A good report is saved, and the master gets one short line instead of the full report."""
    if inp.get("agent_type") != L.WORKER:
        sys.exit(0)
    run_dir = L.current_run_dir()
    if run_dir is None:
        sys.exit(0)
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    message = str(ti.get("message") or "")
    action, reason, info = process_report(
        run_dir, inp.get("agent_id"), message,
        again="Then call SubagentHandback again, with only the corrected JSON object as the message.",
        source="handback",
    )
    if action == "retry":
        L.log(run_dir, "handback-deny", agent_id=inp.get("agent_id"), reason=reason[:300])
        sys.stderr.write(reason + "\n")
        sys.exit(2)
    if action == "saved":
        short = json.dumps({"folder": info.get("folder"), "status": info.get("status"), "result": "saved by the hook"})
        finish_allow({**ti, "message": short})
    sys.exit(0)


def capture(inp: dict) -> None:
    """SubagentStop of a worker: the report is its final text, when Claude Code does not use hand-backs."""
    if inp.get("agent_type") != L.WORKER:
        sys.exit(0)
    run_dir = L.current_run_dir()
    if run_dir is None:
        sys.exit(0)
    agent_id = inp.get("agent_id")
    message = inp.get("last_assistant_message") or ""
    if not message.strip() or saved_for(run_dir, agent_id):
        # The last message was the SubagentHandback call (its hook checked the report), or the
        # report is saved already. A block cannot help after a hand-back: the run has ended.
        sys.exit(0)
    action, reason, _info = process_report(
        run_dir, agent_id, message, again="Reply again with only the corrected JSON object.", source="stop"
    )
    if action == "retry":
        L.log(run_dir, "capture-block", agent_id=agent_id, reason=reason[:300])
        block_stop(reason)
    sys.exit(0)


def shown_since(transcript: Optional[str], since: float) -> str:
    """The text that the candidate saw in the main session since `since`: its replies, and the output of a skill that
    they ran themselves (a forked skill: the master's text goes to them directly, as a local command output)."""
    try:
        lines = Path(str(transcript)).read_text(encoding="utf-8").splitlines() if transcript else []
    except OSError:
        return ""
    texts = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            stamp = _dt.datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except (json.JSONDecodeError, ValueError, AttributeError):
            continue
        if stamp < since:
            break
        if entry.get("isSidechain"):
            continue
        if entry.get("type") == "system" and entry.get("subtype") == "local_command":
            texts.append(str(entry.get("content") or ""))
        elif entry.get("type") == "assistant":
            content = (entry.get("message") or {}).get("content") or []
            texts += [b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(texts)


def report(inp: dict) -> None:
    """Stop of the main session: the token table of the last run must reach the candidate, one time."""
    if not L.REPORT_FILE.exists() or inp.get("agent_type"):
        sys.exit(0)  # a forked skill's own Stop: its text may never reach the candidate, so only the main session counts
    table = L.REPORT_FILE.read_text(encoding="utf-8")
    seen = (inp.get("last_assistant_message") or "") + "\n" + shown_since(inp.get("transcript_path"), L.REPORT_FILE.stat().st_mtime)
    L.REPORT_FILE.unlink(missing_ok=True)
    if not L.report_shown(table, seen):
        # shown to the candidate by Claude Code itself: no block, so the model does not run again and repeat its reply
        print(json.dumps({"systemMessage": table.strip()}))
    sys.exit(0)


def find_workspace(inp: dict) -> Optional[Path]:
    """The workspace: the nearest folder with ohmyjob.json at or above the PARSED top-level cwd, else above
    $CLAUDE_PROJECT_DIR, else the workspace hook.sh exported (a candidate only, never trusted over the parsed cwd)."""
    cwd = inp.get("cwd")
    for start in (cwd if isinstance(cwd, str) else None, os.environ.get("CLAUDE_PROJECT_DIR")):
        if start and os.path.isabs(start):
            here = Path(start).resolve()
            for folder in (here, *here.parents):
                if (folder / "ohmyjob.json").is_file():
                    return folder
    exported = os.environ.get("OHMYJOB_WORKSPACE")
    if exported and (Path(exported) / "ohmyjob.json").is_file():
        return Path(exported).resolve()
    return None


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    handlers = {"guard": guard, "snapshot": snapshot, "handback": handback, "capture": capture, "report": report}
    if command not in handlers:
        sys.stderr.write(f"unknown hook command: {command}\n")
        sys.exit(3)
    raw = sys.stdin.read()
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        inp = None
    if not isinstance(inp, dict):
        if command == "guard":
            sys.stderr.write("ohmyjob: the hook input is not a JSON object, the call is blocked\n")
            sys.exit(2)
        sys.exit(0)
    root = find_workspace(inp)
    if root is None:
        sys.exit(0)
    os.environ["OHMYJOB_WORKSPACE"] = str(root)
    load_modules()
    agent_type = inp.get("agent_type")
    if isinstance(agent_type, str) and agent_type in W.PREP_AGENTS:
        # a prep agent's call (always so for its Chrome calls): the prep rules, whatever script hook.sh chose
        if command in ("guard", "handback", "capture"):
            import pj_hooks

            sys.stdin = io.StringIO(raw)
            pj_hooks.main()
        sys.exit(0)
    if command == "guard" and not isinstance(inp.get("tool_name"), str):
        sys.stderr.write("ohmyjob: the hook input has no readable tool_name, the call is blocked\n")
        sys.exit(2)
    handlers[command](inp)
    sys.exit(0)


if __name__ == "__main__":
    main()
