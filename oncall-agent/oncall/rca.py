"""Graph root-cause analysis: anomaly scores on a learned dependency graph, ranked by a random walk.  Owner: P1+P3.

Idea (MonitorRank / MicroRCA family, adapted to our small data):
  1. Every service/container gets an anomaly score from ITS OWN history: a robust z-score (median / MAD) of
     error ratio, p95 latency, request rate, CPU% and memory% in the last 3 min against the same series 6-15 min
     ago. No fixed thresholds per service, no training data.
  2. WHO BROKE FIRST: for each anomalous series we find the onset, the first sustained crossing.
     Causes usually start before their symptoms.
  3. The call graph (static architecture prior + edges learned from live traces) tells us where blame can flow:
     a failing caller whose callee fails in a CORRELATED way is probably a symptom. A random walk with restarts
     moves probability from symptoms toward anomalous, correlated callees and stays at nodes that nothing
     below them explains. The stationary distribution ranks root causes.
  4. Combined incidents: after the top root, every anomaly it can explain (itself and its callers) is removed;
     if a strong unexplained anomaly remains, it is a second, independent root.
All of this is plain deterministic Python (no LLM), so it is testable and its output becomes cited evidence.
"""
from __future__ import annotations

import math
from collections import defaultdict

# Static prior: the Astronomy Shop architecture (docs). The live graph adds edges learned from traces.
CALLS = {
    "frontend-proxy": ["frontend", "image-provider"],
    "frontend": ["ad", "cart", "checkout", "currency", "product-catalog", "recommendation", "shipping"],
    "checkout": ["cart", "currency", "email", "payment", "product-catalog", "shipping", "kafka"],
    "cart": ["valkey-cart"], "recommendation": ["product-catalog"], "shipping": ["quote"],
    "product-catalog": ["astronomy-db"], "accounting": ["kafka", "astronomy-db"], "fraud-detection": ["kafka"],
    "load-generator": ["frontend-proxy"],
}
EDGE = {"frontend", "frontend-proxy"}
NOT_ROOT = {"otel-collector", "jaeger", "prometheus", "grafana", "opensearch", "flagd", "flagd-ui", "opamp-server",
            "telemetry-docs"}

P = {   # tunables (swept on recorded incidents by eval/sweep.py)
    "window_min": 10, "recent_s": 150, "gap_s": 300, "z_on": 3.0, "z_full": 9.0,
    "err_min_delta": 0.04, "err_min": 0.05, "p95_ratio": 1.8, "p95_min_delta": 50.0,
    "rps_up": 1.8, "rps_down": 0.3, "rps_min": 0.2, "cpu_delta": 20.0, "mem_delta": 5.0,
    "alpha": 0.85, "back": 0.3, "onset_boost": 0.3, "root_min": 0.35, "second_min": 0.5,
}
FLOOR = {"err": lambda m: 0.01, "p95": lambda m: max(5.0, 0.1 * m), "rps": lambda m: max(0.05, 0.1 * m),
         "cpu": lambda m: 3.0, "mem": lambda m: 1.0}
CLASS = {"err": "app_errors", "p95": "latency_degradation", "cpu": "high_cpu", "mem": "memory_pressure",
         "rps_up": "traffic_surge", "rps_down": "service_down", "down": "service_down"}


CAUSE_ORDER = ["down", "rps_down", "mem", "cpu", "err", "p95", "rps_up"]


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if n == 0 else (xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2]))


