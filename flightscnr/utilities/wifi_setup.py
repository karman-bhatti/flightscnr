"""First-boot Wi-Fi setup via NetworkManager soft-AP + captive portal.

When the Pi has no ethernet carrier and no saved client Wi-Fi profiles,
we bring up a temporary hotspot so a phone can join (QR on the round
display) and submit home Wi-Fi credentials through the web portal.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("flightscnr.wifi_setup")

DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
AP_STATE_PATH = os.path.join(DATA_DIR, "setup_ap.json")
# Cross-process signal: Flask writes this when join succeeds; display polls it.
CONNECTED_FLAG_PATH = os.path.join(DATA_DIR, "wifi_setup_connected")
AP_CONNECTION_NAME = "flightscnr-setup-ap"
AP_SSID_PREFIX = "FlightScnr-Setup"
WLAN_IFACE = os.environ.get("FLIGHTSCNR_WLAN", "wlan0")
DNSMASQ_SHARED_DIR = "/etc/NetworkManager/dnsmasq-shared.d"
DNSMASQ_CAPTIVE_CONF = os.path.join(DNSMASQ_SHARED_DIR, "flightscnr-captive.conf")

_lock = threading.RLock()
_ap_active = False
_status_message = ""
_last_error = ""


@dataclass(frozen=True)
class ApCredentials:
    ssid: str
    password: str
    gateway: str = "10.42.0.1"

    @property
    def wifi_qr_payload(self) -> str:
        # WIFI QR (WPA-PSK). Escape special chars per WIFI:QR convention.
        def esc(value: str) -> str:
            return (
                value.replace("\\", "\\\\")
                .replace(";", "\\;")
                .replace(",", "\\,")
                .replace(":", "\\:")
                .replace('"', '\\"')
            )

        return f"WIFI:T:WPA;S:{esc(self.ssid)};P:{esc(self.password)};;"

    @property
    def portal_url(self) -> str:
        return f"http://{self.gateway}/wifi"


def _run(cmd: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _nmcli(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return _run(["nmcli", *args], timeout=timeout)


def skip_requested() -> bool:
    return os.environ.get("FLIGHTSCNR_SKIP_WIFI_SETUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def force_requested() -> bool:
    """Force setup hotspot/QR even when Wi-Fi profiles already exist (testing)."""
    return os.environ.get("FLIGHTSCNR_FORCE_WIFI_SETUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def ethernet_up() -> bool:
    """True when a wired interface has carrier (setup not needed)."""
    try:
        for name in os.listdir("/sys/class/net"):
            if name == "lo" or name.startswith(("wlan", "p2p-", "docker", "veth", "br")):
                continue
            carrier = f"/sys/class/net/{name}/carrier"
            oper = f"/sys/class/net/{name}/operstate"
            try:
                with open(carrier, encoding="utf-8") as fh:
                    if fh.read().strip() == "1":
                        return True
            except OSError:
                pass
            try:
                with open(oper, encoding="utf-8") as fh:
                    if fh.read().strip() == "up":
                        # Some NICs lack carrier sysfs; treat operstate up as wired OK.
                        if not name.startswith("wlan"):
                            return True
            except OSError:
                pass
    except OSError:
        pass
    return False


def _connection_lines() -> list[str]:
    proc = _nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show")
    if proc.returncode != 0:
        return []
    return [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]


def saved_client_wifi_names() -> list[str]:
    """Saved Wi-Fi profiles that are not our setup hotspot."""
    names: list[str] = []
    for line in _connection_lines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name, ctype = parts[0], parts[1]
        if ctype != "802-11-wireless" and ctype != "wifi":
            continue
        if name == AP_CONNECTION_NAME:
            continue
        # Confirm mode is infrastructure (client), not AP.
        mode = _nmcli("-g", "802-11-wireless.mode", "connection", "show", name)
        mode_val = (mode.stdout or "").strip().lower()
        if mode_val in ("ap", "adhoc"):
            continue
        names.append(name)
    return names


def active_client_wifi() -> bool:
    """True when wlan0 (or FLIGHTSCNR_WLAN) is up as a client with an IPv4 address."""
    proc = _nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device", "status")
    if proc.returncode != 0:
        return False
    for line in (proc.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        device, dtype, state = parts[0], parts[1], parts[2]
        if device != WLAN_IFACE or dtype != "wifi":
            continue
        if not state.startswith("connected"):
            return False
        # Exclude AP mode: check connection mode of the active profile.
        mode = _nmcli("-g", "GENERAL.CONNECTION", "device", "show", device)
        con = (mode.stdout or "").strip()
        if not con or con == AP_CONNECTION_NAME:
            return False
        mode2 = _nmcli("-g", "802-11-wireless.mode", "connection", "show", con)
        if (mode2.stdout or "").strip().lower() == "ap":
            return False
        return True
    return False


def needs_wifi_setup() -> bool:
    """Fast check: should the captive portal / QR path be considered active?

    Does not block. For boot entry (including “saved SSID missing”), use
    ``should_enter_setup_at_boot`` which applies a short autoconnect grace.
    """
    if skip_requested():
        return False
    if force_requested():
        return True
    if ethernet_up():
        return False
    if active_client_wifi():
        return False
    if not saved_client_wifi_names():
        return True
    return False


_OFFLINE_GRACE_S = float(os.environ.get("FLIGHTSCNR_WIFI_OFFLINE_GRACE_S", "25") or 25)


def _wait_for_client_wifi(timeout_s: float) -> bool:
    """Return True if client Wi-Fi or ethernet comes up within timeout_s."""
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        if active_client_wifi() or ethernet_up():
            return True
        time.sleep(1.0)
    return active_client_wifi() or ethernet_up()


def should_enter_setup_at_boot() -> bool:
    """Boot-time decision, including fallback when a saved SSID is not found.

    If client Wi-Fi profiles exist but never associate (wrong place, AP off,
    bad PSK), wait briefly for NetworkManager autoconnect, then enter setup.
    """
    if skip_requested():
        return False
    if force_requested():
        return True
    if ethernet_up() or active_client_wifi():
        return False
    if not saved_client_wifi_names():
        return True
    logger.info(
        "Saved Wi-Fi present but not connected — waiting %.0fs before setup hotspot",
        _OFFLINE_GRACE_S,
    )
    if _wait_for_client_wifi(_OFFLINE_GRACE_S):
        return False
    logger.info("Still offline after grace — entering Wi-Fi setup hotspot")
    return True


def mark_wifi_connected(ssid: str = "") -> None:
    """Signal the display process that portal join succeeded (cross-process)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONNECTED_FLAG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ssid": ssid, "at": time.time()}, fh)
        os.replace(tmp, CONNECTED_FLAG_PATH)
    except OSError as exc:
        logger.warning("Could not write Wi-Fi connected flag: %s", exc)


