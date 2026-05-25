FROM golang:1.26-bookworm AS demo-tools-builder
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
RUN CGO_ENABLED=0 go build -o /out/demo-tools ./cmd/demo-tools

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gettext-base \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir anthropic==0.39.0 rich==13.9.4 fastapi==0.115.5 uvicorn==0.32.1 requests==2.32.3

RUN curl -fsSL -o /usr/local/bin/mitrity-gateway \
        "https://github.com/mitrity-io/iag-sentinel/releases/latest/download/mitrity-gateway-linux-amd64" \
    && chmod +x /usr/local/bin/mitrity-gateway

COPY --from=demo-tools-builder /out/demo-tools /usr/local/bin/demo-tools

COPY config/notification-worker.yaml.tmpl /etc/mitrity/gateway.yaml.tmpl
COPY workers/notification-worker/entrypoint.sh /app/entrypoint.sh
COPY workers/notification-worker/server.py /app/server.py

RUN chmod +x /app/entrypoint.sh

EXPOSE 8082
ENTRYPOINT ["/app/entrypoint.sh"]
