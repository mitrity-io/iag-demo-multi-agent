"""MITRITY Multi-Agent Governance Demo — Orchestrator runner.

Phase 7 of the MITRITY governance demo series: delegation chains and threat
intelligence. The single-agent gateway demo (iag-demo-mcp-gateway) runs phases
1-6, 8 and 9 and has no phase 7; this demo is that phase, with three governed
agents in three containers.

The orchestrator is a Claude Agent SDK session governed by MITRITY on both of
its entrances:

- MCP tools (delegate__delegate_to, fs__read_file, fs__list_directory) reach the
  model through the orchestrator's own Mitrity Gateway, which the SDK starts as
  its MCP server. Every tools/call is judged by the gateway before the upstream
  tool runs (surface mcp_gateway). An allowed delegate__delegate_to HTTP-POSTs
  the task to the named worker, whose own gateway judges the next hop on the
  same chain (workers extract chain_id from the incoming HTTP body).
- The SDK's own built-in tools (Bash, Write, Edit) never produce an MCP call.
  `mitrity.claude_agent_sdk.Governor` installs the PreToolUse hook that admits
  each of them through the gateway's loopback admission API before the SDK
  runs it (surface agent_hook). No scenario here asks for them; they are hooked
  so the orchestrator's coverage is complete and attested. If the edge cannot
  be reached, the call is denied — there is no fail-open mode.

The SDK's file-reading built-ins (Read, Glob, Grep) are not enabled. The adapter
does not hook them (they are outside its execution-capable inventory), so an
enabled Read would let the model read the workspace — or /etc/passwd, the path
S5 hands to the data-worker — without any decision being made. The gateway's
fs__read_file and fs__list_directory are this demo's only file access, and every
one of those calls is judged.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import claude_agent_sdk
import mitrity
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from mitrity.claude_agent_sdk import Governor
from output import (
    agent_message,
    console,
    info,
    pause,
    phase_header,
    print_summary,
    tool_allowed,
    tool_blocked,
    tool_held,
    worker_reply,
)
from phases import phase7_delegation_chains

GATEWAY_NAME = "mitrity"
GATEWAY_COMMAND = "/usr/local/bin/mitrity-gateway"
GATEWAY_CONFIG = "/etc/mitrity/gateway.yaml"
DELEGATE_TOOL = "delegate__delegate_to"

# The built-in tools the SDK may use: every one of them is execution-capable and
# admitted through the adapter's PreToolUse hook. The adapter (mitrity 0.2.0)
# hooks only its execution-capable inventory (Bash, Write, Edit, MultiEdit,
# NotebookEdit, WebFetch, WebSearch); Read, Glob and Grep are outside it and can
# neither be hooked nor attested as hooked, so they stay out of the demo — the
# gateway's fs__read_file / fs__list_directory are the only file access. They
# are also passed as disallowed_tools so the attestation records the exclusion.
BUILTIN_TOOLS = ("Bash", "Write", "Edit")
UNHOOKABLE_READ_TOOLS = ("Read", "Glob", "Grep")

SYSTEM_PROMPT = (
    "You are the orchestrator agent in a multi-agent governance demo. Your tools are served "
    "by an MCP server named 'mitrity': delegate__delegate_to hands a task to a worker, "
    "fs__read_file and fs__list_directory read the workspace. You also have your own built-in "
    "Bash, Write and Edit tools. Use the MCP server's tools by default; use a built-in tool "
    "only when a request explicitly asks for it — no task here does. When asked to delegate, "
    "call delegate__delegate_to with the EXACT argument values from the message — do not "
    "invent IDs or chain identifiers, and do not substitute one tool for another. When a tool "
    "call is denied, report the reason you were given and stop; do not retry. Be concise."
)


def display_name(tool_name: str) -> str:
    """`mcp__mitrity__delegate__delegate_to` -> `delegate__delegate_to (gateway)`; built-ins are marked."""
    prefix = f"mcp__{GATEWAY_NAME}__"
    if tool_name.startswith(prefix):
        return f"{tool_name[len(prefix):]} (gateway)"
    if tool_name.startswith("mcp__"):
        return f"{tool_name} (ungoverned MCP server)"
    if tool_name in BUILTIN_TOOLS:
        return f"{tool_name} (built-in, admitted via hook)"
    return f"{tool_name} (built-in, unhooked)"


def result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def worker_response(text: str) -> dict[str, Any] | None:
    """The worker's JSON reply behind an allowed delegation (`[delegated to ...]\\n{...}`)."""
    _, _, body = text.partition("\n")
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


