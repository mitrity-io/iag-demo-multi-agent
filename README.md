# MITRITY Multi-Agent Governance Demo

Real-world multi-agent governance scenarios: an **orchestrator** Claude agent delegates work to specialized **worker** agents (`data-worker`, `notification-worker`). Each agent runs in its own container with its own MITRITY Gateway and its own MITRITY agent identity. Delegation chains, threat-intel matches, and per-agent privilege boundaries emerge from real activity — no seed data, no synthetic UUIDs.

Sister demos: [`iag-demo-mcp-gateway`](https://github.com/mitrity-io/iag-demo-mcp-gateway) and [`iag-demo-mcp-sidecar`](https://github.com/mitrity-io/iag-demo-mcp-sidecar) demonstrate single-agent governance (policy, injection detection, DLP, hold/approve).

## Prerequisites

- **Pro or Enterprise MITRITY subscription.** Starter does not include Threat Intelligence or Delegation Chains; this demo will boot but the corresponding dashboard pages stay empty.
- Docker + docker-compose
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

The orchestrator runner cycles through six scenarios over ~5 minutes. Watch the dashboard at [app.mitrity.com/app](https://app.mitrity.com/app) — specifically `/delegation-chains`, `/threat-intel`, and `/audit`.

## Setup

### 1. Provision three agents in the dashboard

In `app.mitrity.com/app/agents`, click **+ New Agent** three times:

| Agent name | Mission scope | Plan-gated tools to enable |
|---|---|---|
| `orchestrator` | `coordinate customer-order workflow` | (read-only: filesystem, no DB writes) |
| `data-worker` | `query and modify customer order data` | `query_database`, `create_order`, `fetch_orders` |
| `notification-worker` | `notify customers of order status changes` | `send_notification`, `email_customer` |

Copy the **Agent ID** and **Agent Key** for each into `.env` (see `.env.example`).

> The intentional asymmetry — orchestrator has narrow read-only perms, workers have write capability — is what makes the privilege-escalation scenario meaningful. Don't grant the orchestrator the worker tools; that's the demo.

### 2. Confirm Threat Intelligence is enabled

If your tenant was created after May 25 2026, the `mitrity_curated` feed is enabled by default. Verify at `/app/threat-intel/settings` — the **Subscribed feeds** list should include `mitrity_curated`. If not, click **Subscribe** on the feed.

### 3. Run the demo

```bash
docker compose up --build
```

The first run can take ~3 minutes for the multi-arch image builds. Subsequent runs reuse the cache.

## What it demonstrates

Six scenarios cycle through in order. Each runs ~30s with real Claude-driven tool use across multiple agents.

| | Scenario | What you'll see in the dashboard |
|---|---|---|
| S1 | Clean order lookup: orchestrator → data-worker | 2-hop chain on `/delegation-chains`, decision `allowed` |
| S2 | Order creation: orchestrator delegates a write the orchestrator can't do itself | Chain blocked with `privilege_escalation`; escalation diff card renders the (tool, op) the orchestrator lacks |
| S3 | End-to-end pipeline: orchestrator → data-worker → notification-worker | Clean 3-hop chain |
| S4 | Deep chain: cascade through workers beyond `max_chain_depth` | Chain blocked with `depth_exceeded` at hop N+1 |
| S5 | Threat-intel match (`/etc/passwd` read by worker) | Match on `/threat-intel`, block on `/audit` with **Threat Intel** badge |
| S6 | Loopback: worker tries to delegate back to orchestrator | Chain blocked with `circular_delegation` |

## Architecture

```
┌─ orchestrator (container) ─┐    ┌─ data-worker (container) ──┐    ┌─ notification-worker (container) ──┐
│ Python runner (Claude SDK) │    │ Python HTTP /task server   │    │ Python HTTP /task server           │
│  ├─ delegate_to tool       │    │  ├─ query_database         │    │  ├─ send_notification              │
│  └─ read-only fs tools     │    │  ├─ fetch_orders           │    │  └─ email_customer                 │
│ mitrity-gateway            │    │  ├─ create_order           │    │ mitrity-gateway                    │
│ agent: orchestrator        │    │  └─ delegate_to            │    │ agent: notification-worker         │
└────────────┬───────────────┘    │ mitrity-gateway            │    └─────────────┬──────────────────────┘
             │ HTTP POST /task    │ agent: data-worker         │                  │
             ├────────────────────┴────────────┬───────────────┘                  │
             │                                 │ HTTP POST /task                  │
             │                                 ├──────────────────────────────────┘
             ▼                                 ▼
        (chain_id, delegator_agent_id, task as JSON body)

All three gateways heartbeat independently to MITRITY_CONTROL_PLANE_URL.
Each agent's actions are evaluated by ITS gateway against ITS profile.
The delegation chain accumulates real hops across the three event streams.
```

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | For Claude API calls (shared across all 3 containers) |
| `MITRITY_CONTROL_PLANE_URL` | e.g. `https://api.mitrity.com` |
| `MITRITY_AGENT_ID_ORCHESTRATOR` / `_KEY_ORCHESTRATOR` | Orchestrator agent identity |
| `MITRITY_AGENT_ID_DATA` / `_KEY_DATA` | Data worker agent identity |
| `MITRITY_AGENT_ID_NOTIFY` / `_KEY_NOTIFY` | Notification worker agent identity |

See [`.env.example`](.env.example).

## Troubleshooting

**The orchestrator boots and prints "Skipping delegation/TI scenarios — current tenant is on the Starter plan."**
Your tenant is on Starter. Delegation Chains and Threat Intelligence require Pro or Enterprise. Upgrade at `/app/billing`.

**`/delegation-chains` page is empty after a run.**
Check the orchestrator logs (`docker compose logs orchestrator`) for `chain_id` lines. If chains were emitted but the dashboard shows nothing, verify all three agents are registered to the **same tenant** (an agent registered to the wrong tenant means the worker's events land in a different tenant's audit log).

**`/threat-intel` page shows no matches.**
Confirm `tenant_threat_settings.subscribed_feeds` includes `mitrity_curated` at `/app/threat-intel/settings`.

## License

Copyright © 2026 MITRITY AB. See [LICENSE](LICENSE).
