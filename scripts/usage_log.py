"""`ohmyjob usage ...`: the cost log of every prep, apply and continue run, one json line per run.

`prep end` and `apply end` append to it (never a block); this script prints it.

    ohmyjob usage     every run: date, command, total and the cost of each kind of work (as the end report prints it)

State: .ohmyjob/usage.jsonl (kept across runs, like traces/ledger.jsonl). Dollars are API-equivalent, not what a
subscription bills. A line is run_costs as it was: by_kind and units are keyed by agent type.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

import aa_lib as L
import workspace as W

LOG_PATH = W.STATE_DIR / "usage.jsonl"
CAVEAT = ("API-equivalent dollars, not what a subscription bills; the master's last turn and the main session "
          "are not included")


def append(command: str, costs: dict, run: Optional[str]) -> None:
    """One line for one run. costs is aa_lib.run_costs."""
    now = L.now_iso()
    entry = {"at": now, "date": now[:10], "command": command, "run": run, **costs,
             "total": round(costs["total"], 4), "by_kind": {k: round(v, 4) for k, v in costs["by_kind"].items()}}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log() -> list[dict]:
    lines = []
    for raw in (LOG_PATH.read_text(encoding="utf-8").splitlines() if LOG_PATH.exists() else []):
        if raw.strip():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue  # a damaged line is skipped
    return lines


def render(runs: list[dict]) -> str:
    if not runs:
        return ("No runs are logged yet: /ohmyjob:prep-jobs, /ohmyjob:apply-jobs and /ohmyjob:continue-applying "
                "write to it.")
    lines = [CAVEAT]
    for run in runs:
        head = f"{run['date']}  {run['command']:27}"
        if run.get("error"):  # the hook never saw the transcript: not a free run
            lines.append(f"{head} cost unknown ({run['error'].removeprefix('Token usage: unknown (').removesuffix(')')})")
            continue
        models = run.get("unknown") or []  # their cost is missing from the figure
        lines.append(f"{head} {L.money(run['total']):>7}" + (f" + unknown model {', '.join(models)}" if models else ""))
        lines += [f"    {line}" for line in L.kind_lines(run)]
    known = [r for r in runs if not r.get("error")]
    left = len(runs) - len(known)
    total: dict = {"by_kind": {}, "units": {}, "who": ""}
    for run in known:
        for field in ("by_kind", "units"):
            for kind, value in run[field].items():
                total[field][kind] = total[field].get(kind, 0) + value
    lines.append(f"Total over {len(runs)} {'run' if len(runs) == 1 else 'runs'}: "
                 f"{L.money(sum(r['total'] for r in known))}"
                 + (f" ({left} {'run' if left == 1 else 'runs'} with unknown cost left out)" if left else ""))
    lines += [f"    {line}" for line in L.kind_lines(total)]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("ohmyjob: usage takes no arguments")
        sys.exit(3)
    print(render(read_log()))


if __name__ == "__main__":
    main()
