"""The ledger and the traces of an application run (spec 4.10).

`ohmyjob apply end` calls `write_ledger_and_traces` before the run folder (copies of the candidate's data) is
deleted. It writes one line per job to `.ohmyjob/traces/ledger.jsonl` and, for the jobs worth learning from, one
trace file next to it. Dream (`/ohmyjob:dream`) reads those traces and writes the ATS notes of this workspace.

Everything here is redacted: no value the worker filled in, no answer it drafted, no email address, and no line of
a page that holds a value of the `# Resume` header of Profile.md.
"""
from __future__ import annotations

import collections
import fnmatch
import json
import re
import statistics
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import aa_lib as L
import lexicon as X
import workspace as W

TRACES_DIR = W.TRACES_DIR
LEDGER_PATH = TRACES_DIR / "ledger.jsonl"
DREAM_STATE = W.STATE_DIR / "dream" / "state.json"  # {"watermark": N}: the ledger lines the last dream read

TRACE_MAX_BYTES = 20_000
DREAM_HINT_TRACES = 10      # new traces before 'end' suggests a dream
COSTLY_FACTOR = 2.0         # a parked job this much over the median of its ATS is a "costly_win"
COSTLY_MIN_SAMPLES = 5      # fewer parked jobs of this ATS in the ledger: use COSTLY_TURNS_FALLBACK instead
COSTLY_TURNS_FALLBACK = 60

SIGNIN_STEPS = ("none", "option", "popup", "account", "consent", "done")
_SIGNIN_EVENTS = {"google-option": 1, "bind-signin": 2, "google-account": 3, "google-consent": 4, "signin-done": 5}


# --------------------------------------------------------------------------- classification


def classify_blocker(text: Optional[str]) -> str:
    """The code of a blocker text: its prefix when it has one, else the code its words point to."""
    blocker = (text or "").strip()
    low = blocker.casefold()
    plain = re.sub(r"[^a-z ]", "", low).strip()
    if plain == L.QUESTIONS_BLOCKER.casefold():
        return "QUESTIONS"
    if plain == L.ALREADY_SUBMITTED:
        return "ALREADY_SUBMITTED"
    for code in L.BLOCKER_CODES:
        if blocker.startswith(code):
            return code
    if "question" in low:
        return "QUESTIONS"
    if "closed" in low or "no longer" in low:
        return "CLOSED"
    if any(word in low for word in ("account", "sign in", "log in", "password", "register")):
        return "ACCOUNT_WALL"
    if "captcha" in low or "bot check" in low:
        return "CAPTCHA"
    if "iframe" in low or "not exposed" in low or "accessible tree" in low:
        return "NOT_ACCESSIBLE"
    if "load" in low or "error" in low or "went wrong" in low:
        return "LOAD_FAIL"
    return "OTHER"


def _is_harness_blocker(blocker: str) -> bool:
    """The worker was stopped by this harness, not by the site."""
    low = (blocker or "").casefold()
    if "google" in low and ("pop-up" in low or "popup" in low):
        return True
    return any(h in low for h in ("hook", "guard")) and any(c in low for c in ("brief", "claude error", "authenticate", "refused"))


def snapshot_has_password_or_signup(run_dir: Optional[Path], tab: Any) -> bool:
    """The last page of this tab really had a sign-up or a password field: an ACCOUNT_WALL report is then verified."""
    if run_dir is None:
        return False
    text = L.snapshot_text(run_dir, tab)
    if 'type="password"' in text:
        return True
    for item in L.snapshot_refs(run_dir, tab).values():
        if (item.get("attrs") or {}).get("type") == "password":
            return True
    return any(X.SIGNUP.find(line) for line in text.splitlines())


