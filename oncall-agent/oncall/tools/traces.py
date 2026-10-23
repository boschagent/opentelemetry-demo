"""Trace tools (Jaeger HTTP API under /jaeger/ui/api).  Owner: P2.

Key derived signals:
  deepest_error  - an error span none of whose children errored  => where the failure ORIGINATES
  edge gap       - client span duration minus its server child's duration => time lost on the
                   network / in connection setup between caller and callee (latency / packet loss)
  orphan client  - an erroring client span with NO server child => callee never answered
                   (down, unreachable, refused)
"""
from __future__ import annotations

import json
import urllib.parse
from collections import Counter, defaultdict

from ..core import DENY_SERVICES, Backend, Config


def _tags(span: dict) -> dict:
    return {t.get("key"): t.get("value") for t in span.get("tags", []) if isinstance(t, dict)}


def _is_err(tags: dict) -> bool:
    return tags.get("error") in (True, "true") or str(tags.get("otel.status_code", "")).upper() == "ERROR"


def _err_msg(span: dict, tags: dict) -> str:
    msg = tags.get("otel.status_description") or tags.get("error.message") or ""
    for lg in span.get("logs", []):
        for f in lg.get("fields", []):
            if f.get("key") in ("exception.message", "message") and not msg:
                msg = str(f.get("value"))
    return str(msg)[:200]


def analyze_trace(trace: dict) -> dict:
    """Pure function: Jaeger trace JSON -> compact structure + derived signals."""
    procs = {pid: p.get("serviceName", "?") for pid, p in trace.get("processes", {}).items()}
    spans = {}
    for s in trace.get("spans", []):
        t = _tags(s)
        parent = None
        for ref in s.get("references", []) or []:
            if ref.get("refType") == "CHILD_OF":
                parent = ref.get("spanID")
        spans[s["spanID"]] = {
            "id": s["spanID"], "parent": parent, "service": procs.get(s.get("processID"), "?"),
            "op": s.get("operationName", "?"), "kind": str(t.get("span.kind", "")).lower(),
            "dur_ms": round(s.get("duration", 0) / 1000.0, 2), "error": _is_err(t), "msg": _err_msg(s, t),
            "peer": t.get("server.address") or t.get("net.peer.name") or t.get("rpc.service") or "",
            "children": [],
        }
    for sp in spans.values():
        if sp["parent"] in spans:
            spans[sp["parent"]]["children"].append(sp["id"])
    deepest, edges, orphans = [], [], []
    for sp in spans.values():
        kids = [spans[c] for c in sp["children"]]
        orphan_client = sp["kind"] == "client" and not any(k["kind"] == "server" for k in kids)
        if sp["error"] and not any(k["error"] for k in kids) and not orphan_client:
            # (an erroring client span with no server child is NOT an origin in the caller: the callee never answered)
            deepest.append({"service": sp["service"], "op": sp["op"], "msg": sp["msg"], "kind": sp["kind"]})
        if sp["kind"] == "client":
            servers = [k for k in kids if k["kind"] == "server" and k["service"] != sp["service"]]
            if servers:
                srv = servers[0]
                edges.append({"from": sp["service"], "to": srv["service"], "client_ms": sp["dur_ms"],
                              "server_ms": srv["dur_ms"], "gap_ms": round(sp["dur_ms"] - srv["dur_ms"], 2)})
            elif sp["error"]:
                orphans.append({"from": sp["service"], "op": sp["op"], "peer": sp["peer"], "msg": sp["msg"]})
    roots = [s for s in spans.values() if s["parent"] not in spans]
    root = roots[0] if roots else None
    return {
        "trace_id": trace.get("traceID"), "n_spans": len(spans),
        "root": f"{root['service']} {root['op']}" if root else "?", "root_ms": root["dur_ms"] if root else 0,
        "deepest_errors": deepest, "edges": edges, "orphan_client_errors": orphans,
        "services": sorted({s["service"] for s in spans.values()}),
    }


def _get_traces(be: Backend, cfg: Config, service: str, lookback: str, limit: int, errors_only: bool) -> dict:
    params = {"service": service, "lookback": lookback, "limit": str(limit)}
    if errors_only:
        params["tags"] = json.dumps({"error": "true"})
    url = f"{cfg.jaeger_url}/api/traces?" + urllib.parse.urlencode(params)
    return be.http("GET", url), url


def list_services(be: Backend, cfg: Config) -> dict:
    url = f"{cfg.jaeger_url}/api/services"
    r = be.http("GET", url)
    if not r.get("ok"):
        return {"ok": False, "summary": f"Jaeger unreachable: {r.get('error') or r.get('status')}", "data": None, "source": url}
    svcs = sorted(s for s in (r["json"] or {}).get("data", []) or [] if s not in DENY_SERVICES)
    return {"ok": True, "summary": "Services with traces: " + ", ".join(svcs), "data": svcs, "source": f"GET {url}"}


