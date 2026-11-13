#!/usr/bin/env python3
"""Claude Code hooks for the application assistant (hooks/hooks.json -> scripts/hook.sh -> this file).

  hooks.py guard        PreToolUse   Chrome tools, Read, Edit, Write, Bash, Agent   enforces the rules
  hooks.py snapshot     PostToolUse  Chrome tools                saves page and tab data (application agents only)
  hooks.py handback     PreToolUse   SubagentHandback            checks and saves the worker result
  hooks.py capture      SubagentStop application-worker          the same, when the result is final text
  hooks.py report       Stop                                     shows the token table of the last run, once

Who is decided how (docs/ARCHITECTURE.md, "Hook routing and the guard model", rule 5): the model controls tool_input, so every decision rests on
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
# Job boards whose Apply button on a listing can send the application in one click (stored profile, stored resume):
# a name like "Apply" is judged there before a form was seen, not waved through. LinkedIn is not here: its Easy Apply
# opens a dialog (ats/linkedin-easy-apply.md), and an "Apply" that leaves for the employer is a link.
BOARD_LABELS = {"ziprecruiter", "naukri", "indeed", "glassdoor", "bayt", "bdjobs", "hellowork", "monster", "jooble",
                "reed", "totaljobs", "stepstone", "jobsdb", "seek"}
MAX_JUDGE_ROUNDS = 6  # Haiku checks for one tool call (a browser_batch can hold several new names)
ALLOW = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def load_modules() -> None:
    """Import the ohmyjob modules (they fix their paths at import, so OHMYJOB_WORKSPACE must be set first)."""
    global L, J, X, W
    import importlib

    import workspace

    importlib.reload(workspace)  # find_workspace() imported it while OHMYJOB_WORKSPACE named hook.sh's candidate
    import aa_lib
    import judge
    import lexicon

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

    def set_flag(self, tab: Any, name: str, value: Any) -> None:
        entry = self.entry(tab)
        if entry is not None and entry.get(name) != value:
            entry[name] = value
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
        short = {k: payload[k] for k in ("name", "role", "filled", "host")}  # a verdict is per host: boards differ
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


def check_signin_lease(ctx: Ctx, tab: Any) -> None:
    """One Google sign-in at a time per host: the first tab takes the lease, the next one waits and then reloads.

    `since` is stored as a timestamp (time.time()), so no string is parsed on every call."""
    host = ctx.host(tab)
    if not host:
        return
    hosts = ctx.state.setdefault("signin_hosts", {})
    entry = hosts.get(host)
    tab_key = L.tab_key(tab)
    stale = isinstance(entry, dict) and entry.get("status") == "pending" \
        and time.time() - float(entry.get("since") or 0) > L.LEASE_SECONDS
    if not isinstance(entry, dict) or stale:
        hosts[host] = {"holder": tab_key, "since": time.time(), "status": "pending", "hinted": []}
        ctx.dirty = True
        L.log(ctx.run_dir, "lease-take", tab=int(tab), host=host)
        return
    if entry["status"] == "pending" and entry["holder"] != tab_key:
        L.log(ctx.run_dir, "lease-wait", tab=int(tab), host=host, holder=int(entry["holder"]))
        raise Deny(f"Tab {entry['holder']} is signing in to {host}. Use computer wait for "
                   f"{L.SIGNIN_WAIT_SECONDS} seconds, then navigate your tab to the same address again "
                   "and call read_page. Do not click the Google option before that.")
    if entry["status"] == "signed_in" and tab_key not in (entry.get("hinted") or []):
        entry.setdefault("hinted", []).append(tab_key)
        ctx.dirty = True
        L.log(ctx.run_dir, "lease-reload-hint", tab=int(tab), host=host)
        raise Deny(f"{host} is signed in already in another tab. Navigate your tab to the same address "
                   "again and call read_page first.")
    L.log(ctx.run_dir, "lease-own" if entry["holder"] == tab_key else "lease-shared", tab=int(tab), host=host)


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
    if google and not filled:
        check_signin_lease(ctx, tab)  # one Google sign-in at a time per host (4.8)
        L.log(ctx.run_dir, "google-option", tab=int(tab), host=ctx.host(tab))
        return
    board = bool(BOARD_LABELS & set((ctx.host(tab) or "").split(".")))
    if not name.strip() or consent or (apply_word and not filled and not sticky and not board) or name in X.SAFE:
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
        L.log(ctx.run_dir, "google-account", tab=int(tab), host=ctx.host(tab))
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
    L.log(ctx.run_dir, "google-consent", tab=int(tab), host=ctx.host(tab))


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
        problem = L.url_problem(url)
        if problem:
            raise Deny(problem)
        if "…" in url:
            prefix = url.split("…")[0].rstrip("?")
            full = {h for h in L.HREF_RE.findall(L.snapshot_text(ctx.run_dir, tab)) if h.startswith(prefix)}
            if "://" not in prefix or len(full) != 1:
                raise Deny("This link is shortened in your view. Read the link with read_page and its ref_id to see its full address.")
            url = full.pop()
            if L.url_problem(url) or not re.match(r"^https?://", url, re.I):
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
            if not L.keys_allowed(keys):
                raise Deny("Press only Tab, Shift+Tab, Escape, Backspace, Delete, the arrow, Home, End and Page keys, or "
                           "Ctrl/Cmd with A, C, V, X, Z, Y. Enter, Return and Space can press a focused button: click the "
                           "control by its ref instead.")
            if L.TAB_KEY_RE.search(keys):
                ctx.set_focus(tab, None)
            return None
        if action == "type":
            if google or signin:
                raise Deny("Do not type on Google pages.")
            text = str(ti.get("text") or "")
            if L.CONTROL_CHAR_RE.search(text):
                raise Deny("No line breaks, tabs or other control characters in a type action (they go out as key presses). "
                           "Use form_input for text with line breaks.")
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
            if pressable and (ctx.entry(tab) or {}).get("choice_dirty"):
                # an answer can rename a button (Next becomes Submit); the check knows only the old name
                raise Deny("You clicked a checkbox, radio button or option since the last read_page. Call read_page first, "
                           "for the whole page (without ref_id): an answer can rename a button (Next can become Submit).")
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
            elif role in L.CHOICE_ROLES:
                ctx.set_flag(tab, "choice_dirty", True)
            # only the checked fields go through: a coordinate or a modifier next to the ref would move the click
            clean = {k: ti[k] for k in ("tabId", "action", "ref", "action_summary") if k in ti}
            return clean if clean != ti else None
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


COORDINATE_KEYS = ("coordinate", "start_coordinate", "end_coordinate")
FOREIGN_FREE_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to"}  # they press and type nothing


def foreign_click_problem(run_dir: Optional[Path], ti: dict) -> Optional[str]:
    """Why a click by a caller that is not an ohmyjob agent is refused, or None. The rule holds in every run state: it
    rests on the pages that any caller read (L.known_items), not on a run."""
    ref, tab = ti.get("ref"), ti.get("tabId")
    if not ref or tab is None:
        return "Click only by a ref: read the page with read_page (or find), then click the element by its ref, never by coordinates."
    items = L.known_items(run_dir, tab, str(ref))
    if not items:
        return (f"Unknown ref {ref} for this tab. Read the page with read_page (or find) first, then click the element by "
                "its ref.")
    for item in items:
        name = item.get("name") or ""
        if item.get("role") not in L.CHOICE_ROLES and X.FINAL.find(name):
            return f"'{name}' is a final control. The candidate submits applications personally."
    return None


class ForeignCall:
    """The rules for a call of a caller that is not an ohmyjob agent, one browser_batch included (every action in order).

    One rule for all of them: a click needs a fresh read of its tab. A read allows at most one pressable click (button,
    link, ...), and that click, like any key, typed text or navigation, makes the tab's refs void until the next read.
    Nothing is saved unless the whole call is allowed (commit())."""

    def __init__(self, run_dir: Optional[Path]):
        self.run_dir = run_dir
        self.state: dict[Any, dict] = {}
        self.tabs: dict[Any, Any] = {}
        self.stale: set = set()  # tabs whose page an earlier action of this call may have changed

    def tab(self, ti: dict) -> Any:
        try:
            key = L.tab_key(ti.get("tabId"))
        except (TypeError, ValueError):
            raise Deny("Give your tabId in every browser call.") from None
        if key not in self.state:
            self.state[key] = dict(L.foreign_state(ti.get("tabId")))
            self.tabs[key] = ti.get("tabId")
        return key

    def moved(self, key: Any) -> None:
        self.stale.add(key)
        self.state[key]["clear"] = True

    def check(self, short: str, ti: dict, in_batch: bool = False) -> None:
        if short == "javascript_tool":
            raise Deny("JavaScript is not allowed in this workspace.")
        if short == "navigate":
            url = str(ti.get("url") or "").strip()
            if url not in ("back", "forward"):
                if re.sub(r"[\x00-\x20\x7f]", "", url).lower().startswith(("javascript:", "vbscript:")):
                    raise Deny("JavaScript is not allowed in this workspace.")
                problem = L.url_problem(url)  # http or https only (a data: page can run script)
                if problem:
                    raise Deny(problem)
            if ti.get("tabId") is not None:
                key = self.tab(ti)
                self.state[key]["focus"] = None
                self.state[key]["url"] = url if "://" in url else "https://" + url
                self.moved(key)
        elif short == "browser_batch":
            if in_batch:
                raise Deny("Do not put browser_batch inside browser_batch.")
            for action in ti.get("actions") or []:
                name = str(action.get("name") or "") if isinstance(action, dict) else ""
                name = name[len(PREFIX):] if name.startswith(PREFIX) else name
                if name != "javascript_tool" and name not in BATCH_TOOLS:
                    raise Deny(f"Do not put {name or 'this'} inside browser_batch. Call it alone.")
                inner = action.get("input")
                self.check(name, inner if isinstance(inner, dict) else {}, in_batch=True)
        elif short in ("form_input", "file_upload"):
            self.field_tool(short, ti)
        elif short == "computer":
            self.computer(ti)

    def field_tool(self, short: str, ti: dict) -> None:
        """form_input and file_upload by a caller that is not an ohmyjob agent: fields only, like a worker. They also take the
        focus away: a typed text after them would go to whatever they touched."""
        key = self.tab(ti)
        items = L.known_items(self.run_dir, ti.get("tabId"), str(ti.get("ref") or ""))
        if not ti.get("ref") or not items:
            raise Deny(f"Unknown ref {ti.get('ref')} for this tab. Read the page with read_page (or find) first, then use "
                       "the ref of the field.")
        for item in items:
            role, attrs = item.get("role") or "", item.get("attrs") or {}
            if short == "form_input" and role in L.CLICKABLE_ROLES and role not in L.TICK_ROLES:
                raise Deny("form_input is for fields. Click buttons and links with computer left_click and the ref.")
            if short == "file_upload" and (attrs.get("type") or "").lower() != "file":
                raise Deny("file_upload is for a file input (type=file) only.")
        self.state[key]["focus"] = None

    def page_problem(self, key: Any, ti: dict, items: list) -> Optional[str]:
        """The worker's rules for a pressable click that need the page: a nameless submit button, an Apply after an
        application form, and an Apply on a job board's listing (it can send the application in one click)."""
        refs = L.known_refs(self.run_dir, ti.get("tabId"))
        url = self.state[key].get("url")
        if not url and self.run_dir is not None:
            url = (L.load_state(self.run_dir).get("tab_urls") or {}).get(key)
        board = bool(BOARD_LABELS & set((L.host_of(url) or "").split(".")))
        for item in items:
            role, name, attrs = item.get("role") or "", item.get("name") or "", item.get("attrs") or {}
            if role in L.CHOICE_ROLES or role in L.TYPE_ROLES:
                continue
            if role == "button" and not name.strip() and attrs.get("type") == "submit":
                return "This submit button has no name, so it cannot be checked. The candidate submits applications personally."
            if X.APPLY.find(name):
                if L.looks_like_form(refs):
                    return f"'{name}' can send the application: this page shows an application form. The candidate submits it personally."
                if board and (role == "button" or (role == "link" and not L.navigable_href(attrs.get("href")))):
                    return (f"'{name}' on a job board can send the application in one click. The candidate applies "
                            "personally.")
        return None

    def computer(self, ti: dict) -> None:
        action = ti.get("action")
        if action in FOREIGN_FREE_ACTIONS:
            return
        if action not in ("left_click", "hover", "key", "type"):
            raise Deny(f"computer action '{action}' is not allowed here: click by ref (left_click), press an allowed key, "
                       "type into a field you clicked, scroll, wait or take a screenshot.")
        if any(k in ti for k in COORDINATE_KEYS):
            raise Deny("Never act by coordinates: read the page with read_page (or find), then use the ref.")
        key = self.tab(ti)
        st = self.state[key]
        if action == "hover":
            if not ti.get("ref"):
                raise Deny("Hover only by a ref from read_page.")
        elif action == "left_click":
            if key in self.stale:
                raise Deny("The page may have changed earlier in this call. Read the page again, then click by ref.")
            problem = foreign_click_problem(self.run_dir, ti)
            if problem:
                raise Deny(problem)
            items = L.known_items(self.run_dir, ti.get("tabId"), str(ti.get("ref")))
            if any(i.get("role") in L.TYPE_ROLES for i in items):
                st["focus"] = "text"  # a text field takes typed text afterwards; focusing it changes no ref
            else:
                # a button, a link, and also a box or an option: an answer can rename a button (Next becomes Submit)
                if st.get("used"):
                    raise Deny("One click per read of the page: read the page again (read_page), then click by ref.")
                if L.click_is_pressable(items):
                    problem = self.page_problem(key, ti, items)
                    if problem:
                        raise Deny(problem)
                st["used"] = True
                st["focus"] = None
                self.moved(key)
        elif action == "key":
            text = ti.get("text")
            if not L.keys_allowed(text):
                raise Deny("Press only Tab, Shift+Tab, Escape, Backspace, Delete, the arrow, Home, End and Page keys, or "
                           "Ctrl/Cmd with A, C, V, X, Z, Y. Enter, Return and Space can press a focused button, such as "
                           "Submit: click the control by its ref instead.")
            if L.TAB_KEY_RE.search(str(text)):
                st["focus"] = None
            self.moved(key)
        else:  # type
            if L.CONTROL_CHAR_RE.search(str(ti.get("text") or "")):
                raise Deny("No line breaks or other control characters in a type action: they go out as key presses.")
            if not st.get("focus"):
                raise Deny("Type only right after you click a text field by its ref (Tab moves the focus away). "
                           "Better: use form_input with the field's ref.")
            self.moved(key)

    def commit(self) -> None:
        for key, st in self.state.items():
            tab = self.tabs[key]
            if st.pop("clear", False):
                L.clear_foreign_refs(tab)
            L.set_foreign_state(tab, st)


