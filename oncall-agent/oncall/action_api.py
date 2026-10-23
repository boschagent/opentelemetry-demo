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
Every POST requires header X-Approved-By (set by the approval gate) and honours "dry_run": true.
Every POST is appended to logs/actions.jsonl.
"""
from __future__ import annotations

import json
import os
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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/actions":
            return self._send(200, {"ok": True, "actions": ACTIONS, "services": sorted(ALLOWED_SERVICES)})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        name = self.path.rstrip("/").split("/")[-1]
        try:
            params = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "invalid json"})
        approver = self.headers.get("X-Approved-By", "")
        dry = bool(params.pop("dry_run", False))
        rec = {"ts": time.time(), "action": name, "target": params.get("service") or params.get("flag") or "load-generator",
               "params": params, "approved_by": approver, "dry_run": dry}
        if name not in ACTIONS:
            code, res = 403, {"ok": False, "error": f"action '{name}' not in allowlist"}
        elif not approver and not dry:
            code, res = 403, {"ok": False, "error": "missing human approval (X-Approved-By)"}
        elif os.path.exists("KILL"):
            code, res = 423, {"ok": False, "error": "kill switch engaged (KILL file present)"}
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
    serve(port=port).serve_forever()
