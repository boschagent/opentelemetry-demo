"""Brain: LLM investigator loop, reviewer agent, evidence-grounding validator, confidence fusion.  Owner: P1.

LLM protocol: plain JSON replies (works with ANY OpenAI-compatible endpoint, including free/local models
that have weak native tool-calling). Each step the model returns either
   {"thought": "...", "tool": "<name>", "args": {...}}      or      {"final": {...diagnosis...}}
Without an LLM configured the agent still works: the triage top suspect becomes the diagnosis.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .core import Backend, Config, Ledger
from .tools import infra, metrics, traces
from .triage import REMEDY, summarize

# ------------------------------------------------------------------ LLM client
class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = bool(cfg.llm_base_url and cfg.llm_model)
        self.calls = 0
        self.tokens = 0

    def chat(self, messages: list[dict], temperature: float = 0.1) -> str:
        url = self.cfg.llm_base_url.rstrip("/") + "/chat/completions"
        body = {"model": self.cfg.llm_model, "messages": messages, "temperature": temperature,
                "response_format": {"type": "json_object"}}
        headers = {"Content-Type": "application/json",  # explicit UA: Groq/Cloudflare blocks Python's default (HTTP 403 code 1010)
                   "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) oncall-agent/1.0"}
        if self.cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.llm_api_key}"
        last = ""
        for attempt in range(4):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    j = json.loads(r.read().decode())
                self.calls += 1
                self.tokens += (j.get("usage") or {}).get("total_tokens", 0)
                return j["choices"][0]["message"]["content"] or ""
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
                if e.code == 400 and "response_format" in body:   # provider without JSON mode
                    body.pop("response_format")
                    continue
                if e.code in (429, 500, 502, 503):
                    time.sleep(3 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(2)
        raise RuntimeError(f"LLM call failed: {last}")


def parse_json(text: str) -> dict | None:
    """Extract the first JSON object from a model reply (tolerates ```json fences and chatter)."""
    if not text:
        return None
    text = re.sub(r"```(json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


# ------------------------------------------------------------------ tools exposed to the LLM
TOOLS = {
    "service_red": (metrics.service_red, "Per-service request rate, error rate, p95 latency now vs baseline. args: {}"),
    "prom_query": (metrics.prom_query, "Any PromQL instant query. args: {\"query\": str}"),
    "find_error_traces": (traces.find_error_traces, "Where errors originate for a service (deepest failing span), "
                          "and calls that never reached the callee. args: {\"service\": str, \"lookback\": \"10m\"}"),
    "get_trace": (traces.get_trace, "Inspect one trace. args: {\"trace_id\": str}"),
    "latency_edges": (traces.latency_edges, "Caller->callee network gap (client time minus server time). "
                      "args: {\"service\": \"frontend\", \"lookback\": \"5m\"}"),
    "get_logs": (infra.get_logs, "Error/warn log lines of a container, deduplicated. "
                 "args: {\"service\": str, \"minutes\": 10, \"grep\": optional regex}"),
    "container_health": (infra.container_health, "State/health/restarts/OOM/limits of all containers. args: {}"),
    "container_stats": (infra.container_stats, "CPU and memory usage per container. args: {}"),
    "deploy_history": (infra.deploy_history, "Recent container creations/restarts and agent actions. args: {\"minutes\": 120}"),
}

FAULT_CLASSES = sorted(REMEDY)

SYSTEM = f"""You are an on-call SRE agent investigating an incident in the OpenTelemetry Astronomy Shop
(microservices: frontend, frontend-proxy, cart, checkout, payment, product-catalog, recommendation, ad,
shipping, quote, currency, email, accounting, fraud-detection, image-provider; infra: valkey-cart, kafka,
astronomy-db (postgres), load-generator).

RULES
- Diagnose ONLY from telemetry returned by tools. Never guess numbers. Every claim must cite evidence ids (ev-XXX)
  exactly as returned, and quoted numbers must appear in that evidence.
- Distinguish SYMPTOM from CAUSE: callers (frontend, checkout) often error because a downstream service fails.
  The root cause is where the failure ORIGINATES (deepest failing span, container down, resource exhausted, network gap).
- Prefer cheap, targeted tool calls. You have a budget of steps; do not repeat identical calls.
- If evidence is weak or contradictory, say so with low confidence. Escalating honestly beats a confident wrong answer.
- Feature-flag configuration is off-limits and is not available to you.

Reply with ONLY one JSON object, either
  {{"thought": "<short reasoning>", "tool": "<tool name>", "args": {{...}}}}
or, when done,
  {{"final": {{"component": "<service/container name>", "fault_class": "<one of {FAULT_CLASSES}>",
             "summary": "<2-3 sentence root cause>", "confidence": <0..1>,
             "evidence": [{{"id": "ev-XXX", "observation": "<what it shows, numbers copied exactly>"}}],
             "alternatives": ["<other plausible cause>"], "secondary": [{{"component": "...", "fault_class": "..."}}]}}}}
Use "secondary" only if there is independent evidence of a SECOND simultaneous fault.

TOOLS
""" + "\n".join(f"- {k}: {v[1]}" for k, v in TOOLS.items())


def run_tool(be: Backend, cfg: Config, ledger: Ledger, name: str, args: dict) -> str:
    if name not in TOOLS:
        return f"ERROR: unknown tool {name}. Valid: {list(TOOLS)}"
    fn = TOOLS[name][0]
    try:
        res = fn(be, cfg, **(args or {}))
    except TypeError as e:
        return f"ERROR: bad args for {name}: {e}"
    ev = ledger.add(name, args or {}, res.get("source", ""), res.get("summary", ""), res.get("data"))
    return f"[{ev.id}] ({name}) {ev.summary[:3000]}"


def investigate(be: Backend, cfg: Config, ledger: Ledger, llm: LLM, tri: dict, alert: str, log) -> tuple[dict | None, list]:
    """LLM tool loop. Returns (raw final diagnosis or None, transcript)."""
    tri_txt = summarize(tri)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"ALERT: {alert}\n\n{tri_txt}\n\nInvestigate. Verify the top suspect(s) with "
                                        f"targeted tools (traces, logs) before concluding."}]
    transcript = []
    seen = set()
    for step in range(cfg.max_llm_steps):
        try:
            reply = llm.chat(msgs)
        except Exception as e:
            log(f"   LLM error: {e}")
            transcript.append({"step": step, "error": str(e)})
            return None, transcript
        j = parse_json(reply)
        msgs.append({"role": "assistant", "content": reply[:4000]})
        if not j:
            msgs.append({"role": "user", "content": "Invalid reply. Return ONLY a JSON object as specified."})
            continue
        if "final" in j and isinstance(j["final"], dict):
            transcript.append({"step": step, "final": j["final"]})
            return j["final"], transcript
        tool, args = j.get("tool"), j.get("args") or {}
        key = json.dumps([tool, args], sort_keys=True)
        log(f"   step {step + 1}: {j.get('thought', '')[:140]} -> {tool}({args})")
        if key in seen:
            obs = "You already ran this exact call. Use a different tool or finish."
        else:
            seen.add(key)
            obs = run_tool(be, cfg, ledger, tool, args)
        transcript.append({"step": step, "thought": j.get("thought"), "tool": tool, "args": args, "obs": obs[:1500]})
        left = cfg.max_llm_steps - step - 1
        msgs.append({"role": "user", "content": f"OBSERVATION:\n{obs}\n\n({left} steps left" +
                     ("; you MUST return final now)" if left <= 1 else ")")})
    msgs.append({"role": "user", "content": "Budget exhausted. Return your final JSON now."})
    try:
        j = parse_json(llm.chat(msgs))
        if j and isinstance(j.get("final"), dict):
            return j["final"], transcript
    except Exception:
        pass
    return None, transcript


