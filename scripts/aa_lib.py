"""Shared code for the application-assistant scripts and hooks.

Layout (the paths come from workspace.py, in the workspace folder):
  .ohmyjob/run/apply/CURRENT                            id of the active run
  .ohmyjob/run/apply/<run_id>/state.json                queue, jobs, tab registry
  .ohmyjob/run/apply/<run_id>/jobs/<folder>/brief.md    brief for the worker
  .ohmyjob/run/apply/<run_id>/jobs/<folder>/result.json captured worker result
  .ohmyjob/run/apply/<run_id>/snap/...                  last tool outputs per tab
  .ohmyjob/run/apply/<run_id>/guard.log                 every guard decision that denied or bound
  <plugin>/ats/*.md                                     ATS notes that a worker may read

A run folder is deleted at the end of a run ('end') and before a new run ('start'); only folders that are ours
(a run id made by 'start') are removed. Set OHMYJOB_WORKSPACE to run the scripts against another workspace (tests).
"""
from __future__ import annotations

import collections
import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

import lexicon as X
import workspace as W
from job_key import LINKEDIN_ID_RE, is_linkedin_url, job_key  # noqa: F401  (job_key is re-exported for pj.py)

ROOT = W.ROOT
RUN_BASE = W.RUN_APPLY
ATS_DIR = W.ATS_DIR
APPLICATIONS = W.APPLICATIONS
PENDING_REVIEW = W.PENDING_REVIEW
NEEDS_ATTENTION = W.NEEDS_ATTENTION
PROFILE = W.PROFILE
TRACKER = W.TRACKER

FOLDER_ID_RE = re.compile(r"^(\d+)_")

WORKER = W.APPLICATION_WORKER  # agent_type of the hook input: ohmyjob:application-worker
MASTER = W.APPLICATION_MASTER

MAX_CAPTURE_BLOCKS = 2  # times the result hooks send a worker back to fix its report

# Worker queue ('next' in aa.py and pj.py): workers running at the same time. L.SLOTS is W.slots():
# below Claude Code's subagent cap by one (the master is a subagent too). It is read when it is used (module
# __getattr__), never at import, so importing this module needs no valid ohmyjob.json.
def __getattr__(name: str) -> Any:
    if name == "SLOTS":
        return W.slots()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


LOST_SECONDS = 60       # a printed worker whose Agent call did not come by then is printed again
NEXT_SECONDS = 90       # 'next' returns by then even with no free slot: under the 120 s default Bash timeout
WORKER_SECONDS = 17 * 60  # a worker's time limit, from the master's Agent call: then the guard denies every tool call
STALE_SECONDS = 20 * 60   # 'next' gives up on a worker's report: its job is recorded as missing (time limit + 3 min to report)


# --------------------------------------------------------------------------- time


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


# --------------------------------------------------------------------------- files


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # errors="replace": a lone surrogate in scraped page text must not make the write (and every later one) fail
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", errors="replace")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", errors="replace")  # surrogate-safe, see write_json
    os.replace(tmp, path)


# --------------------------------------------------------------------------- run state


def current_run_dir() -> Optional[Path]:
    marker = RUN_BASE / "CURRENT"
    if not marker.exists():
        return None
    run_id = marker.read_text(encoding="utf-8").strip()
    if not run_id:
        return None
    run_dir = RUN_BASE / run_id
    return run_dir if (run_dir / "state.json").exists() else None


@contextlib.contextmanager
def locked(run_dir: Path) -> Iterator[None]:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_state(run_dir: Path) -> dict:
    return read_json(run_dir / "state.json", {})


def save_state(run_dir: Path, state: dict) -> None:
    write_json(run_dir / "state.json", state)


