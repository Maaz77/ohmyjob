#!/usr/bin/env python3
"""Application-assistant commands for application-master.

Run it as `ohmyjob apply <command>` (bin/ohmyjob finds the workspace and the plugin's Python).

  start                  delete old run folders, queue every tracker row at "Resume Built" (a posting that the early
                         liveness check finds closed is set to Deleted first; see liveness.py)
  start --continue       /continue-applying: queue the jobs in Needs-Attention/ whose tab the candidate fixed by hand
  preflight --tab H      LinkedIn sign-in check on tab H (only when a queued job is on linkedin.com), pop-up helper
                         check (with its version), Haiku check test
  preflight --tab H --home-only  /continue-applying: no sign-in or Haiku check; registers tab H, waits for the helper tag
  assign --tabs T1,T2    give each new tab one job, write the briefs, queue the workers ('next' prints them)
  adopt-plan             /continue-applying: the page that brings the job tabs back from the "Needs Attention" group
  adopted                /continue-applying: keep the jobs whose tab came back; leave the others alone
  assign --continue      /continue-applying: give each kept job its own tab again, queue the workers
  next                   print the queued workers for the free slots (an UPLOAD_HANG job is queued again once);
                         waits up to 90 s for a free slot; prints "done": true when every worker has reported
  retry-plan             a job whose worker reported UPLOAD_HANG gets a short resume name and a new worker (once)
  finish                 check every worker result, write the job.md notes, move the folders, update the tracker
  group-plan             the page that puts the job tabs in the groups "Pending Review" and "Needs Attention"
  cleanup-plan           tabs to close at the end of the run
  questions              the questions about the candidate that workers could not answer from Profile.md (waiting for the answers)
  answer --json '[...]'  save the candidate's answers in the "Follow Up Questions" section of Profile.md
  end                    delete the run folder (last step of the run)

Every command prints JSON or one line. An error prints a line that starts with FATAL.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit
from typing import Any, Optional

import yaml

import aa_lib as L
import judge as J
import ledger
import liveness
import workspace as W
from tracker import Tracker, TrackerError


# --------------------------------------------------------------------------- output


def emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def fatal(message: str) -> None:
    print(f"FATAL: {message}")
    sys.exit(3)


class JobError(Exception):
    """A job that cannot be recorded. finish records the other jobs and lists this one."""


RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[0-9a-f]{6}$")  # the id that 'start' makes (see cmd_start)


def is_our_run_dir(path: Path) -> bool:
    """A real folder directly in the run base, named like a run id, that holds a run's own state (a stopped run included).
    Anything else in the base is not ours and stays."""
    if path.is_symlink() or not path.is_dir() or not RUN_ID_RE.match(path.name):
        return False
    state = L.read_json(path / "state.json", None) if (path / "state.json").is_file() else None
    if isinstance(state, dict):
        return state.get("run_id") == path.name
    return (path / ".lock").is_file()  # 'start' died between creating the folder and saving the state


def delete_run_folders() -> None:
    """Remove the run state: briefs (copies of the candidate's data), results, snapshots, logs.

    Only the run folders that 'start' made, and the CURRENT marker: never the base folder or anything else in it."""
    if not L.RUN_BASE.is_dir():
        return
    for path in sorted(L.RUN_BASE.iterdir()):
        if is_our_run_dir(path):
            shutil.rmtree(path)
    marker = L.RUN_BASE / "CURRENT"
    if marker.is_file() or marker.is_symlink():
        marker.unlink()


def need_run() -> Path:
    run_dir = L.current_run_dir()
    if run_dir is None:
        fatal("no active run; run 'start' first")
    return run_dir


# --------------------------------------------------------------------------- brief


def job_folder(job: dict) -> Path:
    """The job's folder: in Applications/ (/apply-jobs) or in Needs-Attention/ (/continue-applying)."""
    return (L.NEEDS_ATTENTION if job.get("continue") else L.APPLICATIONS) / job["folder"]


TAB_NOTE_RE = re.compile(r"^Tab (\d+) in the run window", re.M)
NOTE_URL_RE = re.compile(r"^Tab \d+ in the run window \| URL: (\S+)", re.M)


def last_note(job_md: str) -> str:
    """The last Application Assistant note of job.md (the run that sent the job to Needs Attention)."""
    start = job_md.rfind("**Run:**")  # every note starts with it (note_block); the last one is the newest
    if start < 0:
        return ""
    end = job_md.find("\n# ", start)  # up to the next top-level section, if any
    return job_md[start:end if end >= 0 else len(job_md)].strip()


def continue_section(job_md: str) -> str:
    note = last_note(job_md)
    if len(note) > 2500:
        note = note[:2500] + "\n(...)"
    return f"""
## Continue from the open page
The candidate fixed this job by hand after the last run (for example, signed in or created the account). Your tab
still shows the page where the candidate left it. Do not navigate first: call read_page on your tab and go on with the
application from that page (Flow step 2). If the page says that the application was sent or submitted, stop at
once with status "blocked" and blocker "already submitted": never start this job again. If the tab shows a sign-in or
account page (not an application form), navigate the tab to its current address once and call read_page before you
report `ACCOUNT_WALL`. Navigate to the Apply
URL (else the Job URL) of the brief only when the tab shows no application for this job (an empty or error page, or a page about something else).
The last run stopped as follows:

{note or "(no note of the last run in job.md)"}
"""


def resume_head(yaml_path: Path) -> Optional[str]:
    """The `title` and `introduction` of a tailored resume yaml, as a small yaml block; None when it cannot be read.

    A `document: cv` yaml has no introduction: its title alone. The keys are read by their names, never by splitting
    the text on `experience:` (the yamls have no fixed key order).
    """
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    head = {key: data[key] for key in ("title", "introduction") if isinstance(data.get(key), str) and data[key].strip()}
    if not head:
        return None
    return yaml.safe_dump(head, sort_keys=False, allow_unicode=True, width=1000).strip()


def ats_note_lines() -> str:
    """One line per ATS note the worker may read: `- <title> (hosts: <patterns>): <absolute path>`."""
    lines = []
    for note in L.ats_notes():
        hosts = ", ".join(note["hosts"]) or "the employer's own host"
        lines.append(f"  - {note['title']} (hosts: {hosts}): {note['path']}")
    return "\n".join(lines) if lines else "  (no ATS notes found)"


def write_brief(run_dir: Path, job: dict) -> Path:
    """The worker's only source: the full Profile.md (with its Follow Up Questions), the title and introduction of
    the tailored resume YAML, and the Overview and the Job Description of job.md (its match notes and resume-prep
    notes stay out). The rest of the YAML (roles, dates, projects, skills) repeats Profile.md, which the resume-worker
    never changes."""
    folder_path = job_folder(job)
    profile_text = L.PROFILE.read_text(encoding="utf-8").strip()
    if not profile_text:
        fatal("Profile.md is empty")
    job_md = (folder_path / "job.md").read_text(encoding="utf-8")
    overview = L.md_section_body(job_md, r"(1\.\s*)?Overview", 2) or "(no Overview section in job.md)"
    jd = L.job_description(job_md) or "(no Job Description section in job.md)"
    yamls = L.tailored_yamls(folder_path)
    head = resume_head(yamls[0]) if len(yamls) == 1 else None
    if head is not None:
        resume_title = f"Tailored resume for this job ({yamls[0].name}, title and introduction)"
        resume_text = "```yaml\n" + head + "\n```"
    elif len(yamls) == 1:  # a yaml that cannot be parsed, or holds neither key: keep today's text
        resume_title = f"Tailored resume for this job ({yamls[0].name}, full text)"
        resume_text = "```yaml\n" + yamls[0].read_text(encoding="utf-8").strip() + "\n```"
    else:
        resume_title = "Tailored resume for this job"
        resume_text = f"(expected 1 resume yaml in the job folder, found {len(yamls)})"
    apply_line = (f"\n- Apply URL (the employer's own application address: navigate to it first, instead of the Job URL): "
                  f"{job['apply_url']}" if job.get("apply_url") else "")
    text = f"""# Brief: {job['company']} | {job['title']}

- folder: {job['folder']}
- tab: {job['tab']}
- Job URL: {job['url']}{apply_line}
- Resume PDF to upload (exact path): {job['pdf']}
- ATS notes. When you reach one of these, Read the note once:
{ats_note_lines()}

{continue_section(job_md) if job.get("continue") else ""}
## Job: Overview (from job.md)
{overview.strip()}

## Job: Job Description (from job.md)
{jd.strip()}

## Profile.md (full text)
{profile_text}

## {resume_title}
{resume_text}
"""
    path = L.job_dir(run_dir, job["folder"]) / "brief.md"
    L.write_text(path, text)
    return path.resolve()


# --------------------------------------------------------------------------- job.md note


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- (none)"


def note_block(job: dict, result: Optional[dict], verdict: str, reasons: list[str]) -> str:
    date = L.today()
    result = result or {}
    where = result.get("where") or "(unknown)"
    url = result.get("url") or ""
    head = {
        "PASS": f"**Run:** {date} | Pending Review | parked at: {where}",
        "BLOCKED": f"**Run:** {date} | Needs Attention | BLOCKED at: {where}",
        "FAIL": f"**Run:** {date} | Needs Attention | the checks failed at: {where}",
        "CLOSED": f"**Run:** {date} | Deleted | job closed",
        "ALARM": f"**Run:** {date} | Needs Attention | CHECK THIS JOB NOW: the page shows a submission message",
    }[verdict]
    parts = [head, "", f"Tab {job.get('tab')} in the run window | URL: {url}"]
    if verdict == "PASS" and result.get("final_button"):
        parts.append(f"Final button left for you: \"{result['final_button']}\"")
    if verdict == "BLOCKED":
        parts += ["", f"**Blocker:** {result.get('blocker') or '; '.join(reasons)}"]
    if verdict in ("FAIL", "ALARM"):
        parts += ["", "**Checks:**", _bullets(reasons)]
    if result.get("filled"):
        parts += ["", "Filled:", _bullets(result["filled"])]
    parts += ["", f"Uploaded resume: {result.get('uploaded') or 'not uploaded'}"]
    drafted = result.get("drafted") or []
    if drafted:
        parts += ["", "Drafted answers (check them before you submit):"]
        for item in drafted:
            parts.append(f"- **Q:** {item.get('q')}\n  **A:** {item.get('a')}")
    actions = [f"Question for you (saved in Profile.md once you answer): {x}" for x in result.get("questions") or []]
    actions += [f"Open: {x}" for x in result.get("open") or []]
    actions += [f"Consent or declaration ticked or accepted by the worker (check it): {x}" for x in result.get("declarations") or []]
    if verdict == "PASS":
        actions.append("Review every field, then submit yourself.")
    parts += ["", "**ACTION NEEDED FROM YOU:**", _bullets(actions)]
    return "\n".join(parts).rstrip() + "\n"


def insert_note(job_md: str, block: str) -> str:
    lines = job_md.rstrip("\n").splitlines()
    for level, title, start, end in L.md_sections(job_md.rstrip("\n")):
        if level == 1 and title.strip().lower() == "application assistant":
            new = lines[:end] + ["", block.rstrip("\n")] + lines[end:]
            return "\n".join(new).rstrip("\n") + "\n"
    return "\n".join(lines) + "\n\n# Application Assistant\n\n" + block


def tracker_note(verdict: str, result: Optional[dict], reasons: list[str]) -> str:
    result = result or {}
    if verdict == "PASS":
        text = f"Pending Review, parked at {result.get('where')}; open {len(result.get('open') or [])}, drafted {len(result.get('drafted') or [])}"
    elif verdict == "CLOSED":
        text = f"Deleted, job closed: {result.get('blocker')}"
    elif verdict == "BLOCKED":
        text = f"Needs Attention, blocked: {result.get('blocker') or '; '.join(reasons)}"
    else:
        text = f"Needs Attention, {verdict}: {'; '.join(reasons)}"
    return f"Application Assistant {L.today()}: {' '.join(text.split())[:300]}"


# --------------------------------------------------------------------------- commands


def match_score(row: dict) -> float:
    """The tracker Match Score as a number; a row without one ranks last (and keeps its place among its equals)."""
    try:
        return float(row["Match Score"])
    except (TypeError, ValueError):
        return float("-inf")


def pick_rows(rows: list[dict], args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
    """The "Resume Built" rows to check, in tracker order, and the skips of the --selected entries."""
    ready = [row for row in rows if (row["Status"] or "").strip() == "Resume Built"]
    if args.selected is None:
        return ready, []
    names, skipped = L.match_folders(args.selected)
    chosen: list[dict] = []
    for name in names:
        key = L.folder_id(name)
        found = [row for row in ready if key is not None and row["_key"] == key]
        if found:
            chosen.extend(found)
        elif key is not None and any(row["_key"] == key for row in rows):
            skipped.append({"job": name, "reason": "not at Resume Built"})
        else:
            skipped.append({"job": name, "reason": "no tracker row for this job id"})
    return chosen, skipped


def continue_queue(rows: list[dict], by_key: dict) -> tuple[list[dict], list[dict]]:
    """/continue-applying: every job in Needs-Attention/ at "Needs Attention", with the tab its last note names."""
    folders: dict[str, list[Path]] = {}
    if L.NEEDS_ATTENTION.is_dir():
        for path in sorted(L.NEEDS_ATTENTION.iterdir()):
            fid = L.folder_id(path.name) if path.is_dir() else None
            if fid:
                folders.setdefault(fid, []).append(path)
    queue, skipped = [], []
    for row in rows:
        if (row["Status"] or "").strip() != "Needs Attention":
            continue
        label = f"{row['Company']} | {row['Job Title']}"
        candidates = folders.get(row["_key"], [])
        if len(by_key[row["_key"]]) > 1 or len(candidates) != 1:
            skipped.append({"job": label, "reason": "not exactly one tracker row and one folder in Needs-Attention/"})
            continue
        folder = candidates[0]
        pdf = L.job_pdf(folder)
        md_path = folder / "job.md"
        if pdf is None or not md_path.exists():
            skipped.append({"job": label, "folder": folder.name, "reason": "expected job.md and a PDF next to it"})
            continue
        job_md = md_path.read_text(encoding="utf-8")
        note = last_note(job_md)
        if "CHECK THIS JOB NOW" in note.split("\n", 1)[0]:
            skipped.append({"job": label, "folder": folder.name, "reason": "the last run saw a submission message: check it by hand"})
            continue
        tabs = TAB_NOTE_RE.findall(note)  # may be empty: a tab the candidate added is matched by its page (match_tabs)
        overview = L.job_overview(job_md)
        queue.append({
            "folder": folder.name, "company": row["Company"] or overview.get("Company", ""),
            "title": row["Job Title"] or overview.get("Job Title", ""),
            "url": overview.get("Job URL") or row["Job URL"], "apply_url": overview.get("Apply URL") or "",
            "pdf": str(pdf.resolve()),
            "location": str(overview.get("Location") or row["Location"] or ""),
            "continue": True, "prev_tab": int(tabs[-1]) if tabs else None,
            # the pages this job used, best first: the address of the last note, the Apply URL, the Job URL
            "urls": [u for u in (*NOTE_URL_RE.findall(note)[-1:], overview.get("Apply URL"),
                                 overview.get("Job URL") or row["Job URL"]) if u],
        })
    return queue, skipped


# The master loads one of these in its home tab: the helper extension (chrome-extension/) registers the run's tab group
# on them. The feed also shows whether LinkedIn is signed in; robots.txt is a public page, for a run with no LinkedIn job.
FEED_MARKER_URL = "https://www.linkedin.com/feed/#aa-popups-as-tabs"
ROBOTS_MARKER_URL = "https://www.linkedin.com/robots.txt#aa-popups-as-tabs"


def cmd_start(args: argparse.Namespace) -> None:
    if W.has_template_marker(W.PROFILE):
        fatal("Profile.md still has the template marker (the mock data of the template): fill in your own details, then "
              "delete the marker line, and run this again.")
    if args.cont and (args.selected is not None or args.top_k is not None):
        fatal("--continue takes no --selected and no --top-k-match")
    if args.selected is not None and args.top_k is not None:
        fatal("give --selected or --top-k-match, not both")
    if args.top_k is not None:
        if not args.top_k.strip().isdecimal() or int(args.top_k) < 1:
            fatal(f"--top-k-match takes a whole number of 1 or more, got {args.top_k!r}")
        args.top_k = int(args.top_k)
    W.load_config()  # reads ohmyjob.json now: a bad config stops the run here (main prints FATAL), not in the middle of it
    # A run that stopped early leaves its folder. Its jobs are still at "Resume Built", so they are queued again.
    delete_run_folders()
    L.RUN_BASE.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    try:
        tracker = Tracker(L.TRACKER)
    except TrackerError as exc:
        fatal(str(exc))
    rows = tracker.rows()
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(row["_key"], []).append(row)
    folders: dict[str, list[Path]] = {}
    if L.APPLICATIONS.is_dir():
        for path in sorted(L.APPLICATIONS.iterdir()):
            fid = L.folder_id(path.name) if path.is_dir() else None
            if fid:
                folders.setdefault(fid, []).append(path)
    queue, skipped = continue_queue(rows, by_key) if args.cont else ([], [])
    ready, selection_skips = ([], []) if args.cont else pick_rows(rows, args)
    skipped.extend(selection_skips)
    scores: dict[str, float] = {}
    for row in ready:
        label = f"{row['Company']} | {row['Job Title']}"
        if len(by_key[row["_key"]]) > 1:
            skipped.append({"job": label, "reason": "more than one tracker row has this job id"})
            continue
        candidates = folders.get(row["_key"], [])
        if len(candidates) != 1:
            reason = "no folder in Applications/" if not candidates else "more than one folder has this job id"
            skipped.append({"job": label, "reason": reason})
            continue
        folder = candidates[0]
        pdf = L.job_pdf(folder)
        if pdf is None:
            skipped.append({"job": label, "folder": folder.name, "reason": "no PDF next to job.md"})
            continue
        job_md_path = folder / "job.md"
        if not job_md_path.exists():
            skipped.append({"job": label, "folder": folder.name, "reason": "job.md is missing"})
            continue
        overview = L.job_overview(job_md_path.read_text(encoding="utf-8"))
        url = overview.get("Job URL") or row["Job URL"]
        if not url:
            skipped.append({"job": label, "folder": folder.name, "reason": "no job URL"})
            continue
        location = overview.get("Location") or row["Location"] or ""
        scores[folder.name] = match_score(row)
        queue.append(
            {
                "folder": folder.name,
                "company": row["Company"] or overview.get("Company", ""),
                "title": row["Job Title"] or overview.get("Job Title", ""),
                "url": url,
                "apply_url": overview.get("Apply URL") or "",
                "pdf": str(pdf.resolve()),
                "location": str(location),
            }
        )
    closed_jobs: list[str] = []
    if not args.cont and queue:  # early liveness check: a positively closed posting is dropped before a tab is opened
        verdicts = liveness.find_closed([(q["folder"], q["apply_url"] or q["url"]) for q in queue])
        gone = [(by_key[L.folder_id(q["folder"])][0]["_index"], L.APPLICATIONS / q["folder"], verdicts[q["folder"]])
                for q in queue if q["folder"] in verdicts]
        if gone:
            try:
                liveness.delete_closed(tracker, gone, L.today())  # rows first (one save), then the folders
            except Exception as exc:  # noqa: BLE001  (a stale or locked tracker: nothing was deleted, the jobs stay queued)
                warnings.append(f"liveness check: the tracker could not be saved ({exc}); closed jobs stay queued")
                gone = []
        done = {folder.name for _i, folder, _c in gone}
        closed_jobs = [f"{q['company']} | {q['title']}: {liveness.CLOSED_TEXT[verdicts[q['folder']]]}" for q in queue if q["folder"] in done]
        queue = [q for q in queue if q["folder"] not in done]
    if args.top_k is not None:  # the best K of the jobs that can be queued; the rest stay at "Resume Built"
        queue = sorted(queue, key=lambda q: scores[q["folder"]], reverse=True)[: args.top_k]  # stable on ties
    # The LinkedIn sign-in is checked only when a queued job starts on linkedin.com (its Apply URL when it has one).
    needs_linkedin = (not args.cont) and any(L.is_linkedin_url(q.get("apply_url") or q["url"]) for q in queue)
    home_url = FEED_MARKER_URL if needs_linkedin else ROBOTS_MARKER_URL
    run_id = f"{L.today()}_{uuid.uuid4().hex[:6]}"
    run_dir = L.RUN_BASE / run_id
    state = {
        "run_id": run_id,
        "started": L.now_iso(),
        "status": "running" if queue else "done",
        "queue": [q["folder"] for q in queue],
        "jobs": {
            q["folder"]: {**q, "tab": None, "verdict": None, "reasons": [], "recorded": False, "dest": None,
                          "capture_blocks": 0}
            for q in queue
        },
        "tabs": {},
        "tab_urls": {},
        "tab_urls_seen": {},
        "home_tab": None,
        "initial_tabs": None,
        "skipped": skipped,
        "closed_jobs": closed_jobs,
        "preflight": {},
        "needs_linkedin": needs_linkedin,
        "home_url": home_url,
        "mode": "continue" if args.cont else "apply",
    }
    with L.locked(run_dir):
        L.save_state(run_dir, state)
    L.write_text(L.RUN_BASE / "CURRENT", run_id)
    emit(
        {
            "run_id": run_id,
            "count": len(queue),
            "jobs": [{"folder": q["folder"], "company": q["company"], "title": q["title"]} for q in queue],
            "skipped": skipped,
            "closed": closed_jobs,
            "warnings": warnings,
            "home_url": home_url,
            "linkedin_sign_in_check": needs_linkedin,
        }
    )


# The extension writes its title tag a moment after the page loads, and the master can read the tabs before that
# ("[AA on <version>]" can come one call too late, and the run would report the helper as off). Wait, then ask for a
# new tab list.
TITLE_TAG_RETRIES = 3
TITLE_TAG_WAIT = 2.0  # seconds


def retry_for_title(run_dir: Path, counter: str, command: str) -> bool:
    """True (and one RETRY line) while the tag can still come: call tabs_context_mcp, then run the command again."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        tries = int(state.get(counter) or 0)
        if tries >= TITLE_TAG_RETRIES:
            return False
        state[counter] = tries + 1
        L.save_state(run_dir, state)
    time.sleep(TITLE_TAG_WAIT)
    print(f"RETRY call tabs_context_mcp alone, then run {command} again")
    return True


RELOAD_HINT = "reload it in chrome://extensions (/ohmyjob:setup prints its folder)"


def helper_note(helper: bool, title: str) -> str:
    """The pop-up helper part of the preflight line: off, on, or on with another version than the plugin's."""
    if not helper:
        return ("WARN pop-up helper: off (Google sign-ins in a pop-up will go to Needs-Attention; load the ohmyjob "
                "chrome-extension folder in chrome://extensions, /ohmyjob:setup prints its path)")
    have, want = L.helper_version(title), L.extension_version()
    if want and have != want:
        return (f"WARN pop-up helper: on, but version {have or 'older than 1.6'} while the plugin has {want}: "
                f"{RELOAD_HINT}")
    return "pop-up helper: on"


def limits_note() -> str:
    """The worker limits for the preflight line: `OK limits: {...}`, or `WARN limits: ...` for a value that fell back."""
    limits = L.worker_limits()
    warnings = limits.get("_warnings") or []
    values = {k: v for k, v in limits.items() if k != "_warnings"}
    if warnings:
        return ("WARN limits: " + ", ".join(f"{k} unusable, using {values[k]}" for k in warnings)
                + f" (edit {L.LIMITS_PATH.name} in your workspace)")
    return f"OK limits: {values}"


def cmd_preflight(args: argparse.Namespace) -> None:
    """One line: OK (or SIGNED_OUT / UNKNOWN), then the state of the pop-up helper and of the Haiku check."""
    run_dir = need_run()
    key = L.tab_key(args.tab)
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if L.job_tab_owner(state, args.tab):
            fatal(f"tab {args.tab} belongs to a job; use a separate tab for the check")
        state["tabs"][key] = {**state["tabs"].get(key, {}), "role": "home"}
        state["home_tab"] = int(args.tab)
        url = (state.get("tab_urls_seen") or {}).get(key)
        title = (state.get("tab_titles") or {}).get(key) or ""
        L.save_state(run_dir, state)
    if args.home_only:  # /continue-applying: the tabs are signed in already; only wait for the helper to register the home tab
        command = f"preflight --tab {args.tab} --home-only"
        helper = bool(L.HELPER_ON_RE.match(title))
        if not helper and retry_for_title(run_dir, "preflight_retries", command):
            return
        print("OK sign-in check skipped | " + helper_note(helper, title))
        return
    if not url:
        print("UNKNOWN: no URL is known for this tab yet. Call tabs_context_mcp, then run preflight again.")
        return
    needs_linkedin = bool(state.get("needs_linkedin", True))
    if needs_linkedin and L.LINKEDIN_SIGNED_OUT_RE.search(url):
        print(f"SIGNED_OUT {url}")
        return
    if needs_linkedin and L.host_of(url) and L.host_of(url).endswith("linkedin.com") \
            and not re.match(r"^/feed(/|$)", urlsplit(url).path):
        # a queued job is on linkedin.com: only the feed shows whether LinkedIn is signed in (robots.txt shows nothing)
        if not retry_for_title(run_dir, "preflight_retries", f"preflight --tab {args.tab}"):
            print(f"UNKNOWN: a queued job is on linkedin.com, so tab {args.tab} must show the feed (home_url of 'start'), "
                  f"not {url}")
        return
    command = f"preflight --tab {args.tab}"
    if not (L.host_of(url) and L.host_of(url).endswith("linkedin.com")):  # both marker pages are on linkedin.com
        if not retry_for_title(run_dir, "preflight_retries", command):
            print(f"UNKNOWN: tab {args.tab} is not on the page that 'start' printed as home_url ({url})")
        return
    helper = bool(L.HELPER_ON_RE.match(title))
    if not helper and retry_for_title(run_dir, "preflight_retries", command):
        return
    judge_ok, judge_note = J.selftest(run_dir)
    notes = []
    if not needs_linkedin:
        notes.append("LinkedIn sign-in check skipped: no queued job is on linkedin.com")
    notes.append(helper_note(helper, title))
    notes.append(f"Haiku check: OK ({judge_note})" if judge_ok else
                 f"WARN Haiku check: not available ({judge_note}); button names that no word list knows will be blocked")
    notes.append(limits_note())
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        state["preflight"] = {"helper": helper, "judge": judge_ok, "judge_note": judge_note,
                              "helper_version": L.helper_version(title) if helper else None}
        L.save_state(run_dir, state)
    print(f"OK {url} | " + " | ".join(notes))


def cmd_adopt_plan(args: argparse.Namespace) -> None:
    """/continue-applying, after preflight: the page for the master's home tab. The helper extension moves the listed
    tabs from the group "Needs Attention" back into Claude's group, where the workers can use them."""
    run_dir = need_run()
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if state.get("mode") != "continue" or state.get("home_tab") is None:
            fatal("adopt-plan is for /continue-applying, after 'preflight'")
        # the helper extension brings back every tab of the group "Needs Attention", also one the candidate added; the
        # ids in the address only serve an older version, which moved just the tabs it had grouped itself
        tabs = sorted({state["jobs"][f]["prev_tab"] for f in state["queue"]} - {None})
        # the query makes it a new page: the home tab is on robots.txt#aa-popups-as-tabs, and a change after '#' alone does
        # not load a page again, so the extension would never see the plan
        url = ("https://www.linkedin.com/robots.txt?aa-adopt#aa-adopt=" + quote(json.dumps(tabs, separators=(",", ":")), safe="")
               if state["queue"] else None)
        state["adopt_url"] = url  # the guard lets the master load this page only
        L.save_state(run_dir, state)
    emit({"tab": state["home_tab"], "url": url, "jobs": len(state["queue"])})


def site_key(url: Optional[str]) -> Optional[str]:
    """Where a page is: its host. A LinkedIn job page also gets its job id (every LinkedIn job shares the host)."""
    host = L.host_of(url)
    job_id = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)", url or "")
    return f"{host}/{job_id.group(1)}" if host and job_id and host.endswith("linkedin.com") else host


def _segments(url: Optional[str]) -> list[str]:
    return [x for x in urlsplit(url or "").path.split("/") if x]


def _name(text: Optional[str]) -> str:
    """Letters and digits only, without a legal suffix: "Acme, Inc." and "Acme | Careers" both contain "acme"."""
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(inc|llc|ltd|gmbh|ag|corp|plc|pty|limited)\b", "", (text or "").casefold()))


def _fit(job: dict, tab: dict) -> tuple[int, bool]:
    """How well a tab on a host of the job fits it: the leading path segments it shares with a page of the job (on a
    shared ATS host the path holds the company and the job id), then the company name in the tab title."""
    here = _segments(tab["url"])
    shared = max((sum(1 for _ in itertools.takewhile(lambda p: p[0] == p[1], zip(_segments(u), here)))
                  for u in job.get("urls") or [] if site_key(u) == site_key(tab["url"])), default=0)
    company = _name(job.get("company"))
    return shared, bool(company) and company in _name(tab.get("title"))


def _top(scores: dict) -> Any:
    """The key with the one highest score, or None when two share it."""
    ranked = sorted(scores, key=scores.get, reverse=True)
    return ranked[0] if ranked and (len(ranked) == 1 or scores[ranked[0]] > scores[ranked[1]]) else None


def match_tabs(jobs: dict[str, dict], tabs: list[dict]) -> dict[str, int]:
    """/continue-applying: folder -> the id of the tab that holds the job's application.

    First the tab that the last note names, but only when its page fits the job (a host of a page the job used): after
    a Chrome restart that id can be another tab. Then the tabs the candidate added, round by round: a tab is on a host
    of the job, and each is the other's best fit (see _fit). With no better sign, a lone tab on a job's host is that
    job's tab. A tab that two jobs fit equally goes to neither; a match leaves the other jobs and tabs fewer, so the
    next round can settle them."""
    keys = {f: {k for k in map(site_key, j.get("urls") or []) if k} for f, j in jobs.items()}
    by_id = {t["id"]: t for t in tabs}
    named = {f: j["prev_tab"] for f, j in jobs.items()
             if j.get("prev_tab") in by_id and (not keys[f] or site_key(by_id[j["prev_tab"]]["url"]) in keys[f])}
    taken = collections.Counter(named.values())
    out = {f: t for f, t in named.items() if taken[t] == 1}
    while True:
        fit = {(f, t["id"]): _fit(jobs[f], t) for f in jobs if f not in out for t in tabs
               if t["id"] not in out.values() and site_key(t["url"]) in keys[f]}
        new = {}
        for f in {f for f, _ in fit}:
            t = _top({u: s for (g, u), s in fit.items() if g == f})
            if t is not None and _top({g: s for (g, u), s in fit.items() if u == t}) == f:
                new[f] = t
        if not new:
            # last resort, for a job that moved to a host none of its pages name (a new external ATS): the company
            # name in the tab title, when exactly one free tab carries it and that tab carries no other job's name
            loose = {f: [t["id"] for t in tabs if t["id"] not in out.values() and _fit(jobs[f], t)[1]]
                     for f in jobs if f not in out}
            owners = collections.Counter(t for ids in loose.values() for t in ids)
            new = {f: ids[0] for f, ids in loose.items() if len(ids) == 1 and owners[ids[0]] == 1}
            if not new:
                return out
        out.update(new)


def cmd_adopted(args: argparse.Namespace) -> None:
    """After the adopt page and tabs_context_mcp: keep only the jobs whose tab came back (see 'match_tabs'). A job
    without an open tab in the group "Needs Attention" (finished by hand with the tab closed, for example) is left
    alone: it leaves the run, and its folder and row stay as they are for reconcile. A tab that fits no job stays the
    candidate's: the run keeps it open and puts it back in the group "Needs Attention"."""
    run_dir = need_run()
    seen = L.read_json(L.snap_dir(run_dir) / "tabs_context.json", None)
    if seen is None:
        fatal("no tabs_context result is saved; call tabs_context_mcp after the adopt page loaded")
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if state.get("status") != "running" or state.get("mode") != "continue" or "adopt_url" not in state:
            fatal("'adopted' is for an active /continue-applying run, after 'adopt-plan'")
        home = next((t for t in seen if str(t["id"]) == str(state.get("home_tab"))), {})
        # no adopt page (no tab to bring back): nothing to confirm, every job is left alone below
        if state["adopt_url"] and not str(home.get("title") or "").startswith("[AA adopted "):
            state["adopt_retries"] = state.get("adopt_retries", 0) + 1
            L.save_state(run_dir, state)
            if state["adopt_retries"] > 2:
                fatal(f"the helper extension did not confirm the adopt page: reload it in chrome://extensions "
                      f"(version {L.extension_version()} or later), then run /continue-applying again")
            print("RETRY wait 3 seconds, call tabs_context_mcp, then run adopted again")
            return
        # the tabs that came back: all but the home tab and the tabs that were in Claude's group before the run
        before = {int(t) for t in state.get("initial_tabs") or []} | {int(state["home_tab"])}
        back = [t for t in seen if int(t["id"]) not in before]
        found = match_tabs({f: state["jobs"][f] for f in state["queue"]}, back)
        kept = [f for f in state["queue"] if f in found]
        left = [f for f in state["queue"] if f not in found]
        for folder in left:
            del state["jobs"][folder]
        for folder in kept:
            state["jobs"][folder]["prev_tab"] = found[folder]
        state["extra_tabs"] = [int(t["id"]) for t in back if int(t["id"]) not in found.values()]
        state["queue"] = kept
        state["left_alone"] = left
        state["adopted"] = True
        if not kept:
            state["status"] = "done"
        L.save_state(run_dir, state)
    emit({"count": len(kept), "jobs": [{"folder": f, "company": state["jobs"][f]["company"]} for f in kept],
          "left_alone": len(left)})


def assign_continue(run_dir: Path) -> None:
    """Each kept job gets back its own tab (see 'adopted')."""
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if state.get("status") != "running" or not state.get("adopted"):
            fatal("assign --continue is for an active /continue-applying run, after 'adopted'")
        workers = []
        for folder in state["queue"]:
            job = state["jobs"][folder]
            tab = job["prev_tab"]
            if job.get("tab") is not None or L.tab_key(tab) in state["tabs"]:
                continue
            job["tab"] = tab
            state["tabs"][L.tab_key(tab)] = {
                "role": "job", "folder": folder, "agent_id": None,
                # the page may hold a form of the last run: a form page blocks "Apply" (hooks.py Ctx.filled); on a
                # page without a form the Haiku check decides about "Apply" (sticky), and the state survives page changes
                "filled": False, "filled_host": None, "sticky": True,
                "google_clicks": 0, "google_continue": 0,
            }
            brief = write_brief(run_dir, job)
            workers.append({"folder": folder, "description": f"Continue: {job['company']}",
                            "prompt": f"folder={folder} tab={tab} brief={brief}"})
        state["waiting"] = workers
        L.save_state(run_dir, state)
    emit({"queued": len(workers)})


def cmd_assign(args: argparse.Namespace) -> None:
    run_dir = need_run()
    if args.cont:
        assign_continue(run_dir)
        return
    if not args.tabs:
        fatal("give --tabs (or --continue for /continue-applying)")
    try:
        tabs = [int(t) for t in args.tabs.split(",") if t.strip()]
    except ValueError:
        fatal(f"--tabs must be tab ids separated by commas, got: {args.tabs}")
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if state.get("status") != "running":
            fatal(f"the run is not active (status: {state.get('status')})")
        todo = [f for f in state["queue"] if state["jobs"][f].get("tab") is None]
        if len(tabs) != len(todo) or len(set(tabs)) != len(tabs):
            fatal(f"give {len(todo)} different new tab ids, one for each job; got: {args.tabs}")
        for tab in tabs:
            if L.tab_key(tab) in state["tabs"]:
                fatal(f"tab {tab} is already registered ({state['tabs'][L.tab_key(tab)].get('role')}); create a new tab")
        workers = []
        for folder, tab in zip(todo, tabs):
            job = state["jobs"][folder]
            job["tab"] = tab
            state["tabs"][L.tab_key(tab)] = {
                "role": "job",
                "folder": folder,
                "agent_id": None,
                "filled": False,
                "filled_host": None,
                "google_clicks": 0,
                "google_continue": 0,
            }
            brief = write_brief(run_dir, job)
            workers.append({"folder": folder, "description": f"Apply: {job['company']}",
                            "prompt": f"folder={folder} tab={tab} brief={brief}"})
        state["waiting"] = workers
        L.save_state(run_dir, state)
    emit({"queued": len(workers)})


HANG = "UPLOAD_HANG"  # a worker blocker: file_upload hung (Chrome shows a hidden alert, e.g. "File name is too long")


def cmd_retry_plan(args: argparse.Namespace) -> None:
    emit({"workers": retry_workers(need_run())})


def cmd_next(args: argparse.Namespace) -> None:
    """The worker queue: start waiting workers in the free slots (an UPLOAD_HANG job goes back in the queue
    once), else wait at most L.NEXT_SECONDS. Prints "done": true when every worker has reported."""
    run_dir = need_run()
    deadline = time.time() + L.NEXT_SECONDS
    while True:
        retry = retry_workers(run_dir)
        with L.locked(run_dir):
            state = L.load_state(run_dir)
            now = time.time()
            for w in retry:
                state["jobs"][w["folder"]]["started_at"] = None
            # printed but never started (the master dropped it; the guard marks a started one): queue it again
            lost = [j for f, j in state["jobs"].items() if j.get("started_at") and not j.get("spawned")
                    and not j.get("recorded") and now - j["started_at"] > L.LOST_SECONDS
                    and not (L.job_dir(run_dir, f) / "result.json").exists()]
            for j in lost:
                j["started_at"] = None
            waiting = [j["worker"] for j in lost] + (state.get("waiting") or []) + retry
            # the progress watchdog has no wall-clock limit: a slot is free when its worker ended (capture) or made
            # no tool call for idle_minutes (Worker_Limits.json)
            limits = L.worker_limits()
            for folder, job in state["jobs"].items():
                if not job.get("started_at") or job.get("recorded") or job.get("stopped_by") \
                        or (L.job_dir(run_dir, folder) / "result.json").exists():
                    continue
                if job.get("ended_at"):
                    job["stopped_by"] = "ended"
                    L.log(run_dir, "give-up", folder=folder, reason="the worker ended without a report")
                    continue
                last = job.get("last_call_at") or job.get("spawned_at") or job["started_at"]
                if now - last > limits["idle_minutes"] * 60:
                    job["stopped_by"] = "idle"
                    L.log(run_dir, "give-up", folder=folder,
                          reason=f"no tool call for {limits['idle_minutes']} minutes")
            running = [f for f, j in state["jobs"].items() if j.get("started_at") and not j.get("recorded")
                       and not j.get("ended_at") and j.get("stopped_by") not in ("idle", "ended")
                       and not (L.job_dir(run_dir, f) / "result.json").exists()]
            free = max(0, L.SLOTS - len(running))
            take, state["waiting"] = waiting[:free], waiting[free:]
            for w in take:
                job = state["jobs"][w["folder"]]
                job.update({"started_at": now, "spawned": False, "worker": w,
                            "brief_agent": None})  # a new worker claims the brief
                L.reset_worker_slot(job)  # nothing of the last worker counts for this one (4.7 defect 2)
            L.save_state(run_dir, state)
        if take:
            return emit({"workers": take})
        if not running and not state["waiting"]:
            return emit({"done": True})
        if time.time() > deadline:
            return emit({"workers": [], "running": len(running)})
        time.sleep(3)


def retry_workers(run_dir: Path) -> list:
    """For each job whose worker reported UPLOAD_HANG, shorten the resume name (<prefix>_<Company>.pdf, and the yaml of the same name,
    in the job folder) and return a new worker for the same tab. Once for each job."""
    workers = []
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        for folder, job in state["jobs"].items():
            result_path = L.job_dir(run_dir, folder) / "result.json"
            result = L.read_json(result_path, None)
            if job.get("renamed") or job.get("recorded") or job.get("tab") is None or not isinstance(result, dict) \
                    or not str(result.get("blocker") or "").startswith(HANG):
                continue
            pdf = Path(job["pdf"])
            short = f"{W.file_prefix()}_" + re.sub(r"^\d+_", "", folder).split("_")[0]
            if pdf.stem == short:
                continue  # the name is short already: not the cause, the job stays blocked
            olds = [pdf, *(y for y in L.tailored_yamls(pdf.parent) if y.stem == pdf.stem)]
            clash = [o for o in olds if o.with_name(short + o.suffix).exists()]
            if clash:
                # renaming would overwrite another resume of this job: leave both files and the job blocked
                job["error"] = (f"cannot shorten the resume name: {clash[0].with_name(short + clash[0].suffix).name} "
                                f"exists already in the job folder")
                L.log(run_dir, "retry-skip", folder=folder, reason=job["error"])
                continue
            for old in olds:
                old.rename(old.with_name(short + old.suffix))
            job.update({"pdf": str(pdf.with_name(short + ".pdf")), "renamed": True, "capture_blocks": 0})
            L.reset_worker_slot(job)  # the second worker of this job starts clean (4.7 defect 2)
            result_path.unlink()
            state["tabs"][L.tab_key(job["tab"])].update(
                {"agent_id": None, "started": False, "google_clicks": 0,
                 "google_continue": 0})
            brief = write_brief(run_dir, job)
            workers.append({"folder": folder, "description": f"Apply again: {job['company']}",
                            "prompt": f"folder={folder} tab={job['tab']} brief={brief}"})
        L.save_state(run_dir, state)
    return workers


def cmd_finish(args: argparse.Namespace) -> None:
    """Check and record every assigned job with one tracker load and one save, then print the summary.

    A job that cannot be recorded (for example: its destination folder exists already) stays in
    Applications/ at "Resume Built" and is listed; the other jobs are recorded."""
    run_dir = need_run()
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        todo = [f for f, j in state["jobs"].items() if j.get("tab") is not None and not j.get("recorded")]
        results = {}
        for folder in todo:
            job = state["jobs"][folder]
            entry = state["tabs"].get(L.tab_key(job["tab"]))
            if entry is not None:
                entry["agent_id"] = None  # the worker has returned: release the tab
            results[folder] = L.read_json(L.job_dir(run_dir, folder) / "result.json", None)
            job["verdict"], job["reasons"] = L.check_result(run_dir, state, job, results[folder])
            job.pop("error", None)
        # the sign-in lease of a worker that has returned is free again (4.8)
        returned = {L.tab_key(state["jobs"][f]["tab"]) for f in todo if state["jobs"][f].get("tab") is not None}
        for host, lease in list((state.get("signin_hosts") or {}).items()):
            if isinstance(lease, dict) and lease.get("status") == "pending" and lease.get("holder") in returned:
                del state["signin_hosts"][host]
        stop = None
        if todo:
            stop = record_all(state, todo, results)
            for folder in todo:
                job = state["jobs"][folder]
                if job.get("recorded") and isinstance(results.get(folder), dict):
                    add_questions(results[folder].get("questions") or [], f"{job['company']} | {job['title']}")
        state["status"] = "done"
        state["ended"] = L.now_iso()
        L.save_state(run_dir, state)
    print(summary(state))
    if stop:  # the tracker could not be read or saved: nothing was recorded; running 'finish' again retries
        fatal(f"{stop} Close the tracker if it is open elsewhere, then run 'finish' again.")


def record_all(state: dict, folders: list[str], results: dict) -> Optional[str]:
    """Record the jobs. Returns the tracker's own error text when the tracker could not be loaded or saved
    (every job is then left as it was); None otherwise."""
    try:
        tracker = Tracker(L.TRACKER)
    except TrackerError as exc:
        for folder in folders:
            state["jobs"][folder]["error"] = str(exc)
        return str(exc)
    done = []  # (folder, src, dest, original job.md)
    for folder in folders:
        try:
            done.append(record_one(state, tracker, folder, results.get(folder)))
        except JobError as exc:
            state["jobs"][folder]["error"] = str(exc)
    if not done:
        # No job was recorded; return an error so the caller can handle it
        return "no job was recorded; all folders had errors"
    try:
        tracker.save()
    except Exception as exc:  # noqa: BLE001  (a TrackerError: stale or locked workbook; nothing was saved)
        for folder, src, dest, original_md in reversed(done):
            if dest != src:
                shutil.move(str(dest), str(src))
            (src / "job.md").write_text(original_md, encoding="utf-8")
            state["jobs"][folder]["error"] = f"could not save the tracker ({exc}); folder moved back, job.md restored"
        return f"could not save the tracker: {exc}"
    for folder, src, dest, _orig in done:
        job = state["jobs"][folder]
        if job["verdict"] == "CLOSED":
            shutil.rmtree(src, ignore_errors=True)
            job["dest"] = "Deleted"
        else:
            job["dest"] = dest.parent.name
        job["recorded"] = True


def record_one(state: dict, tracker: Tracker, folder: str, result: Optional[dict]) -> tuple:
    """job.md note, folder move, tracker row (in memory: record_all saves the tracker once)."""
    job = state["jobs"][folder]
    verdict = job["verdict"]
    reasons = job.get("reasons") or []
    closed = verdict == "CLOSED"  # closed job: tracker row "Deleted", folder removed by record_all after the tracker saved
    dest_root = L.PENDING_REVIEW if verdict == "PASS" else L.NEEDS_ATTENTION
    status_text = "Pending Review" if verdict == "PASS" else "Deleted" if closed else "Needs Attention"
    src = job_folder(job)
    dest = src if closed else dest_root / folder
    if not src.is_dir():
        raise JobError(f"folder not found: {src}")
    if dest.exists() and dest != src:
        raise JobError(f"destination already exists, nothing moved: {dest}")
    key = L.folder_id(folder)
    rows = tracker.rows_for_key(key) if key else []
    if len(rows) > 1:
        # never move a folder without its tracker row: fix the tracker first
        raise JobError(f"more than one tracker row has job id {key}; nothing was changed")
    job_md_path = src / "job.md"
    original_md = job_md_path.read_text(encoding="utf-8")
    overview = L.job_overview(original_md)
    if not closed:
        job_md_path.write_text(insert_note(original_md, note_block(job, result, verdict, reasons)), encoding="utf-8")
    dest_root.mkdir(parents=True, exist_ok=True)
    try:
        if dest != src:  # a /continue-applying job that is still blocked stays in Needs-Attention/
            shutil.move(str(src), str(dest))
    except Exception as exc:  # noqa: BLE001
        job_md_path.write_text(original_md, encoding="utf-8")
        raise JobError(f"could not move the folder, job.md restored: {exc}") from None
    note_line = tracker_note(verdict, result, reasons)
    try:
        if rows:
            tracker.set_status(rows[0]["_index"], status_text, note_line)
        else:
            score = overview.get("Match Score")
            try:
                score_value: Any = float(score) if score not in (None, "") else None
            except ValueError:
                score_value = score
            tracker.add_row(
                {
                    "Job URL": overview.get("Job URL") or job.get("url"),
                    "Status": status_text,
                    "Company": overview.get("Company") or job.get("company"),
                    "Job Title": overview.get("Job Title") or job.get("title"),
                    "Date Found": overview.get("Date Found"),
                    "Location": overview.get("Location"),
                    "Source": overview.get("Source"),
                    "Match Score": score_value,
                    "Notes": note_line,
                }
            )
    except Exception as exc:  # noqa: BLE001
        if dest != src:
            shutil.move(str(dest), str(src))
        (src / "job.md").write_text(original_md, encoding="utf-8")
        raise JobError(f"could not update the tracker row, folder moved back, job.md restored: {exc}") from None
    return folder, src, dest, original_md


GROUP_TITLES = {"Pending-Review": "Pending Review", "Needs-Attention": "Needs Attention", "Deleted": "Deleted"}


def cmd_group_plan(args: argparse.Namespace) -> None:
    """After 'finish': the page for the master's home tab. The helper extension reads the plan in its address and
    puts each recorded job's tab in the group of the folder the job moved to (tabs of this run only)."""
    run_dir = need_run()
    with L.locked(run_dir):
        state = L.load_state(run_dir)
        if state.get("status") != "done":
            fatal("run 'finish' first")
        groups: dict[str, list[int]] = {}
        for job in state["jobs"].values():
            title = GROUP_TITLES.get(job.get("dest") or "")
            if title and job.get("recorded") and job.get("tab") is not None:
                groups.setdefault(title, []).append(int(job["tab"]))
        for tab in state.get("extra_tabs") or []:  # a tab that fits no job: back where the candidate had it
            groups.setdefault("Needs Attention", []).append(int(tab))
        url = None
        if groups and state.get("home_tab") is not None:
            # the query makes it a new page: in /continue-applying the home tab is on robots.txt?aa-adopt#aa-adopt=..., and a
            # change after '#' alone does not load the page again, so the extension would never see it
            url = "https://www.linkedin.com/robots.txt?aa-groups#aa-groups=" + quote(json.dumps(groups, separators=(",", ":")), safe="")
        state["groups_planned"] = {k: len(v) for k, v in groups.items()} if url else {}
        state["group_planned_at"] = time.time()
        state["group_url"] = url  # the guard lets the master load this page only
        L.save_state(run_dir, state)
    emit({"tab": state.get("home_tab"), "url": url, "groups": state["groups_planned"]})


def grouped_note(run_dir: Path, state: dict) -> Optional[str]:
    """WARN line when the tab groups were planned but the helper extension did not confirm them in the home tab's title."""
    if not state.get("groups_planned"):
        return None
    tabs = L.read_json(L.snap_dir(run_dir) / "tabs_context.json", None) or []
    title = next((t.get("title") or "" for t in tabs if str(t.get("id")) == str(state.get("home_tab"))), "")
    planned = sum(state["groups_planned"].values())
    match = re.match(r"^\[AA grouped (\d+)\]", title)
    if not match:
        return ("WARN the job tabs were not put in the groups 'Pending Review' and 'Needs Attention': reload the helper "
                "extension in chrome://extensions (it needs the tabGroups permission)")
    if int(match.group(1)) < planned:
        return f"WARN only {match.group(1)} of {planned} job tabs went to their groups (a tab was closed or is in another window)"
    return None


# --------------------------------------------------------------------------- follow-up questions
# A worker never guesses a fact about the candidate: it stops and lists the question (report key "questions"). The
# questions wait in questions.json. At the end of /apply-jobs and /continue-applying, the main session asks
# the candidate ('questions') and saves the answers in Profile.md ('answer'), where every worker reads them next time.

QUESTIONS_FILE = W.QUESTIONS_FILE
FOLLOW_UP = "Follow Up Questions"
ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")  # a list item: "1. ", "1) " or (old files) "- "
MAX_QUESTION = 300  # characters: a question is copied from a web page, so it stays one short line