def check_any_action(run_dir: Optional[Path], short: str, ti: dict) -> None:
    """The main-session rules, for every Chrome caller that is not an ohmyjob agent (the main session, Explore and any
    other subagent), in every run state: no JavaScript, no final click, no coordinates, only the computer actions of an
    allowlist, and one pressable click per read of a tab (see ForeignCall)."""
    call = ForeignCall(run_dir)
    call.check(short, ti)
    call.commit()


def read_allowed(run_dir: Optional[Path], file_path: Any, agent_id: Optional[str] = None) -> bool:
    """The worker may read the ATS notes (the plugin's and the workspace's) and ITS OWN brief, by an absolute path.

    The brief is claimed by the first worker that reads it (a worker has no tab bound yet: it reads the brief first);
    'next' clears the claim when it hands the job to a new worker. One brief per agent. Other briefs and every result.json are refused."""
    if run_dir is None or not isinstance(file_path, str) or not os.path.isabs(file_path):
        return False
    try:
        target = Path(file_path).resolve()
    except (OSError, RuntimeError):
        return False
    ats = W.ATS_DIR.resolve()
    user_ats = W.USER_ATS_DIR.resolve()
    if target.suffix == ".md" and target.parent in (ats, user_ats):
        return True
    if not agent_id or target.name != "brief.md" or target.parent.parent != (run_dir / "jobs").resolve():
        return False
    folder = target.parent.name
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        job = state.get("jobs", {}).get(folder)
        if job is None:
            return False
        for entry in state.get("tabs", {}).values():  # a job tab bound to another worker is not this worker's
            if entry.get("role") == "job" and entry.get("folder") == folder and entry.get("agent_id") not in (None, agent_id):
                return False
        if job.get("brief_agent") not in (None, agent_id):
            return False
        if job.get("brief_agent") is None:
            # one brief per agent, and only a job that 'next' has handed out
            if not job.get("started_at") or any(j.get("brief_agent") == agent_id for j in state.get("jobs", {}).values()):
                return False
            job["brief_agent"] = agent_id
            L.save_state(run_dir, state)
    return True


