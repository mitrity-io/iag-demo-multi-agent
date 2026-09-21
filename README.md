# MITRITY Multi-Agent Governance Demo

Real-world multi-agent governance scenarios: an **orchestrator** [Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk) agent delegates work to specialized **worker** agents (`data-worker`, `notification-worker`). Each agent runs in its own container with its own Mitrity Gateway and its own MITRITY agent identity. Delegation chains, threat-intel matches, and per-agent privilege boundaries emerge from real activity — no seed data, no synthetic UUIDs.

This is **phase 7** of the MITRITY governance demo series. The single-agent demos — [`iag-demo-mcp-gateway`](https://github.com/mitrity-io/iag-demo-mcp-gateway) and [`iag-demo-mcp-sidecar`](https://github.com/mitrity-io/iag-demo-mcp-sidecar) — run phases 1–6, 8 and 9 (policy, injection detection, DLP, hold/approve, credential broker, built-in tools, governed shell) and have no phase 7: delegation chains and threat intelligence live here, with their number.

The orchestrator is governed on **both** of its entrances:

- **MCP tools** (`delegate__delegate_to`, `fs__read_file`, `fs__list_directory`) reach the model through the orchestrator's gateway, which the SDK starts as its MCP server. Every `tools/call` is evaluated against your MITRITY policies before the upstream tool runs. An allowed delegation is a governed HTTP POST to the worker, whose own gateway judges the next hop on the same chain.
- **The SDK's own built-in tools** (`Bash`, `Write`, `Edit`) never produce an MCP call. The [`mitrity`](https://github.com/mitrity-io/mitrity-python) adapter installs a `PreToolUse` hook that admits each one through the gateway's loopback admission API before the SDK runs it (`surface=agent_hook`). No scenario here uses them; they are hooked so the orchestrator's coverage is complete and attested. If the edge cannot be reached, the call is denied. The SDK's file-reading built-ins (`Read`, `Glob`, `Grep`) are not enabled: the adapter does not hook them, so the gateway's `fs__read_file` and `fs__list_directory` are the orchestrator's only file access and every read is judged.

## Prerequisites

- **Pro or Enterprise MITRITY subscription.** Starter does not include Threat Intelligence or Delegation Chains; this demo will boot but the corresponding dashboard pages stay empty.
- Docker Desktop (or any Docker runtime with Compose)
- An [Anthropic API key](https://console.anthropic.com)
- A MITRITY tenant with **three agents provisioned**: the orchestrator and two workers. See the [Setup](#setup) section for the exact steps.

## Quick start

```bash
git clone https://github.com/mitrity-io/iag-demo-multi-agent
cd iag-demo-multi-agent
cp .env.example .env
# Fill in the 6 MITRITY_AGENT_* values + ANTHROPIC_API_KEY + MITRITY_CONTROL_PLANE_URL
docker compose up --build
```

The orchestrator runner cycles through six scenarios over ~5 minutes. Watch the dashboard at [mitrity.com/app](https://mitrity.com/app) — specifically `/delegation-chains`, `/threat-intel`, and `/audit`.

> The orchestrator image installs the adapter from PyPI, pinned to `mitrity[claude-agent-sdk]==0.2.0` (see `MITRITY_PYTHON_SPEC` in `orchestrator/Dockerfile`). Another version is one build argument away: `docker compose build --build-arg MITRITY_PYTHON_SPEC='mitrity[claude-agent-sdk]==X.Y.Z'`.

## Setup

### 1. Provision three agents in the dashboard

In `mitrity.com/app/agents`, click **+ New Agent** three times:

| Agent name | Mission scope | Tools to enable |
|---|---|---|
| `orchestrator` | `coordinate customer-order workflow` | `delegate__delegate_to` + read-only `fs__read_file`, `fs__list_directory` (no DB writes) |
| `data-worker` | `query and modify customer order data` | `data__query_database`, `data__fetch_orders`, `data__create_order`, `delegate__delegate_to` |
| `notification-worker` | `notify customers of order status changes` | `notify__send_notification`, `notify__email_customer`, `delegate__delegate_to` |

Copy the **Agent ID** and **Agent Key** for each into `.env` (see `.env.example`).

> The intentional asymmetry — orchestrator has narrow read-only perms, workers have write capability — is what makes the privilege-escalation scenario meaningful. Don't grant the orchestrator the worker tools; that's the demo.

**Tool names.** Each gateway serves its upstream's tools as `<namespace>__<tool>`: the `namespace` from `config/*.yaml.tmpl` and the tool name joined by a double underscore (`delegate__delegate_to`, `data__create_order`, `notify__email_customer`). A policy rule targets a tool as `mcp:<namespace>__<tool>` — for example `mcp:data__create_order` — and the SDK's built-in tools as `builtin:<tool>`.

> **Upgrading from an earlier version of this demo?** Tools used to be served as `delegate:delegate_to`, `data:create_order`, `notify:email_customer`. Claude Code exposes MCP tools to the model as `mcp__<server>__<tool>`, and the Anthropic API accepts tool names matching `^[a-zA-Z0-9_-]{1,128}$` only, so a colon could never be called from the Agent SDK. Since sentinel v0.21.0 the gateway joins namespace and tool with a double underscore. The control plane rewrote existing policy rules to the `mcp:<namespace>__<tool>` form; any rule you add uses that form too.

### 2. Confirm Threat Intelligence is enabled

If your tenant was created after May 25 2026, the `mitrity_curated` feed is enabled by default. Verify at `/app/threat-intel/settings` — the **Subscribed feeds** list should include `mitrity_curated`. If not, click **Subscribe** on the feed.

### 3. Run the demo

```bash
docker compose up --build
```

The first run can take a few minutes for the image builds. Subsequent runs reuse the cache. The orchestrator waits for both workers' health checks (their gateway handshakes) before the first scenario.

## What it demonstrates

Six scenarios cycle through in order. Each runs ~30s with real Claude-driven tool use across multiple agents. Every scenario is one `delegate__delegate_to` call from the orchestrator; the workers delegate further where the task tells them to.

| | Scenario | What you'll see in the dashboard |
|---|---|---|
| S1 | Clean order lookup: orchestrator → data-worker | 2-hop chain on `/delegation-chains`, decision `allowed` |
| S2 | Order creation: orchestrator delegates a write the orchestrator can't do itself | Chain blocked with `privilege_escalation`; escalation diff card renders the (tool, op) the orchestrator lacks |
| S3 | End-to-end pipeline: orchestrator → data-worker → notification-worker | Clean 3-hop chain |
| S4 | Deep chain: cascade through workers beyond `max_chain_depth` | Chain blocked with `depth_exceeded` at hop N+1 |
| S5 | Threat-intel match (`/etc/passwd` in the delegated task) | Match on `/threat-intel`, block on `/audit` with **Threat Intel** badge |
| S6 | Loopback: worker tries to delegate back to orchestrator | Chain blocked with `circular_delegation` |

The runner narrates each call as the SDK reports it — `OK`, `BLOCKED` or `HELD` with the gateway's reason — and, for an allowed delegation, the worker's answer. The worker's own hops (S3, S4, S6) are judged by the worker's gateway and show up on the dashboard, not in the orchestrator's terminal.

## Architecture

```
┌─ orchestrator (container) ───────────────────────────────────────────────────────────────┐
│ Python runner (Claude Agent SDK, ClaudeSDKClient)                                        │
│  ├─ built-in tools: Bash, Write, Edit ── PreToolUse hook (mitrity adapter)               │
│  │                                          └── POST /v1/admit ──► admission API          │
│  │                                                (unix:/run/mitrity/admission.sock)     │
│  │   (Read, Glob, Grep are disallowed — the model reads files through the gateway)       │
│  └─ MCP server "mitrity" = Mitrity Gateway (stdio)                                       │
│       ├─ upstream "delegate" (namespace delegate): delegate__delegate_to                 │
│       └─ upstream "filesystem" (namespace fs): fs__read_file, fs__list_directory         │
│ agent: orchestrator                                                                      │
└────────────┬─────────────────────────────────────────────────────────────────────────────┘
             │ HTTP POST /task  (chain_id, delegator_agent_id, task as JSON body)
             ▼
┌─ data-worker (container) ────────────────────────┐      ┌─ notification-worker (container) ────────────┐
│ Python HTTP /task server (Claude loop)           │      │ Python HTTP /task server (Claude loop)       │
│  └─ Mitrity Gateway (stdio)                      │      │  └─ Mitrity Gateway (stdio)                  │
│       ├─ data__query_database, data__fetch_orders│      │       ├─ notify__send_notification,          │
│       │  data__create_order                      │ POST │       │  notify__email_customer               │
│       └─ delegate__delegate_to ──────────────────┼─────►│       └─ delegate__delegate_to               │
│ agent: data-worker                               │      │ agent: notification-worker                   │
└──────────────────────────────────────────────────┘      └──────────────────────────────────────────────┘

All three gateways heartbeat independently to MITRITY_CONTROL_PLANE_URL.
Each agent's actions are evaluated by ITS gateway against ITS profile.
The delegation chain accumulates real hops across the three event streams.
```

The orchestrator's gateway serves both entrances: the MCP `tools/call` stream from the SDK and the loopback admission API the hook calls. It attests the runtime's posture (which built-in tools are hooked, which are not, which are disallowed, other MCP servers, permission mode) so the dashboard can show honest coverage. The adapter is `mitrity.claude_agent_sdk.Governor` — see [`orchestrator/runner.py`](orchestrator/runner.py) for the ~20 lines that wire it up, and [the Framework Adapters contract](https://mitrity.com/docs/integrations/adapters) for what it guarantees.

The workers are plain HTTP `/task` servers: each request runs a Claude tool-use loop against the worker's own gateway over stdio, so every worker-side call — including a further `delegate__delegate_to` — is judged under the worker's identity.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | For Claude API calls (shared across all 3 containers) |
| `MITRITY_CONTROL_PLANE_URL` | Yes | e.g. `https://api.mitrity.com` |
| `MITRITY_AGENT_ID_ORCHESTRATOR` / `_KEY_ORCHESTRATOR` | Yes | Orchestrator agent identity |
| `MITRITY_AGENT_ID_DATA` / `_KEY_DATA` | Yes | Data worker agent identity |
| `MITRITY_AGENT_ID_NOTIFY` / `_KEY_NOTIFY` | Yes | Notification worker agent identity |
| `ANTHROPIC_MODEL` | No | Claude model for all three agents (default: `claude-sonnet-5`) |
| `MITRITY_DEMO_SPEED` | No | `normal` (default) or `fast` (skip the pauses between scenarios) |

See [`.env.example`](.env.example).

## Credential broker (per-agent, hot rotation)

All three gateway configs (`config/orchestrator.yaml.tmpl`, `config/data-worker.yaml.tmpl`, `config/notification-worker.yaml.tmpl`) ship with `credentials.injection_enabled: true`. None of S1–S6 exercise it directly — those scenarios focus on delegation chains + threat intelligence — but the capability is fully available for ad-hoc testing.

To try it: provision a credential in the dashboard (`mitrity.com/app/credentials`), grant it to one of the three agents (e.g., the `data-worker`), then drive a tool call whose args include `${credential:<id>}`. The gateway substitutes the placeholder via the broker before the upstream tool runs. Rotation in the dashboard propagates to the running container within 30 seconds without any restart.

For a guided end-to-end walkthrough (provisioning, substitution, mid-scenario rotation, fail-closed), see Phase 6 in either:
- [iag-demo-mcp-sidecar](https://github.com/mitrity-io/iag-demo-mcp-sidecar) — single-agent + transparent-proxy form
- [iag-demo-mcp-gateway](https://github.com/mitrity-io/iag-demo-mcp-gateway) — single-agent + aggregating-gateway form

The multi-agent setting adds one twist worth noting: each agent has its own MITRITY identity, so credential grants and rotations are scoped per-agent. The `data-worker` can hold DB credentials while the `notification-worker` independently holds SMTP / Slack / SendGrid keys; rotating one does not invalidate the other's cache.

## Troubleshooting

**`/delegation-chains` page is empty after a run.**
Check the orchestrator logs (`docker compose logs orchestrator`) for `chain=` lines. If chains were emitted but the dashboard shows nothing, verify all three agents are registered to the **same tenant** (an agent registered to the wrong tenant means the worker's events land in a different tenant's audit log).

**`/threat-intel` page shows no matches.**
Confirm `tenant_threat_settings.subscribed_feeds` includes `mitrity_curated` at `/app/threat-intel/settings`.

**A policy rule does not match.**
Rules target gateway-served tools as `mcp:<namespace>__<tool>` (`mcp:delegate__delegate_to`, `mcp:data__create_order`), not the bare tool name and not the older `mcp:<namespace>:<tool>` form.

**The orchestrator exits before the first scenario.**
`docker compose up` waits for both workers to report healthy (their gateway handshake with the control plane). If a worker never becomes healthy, its logs (`docker compose logs data-worker`) name the missing variable or the rejected agent key.

**The orchestrator exits with `FATAL: /run/mitrity ...`.**
The admission runtime directory is missing or owned by another uid. The image creates it for `demo` (uid 1000), mode 0700; a tmpfs mounted on `/run` or a `user:` override in `docker-compose.yml` changes that. Create the directory for the container user (mode 0700) or drop the override.

## License

Copyright © 2026 MITRITY AB. See [LICENSE](LICENSE).
