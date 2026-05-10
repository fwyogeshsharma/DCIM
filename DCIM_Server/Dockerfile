FROM golang:latest AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=1 GOOS=linux go build -o dcim-server .

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libsqlite3-0 \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/dcim-server /app/dcim-server
COPY config.yaml /app/config.yaml
COPY config.docker.yaml /app/config.docker.yaml
COPY migrations/ /app/migrations/

RUN mkdir -p /app/data /app/certs /app/logs

EXPOSE 8080

CMD ["/app/dcim-server", "-config", "/app/config.docker.yaml"]
