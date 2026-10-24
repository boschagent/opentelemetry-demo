"""HUMAN-ONLY practice tool to inject infrastructure faults (the agent never imports or calls this).  Owner: P5.

The organisers' `faultctl` does this on the day; use this only for practice if faultctl isn't available.
App faults (payment failure, cart failure, memory leak, ad CPU ...) are injected by a HUMAN in the flag UI:
    http://localhost:8080/feature/      (the agent must never read this page)

Usage (from repo root):
  python practice/inject.py stop cart              # container stopped
  python practice/inject.py pause valkey-cart      # cache unavailable (frozen)
  python practice/inject.py cpu ad 0.05            # starve CPU to 0.05 cores
  python practice/inject.py mem recommendation 60m # shrink memory limit (may OOM)
  python practice/inject.py delay shipping 800     # network delay (ms) via pumba (pulls an image; UNVERIFIED on Windows)
  python practice/inject.py loss checkout 30       # packet loss (%) via pumba
  python practice/inject.py reset                  # undo everything this script did
"""
import json
import os
import subprocess
import sys

STATE = "practice/.injected.json"


def sh(*a, check=False):
    print("$ docker", " ".join(a))
    p = subprocess.run(["docker", *a], capture_output=True, text=True)
    print((p.stdout + p.stderr).strip()[:500])
    return p


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else []


def save(s):
    os.makedirs("practice", exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=1)


def inspect(svc):
    p = subprocess.run(["docker", "inspect", svc], capture_output=True, text=True)
    hc = json.loads(p.stdout)[0]["HostConfig"]
    return hc.get("NanoCpus", 0), hc.get("Memory", 0), hc.get("MemorySwap", 0)


def host_cpus() -> str:
    """Docker cannot 'unset' a CPU limit (--cpus 0 is ignored, --cpu-quota -1 conflicts with NanoCPUs), so
    'no limit' is restored as a limit equal to every CPU of the Docker VM."""
    p = subprocess.run(["docker", "info", "--format", "{{.NCPU}}"], capture_output=True, text=True)
    return p.stdout.strip() if p.stdout.strip().isdigit() else "8"


def restore_demo() -> None:
    """Safety net: unpause / start any container of the demo's compose project that is paused or stopped."""
    proj = subprocess.run(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project"}}', "frontend"],
                          capture_output=True, text=True).stdout.strip()
    if not proj:
        return
    rows = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={proj}",
                           "--format", "{{.Names}} {{.State}}"], capture_output=True, text=True).stdout.split("\n")
    for row in rows:
        if not row.strip():
            continue
        name, state = row.split()[0], row.split()[-1]
        if state == "paused":
            sh("unpause", name)
        elif state in ("exited", "created", "dead"):
            sh("start", name)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd, args, st = sys.argv[1], sys.argv[2:], load()
    if cmd != "reset" and args:
        chk = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", args[0]], capture_output=True, text=True)
        if chk.returncode != 0:
            print(f"Container '{args[0]}' does not exist. Start the demo first (in the opentelemetry-demo folder):\n"
                  "  docker compose -f compose.yaml -f compose.observability.yaml up -d\nthen: python -m oncall.doctor")
            return 1
        if chk.stdout.strip() != "running":
            print(f"Container '{args[0]}' is '{chk.stdout.strip()}', not running. Run: python practice/inject.py reset")
            return 1
    ok = True
    if cmd == "stop":
        ok = sh("stop", args[0]).returncode == 0
        if ok:
            st.append({"kind": "stop", "svc": args[0]})
    elif cmd == "pause":
        ok = sh("pause", args[0]).returncode == 0
        if ok:
            st.append({"kind": "pause", "svc": args[0]})
    elif cmd == "cpu":
        cpu, mem, swap = inspect(args[0])
        ok = sh("update", "--cpus", args[1], args[0]).returncode == 0
        if ok:
            st.append({"kind": "cpu", "svc": args[0], "old": cpu})
    elif cmd == "mem":
        cpu, mem, swap = inspect(args[0])
        ok = sh("update", "--memory", args[1], "--memory-swap", args[1], args[0]).returncode == 0
        if ok:
            st.append({"kind": "mem", "svc": args[0], "old": mem, "old_swap": swap})
    elif cmd in ("delay", "loss"):
        name = f"pumba-{cmd}-{args[0]}"
        netem = ["delay", "--time", args[1]] if cmd == "delay" else ["loss", "--percent", args[1]]
        ok = sh("run", "-d", "--name", name, "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba",
                "netem", "--duration", "15m", "--tc-image", "gaiadocker/iproute2", *netem, args[0]).returncode == 0
        if ok:
            st.append({"kind": "pumba", "name": name})
    elif cmd == "reset":
        left = []
        for x in reversed(st):
            if x["kind"] == "stop":
                r = sh("start", x["svc"])
            elif x["kind"] == "pause":
                r = sh("unpause", x["svc"])
            elif x["kind"] == "cpu":
                r = sh("update", "--cpus", f"{x['old'] / 1e9:g}" if x["old"] else host_cpus(), x["svc"])
            elif x["kind"] == "mem":
                r = sh("update", "--memory", str(x["old"] or 0), "--memory-swap", str(x["old_swap"] or -1), x["svc"])
            else:
                r = sh("rm", "-f", x["name"])
            if r.returncode != 0 and "is not paused" not in (r.stdout + r.stderr):
                left.append(x)
        restore_demo()
        st = left
        ok = not left
        print("reset done." if ok else f"reset INCOMPLETE, still injected: {left}")
        print("Also reset flags in http://localhost:8080/feature/ if you changed any.")
    else:
        print(__doc__)
        return 2
    save(st)
    if not ok:
        print(f"FAILED: inject {cmd} {args}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
