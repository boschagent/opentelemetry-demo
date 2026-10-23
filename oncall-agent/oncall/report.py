"""Incident report (JSON + Markdown), schema validation, incident memory, run log.  Owner: P5.

NOTE: schema/incident_report.schema.json is OUR schema. When the organisers' JSON schema is available,
map fields in `to_organiser_schema()` - that is the single place to change.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import time

from .core import atomic_write, now_iso

PREVENTION = {
    "service_down": "Add a container liveness/readiness check with auto-restart policy and an alert on 'container not running' for >1 min.",
    "crash_restart": "Alert on restart count increase; inspect crash logs; add restart backoff and a crash-loop alert.",
    "oom": "Set memory limits with headroom, alert at 80% of limit, and fix unbounded caches (bounded LRU/TTL).",
    "memory_pressure": "Bound in-process caches (max size + TTL), alert on memory > 80% of limit, add memory-trend SLO.",
    "high_cpu": "Alert on sustained CPU > 80%, profile hot paths, add autoscaling / CPU limits per service.",
    "cpu_starved": "Review resource limits (CPU quota was too low); alert on CPU throttling; capacity-plan limits in config review.",
    "dependency_unreachable": "Add timeouts, retries with backoff and a circuit breaker on the client; alert on connection errors.",
    "network_degradation": "Alert on client-vs-server latency gap per edge; add timeouts/retries; check network policies.",
    "latency_degradation": "Add a p95 latency SLO alert per service and investigate the slow dependency.",
    "app_errors": "Add an error-rate SLO alert per service, canary/feature-flag guardrails and a regression test for the failing path.",
    "traffic_surge": "Add rate limiting at the edge proxy and an alert on request rate > 2x baseline; autoscale frontend.",
}

REQUIRED = {"incident_id": str, "status": str, "detected_at": str, "alert": str, "root_cause": dict,
            "confidence": (int, float), "evidence": list, "proposed_action": (dict, type(None)),
            "approval": (dict, type(None)), "actions_taken": list, "verification": (dict, type(None)),
            "timeline": list, "prevention": list}
STATUSES = {"resolved", "escalated", "diagnosed", "rejected_by_human", "no_incident", "aborted", "failed_rolled_back"}


def validate_report(r: dict) -> list[str]:
    errs = []
    for k, t in REQUIRED.items():
        if k not in r:
            errs.append(f"missing {k}")
        elif not isinstance(r[k], t):
            errs.append(f"{k} has wrong type {type(r[k]).__name__}")
    if r.get("status") not in STATUSES:
        errs.append(f"bad status {r.get('status')}")
    if isinstance(r.get("confidence"), (int, float)) and not 0 <= r["confidence"] <= 1:
        errs.append("confidence out of [0,1]")
    for e in r.get("evidence", []):
        if not isinstance(e, dict) or not {"id", "source", "observation"} <= set(e):
            errs.append("evidence item missing id/source/observation")
            break
    if r.get("status") == "resolved" and not (r.get("verification") or {}).get("recovered"):
        errs.append("status resolved but verification not recovered")
    if r.get("actions_taken") and not (r.get("approval") or {}).get("approved") and not all(a.get("dry_run") for a in r["actions_taken"]):
        errs.append("real action without approval")
    return errs


def to_organiser_schema(r: dict) -> dict:
    """ASSUMPTION: identity mapping until we receive the organisers' schema."""
    return r


def to_markdown(r: dict) -> str:
    rc = r["root_cause"]
    L = [f"# Incident {r['incident_id']} - {r['status'].upper()}", "",
         f"- **Detected:** {r['detected_at']}", f"- **Alert:** {r['alert']}",
         f"- **Root cause:** `{rc.get('component')}` - {rc.get('fault_class')}  (confidence **{r['confidence']}**)",
         f"- **Summary:** {rc.get('summary')}", ""]
    if r.get("escalation_reason"):
        L += [f"> **Escalated to human:** {r['escalation_reason']}", ""]
    L += ["## Evidence"] + [f"- `{e['id']}` ({e.get('tool', '')}): {e['observation']}  \n  source: `{str(e['source'])[:160]}`"
                            for e in r["evidence"]] + [""]
    if rc.get("alternatives"):
        L += ["## Alternatives considered"] + [f"- {a}" for a in rc["alternatives"]] + [""]
    if r.get("reviewer"):
        L += ["## Reviewer agent", f"- verdict: **{r['reviewer'].get('verdict')}** - {r['reviewer'].get('reason')}", ""]
    pa = r.get("proposed_action")
    L += ["## Action", f"- Proposed: {pa['action']} {json.dumps(pa['params'])} - {pa['reason']}" if pa else "- None proposed"]
    if r.get("approval"):
        L.append(f"- Approval: approved={r['approval'].get('approved')} by {r['approval'].get('approver')}")
    for a in r["actions_taken"]:
        L.append(f"- Taken: {json.dumps(a)[:300]}")
    if r.get("verification"):
        L.append(f"- Verification: recovered={r['verification'].get('recovered')}")
    L += ["", "## Timeline"] + [f"- {t['time']} - {t['event']}" for t in r["timeline"]]
    L += ["", "## Prevention"] + [f"- {p}" for p in r["prevention"]]
    return "\n".join(L) + "\n"


def save(r: dict, root: str = "incidents") -> str:
    d = os.path.join(root, r["incident_id"])
    atomic_write(os.path.join(d, "report.json"), json.dumps(to_organiser_schema(r), indent=2, default=str))
    atomic_write(os.path.join(d, "report.md"), to_markdown(r))
    return d


def similar_incidents(component: str | None, fault_class: str | None, root: str = "incidents", k: int = 3) -> list[dict]:
    """Incident memory: past resolved reports with the same component and/or fault class."""
    hits = []
    for p in glob.glob(os.path.join(root, "*", "report.json")):
        try:
            r = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        rc = r.get("root_cause") or {}
        score = (rc.get("component") == component) + (rc.get("fault_class") == fault_class)
        if score and r.get("status") in ("resolved", "diagnosed"):
            hits.append((score, r.get("detected_at", ""), {"incident_id": r["incident_id"], "component": rc.get("component"),
                         "fault_class": rc.get("fault_class"), "action": (r.get("proposed_action") or {}).get("action"),
                         "status": r["status"], "summary": rc.get("summary", "")[:200]}))
    hits.sort(key=lambda x: (x[0], x[1]), reverse=True)  # best match first, then most recent
    return [h[2] for h in hits[:k]]


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or "nogit"
    except Exception:
        return "nogit"


def runlog(entry: dict, path: str = "docs/RUNLOG.md") -> None:
    """Append one row to the human-readable run log (who/what/when/why/result)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("# Run log (auto-appended by code)\n\n| time | git | command | incident | purpose | result | notes |\n|---|---|---|---|---|---|---|\n")
        f.write(f"| {now_iso()} | {git_sha()} | `{entry.get('command', '')}` | {entry.get('incident', '')} | "
                f"{entry.get('purpose', '')} | {entry.get('result', '')} | {str(entry.get('notes', '')).replace('|', '/')[:200]} |\n")