def claimed_brief(run_dir: Optional[Path], agent_id: Optional[str]) -> Optional[Path]:
    """The brief this worker has claimed: a refused Read names it, so a worker whose context was summarized can
    Read it again (4.12)."""
    if run_dir is None or not agent_id:
        return None
    state = L.load_state(run_dir)
    for folder, job in (state.get("jobs") or {}).items():
        if job.get("brief_agent") == agent_id:
            return L.job_dir(run_dir, folder) / "brief.md"
    return None


# --------------------------------------------------------------------------- progress watchdog (application workers)
# An application worker has no wall-clock limit (4.7): it is stopped for a lack of progress, a repeated denial, or the
# call budget. The counters live in the job entry's "watchdog" dict (L.init_watchdog()).

_STOP_REASONS = {
    "stall": "no progress in your last calls",
    "repeat_deny": "the same denial repeated too many times",
    "budget": "you reached the call budget",
    "idle": "you were idle too long and your slot was freed",
    "ended": "the worker ended without a report",
}


def stop_message(stopped_by: str) -> str:
    """The denial text for a worker the watchdog stopped: it says which code to report and to stop calling tools."""
    reason = _STOP_REASONS.get(stopped_by, stopped_by)
    return (f"Stop now: {reason}. Make no more tool calls (the hook denies them all). Send your final report now "
            'with status "blocked" and a blocker that starts with the code that fits the wall (for example '
            "NOT_ACCESSIBLE or LOAD_FAIL), or STALLED if none fits, and say what is left to do. "
            "The candidate finishes it by hand in your tab.")


