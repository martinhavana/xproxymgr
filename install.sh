#!/usr/bin/env sh
# ─────────────────────────────────────────────────────────────────────────────
# install.sh – XProxy Manager Installer
#
# Supports:
#   • OpenWrt / LEDE  (opkg)
#   • Debian / Ubuntu / Armbian (apt)
#   • Automatic detection of systemd vs init.d service management
#
# Usage:
#   chmod +x install.sh && sudo ./install.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
ok()      { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
die()     { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; exit 1; }
section() { printf "\n${BOLD}═══ %s ═══${NC}\n" "$*"; }

# ── Must run as root ──────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "This installer must be run as root. Try: sudo $0"

# ── Paths ─────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/xproxymgr"
CONFIG_DIR="/etc/xproxymgr"
LOG_DIR="/var/log"
SERVICE_NAME="xproxymgr"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Architecture check ────────────────────────────────────────────────────────
section "Architecture check"
ARCH="$(uname -m)"
info "Detected architecture: ${ARCH}"
case "${ARCH}" in
  arm*|aarch64)
    ok "ARM architecture confirmed – compatible." ;;
  x86_64|i686)
    warn "Running on x86 – installer will proceed (development mode)." ;;
  *)
    warn "Unknown architecture '${ARCH}' – proceeding anyway." ;;
esac

# ── Package manager detection ─────────────────────────────────────────────────
section "Package manager detection"
PKG_MGR=""

if command -v opkg >/dev/null 2>&1; then
    PKG_MGR="opkg"
    info "OpenWrt/opkg detected."
elif command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
    info "Debian/apt detected."
elif command -v apk >/dev/null 2>&1; then
    PKG_MGR="apk"
    info "Alpine/apk detected."
elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
    info "RHEL/yum detected."
else
    die "No supported package manager found (opkg / apt-get / apk / yum)."
fi

# ── Install system dependencies ───────────────────────────────────────────────
section "Installing system dependencies"

install_pkg() {
    case "${PKG_MGR}" in
        opkg)
            opkg update 2>/dev/null || warn "opkg update failed (offline?)"
            opkg install "$@" || warn "opkg install $* failed – continuing" ;;
        apt)
            DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
        apk)
            apk add --no-cache "$@" ;;
        yum)
            yum install -y "$@" ;;
    esac
}

# python3
if ! command -v python3 >/dev/null 2>&1; then
    info "Installing python3…"
    case "${PKG_MGR}" in
        opkg) install_pkg python3 python3-pip ;;
        apt)  install_pkg python3 python3-pip ;;
        apk)  install_pkg python3 py3-pip ;;
        yum)  install_pkg python3 python3-pip ;;
    esac
else
    ok "python3 already installed: $(python3 --version)"
fi

# pip3
if ! command -v pip3 >/dev/null 2>&1; then
    info "Installing pip3…"
    case "${PKG_MGR}" in
        opkg) install_pkg python3-pip ;;
        apt)  install_pkg python3-pip ;;
        apk)  install_pkg py3-pip ;;
        yum)  install_pkg python3-pip ;;
    esac
else
    ok "pip3 already installed."
fi

# 3proxy
if ! command -v 3proxy >/dev/null 2>&1; then
    info "Installing 3proxy…"
    case "${PKG_MGR}" in
        opkg)
            # 3proxy may be in community feeds
            opkg install 3proxy 2>/dev/null || {
                warn "3proxy not in opkg feeds – attempting to build from source…"
                _install_3proxy_from_source
            } ;;
        apt)
            apt-get install -y 3proxy 2>/dev/null || {
                warn "3proxy not in apt – attempting to build from source…"
                _install_3proxy_from_source
            } ;;
        apk)
            apk add 3proxy 2>/dev/null || _install_3proxy_from_source ;;
        yum)
            yum install -y 3proxy 2>/dev/null || _install_3proxy_from_source ;;
    esac
else
    ok "3proxy already installed: $(3proxy -h 2>&1 | head -1 || true)"
fi

_install_3proxy_from_source() {
    info "Building 3proxy from source…"
    if ! command -v gcc >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
        case "${PKG_MGR}" in
            opkg) install_pkg gcc make ;;
            apt)  install_pkg gcc make ;;
            apk)  install_pkg gcc make musl-dev ;;
        esac
    fi
    TMP_DIR="$(mktemp -d)"
    cd "${TMP_DIR}"
    # Download latest release tarball
    if command -v wget >/dev/null 2>&1; then
        wget -q "https://github.com/z3apa3a/3proxy/archive/refs/tags/0.9.4.tar.gz" -O 3proxy.tar.gz
    elif command -v curl >/dev/null 2>&1; then
        curl -sL "https://github.com/z3apa3a/3proxy/archive/refs/tags/0.9.4.tar.gz" -o 3proxy.tar.gz
    else
        die "Neither wget nor curl available – cannot download 3proxy source."
    fi
    tar -xzf 3proxy.tar.gz
    cd 3proxy-*
    make -f Makefile.Linux 2>/dev/null || make
    cp bin/3proxy /usr/bin/3proxy
    chmod +x /usr/bin/3proxy
    cd /
    rm -rf "${TMP_DIR}"
    ok "3proxy built and installed to /usr/bin/3proxy"
}

