"""Watchdog: scans system state for suspicious activity. Logs alerts. Never changes state yet."""
import time, re
from pathlib import Path
from threat_log import log_incident

try:
    import psutil
except ImportError:
    psutil = None

WATCH_DIRS = [Path.home() / "blackhorse" / "config", Path.home() / "blackhorse" / "heart" / "policies"]

# patterns that, if seen in a process cmdline, deserve a look
SUSPICIOUS_CMD = re.compile(r"(ncat|netcat|nc -e|bash -i|/dev/tcp|curl .*\| *(ba)?sh|wget .*\| *(ba)?sh)", re.I)

def scan_processes():
    if not psutil:
        return
    for p in psutil.process_iter(["pid", "name", "cmdline", "username"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if SUSPICIOUS_CMD.search(cmd):
                log_incident("ALERT", "watchdog", f"suspicious process: {p.info['name']}",
                             {"pid": p.info["pid"], "cmd": cmd[:300], "user": p.info["username"]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def scan_resources():
    if not psutil:
        return
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    if cpu > 90:
        log_incident("WARN", "watchdog", f"high CPU {cpu}%")
    if mem > 90:
        log_incident("WARN", "watchdog", f"high memory {mem}%")

def scan_policy_files():
    baseline = Path.home() / "blackhorse" / "logs" / "policy_baseline.json"
    import json
    try:
        old = json.loads(baseline.read_text()) if baseline.exists() else {}
    except (OSError, ValueError):
        old = {}  # empty/corrupt baseline — re-baseline, never crash the sweep
    if not isinstance(old, dict):
        old = {}
    new = {}
    for d in WATCH_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            try:
                m = f.stat().st_mtime
                new[str(f)] = m
                if str(f) in old and old[str(f)] != m:
                    log_incident("ALERT", "watchdog", f"policy file modified: {f}")
            except OSError:
                pass
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(json.dumps(new))

def scan_recent_auth_failures():
    auth = Path("/var/log/auth.log")
    if not auth.exists():
        return
    try:
        fails = [l for l in auth.read_text(errors="ignore").splitlines()[-200:] if "Failed password" in l]
        if len(fails) > 5:
            log_incident("ALERT", "watchdog", f"{len(fails)} recent failed SSH logins")
    except PermissionError:
        pass

def sweep():
    scan_processes()
    scan_resources()
    scan_policy_files()
    scan_recent_auth_failures()
    log_incident("INFO", "watchdog", "sweep complete")

if __name__ == "__main__":
    sweep()
