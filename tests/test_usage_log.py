"""The per-run cost log: one computation for the printed total and the logged one, per-job attribution, and
`ohmyjob usage` rendering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import aa_lib as L  # noqa: E402
import usage_log  # noqa: E402
from test_pj import MASTER, MATCH, SCREEN, pe  # noqa: E402,F401  (pe is a fixture)


def fake_transcripts(root: Path, agents: list[tuple[str, str, str]]) -> Path:
    """(agent id, agent type, model) -> one 1000-token call each, under <root>/transcripts/sess/subagents."""
    sub = root / "transcripts" / "sess" / "subagents"
    sub.mkdir(parents=True)
    for agent_id, kind, model in agents:
        (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps({"agentType": kind, "parentAgentId": "m1"}))
        line = {"type": "assistant", "message": {"id": "a", "model": model, "usage": {
            "input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "output_tokens": 1000}}}
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(line))
    return root / "transcripts" / "sess.jsonl"


def test_logged_total_is_the_printed_total_and_jobs_add_up(pe, monkeypatch):
    for job_id, company in (("2001", "Acme"), ("2002", "Beta")):
        pe.folder(job_id, company, "Analyst")
        pe.row(job_id, company, "Analyst", "Found")
    transcript = fake_transcripts(pe.root, [("m1", MASTER, "claude-sonnet-5-5"), ("s1", SCREEN, "claude-haiku-5-5")])
    pe.pj("start")
    jobs = dict(pe.workers(pe.pj("assign-screen", "--max", "1")))  # only one job gets a worker
    (job, brief), = jobs.items()
    pe.bind(SCREEN, "s1", brief)
    assert pe.report(SCREEN, "s1", {"job": job, "score": 7, **MATCH}).returncode == 0
    pe.hook("guard", {"agent_type": MASTER, "agent_id": "m1", "tool_name": "Bash", "transcript_path": str(transcript),
                      "tool_input": {"command": "ohmyjob prep end"}})
    out = pe.pj("end")
    (line,) = [json.loads(x) for x in (pe.root / ".ohmyjob" / "usage.jsonl").read_text().splitlines()]

    assert f"Run cost ≈ {L.money(line['total'])} (" in out
    assert line["command"] == "/ohmyjob:prep-jobs" and line["by_kind"][SCREEN] > 0 and line["units"] == {SCREEN: 1}
    assert L.kind_lines(line) and all(row in out for row in L.kind_lines(line))  # what the end report printed


def test_append_and_render(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, "LOG_PATH", tmp_path / "usage.jsonl")
    assert "No runs are logged yet" in usage_log.render(usage_log.read_log())
    costs = {"by_kind": {"ohmyjob:screen-worker": 0.2, "overhead": 0.13}, "units": {"ohmyjob:screen-worker": 12},
             "unknown": [], "total": 0.33}
    usage_log.append("/ohmyjob:prep-jobs", costs, "run1")
    usage_log.append("/ohmyjob:apply-jobs", costs, None)
    usage_log.append("/ohmyjob:continue-applying", {**costs, "by_kind": {}, "units": {}, "total": 0.0,
                     "error": "Token usage: unknown (no transcript path was seen by the hook)"}, None)
    text = usage_log.render(usage_log.read_log())
    assert text.count("/ohmyjob:") == 3 and "Total over 3 runs: $0.66 (1 run with unknown cost left out)" in text
    assert "cost unknown (no transcript path was seen by the hook)" in text
    assert text.count("$0.02 per job") == 3  # two runs and the total over them; the unknown run adds nothing
