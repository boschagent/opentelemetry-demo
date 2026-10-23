"""Experiment grid over REAL recorded incidents: which thresholds / escalation cut-off score best?  Owner: P5.

    python eval/sweep.py

Scoring mirrors the brief: correct fix > honest escalation > confident wrong fix.
  correct (right component, not escalated) = +1.0 ; escalated = +0.3 ; wrong = -1.0 ; false alarm on a healthy bundle = -0.5
Writes eval/results/sweep_<ts>.json and prints the top configs. Pick the winner, then put its values into
oncall/triage.py THRESHOLDS and ONCALL_CONFIDENCE_THRESHOLD, and record the decision in docs/DECISIONS.md.
"""
import itertools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glob  # noqa: E402

from oncall import brain, triage  # noqa: E402
from oncall.core import Backend, Config, Ledger, atomic_write  # noqa: E402

GRID = {
    "err_abs": [0.02, 0.05, 0.1],
    "p95_ratio": [1.5, 2.0, 3.0],
    "rps_surge_ratio": [1.5, 2.0, 3.0],
    "log_err_min": [5, 10, 20],
    "conf": [0.4, 0.55, 0.7],
}


def main():
    labels = json.load(open("eval/labels.json", encoding="utf-8"))
    bundles = [(os.path.basename(os.path.dirname(b)), os.path.dirname(b)) for b in sorted(glob.glob("eval/bundles/*/calls.jsonl"))]
    bundles = [(n, d) for n, d in bundles if n in labels]
    if not bundles:
        print("No labelled bundles yet. Record incidents first (see eval/run_eval.py docstring).")
        return
    keys = list(GRID)
    results = []
    orig = dict(triage.THRESHOLDS)
    for combo in itertools.product(*[GRID[k] for k in keys]):
        params = dict(zip(keys, combo))
        triage.THRESHOLDS.update({k: v for k, v in params.items() if k != "conf"})
        cfg = Config.from_env()
        cfg.confidence_threshold = params["conf"]
        score, detail = 0.0, {}
        for name, d in bundles:
            lab = labels[name]
            L = Ledger()
            tri = triage.Triage(Backend("replay", d), cfg, L).run()
            dec = brain.decide(cfg, tri, None, L, None)
            healthy_truth = lab.get("component") in (None, "", "none")
            if healthy_truth:
                s, o = (0.0, "ok") if tri["healthy"] or dec["escalate"] else (-0.5, "false_alarm")
            elif dec["escalate"]:
                s, o = 0.3, "escalated"
            elif dec["component"] == lab["component"]:
                s, o = 1.0, "correct"
            else:
                s, o = -1.0, "WRONG"
            score += s
            detail[name] = o
        results.append({"params": params, "score": round(score, 2), "detail": detail})
    triage.THRESHOLDS.clear()
    triage.THRESHOLDS.update(orig)
    results.sort(key=lambda r: -r["score"])
    ts = time.strftime("%Y%m%d-%H%M%S")
    atomic_write(f"eval/results/sweep_{ts}.json", json.dumps(results, indent=1))
    print(f"{len(results)} configs x {len(bundles)} real incidents. Top 5:")
    for r in results[:5]:
        print(f"  score={r['score']:5}  {r['params']}  {r['detail']}")
    cur = next(r for r in results if all(r["params"].get(k) == orig.get(k) for k in keys if k != "conf"))
    print(f"\nCurrent defaults score: {cur['score']}  (max possible {len(bundles)})")


if __name__ == "__main__":
    main()
