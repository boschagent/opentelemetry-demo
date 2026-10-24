"""Metrics tools (Prometheus).  Owner: P2.

Metric names verified in the demo's Grafana dashboards (src/grafana):
  traces_span_metrics_calls_total{service_name, span_name, span_kind, status_code}
  traces_span_metrics_duration_milliseconds_bucket{..., le}
Every tool returns {"ok", "summary", "data", "source"} and never raises.
"""
from __future__ import annotations

import math
import urllib.parse

from ..core import _FLAG_KEY, DENY_SERVICES, Backend, Config

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
    """Generic PromQL instant query (for the LLM to explore). Flag metrics are off-limits (rule: never read flag state)."""
    if _FLAG_KEY.search(query or ""):
        return {"ok": False, "summary": "refused: feature-flag metrics are off-limits", "data": None, "source": "(not run)"}
    ok, res, err = _instant(be, cfg, query)
    if not ok:
        return {"ok": False, "summary": f"Prometheus query failed: {err}", "data": None, "source": query}
    res = [(m, v) for m, v in res if m.get("service_name") not in DENY_SERVICES
           and not any(_FLAG_KEY.search(f"{k}={x}") for k, x in m.items())]
    lines = [f"{ {k: v for k, v in m.items() if k != '__name__'} } = {v:.4g}" for m, v in res[:40]]
    if not res:
        return {"ok": True, "summary": "0 series: NO DATA. This metric or label does not exist here; an empty result says "
                "nothing about whether a service is up or down.", "data": [], "source": f"PromQL: {query}"}
    return {"ok": True, "summary": f"{len(res)} series\n" + "\n".join(lines), "data": [[m, v] for m, v in res[:200]],
            "source": f"PromQL: {query}"}


def _by_service(res: list[tuple[dict, float]]) -> dict[str, float]:
    return {m.get("service_name", "?"): v for m, v in res if m.get("service_name") not in DENY_SERVICES}


def _range(be: Backend, cfg: Config, q: str, minutes: int = 15, step: int = 30):
    """PromQL range query -> (ok, [(labels, [(t, v), ...])], err). Timestamps come from Prometheus, never the
    wall clock, so recorded incidents replay identically."""
    import time as _t
    end = _t.time()
    url = f"{cfg.prom_url}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": f"{end - minutes * 60:.0f}", "end": f"{end:.0f}", "step": str(step)})
    r = be.http("GET", url, timeout=20)
    if not r.get("ok") or not isinstance(r.get("json"), dict):
        return False, [], r.get("error") or (r.get("text") or "")[:300]
    out = []
    for s in r["json"].get("data", {}).get("result", []):
        pts = []
        for t, v in s.get("values", []):
            try:
                fv = float(v)
            except Exception:
                continue
            if not (math.isnan(fv) or math.isinf(fv)):
                pts.append((float(t), fv))
        out.append((s.get("metric", {}), pts))
    return True, out, ""


def service_series(be: Backend, cfg: Config, minutes: int = 15, step: int = 30) -> dict:
    """Per-service time series of request rate, error ratio and p95 latency (server spans) over the last
    `minutes`. Used for robust anomaly scores and for WHO-BROKE-FIRST onset ordering."""
    srv = 'span_kind="SPAN_KIND_SERVER"'
    # span metrics arrive every ~60 s, so a 1m rate is empty (seen live 23 Oct); use the same 3m window that
    # service_red uses and that is proven on the live demo
    w = cfg.rate_window
    qs = {"rps": f'sum by (service_name) (rate({CALLS}{{{srv}}}[{w}]))',
          "errs": f'sum by (service_name) (rate({CALLS}{{{srv},status_code="STATUS_CODE_ERROR"}}[{w}]))',
          "p95": f'histogram_quantile(0.95, sum by (le, service_name) (rate({DUR}{{{srv}}}[{w}])))'}
    raw, errors = {}, []
    for k, q in qs.items():
        ok, res, err = _range(be, cfg, q, minutes, step)
        if not ok:
            errors.append(f"{k}: {err}")
        raw[k] = {m.get("service_name", "?"): dict(pts) for m, pts in res if m.get("service_name") not in DENY_SERVICES}
    if not raw.get("rps"):
        return {"ok": False, "summary": "no span-metric history: " + "; ".join(errors)[:300], "data": None,
                "source": "PromQL range:\n" + "\n".join(qs.values())}
    data = {}
    for svc, rps in raw["rps"].items():
        ts = sorted(rps)
        er = raw["errs"].get(svc, {})
        p = raw["p95"].get(svc, {})
        data[svc] = {"rps": [[t, rps[t]] for t in ts],
                     "err": [[t, (er.get(t, 0.0) / rps[t]) if rps[t] > 0 else 0.0] for t in ts],
                     "p95": [[t, p[t]] for t in ts if t in p]}
    return {"ok": True, "summary": f"{len(data)} services, {minutes} min history at {step}s steps", "data": data,
            "source": "PromQL range:\n" + "\n".join(qs.values())}


