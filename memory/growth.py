"""memory/growth.py — bounded, disk-backed "brain growth" store.

A simple append-only JSONL for the brain's growing notes (lessons, rules,
observations) capped at a hard byte ceiling. When the cap is breached the
oldest entries are evicted, newest kept. All on disk — no RAM cost.

Cap: 5 GiB total under memory/growth/.

CLI:
  python3 memory/growth.py add kind text [source]
  python3 memory/growth.py list [kind] [limit]
  python3 memory/growth.py size
"""
import json, os, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
GROWTH = BH / "memory" / "growth"
LOG = GROWTH / "notes.jsonl"
CAP = 5 * 1024 ** 3  # 5 GiB

GROWTH.mkdir(parents=True, exist_ok=True)


def _total_bytes() -> int:
    return sum(p.stat().st_size for p in GROWTH.rglob("*") if p.is_file())


def _prune():
    """Evict oldest-lines until under 90% of the cap."""
    if _total_bytes() <= CAP:
        return
    try:
        lines = LOG.read_text(errors="ignore").splitlines()
    except OSError:
        return
    keep = []
    used = 0
    for l in reversed(lines):
        b = len(l.encode()) + 1
        if used + b > CAP * 0.9:
            break
        used += b
        keep.append(l)
    keep.reverse()
    LOG.write_text("\n".join(keep) + ("\n" if keep else ""))


def add(kind: str, text: str, source: str = "brain"):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "kind": kind, "text": text, "source": source}
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    _prune()
    return rec


def search(query: str, limit: int = 20):
    """Simple substring search over growth notes (streams; no RAM blow-up)."""
    if not LOG.exists() or not query:
        return []
    q = query.lower()
    out = []
    for line in reversed(LOG.read_text(errors="ignore").splitlines()):
        if q in line.lower():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                break
    return list(reversed(out))


def list_recent(kind: str | None = None, limit: int = 20):
    if not LOG.exists():
        return []
    out = []
    for line in reversed(LOG.read_text(errors="ignore").splitlines()):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if kind and r.get("kind") != kind:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return list(reversed(out))


def _cmd():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        kind, text = sys.argv[2], " ".join(sys.argv[3:-1]) if len(sys.argv) > 4 else sys.argv[3]
        source = sys.argv[-1] if len(sys.argv) > 4 and sys.argv[-1] != text else "brain"
        print(add(kind, text, source))
    elif cmd == "size":
        print(f"{_total_bytes() / (1024**3):.4f} GiB / 5 GiB")
    else:
        kind = sys.argv[2] if len(sys.argv) > 2 else None
        for r in list_recent(kind, 20):
            print(f"[{r['kind']}] {r['text']} ({r['source']})")


if __name__ == "__main__":
    _cmd()