def classify_outcome(verdict: str, code: str, blocker: str, has_result: bool, stopped_by: Any = None,
                     judge_failure: bool = False, signin_step: Optional[str] = None,
                     account_wall_verified: Optional[bool] = None) -> str:
    """What this job really was: parked, handoff, unverified_handoff, wall or harness."""
    if verdict == "ALARM" or judge_failure or _is_harness_blocker(blocker):
        return "harness"
    if stopped_by in ("idle", "ended"):
        return "harness"
    if verdict == "FAIL" and not has_result and not stopped_by:
        return "harness"
    if stopped_by in ("stall", "repeat_deny", "budget"):
        return "wall"
    if signin_step == "option":  # the worker clicked the Google option and never reached a Google page
        return "harness"
    if verdict == "PASS":
        return "parked"
    if code in ("QUESTIONS", "CLOSED", "ALREADY_SUBMITTED"):
        return "handoff"
    if code == "ACCOUNT_WALL":
        return "unverified_handoff" if account_wall_verified is False else "handoff"
    return "wall"


def classify_bin(outcome: str, ats: str, turns: Optional[int], tokens: Any, ledger_lines: list[dict]) -> str:
    """Which jobs are worth a trace: a wall, a harness failure, a costly win, the first win on an unknown ATS."""
    if outcome in ("wall", "unverified_handoff"):
        return "wall"
    if outcome == "harness":
        return "harness"
    if outcome == "handoff":
        return "skip"
    if outcome != "parked":
        return "skip"
    same = [line for line in ledger_lines
            if line.get("ats") == ats and line.get("outcome") == "parked"
            and line.get("turns") is not None and line.get("tokens") is not None]
    current = sum(tokens.values()) if isinstance(tokens, dict) else (tokens or 0)
    if len(same) >= COSTLY_MIN_SAMPLES:
        median_turns = statistics.median([line["turns"] for line in same])
        # every ledger line holds a dict of token counts: sum each one before the median (a list of dicts raises)
        median_tokens = statistics.median([sum((line["tokens"] or {}).values()) for line in same])
        if (turns is not None and turns > COSTLY_FACTOR * median_turns) or current > COSTLY_FACTOR * median_tokens:
            return "costly_win"
    elif turns is not None and turns > COSTLY_TURNS_FALLBACK:
        return "costly_win"
    for note in L.ats_notes():  # a known ATS: nothing new to learn from a win
        if note["key"] == ats or any(fnmatch.fnmatch(ats, pattern) for pattern in note["hosts"]):
            return "skip"
    return "first_win"


# --------------------------------------------------------------------------- the run's own evidence


def _log_entries(run_dir: Path) -> list[dict]:
    path = run_dir / "guard.log"
    out = []
    for raw in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []):
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def guard_denials(run_dir: Path, tab: Any) -> tuple[int, list[dict]]:
    """How often the guard denied a call of this tab, and the reasons (short)."""
    key = L.tab_key(tab)
    events = [{"reason": (e.get("reason") or "")[:200], "tool": e.get("tool")}
              for e in _log_entries(run_dir) if e.get("event") == "deny" and str(e.get("tab", "")) == key]
    return len(events), events


def has_judge_failure(run_dir: Path, tab: Any) -> bool:
    """A Haiku check of this tab failed: the job was blocked by this harness, not by the site."""
    key = L.tab_key(tab)
    return any(e.get("event") == "judge" and e.get("error") and str(e.get("tab", "")) == key
               for e in _log_entries(run_dir))


def signin_info(run_dir: Path, tab: Any, state: dict) -> Optional[dict]:
    """How far the Google sign-in of this tab came, and whether it was its own, shared or waited for."""
    entries = _log_entries(run_dir)
    if not entries:
        return None
    key = L.tab_key(tab)
    signin_tab = next((k for k, v in (state.get("tabs") or {}).items()
                       if v.get("role") == "signin" and str(v.get("opener")) == key), None)
    mine = {key} | ({signin_tab} if signin_tab else set())
    step_index, waited, host, own_option = 0, False, None, False
    for entry in entries:
        event, event_tab = entry.get("event"), str(entry.get("tab", ""))
        if event_tab not in mine:
            continue
        if event in _SIGNIN_EVENTS:
            step_index = max(step_index, _SIGNIN_EVENTS[event])
            host = host or entry.get("host")
            own_option = own_option or (event == "google-option" and event_tab == key)
        elif event == "lease-wait" and event_tab == key:
            waited = True
        elif event == "lease-shared" and event_tab == key:
            waited = False  # it did not wait in the end: another tab had signed in already
    if step_index == 0:
        return None
    kind = "waited" if waited else ("own" if own_option else "shared")
    return {"host": host, "type": kind, "step": SIGNIN_STEPS[step_index]}