REVIEWER = """You are a skeptical senior SRE reviewing a junior agent's incident diagnosis.
Try to DISPROVE it using only the evidence shown. Check: is the named component the origin or merely a symptom?
Do the cited numbers support the claim? Is a different suspect better supported?
Reply ONLY JSON: {"verdict": "agree"|"disagree"|"uncertain", "reason": "<1-3 sentences>",
"better_component": "<name or null>"}"""


def review(llm: LLM, diag: dict, ledger: Ledger, tri: dict) -> dict:
    ev_txt = "\n".join(f"[{e['id']}] {ledger.items[e['id']].summary[:1500]}" for e in diag.get("evidence", [])
                       if e.get("id") in ledger.items)
    msgs = [{"role": "system", "content": REVIEWER},
            {"role": "user", "content": f"DIAGNOSIS:\n{json.dumps({k: diag.get(k) for k in ('component', 'fault_class', 'summary', 'evidence')}, indent=1)}"
                                        f"\n\nCITED EVIDENCE:\n{ev_txt}\n\nOTHER TRIAGE SUSPECTS:\n{summarize(tri)}"}]
    try:
        j = parse_json(llm.chat(msgs)) or {}
    except Exception as e:
        return {"verdict": "unavailable", "reason": str(e)[:200], "better_component": None}
    v = str(j.get("verdict", "uncertain")).lower()
    return {"verdict": v if v in ("agree", "disagree", "uncertain") else "uncertain",
            "reason": str(j.get("reason", ""))[:500], "better_component": j.get("better_component")}


# ------------------------------------------------------------------ grounding validator
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(s: str) -> set[str]:
    return set(_NUM.findall(s or ""))


def _num_in(n: str, text: str, text_nums: set[str]) -> bool:
    if n in text_nums or n in text:
        return True
    try:
        v = float(n)
    except ValueError:
        return False
    for t in text_nums:
        try:
            tv = float(t)
        except ValueError:
            continue
        if abs(tv - v) <= max(0.051, abs(v) * 0.01) or abs(tv * 100 - v) <= 0.11:  # rounding / ratio->percent
            return True
    return False