def log(run_dir: Optional[Path], event: str, **fields: Any) -> None:
    if run_dir is None:
        return
    entry = {"t": now_iso(), "event": event, **fields}
    with open(run_dir / "guard.log", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def job_dir(run_dir: Path, folder: str) -> Path:
    return run_dir / "jobs" / folder


def snap_dir(run_dir: Path) -> Path:
    return run_dir / "snap"


def tab_key(tab: Any) -> str:
    return str(int(tab))


def job_tab_owner(state: dict, tab: Any) -> Optional[str]:
    """Folder that owns this tab, or None."""
    try:
        entry = state.get("tabs", {}).get(tab_key(tab))
    except (TypeError, ValueError):
        return None
    if entry and entry.get("role") == "job":
        return entry.get("folder")
    return None


# --------------------------------------------------------------------------- refs of every Chrome caller
# A click by a caller that is not an ohmyjob agent is checked against the refs of the page it read. Those reads are
# kept per tab here, outside any run (there may be no run), apart from the worker snapshots of a run.

CHROME_STORE = W.STATE_DIR / "run" / "chrome"


def foreign_refs_path(tab: Any) -> Path:
    return CHROME_STORE / f"tab_{tab_key(tab)}.refs.json"


def save_foreign_refs(tab: Any, refs: dict, partial: bool) -> None:
    """Keep the refs of a read by any caller. A full read replaces the tab's refs; a part read (ref_id, find) adds."""
    path = foreign_refs_path(tab)
    if partial:
        refs = {**read_json(path, {}), **refs}
    write_json(path, refs)


def foreign_state_path(tab: Any) -> Path:
    return CHROME_STORE / f"tab_{tab_key(tab)}.state.json"


def foreign_state(tab: Any) -> dict:
    """What the guard keeps for a tab between the calls of callers that are not ohmyjob agents: "used" (a pressable click
    was made since the last full read of the tab) and "focus" (a text field was clicked and nothing moved the focus)."""
    try:
        state = read_json(foreign_state_path(tab), {})
    except (json.JSONDecodeError, OSError):
        return {}
    return state if isinstance(state, dict) else {}


def set_foreign_state(tab: Any, state: dict) -> None:
    write_json(foreign_state_path(tab), state)


def click_is_pressable(items: list) -> bool:
    """A click that can press a control (button, link, tab, menu item...) rather than only tick a box or focus a field."""
    return any(i.get("role") not in CHOICE_ROLES and i.get("role") not in TYPE_ROLES for i in items)


def clear_foreign_refs(tab: Any) -> None:
    """The page may have changed (a click, a key, a typed text, a navigation): the next click needs a new read."""
    foreign_refs_path(tab).unlink(missing_ok=True)


def known_refs(run_dir: Optional[Path], tab: Any) -> dict:
    """All refs of the tab's snapshots (caller-read and, in a run, the worker's) as one dict: for page-shape checks."""
    merged: dict = {}
    try:
        for path in ([foreign_refs_path(tab)] + ([refs_path(run_dir, tab)] if run_dir is not None else [])):
            refs = read_json(path, {})
            if isinstance(refs, dict):
                merged.update({k: v for k, v in refs.items() if isinstance(v, dict)})
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return merged


def known_items(run_dir: Optional[Path], tab: Any, ref: str) -> list[dict]:
    """Every entry for `ref` in the tab's snapshots: the caller-read refs and, in a run, the worker snapshot."""
    items = []
    try:
        sources = [read_json(foreign_refs_path(tab), {})]
        if run_dir is not None:
            sources.append(read_json(refs_path(run_dir, tab), {}))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    for refs in sources:
        if isinstance(refs, dict) and isinstance(refs.get(ref), dict):
            items.append(refs[ref])
    return items


# --------------------------------------------------------------------------- page reads


# The guard asks read_page for the whole page (FULL_READ); the snapshot hook keeps that full copy for the
# refs, and gives the worker a compact copy: long links shortened, long option lists cut, then a size cap.
# Before this, a plain cap cut LinkedIn's resume step (25 saved resumes with 800-character links) before
# its file input, and long company pages before their lower fields .
# 90,000 characters: the largest page seen had about 75,000. Pages full of links can count 2 characters
# per token, so .claude/settings.json raises Claude Code's limit for one MCP result (MAX_MCP_OUTPUT_TOKENS, 25,000
# by default) to 60,000: the hook gets the page itself, not an error.
FULL_READ = 90000
COMPACT_CAP = {"interactive": 25000, "all": 40000}
MAX_OPTIONS = 12
MAX_HREF = 100
HREF_RE = re.compile(r'href="([^"]*)"')
OPTION_LINE_RE = re.compile(r"^(\s*)option\b")
REF_IN_LINE_RE = re.compile(r"\[(ref_\d+)\]")


def _short_href(match: re.Match) -> str:
    url = match.group(1)
    if len(url) <= MAX_HREF or "/safety/go" in url:  # the "Apply on company website" link: the worker navigates to it
        return match.group(0)
    base, _sep, _query = url.partition("?")
    base = base.split("#")[0]
    if len(base) > MAX_HREF:
        base = base[:MAX_HREF] + "…"
    return f'href="{base}?…"' if _sep else f'href="{base}"'


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def compact_page(text: str, mode: str, cut_options: bool = True) -> str:
    """The worker's copy of a read_page result (see FULL_READ). Refs stay as they are.
    cut_options False: a read of one element (ref_id): full option lists and full links, only the size cap."""
    head, sep, tail = text.partition("\nTab Context:")
    lines = (HREF_RE.sub(_short_href, head) if cut_options else head).split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = OPTION_LINE_RE.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        level = len(match.group(1))
        block_end = i
        options = []
        while block_end < len(lines) and _indent(lines[block_end]) >= level and lines[block_end].strip():
            if _indent(lines[block_end]) == level:
                if not OPTION_LINE_RE.match(lines[block_end]):
                    break
                options.append(lines[block_end])
            block_end += 1
        if not cut_options or len(options) <= MAX_OPTIONS:
            out.extend(lines[i:block_end])
        else:
            kept = options[:MAX_OPTIONS] + [o for o in options[MAX_OPTIONS:] if "(selected)" in o]
            parent = next((REF_IN_LINE_RE.search(l).group(1) for l in reversed(out)
                           if _indent(l) < level and REF_IN_LINE_RE.search(l)), None)
            where = f"read_page with ref_id={parent}" if parent else "read_page with the ref_id of the field"
            out.extend(kept)
            out.append(f"{' ' * level}… {len(options) - len(kept)} more options: fill the field with form_input "
                       f"and the option text, or {where} to list them all")
        i = block_end
    body = "\n".join(out)
    cap = COMPACT_CAP.get(mode, COMPACT_CAP["interactive"])
    if len(body) > cap:
        cut = body.rfind("\n", 0, cap)
        cut = cut if cut > 0 else cap
        below = [l for l in body[cut:].split("\n") if l.strip() and REF_IN_LINE_RE.search(l)]
        top = min((_indent(l) for l in below), default=0)
        outline = [l.strip() for l in below if _indent(l) == top]
        more = f"\n... and {len(outline) - 60} more" if len(outline) > 60 else ""
        body = (body[:cut] + f"\n[AA: the page is cut here, at {cut} of {len(body)} characters. Below the cut are the "
                "elements listed here: use their refs directly, or read one of them with read_page and its ref_id"
                + (":\n" + "\n".join(outline[:60]) + more if outline else ".") + "]")
    return body + sep + tail


def replace_tool_text(tool_response: Any, text: str) -> Any:
    """The same tool response with its text replaced (images and other blocks kept), for the PostToolUse updatedToolOutput."""
    if isinstance(tool_response, str):
        return text
    if isinstance(tool_response, list):
        out, done = [], False
        for item in tool_response:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                if not done:
                    out.append({**item, "text": text})
                    done = True
                continue  # tool_text joined every text block: the new text holds them all
            out.append(item)
        return out if done else [{"type": "text", "text": text}, *out]
    if isinstance(tool_response, dict):
        for key in ("content", "result", "output"):
            if key in tool_response:
                return {**tool_response, key: replace_tool_text(tool_response[key], text)}
        if isinstance(tool_response.get("text"), str):
            return {**tool_response, "text": text}
    return [{"type": "text", "text": text}]


def linkedin_target(url: str) -> str:
    """The company address inside a LinkedIn "Apply on company website" link (linkedin.com/safety/go/?url=...).
    That redirect page itself often failed to load in worker runs; the company pages loaded. Any other address is returned as it is."""
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    if (parts.hostname or "").endswith("linkedin.com") and parts.path.startswith("/safety/go"):
        target = (parse_qs(parts.query).get("url") or [""])[0].strip()
        if re.match(r"^https?://", target, re.I):
            return target
    return url


def greenhouse_form_url(url: str) -> Optional[str]:
    """A careers page that embeds a Greenhouse form (?gh_jid=N) shows it in a frame that read_page cannot see
    into. The form itself, with the job description, opens at boards.greenhouse.io/embed/job_app?token=N."""
    from urllib.parse import urlsplit

    target = linkedin_target(url)
    match = re.search(r"[?&]gh_jid=(\d+)", target)
    host = urlsplit(target).hostname or ""
    if not match or host.endswith("greenhouse.io"):
        return None
    return f"https://boards.greenhouse.io/embed/job_app?token={match.group(1)}"


# --------------------------------------------------------------------------- token usage


USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def usage_table(transcript: Optional[str], master_id: Optional[str], extra: Optional[dict] = None) -> str:
    """Tokens of one run: the master and every agent it started, read from the transcripts Claude Code writes
    (<session>/subagents/agent-<id>.jsonl, with the agent type and parent in agent-<id>.meta.json).
    extra: {(label, model): Counter} for calls outside the session (the Haiku judge's 'claude -p')."""
    rows: dict = {}
    agents: collections.Counter = collections.Counter()
    folder = None
    if transcript and master_id:
        path = Path(transcript)
        folder = path.parent if path.parent.name == "subagents" else path.with_suffix("") / "subagents"
        for f in sorted(folder.glob("agent-*.jsonl")):
            meta = read_json(f.with_name(f.stem + ".meta.json"), {})
            if f.stem != f"agent-{master_id}" and meta.get("parentAgentId") != master_id:
                continue
            kind = meta.get("agentType") or "?"
            agents[kind] += 1
            calls: dict = {}  # one API response is written as several lines; keep the largest usage of each
            for line in f.open(encoding="utf-8"):
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = d.get("message")
                if d.get("type") != "assistant" or not isinstance(m, dict) or not m.get("usage"):
                    continue
                mid = m.get("id") or d.get("requestId")
                old = calls.get(mid, (None, {}))[1]
                calls[mid] = (m.get("model"), {k: max(old.get(k, 0), m["usage"].get(k) or 0) for k in USAGE_KEYS})
            for model, usage in calls.values():
                row = rows.setdefault((kind, model or "?"), collections.Counter())
                row["calls"] += 1
                row.update(usage)
    for key, counter in (extra or {}).items():
        rows.setdefault(key, collections.Counter()).update(counter)
    if not rows:
        where = f"no transcripts found in {folder}" if folder else "no transcript path was seen by the hook"
        return f"Token usage: unknown ({where})"

    width = max([18] + [len(kind) for kind, _model in rows])  # scoped names (ohmyjob:application-worker) are longer

    def line(name: str, model: str, n: Any, c: collections.Counter) -> str:
        return (f"{name:{width}} {model:18} {n:>6} {c['calls']:>6} {c['input_tokens']:>9,} {c['cache_creation_input_tokens']:>12,} "
                f"{c['cache_read_input_tokens']:>12,} {c['output_tokens']:>9,}")

    out = ["Token usage of this run (the master and the agents it started; the master's last turn and the main session "
           "not included):",
           f"{'agent':{width}} {'model':18} {'agents':>6} {'calls':>6} {'input':>9} {'cache_write':>12} {'cache_read':>12} {'output':>9}"]
    total: collections.Counter = collections.Counter()
    for (kind, model), c in sorted(rows.items()):
        out.append(line(kind, model, agents[kind] or "", c))
        total.update(c)
    out.append(line("TOTAL", "", sum(agents.values()), total))
    return "\n".join(out)


# The master's text passes through 2 models, and either can drop the table. 'end' saves it here; the Stop hook makes
# the main session show it (hooks.py report) and then deletes the file.
REPORT_FILE = W.REPORT_FILE


def save_report(table: str) -> None:
    write_text(REPORT_FILE, table)


def report_shown(table: str, message: str) -> bool:
    """True if the message has the table: the total output tokens (the last number of the TOTAL line), in any layout,
    or the whole line when there is no TOTAL. The models often drop columns (often the input column)."""
    last = table.splitlines()[-1]
    numbers = re.findall(r"\d[\d,]*", last) if last.startswith("TOTAL") else []
    return numbers[-1] in message if numbers else last in message


# --------------------------------------------------------------------------- folders


def folder_id(name: str) -> Optional[str]:
    match = FOLDER_ID_RE.match(name)
    return match.group(1) if match else None


def job_pdf(folder_path: Path) -> Optional[Path]:
    """The resume PDF to upload: any PDF next to job.md; with several, the newest by modification time (name breaks a tie)."""
    pdfs = [p for p in folder_path.iterdir() if p.suffix.lower() == ".pdf" and p.is_file()]
    return max(pdfs, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)


def tailored_yamls(folder_path: Path) -> list[Path]:
    return sorted(p for p in folder_path.glob("*.yaml") if p.is_file())


# --------------------------------------------------------------------------- markdown


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def md_sections(text: str) -> list[tuple[int, str, int, int]]:
    """List of (level, title, start_line, end_line_exclusive) for every heading."""
    lines = text.splitlines()
    heads = []
    for i, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            heads.append((len(match.group(1)), match.group(2).strip(), i))
    out = []
    for idx, (level, title, start) in enumerate(heads):
        end = len(lines)
        for level2, _title2, start2 in heads[idx + 1:]:
            if level2 <= level:
                end = start2
                break
        out.append((level, title, start, end))
    return out


def md_section_body(text: str, title_re: str, level: int) -> Optional[str]:
    """Body (without the heading line) of the first heading of `level` whose title matches."""
    lines = text.splitlines()
    pattern = re.compile(title_re, re.I)
    for lvl, title, start, end in md_sections(text):
        if lvl == level and pattern.fullmatch(title):
            return "\n".join(lines[start + 1:end]).strip("\n")
    return None


def strip_subsections(body: str, title_re: str) -> str:
    """Remove every subsection (any level) whose title matches title_re."""
    lines = body.splitlines()
    pattern = re.compile(title_re, re.I)
    drop = [False] * len(lines)
    for lvl, title, start, end in md_sections(body):
        if pattern.search(title):
            for i in range(start, end):
                drop[i] = True
    return "\n".join(line for i, line in enumerate(lines) if not drop[i]).strip("\n")


def until_rule(body: str) -> str:
    """Text up to the first horizontal rule line ('---')."""
    out = []
    for line in body.splitlines():
        if line.strip() == "---":
            break
        out.append(line)
    return "\n".join(out).strip("\n")


# "- Key: value" and "- **Key:** value" (the closing ** of a bold key comes after the colon)
OVERVIEW_LINE_RE = re.compile(r"^\s*-\s*\**([^:*]+?)\**\s*:\s*(?:\*\*\s*)?(.*?)\s*$")


def job_overview(job_md: str) -> dict[str, str]:
    body = md_section_body(job_md, r"(1\.\s*)?Overview", 2)
    if body is None:
        return {}
    fields = {}
    for line in body.splitlines():
        match = OVERVIEW_LINE_RE.match(line)
        if match:
            fields[match.group(1).strip()] = match.group(2).strip()
    return fields


def job_description(job_md: str) -> Optional[str]:
    return md_section_body(job_md, r"(2\.\s*)?Job Description", 2)


# --------------------------------------------------------------------------- page rules
# The word lists (button names, consent, cookies, sign-up, delete, "application sent") are in
# lexicon.py, in many languages.
# The rules here need no language.

OAUTH_URL_RE = re.compile(
    r"(oauth|/authori[sz]e|accounts\.google\.com|login\.microsoftonline\.com|login\.live\.com|appleid\.apple\.com"
    r"|github\.com/login)",
    re.I,
)
TICK_ROLES = {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"}
# Keys a worker may press (a computer "key" action): an ALLOWLIST. Enter, Return, Space and their many spellings
# (NumpadEnter, KP_Return, ISO_Enter, a raw "\r") activate the focused button, so none of them is on it.
KEY_NAMES = {"tab", "shift+tab", "escape", "esc", "backspace", "delete", "home", "end", "pageup", "pagedown",
             "arrowup", "arrowdown", "arrowleft", "arrowright", "up", "down", "left", "right"}
EDIT_CHORD_RE = re.compile(r"(ctrl|control|cmd|meta|super)\+[acvxzy]")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def keys_allowed(text: Any) -> bool:
    """True if every key of a space-separated key action is on the allowlist (no control characters)."""
    if not isinstance(text, str) or CONTROL_CHAR_RE.search(text) or not text.strip():
        return False
    return all(k in KEY_NAMES or EDIT_CHORD_RE.fullmatch(k) for k in text.lower().split())


def activation_key(text: Any) -> Optional[str]:
    """The first key of a key action that can press a focused control (Enter, Return, Space in any spelling, Ctrl+M or
    Ctrl+J), or a control character. For callers that are not ohmyjob agents: they may press other keys."""
    text = text if isinstance(text, str) else ""
    if text and not text.strip():
        return "space"  # a text of blanks is the Space key
    match = CONTROL_CHAR_RE.search(text)
    if match:
        return repr(match.group(0))
    for key in text.lower().split():
        plain = re.sub(r"[^a-z0-9]", "", key)
        if "enter" in plain or "return" in plain or "space" in plain or re.fullmatch(r"(ctrl|control)[+]?[mj]", plain) \
                or re.fullmatch(r"(ctrl|control)\+[mj]", key):
            return key
    return None


def url_problem(url: str) -> Optional[str]:
    """Why a navigate address is refused: a control character (URL parsers drop them, so "java\tscript:" would run), or
    a scheme that is not http or https. None when it is fine. An address without a scheme is https."""
    if CONTROL_CHAR_RE.search(url):
        return "Navigate only to plain http or https addresses (no control characters)."
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) and not re.match(r"^https?://", url, re.I):
        return "Navigate only to http or https addresses."
    return None


TAB_KEY_RE = re.compile(r"(^|[\s+])tab($|[\s+])", re.I)
# Roles that take typed text. A worker may type only after it clicked one of them.
TYPE_ROLES = {"textbox", "combobox", "searchbox", "spinbutton", "textarea"}
BLANK_URL_RE = re.compile(r"^(about:blank|chrome://newtab/?|chrome://new-tab-page/?|)$", re.I)
LINKEDIN_SIGNED_OUT_RE = re.compile(r"linkedin\.com/(login|authwall|checkpoint|uas/|signup|reg/|start/join)", re.I)
# Roles (as read_page prints them) that a click can activate.
CLICKABLE_ROLES = {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option"}
# Roles that pick a value and never send a form: final and Apply words do not count on them
# ("I confirm I have read the above." is a checkbox, "Acknowledge/Confirm" an option: both were wrongly blocked once).
CHOICE_ROLES = TICK_ROLES | {"option"}


def is_search_field(item: dict) -> bool:
    """A search or filter box: a fill there does not mean that the tab shows an application form."""
    kind = ((item.get("attrs") or {}).get("type") or "").lower()
    return item.get("role") == "searchbox" or kind == "search" or bool(X.SEARCH.find(item.get("name") or ""))


def looks_like_form(refs: dict) -> bool:
    """A page with the shape of an application form: a file input, or at least 3 text fields that are not search boxes.

    In worker runs, no page with a pre-form Apply button had a file input or more than 2 such fields; the form
    pages had one or the other. It also covers a form that the site filled already."""
    fields = 0
    for item in refs.values():
        kind = ((item.get("attrs") or {}).get("type") or "").lower()
        if kind == "file":
            return True
        # read_page lists hidden inputs too (5 to 7 on a job page)
        if kind != "hidden" and item.get("role") in TYPE_ROLES and not is_search_field(item):
            fields += 1
    return fields >= 3
def navigable_href(href: Optional[str]) -> bool:
    """A link that goes to another page (a click on it does not run a form action)."""
    h = (href or "").strip().lower()
    return bool(h) and not h.startswith(("#", "javascript:")) and h not in ("/",)


# The English note of a click (computer action_summary). The worker writes what the click does.
NOTE_NEGATION_RE = re.compile(
    r"\b(not|never|without|before|no)\s+(\w+\s+){0,3}?(submit\w*|send\w*|finali[sz]\w*|apply\w*|applies)\b", re.I
)
NOTE_FINAL_RE = re.compile(
    r"\b(submit\w*|send|sends|sending|finali[sz]e\w*|complete[sd]?\s+(the|my|this|your)\s+application"
    r"|confirm\w*\s+(the|my|this|your)\s+(application|submission))\b",
    re.I,
)
NOTE_APPLY_RE = re.compile(r"\b(apply|applies|applying)\b", re.I)


def note_problem(note: Optional[str], filled: bool) -> Optional[str]:
    """The words of a click note that say the click sends the application, or None."""
    text = NOTE_NEGATION_RE.sub(" ", note or "")
    match = NOTE_FINAL_RE.search(text)
    if match:
        return match.group(0)
    if filled:
        match = NOTE_APPLY_RE.search(text)
        if match:
            return match.group(0)
    return None


# --------------------------------------------------------------------------- Google sign-in
# Rules without language: the access that the site asks for comes from the sign-in address
# (the OAuth "scope" list, also inside a nested continue=... address); Sign in with Google
# (/gsi/ pages) gives only the basic profile. The account is the entry with an email address.

GOOGLE_HOSTS = ("accounts.google.com",)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BASIC_SCOPES = {
    "openid", "email", "profile", "userinfo.email", "userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/plus.me",
}
GOOGLE_CHALLENGE_RE = re.compile(r"/(challenge|pwd|password|speedbump|signin/rejected|deniedsigninrejected)", re.I)
GOOGLE_CHOOSER_RE = re.compile(r"(accountchooser|chooseaccount|oauthchooseaccount|selectaccount|/gsi/select)", re.I)
# Title tags of the helper extension (chrome-extension/): "[AA on <version>] " on the tab that registered
# the run's tab group (an older extension writes "[AA on] "), "[AA from N] " on a tab that tab N opened
# (a sign-in pop-up as a tab).
# Chrome trims the trailing space of a tab title, so a page without a title of its own (robots.txt) shows the bare tag:
# a tag is followed by a space or ends the title.
HELPER_ON_RE = re.compile(r"^\[AA on(?: (?P<version>[0-9][\w.+-]*))?\](?: |$)")
CHILD_TITLE_RE = re.compile(r"^\[AA from (\d+)\](?: |$)")


def host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#:]+)", url.strip(), re.I)
    return match.group(1).lower() if match else None


def is_google_host(host: Optional[str]) -> bool:
    return bool(host) and any(host == h or host.endswith("." + h) for h in GOOGLE_HOSTS)


def oauth_scopes(url: Optional[str]) -> Optional[set[str]]:
    """The scopes that a sign-in address asks for, or None if it names none."""
    from urllib.parse import parse_qsl, urlsplit

    found: set[str] = set()
    seen = False

    def walk(address: str, depth: int) -> None:
        nonlocal seen
        try:
            parts = urlsplit(address)
        except ValueError:
            return
        for query in (parts.query, parts.fragment):
            for key, value in parse_qsl(query, keep_blank_values=True):
                if key.lower() in ("scope", "scopes"):
                    seen = True
                    found.update(v for v in re.split(r"[\s,+]+", value) if v)
                elif depth < 3 and value.lower().startswith(("http://", "https://", "/")):
                    walk(value if "://" in value else "https://accounts.google.com" + value, depth + 1)

    walk(url or "", 0)
    return found if seen else None


def is_gsi_url(url: Optional[str]) -> bool:
    match = re.match(r"^https?://[^/]+(/[^?#]*)", url or "", re.I)
    return bool(match) and match.group(1).lower().startswith("/gsi/")


def track_google(entry: dict, url: str) -> bool:
    """Record a URL of a tab in its entry: the access that a Google sign-in asks for. True if changed."""
    before = json.dumps({k: entry.get(k) for k in ("google_active", "google_scopes", "google_gsi")}, sort_keys=True)
    if is_google_host(host_of(url)):
        if not entry.get("google_active"):
            # a new Google visit: the counters start again
            entry.update({"google_active": True, "google_scopes": [], "google_gsi": False,
                          "google_clicks": 0, "google_continue": 0})
        scopes = oauth_scopes(url)
        if scopes:
            entry["google_scopes"] = sorted(set(entry.get("google_scopes") or []) | scopes)
        if is_gsi_url(url):
            entry["google_gsi"] = True
    elif entry.get("google_active") and host_of(url):
        entry["google_active"] = False
    after = json.dumps({k: entry.get(k) for k in ("google_active", "google_scopes", "google_gsi")}, sort_keys=True)
    return before != after


def google_access(entry: Optional[dict]) -> Optional[str]:
    """'basic', 'more', or None when the guard cannot see what the site asks for."""
    entry = entry or {}
    scopes = set(entry.get("google_scopes") or [])
    if scopes:
        return "basic" if scopes <= BASIC_SCOPES else "more"
    if entry.get("google_gsi"):
        return "basic"
    return None


# --------------------------------------------------------------------------- Haiku check


class NeedJudge(Exception):
    """The decision needs a Haiku check (judge.py) that is not in the cache yet."""

    def __init__(self, kind: str, payload: dict):
        super().__init__(kind)
        self.kind = kind
        self.payload = payload


# --------------------------------------------------------------------------- snapshots
# These parsers follow the real output of the Claude in Chrome tools, as seen in a live
# read-only test (constructed samples in tests/apply/samples/):
#
#   read_page line:   <indent><role> ["<name>"] [ref_N] attr="value" ...
#                     button "Submit form" [ref_435] type="submit"
#                     link "Go to company site" [ref_26] href="https://..."
#                     textbox [ref_1]                (no label: no name)
#   read_page never shows "required", and does not show values or checked states.
#   After a fill, an input without a label shows its value as its name.
#   Tab Context block (end of most results):
#     - Executed on tabId: 1690899341
#     • tabId 1690899341: "<title>" ("<url>")
#   tabs_context_mcp: one JSON object first: {"availableTabs":[{"tabId":..,"title":..,"url":..}],"tabGroupId":..}

ELEMENT_RE = re.compile(
    r'^(?P<indent>\s*)(?P<role>[A-Za-z][\w-]*)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?\s+\[(?P<ref>ref_\d+)\](?P<attrs>.*)$'
)
ATTR_RE = re.compile(r'([A-Za-z][\w-]*)="((?:[^"\\]|\\.)*)"')
EXECUTED_RE = re.compile(r"Executed on tabId:\s*(\d+)")
TAB_LINE_RE = re.compile(r'^\s*•\s*tabId\s+(\d+):\s*(.*)\s\("([^"]*)"\)\s*$')


def _unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


def parse_refs(text: str) -> dict[str, dict]:
    """Map ref -> {role, name, attrs, line} for every element line of a read_page result."""
    refs: dict[str, dict] = {}
    for raw in text.splitlines():
        match = ELEMENT_RE.match(raw.rstrip())
        if not match:
            continue
        attrs = {k: _unescape(v) for k, v in ATTR_RE.findall(match.group("attrs") or "")}
        refs[match.group("ref")] = {
            "role": match.group("role").lower(),
            "name": _unescape(match.group("name") or ""),
            "attrs": attrs,
            "line": raw.strip()[:400],
        }
    return refs


def context_names(text: str, ref: str, span: int = 2) -> str:
    """Names on the line of `ref`, on its parent line, and on `span` lines above and below it.

    read_page puts a label next to its box (Bootstrap) or as the parent of its box (httpbin),
    so these lines carry the label text of a checkbox or a radio button."""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if f"[{ref}]" in line), None)
    if index is None:
        return ""
    picked = set(range(max(0, index - span), min(len(lines), index + span + 1)))
    indent = len(lines[index]) - len(lines[index].lstrip())
    for j in range(index - 1, -1, -1):
        line = lines[j]
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            picked.add(j)
            break
    names = []
    for j in sorted(picked):
        match = ELEMENT_RE.match(lines[j].rstrip())
        if match and match.group("name"):
            names.append(_unescape(match.group("name")))
        else:
            quoted = re.match(r'^\s*[A-Za-z][\w-]*\s+"((?:[^"\\]|\\.)*)"', lines[j])
            if quoted:
                names.append(_unescape(quoted.group(1)))
    return " | ".join(names)