def question_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w+#]+", " ", text.casefold()).split())


def question_part(line: str) -> str:
    """The question of a "Follow Up Questions" line: up to its first "?" or ":" (the answer follows)."""
    match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s*(.*?[?:？：])", line)
    return match.group(1) if match else ""


def answered_questions() -> set[str]:
    body = L.md_section_body(L.PROFILE.read_text(encoding="utf-8"), FOLLOW_UP, 1) or ""
    return {question_key(question_part(line)) for line in body.splitlines() if question_part(line)}


def pending_questions() -> list[dict]:
    return L.read_json(QUESTIONS_FILE, [])


def add_questions(questions: list, job: str) -> None:
    """Add new questions (not waiting yet, not answered in Profile.md yet) with the job that asked them."""
    waiting = pending_questions()
    known = {question_key(q["q"]): q for q in waiting}
    answered = answered_questions()
    changed = False
    for text in questions:
        text = " ".join(str(text).split())[:MAX_QUESTION]
        key = question_key(text)
        if not key or question_key(question_part(f"- {text}") or text) in answered:
            continue
        if key in known:
            if job not in known[key]["jobs"]:
                known[key]["jobs"].append(job)
                changed = True
            continue
        item = {"q": text, "jobs": [job], "asked": L.today()}
        waiting.append(item)
        known[key] = item
        changed = True
    if changed:
        L.write_json(QUESTIONS_FILE, waiting)


