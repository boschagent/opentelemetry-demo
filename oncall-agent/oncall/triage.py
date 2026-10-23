"""Deterministic triage: turn raw telemetry into a ranked list of root-cause suspects.  Owner: P1+P2+P3.

Why deterministic first?  Free/small LLMs are unreliable at arithmetic over big JSON. Plain code
computes the hard signals; the LLM then reasons over a short, ranked, evidence-linked list.
Every threshold lives in THRESHOLDS so it can be tuned on the practice set (see docs/DECISIONS.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .core import Backend, Config, Ledger
from .tools import infra, metrics, traces

THRESHOLDS = {
    "err_abs": 0.05,          # error rate must be >= 5% ...
    "err_delta": 0.04,        # ... and >= 4 points above baseline
    "p95_ratio": 2.0,         # latency x2 vs baseline
    "p95_min_delta_ms": 50,
    "rps_surge_ratio": 2.0,   # frontend traffic x2 vs baseline
    "mem_pct": 85.0,
    "cpu_pct_hot": 80.0,      # % of one core
    "cpu_limit_frac": 0.8,    # using >=80% of an explicit CPU limit
    "gap_ms": 100.0,          # network gap caller->callee
    "origin_share": 0.5,
}
NOT_APP = infra.OBSERVABILITY | {"load-generator"}

# fault class -> default safe remedy (must exist in runbook allowlist). None = escalate to a human.
REMEDY = {
    "service_down": "restart_service",
    "crash_restart": "restart_service",
    "oom": "restart_service",
    "memory_pressure": "restart_service",
    "high_cpu": "restart_service",
    "dependency_unreachable": "restart_service",
    "network_degradation": "restart_service",
    "latency_degradation": "restart_service",
    "app_errors": "restart_service",
    "traffic_surge": "throttle_loadgen",
    "cpu_starved": None,      # a CPU limit was lowered; restart won't fix it and no allowlisted action changes limits
}


@dataclass
class Suspect:
    component: str
    fault_class: str
    score: float
    rationale: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"component": self.component, "fault_class": self.fault_class, "score": round(self.score, 3),
                "rationale": self.rationale, "evidence": self.evidence,
                "suggested_action": REMEDY.get(self.fault_class)}


def _peer_to_service(peer: str, known: set[str]) -> str | None:
    p = (peer or "").lower()
    p = re.sub(r"^(https?://)", "", p).split("/")[0].split(":")[0]
    for k in sorted(known, key=len, reverse=True):
        if k and (p == k or p.startswith(k) or k in p):
            return k
    return None


class Triage:
    def __init__(self, be: Backend, cfg: Config, ledger: Ledger):
        self.be, self.cfg, self.ledger = be, cfg, ledger
        self.suspects: dict[tuple[str, str], Suspect] = {}
        self.signals: dict = {}

    def _call(self, name: str, fn, **kw) -> dict:
        res = fn(self.be, self.cfg, **kw)
        ev = self.ledger.add(name, kw, res.get("source", ""), res.get("summary", ""), res.get("data"))
        res["ev"] = ev.id
        return res

    def _add(self, comp: str, cls: str, score: float, why: str, ev: str) -> None:
        key = (comp, cls)
        s = self.suspects.get(key)
        if s is None:
            self.suspects[key] = Suspect(comp, cls, score, [why], [ev])
        else:  # independent supporting signal -> noisy-OR combination
            s.score = 1 - (1 - s.score) * (1 - score)
            s.rationale.append(why)
            if ev not in s.evidence:
                s.evidence.append(ev)

    def run(self) -> dict:
        T = THRESHOLDS
        health = self._call("container_health", infra.container_health)
        stats = self._call("container_stats", infra.container_stats)
        red = self._call("service_red", metrics.service_red)
        known = {c["name"] for c in health.get("data") or []} | {r["service"] for r in red.get("data") or []}

        # R1/R2: containers down, restarting, OOM-killed
        for c in health.get("data") or []:
            if c["name"] in NOT_APP:
                continue
            if c["status"] in ("exited", "dead", "paused", "restarting", "created"):
                self._add(c["name"], "service_down", 0.9,
                          f"container {c['name']} status={c['status']} exit_code={c['exit_code']}", health["ev"])
            elif c["oom_killed"]:
                self._add(c["name"], "oom", 0.75, f"{c['name']} was OOM-killed (restarts={c['restarts']})", health["ev"])
            elif c["restarts"] and c["restarts"] > 0:
                self._add(c["name"], "crash_restart", 0.6, f"{c['name']} restart count={c['restarts']}", health["ev"])
            elif c["health"] == "unhealthy":
                self._add(c["name"], "service_down", 0.6, f"{c['name']} healthcheck=unhealthy", health["ev"])

        # R3/R4: resource pressure
        limits = {c["name"]: c for c in health.get("data") or []}
        for s in stats.get("data") or []:
            if s["name"] in NOT_APP:
                continue
            lim = (limits.get(s["name"]) or {}).get("cpu_limit_cores")
            if s["mem_pct"] >= T["mem_pct"]:
                self._add(s["name"], "memory_pressure", 0.6, f"{s['name']} memory at {s['mem_pct']}% of its limit", stats["ev"])
            if lim and lim < 1.0 and s["cpu_pct"] >= T["cpu_limit_frac"] * lim * 100:
                self._add(s["name"], "cpu_starved", 0.7,
                          f"{s['name']} pinned at {s['cpu_pct']}% CPU against a {lim}-core limit", stats["ev"])
            elif s["cpu_pct"] >= T["cpu_pct_hot"]:
                self._add(s["name"], "high_cpu", 0.6, f"{s['name']} using {s['cpu_pct']}% CPU (>= {T['cpu_pct_hot']}% of a core)", stats["ev"])

        rows = red.get("data") or []
        by = {r["service"]: r for r in rows}
        self.signals["red"] = rows

        # R7: traffic surge at the edge
        for edge in ("frontend-proxy", "frontend"):
            r = by.get(edge)
            if r and r["rps_ratio"] and r["rps_ratio"] >= T["rps_surge_ratio"]:
                self._add("load-generator", "traffic_surge", 0.7,
                          f"{edge} request rate {r['rps']}/s is x{r['rps_ratio']} of baseline {r['rps_base']}/s", red["ev"])
                break

        # R5: where do errors originate? (trace analysis of the services whose error rate jumped)
        erroring = [r for r in rows if r["error_rate"] >= T["err_abs"] and r["error_rate"] - r["error_rate_base"] >= T["err_delta"]]
        self.signals["erroring"] = [r["service"] for r in erroring]
        # Probe the deepest erroring services first (non-edge), dedupe traces across probes so the same
        # failing request seen from frontend AND frontend-proxy is counted once.
        edge = {"frontend", "frontend-proxy"}
        probe = [r["service"] for r in erroring if r["service"] not in edge][:2] + \
                [r["service"] for r in erroring if r["service"] in edge][:1]
        origins, orphans, ev_ids, seen = {}, {}, [], set()
        for svc in probe:
            et = self._call("find_error_traces", traces.find_error_traces, service=svc, lookback="10m", limit=20)
            ev_ids.append(et["ev"])
            d = et.get("data") or {}
            for tid in d.get("trace_ids", []):
                seen.add(tid)
            for o_svc, op, n in d.get("origins", []):
                k = (o_svc, op)
                origins[k] = max(origins.get(k, (0, None))[0], n), et["ev"]
            for frm, peer, n in d.get("orphans", []):
                k = (frm, peer)
                orphans[k] = max(orphans.get(k, (0, None))[0], n), et["ev"]
        total = sum(n for n, _ in origins.values()) + sum(n for n, _ in orphans.values())
        for (o_svc, op), (n, ev) in sorted(origins.items(), key=lambda kv: -kv[1][0])[:3]:
            share = n / total if total else 0
            if share >= T["origin_share"] or len(origins) == 1:
                self._add(o_svc, "app_errors", 0.45 + 0.4 * share,
                          f"{n} error traces originate in {o_svc} / {op} ({share:.0%} of failing requests)", ev)
        for (frm, peer), (n, ev) in sorted(orphans.items(), key=lambda kv: -kv[1][0])[:3]:
            tgt = _peer_to_service(peer, known)
            if tgt and tgt != frm:
                self._add(tgt, "dependency_unreachable", 0.5 + 0.3 * (n / total if total else 0),
                          f"{n} failed calls from {frm} to {peer} never reached a {tgt} server span", ev)

        # R6: latency - network gap vs slow service
        slow = [r for r in rows if r["p95_ratio"] and r["p95_ratio"] >= T["p95_ratio"]
                and (r["p95_ms"] or 0) - (r["p95_base_ms"] or 0) >= T["p95_min_delta_ms"]]
        self.signals["slow"] = [r["service"] for r in slow]
        if slow:
            le = self._call("latency_edges", traces.latency_edges, service="frontend", lookback="5m", limit=30)
            net_hit = False
            for e in (le.get("data") or [])[:3]:
                if e["median_gap_ms"] >= T["gap_ms"] and e["median_gap_ms"] >= 2 * max(e["median_server_ms"], 1):
                    net_hit = True
                    self._add(e["to"], "network_degradation", 0.65,
                              f"calls {e['from']}->{e['to']} lose a median {e['median_gap_ms']}ms between client and server "
                              f"while {e['to']} itself takes {e['median_server_ms']}ms", le["ev"])
            if not net_hit:
                cand = [r for r in slow if r["service"] not in ("frontend", "frontend-proxy")] or slow
                worst = max(cand, key=lambda r: r["p95_ratio"])
                self._add(worst["service"], "latency_degradation", 0.5,
                          f"{worst['service']} p95 {worst['p95_ms']}ms vs baseline {worst['p95_base_ms']}ms (x{worst['p95_ratio']})", red["ev"])

        ranked = sorted(self.suspects.values(), key=lambda s: -s.score)
        strong = [s for s in ranked if s.score >= 0.6]
        out = {
            "suspects": [s.to_dict() for s in ranked],
            "combination": len({s.component for s in strong}) >= 2,
            "healthy": not ranked,
            "signals": self.signals,
            "tool_errors": [n for n, r in (("container_health", health), ("container_stats", stats), ("service_red", red)) if not r.get("ok")],
        }
        return out


def summarize(tri: dict) -> str:
    if tri["healthy"]:
        return "Triage: no anomaly crossed thresholds." + (f" (tool errors: {tri['tool_errors']})" if tri["tool_errors"] else "")
    lines = ["Deterministic triage - ranked suspects:"]
    for i, s in enumerate(tri["suspects"][:6], 1):
        lines.append(f"{i}. {s['component']} [{s['fault_class']}] score={s['score']} evidence={s['evidence']}")
        for r in s["rationale"][:3]:
            lines.append(f"     - {r}")
    if tri["combination"]:
        lines.append("NOTE: two or more independent strong suspects -> possible combined incident.")
    if tri["tool_errors"]:
        lines.append(f"Tool errors: {tri['tool_errors']}")
    return "\n".join(lines)