def line_name(line: str) -> str:
    """The quoted name of a read_page line, or ''."""
    match = ELEMENT_RE.match(line.rstrip())
    if match:
        return _unescape(match.group("name") or "")
    quoted = re.match(r'^\s*[A-Za-z][\w-]*\s+"((?:[^"\\]|\\.)*)"', line)
    return _unescape(quoted.group(1)) if quoted else ""


def subtree_names(text: str, ref: str) -> list[str]:
    """The name of `ref` and the names of the lines inside it (the lines right below it with a deeper indent).

    Google's account list puts the name and the email address inside an unnamed link:
        link [ref_1]
         generic "Alex Example" [ref_17]
         generic "alex@example.com" [ref_18]
    Only read_page with filter "all" shows the lines inside; the interactive view shows "link [ref_1]" alone."""
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if f"[{ref}]" in line), None)
    if index is None:
        return []
    indent = len(lines[index]) - len(lines[index].lstrip())
    names = [line_name(lines[index])]
    for line in lines[index + 1:]:
        if not line.strip() or len(line) - len(line.lstrip()) <= indent:
            break
        names.append(line_name(line))
    return [n for n in names if n.strip()]


def tab_context(text: str) -> tuple[Optional[int], dict[int, dict]]:
    """(executed tab id, {tab id: {title, url}}) from the Tab Context block of a tool result."""
    executed = EXECUTED_RE.search(text)
    tabs: dict[int, dict] = {}
    for line in text.splitlines():
        match = TAB_LINE_RE.match(line)
        if match:
            title = match.group(2).strip()
            if len(title) >= 2 and title[0] == '"' and title[-1] == '"':
                title = title[1:-1]
            tabs[int(match.group(1))] = {"title": title, "url": match.group(3)}
    return (int(executed.group(1)) if executed else None), tabs


