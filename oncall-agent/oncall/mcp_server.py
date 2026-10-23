"""MCP server exposing the agent's READ-ONLY investigation tools (stdio transport, stdlib only).  Owner: P2.

Lives in OUR folder - we never edit the demo's own src/mcp (editing demo source = disqualification).
Any MCP client (Claude Desktop, MCP Inspector, other agents) can call the same tools our agent uses.
Actions are deliberately NOT exposed over MCP: changes go only through the Action API after human approval.

Run:     python -m oncall.mcp_server
Inspect: npx @modelcontextprotocol/inspector python -m oncall.mcp_server
Claude Desktop config entry:
  "oncall": {"command": "python", "args": ["-m", "oncall.mcp_server"], "cwd": "<path>/oncall-agent"}
"""
from __future__ import annotations

import json
import sys

from .brain import TOOLS
from .core import Backend, Config, redact

PROTOCOL = "2025-06-18"
SCHEMAS = {
    "service_red": {},
    "prom_query": {"query": {"type": "string", "description": "PromQL instant query"}},
    "find_error_traces": {"service": {"type": "string"}, "lookback": {"type": "string", "default": "10m"}},
    "get_trace": {"trace_id": {"type": "string"}},
    "latency_edges": {"service": {"type": "string", "default": "frontend"}, "lookback": {"type": "string", "default": "5m"}},
    "get_logs": {"service": {"type": "string"}, "minutes": {"type": "integer", "default": 10},
                 "grep": {"type": "string", "description": "optional regex"}},
    "container_health": {}, "container_stats": {},
    "deploy_history": {"minutes": {"type": "integer", "default": 120}},
}
REQUIRED = {"prom_query": ["query"], "find_error_traces": ["service"], "get_trace": ["trace_id"], "get_logs": ["service"]}


class Server:
    def __init__(self, be: Backend | None = None, cfg: Config | None = None):
        self.be, self.cfg = be or Backend(), cfg or Config.from_env()

    def handle(self, msg: dict) -> dict | None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # notification
            return None
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "oncall-telemetry", "version": "1.0"}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": TOOLS[n][1],
                              "inputSchema": {"type": "object", "properties": SCHEMAS.get(n, {}), "required": REQUIRED.get(n, [])}}
                             for n in TOOLS]}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in TOOLS:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
            try:
                out = TOOLS[name][0](self.be, self.cfg, **args)
                text = out.get("summary", "") + "\n\nsource: " + str(out.get("source", ""))
                text, _ = redact(text)
                res = {"content": [{"type": "text", "text": text}], "isError": not out.get("ok", False)}
            except TypeError as e:
                res = {"content": [{"type": "text", "text": f"bad arguments: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> None:
    srv = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = srv.handle(json.loads(line))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
