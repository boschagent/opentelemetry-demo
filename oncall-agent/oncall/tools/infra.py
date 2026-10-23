"""Infrastructure tools: container health, resource usage, logs, change history.  Owner: P3.

All commands are READ-ONLY docker CLI calls (ps / inspect / stats / logs). The agent never
changes anything from here - changes go only through the Action API after human approval.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

from ..core import DENY_SERVICES, Backend, Config, redact

# Containers that are part of the demo's own tooling, not the application under test.
OBSERVABILITY = {"jaeger", "grafana", "prometheus", "opensearch", "otel-collector", "opamp-server", "telemetry-docs"}


def _parse_ts(s: str) -> float | None:
    if not s or s.startswith("0001-"):
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _pct(s: str) -> float:
    try:
        return float(str(s).strip().rstrip("%"))
    except Exception:
        return 0.0


def _ps_names(be: Backend) -> tuple[list[str], dict]:
    r = be.run(["docker", "ps", "-a", "--format", "{{json .}}"])
    if not r.get("ok"):
        return [], r
    names = []
    for line in r["stdout"].splitlines():
        try:
            names.append(json.loads(line)["Names"])
        except Exception:
            continue
    return names, r


def container_health(be: Backend, cfg: Config) -> dict:
    """State, health, restarts, OOM, exit code, CPU/mem limits for every container."""
    names, r = _ps_names(be)
    if not names:
        return {"ok": False, "summary": f"docker ps failed: {r.get('stderr', '')[:300]}", "data": None, "source": "docker ps -a"}
    names = [n for n in names if n not in DENY_SERVICES]
    ins = be.run(["docker", "inspect"] + sorted(names))
    try:
        info = json.loads(ins.get("stdout") or "[]")
    except Exception:
        info = []
    rows = []
    for c in info:
        st = c.get("State", {})
        hc = c.get("HostConfig", {})
        labels = (c.get("Config") or {}).get("Labels") or {}
        rows.append({
            "name": c.get("Name", "").lstrip("/"), "status": st.get("Status"),
            "health": (st.get("Health") or {}).get("Status"), "restarts": c.get("RestartCount", 0),
            "oom_killed": st.get("OOMKilled", False), "exit_code": st.get("ExitCode"),
            "started_at": st.get("StartedAt"), "finished_at": st.get("FinishedAt"), "created": c.get("Created"),
            "image": (c.get("Config") or {}).get("Image"),
            "cpu_limit_cores": round(hc.get("NanoCpus", 0) / 1e9, 3) if hc.get("NanoCpus") else
                               (round(hc["CpuQuota"] / hc["CpuPeriod"], 3) if hc.get("CpuQuota", 0) > 0 and hc.get("CpuPeriod") else None),
            "mem_limit_mb": round(hc.get("Memory", 0) / 1e6, 1) if hc.get("Memory") else None,
            "compose_project": labels.get("com.docker.compose.project"),
            "compose_service": labels.get("com.docker.compose.service"),
        })
    bad = [x for x in rows if x["status"] != "running" or x["health"] == "unhealthy" or x["oom_killed"] or x["restarts"]]
    lines = [f"{len(rows)} containers; {len(rows) - len(bad)} running normally."]
    for x in bad:
        lines.append(f"  !! {x['name']}: status={x['status']} health={x['health']} restarts={x['restarts']} "
                     f"oom={x['oom_killed']} exit={x['exit_code']} finished_at={x['finished_at']}")
    return {"ok": True, "summary": "\n".join(lines), "data": rows, "source": "docker ps -a; docker inspect <all>"}


def container_stats(be: Backend, cfg: Config) -> dict:
    """One `docker stats` sample: CPU% (100% = one core) and memory % of limit."""
    r = be.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=45)
    if not r.get("ok"):
        return {"ok": False, "summary": f"docker stats failed: {r.get('stderr', '')[:300]}", "data": None, "source": "docker stats --no-stream"}
    rows = []
    for line in r["stdout"].splitlines():
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("Name") in DENY_SERVICES:
            continue
        rows.append({"name": j.get("Name"), "cpu_pct": _pct(j.get("CPUPerc")), "mem_pct": _pct(j.get("MemPerc")),
                     "mem_usage": j.get("MemUsage"), "net_io": j.get("NetIO"), "pids": j.get("PIDs")})
    rows.sort(key=lambda x: -max(x["cpu_pct"], x["mem_pct"]))
    lines = [f"{x['name']}: cpu={x['cpu_pct']}% mem={x['mem_pct']}% ({x['mem_usage']})" for x in rows[:15]]
    return {"ok": True, "summary": "Top resource users (cpu 100% = 1 core; mem % of container limit):\n" + "\n".join(lines),
            "data": rows, "source": "docker stats --no-stream"}


_INTERESTING = re.compile(r"(?i)(error|exception|fail|refused|timeout|timed out|unavailable|panic|fatal|oom|out of memory|"
                          r"killed|warn|denied|reset by peer|deadline|unreachable|cannot|could not|503|500)")
_NORM = re.compile(r"\b(\d{4}-\d\d-\d\dT[\d:.]+Z?|[0-9a-f]{16,}|\d+(\.\d+)?)\b")


def get_logs(be: Backend, cfg: Config, service: str, minutes: int = 10, grep: str | None = None, max_lines: int = 40) -> dict:
    """Recent log lines of one container, error-ish lines by default, de-duplicated with counts."""
    if service in DENY_SERVICES:
        return {"ok": False, "summary": f"logs of '{service}' are not allowed (feature-flag service)", "data": None, "source": ""}
    cmd = ["docker", "logs", "--since", f"{int(minutes)}m", "--timestamps", service]
    r = be.run(cmd, timeout=30)
    if not r.get("ok") and not r.get("stdout"):
        return {"ok": False, "summary": f"docker logs failed: {r.get('stderr', '')[:300]}", "data": None, "source": " ".join(cmd)}
    text = (r.get("stdout", "") + "\n" + r.get("stderr", "")).strip()
    text, _ = redact(text)
    pat = re.compile(grep, re.I) if grep else _INTERESTING
    lines = [ln for ln in text.splitlines() if ln.strip() and pat.search(ln)]
    counts, first = Counter(), {}
    for ln in lines:
        body = ln.split(" ", 1)[1] if " " in ln else ln
        key = _NORM.sub("#", body)[:300]
        counts[key] += 1
        first.setdefault(key, ln[:400])
    top = [{"count": n, "example": first[k]} for k, n in counts.most_common(max_lines)]
    total = len(text.splitlines())
    s = [f"{service}: {total} log lines in last {minutes}m, {len(lines)} matched {'/' + grep + '/' if grep else 'error/warn patterns'}."]
    s += [f"  x{t['count']}: {t['example']}" for t in top[:20]]
    return {"ok": True, "summary": "\n".join(s), "data": {"total_lines": total, "matched": len(lines), "top": top},
            "source": " ".join(cmd)}


def deploy_history(be: Backend, cfg: Config, minutes: int = 120, action_log: str = "logs/actions.jsonl") -> dict:
    """What changed recently? Containers created/(re)started in the window + our own logged actions."""
    h = container_health(be, cfg)
    events = []
    cutoff = time.time() - minutes * 60
    for c in h.get("data") or []:
        for kind, key in (("created", "created"), ("started", "started_at"), ("stopped", "finished_at")):
            ts = _parse_ts(c.get(key) or "")
            if ts and ts >= cutoff and not (kind == "stopped" and c["status"] == "running"):
                events.append({"ts": ts, "container": c["name"], "event": kind, "image": c.get("image"),
                               "restarts": c.get("restarts")})
    if os.path.exists(action_log):
        with open(action_log, encoding="utf-8") as f:
            for line in f:
                try:
                    a = json.loads(line)
                except Exception:
                    continue
                if a.get("ts", 0) >= cutoff:
                    events.append({"ts": a["ts"], "container": a.get("target"), "event": f"agent action {a.get('action')}",
                                   "image": None, "restarts": None})
    events.sort(key=lambda e: e["ts"])
    # Containers whose creation is much newer than the bulk of the stack = new deployment / injected sidecar
    created = sorted(_parse_ts(c.get("created") or "") or 0 for c in h.get("data") or [])
    median_created = created[len(created) // 2] if created else 0
    late = [c["name"] for c in h.get("data") or [] if (_parse_ts(c.get("created") or "") or 0) - median_created > 300]
    lines = [f"Changes in last {minutes} min ({len(events)} events):"]
    for e in events[-25:]:
        lines.append(f"  {datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')} {e['container']}: {e['event']}"
                     + (f" (image {e['image']})" if e.get("image") else ""))
    if late:
        lines.append(f"  Containers created >5 min after the rest of the stack (new deploy or injected helper?): {late}")
    return {"ok": True, "summary": "\n".join(lines), "data": {"events": events, "late_containers": late},
            "source": "docker inspect timestamps + logs/actions.jsonl"}
