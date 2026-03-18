"""
hilink.py - XH22 Dongle API Client
Implements Digest authentication (Mongoose 3.0 / Qualcomm-based firmware)
for XProxy XH22 dongle at 192.168.101.1

API: GET/POST /xml_action.cgi?method=get|set&module=duster&file=<name>
Auth: HTTP Digest with URI hardcoded to /cgi/xml_action.cgi
"""

import hashlib
import random
import time
import logging
import subprocess
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any

import requests
from requests.exceptions import RequestException

import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _xml_to_dict(xml_text: str) -> Dict[str, Any]:
    """Flatten XML into dict — handles nested <RGW><wan><field>val</field></wan></RGW>."""
    result: Dict[str, Any] = {}
    try:
        root = ET.fromstring(xml_text)
        # Walk all descendants, collect leaf text nodes
        for elem in root.iter():
            if elem.tag != root.tag and (elem.text and elem.text.strip()):
                result[elem.tag] = elem.text.strip()
    except ET.ParseError:
        pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# XH22 Client
# ─────────────────────────────────────────────────────────────────────────────

class XH22Client:
    """
    Client for the XProxy XH22 4G dongle.
    Uses Digest auth with Mongoose 3.0 quirks:
      - HA2 is always computed against '/cgi/xml_action.cgi' (hardcoded URI)
      - Login via GET /login.cgi with Authorization header (nc=00000001)
      - Subsequent API calls use the same nonce with increasing nc
    """

    DIGEST_URI = "/cgi/xml_action.cgi"

    def __init__(self, host: str = None, user: str = "admin", password: str = "admin"):
        self.host = host or config.DONGLE_HOST
        self.base = f"http://{self.host}"
        self.user = user
        self.password = password
        self._counter = 1
        self._realm = self._nonce = self._qop = self._ha1 = None
        self._session = requests.Session()
        self._login()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_challenge(self) -> bool:
        """Fetch a fresh Digest challenge from login.cgi."""
        try:
            r = self._session.get(f"{self.base}/login.cgi", timeout=5)
            h = r.headers.get("WWW-Authenticate", "")
            import re
            realm_m = re.search(r'realm="([^"]+)"', h)
            nonce_m = re.search(r'nonce="([^"]+)"', h)
            qop_m   = re.search(r'qop="([^"]+)"', h)
            if not (realm_m and nonce_m):
                return False
            self._realm = realm_m.group(1)
            self._nonce = nonce_m.group(1)
            self._qop   = qop_m.group(1) if qop_m else "auth"
            self._ha1   = _md5(f"{self.user}:{self._realm}:{self.password}")
            self._counter = 1
            return True
        except Exception as e:
            logger.error(f"Challenge failed: {e}")
            return False

    def _make_auth(self, method: str) -> str:
        """Build a Digest Authorization header for the next request."""
        ha2 = _md5(f"{method}:{self.DIGEST_URI}")
        salt = str(random.randint(0, 100000)) + str(int(time.time() * 1000))
        cnonce = _md5(salt)[:16]
        nc = format(self._counter, "08x")
        resp = _md5(f"{self._ha1}:{self._nonce}:{nc}:{cnonce}:{self._qop}:{ha2}")
        self._counter += 1
        return (
            f'Digest username="{self.user}", realm="{self._realm}", '
            f'nonce="{self._nonce}", uri="{self.DIGEST_URI}", '
            f'response="{resp}", qop={self._qop}, nc={nc}, cnonce="{cnonce}"'
        )

    def _login(self) -> bool:
        """Authenticate with the dongle."""
        if not self._get_challenge():
            return False
        auth = self._make_auth("GET")  # nc=1 consumed here
        try:
            self._session.get(
                f"{self.base}/login.cgi",
                headers={"Authorization": auth},
                timeout=5,
            )
            return True
        except Exception as e:
            logger.error(f"Login request failed: {e}")
            return False

    # ── Low-level API ─────────────────────────────────────────────────────────

    def _api_get(self, file_name: str) -> Optional[str]:
        try:
            auth = self._make_auth("GET")
            r = self._session.get(
                f"{self.base}/xml_action.cgi?method=get&module=duster&file={file_name}",
                headers={"Authorization": auth},
                timeout=10,
            )
            if "UNAUTHORIZED" in r.text:
                logger.warning("Session expired, re-logging in")
                self._login()
                return self._api_get(file_name)
            return r.text
        except Exception as e:
            logger.error(f"API GET {file_name}: {e}")
            return None

    def _api_post(self, file_name: str, xml_body: str) -> Optional[str]:
        try:
            auth = self._make_auth("POST")
            r = self._session.post(
                f"{self.base}/xml_action.cgi?method=set&module=duster&file={file_name}",
                headers={"Authorization": auth, "Content-Type": "application/xml"},
                data=xml_body,
                timeout=10,
            )
            if "UNAUTHORIZED" in r.text:
                logger.warning("Session expired, re-logging in")
                self._login()
                return self._api_post(file_name, xml_body)
            return r.text
        except Exception as e:
            logger.error(f"API POST {file_name}: {e}")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_connection_status(self) -> str:
        """Returns 'connected', 'disconnected', or 'unknown'."""
        xml = self._api_get("wan")
        if not xml:
            return "unknown"
        d = _xml_to_dict(xml)
        state = d.get("connect_disconnect", "unknown")
        if state == "cellular":
            return "connected"
        elif state == "disconnect":
            return "disconnected"
        return state

    def get_current_ip(self) -> Optional[str]:
        """Get current WAN (public) IP via external check using the dongle's subnet IP."""
        try:
            # Derive the local bind IP from host: 192.168.101.1 → 192.168.101.100
            parts = self.host.split(".")
            bind_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.100"
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", "8",
                 "--interface", bind_ip, "https://api.ipify.org"],
                capture_output=True, text=True, timeout=12
            )
            ip = result.stdout.strip()
            return ip if ip else None
        except Exception:
            return None

    def get_device_info(self) -> Dict[str, Any]:
        """Return device info (signal, network type, etc.)."""
        xml = self._api_get("wan")
        if not xml:
            return {}
        info = _xml_to_dict(xml)
        # Try to get signal info from a separate file
        sig_xml = self._api_get("signal")
        if sig_xml and "UNAUTHORIZED" not in sig_xml and "400" not in sig_xml:
            info.update(_xml_to_dict(sig_xml))
        return info

    def disconnect(self) -> bool:
        """Disconnect 4G."""
        logger.info("Disconnecting 4G...")
        xml = '<?xml version="1.0" encoding="US-ASCII"?><RGW><wan><connect_disconnect>disconnect</connect_disconnect></wan></RGW>'
        result = self._api_post("wan", xml)
        logger.info(f"Disconnect result: {result[:100] if result else 'None'}")
        return result is not None and "disconnect" in result

    def connect(self) -> bool:
        """Connect 4G (cellular mode)."""
        logger.info("Connecting 4G...")
        self._login()  # fresh session for reconnect
        xml = '<?xml version="1.0" encoding="US-ASCII"?><RGW><wan><connect_disconnect>cellular</connect_disconnect></wan></RGW>'
        result = self._api_post("wan", xml)
        logger.info(f"Connect result: {result[:100] if result else 'None'}")
        return result is not None and "cellular" in result

    def rotate_ip(self, timeout: int = None) -> Optional[str]:
        """
        IP rotation using the native XH22 'reset' endpoint.
        GET file=reset — dongle internally disconnects and reconnects (~5s).
        Returns new public IP on success, or None on failure.
        """
        timeout = timeout or config.ROTATE_WAIT_TIMEOUT
        old_ip = self.get_current_ip()
        logger.info(f"Rotating IP via native reset (current: {old_ip})")

        # Trigger network restart — dongle disconnects and reconnects with new IP
        # file=router → <RGW><nwrestart/> (network restart, cleanest method)
        # file=reset  → empty (full reset, fallback)
        try:
            self._api_get("router")
            logger.info("Network restart command sent (router), dongle reconnecting...")
        except Exception as e:
            logger.info(f"Router reset sent (expected disconnect): {e}")
            try:
                self._api_get("reset")
            except Exception:
                pass

        # Wait for dongle to come back and get a new IP
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            try:
                new_ip = self.get_current_ip()
                if new_ip and new_ip != old_ip:
                    logger.info(f"New IP: {new_ip}")
                    return new_ip
                logger.debug(f"Waiting for new IP... current={new_ip}")
            except Exception:
                logger.debug("Dongle still reconnecting...")

        new_ip = self.get_current_ip()
        logger.info(f"Rotation done, IP: {new_ip}")
        return new_ip

    def get_signal_info(self) -> Dict[str, Any]:
        """Try to get signal strength info."""
        for fname in ["signal", "lte_signal", "network_info", "cellular_info"]:
            xml = self._api_get(fname)
            if xml and "UNAUTHORIZED" not in xml and "Error 400" not in xml:
                return _xml_to_dict(xml)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_client: Optional[XH22Client] = None


def get_client() -> XH22Client:
    global _client
    if _client is None:
        _client = XH22Client()
    return _client


def reset_client():
    global _client
    _client = None
