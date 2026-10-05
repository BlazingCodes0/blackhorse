"""Egress watch: what is this machine CONNECTING OUT to?
Baselines first-seen remote endpoints; alerts on new IPs/ports and suspicious ones."""
import json, ipaddress
from pathlib import Path
from threat_log import log_incident

try:
    import psutil
except ImportError:
    psutil = None

BASELINE = Path.home() / "blackhorse" / "logs" / "egress_baseline.json"

SUSPICIOUS_OUTBOUND_PORTS = {4444, 5555, 6666, 1337, 31337, 23, 69}

def scan():
    if not psutil:
        return
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    seen = {}
    for c in psutil.net_connections(kind="tcp"):
        if c.status != "ESTABLISHED" or not c.raddr or not c.pid:
            continue
        ip, port = c.raddr.ip, c.raddr.port
        if ip.startswith(("127.", "::1")):
            continue  # localhost chatter is normal
        try:
            proc = psutil.Process(c.pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            proc = "unknown"
        key = f"{ip}:{port}"
        seen[key] = proc

    for key, proc in seen.items():
        ip, port = key.rsplit(":", 1)
        port = int(port)
        if key not in baseline:
            level = "ALERT" if port in SUSPICIOUS_OUTBOUND_PORTS else "WARN"
            log_incident(level, "egress", f"NEW outbound connection {key} by '{proc}'",
                         {"ip": ip, "port": port, "proc": proc})
            baseline[key] = proc
        elif baseline[key] != proc:
            log_incident("WARN", "egress", f"outbound {key} used by NEW process '{proc}' (was '{baseline[key]}')", {"ip": ip, "port": port, "proc": proc})  # process-flap is noise; new suspicious ports stay ALERT
            baseline[key] = proc

    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(baseline, indent=2))
    return seen

if __name__ == "__main__":
    scan()
