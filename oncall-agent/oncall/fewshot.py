"""Few-shot incident memory: learn from labelled practice incidents and verified fixes.  Owner: P3+P5.

Every incident is turned into a NAME-FREE fingerprint: the shape of its symptoms around the top suspect
(which fault classes the rules saw on it, which of its own metrics deviate, whether it is the front door or an
uninstrumented dependency, how many other services are disturbed). Because no service names are in the vector,
an unseen incident on a different service still finds its look-alikes.

Uses at run time:
  1. the k most similar past incidents go into the LLM prompt as worked examples (few-shot), and
  2. a similarity-weighted vote acts as a LEARNED TIE-BREAK between the fault classes the evidence already supports
     on the suspect component (it can never invent a class or a component that the telemetry does not show).

  python -m oncall.fewshot build     # replay every labelled bundle in eval/bundles -> memory/fewshot.jsonl, then
                                     # leave-one-out accuracy (the honest number for the judges)
  python -m oncall.fewshot show      # list the library
Resolved live incidents (fix applied AND recovery verified) are appended automatically: learning from experience.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re

from .triage import CAUSE_PRIORITY, EDGE

LIB = os.path.join("memory", "fewshot.jsonl")
KINDS = ["err", "p95", "cpu", "mem", "rps_up", "rps_down"]
FEATURES = [f"cls:{c}" for c in CAUSE_PRIORITY] + [f"rca:{k}" for k in KINDS] + \
           ["edge", "instrumented", "others_disturbed", "combined", "rca_agrees", "log_burst"]


def fingerprint(tri: dict) -> list[float] | None:
    sus = tri.get("suspects") or []
    if not sus:
        return None
    top = sus[0]
    comp = top["component"]
    classes = {top["fault_class"]} | set(re.findall(r"^\(also ([a-z_]+)\)", "\n".join(top.get("rationale", [])), re.M))
    sig = tri.get("signals") or {}
    rca = sig.get("rca") or {}
    anom = rca.get("anomalous") or {}
    mine = set((anom.get(comp) or {}).get("kinds", []))
    red_names = {r["service"] for r in sig.get("red") or []}
    others = len([n for n in anom if n != comp])
    roots = [r["node"] for r in rca.get("roots", [])]
    v = [1.0 if c in classes else 0.0 for c in CAUSE_PRIORITY]
    v += [1.0 if k in mine else 0.0 for k in KINDS]
    v += [1.0 if comp in EDGE else 0.0, 1.0 if comp in red_names else 0.0, min(1.0, others / 4.0),
          1.0 if tri.get("combination") else 0.0, 1.0 if roots and roots[0] == comp else 0.0,
          1.0 if comp in (sig.get("log_bursts") or {}) else 0.0]
    return v


def cosine(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)


def load(path: str = LIB) -> list[dict]:
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def neighbors(tri: dict, k: int = 3, exclude: str | None = None, lib: list[dict] | None = None) -> list[dict]:
    fp = fingerprint(tri)
    if fp is None:
        return []
    lib = load() if lib is None else lib
    scored = [(cosine(fp, e["fp"]), e) for e in lib if e.get("source") != exclude and len(e.get("fp", [])) == len(fp)]
    scored.sort(key=lambda x: -x[0])
    return [{"similarity": round(s, 3), "fault_class": e["fault_class"], "action": e.get("action"),
             "outcome": e.get("outcome"), "source": e.get("source"), "shape": e.get("shape", "")} for s, e in scored[:k]]


def vote(nbrs: list[dict], allowed: set[str], min_sim: float = 0.85) -> tuple[str | None, float]:
    """Similarity-weighted class vote restricted to classes the current evidence supports."""
    w: dict[str, float] = {}
    for n in nbrs:
        if n["similarity"] >= min_sim and n["fault_class"] in allowed:
            w[n["fault_class"]] = w.get(n["fault_class"], 0.0) + n["similarity"]
    if not w:
        return None, 0.0
    cls = max(w, key=w.get)
    return cls, round(w[cls] / sum(w.values()), 3)


def shape(fp: list[float]) -> str:
    return ", ".join(f for f, x in zip(FEATURES, fp) if x >= 0.5) or "no strong features"


def prompt_text(nbrs: list[dict]) -> str:
    if not nbrs:
        return ""
    lines = ["Similar past incidents (few-shot memory; matched on symptom SHAPE with names removed - verify, don't trust):"]
    for n in nbrs:
        lines.append(f"  - similarity {n['similarity']:.2f}: symptoms [{n['shape']}] -> true cause was {n['fault_class']}"
                     + (f", fixed by {n['action']} ({n['outcome']})" if n.get("action") else f" ({n['outcome']})"))
    return "\n".join(lines)


def add(tri: dict, fault_class: str, source: str, action: str | None, outcome: str, path: str = LIB) -> None:
    fp = fingerprint(tri)
    if fp is None or fault_class in (None, "none"):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"fp": fp, "fault_class": fault_class, "source": source, "action": action,
                            "outcome": outcome, "shape": shape(fp)}) + "\n")


def build(bundles: str = "eval/bundles", labels_path: str = "eval/labels.json", path: str = LIB) -> dict:
    """Replay each labelled bundle through deterministic triage, store its fingerprint with the TRUE class,
    then report leave-one-out accuracy of the vote vs. plain triage."""
    from .core import Backend, Config, Ledger
    from .triage import Triage, REMEDY
    labels = json.load(open(labels_path, encoding="utf-8")) if os.path.exists(labels_path) else {}
    entries, tris = [], {}
    for b in sorted(glob.glob(os.path.join(bundles, "*", "calls.jsonl"))):
        name = os.path.basename(os.path.dirname(b))
        lab = labels.get(name)
        if not lab or lab.get("fault_class") in (None, "none"):
            continue
        tri = Triage(Backend("replay", os.path.dirname(b)), Config.from_env(), Ledger()).run()
        fp = fingerprint(tri)
        if fp is None:
            continue
        tris[name] = (tri, lab)
        entries.append({"fp": fp, "fault_class": lab["fault_class"], "source": name,
                        "action": REMEDY.get(lab["fault_class"]), "outcome": "labelled practice incident", "shape": shape(fp)})
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)
    loo = {"n": 0, "triage_class_ok": 0, "fewshot_class_ok": 0}
    for name, (tri, lab) in tris.items():
        top = tri["suspects"][0]
        from .brain import decide
        t2 = dict(tri, fewshot=neighbors(tri, k=3, exclude=name, lib=entries))
        final = decide(Config.from_env(), t2, None, Ledger(), None)["fault_class"]   # the real decision path
        loo["n"] += 1
        loo["triage_class_ok"] += top["fault_class"] == lab["fault_class"]
        loo["fewshot_class_ok"] += final == lab["fault_class"]
    return {"entries": len(entries), "leave_one_out": loo}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "show"])
    a = ap.parse_args(argv)
    if a.cmd == "build":
        r = build()
        print(json.dumps(r, indent=1))
        loo = r["leave_one_out"]
        if loo["n"]:
            print(f"Leave-one-out fault-class accuracy: triage alone {loo['triage_class_ok']}/{loo['n']}, "
                  f"triage + few-shot tie-break {loo['fewshot_class_ok']}/{loo['n']}")
    else:
        for e in load():
            print(f"{e['source']:<20} {e['fault_class']:<24} [{e['shape']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