def _metric_names(be: Backend, cfg: Config) -> list[str]:
    r = be.http("GET", f"{cfg.prom_url}/api/v1/label/__name__/values", timeout=10)
    names = (r.get("json") or {}).get("data", []) if r.get("ok") and isinstance(r.get("json"), dict) else []
    return [n for n in names if not _FLAG_KEY.search(n)]


# The demo's collector ships docker_stats (+ redis, postgresql) metrics to Prometheus with the resource attribute
# container.name promoted to a label. Exact names depend on the OTLP->Prometheus translation, so discover them.
CONTAINER_PATTERNS = {"cpu": ["container_cpu_utilization", "container_cpu_usage_percent"],
                      "mem": ["container_memory_percent", "container_memory_utilization"]}


def container_series(be: Backend, cfg: Config, minutes: int = 15, step: int = 30) -> dict:
    """Per-container CPU% and memory% history from Prometheus (collector docker_stats receiver)."""
    names = _metric_names(be, cfg)
    picked = {}
    for kind, pats in CONTAINER_PATTERNS.items():
        for p in pats:
            hit = sorted(n for n in names if n.startswith(p))
            if hit:
                picked[kind] = hit[0]
                break
    if not picked:
        return {"ok": False, "summary": "no container metrics in Prometheus (docker_stats receiver not exporting?)",
                "data": None, "source": "GET /api/v1/label/__name__/values"}
    data: dict = {}
    for kind, name in picked.items():
        q = f"max by (container_name) ({name})"
        ok, res, _ = _range(be, cfg, q, minutes, step)
        # docker_stats reports PERCENT values even though its unit "1" makes Prometheus append _ratio
        # (verified on the live demo 2026-10-23). Guard anyway: a true 0..1 ratio never exceeds 1.5 on memory.
        scale = 1.0
        if kind == "mem" and res and max((v for _, pts in res for _, v in pts), default=0) <= 1.5:
            scale = 100.0
        for m, pts in res:
            c = m.get("container_name")
            if c and c not in DENY_SERVICES:
                data.setdefault(c, {})[kind] = [[t, v * scale] for t, v in pts]
    return {"ok": True, "summary": f"container history for {len(data)} containers ({picked})", "data": data,
            "source": "PromQL range: " + ", ".join(f"max by (container_name) ({n})" for n in picked.values())}


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
    ms = lambda v: "n/a" if v is None else f"{v}ms"     # a service with no traffic in the baseline has no p95
    lines = [f"{r['service']}: rps={r['rps']} (base {r['rps_base']}), err={r['error_rate']:.1%} "
             f"(base {r['error_rate_base']:.1%}), p95={ms(r['p95_ms'])} (base {ms(r['p95_base_ms'])})" for r in rows]
    return {"ok": True, "summary": "Service RED, worst first:\n" + "\n".join(lines[:25]), "data": rows,
            "source": "PromQL:\n" + "\n".join(queries.values())}


def service_timeline(be: Backend, cfg: Config, service: str, minutes: int = 15) -> dict:
    """When did this service start misbehaving? Each of its series (errors, p95, rate, CPU, memory) compared with
    its own past, with the onset time of any deviation. Answers 'who broke first' for the LLM."""
    from .. import rca
    if service in DENY_SERVICES:
        return {"ok": False, "summary": "service not allowed", "data": None, "source": ""}
    ss = service_series(be, cfg, minutes)
    cs = container_series(be, cfg, minutes)
    kinds = dict((ss.get("data") or {}).get(service, {}))
    kinds.update((cs.get("data") or {}).get(service, {}))
    if not kinds:
        return {"ok": False, "summary": f"no history for {service}", "data": None, "source": ss.get("source", "")}
    t_end = max(t for pts in kinds.values() for t, _ in pts)
    rows, lines = {}, []
    for k, pts in kinds.items():
        an = rca.series_anomaly(k, pts, t_end)
        last = pts[-1][1] if pts else None
        rows[k] = an or {"normal": True, "last": last}
        if an:
            ago = "" if an["onset"] is None else f", started {int(t_end - an['onset'])}s ago"
            lines.append(f"{k}: ANOMALOUS {an['ref']} -> {an['now']} (robust z={an['z']}){ago}")
        else:
            lines.append(f"{k}: normal (last {round(last, 4) if last is not None else '-'})")
    return {"ok": True, "summary": f"{service} over the last {minutes} min vs its own past:\n" + "\n".join(lines),
            "data": rows, "source": ss.get("source", "") + "\n" + cs.get("source", "")}
