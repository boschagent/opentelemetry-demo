"""Core plumbing: config, I/O backend (live / record / replay), sanitiser, evidence ledger.

Owner: P1 (Lead).  Zero third-party dependencies (stdlib only) so setup can't break.

Backend modes
  live    - real HTTP calls + real `docker` CLI
  record  - live, AND every call/result is appended to <bundle>/calls.jsonl
  replay  - no network/docker at all; answers come from <bundle>/calls.jsonl
            (used for repeatable evaluation and for laptops that can't run the demo)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------------------
# Config (all overridable through environment variables or a .env file in repo root)
# --------------------------------------------------------------------------------------

def load_dotenv(path: str = "oncall.env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    prom_url: str = "http://localhost:9090"
    jaeger_url: str = "http://localhost:8080/jaeger/ui"
    action_api_url: str = "http://127.0.0.1:8765"
    loadgen_url: str = "http://localhost:8080/loadgen"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    rate_window: str = "3m"            # PromQL rate window (metrics arrive ~every 60s)
    baseline_offset: str = "10m"       # baseline = same window, this long ago
    confidence_threshold: float = 0.55  # below this -> escalate (tuned on practice set)
    max_llm_steps: int = 10
    verify_minutes: float = 4.0
    dry_run: bool = True               # SAFE DEFAULT: nothing changes unless --execute
    kill_file: str = "KILL"
    baseline_file: str = "baseline.json"
    approve_mode: str = "terminal"     # "terminal" (type yes) or "web" (approval page of the Action API)
    approval_timeout_s: float = 600.0  # no decision in time = rejected

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        c = cls()
        for k in c.__dataclass_fields__:
            ev = os.environ.get("ONCALL_" + k.upper())
            if ev is None:
                continue
            cur = getattr(c, k)
            if isinstance(cur, bool):
                setattr(c, k, ev.lower() in ("1", "true", "yes"))
            elif isinstance(cur, int):
                setattr(c, k, int(ev))
            elif isinstance(cur, float):
                setattr(c, k, float(ev))
            else:
                setattr(c, k, ev)
        return c


# --------------------------------------------------------------------------------------
# Sanitiser - RULE: the agent must never see feature-flag state (mentor confirmed).
# Everything a tool returns passes through redact() before the LLM or the ledger sees it.
# --------------------------------------------------------------------------------------

DENY_SERVICES = {"flagd", "flagd-ui"}
_FLAG_KEY = re.compile(r"(?i)(feature_flag|featureflag|openfeature|flagd|feature\.flag)")
_FLAG_LINE = re.compile(r"(?i)(feature[ _.-]?flag|openfeature|flagd)")


def redact(obj: Any) -> tuple[Any, int]:
    """Recursively strip anything that reveals feature-flag state.

    Removes: dict keys matching _FLAG_KEY; Jaeger-style {"key": "feature_flag.x", ...} tag
    entries; span events/logs that carry such fields; strings (log lines) mentioning flags;
    anything whose serviceName/service_name is flagd / flagd-ui.
    Returns (clean_object, number_of_items_removed).
    """
    removed = 0

    def is_flag_item(x: Any) -> bool:
        if isinstance(x, str):
            return bool(_FLAG_LINE.search(x))
        if isinstance(x, dict):
            for kk in ("key", "name", "event"):
                v = x.get(kk)
                if isinstance(v, str) and _FLAG_KEY.search(v):
                    return True
            for kk in ("serviceName", "service_name", "service"):
                v = x.get(kk)
                if isinstance(v, str) and v in DENY_SERVICES:
                    return True
            # span event / jaeger log: {"timestamp":..,"fields":[{"key":"event","value":"feature_flag"}]}
            f = x.get("fields")
            if isinstance(f, list) and any(is_flag_item(i) or (isinstance(i, dict) and isinstance(i.get("value"), str) and _FLAG_KEY.search(i["value"])) for i in f):
                return True
        return False

    def walk(x: Any) -> Any:
        nonlocal removed
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if isinstance(k, str) and _FLAG_KEY.search(k):
                    removed += 1
                    continue
                out[k] = walk(v)
            return out
        if isinstance(x, list):
            out = []
            for i in x:
                if is_flag_item(i):
                    removed += 1
                    continue
                out.append(walk(i))
            return out
        if isinstance(x, str) and "\n" in x:
            lines = x.split("\n")
            keep = [ln for ln in lines if not _FLAG_LINE.search(ln)]
            removed += len(lines) - len(keep)
            return "\n".join(keep)
        if isinstance(x, str) and _FLAG_LINE.search(x):
            removed += 1
            return "[redacted]"
        return x

    return walk(obj), removed


# --------------------------------------------------------------------------------------
# Backend: the ONLY place that touches the network or docker.
# --------------------------------------------------------------------------------------

_TIME_PARAMS = {"start", "end", "time", "_", "lookback", "query.start_time_min", "query.start_time_max"}


def _norm_key(kind: str, target: str) -> str:
    """Stable key for record/replay: drop time-varying query params."""
    if kind == "http":
        p = urllib.parse.urlsplit(target)
        q = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query) if k not in _TIME_PARAMS)
        target = p.path + "?" + urllib.parse.urlencode(q)
    return hashlib.sha1(f"{kind}|{target}".encode()).hexdigest()[:16]


_REPLAY_CACHE: dict[str, tuple] = {}     # bundle path -> ((mtime, size), {key: [results]}) - parse each bundle once


class Backend:
    def __init__(self, mode: str = "live", bundle: str | None = None):
        assert mode in ("live", "record", "replay"), mode
        self.mode, self.bundle = mode, bundle
        self._replay: dict[str, list[Any]] = {}
        if mode in ("record", "replay"):
            assert bundle, "record/replay need a bundle directory"
            os.makedirs(bundle, exist_ok=True)
        if mode == "replay":
            path = os.path.abspath(os.path.join(bundle, "calls.jsonl"))
            st = os.stat(path)
            cached = _REPLAY_CACHE.get(path)
            if cached is None or cached[0] != (st.st_mtime, st.st_size):
                recs: dict[str, list[Any]] = {}
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        r = json.loads(line)
                        recs.setdefault(r["key"], []).append(r["result"])
                _REPLAY_CACHE[path] = cached = ((st.st_mtime, st.st_size), recs)
            # each replay consumes its own copy of the per-key queues (the recorded results themselves are read-only)
            self._replay = {k: list(v) for k, v in cached[1].items()}

    def _record(self, kind: str, target: str, result: Any) -> None:
        if self.mode != "record":
            return
        rec = {"ts": time.time(), "kind": kind, "target": target, "key": _norm_key(kind, target), "result": result}
        with open(os.path.join(self.bundle, "calls.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _from_replay(self, kind: str, target: str) -> Any:
        # robustness experiments (eval/run_eval.py --degrade docker,jaeger,prom): pretend a data source is gone
        deg = os.environ.get("ONCALL_DEGRADE", "")
        if deg and ((kind == "cmd" and "docker" in deg) or (kind == "http" and (
                ("jaeger" in deg and ("/jaeger" in target or ":16686" in target)) or ("prom" in deg and ":9090" in target)))):
            return {"ok": False, "status": 0, "json": None, "text": "", "stdout": "", "stderr": "degraded", "code": -1,
                    "error": f"source unavailable (experiment: {deg})"}
        vals = self._replay.get(_norm_key(kind, target))
        if not vals:
            return {"ok": False, "error": f"replay: no recording for {kind} {target[:120]}"}
        # serve recordings in the order they happened (triage saw the broken state, verify saw the healed one);
        # once used up, keep answering with the last one
        return vals.pop(0) if len(vals) > 1 else vals[0]

    def http(self, method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        """Returns {"ok": bool, "status": int, "json": obj|None, "text": str, "error": str}"""
        target = f"{method} {url}" + (f" {json.dumps(body, sort_keys=True)}" if body else "")
        if self.mode == "replay":
            return self._from_replay("http", url if method == "GET" else target)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as e:
            text, status = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:  # connection refused, timeout ...
            res = {"ok": False, "status": 0, "json": None, "text": "", "error": f"{type(e).__name__}: {e}"}
            self._record("http", url if method == "GET" else target, res)
            return res
        try:
            js = json.loads(text)
        except Exception:
            js = None
        res = {"ok": 200 <= status < 300, "status": status, "json": js, "text": text[:20000], "error": ""}
        self._record("http", url if method == "GET" else target, res)
        return res

    def run(self, cmd: list[str], timeout: float = 30.0) -> dict:
        """Run a READ-ONLY command (docker ps/inspect/logs/stats). Returns {"ok","stdout","stderr","code"}."""
        target = " ".join(cmd)
        if self.mode == "replay":
            return self._from_replay("cmd", target)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
            res = {"ok": p.returncode == 0, "stdout": p.stdout[-5_000_000:], "stderr": p.stderr[-200_000:], "code": p.returncode}
        except Exception as e:
            res = {"ok": False, "stdout": "", "stderr": f"{type(e).__name__}: {e}", "code": -1}
        self._record("cmd", target, res)
        return res


# --------------------------------------------------------------------------------------
# Evidence ledger: every tool result gets an id. Reports may ONLY cite ledger ids.
# --------------------------------------------------------------------------------------

@dataclass
class Evidence:
    id: str
    tool: str
    args: dict
    source: str      # exact query / url / command
    summary: str     # what the LLM saw
    raw: Any         # sanitised raw data (for grounding checks)
    ts: float = field(default_factory=time.time)


class Ledger:
    def __init__(self):
        self.items: dict[str, Evidence] = {}
        self.redactions = 0

    def add(self, tool: str, args: dict, source: str, summary: str, raw: Any) -> Evidence:
        raw, n1 = redact(raw)
        summary, n2 = redact(summary)
        self.redactions += n1 + n2
        ev = Evidence(f"ev-{len(self.items) + 1:03d}", tool, args, source, summary, raw)
        self.items[ev.id] = ev
        return ev

    def text_of(self, ev_id: str) -> str:
        ev = self.items.get(ev_id)
        if not ev:
            return ""
        return ev.summary + "\n" + json.dumps(ev.raw, default=str)

    def to_list(self) -> list[dict]:
        return [{"id": e.id, "tool": e.tool, "args": e.args, "source": e.source, "summary": e.summary,
                 "ts": e.ts} for e in self.items.values()]


def atomic_write(path: str, text: str) -> None:
    """Write via temp file + rename so a crash never leaves a half-written file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts or time.time()))
