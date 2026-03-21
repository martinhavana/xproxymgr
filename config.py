"""
config.py - XProxy Manager Configuration
Centralized configuration with environment variable overrides.
"""

import os

# ─────────────────────────────────────────────
# Dongle / Hilink settings
# ─────────────────────────────────────────────
DONGLE_HOST = os.environ.get("DONGLE_HOST", "192.168.101.1")
DONGLE_HOSTS = os.environ.get("DONGLE_HOSTS", "192.168.101.1,192.168.102.1")

# ─────────────────────────────────────────────
# Proxy settings
# ─────────────────────────────────────────────
PROXY_PORT = int(os.environ.get("PROXY_PORT", 1080))
PROXY_USER = os.environ.get("PROXY_USER", "proxy")
PROXY_PASS = os.environ.get("PROXY_PASS", "changeme")

# ─────────────────────────────────────────────
# External access
# ─────────────────────────────────────────────
# Public hostname shown in dashboard access info (DuckDNS or IP)
EXTERNAL_HOST = os.environ.get("EXTERNAL_HOST", "havanawin.duckdns.org")

# ─────────────────────────────────────────────
# Web dashboard settings
# ─────────────────────────────────────────────
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", 8080))

# ─────────────────────────────────────────────
# IP rotation settings
# ─────────────────────────────────────────────
# Maximum seconds to wait for a new IP after reconnect
ROTATE_WAIT_TIMEOUT = int(os.environ.get("ROTATE_WAIT_TIMEOUT", 60))
# Seconds between polling attempts while waiting for new IP
ROTATE_POLL_INTERVAL = int(os.environ.get("ROTATE_POLL_INTERVAL", 3))

# ─────────────────────────────────────────────
# File / directory paths
# ─────────────────────────────────────────────
CONFIG_DIR      = os.environ.get("CONFIG_DIR",  "/etc/xproxymgr")
PROXY_CFG_PATH  = os.environ.get("PROXY_CFG",   "/etc/xproxymgr/3proxy.cfg")
PROXY_BIN       = os.environ.get("PROXY_BIN",   "/usr/bin/3proxy")
LOG_FILE        = os.environ.get("LOG_FILE",    "/var/log/xproxymgr.log")
PROXY_LOG_FILE  = os.environ.get("PROXY_LOG",   "/var/log/3proxy.log")
PID_FILE        = os.environ.get("PID_FILE",    "/var/run/3proxy.pid")

# ─────────────────────────────────────────────
# Monitoring / background thread
# ─────────────────────────────────────────────
# How often the background monitor thread polls status (seconds)
MONITOR_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", 15))

# ─────────────────────────────────────────────
# Log ring-buffer size (lines kept in memory for /api/logs)
# ─────────────────────────────────────────────
LOG_BUFFER_SIZE = int(os.environ.get("LOG_BUFFER_SIZE", 200))

# ─────────────────────────────────────────────
# Telegram alerts
# ─────────────────────────────────────────────
TG_TOKEN        = os.environ.get("TG_TOKEN",   "8705907799:AAF5jJYCYPx3rNkSZ6B-b2agHp-30aDxh7g")
TG_CHAT_ID      = os.environ.get("TG_CHAT_ID", "435328284")
# Seconds of downtime before sending alert (grace period)
PROXY_DOWN_GRACE     = int(os.environ.get("PROXY_DOWN_GRACE",     240))
# How often to check SOCKS5 proxies (seconds)
PROXY_CHECK_INTERVAL = int(os.environ.get("PROXY_CHECK_INTERVAL", 30))
# SOCKS5 ports per dongle (order matches DONGLE_HOSTS)
SOCKS5_PORTS    = os.environ.get("SOCKS5_PORTS", "1080,1081")
