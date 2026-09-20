#!/usr/bin/env bash
set -euo pipefail

required=(
    ANTHROPIC_API_KEY
    MITRITY_CONTROL_PLANE_URL
    MITRITY_AGENT_ID
    MITRITY_AGENT_KEY
)
for v in "${required[@]}"; do
    if [ -z "${!v:-}" ]; then
        echo "FATAL: required env var $v is unset" >&2
        exit 1
    fi
done

# Render the gateway config template with env vars. The rendered file carries
# the agent key, so it is created readable by this user only: the umask covers
# the file the subshell creates, the chmod a file that already existed.
(umask 077 && envsubst < /etc/mitrity/gateway.yaml.tmpl > /etc/mitrity/gateway.yaml)
chmod 0600 /etc/mitrity/gateway.yaml
echo "[notification-worker] gateway.yaml rendered for agent $MITRITY_AGENT_ID"

exec python /app/server.py
