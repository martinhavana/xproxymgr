"""
app.py - XProxy Manager – Flask Web Dashboard & REST API
Serves the single-page dashboard and exposes a JSON API consumed
both by the dashboard and by external automation.
"""

import json
import logging
import logging.handlers
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict

from flask import Flask, jsonify, render_template_string, request

import config
import hilink
import proxy_manager

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup – ring-buffer handler so /api/logs can return recent entries
# ─────────────────────────────────────────────────────────────────────────────

_log_buffer: deque = deque(maxlen=config.LOG_BUFFER_SIZE)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_buffer.append(
            {
                "ts":    datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "msg":   self.format(record),
            }
        )


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s – %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file log
    try:
        os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        logging.warning("Could not open log file %s: %s", config.LOG_FILE, exc)

    # In-memory ring buffer
    bh = _BufferHandler()
    bh.setLevel(logging.INFO)
    bh.setFormatter(fmt)
    root.addHandler(bh)


_setup_logging()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Global state – written by the background monitor thread, read by the API
# ─────────────────────────────────────────────────────────────────────────────

_state: Dict[str, Any] = {
    "current_ip":       None,
    "connection_status": "unknown",
    "network_type":     "unknown",
    "signal_icon":      0,
    "proxy_running":    False,
    "uptime_start":     time.time(),
    "last_rotate":      None,
    "rotate_in_progress": False,
    "dongles":          [],
}
_state_lock = threading.Lock()


def _update_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def _get_state() -> Dict[str, Any]:
    with _state_lock:
        return dict(_state)


# ─────────────────────────────────────────────────────────────────────────────
# Background monitor thread
# ─────────────────────────────────────────────────────────────────────────────

DONGLE_PANELS = [h.strip() for h in config.DONGLE_HOSTS.split(",") if h.strip()]

# Per-dongle client cache: host → XH22Client
_dongle_clients: Dict[str, hilink.XH22Client] = {}
_dongle_clients_lock = threading.Lock()


def _get_dongle_client(host: str) -> hilink.XH22Client:
    with _dongle_clients_lock:
        if host not in _dongle_clients:
            _dongle_clients[host] = hilink.XH22Client(host=host)
        return _dongle_clients[host]


