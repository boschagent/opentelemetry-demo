"""Entry point: full incident loop.  Owner: P1.

Examples (run from the repo root):
  python -m oncall.run                       # investigate now, dry-run, asks approval
  python -m oncall.run --no-llm              # deterministic triage only (no API key needed)
  python -m oncall.run --execute             # real changes (still needs a typed 'yes')
  python -m oncall.run --watch               # detector: poll every 20s, start automatically on anomaly
  python -m oncall.run --mode record --bundle eval/bundles/payment1   # also record telemetry for replay
  python -m oncall.run --mode replay --bundle eval/bundles/payment1 --no-act   # offline, repeatable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import brain, fewshot, report
from .act import KillSwitch, RUNBOOK, ask_approval, call_action, check_kill, plan_action, verify
from .core import Backend, Config, Ledger, now_iso
from .triage import Triage, summarize


def investigate_incident(be: Backend, cfg: Config, alert: str, use_llm: bool, act: bool, incident_id: str | None = None,
                         input_fn=input, out=print) -> dict:
    t0 = time.time()
    iid = incident_id or time.strftime("INC-%Y%m%d-%H%M%S")
    timeline = [{"time": now_iso(t0), "event": f"alert received: {alert}"}]
    ledger = Ledger()
    llm = brain.LLM(cfg)
    rep = {"incident_id": iid, "status": "diagnosed", "detected_at": now_iso(t0), "alert": alert, "root_cause": {},
           "confidence": 0.0, "evidence": [], "proposed_action": None, "approval": None, "actions_taken": [],
           "verification": None, "timeline": timeline, "prevention": [], "reviewer": None, "escalation_reason": None}
    try:
        check_kill(cfg)
        out(f"[{iid}] triage ...")
        tri = Triage(be, cfg, ledger).run()
        out(summarize(tri))
        timeline.append({"time": now_iso(), "event": f"triage: {len(tri['suspects'])} suspects"
                         + (f", top={tri['suspects'][0]['component']}/{tri['suspects'][0]['fault_class']}" if tri['suspects'] else "")})
        raw, transcript, rv = None, [], None
        top = tri["suspects"][0] if tri["suspects"] else {}
        mem = report.similar_incidents(top.get("component"), top.get("fault_class"))
        tri["fewshot"] = [] if "fewshot" in os.environ.get("ONCALL_ABLATE", "") else \
            fewshot.neighbors(tri, exclude=iid[5:] if iid.startswith("EVAL-") else None)
        if tri["fewshot"]:
            out(fewshot.prompt_text(tri["fewshot"]))
        if use_llm and llm.enabled and not tri["healthy"]:
            out(f"[{iid}] LLM investigation ({cfg.llm_model}) ...")
            alert_ctx = alert
            if mem:
                alert_ctx += f"\nSimilar past incidents (memory, verify before trusting): {json.dumps(mem)[:800]}"
            if tri["fewshot"]:
                alert_ctx += "\n" + fewshot.prompt_text(tri["fewshot"])
            raw, transcript = brain.investigate(be, cfg, ledger, llm, tri, alert_ctx, out)
            timeline.append({"time": now_iso(), "event": f"LLM investigation finished ({len(transcript)} steps)"})
            if raw:
                rv = brain.review(llm, raw, ledger, tri)
                out(f"[{iid}] reviewer: {rv['verdict']} - {rv['reason'][:200]}")
                timeline.append({"time": now_iso(), "event": f"reviewer verdict: {rv['verdict']}"})
        diag = brain.decide(cfg, tri, raw, ledger, rv)
        rep.update({"root_cause": {"component": diag["component"], "fault_class": diag["fault_class"], "summary": diag["summary"],
                                   "alternatives": diag["alternatives"], "secondary": diag["secondary"], "mode": diag["mode"],
                                   "notes": diag["notes"]},
                    "confidence": diag["confidence"], "evidence": diag["evidence"], "reviewer": rv,
                    "rejected_evidence": diag["rejected_evidence"], "memory_hits": mem, "fewshot": tri.get("fewshot"),
                    "llm": {"model": cfg.llm_model if raw else None, "calls": llm.calls, "tokens": llm.tokens},
                    "investigation_transcript": transcript, "triage": tri["suspects"][:6],
                    "rca": (tri.get("signals") or {}).get("rca"), "combination": tri.get("combination"),
                    "ledger": ledger.to_list(), "redactions": ledger.redactions})
        timeline.append({"time": now_iso(), "event": f"diagnosis: {diag['component']} [{diag['fault_class']}] conf={diag['confidence']}"})
        if diag["fault_class"]:
            rep["prevention"] = [report.PREVENTION.get(diag["fault_class"], "")]
        if tri["healthy"] and not raw and tri["tool_errors"]:
            rep["status"] = "escalated"
            rep["escalation_reason"] = f"telemetry unavailable ({tri['tool_errors']}): cannot diagnose - check the demo/Prometheus/docker"
            return finish(rep, out)
        if tri["healthy"] and not raw:
            rep["status"] = "no_incident"
            rep["escalation_reason"] = "No anomaly found in telemetry; nothing to fix. Human should confirm the alert."
            return finish(rep, out)
        plan = plan_action(diag)
        rep["proposed_action"] = plan
        if diag["escalate"] or plan is None:
            rep["status"] = "escalated"
            rep["escalation_reason"] = ("confidence %.2f below threshold %.2f" % (diag["confidence"], cfg.confidence_threshold)
                                        if diag["escalate"] else f"no allowlisted remedy for fault class '{diag['fault_class']}'")
            timeline.append({"time": now_iso(), "event": "escalated to human: " + rep["escalation_reason"]})
            return finish(rep, out)
        if not act:
            return finish(rep, out)
        appr = ask_approval(diag, plan, cfg, input_fn=input_fn, out=out)
        rep["approval"] = appr
        timeline.append({"time": now_iso(), "event": f"human approval: {'APPROVED' if appr['approved'] else 'REJECTED'}"})
        if not appr["approved"]:
            rep["status"] = "rejected_by_human"
            return finish(rep, out)
        res = call_action(be, cfg, plan, appr["approver"], appr.get("proposal"))
        rep["actions_taken"].append({"ts": time.time(), "action": plan["action"], "params": plan["params"],
                                     "dry_run": cfg.dry_run, "response": res})
        timeline.append({"time": now_iso(), "event": f"action {plan['action']} -> ok={res.get('ok')} dry_run={cfg.dry_run}"})
        if not res.get("ok"):
            rep["status"] = "escalated"
            rep["escalation_reason"] = f"action failed: {res.get('error')}"
            return finish(rep, out)
        if cfg.dry_run:
            rep["status"] = "diagnosed"
            rep["escalation_reason"] = "dry-run: no change was made"
            return finish(rep, out)
        out(f"[{iid}] verifying recovery (up to {cfg.verify_minutes} min) ...")
        v = verify(be, cfg, diag, out=out)
        rep["verification"] = v
        timeline.append({"time": now_iso(), "event": f"verification: recovered={v['recovered']}"})
        if v["recovered"]:
            rep["status"] = "resolved"
            # learning from experience: a fix that was applied AND verified becomes a labelled few-shot example
            fewshot.add(tri, diag["fault_class"], iid, plan["action"], "resolved (verified)")
        else:
            inv = RUNBOOK[plan["action"]]["inverse"]
            prev = res.get("previous_users")
            if inv == "throttle_loadgen" and prev is not None:
                rb = call_action(be, cfg, {"action": inv, "params": {"users": prev, "spawn_rate": 1}}, appr["approver"])
                rep["actions_taken"].append({"ts": time.time(), "action": f"rollback:{inv}", "params": {"users": prev},
                                             "dry_run": cfg.dry_run, "response": rb})
                timeline.append({"time": now_iso(), "event": f"rolled back {plan['action']}"})
            rep["status"] = "failed_rolled_back" if inv else "escalated"
            rep["escalation_reason"] = "system did not recover after the approved action" + ("; action rolled back" if inv else
                                                                                             " (restart has no inverse)")
        return finish(rep, out)
    except KillSwitch as e:
        rep["status"] = "aborted"
        rep["escalation_reason"] = str(e)
        timeline.append({"time": now_iso(), "event": "KILL SWITCH - agent stopped"})
        return finish(rep, out)


def finish(rep: dict, out=print) -> dict:
    errs = report.validate_report(rep)
    rep["schema_errors"] = errs
    d = report.save(rep)
    out(f"\n==> {rep['incident_id']}: {rep['status'].upper()}  root cause={rep['root_cause'].get('component')} "
        f"[{rep['root_cause'].get('fault_class')}] conf={rep['confidence']}  report: {d}/report.md"
        + (f"  SCHEMA ERRORS: {errs}" if errs else ""))
    return rep


def watch(be: Backend, cfg: Config, args) -> None:
    print("Detector running: polling every 20s (Ctrl-C to stop; create KILL file to stop) ...")
    hits = 0
    while True:
        check_kill(cfg)
        tri = Triage(be, cfg, Ledger()).run()
        strong = [s for s in tri["suspects"] if s["score"] >= 0.6]
        hits = hits + 1 if strong else 0
        print(f"{now_iso()} suspects={[(s['component'], s['fault_class'], s['score']) for s in strong[:3]]} streak={hits}")
        if hits >= 2:
            s = strong[0]
            investigate_incident(be, cfg, f"auto-detected: {s['component']} {s['fault_class']} (score {s['score']})",
                                 not args.no_llm, not args.no_act)
            hits = 0
            print("Cooling down 120s ...")
            time.sleep(120)
        time.sleep(20)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AI on-call engineer")
    p.add_argument("--alert", default="manual trigger: on-call investigation requested")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-act", action="store_true", help="diagnose only")
    p.add_argument("--execute", action="store_true", help="really change the system (default is dry-run)")
    p.add_argument("--mode", default="live", choices=["live", "record", "replay"])
    p.add_argument("--bundle")
    p.add_argument("--id")
    p.add_argument("--web-approve", action="store_true", help="approve on the Action API web page instead of the terminal")
    a = p.parse_args(argv)
    cfg = Config.from_env()
    if a.execute:
        cfg.dry_run = False
    if a.web_approve:
        cfg.approve_mode = "web"
    be = Backend(a.mode, a.bundle)
    cmd = "python -m oncall.run " + " ".join(argv if argv is not None else sys.argv[1:])
    try:
        if a.watch:
            watch(be, cfg, a)
            return 0
        rep = investigate_incident(be, cfg, a.alert, not a.no_llm, not a.no_act, a.id)
        report.runlog({"command": cmd, "incident": rep["incident_id"], "purpose": a.alert[:60],
                       "result": f"{rep['status']} {rep['root_cause'].get('component')}/{rep['root_cause'].get('fault_class')} conf={rep['confidence']}",
                       "notes": f"mode={a.mode} llm={cfg.llm_model if not a.no_llm else 'off'} dry_run={cfg.dry_run}"})
    except KillSwitch:
        print("Kill switch engaged - stopped.")
    except KeyboardInterrupt:
        print("Interrupted by human - stopped. No further actions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