def clear_wifi_connected_flag() -> None:
    try:
        if os.path.isfile(CONNECTED_FLAG_PATH):
            os.unlink(CONNECTED_FLAG_PATH)
    except OSError:
        pass


def wifi_connect_signaled() -> bool:
    return os.path.isfile(CONNECTED_FLAG_PATH)


def setup_mode_active() -> bool:
    with _lock:
        return _ap_active


def status_message() -> str:
    with _lock:
        return _status_message


def last_error() -> str:
    with _lock:
        return _last_error


def _set_status(msg: str) -> None:
    global _status_message
    with _lock:
        _status_message = msg
        logger.info("%s", msg)


def _set_error(msg: str) -> None:
    global _last_error
    with _lock:
        _last_error = msg
        logger.warning("%s", msg)


def _device_suffix() -> str:
    path = f"/sys/class/net/{WLAN_IFACE}/address"
    try:
        with open(path, encoding="utf-8") as fh:
            mac = fh.read().strip().replace(":", "")
        if len(mac) >= 4:
            return mac[-4:].upper()
    except OSError:
        pass
    return secrets.token_hex(2).upper()


def _load_or_create_ap_creds() -> ApCredentials:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.isfile(AP_STATE_PATH):
        try:
            with open(AP_STATE_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            ssid = str(data.get("ssid") or "").strip()
            password = str(data.get("password") or "").strip()
            gateway = str(data.get("gateway") or "10.42.0.1").strip()
            if ssid and len(password) >= 8:
                return ApCredentials(ssid=ssid, password=password, gateway=gateway)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    ssid = f"{AP_SSID_PREFIX}-{_device_suffix()}"
    # Easy to type if QR fails; still WPA2-length.
    password = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")[:10]
    creds = ApCredentials(ssid=ssid, password=password, gateway="10.42.0.1")
    _save_ap_creds(creds)
    return creds


def _save_ap_creds(creds: ApCredentials) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = AP_STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(
            {"ssid": creds.ssid, "password": creds.password, "gateway": creds.gateway},
            fh,
            indent=2,
        )
    os.replace(tmp, AP_STATE_PATH)


def get_ap_credentials() -> ApCredentials:
    return _load_or_create_ap_creds()


def _write_captive_dns(gateway: str) -> None:
    """Point all DNS answers at the Pi so phones open the captive portal."""
    try:
        os.makedirs(DNSMASQ_SHARED_DIR, exist_ok=True)
        conf = (
            "# Managed by FlightScnr — captive portal during Wi-Fi setup\n"
            f"address=/#/{gateway}\n"
        )
        tmp = DNSMASQ_CAPTIVE_CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(conf)
        os.replace(tmp, DNSMASQ_CAPTIVE_CONF)
    except OSError as exc:
        logger.warning("Could not write captive DNS config: %s", exc)


def _remove_captive_dns() -> None:
    try:
        if os.path.isfile(DNSMASQ_CAPTIVE_CONF):
            os.unlink(DNSMASQ_CAPTIVE_CONF)
    except OSError:
        pass


def _detect_gateway() -> str:
    proc = _nmcli("-g", "IP4.ADDRESS", "device", "show", WLAN_IFACE)
    text = (proc.stdout or "").strip()
    # e.g. 10.42.0.1/24
    m = re.match(r"(\d+\.\d+\.\d+\.\d+)", text.splitlines()[0] if text else "")
    if m:
        return m.group(1)
    return "10.42.0.1"


def ensure_setup_ap() -> ApCredentials:
    """Bring up the setup hotspot; idempotent."""
    global _ap_active
    creds = _load_or_create_ap_creds()
    with _lock:
        if _ap_active:
            try:
                gw = _detect_gateway()
                if gw != creds.gateway:
                    creds = ApCredentials(creds.ssid, creds.password, gw)
                    _save_ap_creds(creds)
            except Exception:
                pass
            return creds

    _set_status("Starting setup Wi-Fi hotspot…")
    _write_captive_dns(creds.gateway)

    # Remove stale AP profile so we recreate cleanly.
    _nmcli("connection", "delete", AP_CONNECTION_NAME)

    proc = _nmcli(
        "device",
        "wifi",
        "hotspot",
        "ifname",
        WLAN_IFACE,
        "con-name",
        AP_CONNECTION_NAME,
        "ssid",
        creds.ssid,
        "password",
        creds.password,
        timeout=60.0,
    )
    if proc.returncode != 0:
        # Fallback: explicit AP connection with shared IPv4.
        _nmcli(
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            WLAN_IFACE,
            "con-name",
            AP_CONNECTION_NAME,
            "ssid",
            creds.ssid,
            "wifi.mode",
            "ap",
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.psk",
            creds.password,
            "ipv4.method",
            "shared",
            "ipv6.method",
            "ignore",
        )
        proc = _nmcli("connection", "up", AP_CONNECTION_NAME, timeout=60.0)
        if proc.returncode != 0:
            _set_error(
                (proc.stderr or proc.stdout or "Failed to start setup hotspot").strip()
            )
            raise RuntimeError(_last_error)

    # Harden a bit when supported.
    _nmcli(
        "connection",
        "modify",
        AP_CONNECTION_NAME,
        "connection.autoconnect",
        "no",
        "wifi-sec.proto",
        "rsn",
        "wifi-sec.pairwise",
        "ccmp",
    )

    time.sleep(1.0)
    gateway = _detect_gateway()
    creds = ApCredentials(creds.ssid, creds.password, gateway)
    _save_ap_creds(creds)
    _write_captive_dns(gateway)
    # Reload shared dnsmasq by bouncing the AP connection once DNS file exists.
    _nmcli("connection", "up", AP_CONNECTION_NAME, timeout=60.0)

    with _lock:
        _ap_active = True
    _set_status(f"Setup hotspot ready: {creds.ssid}")
    return creds


def stop_setup_ap() -> None:
    """Tear down the setup hotspot and captive DNS."""
    global _ap_active
    _remove_captive_dns()
    _nmcli("connection", "down", AP_CONNECTION_NAME)
    _nmcli("connection", "delete", AP_CONNECTION_NAME)
    with _lock:
        _ap_active = False
    _set_status("Setup hotspot stopped")


def list_wifi_networks(*, rescan: bool = True) -> list[dict]:
    """Scan nearby SSIDs for the captive portal picker."""
    args = ["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    if rescan:
        args.extend(["--rescan", "yes"])
    proc = _nmcli(*args, timeout=45.0)
    if proc.returncode != 0:
        _set_error((proc.stderr or "Wi-Fi scan failed").strip())
        return []
    seen: set[str] = set()
    rows: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen:
            continue
        # Skip our own setup SSID.
        if ssid.startswith(AP_SSID_PREFIX):
            continue
        seen.add(ssid)
        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0
        security = parts[2] if len(parts) > 2 else ""
        rows.append({"ssid": ssid, "signal": signal, "security": security})
    rows.sort(key=lambda r: (-r["signal"], r["ssid"].lower()))
    return rows


def connect_to_wifi(ssid: str, password: str = "") -> tuple[bool, str]:
    """Leave setup AP and join the user's network. Returns (ok, message)."""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "SSID is required"
    password = password or ""

    _set_status(f"Connecting to “{ssid}”…")
    # Bring down the AP so the radio can associate as a client.
    stop_setup_ap()
    time.sleep(1.0)

    # Delete any prior profile with the same name to avoid stale PSKs.
    safe_name = re.sub(r"[^\w.\-]+", "_", ssid)[:48] or "wifi"
    con_name = f"flightscnr-{safe_name}"
    _nmcli("connection", "delete", con_name)

    add_cmd = [
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        WLAN_IFACE,
        "con-name",
        con_name,
        "ssid",
        ssid,
        "ipv4.method",
        "auto",
        "ipv6.method",
        "auto",
        "connection.autoconnect",
        "yes",
    ]
    if password:
        add_cmd.extend(
            [
                "wifi-sec.key-mgmt",
                "wpa-psk",
                "wifi-sec.psk",
                password,
            ]
        )
    else:
        add_cmd.extend(["wifi-sec.key-mgmt", "none"])

    proc = _nmcli(*add_cmd, timeout=30.0)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "Could not create Wi-Fi profile").strip()
        _set_error(msg)
        # Try to restore setup AP so the user can retry.
        try:
            ensure_setup_ap()
        except Exception:
            logger.exception("Failed to restore setup AP after connect error")
        return False, msg

    proc = _nmcli("connection", "up", con_name, timeout=60.0)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or f"Could not join “{ssid}”").strip()
        _set_error(msg)
        _nmcli("connection", "delete", con_name)
        try:
            ensure_setup_ap()
        except Exception:
            logger.exception("Failed to restore setup AP after join failure")
        return False, msg

    # Wait briefly for an address.
    for _ in range(20):
        if active_client_wifi():
            _set_status(f"Connected to “{ssid}”")
            mark_wifi_connected(ssid)
            return True, f"Connected to “{ssid}”"
        time.sleep(0.5)

    if active_client_wifi():
        mark_wifi_connected(ssid)
        return True, f"Connected to “{ssid}”"

    msg = f"Joined “{ssid}” but no IP yet — check the password and try again"
    _set_error(msg)
    try:
        ensure_setup_ap()
    except Exception:
        logger.exception("Failed to restore setup AP after no-IP")
    return False, msg


def portal_ready() -> bool:
    """True when the captive portal should intercept / serve Wi-Fi UI."""
    return setup_mode_active() or needs_wifi_setup()