def save_answers(pairs: list[tuple[str, str]]) -> None:
    """Add "<n>. <question> <answer>" lines (n continues the numbering) at the end of the list in the
    "Follow Up Questions" section of Profile.md (the section is created at the end of the file if it is missing). Nothing else in Profile.md changes."""
    text = L.PROFILE.read_text(encoding="utf-8")
    texts = [f"{q} {a}" if question_part(f"- {q}") else f"{q}: {a}" for q, a in pairs]
    new = [f"{k}. {t}" for k, t in enumerate(texts, 1)]  # the section is missing: numbering starts at 1
    body = text.rstrip("\n")
    tail = text[len(body):] or "\n"  # the file's own blank lines at the end stay
    lines = body.splitlines()
    for level, title, start, end in L.md_sections(body):
        if level == 1 and title.strip().lower() == FOLLOW_UP.lower():
            # after the last list item of the section's own text (before any subsection)
            stop = next((i for i in range(start + 1, end) if lines[i].startswith("#")), end)
            items = [i for i in range(start + 1, stop) if ITEM_RE.match(lines[i])]
            number = max([int(m.group(1)) for i in items if (m := re.match(r"^\s*(\d+)[.)]", lines[i]))] or [0])
            new = [f"{number + k}. {t}" for k, t in enumerate(texts, 1)]
            at = items[-1] + 1 if items else start + 1
            while items and at < stop and lines[at].startswith((" ", "\t")) and lines[at].strip():
                at += 1  # the indented continuation lines of the last item
            lines[at:at] = new if items else ["", *new]
            break
    else:
        lines += ["", f"# {FOLLOW_UP}", "", *new]
    L.write_text(L.PROFILE, "\n".join(lines) + tail)