def worker_job(state: dict, agent_id: Optional[str]) -> Optional[dict]:
    """The job dict of this worker: the job tab it is bound to (the tab registry key is the tab id)."""
    if not agent_id:
        return None
    for entry in state.get("tabs", {}).values():
        if entry.get("role") == "job" and entry.get("agent_id") == agent_id:
            folder = entry.get("folder")
            if folder:
                return state.get("jobs", {}).get(folder)
    return None


def worker_tab(state: dict, agent_id: Optional[str]) -> Optional[str]:
    """The tab key of this worker's job tab, or None. (The registry key IS the tab id: an entry has no "tab" key.)"""
    if not agent_id:
        return None
    for key, entry in state.get("tabs", {}).items():
        if entry.get("role") == "job" and entry.get("agent_id") == agent_id:
            return key
    return None


def _normalize_reason(text: str) -> str:
    return re.sub(r"\s+", " ", text[:80]).strip().lower()


def is_lease_denial(reason: str, state: dict, agent_id: Optional[str]) -> bool:
    """True when this denial is a sign-in lease wait for another tab: it is not the worker's own fault."""
    low = reason.lower()
    if "is signing in to" not in low and "signed in already" not in low:
        return False
    my_tab = worker_tab(state, agent_id)
    if my_tab is None:
        return False
    for lease in (state.get("signin_hosts") or {}).values():
        if lease.get("status") in ("pending", "signed_in"):
            holder = lease.get("holder")
            if holder and str(holder) != str(my_tab):
                return True
    return False


def record_progress(wd: dict) -> None:
    wd["stall"] = 0
    wd["nudged"] = False
    wd["pending_nudge"] = False
    wd["progress"] = wd.get("progress", 0) + 1
    wd["deny_reason"] = ""
    wd["deny_count"] = 0


def record_stall(wd: dict, limits: dict, job: dict) -> None:
    wd["stall"] = wd.get("stall", 0) + 1
    wd["max_stall"] = max(wd.get("max_stall", 0), wd["stall"])
    if wd["stall"] >= limits["stall_stop_calls"]:
        job["stopped_by"] = "stall"
    elif wd["stall"] >= limits["stall_nudge_calls"] and not wd.get("nudged"):
        wd["pending_nudge"] = True


def record_deny(wd: dict, reason: str, limits: dict, job: dict, state: dict, agent_id: Optional[str]) -> None:
    """A denial counts as a stall step and as a repeat of the same denial; a sign-in lease wait counts as neither."""
    if is_lease_denial(reason, state, agent_id):
        return
    record_stall(wd, limits, job)
    norm = _normalize_reason(reason)
    if norm == wd.get("deny_reason"):
        wd["deny_count"] = wd.get("deny_count", 0) + 1
    else:
        wd["deny_reason"] = norm
        wd["deny_count"] = 1
    if wd["deny_count"] >= limits["repeat_deny_stop"]:
        job["stopped_by"] = "repeat_deny"


NUDGE_TEMPLATE = "[AA: no progress in your last {n} calls. If you are stuck, stop now and send your report.]"

_ERROR_TEXT_RE = re.compile(r"^(error|failed|page still loading|showing error page)", re.I)


def tool_is_error(tool_response: Any) -> bool:
    """A tool result that reports a failure (it never counts as progress)."""
    text = L.tool_text(tool_response).strip()
    return bool(text) and bool(_ERROR_TEXT_RE.match(text)) and "Tab Context:" not in text


