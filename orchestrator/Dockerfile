# Orchestrator: Claude-driven agent that delegates work to workers via HTTP.
# Each scenario in runner.py is one or more `delegate_to` tool calls.

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

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --system --group --home /app appuser

COPY --from=go-builder /demo-tools /usr/local/bin/demo-tools
COPY --from=gateway /mitrity-gateway /usr/local/bin/mitrity-gateway

COPY config/orchestrator.yaml.tmpl /etc/mitrity/gateway.yaml.tmpl
COPY orchestrator/requirements.txt /app/requirements.txt
COPY orchestrator/entrypoint.sh /app/entrypoint.sh
COPY orchestrator/runner.py /app/runner.py

RUN pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
RUN chmod +x /app/entrypoint.sh && chown -R appuser:appuser /app /etc/mitrity

USER appuser
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