def cmd_questions(args: argparse.Namespace) -> None:
    emit({"questions": [{"q": q["q"], "jobs": q["jobs"]} for q in pending_questions()]})


def cmd_answer(args: argparse.Namespace) -> None:
    """Save the candidate's answers: [{"q": "<question exactly as 'questions' printed it>", "a": "<the answer>"}, ...].
    An empty answer keeps the question waiting."""
    try:
        data = json.loads(args.json or "")
    except json.JSONDecodeError as exc:
        print(f"RETRY: the text is not valid JSON ({exc})")
        return
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        print('RETRY: give a JSON list like [{"q": "...", "a": "..."}]')
        return
    waiting = pending_questions()
    by_key = {question_key(q["q"]): q for q in waiting}
    unknown = [str(x.get("q")) for x in data if question_key(str(x.get("q") or "")) not in by_key]
    if unknown:
        print("RETRY: not a waiting question (copy it exactly from 'questions'): " + "; ".join(unknown[:5]))
        return
    pairs, jobs = [], []
    for x in data:
        answer = " ".join(str(x.get("a") or "").split())
        item = by_key.pop(question_key(str(x["q"])), None) if answer else None
        if item is None:
            continue  # skipped, or the same question twice
        pairs.append((item["q"], answer))
        jobs += [j for j in item["jobs"] if j not in jobs]
    if pairs:
        save_answers(pairs)
        L.write_json(QUESTIONS_FILE, list(by_key.values()))
    still = {j for q in by_key.values() for j in q["jobs"]}  # a job with another waiting question cannot go on yet
    emit({"saved": len(pairs), "still_waiting": len(by_key), "jobs_that_can_continue": [j for j in jobs if j not in still]})


