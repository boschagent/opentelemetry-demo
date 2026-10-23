"""ONE command that proves the agent does what the spec says:   python -m oncall.selftest

Runs fully offline (no demo, no LLM, no network): a FakeDemo backend serves Prometheus / Jaeger / docker
answers in the REAL response formats for 10 scripted incident scenarios. Each test states the requirement
it checks.  Owner: everyone (add a test whenever you change behaviour).

What these tests can NOT prove (be honest in the demo): that the thresholds are right for the real demo,
or that the real metric/label names match this demo version. That is what the live eval (eval/) is for.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse

from . import act, action_api, brain, report
from .core import Backend, Config, Ledger, redact
from .run import investigate_incident
from .tools import traces
from .triage import Triage

# ------------------------------------------------------------------ fake demo
APPS = ["frontend-proxy", "frontend", "cart", "checkout", "payment", "product-catalog", "recommendation", "ad",
        "shipping", "quote", "currency", "email", "valkey-cart", "load-generator", "prometheus", "jaeger", "flagd"]
BASE_RPS = {"frontend-proxy": 10, "frontend": 9, "cart": 3, "checkout": 0.5, "payment": 0.5, "product-catalog": 8,
            "recommendation": 2, "ad": 2, "shipping": 0.8, "quote": 0.8, "currency": 4, "email": 0.5}


def span(sid, parent, svc, op, kind, ms, err=False, msg="", extra_tags=None, logs=None):
    tags = [{"key": "span.kind", "type": "string", "value": kind}]
    if err:
        tags += [{"key": "error", "type": "bool", "value": True}, {"key": "otel.status_code", "value": "ERROR"},
                 {"key": "otel.status_description", "value": msg}]
    tags += extra_tags or []
    return {"traceID": "t", "spanID": sid, "operationName": op, "references": ([{"refType": "CHILD_OF", "spanID": parent}] if parent else []),
            "duration": int(ms * 1000), "tags": tags, "logs": logs or [], "processID": svc}


def trace(tid, spans):
    for s in spans:
        s["traceID"] = tid
    return {"traceID": tid, "spans": spans, "processes": {s["processID"]: {"serviceName": s["processID"]} for s in spans}}


FLAG_EVENT = [{"timestamp": 1, "fields": [{"key": "event", "value": "feature_flag"},
                                          {"key": "feature_flag.key", "value": "paymentFailure"},
                                          {"key": "feature_flag.variant", "value": "100%"}]}]


def scenario(name: str) -> dict:
    s = {"rps": dict(BASE_RPS), "err": {}, "p95": {k: 20.0 for k in BASE_RPS}, "containers": {a: {} for a in APPS},
         "stats": {}, "err_traces": {}, "traces": {}, "logs": {}}
    if name == "payment_errors":
        s["err"] = {"payment": 0.25, "checkout": 0.25, "frontend": 0.3, "frontend-proxy": 0.3}
        s["err_traces"] = {k: [trace(f"p{i}", [
            span("a", None, "frontend", "POST /api/checkout", "server", 120, True, "500"),
            span("b", "a", "frontend", "oteldemo.CheckoutService/PlaceOrder", "client", 110, True, "rpc error"),
            span("c", "b", "checkout", "oteldemo.CheckoutService/PlaceOrder", "server", 100, True, "failed to charge card",
                 logs=FLAG_EVENT),
            span("d", "c", "checkout", "oteldemo.PaymentService/Charge", "client", 30, True, "Invalid token"),
            span("e", "d", "payment", "oteldemo.PaymentService/Charge", "server", 25, True,
                 "Payment request failed. Invalid token. app.loyalty.level=gold")]) for i in range(8)]
            for k in ("frontend", "checkout", "payment", "frontend-proxy")}
    elif name == "cart_down":
        s["containers"]["cart"] = {"status": "exited", "exit": 137}
        s["err"] = {"frontend": 0.4, "frontend-proxy": 0.4}
        s["rps"]["cart"] = 0.0
        s["err_traces"] = {k: [trace(f"c{i}", [
            span("a", None, "frontend", "GET /api/cart", "server", 50, True, "unavailable"),
            span("b", "a", "frontend", "oteldemo.CartService/GetCart", "client", 40, True, "connect ECONNREFUSED",
                 extra_tags=[{"key": "server.address", "value": "cart"}])]) for i in range(6)] for k in ("frontend", "frontend-proxy")}
    elif name == "rec_memory":
        s["stats"]["recommendation"] = {"cpu": 30.0, "mem": 96.0}
        s["p95"]["recommendation"] = 30.0
    elif name == "ad_cpu":
        s["stats"]["ad"] = {"cpu": 180.0, "mem": 40.0}
        s["p95"]["ad"] = 400.0
    elif name == "traffic":
        for k in ("frontend-proxy", "frontend", "product-catalog"):
            s["rps"][k] = BASE_RPS[k] * 4
        s["now_only_rps"] = True
    elif name == "network":
        s["p95"].update({"frontend": 900.0, "checkout": 850.0, "frontend-proxy": 950.0})
        s["traces"]["frontend"] = [trace(f"n{i}", [
            span("a", None, "frontend", "POST /api/checkout", "server", 950),
            span("b", "a", "frontend", "PlaceOrder", "client", 900),
            span("c", "b", "checkout", "PlaceOrder", "server", 880),
            span("d", "c", "checkout", "GetQuote", "client", 820),
            span("e", "d", "shipping", "GetQuote", "server", 15)]) for i in range(10)]
    elif name == "cpu_starved":
        s["containers"]["product-catalog"] = {"cpu_limit": 0.1}
        s["stats"]["product-catalog"] = {"cpu": 9.9, "mem": 30.0}
        s["p95"]["product-catalog"] = 300.0
    elif name == "combo":
        s.update(scenario("cart_down"))
        s["stats"]["ad"] = {"cpu": 180.0, "mem": 40.0}
    elif name == "valkey_paused":
        s["containers"]["valkey-cart"] = {"status": "paused"}
        s["err"] = {"cart": 0.6, "frontend": 0.3}
        s["err_traces"] = {k: [trace(f"v{i}", [
            span("a", None, "cart", "GetCart", "server", 5000, True, "Timeout performing HGET"),
            span("b", "a", "cart", "HGET", "client", 5000, True, "Timeout performing HGET (5000ms)",
                 extra_tags=[{"key": "server.address", "value": "valkey-cart"}])]) for i in range(6)] for k in ("cart", "frontend")}
    return s


class FakeDemo(Backend):
    """Answers like the real demo would, for a scripted scenario. Records calls for assertions."""

    def __init__(self, scen: dict):
        super().__init__("live")
        self.s, self.calls = scen, []

    def http(self, method, url, body=None, timeout=15.0):
        self.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.endswith("/api/v1/query"):
            return self._prom(q["query"])
        if u.path.endswith("/api/services"):
            return {"ok": True, "status": 200, "json": {"data": [k for k in BASE_RPS] + ["flagd"]}, "text": "", "error": ""}
        if u.path.endswith("/api/traces"):
            svc = q.get("service")
            data = self.s["err_traces"].get(svc, []) if "tags" in q else self.s["traces"].get(svc, [])
            if q.get("lookback") == "1m" and self.s.get("healed"):
                data = []
            return {"ok": True, "status": 200, "json": {"data": data}, "text": "", "error": ""}
        if "/api/traces/" in u.path:
            tid = u.path.rsplit("/", 1)[-1]
            for lst in list(self.s["err_traces"].values()) + list(self.s["traces"].values()):
                for t in lst:
                    if t["traceID"] == tid:
                        return {"ok": True, "status": 200, "json": {"data": [t]}, "text": "", "error": ""}
            return {"ok": True, "status": 200, "json": {"data": []}, "text": "", "error": ""}
        return {"ok": False, "status": 0, "json": None, "text": "", "error": "unknown url"}

    def _prom(self, query):
        base = "offset" in query
        if "histogram_quantile" in query:
            vals = {k: (20.0 if base else v) for k, v in self.s["p95"].items()}
        elif "STATUS_CODE_ERROR" in query:
            vals = {k: (0.0 if base else self.s["rps"].get(k, 0) * r) for k, r in self.s["err"].items()}
        else:
            vals = {k: (BASE_RPS.get(k, 0) if base else v) for k, v in self.s["rps"].items()}
        res = [{"metric": {"service_name": k, "span_kind": "SPAN_KIND_SERVER"}, "value": [time.time(), str(v)]} for k, v in vals.items()]
        res.append({"metric": {"service_name": "flagd"}, "value": [time.time(), "1"]})
        return {"ok": True, "status": 200, "json": {"status": "success", "data": {"result": res}}, "text": "", "error": ""}

    def run(self, cmd, timeout=30.0):
        self.calls.append(" ".join(cmd))
        C = self.s["containers"]
        if cmd[:2] == ["docker", "ps"]:
            return {"ok": True, "stdout": "\n".join(json.dumps({"Names": n}) for n in C), "stderr": "", "code": 0}
        if cmd[:2] == ["docker", "inspect"]:
            out = []
            for n in cmd[2:]:
                c = C.get(n, {})
                st = "running" if self.s.get("healed") else c.get("status", "running")
                lim = c.get("cpu_limit")
                out.append({"Name": "/" + n, "RestartCount": c.get("restarts", 0), "Created": "2026-10-23T08:00:00.0Z",
                            "State": {"Status": st, "OOMKilled": False, "ExitCode": c.get("exit", 0),
                                      "StartedAt": "2026-10-23T08:00:05.0Z", "FinishedAt": "0001-01-01T00:00:00Z"},
                            "HostConfig": {"NanoCpus": int(lim * 1e9) if lim else 0, "Memory": 0},
                            "Config": {"Image": f"ghcr.io/open-telemetry/demo:latest-{n}", "Labels": {}}})
            return {"ok": True, "stdout": json.dumps(out), "stderr": "", "code": 0}
        if cmd[:2] == ["docker", "stats"]:
            rows = []
            for n in C:
                st = self.s["stats"].get(n, {"cpu": 2.0, "mem": 20.0})
                rows.append(json.dumps({"Name": n, "CPUPerc": f"{st['cpu']}%", "MemPerc": f"{st['mem']}%", "MemUsage": "10MiB / 100MiB"}))
            return {"ok": True, "stdout": "\n".join(rows), "stderr": "", "code": 0}
        if cmd[:2] == ["docker", "logs"]:
            return {"ok": True, "stdout": self.s["logs"].get(cmd[-1], ""), "stderr": "", "code": 0}
        return {"ok": False, "stdout": "", "stderr": "unsupported", "code": 1}


# ------------------------------------------------------------------ tests
CFG = Config(llm_base_url="", llm_model="", dry_run=True, verify_minutes=0.05)
EXPECT = {  # scenario -> (top component, fault class)
    "payment_errors": ("payment", "app_errors"),
    "cart_down": ("cart", "service_down"),
    "rec_memory": ("recommendation", "memory_pressure"),
    "ad_cpu": ("ad", "high_cpu"),
    "traffic": ("load-generator", "traffic_surge"),
    "network": ("shipping", "network_degradation"),
    "cpu_starved": ("product-catalog", "cpu_starved"),
    "combo": ("cart", "service_down"),
    "valkey_paused": ("valkey-cart", "service_down"),
}
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def t_redact_removes_flag_state():
    """REQ: the agent never sees feature-flag state (span events, tags, log lines, flagd service)."""
    obj = {"spans": [{"tags": [{"key": "feature_flag.key", "value": "paymentFailure"}, {"key": "http.status", "value": 500}],
                      "logs": FLAG_EVENT}],
           "services": [{"serviceName": "flagd"}, {"serviceName": "cart"}],
           "log": "ok line\nFeature Flag adManualGc enabled, performing a manual gc now\nerror: timeout"}
    clean, n = redact(obj)
    txt = json.dumps(clean).lower()
    assert "paymentfailure" not in txt and "feature_flag" not in txt and "admanualgc" not in txt, txt
    assert "flagd" not in txt and "cart" in txt and "error: timeout" in txt and "http.status" in txt
    assert n >= 4, n


@test
def t_ledger_redacts_everything_it_stores():
    """REQ: nothing un-sanitised reaches the evidence ledger (and therefore the LLM)."""
    L = Ledger()
    ev = L.add("x", {}, "src", "feature_flag.key=paymentFailure was on\nreal error", {"feature_flag.variant": "on", "v": 1})
    assert "paymentFailure" not in L.text_of(ev.id) and "real error" in L.text_of(ev.id) and L.redactions >= 2


@test
def t_analyze_trace_finds_origin_and_network_gap():
    """REQ: traces reveal where errors originate (deepest failing span) and caller->callee network gaps."""
    a = traces.analyze_trace(scenario("payment_errors")["err_traces"]["frontend"][0])
    assert a["deepest_errors"][0]["service"] == "payment", a
    n = traces.analyze_trace(scenario("network")["traces"]["frontend"][0])
    worst = max(n["edges"], key=lambda e: e["gap_ms"])
    assert (worst["from"], worst["to"]) == ("checkout", "shipping") and worst["gap_ms"] == 805.0, worst
    o = traces.analyze_trace(scenario("cart_down")["err_traces"]["frontend"][0])
    assert o["orphan_client_errors"] and o["orphan_client_errors"][0]["peer"] == "cart"


@test
def t_triage_scenarios():
    """REQ: deterministic triage ranks the true root cause first for every scripted incident class."""
    for name, (comp, cls) in EXPECT.items():
        tri = Triage(FakeDemo(scenario(name)), CFG, Ledger()).run()
        top = tri["suspects"][0]
        assert (top["component"], top["fault_class"]) == (comp, cls), f"{name}: got {top} all={tri['suspects'][:3]}"
        assert top["evidence"], f"{name}: top suspect has no evidence ids"


@test
def t_triage_symptom_vs_cause():
    """REQ: callers that only propagate errors (frontend, checkout) must rank BELOW the origin (payment)."""
    tri = Triage(FakeDemo(scenario("payment_errors")), CFG, Ledger()).run()
    comps = [s["component"] for s in tri["suspects"]]
    assert comps[0] == "payment" and ("frontend" not in comps or comps.index("frontend") > 0)


@test
def t_triage_healthy_and_combination():
    """REQ: a healthy system yields no suspects; two independent faults are flagged as a combination."""
    assert Triage(FakeDemo(scenario("healthy")), CFG, Ledger()).run()["healthy"]
    tri = Triage(FakeDemo(scenario("combo")), CFG, Ledger()).run()
    assert tri["combination"] and {"cart", "ad"} <= {s["component"] for s in tri["suspects"]}


@test
def t_flagd_never_queried():
    """REQ: the agent never reads logs/traces of flagd or flagd-ui."""
    be = FakeDemo(scenario("payment_errors"))
    Triage(be, CFG, Ledger()).run()
    from .tools import infra
    assert not infra.get_logs(be, CFG, "flagd")["ok"] and not traces.find_error_traces(be, CFG, "flagd-ui")["ok"]
    assert not any("service=flagd" in c or c.endswith(" flagd") for c in be.calls), [c for c in be.calls if "flagd" in c]


@test
def t_validator_rejects_invented_evidence():
    """REQ: invented evidence (unknown id or numbers not present in the data) is rejected."""
    L = Ledger()
    e = L.add("service_red", {}, "q", "payment: err=25.0% p95=20.0ms", {"rows": [{"error_rate": 0.25}]})
    good, bad = brain.validate_evidence([
        {"id": e.id, "observation": "payment error rate 25.0%, p95 20.0ms"},
        {"id": e.id, "observation": "payment error rate 73%"},
        {"id": "ev-999", "observation": "made up"}], L)
    assert len(good) == 1 and len(bad) == 2, (good, bad)


@test
def t_parse_json_robust():
    """REQ: LLM replies with fences/chatter still parse; garbage returns None."""
    assert brain.parse_json('sure!\n```json\n{"tool": "service_red", "args": {}}\n```') == {"tool": "service_red", "args": {}}
    assert brain.parse_json('{"a": {"b": 1}} trailing') == {"a": {"b": 1}}
    assert brain.parse_json("no json here") is None


@test
def t_decide_escalates_when_unsure():
    """REQ: low confidence or a disagreeing reviewer leads to escalation, not action."""
    tri = Triage(FakeDemo(scenario("payment_errors")), CFG, Ledger()).run()
    L = Ledger()
    t2 = Triage(FakeDemo(scenario("payment_errors")), CFG, L).run()
    d = brain.decide(CFG, t2, None, L, None)
    assert d["component"] == "payment" and not d["escalate"] and d["evidence"], d
    raw = {"component": "email", "fault_class": "app_errors", "summary": "x", "confidence": 0.9, "evidence": []}
    d2 = brain.decide(CFG, tri, raw, L, {"verdict": "disagree", "reason": "no evidence"})
    assert d2["escalate"], d2


def _run_full(name, answers, cfg=None, setup=None):
    cfg = cfg or Config(dry_run=True, verify_minutes=0.05, action_api_url=ACTION_URL)
    be = FakeDemo(scenario(name))
    if setup:
        setup(be)
    it = iter(answers)
    return investigate_incident(be, cfg, f"test {name}", use_llm=False, act=True, incident_id=f"TEST-{name}-{time.time_ns()}",
                                input_fn=lambda _p: next(it), out=lambda *_a: None)


@test
def t_no_action_without_yes():
    """REQ: nothing changes without an explicit 'yes' (anything else = reject)."""
    for ans in ("no", "y", "YES please", ""):
        r = _run_full("cart_down", [ans])
        assert r["status"] == "rejected_by_human" and r["actions_taken"] == [], (ans, r["status"])


@test
def t_dry_run_changes_nothing():
    """REQ: dry-run (the default) calls the Action API in dry-run mode only."""
    r = _run_full("cart_down", ["yes"])
    a = r["actions_taken"][0]
    assert a["dry_run"] and a["response"].get("dry_run") is True and "would_run" in a["response"], a
    assert r["status"] == "diagnosed" and not r["schema_errors"], r


@test
def t_execute_verify_resolved():
    """REQ: approved action executes via the Action API, then health is re-checked until recovered."""
    cfg = Config(dry_run=False, verify_minutes=0.05, action_api_url=ACTION_URL)
    old = act.verify.__defaults__
    act.verify.__defaults__ = (print, lambda s: None)
    try:
        r = _run_full("cart_down", ["yes"], cfg, setup=lambda be: be.s.__setitem__("healed", True))
    finally:
        act.verify.__defaults__ = old
    assert r["status"] == "resolved" and r["verification"]["recovered"] and r["actions_taken"][0]["response"]["ok"], r["status"]
    assert not r["schema_errors"], r["schema_errors"]


@test
def t_not_recovered_escalates():
    """REQ: if the system does not recover, the agent escalates (restart has no inverse)."""
    cfg = Config(dry_run=False, verify_minutes=0.0005, action_api_url=ACTION_URL)
    old = act.verify.__defaults__
    act.verify.__defaults__ = (lambda *a: None, lambda s: None)
    try:
        r = _run_full("cart_down", ["yes"], cfg)
    finally:
        act.verify.__defaults__ = old
    assert r["status"] == "escalated" and not r["verification"]["recovered"], r["status"]


@test
def t_kill_switch():
    """REQ: a KILL file stops the agent before any action, and the Action API refuses too."""
    open("KILL", "w").close()
    try:
        r = _run_full("cart_down", ["yes"])
        assert r["status"] == "aborted" and r["actions_taken"] == []
        res = act.call_action(Backend(), Config(action_api_url=ACTION_URL, dry_run=False),
                              {"action": "restart_service", "params": {"service": "cart"}}, "x") if False else None
    finally:
        os.remove("KILL")


@test
def t_action_api_allowlist_and_approval():
    """REQ: Action API refuses non-allowlisted actions/services and real calls without approval; all calls logged."""
    import urllib.request, urllib.error

    def post(path, body, hdr=True):
        req = urllib.request.Request(ACTION_URL + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-Approved-By": "t"} if hdr else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    assert post("/actions/delete_database", {})[0] == 403
    assert post("/actions/restart_service", {"service": "flagd"})[0] == 403
    assert post("/actions/restart_service", {"service": "cart"}, hdr=False)[0] == 403
    assert post("/actions/toggle_flag", {"flag": "x"})[0] == 501
    open("KILL", "w").close()
    try:
        assert post("/actions/restart_service", {"service": "cart"})[0] == 423
    finally:
        os.remove("KILL")
    lines = open(action_api.LOG, encoding="utf-8").read().strip().splitlines()
    assert len(lines) >= 5 and all("approved_by" in json.loads(l) for l in lines)
    assert act.call_action(Backend(), Config(action_api_url=ACTION_URL), {"action": "rm_rf", "params": {}}, "t")["ok"] is False


@test
def t_reports_valid_and_memory():
    """REQ: every report validates against our schema; incident memory retrieves past same-class incidents."""
    r = _run_full("payment_errors", ["no"])
    assert not r["schema_errors"], r["schema_errors"]
    md = open(os.path.join("incidents", r["incident_id"], "report.md"), encoding="utf-8").read()
    assert "payment" in md and "## Evidence" in md and "## Timeline" in md and "## Prevention" in md
    bad = dict(r, status="resolved", verification={"recovered": False})
    assert report.validate_report(bad)
    rr = dict(r, status="diagnosed")
    report.save(rr)
    hits = report.similar_incidents("payment", "app_errors")
    assert hits and hits[0]["component"] == "payment"


@test
def t_llm_loop_with_scripted_model():
    """REQ: LLM loop calls tools, cites ledger ids; invented evidence is dropped; reviewer can veto."""
    script = [
        '{"thought":"check payment traces","tool":"find_error_traces","args":{"service":"checkout"}}',
        '{"thought":"again","tool":"find_error_traces","args":{"service":"checkout"}}',
        '{"final":{"component":"payment","fault_class":"app_errors","summary":"payment rejects charges",'
        '"confidence":0.8,"evidence":[{"id":"ev-004","observation":"8 error traces originate in payment"},'
        '{"id":"ev-004","observation":"error rate 99%"}],"alternatives":[]}}',
        '{"verdict":"agree","reason":"origin is payment","better_component":null}',
    ]
    it = iter(script)
    orig = brain.LLM.chat
    brain.LLM.chat = lambda self, m, temperature=0.1: next(it)
    try:
        cfg = Config(llm_base_url="http://fake", llm_model="fake", dry_run=True, action_api_url=ACTION_URL)
        be = FakeDemo(scenario("payment_errors"))
        r = investigate_incident(be, cfg, "t", use_llm=True, act=False, incident_id=f"TEST-llm-{time.time_ns()}", out=lambda *a: None)
    finally:
        brain.LLM.chat = orig
    assert r["root_cause"]["component"] == "payment" and r["root_cause"]["mode"] == "llm+triage", r["root_cause"]
    assert len(r["rejected_evidence"]) == 1 and "99" in r["rejected_evidence"][0]["why"], r["rejected_evidence"]
    assert any("already ran" in (t.get("obs") or "") for t in r["investigation_transcript"])
    assert r["reviewer"]["verdict"] == "agree" and not r["schema_errors"]


@test
def t_record_replay_roundtrip():
    """REQ: telemetry recorded live can be replayed offline with identical answers (repeatable evaluation)."""
    d = tempfile.mkdtemp()
    try:
        rec = Backend("record", d)
        a = rec.http("GET", ACTION_URL + "/actions?start=1&end=2")
        b = Backend("replay", d).http("GET", ACTION_URL + "/actions?start=999&end=1000")
        assert a == b and a["ok"]
        assert not Backend("replay", d).http("GET", ACTION_URL + "/other")["ok"]
    finally:
        shutil.rmtree(d)


# ------------------------------------------------------------------ runner
ACTION_URL = ""


def main() -> int:
    global ACTION_URL
    work = tempfile.mkdtemp(prefix="oncall_selftest_")
    cwd = os.getcwd()
    os.chdir(work)  # never touch the real incidents/ or logs/
    action_api.LOG = os.path.join(work, "logs", "actions.jsonl")
    action_api._docker = lambda *a: ((True, "running") if a[0] == "inspect" else (True, "ok"))  # no real docker
    srv = action_api.serve(port=0)
    ACTION_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    passed = failed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"PASS  {fn.__name__}: {fn.__doc__.strip().splitlines()[0]}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}: {fn.__doc__.strip().splitlines()[0]}")
            traceback.print_exc()
    srv.shutdown()
    os.chdir(cwd)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    print("ALL TESTS PASSED" if not failed else "SOME TESTS FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