def _monitor_loop() -> None:
    """Periodically refreshes dongle and proxy status into _state."""
    logger.info("Monitor thread started (interval=%ds)", config.MONITOR_INTERVAL)

    while True:
        try:
            running = proxy_manager.is_running()
            detected_dongles = []

            for idx, panel_host in enumerate(DONGLE_PANELS):
                try:
                    client = _get_dongle_client(panel_host)
                    conn   = client.get_connection_status()
                    cur_ip = client.get_current_ip() if conn == "connected" else None
                    # Infer interface name from index: eth1, eth2, ...
                    iface  = f"eth{idx + 1}"
                    detected_dongles.append({
                        "index":     idx,
                        "host":      panel_host,
                        "interface": iface,
                        "status":    conn,
                        "ip":        cur_ip,
                    })
                    logger.debug("Dongle %d (%s): %s ip=%s", idx, panel_host, conn, cur_ip)
                except Exception as exc:
                    logger.debug("Dongle %d (%s) unreachable: %s", idx, panel_host, exc)
                    # Remove stale client so next cycle retries login
                    with _dongle_clients_lock:
                        _dongle_clients.pop(panel_host, None)

            # Primary dongle info (first connected dongle)
            primary = next((d for d in detected_dongles if d["status"] == "connected"), None)
            if not primary and detected_dongles:
                primary = detected_dongles[0]

            _update_state(
                current_ip        = primary["ip"] if primary else None,
                connection_status = primary["status"] if primary else "unknown",
                network_type      = "LTE/4G" if (primary and primary["status"] == "connected") else "–",
                signal_icon       = 4 if (primary and primary["status"] == "connected") else 0,
                proxy_running     = running,
                dongles           = detected_dongles,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Monitor error: %s", exc)

        time.sleep(config.MONITOR_INTERVAL)


_monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="monitor")
_monitor_thread.start()

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard HTML (inline – no external files needed)
# ─────────────────────────────────────────────────────────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>XProxy Manager</title>
<style>
  :root {
    --bg:       #0f1117;
    --surface:  #1a1d27;
    --surface2: #232635;
    --border:   #2e3147;
    --accent:   #4f8ef7;
    --accent2:  #7c5ce0;
    --green:    #00d17a;
    --red:      #ff4757;
    --yellow:   #ffd32a;
    --text:     #e2e8f0;
    --muted:    #8892a4;
    --radius:   10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 14px; min-height: 100vh;
  }
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 14px 24px; display: flex; align-items: center;
    justify-content: space-between;
  }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: .5px; }
  header h1 span { color: var(--accent); }
  #uptime { color: var(--muted); font-size: 12px; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px; padding: 20px 24px;
  }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px;
  }
  .card-title {
    color: var(--muted); font-size: 11px; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 12px;
  }
  .value { font-size: 22px; font-weight: 700; word-break: break-all; }
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 12px; font-weight: 600; text-transform: capitalize;
  }
  .badge-green  { background: rgba(0,209,122,.15); color: var(--green); }
  .badge-red    { background: rgba(255,71,87,.15);  color: var(--red);   }
  .badge-yellow { background: rgba(255,211,42,.15); color: var(--yellow);}
  .badge-grey   { background: rgba(136,146,164,.15);color: var(--muted); }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 16px; border: none; border-radius: 6px;
    font-size: 13px; font-weight: 600; cursor: pointer;
    transition: opacity .15s, transform .1s;
  }
  .btn:hover { opacity: .85; }
  .btn:active { transform: scale(.97); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-primary { background: var(--accent);  color: #fff; }
  .btn-success { background: var(--green);   color: #000; }
  .btn-danger  { background: var(--red);     color: #fff; }
  .btn-purple  { background: var(--accent2); color: #fff; }
  .btn-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
  .signal-bar {
    display: flex; align-items: flex-end; gap: 3px; height: 24px;
  }
  .signal-bar span {
    width: 7px; background: var(--border); border-radius: 2px;
    transition: background .3s;
  }
  .signal-bar span.active { background: var(--green); }
  .signal-bar span:nth-child(1) { height: 30%; }
  .signal-bar span:nth-child(2) { height: 50%; }
  .signal-bar span:nth-child(3) { height: 65%; }
  .signal-bar span:nth-child(4) { height: 82%; }
  .signal-bar span:nth-child(5) { height: 100%; }
  .row-between { display: flex; justify-content: space-between; align-items: center; }
  #logs-wrap {
    margin: 0 24px 24px; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--radius);
    overflow: hidden;
  }
  #logs-title {
    padding: 12px 16px; background: var(--surface2);
    border-bottom: 1px solid var(--border); font-size: 12px;
    color: var(--muted); text-transform: uppercase; letter-spacing: 1px;
    display: flex; justify-content: space-between; align-items: center;
  }
  #logs {
    max-height: 260px; overflow-y: auto; font-family: monospace;
    font-size: 12px; padding: 10px 16px; line-height: 1.7;
  }
  #logs .entry { display: flex; gap: 12px; }
  #logs .ts   { color: var(--muted); white-space: nowrap; }
  #logs .lvl-INFO    { color: var(--accent); }
  #logs .lvl-WARNING { color: var(--yellow); }
  #logs .lvl-ERROR   { color: var(--red);    }
  #logs .lvl-DEBUG   { color: var(--muted);  }
  #toast {
    position: fixed; bottom: 24px; right: 24px; padding: 12px 20px;
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; font-size: 13px; opacity: 0;
    transition: opacity .3s; pointer-events: none; z-index: 100;
  }
  #toast.show { opacity: 1; }
  #spinner {
    display: none; width: 14px; height: 14px;
    border: 2px solid rgba(255,255,255,.3);
    border-top-color: #fff; border-radius: 50%;
    animation: spin .6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .dongle-list { display: flex; flex-direction: column; gap: 8px; }
  .dongle-item {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px 12px;
  }
  .dongle-item .dname { font-weight: 600; margin-bottom: 4px; }
  .dongle-item .dmeta { color: var(--muted); font-size: 12px; }
  #refresh-countdown { font-size: 11px; color: var(--muted); }
</style>
</head>
<body>

<header>
  <h1>X<span>Proxy</span> Manager</h1>
  <div style="display:flex;gap:16px;align-items:center;">
    <span id="refresh-countdown"></span>
    <span id="uptime">–</span>
  </div>
</header>