def check_progress(state: dict, short: str, ti: dict, text: str, page_saved: bool,
                   run_dir: Path, agent_id: Optional[str], tool_response: Any) -> bool:
    """Was this call progress for its job? A new page fingerprint, a first fill of a field, a first click on a choice
    control, an upload, or a Google sign-in step. A re-read of the same page and a second fill of the same field are not."""
    tab_id = ti.get("tabId")
    if tab_id is None:
        return False
    tk = L.tab_key(tab_id)
    job = worker_job(state, agent_id)
    if job is None:
        return False
    wd = job.setdefault("watchdog", L.init_watchdog())
    if tool_is_error(tool_response):
        return False
    url = (state.get("tab_urls") or {}).get(tk, "")

    def fingerprint() -> str:
        snap = L.snapshot_text(run_dir, tab_id)
        return L.page_fingerprint(snap, url) if snap else ""

    def first_fill(ref: Optional[str]) -> bool:
        item = L.snapshot_refs(run_dir, tab_id).get(ref or "", {})
        pair = [fingerprint(), item.get("name", ref or "")]
        if pair not in wd.get("fills", []):
            wd.setdefault("fills", []).append(pair)
            return True
        return False

    if short == "read_page" and page_saved and not ti.get("ref_id"):
        fp = fingerprint()
        if fp not in wd.get("fingerprints", []):
            wd.setdefault("fingerprints", []).append(fp)
            return True
        return False
    if short == "form_input":
        return first_fill(ti.get("ref"))
    if short == "computer":
        if (ti.get("action") or "").lower() != "left_click":
            return False
        ref = ti.get("ref")
        if ref:
            item = L.snapshot_refs(run_dir, tab_id).get(ref, {})
            if item.get("role") in L.CHOICE_ROLES or item.get("role") == "menuitem":
                pair = [fingerprint(), item.get("name", "")]
                if pair not in wd.get("choices", []):
                    wd.setdefault("choices", []).append(pair)
                    return True
        return L.is_google_host(L.host_of(url))
    if short == "file_upload":
        return True
    if short == "browser_batch":
        progressed = False
        for action in (ti.get("actions") or []):
            name = (action.get("name") or "").replace(PREFIX, "")
            args = action.get("input") or {}
            if name == "form_input" and first_fill(args.get("ref")):
                progressed = True
            elif name == "file_upload":
                progressed = True
        return progressed
    return False


def watchdog_count(state: dict, agent_id: Optional[str]) -> Optional[str]:
    """Count this call for its worker (mutating `state`): the stop message when the worker must stop, else None.

    The caller holds the run lock and saves the state; nothing here takes the lock itself (one lock per call).
    """
    job = worker_job(state, agent_id)
    if job is None:
        return None
    job["last_call_at"] = time.time()
    if job.get("stopped_by"):
        return stop_message(job["stopped_by"])
    wd = job.setdefault("watchdog", L.init_watchdog())
    wd["calls"] = wd.get("calls", 0) + 1
    if wd["calls"] >= L.worker_limits()["call_budget"]:
        job["stopped_by"] = "budget"
        return stop_message("budget")
    return None


def watchdog_deny(state: dict, reason: str, agent_id: Optional[str]) -> str:
    """Record a denial for its worker (mutating `state`) and return the text to print: the stop message once the
    worker is stopped. The caller holds the run lock and saves the state."""
    job = worker_job(state, agent_id)
    if job is None:
        return reason
    wd = job.setdefault("watchdog", L.init_watchdog())
    record_deny(wd, reason, L.worker_limits(), job, state, agent_id)
    return stop_message(job["stopped_by"]) if job.get("stopped_by") else reason


def watchdog_carry(fresh: dict, state: dict, agent_id: Optional[str]) -> None:
    """Copy this worker's watchdog bookkeeping from `state` into a freshly loaded `fresh`.

    A denied call must not save what it changed, but its counters must survive: they are the only part of the
    denied call's state that is kept."""
    src, dst = worker_job(state, agent_id), worker_job(fresh, agent_id)
    if src is None or dst is None:
        return
    for key in ("watchdog", "last_call_at", "stopped_by"):
        if key in src:
            dst[key] = src[key]


def watchdog_locked_count(run_dir: Path, agent_id: Optional[str]) -> Optional[str]:
    """watchdog_count for a call that has no locked block of its own (the worker's Read)."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        stopped = watchdog_count(state, agent_id)
        if worker_job(state, agent_id) is not None:
            L.save_state(run_dir, state)
        return stopped


def watchdog_locked_deny(run_dir: Path, agent_id: Optional[str], reason: str) -> str:
    """watchdog_deny for a denial that happens outside the guard's locked block (a failed Haiku check)."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        text = watchdog_deny(state, reason, agent_id)
        if worker_job(state, agent_id) is not None:
            L.save_state(run_dir, state)
        return text


# --------------------------------------------------------------------------- the dream agents (4.11)
# The master runs the `ohmyjob dream` commands and starts one worker per ATS key. A worker is bound to its group the
# way a brief is claimed: the first Read of a bundle path of a spawned, unclaimed group claims that group for this
# agent id. Nothing is taken from the text of the transcript, so no model can choose another group's note.


def dream_run_dir() -> Optional[Path]:
    import dream

    return dream.run_dir()


def dream_group(agent_id: Optional[str], *, bundle: Optional[Path] = None) -> Optional[tuple[Path, str, dict]]:
    """The (run dir, key, group) this worker has claimed, or the group that `bundle` names and this worker may claim."""
    import dream

    run_dir = dream.run_dir()
    if run_dir is None or not agent_id:
        return None
    with dream.locked():
        state = dream.run_state(run_dir)
        groups = state.get("groups") or {}
        for key, group in groups.items():
            if group.get("agent_id") == agent_id:
                return run_dir, key, group
        if bundle is None:
            return None
        for key, group in groups.items():
            if group.get("agent_id") or not group.get("spawned") or group.get("done"):
                continue
            try:
                same = Path(group["bundle"]).resolve() == bundle
            except (OSError, RuntimeError):
                continue
            if same:
                group["agent_id"] = agent_id
                L.write_json(run_dir / "state.json", state)
                return run_dir, key, group
    return None