def page_url(text: str, tab: Any) -> Optional[str]:
    """URL of `tab` as the Tab Context block of a tool result reports it."""
    _executed, tabs = tab_context(text)
    try:
        entry = tabs.get(int(tab))
    except (TypeError, ValueError):
        return None
    return entry["url"] if entry else None


def parse_tabs_context(text: str) -> list[dict]:
    """List of {id, url, title} from a tabs_context_mcp result."""
    stripped = (text or "").lstrip()
    if stripped.startswith("{"):
        try:
            data, _end = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("availableTabs"), list):
            return [
                {"id": int(t["tabId"]), "title": t.get("title") or "", "url": t.get("url") or ""}
                for t in data["availableTabs"]
                if isinstance(t, dict) and "tabId" in t
            ]
    _executed, tabs = tab_context(text or "")
    return [{"id": tid, "title": v["title"], "url": v["url"]} for tid, v in tabs.items()]


def tool_text(tool_response: Any) -> str:
    """Flatten a tool response (string, MCP content list, or dict) into text."""
    if tool_response is None:
        return ""
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, list):
        parts = []
        for item in tool_response:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(tool_response, dict):
        for key in ("content", "result", "output"):
            if key in tool_response:
                return tool_text(tool_response[key])
        if isinstance(tool_response.get("text"), str):
            return tool_response["text"]
        return json.dumps(tool_response, ensure_ascii=False)
    return str(tool_response)