# ── Python packages ───────────────────────────────────────────────────────────
section "Installing Python packages"
pip3 install --quiet flask requests || {
    warn "pip3 install failed – trying with --break-system-packages flag…"
    pip3 install --quiet --break-system-packages flask requests
}
ok "Flask and requests installed."

# ── Directory structure ───────────────────────────────────────────────────────
section "Creating directories"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${LOG_DIR}"
ok "Directories created."

# ── Copy application files ────────────────────────────────────────────────────
section "Copying application files"
for f in app.py hilink.py proxy_manager.py config.py 3proxy.cfg.template; do
    if [ -f "${SCRIPT_DIR}/${f}" ]; then
        cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}"
        ok "Copied ${f}"
    else
        warn "${f} not found in ${SCRIPT_DIR} – skipping."
    fi
done
chmod 644 "${INSTALL_DIR}"/*.py 2>/dev/null || true

# ── Service manager detection ─────────────────────────────────────────────────
section "Setting up service"
SERVICE_MGR=""
if command -v systemctl >/dev/null 2>&1 && systemctl --version >/dev/null 2>&1; then
    SERVICE_MGR="systemd"
elif [ -d /etc/init.d ]; then
    SERVICE_MGR="initd"
else
    warn "No recognised service manager – service will not be auto-started."
fi

info "Service manager: ${SERVICE_MGR:-none}"

# ── systemd service ────────────────────────────────────────────────────────────
if [ "${SERVICE_MGR}" = "systemd" ]; then
    UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    cat > "${UNIT_FILE}" << EOF
[Unit]
Description=XProxy Manager – 4G Proxy Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=$(command -v python3) ${INSTALL_DIR}/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}" || warn "Service failed to start – check: journalctl -u ${SERVICE_NAME}"
    ok "systemd service enabled and started."

# ── init.d service (OpenWrt / BusyBox) ────────────────────────────────────────
elif [ "${SERVICE_MGR}" = "initd" ]; then
    INIT_FILE="/etc/init.d/${SERVICE_NAME}"
    cat > "${INIT_FILE}" << EOF
#!/bin/sh /etc/rc.common
# XProxy Manager init.d service

START=99
STOP=10
USE_PROCD=1
PROG=$(command -v python3)
APP="${INSTALL_DIR}/app.py"

start_service() {
    procd_open_instance
    procd_set_param command "\${PROG}" "\${APP}"
    procd_set_param respawn 3600 5 5
    procd_set_param env PYTHONUNBUFFERED=1
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
    chmod +x "${INIT_FILE}"
    "${INIT_FILE}" enable
    "${INIT_FILE}" start || warn "Service start failed – try: ${INIT_FILE} start"
    ok "init.d service enabled and started."
fi

# ── Detect LAN IP for access URL ─────────────────────────────────────────────
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${LAN_IP}" ] && LAN_IP="$(ip addr show 2>/dev/null | awk '/inet / && !/127\./ {print $2}' | cut -d/ -f1 | head -1)"
[ -z "${LAN_IP}" ] && LAN_IP="<device-ip>"

# Read configured web port from config.py if possible
WEB_PORT="$(python3 -c 'import sys; sys.path.insert(0,"'"${INSTALL_DIR}"'"); import config; print(config.WEB_PORT)' 2>/dev/null || echo 8080)"

section "Installation complete"
printf "\n"
printf "${GREEN}${BOLD}  XProxy Manager has been installed!${NC}\n\n"
printf "  Dashboard URL  : ${BOLD}http://${LAN_IP}:${WEB_PORT}/${NC}\n"
printf "  Install dir    : ${INSTALL_DIR}\n"
printf "  Config dir     : ${CONFIG_DIR}\n"
printf "  Log file       : /var/log/xproxymgr.log\n\n"

if [ "${SERVICE_MGR}" = "systemd" ]; then
    printf "  Service status : systemctl status ${SERVICE_NAME}\n"
    printf "  Service logs   : journalctl -fu ${SERVICE_NAME}\n"
elif [ "${SERVICE_MGR}" = "initd" ]; then
    printf "  Service status : /etc/init.d/${SERVICE_NAME} status\n"
fi
printf "\n"
printf "  Default SOCKS5 : host:1080  user: proxy  pass: changeme\n"
printf "  ${YELLOW}IMPORTANT: Change the proxy password in ${CONFIG_DIR}/config or via env vars.${NC}\n\n"