def dream_read_allowed(file_path: Any, agent_id: Optional[str]) -> bool:
    """A dream worker reads: its bundle (which claims its group), any ATS note, and the worker's own rules."""
    try:
        target = Path(str(file_path)).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if target.suffix == ".md" and target.parent in (W.ATS_DIR.resolve(), W.USER_ATS_DIR.resolve()):
        return True
    if target == (W.PLUGIN_ROOT / "agents" / "application-worker.md").resolve():
        return True
    claimed = dream_group(agent_id)
    if claimed is not None:  # a claimed worker reads its own bundle and nothing else new
        try:
            return target == Path(claimed[2]["bundle"]).resolve()
        except (OSError, RuntimeError):
            return False
    return dream_group(agent_id, bundle=target) is not None


def dream_write_allowed(file_path: Any, agent_id: Optional[str]) -> bool:
    """A dream worker writes ONE file: the note of the group it claimed."""
    claimed = dream_group(agent_id)
    if claimed is None:
        return False
    try:
        return Path(str(file_path)).expanduser().resolve() == Path(claimed[2]["note"]).resolve()
    except (OSError, RuntimeError):
        return False


def dream_save_result(inp: dict, message: str) -> Optional[str]:
    """Save a dream worker's report under the group it claimed (never under the `ats` of the message)."""
    import dream

    report = L.extract_json_object(message)
    if report is None:
        return None
    claimed = dream_group(inp.get("agent_id"))
    if claimed is None:
        return None
    run_dir, key, _group = claimed
    with dream.locked():
        state = dream.run_state(run_dir)
        group = (state.get("groups") or {}).get(key)
        if group is None:
            return None
        L.write_json(Path(group["bundle"]).with_name(f"{key}.result.json"), {**report, "ats": key})
        group["done"] = True
        L.write_json(run_dir / "state.json", state)
    return key


def dream_give_up(agent_id: Optional[str], reason: str) -> None:
    """The worker ended without a usable report: its group is done with an error, so the dream can end (4.11)."""
    import dream

    claimed = dream_group(agent_id)
    if claimed is None:
        return
    run_dir, key, _group = claimed
    with dream.locked():
        state = dream.run_state(run_dir)
        group = (state.get("groups") or {}).get(key)
        if group is not None and not group.get("done"):
            group.update({"done": True, "error": reason})
            L.write_json(run_dir / "state.json", state)


