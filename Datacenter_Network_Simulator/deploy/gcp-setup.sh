#!/usr/bin/env bash
#
# Provision steps 2–4 of the GCP Compute Engine deployment:
#   2. host-local dummy NIC (holds the simulated device IPs)
#   3. OS deps + Python venv + web UI build
#   4. the headless systemd service
#
# Run ON the VM, from the repo root, as root:
#     sudo ./deploy/gcp-setup.sh
#
# Manual steps NOT done here (see deploy/README.md):
#   - VM creation + static IP + firewall (gcloud, from your workstation)
#   - auth.env (credentials)         -> warned about below if missing
#   - Caddy / TLS / domain            -> deploy/Caddyfile
#   - binding device IPs to the dummy -> Web UI Binding panel or /api/binding
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Must run as root:  sudo ./deploy/gcp-setup.sh" >&2
    exit 1
fi

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$DEPLOY_DIR")"
DUMMY_IF="${DCIM_DUMMY_IF:-dcim0}"
IP_BIN="$(command -v ip || echo /usr/sbin/ip)"
PY="$REPO_DIR/.venv/bin/python"

echo "[*] Repo:            $REPO_DIR"
echo "[*] Dummy interface: $DUMMY_IF"
echo "[*] ip binary:       $IP_BIN"

# ── Step 3: OS packages ──────────────────────────────────────────────────────
echo "[*] Installing OS packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
    python3-venv python3-pip nodejs npm git \
    libgl1 libglib2.0-0 libxkbcommon0 libegl1

# ── Step 3: Python venv + dependencies ───────────────────────────────────────
if [[ ! -d "$REPO_DIR/.venv" ]]; then
    echo "[*] Creating virtualenv…"
    python3 -m venv "$REPO_DIR/.venv"
fi
echo "[*] Installing Python dependencies…"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

# ── Step 3: web UI build ─────────────────────────────────────────────────────
echo "[*] Building web UI…"
( cd "$REPO_DIR/webui" && npm ci && npm run build )

# ── Step 2: dummy NIC service ────────────────────────────────────────────────
echo "[*] Installing dcim-dummy.service…"
sed -e "s#__IP__#$IP_BIN#g" \
    -e "s#__DUMMY_IF__#$DUMMY_IF#g" \
    "$DEPLOY_DIR/dcim-dummy.service" > /etc/systemd/system/dcim-dummy.service

# ── Step 4: app service ──────────────────────────────────────────────────────
echo "[*] Installing dcim.service…"
sed -e "s#__INSTALL_DIR__#$REPO_DIR#g" \
    "$DEPLOY_DIR/dcim.service" > /etc/systemd/system/dcim.service

systemctl daemon-reload
systemctl enable --now dcim-dummy
echo "[*] dcim-dummy up — '$DUMMY_IF' ready for device IPs"

# ── auth.env gate ────────────────────────────────────────────────────────────
if [[ -s "$REPO_DIR/auth.env" ]] && grep -q 'DCIM_AUTH_SECRET' "$REPO_DIR/auth.env"; then
    chmod 600 "$REPO_DIR/auth.env"
    systemctl enable --now dcim
    echo
    echo "[✓] dcim.service started on :8001"
else
    systemctl enable dcim
    echo
    echo "[!] auth.env missing/empty — service enabled but NOT started."
    echo "    Set up credentials, then start it:"
    echo "      cp $REPO_DIR/auth.env.example $REPO_DIR/auth.env"
    echo "      $PY -m api.auth set-password      # single-quote the hash in auth.env"
    echo "      chmod 600 $REPO_DIR/auth.env"
    echo "      systemctl start dcim"
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
Steps 2–4 done. Remaining (see deploy/README.md):
  • Firewall (run from workstation):
      gcloud compute firewall-rules create dcim-web \\
        --allow=tcp:80,tcp:443 --target-tags=dcim --source-ranges=0.0.0.0/0
  • TLS: edit deploy/Caddyfile (your domain) →
      sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
  • Bind device IPs to '$DUMMY_IF': Web UI Binding panel, or POST /api/binding
  • Keep simulated device IPs OUT of the VPC subnet (default 10.128.0.0/20)
────────────────────────────────────────────────────────────────────
EOF