def snapshot_path(run_dir: Path, tab: Any) -> Path:
    return snap_dir(run_dir) / f"tab_{tab_key(tab)}.txt"


def refs_path(run_dir: Path, tab: Any) -> Path:
    return snap_dir(run_dir) / f"tab_{tab_key(tab)}.refs.json"


def snapshot_text(run_dir: Path, tab: Any) -> str:
    path = snapshot_path(run_dir, tab)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def snapshot_refs(run_dir: Path, tab: Any) -> dict:
    return read_json(refs_path(run_dir, tab), {})


def final_controls(refs: dict, filled: bool) -> list[str]:
    """Names of visible final controls (Submit and similar in any listed language; Apply after a fill)."""
    names = []
    for item in refs.values():
        name = item.get("name") or ""
        if item.get("role") in CLICKABLE_ROLES - CHOICE_ROLES and (X.FINAL.find(name) or (filled and X.APPLY.find(name))):
            names.append(name)
    return names


def check_result(run_dir: Path, state: dict, job: dict, result: Optional[dict]) -> tuple[str, list[str]]:
    """Verdict for a worker result: PASS, BLOCKED, FAIL (fixable) or ALARM."""
    if result is None:
        return "FAIL", ["The worker returned no result."]
    if result.get("invalid"):
        return "FAIL", ["The worker's final message was not valid: " + "; ".join(result.get("errors", []))]
    if result["status"] == "blocked":
        if str(result.get("blocker") or "").startswith(CLOSED):
            return "CLOSED", [result["blocker"]]
        return "BLOCKED", [result.get("blocker") or "blocked"]
    tab_state = state.get("tabs", {}).get(tab_key(job["tab"]), {})
    if X.SUBMITTED.find(snapshot_text(run_dir, job["tab"])):
        return "ALARM", ["The last page snapshot shows a submission message. Check this job now."]
    # read_page shows no "required" marks and no values, so empty fields cannot be checked here.
    # What can be checked: the worker either reached the step with the final button, or it says why not.
    refs = snapshot_refs(run_dir, job["tab"])
    finals = final_controls(refs, True)
    if finals and not any(X.FINAL.find(n) for n in finals) and not tab_state.get("filled"):
        # an Apply button with no form seen opens the form; it is not the final step (such a tab was once parked
        # and went to Pending-Review unfilled). No retry: a push to go on could send a form
        # that the checks did not recognize.
        return "BLOCKED", [f"Stopped at '{finals[0]}' before the application form: this tab showed no form and "
                           "nothing was filled."]
    if not finals and not result["open"]:
        return "FAIL", [
            "You stopped before the final step and listed no open question. "
            "Go on to the step with the final button, or list in 'open' what stops you."
        ]
    return "PASS", []


