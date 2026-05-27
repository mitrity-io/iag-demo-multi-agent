"""Notification worker — HTTP /task server. Same architecture as data-worker
(see workers/data-worker/server.py docstring); only the role + system
prompt + port differ.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any

import anthropic
import uvicorn
from fastapi import FastAPI, Request
from rich.console import Console

console = Console()
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
AGENT_ID = os.environ["MITRITY_AGENT_ID"]
WORKER_ROLE = "notification-worker"
LISTEN_PORT = 8082

# Peer agent UUIDs — populated per docker-compose so this worker's prompt
# can hand Claude a real {target_worker → UUID} lookup table. Without this
# Claude invents a non-UUID string for to_agent_id on downstream
# delegate_to calls and the backend rejects with 400.
PEER_AGENT_IDS = {
    name: os.environ.get(env_var, "")
    for name, env_var in [
        ("orchestrator", "MITRITY_AGENT_ID_ORCHESTRATOR"),
        ("data-worker", "MITRITY_AGENT_ID_DATA"),
    ]
    if os.environ.get(env_var)
}
SYSTEM_PROMPT = (
    "You are the notification-worker agent in a multi-agent governance demo. "
    "You have notification tools (send_notification, email_customer) and a "
    "delegate_to tool for chaining further. When the incoming task references "
    "chain_id, ALWAYS pass that exact chain_id on any delegate_to call so "
    "the chain accumulates correctly. Be concise."
)


class MCPClient:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["/usr/local/bin/mitrity-gateway", "--config", "/etc/mitrity/gateway.yaml"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._drain, args=(self.proc.stderr,), daemon=True).start()
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": WORKER_ROLE, "version": "0.1.0"},
        })
        self.tools = self._call("tools/list", {}).get("tools", [])

    @staticmethod
    def _drain(stream: Any) -> None:
        for line in iter(stream.readline, b""):
            sys.stderr.write(line.decode(errors="replace"))

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("mitrity-gateway closed stdout")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        r = self._call("tools/call", {"name": name, "arguments": arguments})
        parts = [b.get("text", "") for b in r.get("content", []) if b.get("type") == "text"]
        return "\n".join(parts)


class WorkerAgent:
    def __init__(self, mcp: MCPClient) -> None:
        self.client = anthropic.Anthropic()
        self.mcp = mcp
        self._to_api: dict[str, str] = {}
        self._from_api: dict[str, str] = {}
        for t in mcp.tools:
            api = t["name"].replace(":", "__")
            self._to_api[t["name"]] = api
            self._from_api[api] = t["name"]
        self._tools = [
            {"name": self._to_api[t["name"]], "description": t["description"], "input_schema": t["inputSchema"]}
            for t in mcp.tools
        ]

    def run(self, task: str, chain_id: str, delegator_agent_id: str) -> str:
        # delegator_agent_id here is the UUID of the upstream agent that
        # called THIS worker — NOT this worker's own AGENT_ID. When we
        # forward the chain via delegate_to, we pass that same upstream
        # delegator forward so the backend's delegation ledger records
        # the real chain instead of treating each hop as an independent
        # root invocation.
        peer_table = "\n".join(
            f"  - target_worker='{name}' → to_agent_id='{aid}'"
            for name, aid in PEER_AGENT_IDS.items()
        ) or "  (no peer UUIDs configured — downstream delegate_to will fail)"
        prompt = (
            f"Incoming task (chain_id={chain_id}, delegator={delegator_agent_id}): {task}\n\n"
            f"If the task asks you to delegate further, you must pass chain_id='{chain_id}' "
            f"and delegator_agent_id='{delegator_agent_id}' on your delegate_to call. "
            "The delegator_agent_id is the UUID of the agent that called you (the upstream "
            "hop), NOT your own agent ID — keep forwarding it as-is. Pick the appropriate "
            "target_worker from the enum, and use the matching to_agent_id UUID from this "
            "lookup table (the backend rejects non-UUID values):\n"
            f"{peer_table}"
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for _ in range(6):
            r = self.client.messages.create(
                model=MODEL, max_tokens=1024,
                system=SYSTEM_PROMPT, tools=self._tools, messages=messages,
            )
            text = "\n".join(c.text for c in r.content if c.type == "text")
            tool_uses = [c for c in r.content if c.type == "tool_use"]
            if not tool_uses:
                return text
            messages.append({"role": "assistant", "content": r.content})
            results: list[dict[str, Any]] = []
            for tu in tool_uses:
                mcp_name = self._from_api[tu.name]
                try:
                    out = self.mcp.call_tool(mcp_name, tu.input)
                except Exception as e:
                    out = f"[tool error: {e}]"
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
            messages.append({"role": "user", "content": results})
        return "(max turns reached)"


app = FastAPI()
_mcp: MCPClient | None = None
_agent: WorkerAgent | None = None


@app.on_event("startup")
def _startup() -> None:
    global _mcp, _agent
    _mcp = MCPClient()
    _agent = WorkerAgent(_mcp)
    console.print(f"[green]{WORKER_ROLE} ready — {len(_mcp.tools)} tools loaded[/green]")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "role": WORKER_ROLE, "agent_id": AGENT_ID}


@app.post("/task")
async def task(req: Request) -> dict[str, Any]:
    body = await req.json()
    chain_id = body.get("chain_id", "")
    delegator = body.get("delegator_agent_id", "")
    task_text = body.get("task", "")
    console.print(f"[dim][{WORKER_ROLE}] inbound chain={chain_id} delegator={delegator} task={task_text[:80]}[/dim]")
    if not _agent:
        return {"error": "agent not ready"}
    started = time.time()
    result = _agent.run(task_text, chain_id, delegator)
    return {
        "worker": WORKER_ROLE,
        "agent_id": AGENT_ID,
        "chain_id": chain_id,
        "elapsed_sec": round(time.time() - started, 2),
        "result": result,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT, log_level="info")
