"""File-integrity watch: hash tracked files; alert on change/add/delete."""
import json, hashlib
from pathlib import Path
from threat_log import log_incident

BASELINE = Path.home() / "blackhorse" / "logs" / "integrity_baseline.json"

# directories whose contents must not silently change
WATCHED = [
    Path.home() / "blackhorse" / "skin",
    Path.home() / "blackhorse" / "heart" / "policies",
    Path.home() / "blackhorse" / "config",
]
SKIP = {".pyc", ".log", ".jsonl"}  # runtime artifacts, hashed elsewhere

def _hash(p: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(p.read_bytes())
    except (OSError, PermissionError):
        return "unreadable"
    return h.hexdigest()[:16]

def scan():
    try:
        baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    except (OSError, ValueError):
        baseline = {}  # empty/corrupt baseline — re-baseline, never crash the sweep
    if not isinstance(baseline, dict):
        baseline = {}
    current = {}
    for root in WATCHED:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.is_file() and f.suffix not in SKIP:
                current[str(f)] = _hash(f)

    for path, h in current.items():
        if path not in baseline:
            log_incident("WARN", "integrity", f"NEW file appearing under watched dir: {path}")
        elif baseline[path] != h:
            log_incident("ALERT", "integrity", f"MODIFIED watched file: {path}")
    for path in baseline:
        if path not in current:
            log_incident("ALERT", "integrity", f"DELETED watched file: {path}")

    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(current, indent=2))
    return current

if __name__ == "__main__":
    scan()
