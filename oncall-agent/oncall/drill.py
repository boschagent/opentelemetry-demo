"""One-command workflows so humans run as little as possible.  Owner: P5 (+P1).

  python -m oncall.drill setup              # pull images (auto-retry), start demo w/ Prometheus+Jaeger, wait until healthy, record baseline
  python -m oncall.drill run payment        # guided incident: tells you what to break, waits, runs the FULL agent, records, asks you to undo
  python -m oncall.drill all                # every practice incident in a row, then scoreboard + threshold sweep
  python -m oncall.drill list               # show the incident catalog

The ONLY human jobs: break things when told (flag UI / one command), type 'yes' at approval, undo when told.
The agent never reads the catalog's "truth" - it is written to eval/labels.json as the answer key for scoring only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import doctor
from .core import Backend, Config, atomic_write

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # oncall-agent/
REPO = os.path.dirname(HERE)                                              # repo root with compose.yaml
COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.observability.yaml"]
FLAG_UI = "http://localhost:8080/feature/"

# name -> (how a HUMAN injects it, inject command or None, undo command or None, truth component, truth class)
CATALOG = {
    "payment":    (f"In {FLAG_UI} set paymentFailure to 100% and Save", None, None, "payment", "app_errors"),
    "cart_flag":  (f"In {FLAG_UI} turn cartFailure ON and Save", None, None, "cart", "app_errors"),
    "catalog":    (f"In {FLAG_UI} turn productCatalogFailure ON and Save", None, None, "product-catalog", "app_errors"),
    "rec_cache":  (f"In {FLAG_UI} turn recommendationCacheFailure ON and Save", None, None, "recommendation", "memory_pressure"),
    "ad_cpu":     (f"In {FLAG_UI} turn adHighCpu ON and Save", None, None, "ad", "high_cpu"),
    "ad_gc":      (f"In {FLAG_UI} turn adManualGc ON and Save", None, None, "ad", "latency_degradation"),
    "flood":      (f"In {FLAG_UI} turn loadGeneratorFloodHomepage ON and Save", None, None, "load-generator", "traffic_surge"),
    "cart_down":  ("(automatic) stopping the cart container", ["stop", "cart"], ["reset"], "cart", "service_down"),
    "valkey":     ("(automatic) pausing the valkey-cart cache", ["pause", "valkey-cart"], ["reset"], "valkey-cart", "service_down"),
    "ad_starved": ("(automatic) limiting ad to 0.05 CPU", ["cpu", "ad", "0.05"], ["reset"], "ad", "cpu_starved"),
    "healthy":    ("Nothing - leave everything normal (tests for false alarms)", None, None, "none", "none"),
}


def say(msg: str) -> None:
    print("\n" + "=" * 78 + f"\n{msg}\n" + "=" * 78, flush=True)


def countdown(sec: int, why: str) -> None:
    for left in range(sec, 0, -15):
        print(f"  {why}: {left}s left ...", flush=True)
        time.sleep(min(15, left))


def setup(max_tries: int = 30) -> int:
    say(f"STEP 1/3  Downloading demo images (auto-retry up to {max_tries}x). Folder: {REPO}")
    for i in range(1, max_tries + 1):
        p = subprocess.run(COMPOSE + ["pull", "--ignore-pull-failures"], cwd=REPO)
        missing = subprocess.run(COMPOSE + ["config", "--images"], cwd=REPO, capture_output=True, text=True).stdout.split()
        have = set(subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True).stdout.split())
        missing = [m for m in missing if m not in have]
        if not missing:
            break
        print(f"\n  Try {i}: still missing {missing}")
        if any("grafana" in m or m.startswith("postgres") for m in missing):
            print("  Docker Hub images failing -> the venue Wi-Fi can't reach Docker Hub. SWITCH THIS LAPTOP TO A PHONE HOTSPOT.")
        time.sleep(5)
    else:
        print("Could not download all images. Use a phone hotspot or ask organisers for the USB images, then rerun setup.")
        return 1
    say("STEP 2/3  Starting the demo with Prometheus + Jaeger")
    if subprocess.run(COMPOSE + ["up", "-d"], cwd=REPO).returncode != 0:
        print("docker compose up failed - copy the error to Claude.")
        return 1
    say("STEP 3/3  Waiting for telemetry to flow (checks every 30s, up to 10 min)")
    cfg, be = Config.from_env(), Backend()
    for _ in range(20):
        res = doctor.check(be, cfg)
        bad = [n for n, ok, _ in res if not ok and "optional" not in n.lower() and "Action API" not in n]
        if not bad:
            break
        print(f"  not ready yet: {bad}")
        time.sleep(30)
    else:
        print("Telemetry never became ready. Run: python -m oncall.doctor   and send the output to Claude.")
        return 1
    print("  Demo healthy. Recording baseline (what 'normal' looks like) ...")
    doctor.record_baseline(be, cfg)
    say("SETUP DONE. Next:  python -m oncall.drill run payment     (or: python -m oncall.drill all)")
    return 0


def ensure_action_api(cfg: Config) -> None:
    if Backend().http("GET", cfg.action_api_url + "/health", timeout=3).get("ok"):
        return
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    out = open(os.path.join(HERE, "logs", "action_api.out"), "a")
    subprocess.Popen([sys.executable, "-m", "oncall.action_api"], cwd=HERE, stdout=out, stderr=out)
    for _ in range(10):
        time.sleep(1)
        if Backend().http("GET", cfg.action_api_url + "/health", timeout=3).get("ok"):
            print("  (started the Action API in the background; log: logs/action_api.out)")
            return
    print("  WARNING: Action API did not start - run  python -m oncall.action_api  in another window.")


def write_label(name: str) -> None:
    path = os.path.join(HERE, "eval", "labels.json")
    labels = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    how, _, _, comp, cls = CATALOG[name]
    labels[name] = {"component": comp, "fault_class": cls, "injected": how, "ts": time.strftime("%Y-%m-%d %H:%M")}
    atomic_write(path, json.dumps(labels, indent=1))


def inject(args: list[str]) -> None:
    subprocess.run([sys.executable, os.path.join(HERE, "practice", "inject.py"), *args], cwd=HERE)


def run_one(name: str, execute: bool = True, wait: int = 180) -> dict | None:
    if name not in CATALOG:
        print(f"unknown incident '{name}'. Options: {list(CATALOG)}")
        return None
    from .run import investigate_incident
    from . import report
    os.chdir(HERE)
    cfg = Config.from_env()
    cfg.dry_run = not execute
    ensure_action_api(cfg)
    how, cmd, undo, comp, cls = CATALOG[name]
    say(f"INCIDENT '{name}'\nHUMAN: {how}")
    if cmd:
        inject(cmd)
    elif name != "healthy":
        input("  Press Enter AFTER you saved the change in the flag UI ... ")
    countdown(wait, "letting metrics/traces catch up")
    bundle = os.path.join("eval", "bundles", name)
    if os.path.exists(os.path.join(bundle, "calls.jsonl")):
        os.replace(os.path.join(bundle, "calls.jsonl"), os.path.join(bundle, f"calls.{int(time.time())}.old"))
    be = Backend("record", bundle)
    use_llm = bool(cfg.llm_base_url and cfg.llm_model)
    rep = investigate_incident(be, cfg, f"practice incident '{name}' (alert: on-call page)", use_llm, act=True,
                               incident_id=time.strftime(f"INC-%Y%m%d-%H%M%S-{name}"))
    write_label(name)
    rc = rep["root_cause"]
    verdict = "CORRECT" if rc.get("component") == comp else ("ESCALATED" if rep["status"] in ("escalated", "no_incident") else "WRONG")
    if name == "healthy":
        verdict = "CORRECT (no false alarm)" if rep["status"] in ("no_incident", "escalated") or not rc.get("component") else "FALSE ALARM"
    report.runlog({"command": f"python -m oncall.drill run {name}", "incident": rep["incident_id"], "purpose": f"practice {name}",
                   "result": f"{verdict}: {rc.get('component')}/{rc.get('fault_class')} conf={rep['confidence']} status={rep['status']}",
                   "notes": f"truth={comp}/{cls} llm={cfg.llm_model if use_llm else 'off'}"})
    say(f"RESULT '{name}': {verdict}   agent said {rc.get('component')}/{rc.get('fault_class')} (truth {comp}/{cls}), "
        f"conf={rep['confidence']}, status={rep['status']}\nreport: incidents/{rep['incident_id']}/report.md")
    if undo:
        inject(undo)
    elif name != "healthy":
        input(f"HUMAN: undo it now in {FLAG_UI} (set it back to off / 0%), Save, then press Enter ... ")
    return rep


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if a[0] == "list":
        for k, v in CATALOG.items():
            print(f"{k:11} truth={v[3]}/{v[4]:20} how: {v[0]}")
        return 0
    if a[0] == "setup":
        return setup()
    execute = "--dry-run" not in a
    if a[0] == "run" and len(a) > 1:
        run_one(a[1], execute)
        return 0
    if a[0] == "all":
        for name in CATALOG:
            run_one(name, execute)
            countdown(120, "cool-down so the next incident starts from healthy")
        subprocess.run([sys.executable, os.path.join("eval", "run_eval.py")], cwd=HERE)
        subprocess.run([sys.executable, os.path.join("eval", "sweep.py")], cwd=HERE)
        say("ALL DONE. Send Claude: eval/SCOREBOARD.md and the sweep output above. Then git add/commit/push.")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
