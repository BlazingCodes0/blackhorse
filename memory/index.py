"""BlackHorse memory index — disk-first, permanent vs ephemeral.

Design:
  * PERMANENT memory is NEVER auto-deleted: lessons, rules, trades, key
    decisions, digests, performance. It lives on disk and grows forever.
  * EPHEMERAL memory MUST be deleted after its TTL: raw observations, transient
    signal snapshots, debug dumps.
  * Raw append-only JSONL on disk is gzipped when old, deleted when ancient.
  * SQLite is tuned for DISK (temp_store=FILE, small page cache), so RAM stays
    flat even as history grows.

RAM cost: a few MB of SQLite page cache. Never loads whole logs.

CLI:
  python3 memory/index.py init            # create DB + FTS + schema
  python3 memory/index.py remember --kind lesson --text "..." [--meta k=v ...]
  python3 memory/index.py forget --kind observation --id 12
  python3 memory/index.py search "advisor buy null falling" [--kind observation] [--limit 5]
  python3 memory/index.py recent [--kind trade] [--limit 20]
  python3 memory/index.py cleanup [--apply]     # enforce retention (delete expired)
  python3 memory/index.py stats
"""
import os, sys, json, gzip, time, sqlite3, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
MEM = BH / "memory"
DB = MEM / "brain.db"
RETENTION = MEM / "retention.json"

PERMANENT = {"lesson", "rule", "trade", "decision", "digest", "performance", "identity", "conversation"}
EPHEMERAL = {"observation", "signal_snapshot", "debug", "transient"}


def _retention() -> dict:
    try:
        return json.loads(RETENTION.read_text())
    except (json.JSONDecodeError, OSError):
        return {"ephemeral": {"ttl_seconds": 604800}, "raw_jsonl": {"hot_days": 7, "gzip_days": 90}}


def connect(readonly=False) -> sqlite3.Connection:
    MEM.mkdir(parents=True, exist_ok=True)
    uri = f"file:{DB}?mode=ro" if readonly else str(DB)
    con = sqlite3.connect(uri, uri=readonly, timeout=30)
    con.row_factory = sqlite3.Row
    # Disk-first pragmas — keep RSS flat.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-2000")      # ~2 MB page cache
    con.execute("PRAGMA temp_store=FILE")       # temp data on disk, not RAM
    con.execute(f"PRAGMA mmap_size={64*1024*1024}")
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  zone TEXT NOT NULL,            -- 'permanent' | 'ephemeral'
  text TEXT NOT NULL,
  meta TEXT,                     -- JSON blob (pair, regime, direction, ...)
  expires_at REAL,               -- NULL for permanent
  source TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memory(kind);