# --------------------------------------------------------------------------- redaction


def _profile_values() -> list[str]:
    """The values of the `# Resume` header of Profile.md (`- Name: ...`): they never go into a trace."""
    values = []
    try:
        text = L.PROFILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values
    for line in text.splitlines():
        # the header lines of `# Resume`: `- Name: ...` and `- **Name:** ...` (W.file_prefix accepts both)
        match = re.match(r"^\s*-\s*\*{0,2}(Name|Email|Phone|Address|Location)\*{0,2}\s*:\s*(.+?)\s*$", line, re.I)
        if match and len(match.group(2)) > 2:
            values.append(match.group(2))
    return values


def redact_text(text: str) -> str:
    """No email address and no line that holds a value of the `# Resume` header of Profile.md.

    A line with such a value is dropped whole, not masked: the rest of that line is the candidate's data too."""
    values = _profile_values()
    kept = [line for line in (text or "").splitlines() if not any(value in line for value in values)]
    out = "\n".join(kept) if "\n" in (text or "") else ("".join(kept))
    return L.EMAIL_RE.sub("<email>", out)


def redact_result(result: Optional[dict]) -> Optional[dict]:
    """The worker's report without what it filled in: the field names stay, the values go."""
    if result is None:
        return None
    out: dict = {}
    for key, value in result.items():
        if key == "filled":
            out[key] = [item.split(" = ")[0] if " = " in item else item for item in (value or [])]
        elif key == "drafted":
            out[key] = [{"q": item.get("q", "")} for item in (value or []) if isinstance(item, dict)]
        elif key in ("open", "questions", "declarations", "final_button", "uploaded", "url", "where"):
            out[key] = value
    return out


def page_tail(text: str, max_lines: int = 60) -> str:
    """The lines of the last page around a Google, sign-up, password or final-button word: why the worker stopped."""
    lines = text.splitlines()
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if X.GOOGLE.find(line) or X.SIGNUP.find(line) or X.FINAL.find(line) or "password" in line.lower():
            keep.update(range(max(0, i - 2), min(len(lines), i + 3)))
    return "\n".join(lines[i] for i in sorted(keep)[:max_lines])


# --------------------------------------------------------------------------- the ledger


def read_ledger() -> list[dict]:
    lines = []
    for raw in (LEDGER_PATH.read_text(encoding="utf-8").splitlines() if LEDGER_PATH.exists() else []):
        if raw.strip():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue  # a damaged line is skipped: the ledger must never stop 'end'
    return lines