# --------------------------------------------------------------------------- worker result

RESULT_SCHEMA: dict[str, tuple] = {
    "folder": (str,),
    "tab": (int,),
    "status": (str,),
    "where": (str,),
    "final_button": (str,),
    "url": (str,),
    "filled": (list,),
    "uploaded": (str, type(None)),
    "drafted": (list,),
    "open": (list,),
    "declarations": (list,),
    "blocker": (str, type(None)),
}
CLOSED = "CLOSED"  # a worker blocker: the job no longer accepts applications
QUESTIONS_BLOCKER = W.QUESTIONS_BLOCKER  # a worker that stops on questions it cannot answer from the brief


def extract_json_object(text: Optional[str]) -> Optional[dict]:
    """Return the last top-level JSON object found in text, or None."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    found = None
    i = 0
    while i < len(cleaned):
        if cleaned[i] == "{":
            try:
                obj, end = decoder.raw_decode(cleaned, i)
            except json.JSONDecodeError:
                i += 1
                continue
            if isinstance(obj, dict):
                found = obj
            i = end
        else:
            i += 1
    return found


def validate_result(obj: Optional[dict], folder: str, tab: Any) -> list[str]:
    if obj is None:
        return ["The final message holds no JSON object."]
    errors = []
    for key, types in RESULT_SCHEMA.items():
        if key not in obj:
            errors.append(f"Key '{key}' is missing.")
        elif not isinstance(obj[key], types) or (int in types and isinstance(obj[key], bool)):
            errors.append(f"Key '{key}' has the wrong type.")
    if errors:
        return errors
    if obj["folder"] != folder:
        errors.append(f"'folder' must be '{folder}'.")
    if str(obj["tab"]) != str(tab):
        errors.append(f"'tab' must be {tab}.")
    if obj["status"] not in ("parked", "blocked"):
        errors.append("'status' must be 'parked' or 'blocked'.")
    if obj["status"] == "blocked" and not (obj["blocker"] or "").strip():
        errors.append("A blocked result needs a 'blocker' reason.")
    for item in obj["drafted"]:
        if not (isinstance(item, dict) and isinstance(item.get("q"), str) and isinstance(item.get("a"), str)):
            errors.append("Each 'drafted' item must be {\"q\": \"...\", \"a\": \"...\"}.")
            break
    questions = obj.get("questions") or []
    blocker_says = re.sub(r"[^a-z ]", "", str(obj.get("blocker") or "").casefold()).strip() == QUESTIONS_BLOCKER.casefold()
    if not isinstance(questions, list) or not all(isinstance(x, str) and x.strip() for x in questions):
        errors.append("'questions' must be a list of question texts.")
    elif any("\n" in x.strip() or len(x.strip()) > 300 for x in questions):
        errors.append("Each question must be one line of at most 300 characters: the question itself, word for word.")
    elif questions:
        # a worker with questions for the candidate has stopped for them: the job waits in Needs Attention
        obj.update({"questions": questions, "status": "blocked", "blocker": QUESTIONS_BLOCKER})
    elif blocker_says:
        errors.append("List the questions for the candidate in 'questions'.")
    else:
        obj["questions"] = []
    for key in ("filled", "open", "declarations"):
        if not all(isinstance(x, str) for x in obj[key]):
            errors.append(f"Each '{key}' item must be a string.")
    return errors


# --------------------------------------------------------------------------- helper extension


def extension_version() -> Optional[str]:
    """The version of the extension that ships with this plugin (chrome-extension/manifest.json), or None."""
    data = read_json(W.PLUGIN_ROOT / "chrome-extension" / "manifest.json", {})
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def helper_version(title: Optional[str]) -> Optional[str]:
    """The version in a "[AA on <version>] " tab title; None for "[AA on] " (an older extension) or another title."""
    match = HELPER_ON_RE.match(title or "")
    return match.group("version") if match else None
