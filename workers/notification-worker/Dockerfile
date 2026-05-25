FROM --platform=linux/amd64 golang:1.26-bookworm AS demo-tools-builder
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/demo-tools ./cmd/demo-tools

FROM --platform=linux/amd64 ghcr.io/mitrity-io/mitrity-mcp-gateway:v0.5.0 AS gateway

FROM --platform=linux/amd64 python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --system --group --home /app appuser

RUN pip install --no-cache-dir anthropic==0.39.0 rich==13.9.4 fastapi==0.115.5 uvicorn==0.32.1 requests==2.32.3

COPY --from=gateway /mitrity-gateway /usr/local/bin/mitrity-gateway
COPY --from=demo-tools-builder /out/demo-tools /usr/local/bin/demo-tools

COPY config/notification-worker.yaml.tmpl /etc/mitrity/gateway.yaml.tmpl
COPY workers/notification-worker/entrypoint.sh /app/entrypoint.sh
COPY workers/notification-worker/server.py /app/server.py

RUN chmod +x /app/entrypoint.sh && chown -R appuser:appuser /app /etc/mitrity

USER appuser
EXPOSE 8082
ENTRYPOINT ["/app/entrypoint.sh"]