def dream_guard(inp: dict, tool: str, ti: dict, agent_type: str, agent_id: Optional[str]) -> None:
    """The dream agents' own rules: everything this does not allow is denied."""
    if agent_type == W.DREAM_MASTER:
        if tool == "Bash":
            command = ti.get("command")
            if not isinstance(command, str) or not W.DREAM_MASTER_CMD_RE.fullmatch(command.strip()):
                finish_deny(None, "Run only the 'ohmyjob dream start|next|finish|end' commands of your steps, one "
                                  "command and nothing else.", tool=tool, agent_type=agent_type)
            finish_allow()
        if tool == "Agent":
            if str(ti.get("subagent_type") or "") != W.DREAM_WORKER:
                finish_deny(None, f"The dream master starts only {W.DREAM_WORKER} agents.",
                            tool=tool, agent_type=agent_type)
            finish_allow()
        finish_deny(None, "The dream master runs its 'ohmyjob dream' commands and starts its workers; it uses no "
                          "other tool.", tool=tool, agent_type=agent_type)
    if tool == "Read":
        if not dream_read_allowed(ti.get("file_path"), agent_id):
            finish_deny(None, "The dream host worker reads only its own bundle, the ATS notes and the plugin's "
                              "agents/application-worker.md, by absolute paths.", tool=tool, agent_type=agent_type)
        finish_allow()
    if tool in ("Edit", "Write"):
        if not dream_write_allowed(ti.get("file_path"), agent_id):
            finish_deny(None, "The dream host worker writes only the note of its own ATS key (Read your bundle "
                              "first).", tool=tool, agent_type=agent_type)
        finish_allow()
    finish_deny(None, "The dream host worker uses Read, Edit and Write only.", tool=tool, agent_type=agent_type)


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

    if agent_type in W.DREAM_AGENTS:
        dream_guard(inp, tool, ti, agent_type, agent_id)
    if agent_type not in W.APPLICATION_AGENTS:
        # the main session and any other agent: only Chrome calls are checked; every other call gets no decision
        if not tool.startswith(PREFIX):
            sys.exit(0)
        try:
            check_any_action(run_dir, tool[len(PREFIX):], ti)
        except Deny as exc:
            finish_deny(run_dir, str(exc), tool=tool, agent_type=agent_type)
        sys.exit(0)

    if agent_type == L.MASTER and run_dir is not None and agent_id:
        # where the run's transcripts are, for the token table of 'end'
        L.write_json(run_dir / "master.json", {"agent_id": agent_id, "transcript": inp.get("transcript_path")})

    # ---- ohmyjob application agents: validate, then allow explicitly (or deny)
    if tool == "Read":
        if agent_type == L.WORKER and run_dir is not None:
            # a Read counts toward the call budget too (4.7)
            stopped = watchdog_locked_count(run_dir, agent_id)
            if stopped:
                finish_deny(run_dir, stopped, tool=tool, agent_id=agent_id)
        if agent_type != L.WORKER or not read_allowed(run_dir, ti.get("file_path"), agent_id):
            reason = ("The worker may read only its own brief and the ATS notes (the plugin's ats/ folder and "
                      "the workspace's ats/ folder), by absolute paths; the master reads no files.")
            brief = claimed_brief(run_dir, agent_id) if agent_type == L.WORKER else None
            if brief is not None:
                reason += f" Your brief is {brief}"
            finish_deny(run_dir, reason, tool=tool, agent_type=agent_type)
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
    if agent_type == L.WORKER:
        # the progress watchdog counts this call and stops a worker that is out of budget or stopped already
        stopped = watchdog_locked_count(run_dir, agent_id)
        if stopped is not None:
            finish_deny(run_dir, stopped, tool=short, agent_id=agent_id, tab=ti.get("tabId"))
    updated = None
    reason = None
    wait = 0.0
    denied_recorded = False
    folder_for_error = None  # captured when NeedJudge is caught, for Haiku check failure save
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
                    # Store the folder for judge_error when Haiku check fails outside the lock
                    tab = ctx.my_tab()
                    folder_for_error = tab and state.get("tabs", {}).get(L.tab_key(tab), {}).get("folder")
                if ctx.dirty and reason is None and pending is None:
                    L.save_state(run_dir, state)
                    wait = ctx.wait
            if reason is not None and agent_type == L.WORKER:
                # a fresh load drops what the denied call changed and keeps its watchdog counters (the lock is held,
                # so nothing else wrote the file meanwhile)
                fresh = L.load_state(run_dir)
                watchdog_carry(fresh, state, agent_id)
                reason = watchdog_deny(fresh, reason, agent_id)
                denied_recorded = True
                if worker_job(fresh, agent_id) is not None:
                    L.save_state(run_dir, fresh)
        if pending is None:
            break
        # the Haiku check runs outside the run lock; the next round finds its result in the cache
        result, error = J.ask(run_dir, pending.kind, pending.payload)
        L.log(run_dir, "judge", kind=pending.kind, payload=pending.payload, result=result, error=error or None,
              agent_id=agent_id)
        if result is None:
            reason = judge_failed(pending.kind, pending.payload, error)
            # the master reports the failed check in its summary: the state must be saved, under the lock
            # Use the folder captured when NeedJudge was caught (more reliable than re-looking up via tab)
            if folder_for_error:
                with L.locked(run_dir):
                    fresh = L.load_state(run_dir)
                    if folder_for_error in fresh.get("jobs", {}):
                        fresh["jobs"][folder_for_error]["judge_error"] = f"{pending.payload.get('name')}: {error}"
                        L.save_state(run_dir, fresh)
            break
        # a name is cached for the whole run only when its fill state is known: in /continue-applying an allowed
        # "Apply" that opened one form must not allow a final "Apply" on a review step of another tab
        if pending.kind == "click" and result.get("context_dependent") is False and pending.payload.get("filled") != "unknown":
            short_payload = {k: pending.payload[k] for k in ("name", "role", "filled", "host")}
            J.store(run_dir, J.key_for("click", short_payload), {"result": result, "payload": short_payload})
    else:
        reason = "Too many new names in one call. Click one control at a time."
    if reason is not None:
        if agent_type == L.WORKER and not denied_recorded:
            reason = watchdog_locked_deny(run_dir, agent_id, reason)
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
    else:
        # a full read replaces the page: delete the old refs first, so that a failed write never leaves them to judge
        # the clicks on the new page
        L.refs_path(run_dir, tab).unlink(missing_ok=True)
    L.write_text(path, text)
    if partial:
        merged = L.snapshot_refs(run_dir, tab)
        merged.update(refs)
        refs = merged
    L.write_json(L.refs_path(run_dir, tab), refs)
    return True


def observe_tabs(state: dict, tabs: dict[int, dict], run_dir: Optional[Path] = None) -> bool:
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
        tag = L.GOOGLE_TAG_RE.search(title)
        if entry is not None and entry.get("role") == "job" and tag:
            counts = {"google_count": int(tag.group(1)), "google_named": int(tag.group(2))}
            if {k: entry.get(k) for k in counts} != counts:
                entry.update(counts)
                changed = True
    # the sign-in lease (4.8): a pending sign-in is finished when its pop-up tab is gone or the job tab is back on the
    # host. The next worker on that host then gets the reload hint instead of a wait.
    for host, lease in list((state.get("signin_hosts") or {}).items()):
        if not isinstance(lease, dict) or lease.get("status") != "pending":
            continue
        holder_key = str(lease.get("holder"))
        holder = registry.get(holder_key)
        if holder is None or holder.get("role") != "job":
            continue
        signin_tab = next((k for k, v in registry.items()
                           if v.get("role") == "signin" and str(v.get("opener")) == holder_key), None)
        clicks = int(holder.get("google_clicks") or 0)
        if signin_tab is not None:  # the account click can happen in the pop-up tab
            clicks = max(clicks, int((registry.get(signin_tab) or {}).get("google_clicks") or 0))
        if clicks < 1:
            continue
        gone = signin_tab is not None and int(signin_tab) not in tabs
        back = bool(urls.get(holder_key)) and L.host_of(urls[holder_key]) == host
        if gone or back:
            lease["status"] = "signed_in"
            changed = True
            L.log(run_dir, "signin-done", tab=int(holder_key), host=host)
    return changed


