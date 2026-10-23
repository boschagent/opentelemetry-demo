"""Metrics tools (Prometheus).  Owner: P2.

Metric names verified in the demo's Grafana dashboards (src/grafana):
  traces_span_metrics_calls_total{service_name, span_name, span_kind, status_code}
  traces_span_metrics_duration_milliseconds_bucket{..., le}
Every tool returns {"ok", "summary", "data", "source"} and never raises.
"""
from __future__ import annotations

import math
import urllib.parse

from ..core import DENY_SERVICES, Backend, Config

CALLS = "traces_span_metrics_calls_total"
DUR = "traces_span_metrics_duration_milliseconds_bucket"


def _instant(be: Backend, cfg: Config, q: str) -> tuple[bool, list[tuple[dict, float]], str]:
    url = f"{cfg.prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": q})
    r = be.http("GET", url)
    if not r.get("ok") or not isinstance(r.get("json"), dict):
        return False, [], r.get("error") or r.get("text", "")[:300]
    out = []
    for s in r["json"].get("data", {}).get("result", []):
        try:
            v = float(s["value"][1])
        except Exception:
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        out.append((s.get("metric", {}), v))
    return True, out, ""


def prom_query(be: Backend, cfg: Config, query: str) -> dict:
    """Generic PromQL instant query (for the LLM to explore)."""
    ok, res, err = _instant(be, cfg, query)
    if not ok:
        return {"ok": False, "summary": f"Prometheus query failed: {err}", "data": None, "source": query}
    res = [(m, v) for m, v in res if m.get("service_name") not in DENY_SERVICES]
    lines = [f"{ {k: v for k, v in m.items() if k != '__name__'} } = {v:.4g}" for m, v in res[:40]]
    return {"ok": True, "summary": f"{len(res)} series\n" + "\n".join(lines), "data": [[m, v] for m, v in res[:200]],
            "source": f"PromQL: {query}"}


def _by_service(res: list[tuple[dict, float]]) -> dict[str, float]:
    return {m.get("service_name", "?"): v for m, v in res if m.get("service_name") not in DENY_SERVICES}


def service_red(be: Backend, cfg: Config) -> dict:
    """Per-service Rate / Errors / Duration now vs. baseline (offset). Sorted worst-first."""
    w, off = cfg.rate_window, cfg.baseline_offset
    kind = '{span_kind="SPAN_KIND_SERVER"}'
    ekind = '{span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}'

    def q_rate(sel: str, o: str = "") -> str:
        return f"sum by (service_name) (rate({CALLS}{sel}[{w}]{o}))"

    def q_p95(o: str = "") -> str:
        return f"histogram_quantile(0.95, sum by (le, service_name) (rate({DUR}{kind}[{w}]{o})))"

    queries = {
        "rps": q_rate(kind), "err": q_rate(ekind),
        "rps_base": q_rate(kind, f" offset {off}"), "err_base": q_rate(ekind, f" offset {off}"),
        "p95": q_p95(), "p95_base": q_p95(f" offset {off}"),
    }
    ok_rps, rps_try, _ = _instant(be, cfg, queries["rps"])
    if ok_rps and not rps_try:
        # ASSUMPTION: label is span_kind; if this demo version lacks it, drop the filter.
        kind, ekind = "", '{status_code="STATUS_CODE_ERROR"}'
        queries = {
            "rps": q_rate(kind), "err": q_rate(ekind),
            "rps_base": q_rate(kind, f" offset {off}"), "err_base": q_rate(ekind, f" offset {off}"),
            "p95": q_p95(), "p95_base": q_p95(f" offset {off}"),
        }
    vals, errors = {}, []
    for name, q in queries.items():
        ok, res, err = _instant(be, cfg, q)
        if not ok:
            errors.append(f"{name}: {err}")
        vals[name] = _by_service(res)
    if errors and not vals.get("rps"):
        return {"ok": False, "summary": "Prometheus unreachable: " + "; ".join(errors)[:500], "data": None,
                "source": "\n".join(queries.values())}
    rows = []
    for svc in sorted(set(vals["rps"]) | set(vals["err"]) | set(vals["p95"])):
        rps, err = vals["rps"].get(svc, 0.0), vals["err"].get(svc, 0.0)
        rb, eb = vals["rps_base"].get(svc, 0.0), vals["err_base"].get(svc, 0.0)
        p95, pb = vals["p95"].get(svc), vals["p95_base"].get(svc)
        er = err / rps if rps > 0 else 0.0
        erb = eb / rb if rb > 0 else 0.0
        rows.append({
            "service": svc, "rps": round(rps, 3), "rps_base": round(rb, 3),
            "rps_ratio": round(rps / rb, 2) if rb > 0 else None,
            "error_rate": round(er, 4), "error_rate_base": round(erb, 4),
            "p95_ms": round(p95, 1) if p95 is not None else None,
            "p95_base_ms": round(pb, 1) if pb is not None else None,
            "p95_ratio": round(p95 / pb, 2) if (p95 and pb) else None,
        })
    rows.sort(key=lambda r: (-(r["error_rate"] - r["error_rate_base"]), -(r["p95_ratio"] or 0)))
    lines = [f"{r['service']}: rps={r['rps']} (base {r['rps_base']}), err={r['error_rate']:.1%} "
             f"(base {r['error_rate_base']:.1%}), p95={r['p95_ms']}ms (base {r['p95_base_ms']}ms)" for r in rows]
    return {"ok": True, "summary": "Service RED, worst first:\n" + "\n".join(lines[:25]), "data": rows,
            "source": "PromQL:\n" + "\n".join(queries.values())}
