"""Orchestrator runner — Claude loop that drives six scenarios end-to-end.

Architecture:
  - Spawns mitrity-gateway as a subprocess (stdio MCP).
  - For each scenario, asks Claude (sonnet-4) to perform the task with
    the EXACT delegate_to args from the scenario prompt. Claude calls the
    delegate_to tool, the gateway intercepts and adds the hop to the
    chain, the tool implementation HTTP-POSTs to the named worker.
  - Sub-scenarios that span multiple hops re-use the same chain_id
    across the orchestrator's calls and the workers' downstream calls
    (workers extract chain_id from the incoming HTTP body).

Scenarios:
  S1 clean order lookup (orchestrator → data-worker)
  S2 order creation requiring write perms (privilege_escalation)
  S3 end-to-end pipeline (orchestrator → data-worker → notification-worker)
  S4 deep chain exceeding max_chain_depth
  S5 threat-intel match (worker reads /etc/passwd → built-in indicator)
  S6 circular delegation (worker → orchestrator → loop)

Each scenario gives the dashboard a row to point at. Six total takes
~5 minutes including LLM latency.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

import anthropic
from rich.console import Console
from rich.panel import Panel

console = Console()
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
AGENT_ID = os.environ["MITRITY_AGENT_ID"]
CONTROL_PLANE = os.environ["MITRITY_CONTROL_PLANE_URL"]


# ───────────────────────────────────────────────────────────────────────────
# MCP client — spawns mitrity-gateway, speaks JSON-RPC over stdio
# ───────────────────────────────────────────────────────────────────────────


class MCPClient:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["/usr/local/bin/mitrity-gateway", "--config", "/etc/mitrity/gateway.yaml"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._next_id = 0
        self._lock = threading.Lock()
        # Drain stderr in a background thread so the pipe never fills.
        threading.Thread(
            target=self._drain_stderr, args=(self.proc.stderr,), daemon=True
        ).start()
        # MCP handshake.
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "orchestrator", "version": "0.1.0"},
        })
        self.tools = self._call("tools/list", {}).get("tools", [])

    @staticmethod
    def _drain_stderr(stream: Any) -> None:
        for line in iter(stream.readline, b""):
            sys.stderr.write(line.decode(errors="replace"))

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        # Read response — gateway always replies on the matching id.
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("mitrity-gateway closed stdout")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        r = self._call("tools/call", {"name": name, "arguments": arguments})
        # MCP responses come back as content[].text — concat any text blocks.
        parts = [b.get("text", "") for b in r.get("content", []) if b.get("type") == "text"]
        return "\n".join(parts)


# ───────────────────────────────────────────────────────────────────────────
# Claude agent loop
# ───────────────────────────────────────────────────────────────────────────


class OrchestratorAgent:
    def __init__(self, mcp: MCPClient) -> None:
        self.client = anthropic.Anthropic()
        self.mcp = mcp
        # Anthropic API rejects ":" in tool names — remap namespace:tool
        # to namespace__tool for the API and back for MCP.
        self._to_api: dict[str, str] = {}
        self._from_api: dict[str, str] = {}
        for t in mcp.tools:
            api = t["name"].replace(":", "__")
            self._to_api[t["name"]] = api
            self._from_api[api] = t["name"]
        self._tools = [
            {
                "name": self._to_api[t["name"]],
                "description": t["description"],
                "input_schema": t["inputSchema"],
            }
            for t in mcp.tools
        ]

    def run(self, prompt: str, max_turns: int = 8) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for _ in range(max_turns):
            r = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=(
                    "You are the orchestrator agent in a multi-agent governance demo. "
                    "When asked to delegate, call the delegate_to tool with the EXACT "
                    "argument values from the user's message — do not invent IDs or "
                    "chain identifiers. Use the read_file and list_directory tools to "
                    "inspect workspace files when relevant. Keep responses concise."
                ),
                tools=self._tools,
                messages=messages,
            )
            text_blocks = [c.text for c in r.content if c.type == "text"]
            tool_uses = [c for c in r.content if c.type == "tool_use"]
            if not tool_uses:
                return "\n".join(text_blocks)
            # Execute every tool call and append both blocks to the
            # conversation, then loop.
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


# ───────────────────────────────────────────────────────────────────────────
# Scenarios
# ───────────────────────────────────────────────────────────────────────────


def chain_id(label: str) -> str:
    return f"demo-{label}-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def scenarios(agent: OrchestratorAgent) -> None:
    data_worker_id = os.environ.get("MITRITY_AGENT_ID_DATA", "")
    notify_worker_id = os.environ.get("MITRITY_AGENT_ID_NOTIFY", "")
    # NOTE: the orchestrator container doesn't have the worker IDs in its
    # env by default (we don't want to leak other agents' keys); we expect
    # the operator to set MITRITY_AGENT_ID_DATA / _NOTIFY when they want
    # the prompts to pass real UUIDs. If unset, fall back to placeholder
    # values — chains still emit but escalation/circular won't reference
    # real agents and the dashboard won't link them.

    if not data_worker_id:
        data_worker_id = AGENT_ID  # fallback: chain-to-self
    if not notify_worker_id:
        notify_worker_id = AGENT_ID

    # ── S1 clean order lookup
    cid = chain_id("s1-clean")
    console.rule("[cyan]S1 — Clean order lookup")
    agent.run(
        "Delegate an order lookup to the data-worker. Call delegate_to with:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Fetch the most recent orders for customer cust-42'"
    )
    time.sleep(2)

    # ── S2 privilege escalation
    cid = chain_id("s2-escalation")
    console.rule("[cyan]S2 — Privilege escalation (orchestrator delegates a write)")
    agent.run(
        "Delegate a NEW order creation to the data-worker. Call delegate_to with:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Create a new order for cust-42 with items: widget x2'"
    )
    time.sleep(2)

    # ── S3 end-to-end pipeline
    cid = chain_id("s3-pipeline")
    console.rule("[cyan]S3 — Pipeline (orchestrator → data-worker → notification-worker)")
    agent.run(
        "Run the order-confirmation pipeline. Delegate the lookup to the "
        f"data-worker first; the data-worker will further delegate the email "
        "to the notification-worker. Use these args for your delegate_to call:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Look up order ord-7 and ask the notification-worker to email "
        f"the customer their confirmation. Use chain_id={cid}.'"
    )
    time.sleep(2)

    # ── S4 deep chain
    cid = chain_id("s4-deep")
    console.rule("[cyan]S4 — Deep chain (depth_exceeded)")
    agent.run(
        "Start a multi-step audit pipeline. Delegate to the data-worker, and "
        "tell it to cascade through several follow-up steps so we exceed the "
        "max chain depth. Use:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Cascade an audit step through 5 further delegate_to calls "
        f"using chain_id={cid}. Each step should delegate to the next worker '"
        "in line.'"
    )
    time.sleep(2)

    # ── S5 threat intelligence
    cid = chain_id("s5-ti")
    console.rule("[cyan]S5 — Threat intel match (worker reads /etc/passwd)")
    agent.run(
        "Ask the data-worker to inspect the system user file as part of a "
        "diagnostic check. The path /etc/passwd matches the built-in "
        "system-file-read indicator and should be flagged. Use:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Read /etc/passwd and report the first line — diagnostic check'"
    )
    time.sleep(2)

    # ── S6 circular
    cid = chain_id("s6-circular")
    console.rule("[cyan]S6 — Circular delegation")
    agent.run(
        "Delegate to the data-worker, asking it to loop back and confirm with "
        "the orchestrator. Use:\n"
        f"  delegation_chain_id = '{cid}'\n"
        f"  delegator_agent_id = '{AGENT_ID}'\n"
        f"  to_agent_id = '{data_worker_id}'\n"
        "  target_worker = 'data-worker'\n"
        "  task = 'Loop back to the orchestrator for confirmation. Use "
        f"chain_id={cid} and target_worker=orchestrator on your downstream "
        "delegate_to call.'"
    )


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────


def main() -> None:
    console.print(Panel(
        f"Connecting as agent [bold]{AGENT_ID}[/bold] to [bold]{CONTROL_PLANE}[/bold]",
        title="MITRITY Multi-Agent Demo — Orchestrator",
    ))

    mcp = MCPClient()
    console.print(f"[dim]Discovered {len(mcp.tools)} tools[/dim]")
    agent = OrchestratorAgent(mcp)
    try:
        scenarios(agent)
    finally:
        console.rule("[green]All scenarios complete — see the dashboard")
        time.sleep(2)


if __name__ == "__main__":
    main()