def _split(pts, t_end):
    """Reference = points older than t_end-gap_s; recent = last recent_s. Falls back to halves on short history."""
    ref = [v for t, v in pts if t <= t_end - P["gap_s"]]
    rec = [v for t, v in pts if t > t_end - P["recent_s"]]
    ref_end = t_end - P["gap_s"]
    if len(ref) < 4 and len(pts) >= 6:
        h = len(pts) // 2
        ref, rec, ref_end = [v for _, v in pts[:h]], [v for _, v in pts[-max(2, h // 2):]], pts[h - 1][0]
    return ref, rec, ref_end


def series_anomaly(kind: str, pts: list, t_end: float) -> dict | None:
    """Robust shift of one series. Returns {score 0..1, z, ref, now, onset, dir} or None if not anomalous."""
    pts = sorted((float(t), float(v)) for t, v in pts if v is not None)
    if len(pts) < 4:
        return None
    ref, rec, ref_end = _split(pts, t_end)
    if len(ref) < 3 or not rec:
        return None
    m, now = _median(ref), _median(rec)
    mad = _median([abs(v - m) for v in ref]) * 1.4826
    scale = max(mad, FLOOR[kind if kind in FLOOR else "rps"](m))
    z = (now - m) / scale
    direction = "up"
    ok = False
    if kind == "err":
        ok = now >= P["err_min"] and now - m >= P["err_min_delta"]
    elif kind == "p95":
        ok = m > 0 and now / m >= P["p95_ratio"] and now - m >= P["p95_min_delta"]
    elif kind == "rps":
        if m >= P["rps_min"] and now <= P["rps_down"] * m:
            ok, direction, z = True, "down", abs(z)
        else:
            ok = m > 0 and now / m >= P["rps_up"]
    elif kind == "cpu":
        ok = now - m >= P["cpu_delta"] and now >= 2 * m
    elif kind == "mem":
        ok = now - m >= P["mem_delta"] and now >= 50
    if not ok or z < P["z_on"]:
        return None
    th = m + P["z_on"] * scale if direction == "up" else m - P["z_on"] * scale
    onset = None
    after = [(t, v) for t, v in pts if t > ref_end]
    for i in range(len(after)):
        bad = lambda v: v >= th if direction == "up" else v <= th
        if bad(after[i][1]) and (i + 1 >= len(after) or bad(after[i + 1][1])):
            onset = after[i][0]
            break
    score = min(1.0, (z - P["z_on"]) / (P["z_full"] - P["z_on"]) * 0.7 + 0.3)
    return {"score": round(score, 3), "z": round(z, 1), "ref": round(m, 4), "now": round(now, 4),
            "onset": onset, "dir": direction}


def node_anomalies(svc_series: dict, cont_series: dict) -> dict:
    """{node: {"a": combined score, "kinds": {kind: anomaly}, "onset": t}} over services AND containers."""
    allpts = [t for d in (svc_series, cont_series) for node in d.values() for s in node.values() for t, _ in s]
    if not allpts:
        return {}
    t_end = max(allpts)
    out = {}
    for src in (svc_series, cont_series):
        for node, kinds in src.items():
            for kind, pts in kinds.items():
                an = series_anomaly(kind, pts, t_end)
                if an is None:
                    continue
                key = kind if kind != "rps" else ("rps_up" if an["dir"] == "up" else "rps_down")
                if key == "rps_up" and node not in EDGE:
                    continue   # more calls inside the shop = retries or a shifted mix, not a traffic surge (live 24 Oct)
                n = out.setdefault(node, {"kinds": {}, "a": 0.0, "onset": None})
                n["kinds"][key] = an
    for node, n in out.items():
        prod = 1.0
        for an in n["kinds"].values():
            prod *= 1 - an["score"]
        n["a"] = round(1 - prod, 3)
        ons = [an["onset"] for an in n["kinds"].values() if an["onset"] is not None]
        n["onset"] = min(ons) if ons else None
    out["_t_end"] = t_end
    return out


def badness(kinds: dict, t_end: float) -> dict:
    """Per-timestamp standardized deviation of a node's worst series (for correlating caller and callee)."""
    b = defaultdict(float)
    for kind, pts in kinds.items():
        pts = sorted((float(t), float(v)) for t, v in pts if v is not None)
        if len(pts) < 4:
            continue
        ref, _, _ = _split(pts, t_end)
        if len(ref) < 3:
            continue
        m = _median(ref)
        sc = max(_median([abs(v - m) for v in ref]) * 1.4826, FLOOR[kind if kind in FLOOR else "rps"](m))
        for t, v in pts:
            dev = abs(v - m) / sc if kind == "rps" else (v - m) / sc
            b[t] = max(b[t], max(0.0, dev))
    return dict(b)


def corr(a: dict, b: dict) -> float:
    ts = sorted(set(a) & set(b))
    if len(ts) < 5:
        return 0.0
    x, y = [a[t] for t in ts], [b[t] for t in ts]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y))
    sx = math.sqrt(sum((p - mx) ** 2 for p in x))
    sy = math.sqrt(sum((q - my) ** 2 for q in y))
    return 0.0 if sx < 1e-9 or sy < 1e-9 else max(0.0, sxy / (sx * sy))


def merge_graph(*graphs) -> dict[str, set]:
    g: dict[str, set] = defaultdict(set)
    for gr in graphs:
        if isinstance(gr, dict):
            for a, bs in gr.items():
                for b in bs:
                    if a != b and "excluded" not in b:
                        g[a].add(b)
        else:   # list of [a, b, n]
            for a, b, *_ in gr or []:
                if a != b:
                    g[a].add(b)
    return g


def ancestors(g: dict[str, set], node: str) -> set:
    rev = defaultdict(set)
    for a, bs in g.items():
        for b in bs:
            rev[b].add(a)
    seen, stack = set(), [node]
    while stack:
        for p in rev.get(stack.pop(), ()):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def rank(g: dict[str, set], anom: dict, bad: dict, hard: set | None = None) -> dict:
    """Random walk with restart toward anomalous, correlated callees. Returns {node: score} (sums to 1)."""
    nodes = sorted((set(g) | {b for bs in g.values() for b in bs} | {n for n in anom if not n.startswith("_")}) - NOT_ROOT)
    a = {n: anom.get(n, {}).get("a", 0.0) for n in nodes}
    if sum(a.values()) <= 0:
        return {}
    W = {n: defaultdict(float) for n in nodes}
    for u in nodes:
        best_child = 0.0
        for v in g.get(u, ()):
            if v not in W:
                continue
            if v in (hard or ()):
                c = 1.0      # a dead / frozen dependency fully explains its callers' failures
            else:
                c = corr(bad.get(u, {}), bad.get(v, {})) if bad.get(u) and bad.get(v) else 0.5
            w = c * a[v]
            W[u][v] += w
            W[v][u] += P["back"] * c * a[u]
            best_child = max(best_child, w)
        W[u][u] += max(0.0, a[u] - best_child)
    p = {n: a[n] / sum(a.values()) for n in nodes}
    pi = dict(p)
    for _ in range(60):
        nxt = {n: (1 - P["alpha"]) * p[n] for n in nodes}
        for u in nodes:
            tot = sum(W[u].values())
            if tot <= 0:
                for n in nodes:
                    nxt[n] += P["alpha"] * pi[u] * p[n]
                continue
            for v, w in W[u].items():
                nxt[v] += P["alpha"] * pi[u] * w / tot
        pi = nxt
    return pi


def analyze(svc_series: dict, cont_series: dict, graph: dict[str, set], hard: dict | None = None) -> dict:
    """`hard` = {node: kind} for faults seen directly by docker (paused, exited, OOM ...). Uninstrumented parts
    (valkey, postgres, kafka) emit no span metrics, so without this the walk could never reach them
    (live 24 Oct: paused valkey-cart -> cart was blamed)."""
    anom = node_anomalies(svc_series or {}, cont_series or {})
    t_end = anom.pop("_t_end", None)
    callers = defaultdict(set)
    for a, bs in graph.items():
        for b in bs:
            callers[b].add(a)
    for node in list(anom):
        # less traffic arriving because a CALLER is down is expected, not a fault of this node (live 24 Oct rec_mem)
        if node in anom and "rps_down" in anom[node]["kinds"] and callers.get(node, set()) & set(hard or {}):
            del anom[node]["kinds"]["rps_down"]
            if not anom[node]["kinds"]:
                del anom[node]
            else:
                prod = 1.0
                for an in anom[node]["kinds"].values():
                    prod *= 1 - an["score"]
                anom[node]["a"] = round(1 - prod, 3)
    for node, kind in (hard or {}).items():
        n = anom.setdefault(node, {"kinds": {}, "a": 0.0, "onset": None})
        n["kinds"]["down"] = {"score": 1.0, "z": 99.0, "ref": "up", "now": kind, "onset": None, "dir": "down"}
        n["a"] = 1.0
        if t_end is not None:   # a hard fault seen now is at least as old as the earliest symptom
            ons = [x["onset"] for x in anom.values() if x.get("onset") is not None]
            n["onset"] = min(ons) - 1 if ons else None
    if not anom:
        return {"roots": [], "anomalous": {}, "ranking": [], "combined": False}
    merged_kinds = defaultdict(dict)
    for src in (svc_series or {}, cont_series or {}):
        for node, kinds in src.items():
            merged_kinds[node].update(kinds)
    bad = {n: badness(k, t_end) for n, k in merged_kinds.items()}
    pi = rank(graph, anom, bad, set(hard or {}))
    onsets = [n["onset"] for n in anom.values() if n["onset"] is not None]
    t0, t1 = (min(onsets), max(onsets)) if onsets else (None, None)

    def final(node):
        s = pi.get(node, 0.0) * (0.5 + anom.get(node, {}).get("a", 0.0))
        o = anom.get(node, {}).get("onset")
        if o is not None and t1 is not None and t1 > t0:
            s *= 1 + P["onset_boost"] * (t1 - o) / (t1 - t0)
        return s

    scored = {n: final(n) for n in pi if anom.get(n, {}).get("a", 0) > 0}
    tot = sum(scored.values()) or 1.0
    ranking = sorted(((n, round(s / tot, 3)) for n, s in scored.items()), key=lambda x: -x[1])
    roots = []
    explained: set = set()
    for node, share in ranking:
        if node in explained or node in NOT_ROOT:
            continue
        an = anom[node]
        need = P["root_min"] if not roots else P["second_min"]
        if an["a"] < need:
            continue
        latency_only = set(an["kinds"]) == {"p95"}
        if roots and latency_only:
            continue   # p95 of a quiet service is noisy (live 24 Oct healthy run): never a second, independent root
        if node in EDGE and any(r for r, _ in ranking if r not in EDGE and r not in explained and anom[r]["a"] >= need) \
                and "rps_up" not in an["kinds"]:
            continue   # the front door fails because something behind it fails
        # on one node, a resource anomaly explains latency/errors (CPU-starved -> slow), so causes win over symptoms
        strong = [k for k in CAUSE_ORDER if k in an["kinds"] and an["kinds"][k]["score"] >= 0.5]
        dom_kind = strong[0] if strong else max(an["kinds"].items(), key=lambda kv: (kv[1]["score"], kv[1]["z"]))[0]
        cls = CLASS.get(dom_kind, "app_errors")
        comp = "load-generator" if cls == "traffic_surge" else node
        anc = ancestors(graph, node)
        lag = sorted((anom[x]["onset"] - an["onset"], x) for x in anc
                     if x in anom and anom[x]["onset"] is not None and an["onset"] is not None)
        def _k(k, v):
            if k == "down":
                return f"Docker reports it {v['now'].replace('_', ' ')}"
            names = {"err": "error rate", "p95": "p95 latency", "cpu": "CPU %", "mem": "memory %", "rps_up": "traffic",
                     "rps_down": "traffic"}
            return f"{names.get(k, k)} {v['ref']} -> {v['now']} (robust z={v['z']})"
        why = (f"graph RCA: {node} ranks #{len(roots) + 1} (share {share:.0%}); evidence: "
               + "; ".join(_k(k, v) for k, v in an["kinds"].items()))
        if lag:
            why += "; its callers broke after it: " + ", ".join(f"{x} +{int(d)}s" for d, x in lag[:3] if d >= 0)
        roots.append({"node": comp, "service": node, "fault_class": cls, "share": share, "a": an["a"], "why": why,
                      "latency_only": latency_only,
                      "onset_rel_s": None if an["onset"] is None or t_end is None else int(an["onset"] - t_end)})
        explained |= {node} | anc
        if len(roots) == 2:
            break
    return {"roots": roots, "anomalous": {n: {"a": v["a"], "kinds": sorted(v["kinds"])} for n, v in anom.items()},
            "ranking": ranking[:8], "combined": len(roots) >= 2}


def summarize(res: dict) -> str:
    if not res.get("anomalous"):
        return "Graph RCA: no service or container deviates from its own recent history."
    lines = ["Graph RCA (anomaly scores on the learned call graph, random-walk ranking, onset ordering):",
             "  ranking: " + ", ".join(f"{n} {s:.0%}" for n, s in res["ranking"][:6])]
    for r in res["roots"]:
        lines.append(f"  root: {r['node']} [{r['fault_class']}] - {r['why']}")
    if res.get("combined"):
        lines.append("  TWO independent roots: possible combined incident.")
    return "\n".join(lines)
