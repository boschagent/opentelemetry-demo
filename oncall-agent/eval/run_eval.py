"""Offline evaluation over recorded incident bundles -> scoreboard + calibration table.  Owner: P5.

How bundles are made (on the laptop running the demo, during an injected incident):
    python -m oncall.run --mode record --bundle eval/bundles/<name> --no-act --no-llm
Then write the TRUE answer (from what you injected, never from the agent) into eval/labels.json:
    {"<name>": {"component": "payment", "fault_class": "app_errors", "injected": "paymentFailure 100% via flag UI"}}
Run:
    python eval/run_eval.py              # deterministic (triage-only) scoring, fully repeatable
    python eval/run_eval.py --llm        # also score the LLM path (needs key; not bit-for-bit repeatable)
Outputs: eval/results/<timestamp>_<gitsha>.json, eval/SCOREBOARD.md, a row in docs/RUNLOG.md
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from oncall import report  # noqa: E402
from oncall.core import Backend, Config, atomic_write  # noqa: E402
from oncall.run import investigate_incident  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    labels = json.load(open("eval/labels.json", encoding="utf-8")) if os.path.exists("eval/labels.json") else {}
    cfg = Config.from_env()
    rows = []
    for b in sorted(glob.glob("eval/bundles/*/calls.jsonl")):
        name = os.path.basename(os.path.dirname(b))
        lab = labels.get(name)
        rep = investigate_incident(Backend("replay", os.path.dirname(b)), cfg, f"eval {name}", a.llm, act=False,
                                   incident_id=f"EVAL-{name}", out=lambda *x: None)
        rc = rep["root_cause"]
        comp_ok = bool(lab) and rc.get("component") == lab["component"]
        cls_ok = bool(lab) and rc.get("fault_class") == lab.get("fault_class")
        esc = rep["status"] in ("escalated", "no_incident")
        # outcome scoring mirrors the brief: correct > honest escalation > confident wrong
        outcome = ("correct" if comp_ok and not esc else "escalated" if esc else "WRONG") if lab else "unlabelled"
        rows.append({"bundle": name, "label": lab, "pred_component": rc.get("component"), "pred_class": rc.get("fault_class"),
                     "confidence": rep["confidence"], "status": rep["status"], "component_ok": comp_ok, "class_ok": cls_ok,
                     "outcome": outcome, "schema_errors": rep.get("schema_errors"), "evidence_n": len(rep["evidence"])})
    n = len([r for r in rows if r["label"]])
    summ = {k: sum(r["outcome"] == k for r in rows) for k in ("correct", "escalated", "WRONG", "unlabelled")}
    cal = {}
    for r in rows:
        if r["label"]:
            b = f"{int(r['confidence'] * 5) / 5:.1f}-{int(r['confidence'] * 5) / 5 + 0.2:.1f}"
            c = cal.setdefault(b, [0, 0])
            c[0] += 1
            c[1] += r["component_ok"]
    sha = report.git_sha()
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = {"ts": ts, "git": sha, "llm": a.llm, "summary": summ, "n_labelled": n, "calibration": cal, "rows": rows,
           "threshold": cfg.confidence_threshold}
    atomic_write(f"eval/results/{ts}_{sha}.json", json.dumps(out, indent=2, default=str))
    md = [f"# Scoreboard ({ts}, git {sha}, llm={a.llm}, threshold={cfg.confidence_threshold})", "",
          f"**{summ['correct']}/{n} correct, {summ['escalated']} escalated, {summ['WRONG']} wrong**", "",
          "| bundle | truth | predicted | conf | status | outcome |", "|---|---|---|---|---|---|"]
    for r in rows:
        t = f"{r['label']['component']}/{r['label'].get('fault_class')}" if r["label"] else "-"
        md.append(f"| {r['bundle']} | {t} | {r['pred_component']}/{r['pred_class']} | {r['confidence']} | {r['status']} | {r['outcome']} |")
    md += ["", "## Calibration (confidence bucket -> accuracy)", "| bucket | n | accuracy |", "|---|---|---|"]
    md += [f"| {k} | {v[0]} | {v[1] / v[0]:.0%} |" for k, v in sorted(cal.items())]
    atomic_write("eval/SCOREBOARD.md", "\n".join(md) + "\n")
    report.runlog({"command": "python eval/run_eval.py" + (" --llm" if a.llm else ""), "incident": f"{len(rows)} bundles",
                   "purpose": "offline evaluation", "result": f"{summ['correct']}/{n} correct, {summ['WRONG']} wrong"})
    print("\n".join(md))


if __name__ == "__main__":
    main()
