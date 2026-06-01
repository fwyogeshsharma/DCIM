#!/usr/bin/env bash
# Bootstrap the Let's Encrypt certificate for fwdcim.faberwork.com.
#
# Run ONCE on the production host the first time you bring the stack up (or any
# time the certbot_certs volume has been wiped). It breaks the nginx <-> certbot
# chicken-and-egg: nginx won't start without a cert, but certbot needs nginx on
# port 80 to answer the ACME HTTP-01 challenge.
#
# Steps:
#   1. Write a 1-day self-signed dummy cert into the certbot_certs volume so
#      nginx can load `ssl_certificate` and start.
#   2. Start nginx (now serving /.well-known/acme-challenge on port 80).
#   3. Delete the dummy and request the real cert from Let's Encrypt (webroot).
#   4. Reload nginx to pick up the real cert.
#
# Preconditions (HTTP-01 will FAIL otherwise):
#   - fwdcim.faberwork.com resolves publicly to THIS host.
#   - Inbound TCP 80 is open to this host (firewall / security group).
#
# Usage:   ./init-letsencrypt.sh
set -euo pipefail

DOMAIN="fwdcim.faberwork.com"
EMAIL="hr@faberwork.com"     # Let's Encrypt expiry-notice contact
RSA_KEY_SIZE=4096
STAGING=0                    # set to 1 to hit LE staging while testing (avoids rate limits)

# `docker compose` (v2) or legacy `docker-compose` (v1)
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi

LIVE_PATH="/etc/letsencrypt/live/$DOMAIN"

echo "### 1/4  Creating temporary self-signed cert for $DOMAIN ..."
$DC run --rm --entrypoint /bin/sh certbot -c "\
  mkdir -p '$LIVE_PATH' && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '$LIVE_PATH/privkey.pem' \
    -out    '$LIVE_PATH/fullchain.pem' \
    -subj '/CN=localhost'"

echo "### 2/4  Starting nginx with the dummy cert ..."
$DC up -d nginx
sleep 3

echo "### 3/4  Deleting dummy cert and requesting the real one ..."
$DC run --rm --entrypoint /bin/sh certbot -c "\
  rm -rf /etc/letsencrypt/live/$DOMAIN \
         /etc/letsencrypt/archive/$DOMAIN \
         /etc/letsencrypt/renewal/$DOMAIN.conf"

STAGING_ARG=""
if [ "$STAGING" != "0" ]; then STAGING_ARG="--staging"; fi

$DC run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
    $STAGING_ARG \
    --email "$EMAIL" \
    -d "$DOMAIN" \
    --rsa-key-size "$RSA_KEY_SIZE" \
    --agree-tos --no-eff-email --force-renewal

echo "### 4/4  Reloading nginx with the real cert ..."
$DC exec nginx nginx -s reload

echo "### Done. The certbot service will auto-renew from here on."
