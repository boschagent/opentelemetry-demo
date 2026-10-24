"""One-command workflows so humans run as little as possible.  Owner: P5 (+P1).

  python -m oncall.drill setup              # pull images (auto-retry), start demo w/ Prometheus+Jaeger, wait until healthy, record baseline
  python -m oncall.drill run payment        # guided incident: tells you what to break, waits, runs the FULL agent, records, asks you to undo
  python -m oncall.drill auto               # NO HUMAN NEEDED: starts Docker/demo if needed, fresh baseline, every
                                            # automatic incident in a row (dry-run), scores; --keep skips recorded ones
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
    # more automatic infrastructure faults (the hidden test includes infra faults and combinations)
    "payment_paused": ("(automatic) freezing the payment container", ["pause", "payment"], ["reset"], "payment", "service_down"),
    "catalog_down": ("(automatic) stopping product-catalog", ["stop", "product-catalog"], ["reset"], "product-catalog", "service_down"),
    "db_down":    ("(automatic) stopping the astronomy-db database", ["stop", "astronomy-db"], ["reset"], "astronomy-db", "service_down"),
    "quote_down": ("(automatic) stopping quote", ["stop", "quote"], ["reset"], "quote", "service_down"),
    "email_paused": ("(automatic) freezing email", ["pause", "email"], ["reset"], "email", "service_down"),
    "rec_mem":    ("(automatic) squeezing recommendation to 40 MB of memory", ["mem", "recommendation", "40m"], ["reset"], "recommendation", "memory_starved"),
    "currency_cpu": ("(automatic) limiting currency to 0.02 CPU", ["cpu", "currency", "0.02"], ["reset"], "currency", "cpu_starved"),
    "combo_cart_ad": ("(automatic) COMBINED: stopping cart AND limiting ad to 0.05 CPU",
                      [["stop", "cart"], ["cpu", "ad", "0.05"]], ["reset"], "cart", "service_down"),
}
SECONDARY = {"combo_cart_ad": ("ad", "cpu_starved")}
# fully automatic incidents, in the order `drill auto` runs them (flag incidents need a human in the flag UI)
AUTO = ["healthy", "cart_down", "valkey", "payment_paused", "catalog_down", "db_down", "quote_down",
        "rec_mem", "ad_starved", "combo_cart_ad"]


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
    if not start_stack():
        print("docker compose up kept failing - send Claude the last 30 lines AND the output of:  docker logs opensearch --tail 40")
        return 1
    say("STEP 3/3  Waiting for telemetry to flow (checks every 30s, up to 10 min)")
    cfg, be = Config.from_env(), Backend()
    for i in range(20):
        found = doctor.find_jaeger(be, cfg)
        if found:
            cfg.jaeger_url = found
        res = doctor.check(be, cfg)
        bad = [n for n, ok, _ in res if not ok and "optional" not in n.lower() and "Action API" not in n]
        must = [n for n in bad if n in ("docker reachable", "Prometheus has span metrics")]
        if not bad or (not must and i >= 3):   # Prometheus works: good enough to proceed, warn about the rest
            break
        print(f"  not ready yet: {bad}")
        time.sleep(30)
    else:
        doctor.verbose(be, cfg)
        print("Telemetry never became ready. Send Claude everything printed above.")
        return 1
    if bad:
        print(f"  WARNING: continuing without {bad} (agent still works, with less evidence). Diagnostics:")
        doctor.verbose(be, cfg)
    if found and found != Config.from_env().jaeger_url:
        with open(os.path.join(HERE, "oncall.env"), "a", encoding="utf-8") as f:
            f.write(f"\nONCALL_JAEGER_URL={found}\n")
        print(f"  Saved working Jaeger URL {found} to oncall.env")
    print("  Demo healthy. Recording baseline (what 'normal' looks like) ...")
    doctor.record_baseline(be, cfg)
    say("SETUP DONE. Next:  python -m oncall.drill run payment     (or: python -m oncall.drill all)")
    return 0


def start_stack(tries: int = 4) -> bool:
    """`compose up`, auto-healing the usual laptop failure: OpenSearch slow/unhealthy (needs vm.max_map_count on
    Docker Desktop/WSL2, and time). The otel-collector waits for OpenSearch, so without this no telemetry flows."""
    for i in range(1, tries + 1):
        if subprocess.run(COMPOSE + ["up", "-d"], cwd=REPO).returncode == 0:
            return True
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", "opensearch"], capture_output=True, text=True).stdout.strip()
        print(f"\n  Try {i}: compose up failed (opensearch health={st or '?'}). Auto-fixing ...")
        if os.name == "nt":  # Docker Desktop on Windows: OpenSearch needs a bigger mmap limit inside the WSL VM
            subprocess.run(["wsl", "-d", "docker-desktop", "-u", "root", "sysctl", "-w", "vm.max_map_count=262144"])
        subprocess.run(["docker", "restart", "opensearch"])
        countdown(60, "giving OpenSearch time to become healthy")
    return False


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
    if name in SECONDARY:
        labels[name]["secondary"] = {"component": SECONDARY[name][0], "fault_class": SECONDARY[name][1]}
    atomic_write(path, json.dumps(labels, indent=1))


def inject(args: list[str]) -> bool:
    return subprocess.run([sys.executable, os.path.join(HERE, "practice", "inject.py"), *args], cwd=HERE).returncode == 0


def demo_health(cfg: Config) -> tuple[bool, str]:
    """Is the shop back to normal? Every demo container running and no service erring well above its baseline.
    (Telemetry only - flag settings are never read.)"""
    from .tools import infra, metrics
    from .triage import load_baseline
    be = Backend()
    rows = infra.container_health(be, cfg).get("data") or []
    if not rows:
        return False, "docker not answering"
    down = [c["name"] for c in rows if c["status"] != "running"]
    if down:
        return False, f"not running: {down}"
    base = {r["service"]: r.get("error_rate", 0.0) for r in load_baseline(cfg.baseline_file).get("red", [])}
    red = metrics.service_red(be, cfg).get("data") or []
    hot = [f"{r['service']} {r['error_rate']:.0%}" for r in red
           if r["rps"] > 0.05 and r["error_rate"] > base.get(r["service"], 0.0) + 0.05]
    return (False, f"errors still elevated: {hot}") if hot else (True, "healthy")


def wait_healthy(cfg: Config, max_s: int = 480) -> bool:
    t0, tried_up = time.time(), False
    while time.time() - t0 < max_s:
        ok, why = demo_health(cfg)
        if ok:
            return True
        print(f"  waiting for the shop to be healthy again ({why}) ...", flush=True)
        if not tried_up and time.time() - t0 > 120 and "not running" in why:
            print("  restoring the demo with docker compose up -d ...")
            subprocess.run(COMPOSE + ["up", "-d"], cwd=REPO)
            tried_up = True
        time.sleep(30)
    return False


def docker_up() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True, text=True).returncode == 0


def preflight(cfg: Config) -> tuple[bool, list[str]]:
    """Get from 'laptop just switched on' to 'demo healthy, baseline fresh' without a human."""
    notes = []
    if not docker_up():
        exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker", "Docker Desktop.exe")
        if os.name == "nt" and os.path.exists(exe):
            say("Starting Docker Desktop ...")
            subprocess.Popen([exe])
        for _ in range(36):
            if docker_up():
                break
            time.sleep(5)
        else:
            return False, ["Docker is not running. Open Docker Desktop, wait for 'Engine running', run this again."]
        notes.append("started Docker Desktop")
    inject(["reset"])                        # undo anything a crashed earlier run left behind
    res = {n: ok for n, ok, _ in doctor.check(Backend(), cfg)}
    if not (res.get("all demo containers exist") and res.get("all demo containers running")):
        say("Starting the demo (docker compose up -d) ...")
        if not start_stack():
            return False, ["docker compose up failed - run start_demo.bat and send Claude the output"]
        notes.append("started the demo")
    for i in range(20):                      # telemetry needs ~3 min after a start
        res = doctor.check(Backend(), cfg)
        bad = [n for n, ok, _ in res if not ok and "optional" not in n.lower() and "Action API" not in n]
        if not bad:
            break
        print(f"  waiting for telemetry: {bad}", flush=True)
        time.sleep(30)
    else:
        doctor.verbose(Backend(), cfg)
        return False, [f"telemetry not ready: {bad} (diagnostics printed above)"]
    ensure_action_api(cfg)
    if os.path.exists(cfg.baseline_file):
        wait_healthy(cfg, 300)
    from .tools import metrics
    red = metrics.service_red(Backend(), cfg).get("data") or []
    broken = [f"{r['service']} {r['error_rate']:.0%}" for r in red if r["rps"] > 0.05 and r["error_rate"] > 0.2]
    if broken:
        return False, [f"The shop is not healthy before we start: {broken}. Is a flag still ON in {FLAG_UI}? "
                       "Turn every flag off, wait 5 min, run this again."]
    say("Recording a fresh healthy baseline ...")
    b = doctor.record_baseline(Backend(), cfg)
    notes.append(f"baseline: {len(b['stats'])} containers, {len(b['red'])} services, {len(b['graph'])} call edges")
    return True, notes


def run_one(name: str, execute: bool = True, wait: int = 240, auto: bool = False, use_llm: bool | None = None) -> dict | None:
    if name not in CATALOG:
        print(f"unknown incident '{name}'. Options: {list(CATALOG)}")
        return None
    os.chdir(HERE)
    cfg = Config.from_env()
    cfg.dry_run = not execute
    ensure_action_api(cfg)
    how, cmd, undo, comp, cls = CATALOG[name]
    if not os.path.exists(cfg.baseline_file):
        print("  No healthy baseline yet - recording it now (system must be healthy at this moment) ...")
        doctor.record_baseline(Backend(), cfg)
    say(f"INCIDENT '{name}'\nHUMAN: {how}")
    if cmd:
        for c in (cmd if isinstance(cmd[0], list) else [cmd]):
            if not inject(c):
                inject(["reset"])
                print(f"  Could not inject {c} - skipping '{name}' (nothing recorded, no label written).")
                return None
    elif name != "healthy":
        input("  Press Enter AFTER you saved the change in the flag UI ... ")
    try:
        return _investigate_and_record(name, cfg, wait, auto, use_llm, comp, cls)
    finally:
        if undo:                                 # always undo an automatic fault, even after an error or Ctrl-C
            inject(undo)
        elif name != "healthy" and not auto:
            input(f"HUMAN: undo it now in {FLAG_UI} (set it back to off / 0%), Save, then press Enter ... ")


def _investigate_and_record(name: str, cfg: Config, wait: int, auto: bool, use_llm: bool | None, comp: str, cls: str) -> dict:
    from .run import investigate_incident
    from . import report
    countdown(wait, "letting metrics/traces catch up")
    bundle = os.path.join("eval", "bundles", name)
    if os.path.exists(os.path.join(bundle, "calls.jsonl")):
        os.replace(os.path.join(bundle, "calls.jsonl"), os.path.join(bundle, f"calls.{int(time.time())}.old"))
    be = Backend("record", bundle)
    if use_llm is None:
        use_llm = bool(cfg.llm_base_url and cfg.llm_model)
    kw = {}
    if auto:   # unattended practice: dry-run only, so the automatic "yes" can never change anything
        cfg.dry_run = True
        kw["input_fn"] = lambda _p: (print("approve? > yes   (automatic, DRY-RUN practice: nothing will change)") or "yes")
    rep = investigate_incident(be, cfg, f"practice incident '{name}' (alert: on-call page)", use_llm, act=True,
                               incident_id=time.strftime(f"INC-%Y%m%d-%H%M%S-{name}"), **kw)
    write_label(name)
    rc = rep["root_cause"]
    escalated = rep["status"] in ("escalated", "no_incident")
    verdict = ("ESCALATED (right component)" if escalated else "CORRECT") if rc.get("component") == comp else \
        ("ESCALATED" if escalated else "WRONG")
    if name == "healthy":
        verdict = "CORRECT (no false alarm)" if rep["status"] in ("no_incident", "escalated") or not rc.get("component") else "FALSE ALARM"
    report.runlog({"command": f"python -m oncall.drill run {name}", "incident": rep["incident_id"], "purpose": f"practice {name}",
                   "result": f"{verdict}: {rc.get('component')}/{rc.get('fault_class')} conf={rep['confidence']} status={rep['status']}",
                   "notes": f"truth={comp}/{cls} llm={cfg.llm_model if use_llm else 'off'}"})
    say(f"RESULT '{name}': {verdict}   agent said {rc.get('component')}/{rc.get('fault_class')} (truth {comp}/{cls}), "
        f"conf={rep['confidence']}, status={rep['status']}\nreport: incidents/{rep['incident_id']}/report.md")
    rep["_verdict"] = verdict
    return rep


def auto_run(keep: bool = False, use_llm: bool = False, wait: int = 240, settle: int = 360) -> int:
    """ONE command, no human needed: start Docker/the demo if needed, record a fresh healthy baseline, run every
    automatic incident (inject -> wait -> investigate in DRY-RUN -> record -> undo -> wait until healthy -> settle),
    then score everything. Writes logs/auto_summary.json so Claude can read the whole night without a paste.
    `settle` keeps the 5-10 min window before each incident clean (the graph RCA compares against it)."""
    import traceback
    os.chdir(HERE)
    if os.name == "nt":      # keep Windows from sleeping while this runs (the screen may still turn off)
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass
    cfg = Config.from_env()
    t_start = time.time()
    summary = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "incidents": [], "preflight": None}

    def save():
        summary["minutes"] = round((time.time() - t_start) / 60, 1)
        atomic_write(os.path.join("logs", "auto_summary.json"), json.dumps(summary, indent=1, default=str))

    ok, notes = preflight(cfg)
    summary["preflight"] = {"ok": ok, "notes": notes}
    save()
    if not ok:
        say("CANNOT START:\n" + "\n".join(notes))
        return 1
    todo = [n for n in AUTO if not keep or not os.path.exists(os.path.join("eval", "bundles", n, "calls.jsonl"))]
    eta = len(todo) * (wait + 100 + 60 + settle) // 60
    say(f"AUTO DRILL: {len(todo)} incidents: {', '.join(todo)}\nAbout {eta} min, no human needed. Nothing is really "
        f"changed by the agent (dry-run); faults are injected and undone automatically.\nKeep the laptop plugged in "
        f"and awake. Ctrl-C stops safely (the current fault is undone).")
    for i, name in enumerate(todo, 1):
        print(f"\n##### {i}/{len(todo)}: {name}   ({round((time.time() - t_start) / 60)} min elapsed)", flush=True)
        row = {"name": name, "t0": time.strftime("%H:%M:%S")}
        t0 = time.time()
        try:
            rep = run_one(name, execute=False, auto=True, use_llm=use_llm, wait=wait)
            if rep is None:
                row["error"] = "fault could not be injected"
            else:
                rc = rep["root_cause"]
                row.update({"verdict": rep.get("_verdict"), "predicted": f"{rc.get('component')}/{rc.get('fault_class')}",
                            "truth": f"{CATALOG[name][3]}/{CATALOG[name][4]}", "confidence": rep["confidence"],
                            "status": rep["status"], "incident": rep["incident_id"],
                            "secondary": rc.get("secondary"), "notes": rc.get("notes")})
        except KeyboardInterrupt:
            row["error"] = "stopped by human (Ctrl-C)"
            summary["incidents"].append(row)
            save()
            say("Stopped. The current fault was undone. Run the same command again to continue.")
            return 1
        except Exception as e:   # one broken incident must not stop the night's run
            row["error"] = f"{type(e).__name__}: {e}"
            row["traceback"] = traceback.format_exc()[-3000:]
            print(f"  incident {name} failed: {row['error']}")
            inject(["reset"])
        row["seconds"] = round(time.time() - t0)
        summary["incidents"].append(row)
        save()
        if i < len(todo):
            healthy = wait_healthy(cfg)
            row["healthy_after"] = healthy
            save()
            countdown(settle, "letting the shop run normally before the next incident")
    say("SCORING all recorded incidents ...")
    p = subprocess.run([sys.executable, os.path.join("eval", "run_eval.py")], cwd=HERE, capture_output=True, text=True)
    print(p.stdout[-3000:], p.stderr[-2000:])
    summary["eval"] = p.stdout[-4000:] + p.stderr[-2000:]
    p = subprocess.run([sys.executable, "-m", "oncall.fewshot", "build"], cwd=HERE, capture_output=True, text=True)
    summary["fewshot"] = p.stdout[-2000:] + p.stderr[-2000:]
    save()
    lines = [f"{r['name']:15} {r.get('verdict') or r.get('error')}  ->  {r.get('predicted', '-')}  conf={r.get('confidence', '-')}"
             for r in summary["incidents"]]
    say("AUTO DRILL DONE (" + str(summary["minutes"]) + " min)\n" + "\n".join(lines) +
        "\n\nScoreboard: eval/SCOREBOARD.md\nNow just tell Claude: done")
    return 0


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
    if a[0] == "auto":
        return auto_run(keep="--keep" in a, use_llm="--llm" in a)
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