class DemoAgent:
    """A Claude Agent SDK session governed by MITRITY on both entrances."""

    def __init__(self) -> None:
        self.governor = Governor(
            gateway={
                "type": "stdio",
                "command": GATEWAY_COMMAND,
                "args": ["--config", GATEWAY_CONFIG],
            },
            gateway_name=GATEWAY_NAME,
        )
        self.options = self.governor.options(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            system_prompt=SYSTEM_PROMPT,
            tools=list(BUILTIN_TOOLS),
            # The demo has no human at a permission prompt: allowed_tools
            # auto-approves every built-in and every tool the gateway serves,
            # so the SDK never asks. MITRITY's deny happens in the PreToolUse
            # hook, before the SDK's permission step is reached.
            allowed_tools=[*BUILTIN_TOOLS, f"mcp__{GATEWAY_NAME}"],
            # Already absent from `tools`; listing them here too makes the
            # exclusion part of the attested posture (and its config hash).
            disallowed_tools=list(UNHOOKABLE_READ_TOOLS),
            permission_mode="default",
            # Bounds each query(), not the session. Verified against the
            # bundled Claude Code 2.1.277 (claude-agent-sdk 0.2.157): the SDK
            # input path enters the query loop once per submitted user message
            # with the turn counter reset to 1 and max_turns passed in on every
            # entry; a prompt that exhausts it ends with an error_max_turns
            # result for that prompt only, and the session goes on with the
            # next one. No scenario in this demo needs more than one tool call.
            max_turns=8,
            cwd="/workspace",
            # A delegation blocks inside the gateway while the worker (and the
            # workers it delegates to) run their own model loops, and a held
            # call waits for a human; give the SDK's MCP tool timeout the same
            # patience.
            env={"MCP_TOOL_TIMEOUT": "600000"},
        )
        self._client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> DemoAgent:
        self._client = ClaudeSDKClient(options=self.options)
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def run_prompt(self, prompt: str) -> str:
        """Send a prompt and narrate every tool call as the SDK reports it."""
        if self._client is None:
            raise RuntimeError("DemoAgent is not connected; use `async with DemoAgent()`")
        await self._client.query(prompt)

        pending: dict[str, tuple[str, float]] = {}
        text_parts: list[str] = []

        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        pending[block.id] = (display_name(block.name), time.monotonic())
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if not isinstance(block, ToolResultBlock):
                        continue
                    name, started = pending.pop(block.tool_use_id, ("tool", time.monotonic()))
                    duration_ms = int((time.monotonic() - started) * 1000)
                    text = result_text(block.content)
                    lowered = text.lower()
                    if block.is_error and ("approval" in lowered or "held" in lowered):
                        tool_held(name, text, duration_ms)
                    elif block.is_error:
                        tool_blocked(name, text, duration_ms)
                    else:
                        tool_allowed(name, text, duration_ms)
                        reply = worker_response(text) if name.startswith(DELEGATE_TOOL) else None
                        if reply is not None:
                            worker_reply(
                                str(reply.get("worker", "worker")),
                                float(reply.get("elapsed_sec", 0) or 0),
                                str(reply.get("result", "")),
                            )
                    await pause(0.5)
            elif isinstance(message, ResultMessage) and message.is_error:
                # claude-agent-sdk 0.2.157: ResultMessage carries `errors: list[str] | None`,
                # `result: str | None` and `subtype: str`; report the most specific one set.
                # `errors` is read defensively: this is the error path, and an SDK without
                # the field must not turn it into an AttributeError.
                errors = getattr(message, "errors", None) or []
                detail = ", ".join(errors) or message.result or message.subtype
                info(f"turn ended with an error: {detail}")

        final_text = "\n".join(text_parts).strip()
        if final_text:
            agent_message(final_text)
        return final_text


async def main() -> None:
    version = os.environ.get("MITRITY_GATEWAY_VERSION", "unknown")
    agent_id = os.environ["MITRITY_AGENT_ID"]
    control_plane = os.environ["MITRITY_CONTROL_PLANE_URL"]

    console.print()
    console.print("[bold cyan]MITRITY Multi-Agent Demo — Orchestrator[/bold cyan]", justify="center")
    console.print(
        "[dim]Phase 7: delegation chains and threat intelligence across three governed agents[/dim]",
        justify="center",
    )
    console.print(
        f"[dim]Gateway {version} · Claude Agent SDK {claude_agent_sdk.__version__} · "
        f"mitrity {mitrity.__version__}[/dim]",
        justify="center",
    )
    console.print(f"[dim]Agent {agent_id} · control plane {control_plane}[/dim]", justify="center")
    console.print()

    info(
        "Starting the Claude Agent SDK session (the SDK starts the orchestrator's Mitrity "
        "Gateway as its MCP server)..."
    )

    try:
        async with DemoAgent() as agent:
            attestation = agent.governor.attestation()
            info(
                "Attested to the edge: hooked built-ins "
                f"{', '.join(attestation.hooked_tools) or 'none'}; "
                f"unhooked execution tools {', '.join(attestation.unhooked_exec_tools) or 'none'}; "
                f"disallowed built-ins {', '.join(attestation.disallowed_tools) or 'none'}; "
                f"other MCP servers {', '.join(attestation.other_mcp_servers) or 'none'}"
            )
            await pause(1.0)

            phase_header(7, "Delegation Chains + Threat Intelligence")
            await phase7_delegation_chains.run(agent)
            await pause(2.0)

            stats = agent.governor.stats
            info(
                f"Admission hook: {stats.admitted} built-in calls admitted "
                f"({stats.allowed} allowed, {stats.denied} denied, {stats.held} held, "
                f"{stats.unreachable} blocked because the edge could not be reached); "
                f"{stats.attestations} attestation(s) sent."
            )
            print_summary()
    except KeyboardInterrupt:
        console.print("\n[yellow]Demo interrupted.[/yellow]")
        print_summary()
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise


if __name__ == "__main__":
    asyncio.run(main())
