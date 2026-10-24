"""Local Action API - the ONLY component allowed to change the demo.  Owner: P4.

Why this exists: the agent must act only through an Action API. If the organisers give us theirs,
point ONCALL_ACTION_API_URL at it and adapt `ActionClient` paths in act.py. Until then this small
stdlib server implements the same four runbook verbs locally, with an allowlist and an audit log.

Run:   python -m oncall.action_api            (listens on 127.0.0.1:8765)

Endpoints
  GET  /health
  GET  /actions                      -> allowlist + descriptions
  POST /actions/restart_service      {"service": "cart"}          (docker restart / start / unpause)
  POST /actions/throttle_loadgen     {"users": 2, "spawn_rate": 1} (Locust swarm API)
  POST /actions/toggle_flag          -> 501 locally (we never touch flagd ourselves)
  POST /actions/scale_service        -> 501 locally (Kubernetes only)
Every POST honours "dry_run": true and is appended to logs/actions.jsonl.

Approval is BOUND to the exact action (proposals):
  POST /proposals                    {"action", "params", "diagnosis": {...}}  -> {"id"}   (agent asks)
  GET  /                             approval page for the on-call human (pending proposals, evidence, plan)
  POST /proposals/<id>/decide        {"decision": "approve"|"reject", "approver", "confirm": "yes"}
  GET  /proposals/<id>               status (agent polls)
A real (non-dry-run) action needs header X-Proposal-Id of an APPROVED proposal with the SAME action and params,
not older than 15 min and never used before (single use). So the agent can only execute exactly what a human saw.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import threading
import subprocess
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED_SERVICES = {
    "accounting", "ad", "cart", "checkout", "currency", "email", "fraud-detection", "frontend", "frontend-proxy",
    "image-provider", "load-generator", "payment", "product-catalog", "quote", "recommendation", "shipping",
    "valkey-cart", "kafka", "astronomy-db", "postgresql",
}
ACTIONS = {
    "restart_service": "Restart (or start/unpause) one application container. params: service",
    "throttle_loadgen": "Set the Locust load generator user count. params: users, spawn_rate",
    "toggle_flag": "Toggle a feature flag (organiser Action API only). params: flag, enabled",
    "scale_service": "Scale a deployment (Kubernetes only). params: service, replicas",
}
LOG = os.environ.get("ONCALL_ACTION_LOG", "logs/actions.jsonl")
LOADGEN = os.environ.get("ONCALL_LOADGEN_URL", "http://localhost:8080/loadgen")


def _log(rec: dict) -> None:
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _docker(*args: str) -> tuple[bool, str]:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, (p.stdout + p.stderr).strip()[:500]


def do_restart(params: dict, dry: bool) -> tuple[int, dict]:
    svc = str(params.get("service", ""))
    if svc not in ALLOWED_SERVICES:
        return 403, {"ok": False, "error": f"service '{svc}' not in allowlist"}
    ok, state = _docker("inspect", "-f", "{{.State.Status}}", svc)
    if not ok:
        return 404, {"ok": False, "error": f"container '{svc}' not found: {state}"}
    cmd = {"paused": ["unpause", svc], "exited": ["start", svc], "created": ["start", svc],
           "dead": ["start", svc]}.get(state, ["restart", svc])
    if dry:
        return 200, {"ok": True, "dry_run": True, "would_run": "docker " + " ".join(cmd), "current_state": state}
    ok, out = _docker(*cmd)
    return (200 if ok else 500), {"ok": ok, "ran": "docker " + " ".join(cmd), "output": out, "previous_state": state}


def _locust_users() -> int | None:
    try:
        with urllib.request.urlopen(LOADGEN + "/stats/requests", timeout=10) as r:
            return int(json.loads(r.read().decode()).get("user_count"))
    except Exception:
        return None


def do_throttle(params: dict, dry: bool) -> tuple[int, dict]:
    try:
        users = int(params.get("users", 2))
        rate = float(params.get("spawn_rate", 1))
    except Exception:
        return 400, {"ok": False, "error": "users/spawn_rate must be numbers"}
    if not 0 <= users <= 100:
        return 400, {"ok": False, "error": "users must be 0..100"}
    prev = _locust_users()
    if dry:
        return 200, {"ok": True, "dry_run": True, "would_set_users": users, "previous_users": prev}
    data = urllib.parse.urlencode({"user_count": users, "spawn_rate": rate}).encode()
    try:
        req = urllib.request.Request(LOADGEN + "/swarm", data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode()[:300]
        return 200, {"ok": True, "set_users": users, "previous_users": prev, "locust": body}
    except Exception as e:
        return 502, {"ok": False, "error": f"locust: {e}", "previous_users": prev}


PROPOSALS: dict[str, dict] = {}
_LOCK = threading.Lock()
PROPOSAL_TTL = 15 * 60


def _canon(params: dict) -> str:
    return json.dumps({k: v for k, v in (params or {}).items() if k != "dry_run"}, sort_keys=True)


def check_proposal(pid: str, action: str, params: dict) -> str | None:
    """None if this real action is covered by an approved, matching, fresh, unused proposal; else the reason."""
    with _LOCK:
        p = PROPOSALS.get(pid or "")
        if p is None:
            return "no approved proposal referenced (X-Proposal-Id missing or unknown)"
        if p["status"] != "approved":
            return f"proposal {pid} is {p['status']}, not approved"
        if p["action"] != action or _canon(p["params"]) != _canon(params):
            return f"proposal {pid} approved {p['action']} {_canon(p['params'])}, not {action} {_canon(params)}"
        if time.time() - p["decided_at"] > PROPOSAL_TTL:
            p["status"] = "expired"
            return f"approval of proposal {pid} expired"
        p["status"] = "used"
        p["used_at"] = time.time()
    return None


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>On-call approvals</title>
<script>setInterval(function(){var i=[].slice.call(document.querySelectorAll("input"));
if(!i.some(function(x){return x.value||x===document.activeElement}))location.reload()},5000)</script>
<style>
:root{--bg:#F5F4EF;--card:#fff;--ink:#13202E;--mut:#5A6573;--acc:#D9480F;--ok:#2B8A3E;--line:#E2DFD6}
@media (prefers-color-scheme:dark){:root{--bg:#10171F;--card:#18222E;--ink:#EEF0F2;--mut:#9AA5B1;--line:#2A3644}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,Segoe UI,Arial,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}h1{font-size:24px;margin:0 0 4px}.mut{color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
.tag{display:inline-block;padding:2px 10px;border-radius:99px;font-size:13px;border:1px solid var(--line)}
.dry{background:#FFF3BF;color:#5C3D00}.exec{background:var(--acc);color:#fff}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
code{background:var(--bg);padding:1px 6px;border-radius:4px}li{margin:4px 0}
input{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{font:inherit;padding:8px 18px;border-radius:8px;border:0;cursor:pointer;margin-right:8px}
.ap{background:var(--ok);color:#fff}.rj{background:var(--line);color:var(--ink)}
form{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px}
</style></head><body><main><h1>On-call approvals</h1>
<p class="mut">The agent changes nothing until a human approves here (or types yes in its terminal). An approval covers
exactly the action shown, once, for 15 minutes.</p>@@BODY@@</main></body></html>"""


