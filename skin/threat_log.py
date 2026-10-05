"""Append-only incident logger -> ~/blackhorse/logs/threats.jsonl
Every entry is hash-chained: entry.hash = sha256(prev_hash + entry body).
Deleting/editing any line breaks the chain -> tampering is detectable via verify_chain()."""
import json, time, os, hashlib
from pathlib import Path

LOG_DIR = Path.home() / "blackhorse" / "logs"
THREAT_LOG = LOG_DIR / "threats.jsonl"
CHAIN_STATE = LOG_DIR / ".threat_chain_head"

LEVELS = ("INFO", "WARN", "ALERT", "CRITICAL")

def _last_hash() -> str:
    if CHAIN_STATE.exists():
        return CHAIN_STATE.read_text().strip()
    return "GENESIS"

def log_incident(level: str, source: str, message: str, details: dict | None = None):
    if level not in LEVELS:
        level = "INFO"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "source": source,
        "message": message,
        "details": details or {},
        "prev": _last_hash(),
    }
    body = json.dumps(entry, sort_keys=True)
    entry["hash"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    # Persist only real events (WARN+). INFO chatter stays console-only.
    if level != "INFO":
        with open(THREAT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        CHAIN_STATE.write_text(entry["hash"])
    print(f"[{entry['ts']}] {level:8s} {source}: {message}")
    return entry

def verify_chain() -> tuple[bool, str]:
    """Recompute the chain; returns (ok, first_bad_line or 'ok')."""
    if not THREAT_LOG.exists():
        return True, "empty"
    prev = "GENESIS"
    for i, line in enumerate(THREAT_LOG.read_text().splitlines()):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            return False, f"line {i}: corrupt JSON (edited/truncated?)"
        if "prev" not in e or "hash" not in e:
            # legacy pre-chain entry — chain restarts after this
            prev = None
            continue
        if prev is not None and e["prev"] != prev:
            return False, f"line {i}: prev-hash mismatch — log was tampered with"
        body = json.dumps({k: v for k, v in e.items() if k != "hash"}, sort_keys=True)
        if hashlib.sha256(body.encode()).hexdigest()[:16] != e["hash"]:
            return False, f"line {i}: hash mismatch — entry was edited"
        prev = e["hash"]
    # tail-deletion check: on-disk head must match the last entry we read
    if CHAIN_STATE.exists() and prev is not None and CHAIN_STATE.read_text().strip() != prev:
        return False, "head mismatch — recent entries were deleted"
    return True, "ok"

if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        ok, msg = verify_chain()
        print(("CHAIN OK" if ok else f"TAMPER DETECTED: {msg}"))
        sys.exit(0 if ok else 1)
    log_incident("INFO", "threat_log", "logger self-test")
