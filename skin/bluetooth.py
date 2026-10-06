"""Bluetooth guard: keep the adapter dark and un-discoverable.

Checks adapter state each sweep; if it is powered on, discoverable, or
pairable, it hardens it (pairable off, discoverable off, powered down)
and logs an ALERT. Any unknown connected device triggers a CRITICAL.
"""
import subprocess
from pathlib import Path
from threat_log import log_incident

TRUSTED = Path.home() / "blackhorse" / "config" / "trusted_bt.txt"


def _btctl(*args) -> str:
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True,
                           text=True, timeout=10)
        return r.stdout + r.stderr
    except Exception as e:
        return f"ERROR: {e}"


def _state() -> dict:
    out = _btctl("show")
    if out.startswith("ERROR"):
        return None
    return {
        "powered": "Powered: yes" in out,
        "discoverable": "Discoverable: yes" in out,
        "pairable": "Pairable: yes" in out,
    }


def scan():
    st = _state()
    if st is None:
        return
    if st["discoverable"] or st["pairable"]:

        log_incident("ALERT", "bluetooth",
                     f"adapter exposed (discoverable={st['discoverable']}, pairable={st['pairable']}) — hardening")
        _btctl("discoverable", "off")
        _btctl("pairable", "off")
    if st["powered"]:
        # stay powered down unless the user explicitly brings it up
        _btctl("discoverable", "off")
        _btctl("pairable", "off")
        _btctl("power", "off")
        log_incident("WARN", "bluetooth", "adapter powered on — powered down")
    # unknown connected devices?
    conns = _btctl("devices", "Connected")
    for line in conns.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "device":
            mac = parts[1]
            trusted = []
            if TRUSTED.exists():
                trusted = [l.strip() for l in TRUSTED.read_text().splitlines()]
            if mac not in trusted:
                log_incident("CRITICAL", "bluetooth",
                             f"UNKNOWN CONNECTED DEVICE {mac} ({' '.join(parts[2:])})")
                _btctl("disconnect", mac)


if __name__ == "__main__":
    scan()