def _card(p: dict) -> str:
    d = p.get("diagnosis") or {}
    e = lambda x: html.escape(str(x))
    conf = float(d.get("confidence") or 0)
    ev = "".join(f"<li><code>{e(x.get('id'))}</code> {e(str(x.get('observation', ''))[:260])}</li>" for x in d.get("evidence", [])[:8])
    mode = '<span class="tag dry">DRY-RUN: nothing will change</span>' if p.get("dry_run") else '<span class="tag exec">EXECUTE: will change the system</span>'
    form = ""
    if p["status"] == "pending":
        form = (f'<form method="post" action="/proposals/{e(p["id"])}/decide">'
                f'<input name="approver" placeholder="your name" required>'
                f'<input name="confirm" placeholder="type yes to approve" autocomplete="off">'
                f'<button class="ap" name="decision" value="approve">Approve</button>'
                f'<button class="rj" name="decision" value="reject">Reject</button></form>')
    else:
        form = f'<p class="mut">{e(p["status"])} by {e(p.get("approver") or "-")}</p>'
    return (f'<div class="card"><div class="mut">Proposal {e(p["id"])} · {e(time.strftime("%H:%M:%S", time.localtime(p["created"])))} · {mode}</div>'
            f'<h2 style="margin:8px 0">{e(d.get("component"))} <span class="tag">{e(d.get("fault_class"))}</span></h2>'
            f'<div class="bar"><i style="width:{conf * 100:.0f}%"></i></div><div class="mut">confidence {conf:.2f}</div>'
            f'<p>{e(d.get("summary", ""))}</p><b>Evidence</b><ul>{ev or "<li>none</li>"}</ul>'
            f'<b>Plan</b><p><code>{e(p["action"])} {e(_canon(p["params"]))}</code><br>{e(p.get("reason", ""))}</p>{form}</div>')


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):  # quiet
        pass

    def _html(self, code: int, body: str) -> None:
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/approve"):
            with _LOCK:
                ps = sorted(PROPOSALS.values(), key=lambda p: -p["created"])[:10]
            return self._html(200, PAGE.replace("@@BODY@@", "".join(_card(p) for p in ps) or '<div class="card mut">No proposals yet.</div>'))
        if path == "/proposals":
            with _LOCK:
                ps = sorted(PROPOSALS.values(), key=lambda p: -p["created"])[:20]
            return self._send(200, {"ok": True, "proposals": ps})
        if path.startswith("/proposals/"):
            p = PROPOSALS.get(path.split("/")[2])
            return self._send(200 if p else 404, {"ok": bool(p), **({"proposal": p} if p else {"error": "unknown proposal"})})
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/actions":
            return self._send(200, {"ok": True, "actions": ACTIONS, "services": sorted(ALLOWED_SERVICES)})
        self._send(404, {"ok": False, "error": "not found"})

    def _body(self) -> dict | None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}"
        if "application/x-www-form-urlencoded" in (self.headers.get("Content-Type") or ""):
            return dict(urllib.parse.parse_qsl(raw.decode()))
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _proposals(self, path: str, body: dict):
        parts = path.strip("/").split("/")
        if len(parts) == 1:              # POST /proposals
            if body.get("action") not in ACTIONS:
                return self._send(403, {"ok": False, "error": f"action '{body.get('action')}' not in allowlist"})
            pid = secrets.token_hex(4)
            p = {"id": pid, "action": body["action"], "params": body.get("params") or {}, "diagnosis": body.get("diagnosis") or {},
                 "reason": body.get("reason", ""), "dry_run": bool(body.get("dry_run")), "created": time.time(),
                 "status": "pending", "approver": None, "decided_at": None}
            with _LOCK:
                PROPOSALS[pid] = p
            _log({"ts": time.time(), "event": "proposal", "id": pid, "action": p["action"], "params": p["params"]})
            return self._send(200, {"ok": True, "id": pid, "approve_url": "/"})
        if len(parts) == 3 and parts[2] == "decide":
            with _LOCK:
                p = PROPOSALS.get(parts[1])
                if p is None:
                    return self._send(404, {"ok": False, "error": "unknown proposal"})
                if p["status"] != "pending":
                    return self._send(409, {"ok": False, "error": f"already {p['status']}"})
                approve = body.get("decision") == "approve" and str(body.get("confirm", "")).strip() == "yes" \
                    and bool(str(body.get("approver", "")).strip())
                p.update({"status": "approved" if approve else "rejected", "approver": str(body.get("approver", ""))[:60],
                          "decided_at": time.time()})
            _log({"ts": time.time(), "event": "decision", "id": p["id"], "status": p["status"], "approver": p["approver"]})
            if "application/x-www-form-urlencoded" in (self.headers.get("Content-Type") or ""):
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return None
            return self._send(200, {"ok": True, "status": p["status"]})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        params = self._body()
        if params is None:
            return self._send(400, {"ok": False, "error": "invalid json"})
        if path.startswith("/proposals"):
            return self._proposals(path, params)
        name = path.rstrip("/").split("/")[-1]
        approver = self.headers.get("X-Approved-By", "")
        pid = self.headers.get("X-Proposal-Id", "")
        dry = bool(params.pop("dry_run", False))
        rec = {"ts": time.time(), "action": name, "target": params.get("service") or params.get("flag") or "load-generator",
               "params": params, "approved_by": approver or (PROPOSALS.get(pid) or {}).get("approver"), "proposal": pid or None,
               "dry_run": dry}
        if name not in ACTIONS:
            code, res = 403, {"ok": False, "error": f"action '{name}' not in allowlist"}
        elif os.path.exists("KILL"):
            code, res = 423, {"ok": False, "error": "kill switch engaged (KILL file present)"}
        elif not dry and (why := check_proposal(pid, name, params)):
            code, res = 403, {"ok": False, "error": f"missing human approval: {why}"}
        elif name == "restart_service":
            code, res = do_restart(params, dry)
        elif name == "throttle_loadgen":
            code, res = do_throttle(params, dry)
        else:
            code, res = 501, {"ok": False, "error": f"{name} is not available on the local Action API"}
        rec.update({"status": code, "result": res})
        _log(rec)
        self._send(code, res)


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    port = int(os.environ.get("ONCALL_ACTION_PORT", "8765"))
    print(f"Local Action API on http://127.0.0.1:{port}  (allowlist: {list(ACTIONS)})  log -> {LOG}")
    print(f"Approval page for the on-call human:  http://127.0.0.1:{port}/")
    serve(port=port).serve_forever()
