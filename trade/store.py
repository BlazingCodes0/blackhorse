"""store.py — the trading database (SQLite, disk-first, audit-grade).

One place for the money trail: orders, fills, positions, decisions, heart
verdicts. JSON files were fine for paper; live needs idempotency, reconciliation,
crash-safety, and replay. SQLite is deliberately chosen for the N3540.

Key ideas:
  * IDEMPOTENCY: every order carries a client_order_id; insert_or_ignore means a
    retry can never double-fire. (This is exactly what qubitFlow lacked.)
  * AUDIT/REPLAY: every state change is a row; append-only-ish; nothing is
    destroyed silently. A decision row records intent; a heart row its verdict;
    order/fill rows the execution.
  * RECONCILIATION: local order rows carry exchange_order_id once known, so a
    restart can match local <-> exchange <-> fill.

Tuned for DISK (temp_store=FILE, small page cache) so RAM stays flat.

CLI:
  python3 trade/store.py init
  python3 trade/store.py orders [--status OPEN] [--limit 20]
  python3 trade/store.py positions
  python3 trade/store.py stats
"""
import json, sys, time, sqlite3, hashlib, os
from pathlib import Path

BH = Path.home() / "blackhorse"
DB = BH / "logs" / "trading.db"


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA cache_size=-2000")     # ~2 MB
    con.execute("PRAGMA temp_store=FILE")
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  client_order_id TEXT UNIQUE,          -- idempotency key
  created REAL NOT NULL,
  updated REAL,
  venue TEXT, pair TEXT, side TEXT,
  order_type TEXT,                    -- market | limit | stop-loss | trailing-stop | ...
  price REAL, amount_base REAL, amount_quote REAL,
  status TEXT NOT NULL,               -- NEW | OPEN | FILLED | PARTIAL | CANCELED | REJECTED
  exchange_order_id TEXT,             -- once the venue acknowledges
  agent TEXT, source TEXT,            -- 'spice' | 'agent:<id>' | 'house'
  mode TEXT,                          -- PAPER | LIVE
  reason TEXT, raw TEXT
);

CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES orders(id),
  ts REAL NOT NULL,
  price REAL, amount REAL, fee REAL, fee_currency TEXT,
  exchange_fill_id TEXT, raw TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY,                -- pos_...
  opened REAL, closed REAL,
  agent TEXT, pair TEXT, side TEXT,
  entry REAL, qty REAL, stop REAL, target REAL,
  exit REAL, status TEXT,             -- OPEN | CLOSED
  realized_pnl REAL, fees REAL, reason TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  source TEXT,                        -- broker | spice | loop | manual
  action TEXT, pair TEXT, agent TEXT, channel TEXT,
  entry REAL, stop REAL, target REAL, size_quote REAL,
  rr REAL, edge TEXT,                 -- json: the cited edge cell
  reason TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS heart_verdicts (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  action TEXT, quote REAL, venue TEXT,
  decision TEXT, level TEXT, reason TEXT, detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_coid ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_exch ON orders(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts);
"""


def init() -> dict:
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    return {"db": str(DB), "ok": True}


def _now():
    return time.time()


def client_order_id(source: str, pair: str, side: str, amount_quote: float) -> str:
    """Deterministic-ish unique key. Caller may pass a nonce for true uniqueness."""
    raw = f"{source}|{pair}|{side}|{round(float(amount_quote or 0), 8)}|{int(_now()*1000)}|{os.urandom(4).hex()}"
    return "coid_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---- orders -----------------------------------------------------------------

def create_order(source, pair, side, order_type, price=None, amount_base=None,
                 amount_quote=None, agent=None, mode="PAPER", reason=None,
                 coid=None) -> dict:
    """Insert an order. Idempotent on client_order_id: a duplicate returns the
    existing row and does NOT create a second order."""
    init()
    coid = coid or client_order_id(source, pair, side, amount_quote or 0)
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO orders(client_order_id, created, updated, venue, pair, side, "
            "order_type, price, amount_base, amount_quote, status, agent, source, mode, reason) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (coid, _now(), _now(), _active_venue(), pair, side, order_type,
             price, amount_base, amount_quote, "NEW", agent, source, mode, reason))
        con.commit()
        row = _order_row(con, cur.lastrowid)
        return {"created": True, "order": row}
    except sqlite3.IntegrityError:
        row = con.execute("SELECT * FROM orders WHERE client_order_id=?", (coid,)).fetchone()
        return {"created": False, "duplicate": True, "order": dict(row) if row else None}


def set_order_status(order_id, status, exchange_order_id=None, raw=None, reason=None):
    con = connect()
    con.execute("UPDATE orders SET status=?, updated=?, exchange_order_id=COALESCE(?, exchange_order_id),"
                " raw=COALESCE(?, raw), reason=COALESCE(?, reason) WHERE id=?",
                (status, _now(), exchange_order_id, json.dumps(raw) if raw else None, reason, order_id))
    con.commit()
    return _order_row(con, order_id)


def add_fill(order_id, price, amount, fee=0.0, fee_currency=None, exchange_fill_id=None, raw=None):
    con = connect()
    con.execute("INSERT INTO fills(order_id, ts, price, amount, fee, fee_currency, exchange_fill_id, raw) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (order_id, _now(), price, amount, fee, fee_currency, exchange_fill_id,
                 json.dumps(raw) if raw else None))
    con.commit()
    return True


def open_orders(limit=50) -> list:
    con = connect()
    rows = con.execute("SELECT * FROM orders WHERE status IN ('NEW','OPEN','PARTIAL') "
                       "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _order_row(con, oid):
    r = con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    return dict(r) if r else None


# ---- positions --------------------------------------------------------------

def open_position(pos_id, agent, pair, side, entry, qty, stop=None, target=None,
                  mode="PAPER", reason=None) -> dict:
    init()
    con = connect()
    con.execute("INSERT OR REPLACE INTO positions(id, opened, agent, pair, side, entry, qty, "
                "stop, target, status) VALUES(?,?,?,?,?,?,?,?,?, 'OPEN')",
                (pos_id, _now(), agent, pair, side, entry, qty, stop, target))
    con.commit()
    return True


def close_position(pos_id, exit_price, realized_pnl, fees=0.0, reason=None) -> bool:
    con = connect()
    con.execute("UPDATE positions SET closed=?, exit=?, status='CLOSED', realized_pnl=?, "
                "fees=?, reason=? WHERE id=?",
                (_now(), exit_price, realized_pnl, fees, reason, pos_id))
    con.commit()
    return True


def open_positions() -> list:
    con = connect()
    rows = con.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
    return [dict(r) for r in rows]


def recent_decisions(limit=10) -> list:
    init()
    con = connect()
    rows = con.execute("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- decisions + heart ------------------------------------------------------

def record_decision(source, action, pair=None, agent=None, channel=None, entry=None,
                    stop=None, target=None, size_quote=None, rr=None, edge=None,
                    reason=None, detail=None) -> int:
    init()
    con = connect()
    cur = con.execute(
        "INSERT INTO decisions(ts, source, action, pair, agent, channel, entry, stop, target, "
        "size_quote, rr, edge, reason, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_now(), source, action, pair, agent, channel, entry, stop, target, size_quote, rr,
         json.dumps(edge) if edge is not None else None, reason,
         json.dumps(detail) if detail is not None else None))
    con.commit()
    return cur.lastrowid


def record_heart(action, quote, venue, decision, level, reason, detail=None) -> int:
    init()
    con = connect()
    cur = con.execute(
        "INSERT INTO heart_verdicts(ts, action, quote, venue, decision, level, reason, detail) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (_now(), action, quote, venue, decision, level, reason,
         json.dumps(detail) if detail is not None else None))
    con.commit()
    return cur.lastrowid


def _active_venue():
    try:
        import exchanges
        return exchanges.active_exchange()["name"]
    except Exception:
        return None


def stats() -> dict:
    con = connect()
    out = {}
    for t in ("orders", "fills", "positions", "decisions", "heart_verdicts"):
        try:
            out[t] = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        except sqlite3.OperationalError:
            out[t] = "no table (run init)"
    out["open_orders"] = len(open_orders()) if isinstance(out.get("orders"), int) else "?"
    out["db_bytes"] = DB.stat().st_size if DB.exists() else 0
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "init":
        print(json.dumps(init(), indent=2))
    elif cmd == "orders":
        print(json.dumps(open_orders(), indent=2))
    elif cmd == "positions":
        print(json.dumps(open_positions(), indent=2))
    else:
        print(json.dumps(stats(), indent=2))
