"""signals.py — ONE signal snapshot PER PAIR (logs/signals/<PAIR>.json).

The loop now scans every pair its agents trade. Each pair gets its own snapshot so
decisions, exits and snapshots never read another pair's numbers.
logs/signal_latest.json is kept for the GUI/brain as the PRIMARY pair's snapshot
(config/signal_setup.json "pair").

  load(pair)  -> snapshot dict ({} if none)   age(pair) -> seconds since written
"""
import json, os, time
from pathlib import Path

BH = Path.home() / "blackhorse"
DIR = BH / "logs" / "signals"
LATEST = BH / "logs" / "signal_latest.json"


def slug(pair: str) -> str:
    return str(pair).replace("/", "-").upper()


def path(pair: str) -> Path:
    return DIR / f"{slug(pair)}.json"


def primary_pair() -> str:
    try:
        return json.loads((BH / "config" / "signal_setup.json").read_text()).get("pair") or "DOT/USD"
    except (OSError, json.JSONDecodeError):
        return "DOT/USD"


def save(pair: str, snap: dict) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    p = path(pair)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=2))
    os.replace(tmp, p)                      # atomic: readers never see a half-written file
    return p


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load(pair: str = None) -> dict:
    """Snapshot for THIS pair. Falls back to signal_latest.json only if it is for the same pair."""
    if not pair:
        return _read(LATEST)
    s = _read(path(pair))
    if s:
        return s
    s = _read(LATEST)
    return s if s.get("pair") == pair else {}


def age(pair: str) -> float:
    p = path(pair)
    if p.exists():
        return time.time() - p.stat().st_mtime
    if LATEST.exists() and _read(LATEST).get("pair") == pair:
        return time.time() - LATEST.stat().st_mtime
    return 1e9