<div class="grid">

  <!-- Current IP -->
  <div class="card">
    <div class="card-title">Current WAN IP</div>
    <div class="value" id="current-ip">–</div>
    <div class="btn-row">
      <button class="btn btn-primary" id="btn-rotate" onclick="rotateIp()">
        <div id="spinner"></div>
        &#8635; Rotate IP
      </button>
    </div>
  </div>

  <!-- Proxy Status -->
  <div class="card">
    <div class="card-title">SOCKS5 Proxy</div>
    <div class="row-between">
      <span class="badge" id="proxy-badge">–</span>
      <span id="proxy-port" style="color:var(--muted);font-size:13px;"></span>
    </div>
    <div class="btn-row">
      <button class="btn btn-success" onclick="proxyAction('start')">&#9654; Start</button>
      <button class="btn btn-danger"  onclick="proxyAction('stop')">&#9632; Stop</button>
    </div>
  </div>

  <!-- Connection -->
  <div class="card">
    <div class="card-title">Connection</div>
    <div class="row-between">
      <div>
        <span class="badge" id="conn-badge">–</span>
        <span id="net-type" style="margin-left:8px;font-size:13px;color:var(--muted);"></span>
      </div>
      <div class="signal-bar" id="signal-bar">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
    </div>
    <div style="margin-top:10px;color:var(--muted);font-size:12px;" id="operator-name"></div>
  </div>

  <!-- Last Rotate -->
  <div class="card">
    <div class="card-title">Last IP Rotation</div>
    <div class="value" style="font-size:15px;" id="last-rotate">Never</div>
    <div style="margin-top:8px;font-size:12px;color:var(--muted);" id="last-rotate-detail"></div>
  </div>

</div>

<!-- Dongle List -->
<div style="padding: 0 24px 16px;">
  <div class="card">
    <div class="card-title">Connected Dongles</div>
    <div class="dongle-list" id="dongle-list">
      <div style="color:var(--muted);font-size:13px;">Loading…</div>
    </div>
  </div>
</div>

<!-- Log Viewer -->
<div id="logs-wrap">
  <div id="logs-title">
    <span>Recent Logs</span>
    <button class="btn" style="font-size:11px;padding:3px 10px;background:var(--surface);border:1px solid var(--border);"
            onclick="loadLogs()">&#8635; Refresh</button>
  </div>
  <div id="logs">Loading logs…</div>
</div>

<div id="toast"></div>

<script>
const AUTO_REFRESH_S = 10;
let countdown = AUTO_REFRESH_S;
let refreshTimer;

function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = ok ? 'var(--green)' : 'var(--red)';
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}

function apiFetch(path, opts = {}) {
  return fetch(path, opts).then(r => r.json());
}

function fmtUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `Up ${d}d ${h}h`;
  if (h > 0) return `Up ${h}h ${m}m`;
  return `Up ${m}m ${s}s`;
}

function setSignal(level) {
  const bars = document.querySelectorAll('#signal-bar span');
  bars.forEach((b, i) => {
    b.classList.toggle('active', i < level);
  });
}

function connBadgeClass(s) {
  if (s === 'connected')    return 'badge-green';
  if (s === 'disconnected') return 'badge-red';
  if (s.includes('ing'))    return 'badge-yellow';
  return 'badge-grey';
}

function loadStatus() {
  apiFetch('/api/status').then(d => {
    document.getElementById('current-ip').textContent = d.current_ip || '–';
    document.getElementById('uptime').textContent     = fmtUptime(d.uptime_seconds || 0);

    const pb = document.getElementById('proxy-badge');
    pb.textContent = d.proxy_running ? 'Running' : 'Stopped';
    pb.className   = 'badge ' + (d.proxy_running ? 'badge-green' : 'badge-red');
    document.getElementById('proxy-port').textContent = d.proxy_running ? `:{{ proxy_port }}` : '';

    const cb = document.getElementById('conn-badge');
    cb.textContent = d.connection_status || '–';
    cb.className   = 'badge ' + connBadgeClass(d.connection_status || '');

    document.getElementById('net-type').textContent = d.network_type || '';
    setSignal(parseInt(d.signal_icon) || 0);

    if (d.last_rotate) {
      document.getElementById('last-rotate').textContent = new Date(d.last_rotate * 1000).toLocaleTimeString();
      document.getElementById('last-rotate-detail').textContent =
        d.last_rotate_result ? `${d.last_rotate_result.old_ip} → ${d.last_rotate_result.new_ip}` : '';
    }
  }).catch(() => toast('Status fetch failed', false));
}