def cmd_end(args: argparse.Namespace) -> None:
    run_dir = L.current_run_dir()
    if run_dir is not None:
        master = L.read_json(run_dir / "master.json", {})
        judge: dict = {}
        log = run_dir / "judge_usage.jsonl"
        for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
            try:  # a damaged line must not stop 'end': the run folder holds copies of personal data
                entry = json.loads(line)
                usage = entry["usage"]
                counts = {k: usage.get(k) or 0 for k in L.USAGE_KEYS}
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                continue
            row = judge.setdefault(("Haiku check", entry.get("model") or "?"), collections.Counter())
            row["calls"] += 1
            row.update(counts)
        table = L.usage_table(master.get("transcript"), master.get("agent_id"), judge)
        print(table)
        L.save_report(table)
        state = L.load_state(run_dir)
        note = grouped_note(run_dir, state)
        if note:
            print(note)
        # the ledger and the traces are written BEFORE the run folder goes (it holds the reports and the pages)
        try:
            lines = ledger.write_ledger_and_traces(run_dir, state, master.get("transcript"), master.get("agent_id"))
            print(f"Ledger: {lines} lines in {ledger.LEDGER_PATH}")
            hint = ledger.dream_hint()
            if hint:
                print(hint)
        except Exception as exc:  # noqa: BLE001  (nothing about the ledger may stop 'end')
            print(f"WARN the ledger was not written ({exc})")
    delete_run_folders()
    print("RUN FOLDER DELETED")


