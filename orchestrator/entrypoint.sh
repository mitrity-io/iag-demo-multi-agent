#!/usr/bin/env bash
# Orchestrator entrypoint: validate env, render gateway config, exec runner.
set -euo pipefail

required=(
    ANTHROPIC_API_KEY
    MITRITY_CONTROL_PLANE_URL
    MITRITY_AGENT_ID
    MITRITY_AGENT_KEY
    DATA_WORKER_URL
    NOTIFY_WORKER_URL
)
for v in "${required[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "FATAL: required env var $v is unset" >&2
        exit 1
    fi
done

envsubst < /etc/mitrity/gateway.yaml.tmpl > /etc/mitrity/gateway.yaml
echo "[orchestrator] gateway.yaml rendered for agent $MITRITY_AGENT_ID"

# Hand off to the Python runner. The runner spawns mitrity-gateway as a
# subprocess and speaks MCP JSON-RPC over stdio.
exec python /app/runner.py
