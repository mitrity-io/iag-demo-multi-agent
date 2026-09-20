# Orchestrator: a Claude Agent SDK agent that delegates work to the workers
# through its own Mitrity Gateway. Each scenario in phases/phase7_delegation_chains.py
# is one `delegate__delegate_to` tool call; the workers delegate further on their own.

FROM --platform=linux/amd64 golang:1.26-alpine AS go-builder

WORKDIR /build
COPY go.mod ./
RUN go mod download
COPY cmd/ cmd/
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -trimpath -o /demo-tools ./cmd/demo-tools

# Pre-built mitrity-gateway binary from the public GHCR image.
# Uses :latest so customers automatically pick up current gateway
# features and bug fixes — wire protocol is committed backwards-compatible
# across patch + minor releases. Matches the single-agent gateway demo
# pattern. --platform=linux/amd64 to match the Python + Go stages
# (cross-arch COPY between stages fails at runtime with ENOENT).
FROM --platform=linux/amd64 ghcr.io/mitrity-io/mitrity-mcp-gateway:latest AS gateway

FROM --platform=linux/amd64 python:3.12-slim

# gettext-base: envsubst renders the gateway config in entrypoint.sh.
# ca-certificates: TLS to PyPI at build time and to the control plane at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-builder /demo-tools /usr/local/bin/demo-tools
COPY --from=gateway /mitrity-gateway /usr/local/bin/mitrity-gateway

COPY config/orchestrator.yaml.tmpl /etc/mitrity/gateway.yaml.tmpl
COPY orchestrator/requirements.txt orchestrator/entrypoint.sh orchestrator/runner.py orchestrator/output.py /app/
COPY orchestrator/phases/ /app/phases/

# The Claude Agent SDK wheel bundles the Claude Code runtime; no Node.js needed.
RUN pip install --no-cache-dir -r /app/requirements.txt

# The MITRITY adapter from PyPI, pinned to a release. Override for another version:
# docker compose build --build-arg MITRITY_PYTHON_SPEC='mitrity[claude-agent-sdk]==X.Y.Z'.
ARG MITRITY_PYTHON_SPEC="mitrity[claude-agent-sdk]==0.2.0"
RUN pip install --no-cache-dir "$MITRITY_PYTHON_SPEC"

# The orchestrator runs as an unprivileged user (uid 1000, as in the gateway
# demo). The gateway refuses an admission runtime directory it does not own,
# so /run/mitrity is created for this user before the switch (mode 0700;
# entrypoint.sh checks the ownership and re-asserts the mode). /etc/mitrity
# takes the rendered gateway.yaml (mode 0600: it carries the agent key),
# /workspace is the root of the orchestrator's read-only filesystem tools and
# the gateway's transport key, and $HOME the Claude Code session state.
RUN groupadd --gid 1000 demo \
    && useradd --uid 1000 --gid demo --create-home demo \
    && mkdir -p /run/mitrity /workspace \
    && chmod 0700 /run/mitrity \
    && chown -R demo:demo /run/mitrity /etc/mitrity /workspace \
    && chmod +x /app/entrypoint.sh

WORKDIR /app
USER demo
ENV HOME=/home/demo

ENTRYPOINT ["/app/entrypoint.sh"]
