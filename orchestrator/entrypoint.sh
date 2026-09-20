#!/usr/bin/env bash
# Orchestrator entrypoint: validate env, render the gateway config, prepare the
# admission runtime directory, exec the runner.
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
        echo "FATAL: required env var $v is unset. See .env.example for required variables." >&2
        exit 1
    fi
done

# Export the gateway version for the runner's banner.
MITRITY_GATEWAY_VERSION="$(/usr/local/bin/mitrity-gateway -version 2>/dev/null || echo "unknown")"
export MITRITY_GATEWAY_VERSION

envsubst < /etc/mitrity/gateway.yaml.tmpl > /etc/mitrity/gateway.yaml
echo "[orchestrator] gateway.yaml rendered for agent $MITRITY_AGENT_ID"

# Runtime directory for the admission socket and token. The Dockerfile creates
# it owned by the demo user; this only re-asserts mode 0700. The gateway refuses
# a directory it does not own or that is group- or world-writable: anything
# that can bind the socket path could harvest the token and allow everything.
mkdir -p /run/mitrity
chmod 0700 /run/mitrity
export MITRITY_ADMISSION_ADDR="unix:/run/mitrity/admission.sock"
export MITRITY_ADMISSION_TOKEN_FILE="/run/mitrity/admission.token"

# Hand off to the runner. The Claude Agent SDK starts mitrity-gateway as its
# MCP server; the mitrity adapter's PreToolUse hook talks to the admission
# socket above for the SDK's built-in tools.
exec python /app/runner.py "$@"