def _append_ledger(entry: dict) -> None:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def watermark() -> int:
    """How many ledger lines the last dream has read (`.ohmyjob/dream/state.json`)."""
    try:
        state = L.read_json(DREAM_STATE, {})
    except L.DataError:
        return 0
    value = state.get("watermark") if isinstance(state, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0


def dream_hint() -> Optional[str]:
    """The line that 'end' prints when enough new traces are waiting for a dream."""
    new = sum(1 for line in read_ledger()[watermark():] if line.get("bin") in ("wall", "costly_win", "first_win", "harness"))
    if new >= DREAM_HINT_TRACES:
        return f"{new} new traces since the last dream: run /ohmyjob:dream"
    return None


def _best_url(result: Optional[dict], tab_urls: dict, tab: Any) -> str:
    """The job's own page: a Google page is never it (the worker was signing in there)."""
    url = result.get("url") or "" if isinstance(result, dict) else ""
    if not url or L.is_google_host(L.host_of(url)):
        url = tab_urls.get(L.tab_key(tab), "")
        if L.is_google_host(L.host_of(url)):
            url = ""
    return url


def _worker_folder(rec: dict, state: dict) -> Optional[str]:
    """The job folder of a worker: its tab in the registry, else the `folder=` of its first message."""
    for entry in (state.get("tabs") or {}).values():
        if entry.get("role") == "job" and entry.get("agent_id") == rec["agent_id"]:
            return entry.get("folder")
    return L.worker_folder_from_transcript(rec["agent_id"], rec["transcript"], set(state.get("jobs") or {}))


def _folder_usage(agents: list[dict], folder: str, state: dict) -> dict:
    """The calls and tokens of every worker of this job (a retried job has two)."""
    turns = 0
    tokens: collections.Counter = collections.Counter()
    for rec in agents:
        if rec["agent_type"] != L.WORKER or _worker_folder(rec, state) != folder:
            continue
        turns += rec["usage"].get("calls", 0)
        for key in L.USAGE_KEYS:
            tokens[key] += rec["usage"].get(key, 0)
    return {"turns": turns, "tokens": dict(tokens)}


def _tool_steps(agent_id: str, transcript_path: str) -> list[dict]:
    """What the worker did, one line per tool call (no filled values: only the action summaries)."""
    session = Path(transcript_path)
    path = session.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"
    if not path.exists():
        path = session

    def one(name: str, args: dict) -> Optional[dict]:
        short = name.replace("mcp__claude-in-chrome__", "")
        if short == "Read":
            return None
        step = {"tool": short}
        if short == "computer":
            step.update({"action": args.get("action"), "summary": args.get("action_summary")})
        elif short == "form_input":
            step["summary"] = args.get("action_summary")
        elif short == "navigate":
            parts = urlsplit(args.get("url") or "")
            step["nav"] = (parts.hostname or "") + (parts.path or "")
        return step

    steps: list[dict] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message = event.get("message") if event.get("type") == "assistant" else None
            for block in (message or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                args = block.get("input") or {}
                if (block.get("name") or "").endswith("browser_batch"):
                    for action in args.get("actions") or []:
                        inner = one(action.get("name") or "", action.get("input") or {})
                        if inner:
                            steps.append(inner)
                    continue
                step = one(block.get("name") or "", args)
                if step:
                    steps.append(step)
    except (OSError, UnicodeError, ValueError):
        pass
    return steps


def _write_trace(run_dir: Path, state: dict, entry: dict, result: Optional[dict], agents: list[dict],
                 folder: str, denial_events: list[dict], tab: Any) -> str:
    """One trace file for a job worth learning from; returns its name inside the traces folder."""
    trace = {
        "id": entry["id"], "ats": entry["ats"], "host": entry["host"],
        "verdict": entry["verdict"], "code": entry["code"], "blocker": entry["blocker"],
        "outcome": entry["outcome"], "bin": entry["bin"],
        "where": result.get("where") if isinstance(result, dict) else None,
        "report": redact_result(result),
        "steps": [],
        "denials": [{"reason": redact_text(d["reason"]), "tool": d.get("tool")} for d in denial_events],
        "page_tail": redact_text(page_tail(L.snapshot_text(run_dir, tab))),
        "signin": entry["signin"], "google": entry["google"],
    }
    for rec in agents:
        if rec["agent_type"] == L.WORKER and _worker_folder(rec, state) == folder:
            trace["steps"] += _tool_steps(rec["agent_id"], rec["transcript"])
    # a step holds the worker's own action summary, which can name a value it typed
    trace["steps"] = [{key: redact_text(value) if isinstance(value, str) else value for key, value in step.items()}
                      for step in trace["steps"]]
    raw = json.dumps(trace, ensure_ascii=False)
    if len(raw.encode("utf-8")) > TRACE_MAX_BYTES:
        trace["steps"] = trace["steps"][:20] + trace["steps"][-60:]
        raw = json.dumps(trace, ensure_ascii=False)
    name = f"{L.today()}_{run_dir.name}_{folder}.json"
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    L.write_text(TRACES_DIR / name, raw)
    return name


def write_ledger_and_traces(run_dir: Path, state: dict, transcript: Optional[str],
                            master_id: Optional[str]) -> int:
    """One ledger line per job of this run, and a trace for the jobs worth learning from. Returns the line count."""
    ledger = read_ledger()
    agents = L.agent_usage(transcript, master_id)
    tab_urls = state.get("tab_urls") or {}
    written = 0
    for folder, job in (state.get("jobs") or {}).items():
        if job.get("tab") is None:
            continue
        tab = job["tab"]
        result = L.read_json(L.job_dir(run_dir, folder) / "result.json", None)
        if not isinstance(result, dict):
            result = None
        verdict = job.get("verdict") or ("PASS" if result and result.get("status") == "parked" else "BLOCKED")
        blocker = (result or {}).get("blocker") or ""
        code = classify_blocker(blocker) if blocker else ""
        url = _best_url(result, tab_urls, tab)
        host = L.host_of(url) or ""
        ats = L.ats_key(host) if host else ""
        denials, denial_events = guard_denials(run_dir, tab)
        signin = signin_info(run_dir, tab, state)
        step = (signin or {}).get("step")
        entry_tab = (state.get("tabs") or {}).get(L.tab_key(tab)) or {}
        in_snapshot = any(X.GOOGLE.find(item.get("name") or "")
                          for item in L.snapshot_refs(run_dir, tab).values())
        google = {"count": entry_tab.get("google_count", 0), "named": entry_tab.get("google_named", 0),
                  "in_snapshot": in_snapshot}
        # the page had a Google option and no sign-in step ran: the worker missed it, it was no account wall
        if code == "ACCOUNT_WALL" and step in (None, "none") and (google["count"] > 0 or in_snapshot):
            code = "GOOGLE_MISSED"
        verified = snapshot_has_password_or_signup(run_dir, tab) if code == "ACCOUNT_WALL" else None
        usage = _folder_usage(agents, folder, state)
        watchdog = job.get("watchdog") or {}
        stopped_by = job.get("stopped_by")
        result_path = L.job_dir(run_dir, folder) / "result.json"
        end = job.get("ended_at") or (result_path.stat().st_mtime if result_path.exists() else None)
        seconds = round(end - job["spawned_at"], 1) if job.get("spawned_at") and end else None
        tokens = {"input": usage["tokens"].get("input_tokens", 0),
                  "cache_creation": usage["tokens"].get("cache_creation_input_tokens", 0),
                  "cache_read": usage["tokens"].get("cache_read_input_tokens", 0),
                  "output": usage["tokens"].get("output_tokens", 0)}
        outcome = classify_outcome(verdict, code, blocker, result is not None, stopped_by,
                                   judge_failure=has_judge_failure(run_dir, tab), signin_step=step,
                                   account_wall_verified=verified)
        entry = {
            "id": f"{run_dir.name}/{folder}", "run": run_dir.name, "date": L.today(),
            "mode": "continue" if job.get("continue") else "apply", "source": "run",
            "folder": folder, "company": job.get("company", ""), "title": job.get("title", ""),
            "attempt": 1 + sum(1 for line in ledger if line.get("folder") == folder),
            "url": url, "host": host, "ats": ats, "verdict": verdict,
            "blocker": redact_text(blocker[:300]) if blocker else None, "code": code or None,
            "outcome": outcome, "bin": classify_bin(outcome, ats, usage["turns"], tokens, ledger),
            "workers": max(1, sum(1 for rec in agents if rec["agent_type"] == L.WORKER
                                  and _worker_folder(rec, state) == folder)),
            "turns": usage["turns"], "tokens": tokens, "denials": denials,
            "seconds": seconds, "calls": watchdog.get("calls", 0),
            "progress_events": watchdog.get("progress", 0), "max_stall": watchdog.get("max_stall", 0),
            "stopped_by": stopped_by,
            # a worker the watchdog gave up on still sent its report: the line says so
            "late_report": True if stopped_by == "idle" and result is not None else None,
            "signin": signin, "google": google, "trace": None,
        }
        if entry["bin"] in ("wall", "costly_win", "first_win", "harness"):
            entry["trace"] = _write_trace(run_dir, state, entry, result, agents, folder, denial_events, tab)
        _append_ledger(entry)
        ledger.append(entry)
        written += 1
    return written
