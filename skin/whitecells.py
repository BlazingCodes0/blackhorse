"""WHITECELLS — the skin's immune-system bots.
When a threat is flagged, a whitecell traces it to its source
(process tree -> exe -> network connections) and responds.
If Ollama is available it asks a local model for a second opinion;
otherwise it falls back to hard rules. It never blindly trusts the LLM."""
import json, os, subprocess, hashlib
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from threat_log import log_incident
import firewall

ANALYZED = Path.home() / "blackhorse" / "logs" / "whitecell_analyzed.json"
THREATS = Path.home() / "blackhorse" / "logs" / "threats.jsonl"
LLM_CALLS = Path.home() / "blackhorse" / "logs" / "llm_call_times.json"
BRAIN_INBOX = Path.home() / "blackhorse" / "logs" / "brain_inbox.jsonl"

ACTIONS = ("KILL", "BLOCK", "LOCKDOWN", "MONITOR", "IGNORE")

# Events the skin generates about itself (its own verdicts, the LOCKDOWN flag it
# writes, and the settings file it edits) must never be re-classified as threats —
# doing so creates a feedback loop that floods the brain. Matched case-insensitively.
SELF_NOISE = (
    "lockdown",
    "whitecell verdict",
    "/config/lockdown",
    "config/lockdown",
    "skin_settings.json",
    "brain_min_seconds",
    "brain_max_calls_per_hour",
)


def _is_self_noise(low: str) -> bool:
    return any(s in low for s in SELF_NOISE)