def find_error_traces(be: Backend, cfg: Config, service: str, lookback: str = "10m", limit: int = 20) -> dict:
    """Recent ERROR traces touching `service`, aggregated: where do errors originate?"""
    if service in DENY_SERVICES:
        return {"ok": False, "summary": "service not allowed", "data": None, "source": ""}
    r, url = _get_traces(be, cfg, service, lookback, limit, True)
    if not r.get("ok"):
        return {"ok": False, "summary": f"Jaeger query failed: {r.get('error') or r.get('status')}", "data": None, "source": url}
    traces = (r["json"] or {}).get("data", []) or []
    analyses = [analyze_trace(t) for t in traces]
    origin = Counter()
    msgs = defaultdict(Counter)
    orphan = Counter()
    for a in analyses:
        for d in a["deepest_errors"]:
            origin[(d["service"], d["op"])] += 1
            if d["msg"]:
                msgs[(d["service"], d["op"])][d["msg"]] += 1
        for o in a["orphan_client_errors"]:
            orphan[(o["from"], o["peer"] or o["op"])] += 1
    lines = [f"{len(traces)} error traces for '{service}' in last {lookback}."]
    for (svc, op), n in origin.most_common(6):
        top_msg = msgs[(svc, op)].most_common(1)
        lines.append(f"  error ORIGINATES in {svc} / {op}: {n} traces" + (f'  msg="{top_msg[0][0]}"' if top_msg else ""))
    for (frm, peer), n in orphan.most_common(4):
        lines.append(f"  client call from {frm} to '{peer}' failed with NO server span (callee unreachable?): {n} traces")
    lines.append("  example trace ids: " + ", ".join(a["trace_id"] for a in analyses[:5] if a["trace_id"]))
    data = {"n_traces": len(traces), "origins": [[s, o, n] for (s, o), n in origin.most_common(10)],
            "messages": {f"{s}/{o}": dict(c.most_common(3)) for (s, o), c in msgs.items()},
            "orphans": [[f, p, n] for (f, p), n in orphan.most_common(10)],
            "trace_ids": [a["trace_id"] for a in analyses[:10]]}
    return {"ok": True, "summary": "\n".join(lines), "data": data, "source": f"GET {url}"}


def get_trace(be: Backend, cfg: Config, trace_id: str) -> dict:
    url = f"{cfg.jaeger_url}/api/traces/{trace_id}"
    r = be.http("GET", url)
    if not r.get("ok"):
        return {"ok": False, "summary": f"Jaeger query failed: {r.get('error') or r.get('status')}", "data": None, "source": url}
    data = (r["json"] or {}).get("data", []) or []
    if not data:
        return {"ok": False, "summary": "trace not found", "data": None, "source": url}
    a = analyze_trace(data[0])
    s = (f"trace {a['trace_id']}: root={a['root']} ({a['root_ms']}ms), {a['n_spans']} spans, services={a['services']}\n"
         f"deepest errors: {a['deepest_errors'][:5]}\norphan client errors: {a['orphan_client_errors'][:5]}\n"
         f"slowest edges: {sorted(a['edges'], key=lambda e: -e['gap_ms'])[:5]}")
    return {"ok": True, "summary": s, "data": a, "source": f"GET {url}"}


def latency_edges(be: Backend, cfg: Config, service: str = "frontend", lookback: str = "5m", limit: int = 30) -> dict:
    """Aggregate caller->callee network gap over recent traces. Big gap + normal server time = network problem."""
    r, url = _get_traces(be, cfg, service, lookback, limit, False)
    if not r.get("ok"):
        return {"ok": False, "summary": f"Jaeger query failed: {r.get('error') or r.get('status')}", "data": None, "source": url}
    agg = defaultdict(list)
    for t in (r["json"] or {}).get("data", []) or []:
        for e in analyze_trace(t)["edges"]:
            agg[(e["from"], e["to"])].append(e)
    rows = []
    for (f, t), es in agg.items():
        gaps = sorted(e["gap_ms"] for e in es)
        srv = sorted(e["server_ms"] for e in es)
        rows.append({"from": f, "to": t, "n": len(es), "median_gap_ms": gaps[len(gaps) // 2],
                     "median_server_ms": srv[len(srv) // 2], "max_gap_ms": gaps[-1]})
    rows.sort(key=lambda x: -x["median_gap_ms"])
    lines = [f"{x['from']} -> {x['to']}: median network gap {x['median_gap_ms']}ms (server itself {x['median_server_ms']}ms, n={x['n']})" for x in rows[:12]]
    return {"ok": True, "summary": "Caller->callee gaps (client minus server time):\n" + "\n".join(lines), "data": rows, "source": f"GET {url}"}
