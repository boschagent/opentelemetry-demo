# Architecture (one page)

```
 alert (manual --alert, or detector --watch: 2 consecutive anomalous polls)
   |
   v
 TRIAGE  oncall/triage.py  (deterministic, ~10 tool calls)
   container_health  container_stats  service_red  find_error_traces  latency_edges
   -> ranked suspects {component, fault_class, score, rationale, evidence ids}
   |
   v
 INVESTIGATOR  oncall/brain.py  (LLM, JSON tool loop, <=10 steps, duplicate calls blocked)
   tools: service_red prom_query find_error_traces get_trace latency_edges get_logs
          container_health container_stats deploy_history     (+ incident memory hints)
   every tool result -> core.redact() (flag state removed) -> Ledger (ev-001, ev-002, ...)
   |
   v
 VALIDATOR (ids exist, quoted numbers appear in the evidence) -> REVIEWER agent (tries to disprove)
   |
   v
 DECIDE: confidence = 0.5*LLM + 0.5*triage support; x0.6 if reviewer disagrees; < threshold -> ESCALATE
   |
   v
 PLAN from runbook allowlist (restart_service | throttle_loadgen | toggle_flag | scale_service)
   |
   v
 APPROVAL GATE (exact "yes") -- dry-run default -- KILL file checked before every step
   |
   v
 ACTION API  oncall/action_api.py  (allowlist, approval header required, 423 when KILL, audit log)
   |
   v
 VERIFY (container running + no error traces in last 1 min, 2 consecutive checks)
   -> recovered: RESOLVED | not: rollback (if inverse exists) + ESCALATE
   |
   v
 REPORT incidents/<id>/report.json + report.md (timeline, root cause, evidence, action, prevention)
```

## Safety controls
| control | where | tested by |
|---|---|---|
| no flag state reaches the agent | `core.redact`, `DENY_SERVICES` | `t_redact_removes_flag_state`, `t_ledger_redacts_everything_it_stores`, `t_flagd_never_queried` |
| invented evidence rejected | `brain.validate_evidence` | `t_validator_rejects_invented_evidence`, `t_llm_loop_with_scripted_model` |
| escalate when unsure | `brain.decide` | `t_decide_escalates_when_unsure` |
| explicit approval only | `act.ask_approval` + server header check | `t_no_action_without_yes`, `t_action_api_allowlist_and_approval` |
| dry-run default | `Config.dry_run=True` | `t_dry_run_changes_nothing` |
| kill switch | `act.check_kill` + server 423 | `t_kill_switch`, `t_action_api_allowlist_and_approval` |
| verify, then rollback or escalate | `act.verify`, `run.py` | `t_execute_verify_resolved`, `t_not_recovered_escalates` |

## Fault classes the triage can name
service_down, crash_restart, oom, memory_pressure, high_cpu, cpu_starved, dependency_unreachable, network_degradation, latency_degradation, app_errors, traffic_surge.
