"""Setup checker + healthy-baseline recorder.  Owner: P1/P5.

  python -m oncall.doctor            # checks everything the agent needs and prints the exact fix for each problem
  python -m oncall.doctor --baseline # ALSO records what "healthy" looks like -> baseline.json (do this when NO fault is injected)

Why a baseline: in the real demo some services normally sit at ~90% of their memory limit, so fixed thresholds
raise false alarms. Triage compares against this file instead.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .core import Backend, Config, atomic_write, now_iso
from .tools import infra, metrics, traces

REQUIRED = ["frontend", "frontend-proxy", "cart", "checkout", "payment", "product-catalog", "recommendation", "ad",
            "currency", "shipping", "quote", "email", "valkey-cart", "otel-collector", "load-generator"]
OBS = ["prometheus", "jaeger"]
COMPOSE = "docker compose -f compose.yaml -f compose.observability.yaml up -d"


def check(be: Backend, cfg: Config) -> list[tuple[str, bool, str]]:
    out = []
    r = be.run(["docker", "ps", "-a", "--format", "{{json .}}"])
    out.append(("docker reachable", r.get("ok", False), "Start Docker Desktop and wait until it says 'Engine running'."))
    h = infra.container_health(be, cfg)
    rows = {c["name"]: c for c in h.get("data") or []}
    missing = [n for n in REQUIRED + OBS if n not in rows]
    out.append(("all demo containers exist", not missing,
                f"Missing {missing}. In the opentelemetry-demo folder run:  {COMPOSE}"))
    down = [n for n in REQUIRED + OBS if n in rows and rows[n]["status"] != "running"]
    out.append(("all demo containers running", not down,
                f"Not running: {down}. Run:  {COMPOSE}   (or: python practice/inject.py reset)"))
    red = metrics.service_red(be, cfg)
    ok_red = red.get("ok") and bool(red.get("data"))
    out.append(("Prometheus has span metrics", bool(ok_red),
                f"Prometheus at {cfg.prom_url} not answering or empty. Is the 'prometheus' container running? "
                f"(plain 'docker compose up -d' does NOT start it; use: {COMPOSE}). Metrics need ~2-3 min after start."))
    js = traces.list_services(be, cfg)
    out.append(("Jaeger has traces", bool(js.get("ok") and js.get("data")),
                f"Jaeger at {cfg.jaeger_url} not answering. Open http://localhost:8080/jaeger/ui in a browser; needs the jaeger container."))
    a = be.http("GET", cfg.action_api_url + "/health", timeout=3)
    out.append(("Action API running", a.get("ok", False), "Open a 2nd terminal in oncall-agent and run:  python -m oncall.action_api"))
    out.append(("LLM configured (optional)", bool(cfg.llm_base_url and cfg.llm_model and cfg.llm_api_key) or "localhost" in cfg.llm_base_url,
                "Optional. Copy oncall.env.example to oncall.env and paste a key (see README). Without it use --no-llm."))
    return out


def record_baseline(be: Backend, cfg: Config, samples: int = 3) -> dict:
    acc: dict[str, list] = {}
    for i in range(samples):
        st = infra.container_stats(be, cfg)
        for s in st.get("data") or []:
            acc.setdefault(s["name"], []).append((s["cpu_pct"], s["mem_pct"]))
        if i < samples - 1:
            time.sleep(5)
    stats = {n: {"cpu_pct": round(max(v[0] for v in vals), 2), "mem_pct": round(max(v[1] for v in vals), 2)}
             for n, vals in acc.items()}
    logs = {}
    for n in stats:
        if n in infra.OBSERVABILITY:
            continue
        lg = infra.get_logs(be, cfg, n, minutes=3, max_lines=5)
        if lg.get("ok"):
            logs[n] = (lg.get("data") or {}).get("matched", 0)
    red = metrics.service_red(be, cfg)
    base = {"recorded_at": now_iso(), "stats": stats, "log_errors": logs, "red": red.get("data") or []}
    atomic_write(cfg.baseline_file, json.dumps(base, indent=1))
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    a = ap.parse_args(argv)
    cfg, be = Config.from_env(), Backend()
    res = check(be, cfg)
    for name, ok, fix in res:
        print(f"[{'OK ' if ok else 'FIX'}] {name}" + ("" if ok else f"\n      -> {fix}"))
    hard_fail = [n for n, ok, _ in res if not ok and "optional" not in n.lower()]
    if a.baseline:
        if hard_fail:
            print("\nNot recording a baseline until the problems above are fixed.")
            return 1
        b = record_baseline(be, cfg)
        noisy = {k: v for k, v in b["log_errors"].items() if v}
        print(f"\nBaseline saved to {cfg.baseline_file}: {len(b['stats'])} containers, "
              f"{len(b['red'])} services with metrics. Normally-noisy logs: {noisy}")
        print("Make sure NO fault was injected while this ran (all flags off).")
    print("\nREADY" if not hard_fail else f"\nNOT READY: fix {len(hard_fail)} item(s) above, then run this again.")
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    sys.exit(main())
