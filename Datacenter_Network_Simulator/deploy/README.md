# Deploy — Google Cloud (Compute Engine)

Deploys the simulator on a single Ubuntu VM:

- **Web UI + REST API** exposed externally over HTTPS (Caddy + Let's Encrypt).
- **All simulated device IPs** bound to a **host-local dummy NIC** — reachable
  only inside the VM, so a poller/NMS on the same box can query them while they
  never leave the host and need no GCP routing.

```
            Internet ──HTTPS:443──▶ Caddy ──localhost:8001──▶ simulator (headless)
                                                                 │ binds IPs → dcim0
   VM ──────────────────────────────────────────────────────────┼───────────────
        poller / openDCIM ──polls SNMP/gNMI (host-local)─────────┘
```

> Why a VM and not Cloud Run / App Engine: the simulator binds hundreds of
> virtual IPs to a NIC and serves raw UDP (SNMP/gNMI/sFlow/BACnet). That needs
> root + NET_ADMIN on a real Linux host. Serverless can't do it.

## Files

| File | Purpose |
|------|---------|
| `gcp-setup.sh` | Provisions steps 2–4 on the VM (dummy NIC, deps, build, service) |
| `dcim-dummy.service` | systemd unit creating the host-local dummy NIC (`dcim0`) |
| `dcim.service` | systemd unit running the headless simulator on `:8001` |
| `Caddyfile` | Caddy reverse-proxy config (HTTPS termination, SSE-aware) |

The `.service` files and Caddyfile contain `__PLACEHOLDERS__` / `sim.example.com`
that `gcp-setup.sh` (or you) substitute. Don't apply them raw.

## 1. Create the VM (from your workstation)

```bash
gcloud compute instances create dcim-sim \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --zone=us-central1-a --boot-disk-size=30GB --tags=dcim

gcloud compute addresses create dcim-ip --region=us-central1   # static IP; attach it

gcloud compute firewall-rules create dcim-web \
  --allow=tcp:80,tcp:443 --target-tags=dcim --source-ranges=0.0.0.0/0
```

Only 80/443 are open. SNMP/gNMI/8001 are never exposed.

## 2–4. Provision on the VM

```bash
gcloud compute ssh dcim-sim --zone=us-central1-a
sudo apt install -y git
git clone <your-repo> Datacenter_Network_Simulator
cd Datacenter_Network_Simulator
sudo bash deploy/gcp-setup.sh
```

> Use `bash deploy/gcp-setup.sh` (not `./...`) so it works even if the execute
> bit is missing after a Windows checkout. If the script errors on the first
> line, strip CRLF: `sed -i 's/\r$//' deploy/*.sh deploy/*.service`.

This installs deps, builds the web UI, creates the dummy NIC service, and installs
the app service. It warns if `auth.env` is missing.

Custom dummy interface name: `sudo DCIM_DUMMY_IF=mysim0 ./deploy/gcp-setup.sh`.

## 5. Credentials (if not done yet)

```bash
cp auth.env.example auth.env
.venv/bin/python -m api.auth set-password    # single-quote the hash in auth.env
chmod 600 auth.env
sudo systemctl start dcim
```

## 6. TLS

```bash
sudo apt install -y caddy
sudo nano deploy/Caddyfile          # set your domain; point its A record at the static IP
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

No domain? Use the `tls internal` (self-signed) snippet in `Caddyfile`, or access
`http://<VM-IP>:8001` behind a tightened firewall — open 8001 only to your IP:
`--source-ranges=<your-ip>/32`.

## 7. Bind device IPs + start protocols

Web UI **Binding** panel → select `dcim0` → **Bind**, then start SNMP/gNMI.
Or via API:

```bash
TOKEN=$(curl -s -X POST https://sim.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOURPASS"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -X POST https://sim.example.com/api/binding/adapter \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"adapter":"dcim0"}'
curl -X POST https://sim.example.com/api/binding/bind -H "Authorization: Bearer $TOKEN"
```

## 8. Poller on the same box

Install openDCIM / your NMS on this VM and point it at the simulated device IPs
(live on `dcim0`, host-local). SNMP/gNMI work locally — no GCP networking.

## Caveats

- ⚠️ **Keep device IPs OUT of the VPC subnet** (default `10.128.0.0/20`). Overlap
  causes routing ambiguity. Use e.g. `192.0.2.0/24` / `198.51.100.0/24` or a
  non-VPC `10.x` block.
- ⚠️ Binding is **not automatic** in headless — redo step 7 after each boot
  (ask for the `DCIM_AUTOBIND_ADAPTER` flag if you want boot-time auto-bind).
- ⚠️ `auth.env` / `auth_users.json` are gitignored → recreate on the VM.
- ⚠️ Size CPU/RAM to topology — hundreds of devices + a poller on one box wants
  `e2-standard-4`+.
- Check logs: `journalctl -u dcim -f` · `journalctl -u dcim-dummy`.

## Update after a code change

```bash
git pull
.venv/bin/pip install -r requirements.txt
( cd webui && npm ci && npm run build )
sudo systemctl restart dcim
```