def validate_evidence(evidence: list, ledger: Ledger) -> tuple[list, list]:
    """Keep only evidence whose id exists AND whose numbers appear in that ledger entry."""
    good, rejected = [], []
    for e in evidence or []:
        if not isinstance(e, dict):
            rejected.append({"item": str(e)[:100], "why": "not an object"})
            continue
        eid, obs = str(e.get("id", "")), str(e.get("observation", ""))
        if eid not in ledger.items:
            rejected.append({"item": eid, "why": "evidence id does not exist"})
            continue
        text = ledger.text_of(eid)
        tnums = _numbers(text)
        bad = [n for n in _numbers(obs) if not _num_in(n, text, tnums) and n not in eid]
        if bad:
            rejected.append({"item": eid, "why": f"numbers not found in evidence: {bad[:5]}"})
            continue
        good.append({"id": eid, "tool": ledger.items[eid].tool, "source": ledger.items[eid].source, "observation": obs})
    return good, rejected


# ------------------------------------------------------------------ decision
def decide(cfg: Config, tri: dict, raw: dict | None, ledger: Ledger, reviewer: dict | None) -> dict:
    """Fuse LLM diagnosis + deterministic triage + reviewer into a final diagnosis with calibrated-ish confidence."""
    suspects = tri.get("suspects", [])
    top = suspects[0] if suspects else None
    notes = []
    if raw:
        comp = str(raw.get("component", "")).strip()
        cls = raw.get("fault_class") if raw.get("fault_class") in REMEDY else None
        try:
            llm_conf = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
        except Exception:
            llm_conf = 0.5
        evidence, rejected = validate_evidence(raw.get("evidence", []), ledger)
        match = next((s for s in suspects if s["component"] == comp), None)
        if cls is None:
            cls = match["fault_class"] if match else (top["fault_class"] if top else "app_errors")
            notes.append("LLM fault_class invalid; taken from triage")
        support = match["score"] if match else 0.0
        conf = 0.5 * llm_conf + 0.5 * support
        if not match:
            notes.append("LLM component not among triage suspects (unsupported by deterministic signals)")
        if rejected:
            notes.append(f"{len(rejected)} evidence item(s) rejected by grounding check")
            conf *= 0.85
        if not evidence and match:  # fall back to triage's own evidence
            evidence = [{"id": i, "tool": ledger.items[i].tool, "source": ledger.items[i].source,
                         "observation": "; ".join(match["rationale"])} for i in match["evidence"] if i in ledger.items]
        summary = str(raw.get("summary", ""))[:800]
        alternatives = raw.get("alternatives", []) or []
        secondary = raw.get("secondary", []) or []
        mode = "llm+triage"
    else:
        rejected = []
        if not top:
            return {"component": None, "fault_class": None, "summary": "No anomaly detected in telemetry.",
                    "confidence": 0.0, "evidence": [], "rejected_evidence": [], "alternatives": [], "secondary": [],
                    "notes": ["triage found nothing"], "mode": "triage-only", "escalate": True}
        comp, cls = top["component"], top["fault_class"]
        conf = top["score"] * 0.9
        rivals = [s for s in suspects[1:] if s["component"] != comp]
        same = [s for s in suspects[1:] if s["component"] == comp]
        if same:  # independent signals pointing at the same component raise confidence
            conf = 1 - (1 - conf) * (1 - 0.5 * same[0]["score"])
        second = rivals[0]["score"] if rivals else 0.0
        if second >= top["score"] - 0.1:
            conf *= 0.8
            notes.append("runner-up suspect nearly as strong")
        evidence = [{"id": i, "tool": ledger.items[i].tool, "source": ledger.items[i].source,
                     "observation": "; ".join(top["rationale"])} for i in top["evidence"] if i in ledger.items]
        summary = f"{comp}: {cls.replace('_', ' ')}. " + " ".join(top["rationale"][:3])
        alternatives = [f"{s['component']} [{s['fault_class']}] score={s['score']}" for s in suspects[1:4]]
        secondary = [{"component": s["component"], "fault_class": s["fault_class"]} for s in suspects[1:]
                     if s["score"] >= 0.6 and s["component"] != comp] if tri.get("combination") else []
        mode = "triage-only"
    if reviewer and reviewer.get("verdict") == "disagree":
        conf *= 0.6
        notes.append(f"reviewer disagrees: {reviewer.get('reason')}")
    elif reviewer and reviewer.get("verdict") == "uncertain":
        conf *= 0.85
    conf = round(conf, 3)
    return {"component": comp, "fault_class": cls, "summary": summary, "confidence": conf, "evidence": evidence,
            "rejected_evidence": rejected, "alternatives": alternatives, "secondary": secondary, "notes": notes,
            "mode": mode, "escalate": conf < cfg.confidence_threshold or not evidence}
