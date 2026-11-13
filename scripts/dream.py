"""`ohmyjob dream ...`: turn the traces of the last runs into ATS notes (spec 4.11).

One Opus master (`ohmyjob:dream-master`) runs the commands; one Sonnet worker per ATS key
(`ohmyjob:dream-host-worker`) reads the evidence of that key and writes its note.

    ohmyjob dream start    group the new ledger lines by ATS key, write one bundle per key, back the notes up
    ohmyjob dream next     the workers to start next (one per key, at most SLOTS at a time)
    ohmyjob dream finish   check the notes the workers changed, write Dream_Report.md
    ohmyjob dream end      delete the run folder, move the watermark, print the summary
    ohmyjob dream reject   put the notes, the report and the watermark back as they were before the last dream

The notes live in the workspace (`ohmyjob/ats/`), never in the plugin: `start` copies a plugin note there before a
worker may change it. Nothing here uses git; `start` keeps one backup under `.ohmyjob/dream/backup/`, and `reject`
restores it.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import fcntl
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

import aa_lib as L
import ledger
import workspace as W

DREAM_DIR = W.STATE_DIR / "dream"          # state.json (the watermark) and the backup of the last dream
STATE_PATH = DREAM_DIR / "state.json"
BACKUP_DIR = DREAM_DIR / "backup"
RUN_BASE = W.DREAM_DIR                     # .ohmyjob/run/dream: the bundles and the results of the running dream
CURRENT = RUN_BASE / "CURRENT"
REPORT_PATH = W.ROOT / "Dream_Report.md"

NOTE_MAX_LINES = 40
WORKER_SECONDS = 20 * 60   # a dream worker that runs longer is given up on, so 'next' can end
DREAM_BINS = ("wall", "costly_win", "first_win", "harness")
KEY_RE = re.compile(r"[^a-z0-9.-]+")


def fatal(message: str) -> None:
    print(f"FATAL: {message}")
    sys.exit(3)


def safe_key(key: str) -> str:
    """An ATS key that is safe as a file name: lowercase letters, digits, dots and dashes."""
    cleaned = KEY_RE.sub("-", (key or "").strip().lower()).strip("-.")
    return cleaned or "unknown"


@contextlib.contextmanager
def locked():
    """One reader-changer-writer at a time for the dream state (the workers report in parallel)."""
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    with open(DREAM_DIR / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def dream_state() -> dict:
    try:
        state = L.read_json(STATE_PATH, {})
    except L.DataError:
        return {}
    return state if isinstance(state, dict) else {}


def run_dir() -> Optional[Path]:
    if not CURRENT.exists():
        return None
    path = RUN_BASE / CURRENT.read_text(encoding="utf-8").strip()
    return path if path.is_dir() else None


def need_run() -> Path:
    path = run_dir()
    if path is None:
        fatal("no dream is running; run 'ohmyjob dream start' first")
    return path


def run_state(path: Path) -> dict:
    state = L.read_json(path / "state.json", {})
    return state if isinstance(state, dict) else {}


# --------------------------------------------------------------------------- the notes and their backup


def note_path(key: str) -> Path:
    return W.USER_ATS_DIR / f"{safe_key(key)}.md"


def _backup_notes() -> None:
    """One backup of everything a dream may change: the workspace notes, the report and the watermark."""
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    (BACKUP_DIR / "ats").mkdir(parents=True)
    if W.USER_ATS_DIR.is_dir():
        for path in W.USER_ATS_DIR.glob("*.md"):
            shutil.copy2(path, BACKUP_DIR / "ats" / path.name)
    if REPORT_PATH.exists():
        shutil.copy2(REPORT_PATH, BACKUP_DIR / "Dream_Report.md")
    L.write_json(BACKUP_DIR / "state.json", {"watermark": dream_state().get("watermark", 0)})


def _restore_note(key: str) -> None:
    """Put one note back as the backup has it; a note the dream created is deleted."""
    saved = BACKUP_DIR / "ats" / f"{safe_key(key)}.md"
    target = note_path(key)
    if saved.exists():
        shutil.copy2(saved, target)
    else:
        target.unlink(missing_ok=True)


# --------------------------------------------------------------------------- start


def cmd_start(args: argparse.Namespace) -> None:
    lines = ledger.read_ledger()
    watermark = dream_state().get("watermark", 0)
    watermark = watermark if isinstance(watermark, int) and 0 <= watermark <= len(lines) else 0
    new = [line for line in lines[watermark:] if line.get("bin") in DREAM_BINS]
    groups: dict[str, list[dict]] = {}
    no_host = 0
    for line in new:
        key = line.get("ats") or ""
        if not key:
            no_host += 1  # a line without a host teaches nothing about an ATS: the report counts it
            continue
        groups.setdefault(safe_key(key), []).append(line)
    if not groups:
        print(json.dumps({"count": 0, "groups": [], "no_host": no_host}))
        return

    W.USER_ATS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{L.today()}_dream"
    path = RUN_BASE / run_id
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    L.write_text(CURRENT, run_id)

    state = {"watermark_start": watermark, "watermark_end": len(lines), "status": "running",
             "no_host": no_host, "groups": {}}
    out = []
    for key, key_lines in sorted(groups.items()):
        bundle_dir = path / key
        bundle_dir.mkdir(parents=True, exist_ok=True)
        traces = []
        for line in key_lines:
            name = line.get("trace")
            if not name:
                continue
            try:
                traces.append(json.loads((ledger.TRACES_DIR / name).read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        bundle = bundle_dir / f"{key}.json"
        L.write_json(bundle, {"ats": key, "ledger_lines": key_lines, "traces": traces})
        note = note_path(key)
        if not note.exists():  # only the plugin has this note: the worker changes the workspace's copy of it
            plugin_note = W.ATS_DIR / f"{key}.md"
            if plugin_note.exists():
                shutil.copy2(plugin_note, note)
        state["groups"][key] = {"note": str(note), "bundle": str(bundle), "done": False}
        out.append({"ats": key, "note": str(note), "bundle": str(bundle)})
    # the backup is taken AFTER the plugin notes are copied in: putting a note back then gives the plugin's text,
    # and only a note that this dream really created is removed
    _backup_notes()
    L.write_json(path / "state.json", state)
    print(json.dumps({"count": len(out), "groups": out, "no_host": no_host}))


# --------------------------------------------------------------------------- next


def cmd_next(args: argparse.Namespace) -> None:
    path = need_run()
    with locked():
        state = run_state(path)
        groups = state.get("groups") or {}
        now = time.time()
        for key, group in groups.items():
            if group.get("done") or not group.get("spawned"):
                continue
            if Path(group["bundle"]).with_name(f"{key}.result.json").exists():
                group["done"] = True  # the report is in, even if the hook could not mark it
                continue
            if now - float(group.get("spawned_at") or now) > WORKER_SECONDS:
                # the worker never reported: give up on it, so this dream can end
                group.update({"done": True, "error": f"no report in {WORKER_SECONDS // 60} minutes"})
        running = sum(1 for g in groups.values() if g.get("spawned") and not g.get("done"))
        workers = []
        for key, group in sorted(groups.items()):
            if group.get("done") or group.get("spawned") or running >= L.SLOTS:
                continue
            group.update({"spawned": True, "spawned_at": now})
            running += 1
            workers.append({"description": f"Dream: {key}",
                            "prompt": f"ats={key} note={group['note']} bundle={group['bundle']}"})
        L.write_json(path / "state.json", state)
    print(json.dumps({"done": all(g.get("done") for g in groups.values()), "workers": workers}))


# --------------------------------------------------------------------------- finish


def note_problems(path: Path) -> list[str]:
    """What is wrong with a note a worker wrote: 'finish' puts such a note back."""
    if not path.exists():
        return ["the note file is gone"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"the note cannot be read ({exc})"]
    lines = text.splitlines()
    problems = []
    if len(lines) > NOTE_MAX_LINES:
        problems.append(f"{len(lines)} lines, at most {NOTE_MAX_LINES}")
    headings = [line for line in lines if line.startswith("# ")]
    if len(headings) != 1:
        problems.append(f"{len(headings)} H1 headings, exactly one is allowed")
    if not any(L.HOSTS_LINE_RE.match(line.strip()) for line in lines):
        problems.append("no hosts: line")
    if L.EMAIL_RE.search(text):
        problems.append("an email address")
    for value in ledger._profile_values():
        if value and value in text:
            problems.append("a value of the Profile.md header")
            break
    return problems


def _median(values: list) -> Optional[float]:
    return statistics.median(values) if values else None


def ats_metrics(lines: list[dict], key: str, note: Path) -> dict:
    """What the ledger says about one ATS key, and how old its note is (its file date, no git here)."""
    mine = [line for line in lines if safe_key(line.get("ats") or "") == key]
    counts = {name: sum(1 for line in mine if line.get("outcome") == name)
              for name in ("parked", "handoff", "unverified_handoff", "wall", "harness")}
    denominator = counts["parked"] + counts["wall"]
    parked = [line for line in mine if line.get("outcome") == "parked"]
    handoffs = [line for line in mine if line.get("outcome") in ("handoff", "unverified_handoff")]
    # every line's tokens are a dict: sum each one before the median (a list of dicts raises TypeError)
    note_date = _dt.date.fromtimestamp(note.stat().st_mtime).isoformat() if note.exists() else None
    return {
        "attempts": len(mine), **counts,
        "wall_rate": f"{counts['wall'] / denominator:.0%}" if denominator else "n/a",
        "median_turns_parked": _median([line["turns"] for line in parked if line.get("turns") is not None]),
        "median_tokens_parked": _median([sum((line["tokens"] or {}).values()) for line in parked if line.get("tokens")]),
        "median_tokens_handoff": _median([sum((line["tokens"] or {}).values()) for line in handoffs if line.get("tokens")]),
        "note_date": note_date,
        "before": sum(1 for line in mine if note_date and (line.get("date") or "") < note_date),
        "after": sum(1 for line in mine if not note_date or (line.get("date") or "") >= note_date),
    }


def _report(state: dict, results: dict, rejected: list[dict], lines: list[dict]) -> str:
    out = [f"# Dream Report — {L.today()}", "", f"watermark: {state.get('watermark_end', len(lines))}"]
    traces = sum(len((L.read_json(Path(group["bundle"]), {}) or {}).get("traces") or [])
                 for group in (state.get("groups") or {}).values())
    out += [f"traces: {traces}"]
    if state.get("no_host"):
        out += [f"lines without a host (no note written): {state['no_host']}"]
    out += ["", "## Per ATS", ""]
    for key in sorted(state.get("groups") or {}):
        metrics = ats_metrics(lines, key, Path(state["groups"][key]["note"]))
        out.append(f"### {key}")
        out.append(f"- attempts: {metrics['attempts']} (parked {metrics['parked']}, handoffs {metrics['handoff']}, "
                   f"unverified {metrics['unverified_handoff']}, walls {metrics['wall']}, harness {metrics['harness']})")
        out.append(f"- wall rate: {metrics['wall_rate']}")
        for label, name in (("median turns (parked)", "median_turns_parked"),
                            ("median tokens (parked)", "median_tokens_parked"),
                            ("median tokens (handoff)", "median_tokens_handoff")):
            if metrics[name] is not None:
                out.append(f"- {label}: {metrics[name]:.0f}")
        if metrics["note_date"]:
            out.append(f"- note last changed: {metrics['note_date']} "
                       f"(before: {metrics['before']}, after: {metrics['after']})")
        error = (state["groups"][key] or {}).get("error")
        if error:
            out.append(f"- WARN the worker of this key gave no report: {error}")
        out.append("")
    out += ["## Lessons", ""]
    for key in sorted(results):
        result = results[key] or {}
        added, removed = result.get("lessons_added") or [], result.get("lessons_removed") or []
        if not added and not removed:
            continue
        out.append(f"### {key}")
        for lesson in added:
            out.append(f"- **added:** {lesson.get('text', '')} [{', '.join(lesson.get('evidence') or [])}]")
        for lesson in removed:
            out.append(f"- **removed:** {lesson.get('text', '')}")
        out.append("")
    proposals = [{**item, "ats": key} for key in sorted(results)
                 for item in ((results[key] or {}).get("proposals") or [])]
    if proposals:
        out += ["## Proposals", ""]
        for item in proposals:
            out.append(f"- [{item.get('ats', '')}] {item.get('kind', 'other')}: {item.get('text', '')} "
                       f"[{', '.join(item.get('evidence') or [])}]")
        out.append("")
    bugs = [{"text": f"harness: {line.get('folder', '')} — {(line.get('blocker') or '')[:100]}",
             "evidence": [line.get("id", "")]}
            for line in lines[state.get("watermark_start", 0):state.get("watermark_end", len(lines))]
            if line.get("bin") == "harness"]
    bugs += [{**bug, "ats": key} for key in sorted(results) for bug in ((results[key] or {}).get("bugs") or [])]
    if bugs:
        out += ["## Bugs", ""]
        for bug in bugs:
            out.append(f"- {bug.get('text', '')} [{', '.join(bug.get('evidence') or [])}]")
        out.append("")
    if rejected:
        out += ["## Notes that were put back", ""]
        for item in rejected:
            out.append(f"- **{item['ats']}**: {', '.join(item['problems'])}")
        out.append("")
    return "\n".join(out) + "\n"


def cmd_finish(args: argparse.Namespace) -> None:
    path = need_run()
    with locked():
        state = run_state(path)
        lines = ledger.read_ledger()
        results, rejected = {}, []
        for key, group in (state.get("groups") or {}).items():
            results[key] = L.read_json(Path(group["bundle"]).with_name(f"{key}.result.json"), None)
            note = Path(group["note"])
            backup = BACKUP_DIR / "ats" / note.name
            changed = not backup.exists() or backup.read_bytes() != (note.read_bytes() if note.exists() else b"")
            if not changed:
                continue
            problems = note_problems(note)
            if problems:
                _restore_note(key)  # the note goes back as it was; the report says why
                rejected.append({"ats": key, "problems": problems})
        L.write_text(REPORT_PATH, _report(state, results, rejected, lines))
        state["status"] = "done"
        L.write_json(path / "state.json", state)
    print(json.dumps({"rejected": [item["ats"] for item in rejected], "report": str(REPORT_PATH)}))


# --------------------------------------------------------------------------- end and reject


def cmd_end(args: argparse.Namespace) -> None:
    path = need_run()
    with locked():
        state = run_state(path)
        keys = sorted((state.get("groups") or {}))
        L.write_json(STATE_PATH, {"watermark": state.get("watermark_end", 0), "date": L.today()})
        shutil.rmtree(path, ignore_errors=True)
        CURRENT.unlink(missing_ok=True)
    print(f"Dream finished: {len(keys)} ATS keys ({', '.join(keys) or 'none'}).")
    print(f"The notes are in {W.USER_ATS_DIR}, the report is {REPORT_PATH}.")
    print("Undo this dream: ohmyjob dream reject")


def cmd_reject(args: argparse.Namespace) -> None:
    if not BACKUP_DIR.is_dir():
        fatal("no dream backup; there is nothing to put back")
    with locked():
        W.USER_ATS_DIR.mkdir(parents=True, exist_ok=True)
        saved = {path.name for path in (BACKUP_DIR / "ats").glob("*.md")}
        for path in W.USER_ATS_DIR.glob("*.md"):
            if path.name not in saved:
                path.unlink()  # a note the dream created
        for path in (BACKUP_DIR / "ats").glob("*.md"):
            shutil.copy2(path, W.USER_ATS_DIR / path.name)
        report = BACKUP_DIR / "Dream_Report.md"
        if report.exists():
            shutil.copy2(report, REPORT_PATH)
        else:
            REPORT_PATH.unlink(missing_ok=True)
        L.write_json(STATE_PATH, {"watermark": (L.read_json(BACKUP_DIR / "state.json", {}) or {}).get("watermark", 0)})
    print(f"The notes in {W.USER_ATS_DIR}, {REPORT_PATH.name} and the watermark are back as they were before the last dream.")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="ohmyjob dream")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "next", "finish", "end", "reject"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        {"start": cmd_start, "next": cmd_next, "finish": cmd_finish, "end": cmd_end,
         "reject": cmd_reject}[args.cmd](args)
    except W.WorkspaceError as exc:  # a missing workspace, a bad ohmyjob.json, a damaged file
        fatal(str(exc))


if __name__ == "__main__":
    main()