def cmd_cleanup_plan(args: argparse.Namespace) -> None:
    run_dir = need_run()
    with L.locked(run_dir):
        state = L.load_state(run_dir)
    snap = L.snap_dir(run_dir) / "tabs_context.json"
    seen = L.read_json(snap, None)
    if seen is None:
        fatal("no tabs_context result is saved; call tabs_context_mcp first")
    if state.get("group_planned_at") and snap.stat().st_mtime < state["group_planned_at"]:
        print("RETRY call tabs_context_mcp (after the group page loaded), then run cleanup-plan again")
        return
    home_title = next((t.get("title") or "" for t in seen if str(t.get("id")) == str(state.get("home_tab"))), "")
    if state.get("groups_planned") and not home_title.startswith("[AA grouped ") \
            and retry_for_title(run_dir, "cleanup_retries", "cleanup-plan"):
        return
    initial = {str(t) for t in (state.get("initial_tabs") or [])}
    fixed = {str(j["prev_tab"]) for j in state["jobs"].values() if j.get("prev_tab") is not None}  # tabs fixed by hand
    fixed |= {str(t) for t in (state.get("extra_tabs") or [])}  # tabs of the candidate's own: never ours to close
    close, keep = [], []
    for tab in seen:
        tid = str(tab["id"])
        entry = state["tabs"].get(tid, {})
        if entry.get("role") == "job" or tid in fixed:
            keep.append(int(tid))
        elif entry.get("role") in ("home", "signin"):
            close.append(int(tid))  # the sign-in check tab, and sign-in pop-ups that stayed open
        elif tid in initial and not L.BLANK_URL_RE.match(tab.get("url") or "") and not L.HELPER_ON_RE.match(tab.get("title") or ""):
            keep.append(int(tid))  # open before the run started: not ours to close (the home tab of a stopped run is)
        else:
            close.append(int(tid))
    emit({"close": close, "keep": keep})