function rotateDongle(idx) {
  const btn = document.getElementById('btn-rotate-' + idx);
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  toast('Rotating dongle ' + idx + ' IP…');
  apiFetch('/api/rotate/' + idx, { method: 'POST' }).then(d => {
    if (d.success) {
      toast('Dongle ' + idx + ' new IP: ' + d.new_ip + ' (was ' + (d.old_ip || '?') + ')');
    } else {
      toast('Rotation failed: ' + (d.error || 'unknown'), false);
    }
    loadStatus(); loadDongles(); loadLogs();
  }).catch(() => toast('Rotate request failed', false))
    .finally(() => { if (btn) { btn.disabled = false; btn.textContent = '⟳ Rotate'; } });
}

function loadDongles() {
  apiFetch('/api/dongles').then(d => {
    const list = document.getElementById('dongle-list');
    if (!d.dongles || d.dongles.length === 0) {
      list.innerHTML = '<div style="color:var(--muted);font-size:13px;">No dongles detected.</div>';
      return;
    }
    list.innerHTML = d.dongles.map(g => `
      <div class="dongle-item">
        <div class="dname" style="display:flex;justify-content:space-between;align-items:center;">
          <span>Dongle ${g.index !== undefined ? g.index : ''} — ${g.host}${g.interface ? ' (' + g.interface + ')' : ''}</span>
          <button class="btn btn-primary" id="btn-rotate-${g.index}" style="padding:4px 10px;font-size:11px;"
                  onclick="rotateDongle(${g.index})">&#8635; Rotate</button>
        </div>
        <div class="dmeta" style="margin-top:4px;">
          <span class="badge ${connBadgeClass(g.status)}" style="font-size:11px;">${g.status}</span>
          &nbsp;IP: ${g.ip || '–'}
        </div>
      </div>
    `).join('');
    document.getElementById('operator-name').textContent = '';
  });
}