def record_foreign(short: str, ti: dict, text: str) -> None:
    """The result of a Chrome call by a caller that is not an ohmyjob agent: keep the refs it read (for the click check of
    check_any_action). A full read of a tab allows a click again. The output is never rewritten. (The guard voids a tab's
    refs when it lets a click, key, typed text or navigation through, see ForeignCall.)"""
    tab = ti.get("tabId")
    if tab is None:
        return
    try:
        L.tab_key(tab)
    except (TypeError, ValueError):
        return
    if short in ("read_page", "find"):
        refs = L.parse_refs(text)
        partial = short == "find" or bool(ti.get("ref_id"))
        if refs:
            L.save_foreign_refs(tab, refs, partial=partial)
        state = L.foreign_state(tab)
        url = L.page_url(text, tab)
        new = {**state, **({"url": url} if url else {})}
        if not partial:
            new["used"] = False
        if new != state:
            L.set_foreign_state(tab, new)
    elif short == "navigate":
        L.clear_foreign_refs(tab)


def snapshot(inp: dict) -> None:
    tool = str(inp.get("tool_name") or "")
    agent_type = inp.get("agent_type") if isinstance(inp.get("agent_type"), str) else None
    if not tool.startswith(PREFIX) or agent_type in W.PREP_AGENTS:
        sys.exit(0)
    short = tool[len(PREFIX):]
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    text = L.tool_text(inp.get("tool_response"))
    if agent_type in W.DREAM_AGENTS:
        dream_guard(inp, tool, ti, agent_type, agent_id)
    if agent_type not in W.APPLICATION_AGENTS:
        record_foreign(short, ti, text)  # every other caller: the refs of its reads; nothing in the run, no rewrite
        sys.exit(0)
    run_dir = L.current_run_dir()
    if run_dir is None:
        sys.exit(0)
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
        if observe_tabs(state, tabs, run_dir):
            changed = True
        entry = state.get("tabs", {}).get(L.tab_key(ti["tabId"])) if page_saved else None
        if entry is not None:
            if not entry.get("filled") and L.looks_like_form(L.snapshot_refs(run_dir, ti["tabId"])):
                # the tab shows an application form, also one that the site filled: Apply is final from now on
                entry["filled"] = True
                entry["filled_host"] = L.host_of(state["tab_urls"].get(L.tab_key(ti["tabId"])))
                changed = True
            if not ti.get("ref_id") and (entry.get("last_click") or entry.get("choice_dirty")):
                entry["last_click"] = None  # the worker saw the page after its click (a part read may miss that control)
                entry["choice_dirty"] = False
                changed = True
        # ---- progress watchdog: a new page, a first fill, a first choice, an upload or a Google step is progress;
        # anything else is a stall step, and a nudge is added to the tool result at the nudge limit
        if inp.get("agent_type") == L.WORKER:
            snap_agent = inp.get("agent_id")
            wjob = worker_job(state, snap_agent)
            if wjob is not None:
                wjob["last_call_at"] = time.time()
                wd = wjob.setdefault("watchdog", L.init_watchdog())
                limits = L.worker_limits()
                if check_progress(state, short, ti, text, page_saved, run_dir, snap_agent, inp.get("tool_response")):
                    record_progress(wd)
                else:
                    record_stall(wd, limits, wjob)
                if wd.get("pending_nudge") and text and not tool_is_error(inp.get("tool_response")):
                    nudge = NUDGE_TEMPLATE.format(n=wd["stall"])
                    wd["nudged"] = True
                    wd["pending_nudge"] = False
                    base = L.tool_text(replace) if replace is not None else text
                    replace = L.replace_tool_text(inp.get("tool_response"), base + "\n" + nudge)
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
    with L.locked(run_dir):  # the other workers write their results meanwhile
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
    if inp.get("agent_type") == W.DREAM_WORKER:
        ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
        key = dream_save_result(inp, str(ti.get("message") or ""))
        if key:
            finish_allow({**ti, "message": json.dumps({"ats": key, "result": "saved by the hook"})})
        sys.exit(0)
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


def mark_ended(run_dir: Path, agent_id: Optional[str]) -> None:
    """The worker really ended: `next` frees its slot at once instead of waiting for idle_minutes (4.7). Not set when
    the stop is blocked to get a corrected report: the worker goes on then (4.7 defect 3)."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        job = worker_job(state, agent_id)
        if job is not None:
            job["ended_at"] = time.time()
            L.save_state(run_dir, state)


def capture(inp: dict) -> None:
    """SubagentStop of a worker: the report is its final text, when Claude Code does not use hand-backs."""
    if inp.get("agent_type") == W.DREAM_WORKER:
        if dream_save_result(inp, str(inp.get("last_assistant_message") or "")) is None:
            dream_give_up(inp.get("agent_id"), "the worker ended without a usable JSON report")
        sys.exit(0)
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
        mark_ended(run_dir, agent_id)
        sys.exit(0)
    action, reason, _info = process_report(
        run_dir, agent_id, message, again="Reply again with only the corrected JSON object.", source="stop"
    )
    if action == "retry":
        L.log(run_dir, "capture-block", agent_id=agent_id, reason=reason[:300])
        block_stop(reason)  # the worker goes on: it has not ended, so no ended_at
    mark_ended(run_dir, agent_id)
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
    """The workspace: workspace.find_workspace() of the PARSED top-level cwd, else of $CLAUDE_PROJECT_DIR; none: not a
    workspace. (The workspace that hook.sh exports is not used: it can come from a "cwd" text inside tool_input.)"""
    import workspace  # light (standard library only); load_modules() reloads it for the workspace found here

    cwd = inp.get("cwd")
    for start in (cwd if isinstance(cwd, str) else None, os.environ.get("CLAUDE_PROJECT_DIR")):
        if start and os.path.isabs(start):
            root = workspace.find_workspace(start)
            if root is not None:
                return root
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
