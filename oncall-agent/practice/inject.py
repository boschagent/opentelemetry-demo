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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args, st = sys.argv[1], sys.argv[2:], load()
    if cmd == "stop":
        sh("stop", args[0]); st.append({"kind": "stop", "svc": args[0]})
    elif cmd == "pause":
        sh("pause", args[0]); st.append({"kind": "pause", "svc": args[0]})
    elif cmd == "cpu":
        cpu, mem, swap = inspect(args[0])
        sh("update", "--cpus", args[1], args[0]); st.append({"kind": "cpu", "svc": args[0], "old": cpu})
    elif cmd == "mem":
        cpu, mem, swap = inspect(args[0])
        sh("update", "--memory", args[1], "--memory-swap", args[1], args[0])
        st.append({"kind": "mem", "svc": args[0], "old": mem, "old_swap": swap})
    elif cmd in ("delay", "loss"):
        name = f"pumba-{cmd}-{args[0]}"
        netem = ["delay", "--time", args[1]] if cmd == "delay" else ["loss", "--percent", args[1]]
        sh("run", "-d", "--name", name, "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba",
           "netem", "--duration", "15m", "--tc-image", "gaiadocker/iproute2", *netem, args[0])
        st.append({"kind": "pumba", "name": name})
    elif cmd == "reset":
        for x in reversed(st):
            if x["kind"] == "stop":
                sh("start", x["svc"])
            elif x["kind"] == "pause":
                sh("unpause", x["svc"])
            elif x["kind"] == "cpu":
                sh("update", "--cpus", str(x["old"] / 1e9) if x["old"] else "0", x["svc"])
            elif x["kind"] == "mem":
                sh("update", "--memory", str(x["old"] or 0), "--memory-swap", str(x["old_swap"] or -1), x["svc"])
            elif x["kind"] == "pumba":
                sh("rm", "-f", x["name"])
        st = []
        print("reset done. Also reset flags in http://localhost:8080/feature/ if you changed any.")
    else:
        print(__doc__)
        return
    save(st)


if __name__ == "__main__":
    main()