function loadLogs() {
  apiFetch('/api/logs').then(d => {
    const el = document.getElementById('logs');
    if (!d.logs || d.logs.length === 0) {
      el.innerHTML = '<span style="color:var(--muted);">No logs yet.</span>';
      return;
    }
    el.innerHTML = d.logs.slice().reverse().map(l =>
      `<div class="entry"><span class="ts">${l.ts}</span><span class="lvl-${l.level}">${l.level}</span><span>${escHtml(l.msg)}</span></div>`
    ).join('');
  });
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function rotateIp() {
  const btn = document.getElementById('btn-rotate');
  const sp  = document.getElementById('spinner');
  btn.disabled = true; sp.style.display = 'block';
  toast('Rotating IP – this may take up to 60s…');
  apiFetch('/api/rotate', { method: 'POST' }).then(d => {
    if (d.success) {
      toast(`New IP: ${d.new_ip} (was ${d.old_ip})`);
    } else {
      toast(`Rotation failed: ${d.error || 'unknown'}`, false);
    }
    loadStatus(); loadLogs();
  }).catch(() => toast('Rotate request failed', false))
    .finally(() => { btn.disabled = false; sp.style.display = 'none'; });
}

function proxyAction(action) {
  apiFetch('/api/proxy/' + action, { method: 'POST' }).then(d => {
    toast(d.message || (action === 'start' ? 'Proxy started' : 'Proxy stopped'), d.success !== false);
    loadStatus();
  }).catch(() => toast('Proxy action failed', false));
}

function refreshAll() {
  loadStatus();
  loadDongles();
  loadLogs();
}

function startCountdown() {
  clearInterval(refreshTimer);
  countdown = AUTO_REFRESH_S;
  refreshTimer = setInterval(() => {
    countdown--;
    document.getElementById('refresh-countdown').textContent = `Auto-refresh in ${countdown}s`;
    if (countdown <= 0) {
      refreshAll();
      countdown = AUTO_REFRESH_S;
    }
  }, 1000);
}

refreshAll();
startCountdown();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
# Routes – Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template_string(
        _DASHBOARD_HTML,
        proxy_port=config.PROXY_PORT,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes – REST API
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Return aggregated proxy + dongle status."""
    state = _get_state()
    uptime = time.time() - state.get("uptime_start", time.time())
    return jsonify(
        {
            "current_ip":           state.get("current_ip"),
            "connection_status":    state.get("connection_status", "unknown"),
            "network_type":         state.get("network_type", "unknown"),
            "signal_icon":          state.get("signal_icon", 0),
            "proxy_running":        state.get("proxy_running", False),
            "uptime_seconds":       round(uptime),
            "last_rotate":          state.get("last_rotate"),
            "last_rotate_result":   state.get("last_rotate_result"),
            "rotate_in_progress":   state.get("rotate_in_progress", False),
        }
    )


def _do_rotate(dongle_index: int = 0):
    """
    Shared rotation logic used by both /api/rotate and /api/rotate/<index>.
    Returns a (response_dict, http_status) tuple.
    """
    state = _get_state()
    if state.get("rotate_in_progress"):
        return {"success": False, "error": "Rotation already in progress"}, 409

    dongles = state.get("dongles", [])
    if dongle_index >= len(dongles):
        return {"success": False, "error": f"Dongle index {dongle_index} not found (have {len(dongles)})"}, 404

    panel_host = dongles[dongle_index]["host"]

    _update_state(rotate_in_progress=True)
    try:
        old_ip = dongles[dongle_index].get("ip")
        client = _get_dongle_client(panel_host)
        new_ip = client.rotate_ip()
        result = {"success": bool(new_ip), "old_ip": old_ip, "new_ip": new_ip, "dongle_index": dongle_index}
        _update_state(
            last_rotate        = time.time(),
            last_rotate_result = new_ip,
            rotate_in_progress = False,
            current_ip         = new_ip if dongle_index == 0 else state.get("current_ip"),
        )
        logger.info("IP rotation dongle %d (%s): %s → %s", dongle_index, panel_host, old_ip, new_ip)
        return result, 200
    except Exception as exc:  # pylint: disable=broad-except
        _update_state(rotate_in_progress=False)
        logger.error("rotate error (dongle %d): %s", dongle_index, exc)
        return {"success": False, "error": str(exc)}, 500


@app.route("/api/rotate", methods=["GET", "POST"])
def api_rotate():
    """Trigger an IP rotation cycle on the primary (index 0) dongle.
    Supports both GET (for browser/AdsPower) and POST (for API clients).
    GET /api/rotate          → rotate dongle 0
    GET /api/rotate?dongle=1 → rotate dongle 1
    """
    body = request.get_json(silent=True) or {}
    idx = int(request.args.get("dongle", body.get("dongle", 0)))
    result, status = _do_rotate(idx)
    return jsonify(result), status


@app.route("/api/rotate/<int:dongle_index>", methods=["GET", "POST"])
def api_rotate_by_index(dongle_index: int):
    """Trigger an IP rotation cycle on a specific dongle by index (0-based).
    Supports both GET (for browser/AdsPower) and POST (for API clients).
    GET /api/rotate/0  → rotate dongle 0
    GET /api/rotate/1  → rotate dongle 1
    """
    result, status = _do_rotate(dongle_index)
    return jsonify(result), status


@app.route("/api/dongles")
def api_dongles():
    """Return list of connected dongles and their status."""
    state = _get_state()
    return jsonify({"dongles": state.get("dongles", [])})


@app.route("/api/proxy/start", methods=["POST"])
def api_proxy_start():
    """Start the 3proxy SOCKS5 daemon."""
    body     = request.get_json(silent=True) or {}
    port     = int(body.get("port",     config.PROXY_PORT))
    username = body.get("username",     config.PROXY_USER)
    password = body.get("password",     config.PROXY_PASS)
    bind_ip  = body.get("bind_ip",      "0.0.0.0")

    ok = proxy_manager.start_proxy(
        port=port, bind_ip=bind_ip, username=username, password=password
    )
    _update_state(proxy_running=proxy_manager.is_running())
    if ok:
        logger.info("Proxy started via API (port=%d)", port)
        return jsonify({"success": True, "message": f"3proxy started on port {port}"})
    return jsonify({"success": False, "message": "Failed to start 3proxy – check logs"}), 500


@app.route("/api/proxy/stop", methods=["POST"])
def api_proxy_stop():
    """Stop the 3proxy SOCKS5 daemon."""
    ok = proxy_manager.stop_proxy()
    _update_state(proxy_running=proxy_manager.is_running())
    if ok:
        logger.info("Proxy stopped via API")
        return jsonify({"success": True, "message": "3proxy stopped"})
    return jsonify({"success": False, "message": "Failed to stop 3proxy – check logs"}), 500


@app.route("/api/logs")
def api_logs():
    """Return the last N log lines from the in-memory ring buffer."""
    limit = min(int(request.args.get("limit", 100)), config.LOG_BUFFER_SIZE)
    return jsonify({"logs": list(_log_buffer)[-limit:]})


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        "XProxy Manager starting – dashboard at http://%s:%d/",
        config.WEB_HOST if config.WEB_HOST != "0.0.0.0" else "0.0.0.0",
        config.WEB_PORT,
    )
    app.run(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=False,
        threaded=True,
    )
