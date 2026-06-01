#!/bin/bash
# Run this ONCE on the VM before starting the full stack.
# It creates a dummy cert so Nginx can start, then swaps in a real Let's Encrypt cert.
#
# Run from this directory (e.g. /home/DCIM/simulator): `bash init-letsencrypt.sh`.
# It drives the ROOT compose (../docker-compose.yml), whose nginx/certbot services
# bind-mount ./simulator/certbot/{conf,www}. Because this script's CERT_PATH
# (./certbot/conf/...) resolves to that same host dir, the dummy/real cert it writes
# is exactly what nginx serves — one source of truth, no named volumes.

set -e

COMPOSE="docker compose -f ../docker-compose.yml"

DOMAIN="fwdcim.faberwork.com"
EMAIL="aman.yadav@faberwork.com"
CERT_PATH="./certbot/conf/live/$DOMAIN"

echo "==> Checking for existing certificate..."
if [ -d "$CERT_PATH" ]; then
  echo "    Certificate already exists, skipping init."
  exit 0
fi

echo "==> Creating dummy self-signed cert so Nginx can start..."
mkdir -p "$CERT_PATH"
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -keyout "$CERT_PATH/privkey.pem" \
  -out    "$CERT_PATH/fullchain.pem" \
  -subj "/CN=$DOMAIN" 2>/dev/null

# Copy TLS params if they don't exist
mkdir -p ./certbot/conf
if [ ! -f "./certbot/conf/options-ssl-nginx.conf" ]; then
  curl -s https://raw.githubusercontent.com/certonly/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
    > ./certbot/conf/options-ssl-nginx.conf
fi

echo "==> Starting Nginx with dummy cert..."
$COMPOSE up -d nginx

echo "==> Waiting for Nginx to be ready..."
sleep 5

echo "==> Deleting dummy cert..."
rm -rf "./certbot/conf/live"
rm -rf "./certbot/conf/archive"
rm -rf "./certbot/conf/renewal"

echo "==> Requesting real Let's Encrypt certificate..."
$COMPOSE run --rm certbot \
  certonly --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

echo "==> Reloading Nginx with real certificate..."
$COMPOSE exec nginx nginx -s reload

echo ""
echo "Done! https://$DOMAIN is live."
