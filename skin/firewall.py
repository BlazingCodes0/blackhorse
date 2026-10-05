"""Response actions. Default everything is DRY-RUN: log what we WOULD do.
Set DRY_RUN=False only after you've verified the rules in logs."""
import subprocess, os
from threat_log import log_incident

DRY_RUN = os.environ.get("BLACKHORSE_DRY_RUN", "true").lower() != "false"

def alert(msg, source="firewall"):
    log_incident("ALERT", source, msg)

def kill_process(pid: int, reason: str):
    if DRY_RUN:
        log_incident("ALERT", "firewall", f"[dry-run] would kill pid {pid}: {reason}")
        return
    try:
        os.kill(pid, 15)
        log_incident("CRITICAL", "firewall", f"killed pid {pid}: {reason}")
    except (OSError, PermissionError) as e:
        try:
            subprocess.run(["sudo", "kill", str(pid)], check=True)
            log_incident("CRITICAL", "firewall", f"sudo-killed pid {pid}: {reason}")
        except Exception as e2:
            log_incident("WARN", "firewall", f"kill failed: {e} / {e2}")

BLOCKLIST_FILE = os.path.expanduser("~/blackhorse/config/blocked_ips.txt")

def block_ip(ip: str, reason: str):
    cmd = ["sudo", "iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"]
    # persist the intent so the block survives reboot
    existing = set()
    if os.path.exists(BLOCKLIST_FILE):
        existing = set(l.strip() for l in open(BLOCKLIST_FILE) if l.strip())
    if ip not in existing:
        try:
            with open(BLOCKLIST_FILE, "a") as f:
                f.write(ip + "\n")
        except OSError as e:
            log_incident("WARN", "firewall", f"blocklist write failed: {e}")
    if DRY_RUN:
        log_incident("ALERT", "firewall", f"[dry-run] would run: {' '.join(cmd)} ({reason})")
        return
    subprocess.run(cmd, check=False)
    log_incident("CRITICAL", "firewall", f"blocked {ip}: {reason}")

def reapply_blocks():
    """Called on startup: re-apply every persisted IP block. Survives reboot."""
    if not os.path.exists(BLOCKLIST_FILE):
        return
    for line in open(BLOCKLIST_FILE):
        ip = line.strip()
        if ip and not DRY_RUN:
            chk = subprocess.run(["sudo", "iptables", "-C", "INPUT", "-s", ip, "-j", "DROP"],
                                 capture_output=True)
            if chk.returncode != 0:  # rule missing -> add it
                subprocess.run(["sudo", "iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"], check=False)
            log_incident("INFO", "firewall", f"re-applied block on {ip}")

def lockdown(reason: str):
    """Big hammer: pause all motar/trading loops. Stops qubitFlow from trading."""
    log_incident("CRITICAL", "firewall", f"LOCKDOWN: {reason}")
    flag = os.path.expanduser("~/blackhorse/config/LOCKDOWN")
    with open(flag, "w") as f:
        f.write(reason + "\n")

def patrol():
    """Read threats.jsonl; act on new ALERT/CRITICAL incidents per policy.
    Tracks handled entry-hashes in handled.json so each fires once."""
    import json
    handled_file = os.path.expanduser("~/blackhorse/logs/handled.json")
    try:
        handled = set(json.load(open(handled_file))) if os.path.exists(handled_file) else set()
    except (json.JSONDecodeError, OSError):
        handled = set()
    logf = os.path.expanduser("~/blackhorse/logs/threats.jsonl")
    if not os.path.exists(logf):
        return
    for line in open(logf, errors="ignore"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("hash") in handled or e.get("level") not in ("ALERT", "CRITICAL"):
            continue
        if e.get("hash"):
            handled.add(e["hash"])
        msg, src, det = e.get("message", ""), e.get("source", ""), e.get("details", {})
        # policy: suspicious process -> kill it (skip stale/dead pids)
        import psutil
        if src == "watchdog" and "suspicious process" in msg and det.get("pid"):
            if psutil.pid_exists(det["pid"]):
                kill_process(det["pid"], msg)
        # policy: a watched integrity file got DELETED -> lockdown trading
        elif src == "integrity" and "DELETED" in msg:
            lockdown(msg)
        # policy: egress to a classic malware port -> block remote IP
        elif src == "egress" and det.get("port") in (4444, 1337, 31337, 5555, 6666):
            block_ip(det.get("ip", "0.0.0.0"), msg)
    with open(handled_file, "w") as f:
        json.dump(sorted(handled), f)
