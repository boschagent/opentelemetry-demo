# Team form

**Team name:** boschagent  
**Repository:** https://github.com/boschagent/opentelemetry-demo (agent in `oncall-agent/`)

| member | role | built / owns |
|---|---|---|
| <P1 name> | Lead / integrator | run.py, brain.py (LLM loop, reviewer, validator), core.py; live demos |
| <P2 name> | Metrics & traces | tools/metrics.py, tools/traces.py; verified metric names live |
| <P3 name> | Infra, logs, sanitiser | tools/infra.py, core.redact; flag-redaction checks |
| <P4 name> | Hands & safety | act.py, action_api.py; approval gate, kill switch, verification |
| <P5 name> | Eval, data, docs | eval/, practice/, EVAL_NOTE, incident recordings |

## Tools and AI models used
- **AI coding assistant:** Claude (Anthropic, Claude Opus via claude.ai / Claude Code) generated the initial architecture and code skeleton, and the self-test suite. Each member then verified, tuned and modified their module against the live demo.
- **Other assistants:** <ChatGPT / Claude free: list who used what>
- **Runtime LLM for the agent:** <e.g. Groq llama-3.3-70b-versatile (free tier) / organisers' key>
- **Third-party libraries:** none (Python standard library only). The demo itself: OpenTelemetry Demo (Apache-2.0). Practice only: gaiaadm/pumba image for network faults.
