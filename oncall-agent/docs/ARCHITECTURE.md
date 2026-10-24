# Architecture (one page)

One incident, top to bottom. Everything above the dashed line only reads. The only path that changes the shop starts with a human.

```
 ALERT    demo page "Investigate", `python -m oncall.run`, or the detector (`--watch`: triage every 20 s, fires after
          2 anomalous polls in a row). The agent is told only that an alert fired, never which fault was injected.
   |
 EYES     read-only tools (oncall/tools/); every result -> core.redact() -> evidence ledger (ev-001, ev-002, ...)
   metrics.py  Prometheus: per-service rate, errors, p95 now vs the healthy baseline; 10 min of history;
               free PromQL for the LLM (feature-flag metrics refused, empty results say NO DATA)
   traces.py   Jaeger: where errors start (deepest failing span), calls that never reached the callee,
               client-minus-server time (network gap), caller -> callee graph learned from live traces
   infra.py    Docker: state, exit code, OOM, restarts, CPU and memory limits (the demo's compose project only);
               container logs (never flagd); recent creations, restarts and our own actions
   |
 BRAIN
   1 triage.py   rules -> ranked suspects {component, fault_class, score, rationale, evidence ids};
                 a failing caller of a dead dependency counts as a symptom (score x0.5)
   2 rca.py      graph RCA: robust z-score (median/MAD) of each series vs its own past, onset (who broke first),
                 random walk with restart on the learned call graph; a second independent root = combined incident
   3 fewshot.py  past incidents as 24-number fingerprints with no service names; cosine nearest neighbours;
                 tie-break only between classes the evidence supports, never against a Docker fact; verified fixes are added
   4 brain.py    LLM role 1, investigator: JSON tool loop, at most 10 steps, 10 read-only tools, must cite ev ids
                 grounding validator: the id must exist and its numbers must appear in it; an empty result is not evidence
                 LLM role 2, reviewer: a separate skeptical call that sees only the diagnosis and its evidence
                 code checks both: a summary that contradicts Docker is replaced; a class Docker observed cannot be
                 re-labelled; a disagreeing reviewer counts fully only when the evidence supports its alternative
   5 decide      confidence = 0.5 x LLM + 0.5 x deterministic support (reviewer disagreement x0.6 or x0.85, invented
                 evidence x0.85, agreeing memory raises it); ESCALATE below 0.55, with no allowlisted fix, or no evidence
 - - - - - - - - - - - - - - - - - - - nothing above this line changes the shop - - - - - - - - - - - - - - - - - - -
 HANDS (act.py, action_api.py)
   proposal    diagnosis + evidence + ONE allowlisted action, registered at the Action API
   human       demo page "Approve the fix" (+ confirm), terminal "yes", or name + "yes" on the approval page;
               "Preview (dry run)" shows what would run without running it
   Action API  allowlist: 4 verbs, demo services only, never flagd; a real call needs the id of an approved proposal
               with the same action and parameters, at most 15 min old, never used; KILL file -> HTTP 423;
               every call (allowed, refused, dry run) -> logs/actions.jsonl
   verify      every 20 s, 2 healthy checks in a row within 4 min; else roll back (throttle) or escalate (restart)
   report      incidents/<id>/report.json (schema-checked) + report.md; a verified fix joins the few-shot memory
```

## Where the data lives
| file | what it holds |
|---|---|
| `baseline.json` | what "healthy" looks like on this laptop: rates, latencies, limits, learned call graph (`python -m oncall.doctor --baseline`) |
| `incidents/<id>/report.json`, `report.md` | one report per incident: timeline, root cause, evidence, ledger, action, verification |
| `logs/actions.jsonl` | audit log: every proposal, human decision and Action API call |
| `memory/fewshot.jsonl` | few-shot memory: fingerprints of labelled and verified incidents |
| `eval/bundles/<name>/calls.jsonl` | raw telemetry recorded during the overnight run, replayed offline for scoring |
| `eval/labels.json`, `eval/baseline.json` | the human-written truth for each recording, and the baseline recorded with them |
| `eval/SCOREBOARD.md`, `docs/EXPERIMENTS.md` | scores, ablations and robustness runs |
| `oncall.env` | LLM endpoint and key (never committed) |

## Safety controls
| control | where | tested by |
|---|---|---|
| no flag state reaches the agent | `core.redact`, `DENY_SERVICES`, `metrics.prom_query` | `t_redact_removes_flag_state`, `t_ledger_redacts_everything_it_stores`, `t_flagd_never_queried`, `t_flag_metrics_off_limits`, `t_real_recordings_reach_the_agent_flag_free` (54,069 flag mentions in the 10 real recordings, none reach the agent) |
| telemetry is untrusted | `brain.run_tool` (injection warning), SYSTEM prompt | `t_prompt_injection_in_telemetry_is_contained` |
| invented evidence rejected | `brain.validate_evidence` | `t_validator_rejects_invented_evidence`, `t_llm_loop_with_scripted_model` |
| LLM checked against Docker | `brain.check_claims`, `brain.decide` | `t_llm_prose_checked_against_docker`, `t_llm_cannot_relabel_docker_facts` |
| escalate when unsure | `brain.decide` | `t_decide_escalates_when_unsure`, `t_reviewer_disagreement_needs_evidence` |
| approval bound to one action | `act.ask_approval`, `action_api.check_proposal` | `t_no_action_without_yes`, `t_approval_bound_to_exact_action_single_use`, `t_web_approval_flow` |
| dry run | `Config.dry_run` (CLI default), Action API `dry_run`, demo page preview | `t_dry_run_changes_nothing`, `t_control_room_end_to_end` |
| kill switch | `act.check_kill` + server 423 | `t_kill_switch`, `t_action_api_allowlist_and_approval` |
| verify, then roll back or escalate | `act.verify`, `run.py` | `t_execute_verify_resolved`, `t_not_recovered_escalates` |
| detection | `run.watch` | `t_detector_needs_two_anomalous_polls` |

## Fault classes the agent can name
service_down, crash_restart, oom, memory_pressure, memory_starved, high_cpu, cpu_starved, dependency_unreachable, network_degradation, latency_degradation, app_errors, traffic_surge. Restart is the allowlisted remedy for most; traffic_surge throttles the load generator; cpu_starved and memory_starved (a lowered limit) have no allowlisted remedy and are escalated.
