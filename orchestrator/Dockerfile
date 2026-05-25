# Orchestrator: Claude-driven agent that delegates work to workers via HTTP.
# Each scenario in runner.py is one or more `delegate_to` tool calls.

FROM golang:1.26-bookworm AS demo-tools-builder
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
RUN CGO_ENABLED=0 go build -o /out/demo-tools ./cmd/demo-tools

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gettext-base \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir anthropic==0.39.0 rich==13.9.4 requests==2.32.3

# Pre-built mitrity-gateway binary from GHCR. Pinned to a version once we
# tag — `latest` is fine during dev.
RUN curl -fsSL -o /usr/local/bin/mitrity-gateway \
        "https://github.com/mitrity-io/iag-sentinel/releases/latest/download/mitrity-gateway-linux-amd64" \
    && chmod +x /usr/local/bin/mitrity-gateway

COPY --from=demo-tools-builder /out/demo-tools /usr/local/bin/demo-tools

COPY config/orchestrator.yaml.tmpl /etc/mitrity/gateway.yaml.tmpl
COPY orchestrator/entrypoint.sh /app/entrypoint.sh
COPY orchestrator/runner.py /app/runner.py

RUN chmod +x /app/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
