"""Hands: plan -> human approval -> act (via Action API) -> verify -> rollback/escalate.  Owner: P4.

Safety controls
  * Allowlist: only runbook verbs in RUNBOOK; anything else is refused before any HTTP call.
  * Dry-run by default (cfg.dry_run=True). Real changes need --execute AND a typed "yes".
  * Kill switch: a file named KILL in the working dir (or env ONCALL_KILL=1) stops everything,
    checked before every action and between verify polls. Also enforced server-side.
"""
from __future__ import annotations

import json
import os
import time

from .core import Backend, Config
from .tools import infra, traces
from .tools.metrics import service_red

RUNBOOK = {
    "restart_service": {"params": ["service"], "inverse": None,
                        "why": "clears leaked memory/caches, crashed processes, wedged connections; restarts stopped containers"},
    "throttle_loadgen": {"params": ["users", "spawn_rate"], "inverse": "throttle_loadgen",
                         "why": "reduces abnormal traffic so the shop recovers"},
    "toggle_flag": {"params": ["flag", "enabled"], "inverse": "toggle_flag", "why": "disables a faulty feature"},
    "scale_service": {"params": ["service", "replicas"], "inverse": "scale_service", "why": "adds capacity (k8s only)"},
}


class KillSwitch(Exception):
    pass


def check_kill(cfg: Config) -> None:
    if os.path.exists(cfg.kill_file) or os.environ.get("ONCALL_KILL") == "1":
        raise KillSwitch("kill switch engaged")


def plan_action(diag: dict) -> dict | None:
    from .triage import REMEDY
    cls, comp = diag.get("fault_class"), diag.get("component")
    verb = REMEDY.get(cls)
    if not verb or not comp:
        return None
    if verb == "restart_service":
        return {"action": "restart_service", "params": {"service": comp},
                "reason": f"{comp} diagnosed as {cls}; restart is the allowlisted remedy ({RUNBOOK['restart_service']['why']})."}
    if verb == "throttle_loadgen":
        return {"action": "throttle_loadgen", "params": {"users": 2, "spawn_rate": 1},
                "reason": "abnormal traffic surge at the edge; throttle the load generator to normal levels."}
    return None


def _api(cfg: Config, method: str, path: str, body: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(cfg.action_api_url + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"ok": False}
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}


def ask_approval(diag: dict, plan: dict, cfg: Config, input_fn=input, out=print, sleep=time.sleep) -> dict:
    """Show diagnosis + evidence + plan, register it as a PROPOSAL at the Action API (so the approval is bound to
    exactly this action), then wait for a human: the exact word 'yes' in the terminal, or Approve on the web page."""
    approver = os.environ.get("USERNAME") or os.environ.get("USER") or "human"
    code, pr = _api(cfg, "POST", "/proposals", {"action": plan["action"], "params": plan["params"], "reason": plan["reason"],
                                               "dry_run": cfg.dry_run, "diagnosis": {k: diag.get(k) for k in (
                                                   "component", "fault_class", "summary", "confidence", "evidence")}})
    pid = pr.get("id") if code == 200 else None
    out("\n" + "=" * 78)
    out("APPROVAL REQUIRED")
    out(f"Root cause : {diag['component']} [{diag['fault_class']}]  confidence={diag['confidence']}")
    out(f"Summary    : {diag['summary']}")
    for e in diag.get("evidence", [])[:6]:
        out(f"Evidence   : [{e['id']}] {e['observation'][:200]}")
    out(f"Plan       : {plan['action']} {json.dumps(plan['params'])}")
    out(f"Reason     : {plan['reason']}")
    out(f"Mode       : {'DRY-RUN (nothing will change)' if cfg.dry_run else 'EXECUTE (will change the system)'}")
    if pid:
        out(f"Proposal   : {pid}  (approval page: {cfg.action_api_url}/)")
    if cfg.approve_mode == "web" and pid:
        out(f"Waiting up to {int(cfg.approval_timeout_s)}s for a decision on {cfg.action_api_url}/ ...  (KILL file aborts)")
        out("=" * 78)
        deadline = time.time() + cfg.approval_timeout_s
        while time.time() < deadline:
            check_kill(cfg)
            _, st = _api(cfg, "GET", f"/proposals/{pid}")
            p = st.get("proposal") or {}
            if p.get("status") in ("approved", "rejected"):
                return {"approved": p["status"] == "approved", "answer": f"web:{p['status']}", "approver": p.get("approver"),
                        "ts": time.time(), "proposal": pid, "channel": "web"}
            sleep(2)
        return {"approved": False, "answer": "timeout", "approver": None, "ts": time.time(), "proposal": pid, "channel": "web"}
    out("Type 'yes' to approve, anything else rejects.  (Create a file named KILL to abort the agent.)")
    out("=" * 78)
    try:
        ans = input_fn("approve? > ").strip()
    except EOFError:
        ans = ""
    approved = ans == "yes"
    approver = os.environ.get("USERNAME") or os.environ.get("USER") or "human"   # read after the answer (UI sets the name)
    if pid:
        _api(cfg, "POST", f"/proposals/{pid}/decide", {"decision": "approve" if approved else "reject",
                                                      "approver": approver, "confirm": ans})
    return {"approved": approved, "answer": ans, "approver": approver, "ts": time.time(), "proposal": pid,
            "channel": os.environ.get("ONCALL_APPROVAL_CHANNEL", "terminal")}