CREATE INDEX IF NOT EXISTS idx_mem_zone ON memory(zone);
CREATE INDEX IF NOT EXISTS idx_mem_expires ON memory(expires_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
  USING fts5(text, content='memory', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memory BEGIN
  INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memory BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
"""


def init():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    return {"db": str(DB), "ok": True}


def _zone_for(kind: str) -> str:
    if kind in PERMANENT:
        return "permanent"
    if kind in EPHEMERAL:
        return "ephemeral"
    return "ephemeral"  # unknown kinds default to deletable = safe


def remember(kind: str, text: str, meta: dict | None = None, source: str | None = None) -> int:
    init()
    zone = _zone_for(kind)
    ttl = None
    if zone == "ephemeral":
        ttl = time.time() + int(_retention().get("ephemeral", {}).get("ttl_seconds", 604800))
    con = connect()
    cur = con.execute(
        "INSERT INTO memory(ts, kind, zone, text, meta, expires_at, source) VALUES(?,?,?,?,?,?,?)",
        (time.time(), kind, zone, text, json.dumps(meta or {}), ttl, source),
    )
    con.commit()
    return cur.lastrowid


def forget(mem_id: int) -> bool:
    con = connect()
    cur = con.execute("DELETE FROM memory WHERE id=?", (mem_id,))
    con.commit()
    return cur.rowcount > 0


def search(query: str, kind: str | None = None, zone: str | None = None, limit: int = 10) -> list:
    con = connect()
    sql = ("SELECT m.id, m.ts, m.kind, m.zone, m.text, m.meta, m.expires_at "
           "FROM memory_fts f JOIN memory m ON m.id=f.rowid "
           "WHERE memory_fts MATCH ?")
    params = [query]
    if kind:
        sql += " AND m.kind=?"; params.append(kind)
    if zone:
        sql += " AND m.zone=?"; params.append(zone)
    sql += " ORDER BY m.ts DESC LIMIT ?"; params.append(limit)
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # fall back to LIKE if FTS query syntax is odd
        rows = con.execute(
            "SELECT id, ts, kind, zone, text, meta, expires_at FROM memory "
            "WHERE text LIKE ? ORDER BY ts DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
    return [dict(r) for r in rows]


def recent(kind: str | None = None, limit: int = 20) -> list:
    con = connect()
    if kind:
        rows = con.execute("SELECT * FROM memory WHERE kind=? ORDER BY ts DESC LIMIT ?", (kind, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM memory ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- retention: delete ephemeral + compact raw files -------------------------

def _cleanup_sqlite(apply: bool) -> dict:
    con = connect()
    now = time.time()
    expired = con.execute(
        "SELECT COUNT(*) c FROM memory WHERE zone='ephemeral' AND expires_at IS NOT NULL AND expires_at < ?",
        (now,)).fetchone()["c"]
    if apply and expired:
        con.execute("DELETE FROM memory WHERE zone='ephemeral' AND expires_at IS NOT NULL AND expires_at < ?", (now,))
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    return {"ephemeral_expired": expired}


def _age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / 86400


def _cleanup_files(apply: bool) -> dict:
    plan = _retention().get("raw_jsonl", {})
    hot = plan.get("hot_days", 7)
    gz = plan.get("gzip_days", 90)
    dirs = [BH / d for d in plan.get("dirs", [])]
    to_gzip, to_delete, bytes_freed = [], [], 0
    for d in dirs:
        if not d.exists():
            continue
        for f in d.glob("*.jsonl"):
            age = _age_days(f)
            if age > gz:
                to_delete.append(f)
            elif age > hot:
                to_gzip.append(f)
        for f in d.glob("*.jsonl.gz"):
            if _age_days(f) > gz:
                to_delete.append(f)
    if apply:
        for f in to_gzip:
            with open(f, "rb") as src, gzip.open(str(f) + ".gz", "wb", compresslevel=6) as dst:
                while chunk := src.read(1 << 20):   # stream 1 MB at a time — no full load
                    dst.write(chunk)
            bytes_freed += f.stat().st_size
            f.unlink()
        for f in to_delete:
            try:
                bytes_freed += f.stat().st_size
                f.unlink()
            except OSError:
                pass
    return {"to_gzip": len(to_gzip), "to_delete": len(to_delete),
            "bytes_freed": bytes_freed, "applied": apply}


def cleanup(apply: bool = False) -> dict:
    return {"sqlite": _cleanup_sqlite(apply), "files": _cleanup_files(apply), "applied": apply}


def stats() -> dict:
    con = connect()
    total = con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"]
    by_zone = {r["zone"]: r["c"] for r in con.execute("SELECT zone, COUNT(*) c FROM memory GROUP BY zone")}
    by_kind = {r["kind"]: r["c"] for r in con.execute("SELECT kind, COUNT(*) c FROM memory GROUP BY kind")}
    db_bytes = DB.stat().st_size if DB.exists() else 0
    return {"db": str(DB), "db_bytes": db_bytes, "total": total, "by_zone": by_zone, "by_kind": by_kind}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init"); sub.add_parser("stats")
    r = sub.add_parser("remember"); r.add_argument("--kind", required=True); r.add_argument("--text", required=True); r.add_argument("--meta", nargs="*", default=[])
    f = sub.add_parser("forget"); f.add_argument("--id", type=int, required=True)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--kind"); s.add_argument("--limit", type=int, default=10)
    rc = sub.add_parser("recent"); rc.add_argument("--kind"); rc.add_argument("--limit", type=int, default=20)
    c = sub.add_parser("cleanup"); c.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    cmd = args.cmd or "stats"
    if cmd == "init": print(json.dumps(init(), indent=2))
    elif cmd == "stats": print(json.dumps(stats(), indent=2))
    elif cmd == "remember":
        meta = dict(kv.split("=", 1) for kv in args.meta if "=" in kv)
        print(json.dumps({"id": remember(args.kind, args.text, meta, source="cli")}))
    elif cmd == "forget": print(json.dumps({"deleted": forget(args.id)}))
    elif cmd == "search": print(json.dumps(search(args.query, kind=args.kind, limit=args.limit), indent=2))
    elif cmd == "recent": print(json.dumps(recent(kind=args.kind, limit=args.limit), indent=2))
    elif cmd == "cleanup": print(json.dumps(cleanup(apply=args.apply), indent=2))
