FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npx vite build

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM node:20-alpine

WORKDIR /app

# Install ALL deps (express, cors are devDependencies but needed at runtime)
COPY package*.json ./
RUN npm ci

# Copy built UI, proxy server, and vite config (needed for preview proxy rules)
COPY --from=builder /app/dist /app/dist
COPY proxy-server.js /app/proxy-server.js
COPY vite.config.ts /app/vite.config.ts

ENV DCIM_SERVER_URL=http://dcim-server-a:8080
ENV PROXY_PORT=3001

EXPOSE 8000
EXPOSE 3001

# Start proxy (background) + Vite preview (foreground)
CMD ["sh", "-c", "node proxy-server.js & npx vite preview --host 0.0.0.0 --port 8000 --outDir dist"]