def call_action(be: Backend, cfg: Config, plan: dict, approver: str, proposal: str | None = None) -> dict:
    if plan["action"] not in RUNBOOK:
        return {"ok": False, "error": "action not in runbook allowlist (refused client-side)"}
    check_kill(cfg)
    import urllib.request
    body = dict(plan["params"], dry_run=cfg.dry_run)
    req = urllib.request.Request(f"{cfg.action_api_url}/actions/{plan['action']}", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "X-Approved-By": approver or "",
                                                          **({"X-Proposal-Id": proposal} if proposal else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"Action API unreachable at {cfg.action_api_url}: {e}"}


def health_check(be: Backend, cfg: Config, diag: dict) -> dict:
    """Is the diagnosed component healthy now? Uses fast signals (container state + last-1m error traces + RED)."""
    comp, cls = diag["component"], diag["fault_class"]
    checks = {}
    h = infra.container_health(be, cfg)
    row = next((c for c in h.get("data") or [] if c["name"] == comp), None)
    if row is not None:
        checks["container_running"] = row["status"] == "running"
    if cls in ("memory_pressure", "high_cpu", "oom", "cpu_starved"):
        # a restart always looks healthy for a minute; also require the resource itself to be back near its baseline
        st = next((x for x in infra.container_stats(be, cfg).get("data") or [] if x["name"] == comp), None)
        if st is not None:
            from .triage import load_baseline
            b = load_baseline(cfg.baseline_file).get("stats", {}).get(comp, {})
            if cls in ("memory_pressure", "oom"):
                checks["memory_ok"] = st["mem_pct"] < max(90.0, b.get("mem_pct", 0) + 3)
            else:
                checks["cpu_ok"] = st["cpu_pct"] < max(50.0, b.get("cpu_pct", 0) * 2 + 10)
    if cls == "traffic_surge":
        red = service_red(be, cfg)
        fe = next((r for r in red.get("data") or [] if r["service"] in ("frontend-proxy", "frontend")), None)
        checks["traffic_normal"] = bool(fe) and (fe["rps_ratio"] or 0) < 1.5
    else:
        et = traces.find_error_traces(be, cfg, service=comp if row is None or comp not in ("valkey-cart", "kafka", "astronomy-db") else "frontend",
                                      lookback="1m", limit=20)
        if et.get("ok"):
            checks["recent_error_traces"] = (et["data"] or {}).get("n_traces", 0)
            checks["errors_cleared"] = checks["recent_error_traces"] <= 1
    healthy = all(v for k, v in checks.items() if isinstance(v, bool))
    return {"healthy": healthy, "checks": checks, "ts": time.time()}


def verify(be: Backend, cfg: Config, diag: dict, out=print, sleep=time.sleep) -> dict:
    """Poll until 2 consecutive healthy checks or timeout."""
    deadline = time.time() + cfg.verify_minutes * 60
    history, streak = [], 0
    sleep(20)
    while time.time() < deadline:
        check_kill(cfg)
        hc = health_check(be, cfg, diag)
        history.append(hc)
        out(f"   verify: healthy={hc['healthy']} {hc['checks']}")
        streak = streak + 1 if hc["healthy"] else 0
        if streak >= 2:
            return {"recovered": True, "checks": history}
        sleep(20)
    return {"recovered": False, "checks": history}