def summary(state: dict) -> str:
    jobs = list(state.get("jobs", {}).values())
    pending = [j for j in jobs if j.get("dest") == "Pending-Review"]
    needs = [j for j in jobs if j.get("dest") == "Needs-Attention"]
    deleted = [j for j in jobs if j.get("dest") == "Deleted"]
    not_done = [j for j in jobs if not j.get("recorded")]
    lines = [
        f"Run {state.get('run_id')} | status: {state.get('status')}",
        f"Pending-Review: {len(pending)} | Needs-Attention: {len(needs)} | Deleted (closed): {len(deleted)} | not finished: {len(not_done)}",
    ]
    for j in pending:
        lines.append(f"- PARKED {j['folder']} (tab {j.get('tab')})")
    for j in deleted:
        lines.append(f"- DELETED (closed) {j['folder']} (tab {j.get('tab')})")
    for j in needs:
        lines.append(f"- NEEDS ATTENTION {j['folder']} (tab {j.get('tab')}): {'; '.join(j.get('reasons') or [])[:200]}")
    for j in not_done:
        if j.get("tab") is None:
            lines.append(f"- NOT STARTED {j['folder']}" + (f": {j['skip_reason']}" if j.get("skip_reason") else ""))
        else:
            where = "Needs-Attention/ at Needs Attention" if j.get("continue") else "Applications/ at Resume Built"
            lines.append(f"- NOT RECORDED {j['folder']} (still in {where}): {j.get('error') or 'unknown error'}")
    if state.get("extra_tabs"):
        lines.append(f"Extra tabs: {len(state['extra_tabs'])} open tabs fitted no job; they stay open and go back to the "
                     "group 'Needs Attention'")
    if state.get("left_alone"):
        lines.append(f"Left alone: {len(state['left_alone'])} jobs in Needs-Attention/ without an open tab in the group "
                     "'Needs Attention' (finished by hand or closed; reconcile handles them)")
    for text in state.get("closed_jobs") or []:
        lines.append(f"- DELETED (closed, found by the early liveness check, no tab opened) {text}")
    for s in state.get("skipped") or []:
        lines.append(f"- SKIPPED {s.get('folder') or s.get('job')}: {s.get('reason')}")
    pre = state.get("preflight") or {}
    home_title = (state.get("tab_titles") or {}).get(str(state.get("home_tab"))) or ""
    if pre and not pre.get("helper") and not L.HELPER_ON_RE.match(home_title):  # the tag can come after the preflight
        lines.append("WARN the pop-up helper extension was off: load the ohmyjob chrome-extension folder in chrome://extensions "
                     "(/ohmyjob:setup prints its path)")
    if pre and not pre.get("judge"):
        lines.append(f"WARN the Haiku check was not available ({pre.get('judge_note')}): unknown button names were blocked")
    for j in jobs:
        if j.get("judge_error"):
            lines.append(f"WARN Haiku check failed for {j['folder']}: {j['judge_error']}")
    waiting = pending_questions()
    if waiting:
        lines.append(f"Questions for the candidate: {len(waiting)} waiting (ohmyjob apply questions); the jobs that asked them "
                     "are in Needs Attention until they are answered")
    open_tabs = [j.get("tab") for j in jobs if j.get("tab") is not None]
    lines.append(f"Job tabs left open for review: {len(open_tabs)}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="aa")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("--continue", dest="cont", action="store_true")
    p.add_argument("--selected", default=None, help='job folder names, comma separated: "Acme, Lumenfield"')
    p.add_argument("--top-k-match", dest="top_k", default=None, help="queue only the N jobs with the best Match Score")
    p = sub.add_parser("preflight")
    p.add_argument("--tab", type=int, required=True)
    p.add_argument("--home-only", action="store_true")
    p = sub.add_parser("assign")
    p.add_argument("--tabs")
    p.add_argument("--continue", dest="cont", action="store_true")
    sub.add_parser("adopt-plan")
    sub.add_parser("adopted")
    sub.add_parser("questions")
    p = sub.add_parser("answer")
    p.add_argument("--json", required=True)
    sub.add_parser("next")
    sub.add_parser("retry-plan")
    sub.add_parser("finish")
    sub.add_parser("group-plan")
    sub.add_parser("cleanup-plan")
    sub.add_parser("end")
    args = parser.parse_args(argv)
    try:
        dispatch(args)
    except W.WorkspaceError as exc:  # includes ConfigError: a missing workspace or a bad ohmyjob.json
        fatal(str(exc))


def dispatch(args: argparse.Namespace) -> None:
    {
        "start": cmd_start,
        "preflight": cmd_preflight,
        "assign": cmd_assign,
        "next": cmd_next,
        "retry-plan": cmd_retry_plan,
        "finish": cmd_finish,
        "group-plan": cmd_group_plan,
        "adopt-plan": cmd_adopt_plan,
        "adopted": cmd_adopted,
        "questions": cmd_questions,
        "answer": cmd_answer,
        "cleanup-plan": cmd_cleanup_plan,
        "end": cmd_end,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
