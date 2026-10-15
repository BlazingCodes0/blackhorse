"""Connection health checks: internet, DNS, local AI infra, unexpected outbound conns."""
import socket, time, subprocess
from pathlib import Path
from threat_log import log_incident

TARGETS = [
    ("internet", "1.1.1.1", 53, 3),          # Cloudflare DNS
    ("dns",    "8.8.8.8", 53, 3),
    ("ollama", "127.0.0.1", 11434, 1),        # local LLM
    ("phoenix","127.0.0.1", 6006, 1),         # Arize Phoenix UI
]

# outbound ports we do NOT expect to be open listeners on this machine
EXPECTED_LISTEN_PORTS = {11434, 6006, 8000, 5900}

# programs we recognize as "probably fine" — everything else gets a deep look
KNOWN_GOOD = ("code", "chrome", "firefox", "node", "python", "ssh", "sshd", "systemd", "docker", "ollama", "code-server")

# ports that are suspicious when exposed on all interfaces, not just localhost
SUSPICIOUS_BINDINGS = {4444, 5555, 6666, 1337, 31337, 9001, 9999}

def investigate_port(port: int, addr: str = "") -> dict:
    """Find out WHAT owns a port:pid -> process name, exe, user, cmdline."""
    info = {"port": port, "process": "unknown", "exe": "?", "user": "?", "cmd": "?", "verdict": "unknown"}
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.status == "LISTEN" and c.laddr.port == port and c.pid:
                try:
                    p = psutil.Process(c.pid)
                    info.update(process=p.name(), exe=p.exe(), user=p.username(),
                                cmd=" ".join(p.cmdline())[:200])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    info["process"] = "(access denied)"
                break
    except Exception as e:
        info["error"] = str(e)

    name = info["process"].lower()
    if name == "unknown":
        info["verdict"] = "SUSPICIOUS — no owner found, could be hidden/rootkit or race"
    elif any(k in name for k in KNOWN_GOOD):
        info["verdict"] = "likely-normal (recognized program)"
    else:
        info["verdict"] = "INVESTIGATE — unrecognized process listening"
    if port in SUSPICIOUS_BINDINGS:
        info["verdict"] = "SUSPICIOUS — classic backdoor/handler port"
    if addr and not addr.startswith(("127.", "::1", "[::1]")) and name != "unknown":
        info["verdict"] += " + exposed on network interface (not just localhost)"
    return info

FAIL_STATE = Path.home() / "blackhorse" / "logs" / "net_fail_state.json"

def check_port(name, host, port, timeout):
    t0 = time.time()
    import json as _json
    # tolerate a missing, empty, or corrupt state file — a bad read must never
    # crash the defense loop (that just makes systemd restart it forever).
    try:
        state = _json.loads(FAIL_STATE.read_text()) if FAIL_STATE.exists() else {}
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = int((time.time() - t0) * 1000)
            if state.get(name, 0) >= 3:
                log_incident("INFO", "net_check", f"{name} RECOVERED after {state[name]} failures")
            elif state.get(name, 0) > 0:
                log_incident("INFO", "net_check", f"{name} back up ({ms}ms)")
            else:
                log_incident("INFO", "net_check", f"{name} OK ({host}:{port}, {ms}ms)")
            state[name] = 0
            state.pop(name + "__alerted", None)
            FAIL_STATE.write_text(_json.dumps(state))
            return True
    except OSError as e:
        state[name] = state.get(name, 0) + 1
        already = state.get(name + "__alerted", False)
        if state[name] >= 3 and not already:
            log_incident("ALERT", "net_check", f"{name} FAIL x{state[name]} ({host}:{port}): {e}")
            state[name + "__alerted"] = True
        elif state[name] < 3:
            log_incident("WARN", "net_check", f"{name} FAIL x{state[name]} ({host}:{port}): {e}")
        FAIL_STATE.parent.mkdir(parents=True, exist_ok=True)
        FAIL_STATE.write_text(_json.dumps(state))
        return False

def check_listeners():
    """List listening TCP ports; flag anything not in the expected set."""
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5).stdout
    except Exception as e:
        log_incident("WARN", "net_check", f"ss failed: {e}")
        return
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            try:
                port = int(addr.rsplit(":", 1)[1])
            except ValueError:
                continue
            if port not in EXPECTED_LISTEN_PORTS:
                host = addr.rsplit(":", 1)[0]
                info = investigate_port(port, host)
                # baseline: only alert when a NEW listener appears or a port's OWNER changes
                bl_file = Path.home() / "blackhorse" / "logs" / "port_baseline.json"
                import json as _json
                try:
                    baseline = _json.loads(bl_file.read_text()) if bl_file.exists() else {}
                except (_json.JSONDecodeError, OSError):
                    baseline = {}  # torn mid-write; treat as fresh, never crash the sweep
                key = str(port)
                prev_owner = baseline.get(key)
                if prev_owner is None:
                    log_incident("WARN", "net_check",
                                 f"NEW listener: port {port} owned by '{info['process']}' ({info['user']})",
                                 {"exe": info["exe"], "cmd": info["cmd"], "verdict": info["verdict"]})
                elif prev_owner != info["exe"]:
                    log_incident("ALERT", "net_check",
                                 f"port {port} owner CHANGED: '{prev_owner}' -> '{info['exe']}'",
                                 {"cmd": info["cmd"], "verdict": info["verdict"]})
                baseline[key] = info["exe"]
                bl_file.parent.mkdir(parents=True, exist_ok=True)
                bl_file.write_text(_json.dumps(baseline, indent=2))

def run_all():
    up = sum(check_port(*t) for t in TARGETS)
    check_listeners()
    log_incident("INFO", "net_check", f"connection sweep done: {up}/{len(TARGETS)} targets up")
    return up

if __name__ == "__main__":
    run_all()