def trace(details: dict) -> dict:
    """Follow the threat back to its source."""
    info = {"pid": details.get("pid"), "exe": details.get("exe"), "cmd": details.get("cmd"),
            "parent": None, "connections": []}
    if psutil and info["pid"] and psutil.pid_exists(info["pid"]):
        try:
            p = psutil.Process(info["pid"])
            info["exe"] = p.exe()
            info["cmd"] = " ".join(p.cmdline())[:300]
            if p.parent():
                info["parent"] = f"{p.parent().name()} (pid {p.parent().pid})"
            for c in p.connections(kind="inet"):
                if c.raddr:
                    info["connections"].append(f"{c.raddr.ip}:{c.raddr.port}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return info

import settings

_LLM_CALLS = []

def _master_on() -> bool:
    try:
        import settings as _s
        return bool(_s.load().get("master_enabled", True))
    except Exception:
        return True


def _llm_may_run() -> bool:
    global _LLM_CALLS
    import time, json
    if not _master_on():
        return False
    cfg = settings.load()
    if not cfg["llm_enabled"]:
        return False
    cap = int(cfg.get("max_llm_calls_per_min", 0))
    if cap <= 0:
        return False  # 0 disables the local tier outright (no more "unlimited")
    now = time.time()
    _LLM_CALLS = [t for t in _LLM_CALLS if now - t < 60]
    if len(_LLM_CALLS) >= cap:
        return False
    _LLM_CALLS.append(now)
    return True

def ollama_verdict(trace_info: dict, threat_msg: str) -> dict | None:
    """Ask local Ollama for a classification. Returns None if unavailable/budgeted."""
    import urllib.request
    if not _llm_may_run():
        return None
    prompt = (
        "You are a security triage bot. Given this threat and its trace, reply ONLY with JSON: "
        '{"action": one of KILL/BLOCK/LOCKDOWN/MONITOR/IGNORE, "confidence": 0-1, "reason": str}'
        f"\nThreat: {threat_msg}\nTrace: {json.dumps(trace_info)[:800]}"
    )
    try:
        import urllib.request
        # use whatever model is configured — on high-end PCs set qwen2.5:7b/llama3.2:3b etc.
        for model in (settings.load()["llm_model"], "qwen2.5:0.5b"):
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                                 "keep_alive": settings.load()["llm_keep_alive_seconds"]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                out = json.load(urllib.request.urlopen(req, timeout=settings.load()["llm_timeout_seconds"]))["response"]
                if out:
                    break
            except Exception:
                continue
        else:
            return None
        # extract JSON from whatever the model said
        start, end = out.find("{"), out.rfind("}") + 1
        verdict = json.loads(out[start:end])
        if verdict.get("action") in ACTIONS:
            return verdict
    except Exception:
        return None
    return None

# --- Rule engine: compact but broad, no LLM needed ---------------------------
KILL = {
    "reverse shell": ("nc -e", "nc -l", "/dev/tcp", "bash -i", "sh -i >", "mkfifo", "python -c 'import socket", "socat", "telnet 0 2>/dev/null"),
    "dropper":      ("curl | bash", "curl | sh", "wget | bash", "wget | sh", "wget -qO-", "|bash", "| bash", "|sh", "| sh"),
    "fork bomb":    (":(){:|:&};:", "fork bomb"),
    "cryptominer":  ("xmrig", "minerd", "cpuminer", "cryptonight", "stratum+tcp", "kdevtmpfsi", "kinsing"),
    # webshells & PHP backdoors (expert-added)
    "webshell":     ("c99.php", "r57.php", "shell_exec", "base64_decode(", "eval($_", "b374k", "weevely", "p0wny"),
    # persistence mechanisms (expert-added)
    "persistence":  ("@reboot", "rc.local", "systemctl enable", "/etc/cron", "crontab -e", ".bashrc", "authorized_keys", "chmod +s"),
    # privilege escalation attempts (expert-added)
    "privesc":      ("sudo -i", "sudo su", "nopasswd:", "/etc/sudoers", "pkexec", "/tmp/.suid", "getcap"),
    # data exfiltration (expert-added)
    "exfiltration": ("tar czf /tmp", "tar -czf /tmp", "scp -r", "scp -i", "rsync .* ssh", "nc -w", "rclone copy", "wget --post-file"),
    "backdoor port": ("4444", "5555", "6666", "1337", "31337", "9999", "9001"),
    # resource-abuse / laptop freezers (expert-added: freeze your machine)
    "resource exhaustion": ("stress --cpu", "stress-ng", "stress -c", "yes > /dev/null", "yes>/dev/null",
                            "while :; do", "while true; do", "dd if=/dev/zero", "dd of=/dev/sd", "mkfs.",
                            "fork bomb"),
    # connection blockers / network attack tools (expert-added)
    "network attack": ("arpspoof", "ettercap", "arp -s", "hping3", "slowloris", "nping --flood",
                       "syn flood", "synflood", "masscan", "ddos", "iptables -F", "iptables --flush",
                       "nft flush", "ifconfig down", "route del default", "iptables -P INPUT DROP"),
}
LOCKDOWN_ON = ("DELETED watched file", "LOCKDOWN", "policy file modified")
BLOCK_ON = ("ESTABLISHED", "outbound", "NEW outbound", "egress: NEW")  # reserved for future egress-risk scoring

def rule_verdict(trace_info: dict, threat_msg: str) -> dict:
    """Hard rules — every category maps to an action. Compact signature sets."""
    cmd = (trace_info.get("cmd") or "").lower()
    msg = threat_msg.lower()
    if _is_self_noise(f"{msg} {cmd}"):
        return {"action": "IGNORE", "confidence": 0.95, "reason": "self-noise event"}
    blob = f"{cmd} {msg}"
    for category, sigs in KILL.items():
        if any(s in blob for s in sigs):
            return {"action": "KILL", "confidence": 0.9, "reason": f"matches known attack category: {category}"}
    if any(s in msg for s in LOCKDOWN_ON):
        return {"action": "LOCKDOWN", "confidence": 0.95, "reason": "critical system file event"}
    if "owner changed" in msg or "owner changed:" in blob:
        return {"action": "BLOCK", "confidence": 0.8, "reason": "port ownership changed — possible hijack"}
    if "new outbound" in msg and any(p in blob for p in ("4444", "1337", "31337", "6666", "5555")):
        return {"action": "BLOCK", "confidence": 0.85, "reason": "outbound to classic malware port"}
    # expert-added heuristic rules (work even when the LLM is down)
    if "high cpu" in msg:
        return {"action": "MONITOR", "confidence": 0.75, "reason": "sustained high CPU — possible cryptominer or fork bomb"}
    if "new listener" in msg:
        owner = blob.split("owned by")[-1] if "owned by" in blob else "unknown"
        return {"action": "MONITOR", "confidence": 0.7, "reason": f"new listener opened (owner: {owner.strip(' :()\"')[:40]}) — verify it is expected"}
    if ("internet fail" in msg or "dns fail" in msg) and "x3" in msg:
        return {"action": "BLOCK", "confidence": 0.7, "reason": "repeated connectivity loss — possible ARP spoofing / gateway attack"}
    if "unexpected listening port" in msg:
        return {"action": "MONITOR", "confidence": 0.6, "reason": "new listener — investigate trace"}
    if "suspicious process" in msg:
        return {"action": "MONITOR", "confidence": 0.7, "reason": "pattern needs confirmation"}
    return {"action": "MONITOR", "confidence": 0.5, "reason": "uncertain — watch it"}

def act(verdict: dict, trace_info: dict, threat_msg: str):
    a = verdict["action"]
    log_incident("ALERT", "whitecell", f"verdict: {a} (conf {verdict.get('confidence')}): {threat_msg[:60]}")
    if a == "KILL" and trace_info.get("pid"):
        firewall.kill_process(trace_info["pid"], "whitecell verdict")
    elif a == "BLOCK" and trace_info.get("connections"):
        for c in trace_info["connections"]:
            firewall.block_ip(c.split(":")[0], "whitecell verdict")
    elif a == "LOCKDOWN":
        firewall.lockdown("whitecell verdict")

def escalate(trace_info: dict, threat_msg: str):
    """Queue an unresolved threat for the brain (opencode) to judge.

    Whitecells never spawn opencode themselves — that would let every alert
    burn the expensive tier. The brain reads this inbox on its own rhythm,
    subject to the shared budget in brain_budget.py.
    """
    import time, settings as _s
    if not _s.load().get("whitecells_may_escalate", True):
        return
    # dedupe: same message template within 24h does not earn a second brain slot
    import re
    templ = re.sub(r"/[^\s]+|\b[0-9a-f]{6,}\b|\b\d+\b", "", threat_msg).strip()
    statef = BRAIN_INBOX.parent / "brain_escalated_templates.json"
    now = time.time()
    try:
        seen = json.loads(statef.read_text()) if statef.exists() else {}
    except (json.JSONDecodeError, OSError):
        seen = {}
    seen = {k: v for k, v in seen.items() if now - v < 86400}
    if templ in seen:
        return
    seen[templ] = now
    statef.write_text(json.dumps(seen))
    rec = {
        "id": hashlib.sha256(f"{threat_msg}|{json.dumps(trace_info, sort_keys=True)}|{time.time()}".encode()).hexdigest()[:16],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": threat_msg,
        "trace": trace_info,
    }
    BRAIN_INBOX.parent.mkdir(parents=True, exist_ok=True)
    with BRAIN_INBOX.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def hunt():
    """Scan new threats and send a whitecell after each.

    Decision order is deliberate:
      1. deterministic rules FIRST  (instant, zero cost, handles known attacks)
      2. local fallback model ONLY when rules are genuinely unsure, and only
         if the local tier is enabled and under its own minute cap
      3. brain inbox              (opencode reviews it later, budget permitting)
    """
    if not _master_on():
        return  # master OFF — no dispatch, no escalate, no action
    analyzed = set()
    if ANALYZED.exists():
        try:
            analyzed = set(json.loads(ANALYZED.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    if not THREATS.exists():
        return
    for line in THREATS.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("level") not in ("ALERT", "CRITICAL") or e.get("source") == "whitecell":
            continue
        if e.get("hash") in analyzed:
            continue
        if not e.get("hash"):
            continue  # legacy entry without chain hash — skip
        analyzed.add(e["hash"])
        low = f"{e.get('message','')} {json.dumps(e.get('details', {}))}".lower()
        if _is_self_noise(low):
            log_incident("INFO", "whitecell", f"ignoring self-noise: {e['message'][:60]}")
            continue
        log_incident("INFO", "whitecell", f"dispatched on: {e['message'][:50]}")
        t = trace(e.get("details", {}))
        # 1) rules are primary — they already cover the known attack categories
        verdict = rule_verdict(t, e["message"])
        # 2) local small model only fills the genuinely-uncertain gap
        if verdict["confidence"] < 0.7:
            better = ollama_verdict(t, e["message"])
            if better and better.get("confidence", 0) >= verdict["confidence"]:
                verdict = better
        # 3) still unsure -> hand it to the brain (opencode), don't guess.
        #    Only worth the expensive tier when the skin thinks it might be
        #    CRITICAL, or the rules are truly lost (<0.3). Routine uncertain
        #    MONITOR findings stay local so the brain's budget is saved for
        #    things that could actually hurt us.
        if (e.get("level") == "CRITICAL" or verdict["confidence"] < 0.3) and verdict["action"] != "IGNORE":
            escalate(t, e["message"])
            log_incident("INFO", "whitecell", f"escalated to brain: {e['message'][:50]}")
        act(verdict, t, e["message"])
    ANALYZED.parent.mkdir(parents=True, exist_ok=True)
    ANALYZED.write_text(json.dumps(sorted(analyzed)))

if __name__ == "__main__":
    hunt()
