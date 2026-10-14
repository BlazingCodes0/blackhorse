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
    con.execute("PRAGMA synchronous=FULL")
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
    for tbl, col, typ in (
            ("positions", "interval", "TEXT"), ("positions", "edge_cell", "TEXT"),
            ("positions", "mode", "TEXT"), ("positions", "fee_entry", "REAL"),
            ("positions", "fee_exit", "REAL"), ("positions", "entry_txid", "TEXT"),
            ("positions", "exit_txid", "TEXT"), ("positions", "entry_snapshot", "TEXT"),
            ("positions", "net", "REAL"),
            ("orders", "expires", "REAL"), ("orders", "snapshot", "TEXT"),
            ("orders", "vol_exec", "REAL"), ("orders", "avg_price", "REAL"),
            ("orders", "fee", "REAL"), ("orders", "outlay", "REAL")):
        try:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
        except Exception:
            pass
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_fill_ex ON fills(exchange_fill_id) "
                "WHERE exchange_fill_id IS NOT NULL")
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
                  mode="PAPER", reason=None, interval=None, edge_cell=None) -> dict:
    init()
    con = connect()
    con.execute("INSERT INTO positions(id, opened, agent, pair, side, entry, qty, "
                "stop, target, status, interval, edge_cell, mode) VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET agent=excluded.agent, pair=excluded.pair, "
                "side=excluded.side, entry=excluded.entry, qty=excluded.qty, stop=excluded.stop, "
                "target=excluded.target, interval=COALESCE(excluded.interval, interval), "
                "edge_cell=COALESCE(excluded.edge_cell, edge_cell), mode=COALESCE(excluded.mode, mode)",
                (pos_id, _now(), agent, pair, side, entry, qty, stop, target, interval, edge_cell, mode))
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


# ---- LIVE mirror: the exchange truth lands here ------------------------------

def live_order(txid, agent, pair, side, order_type, price, volume, outlay=None,
               expires=None, plan=None, snapshot=None, status="OPEN") -> bool:
    """Record a REAL exchange order the moment it is accepted. Idempotent on txid."""
    init()
    con = connect()
    con.execute(
        "INSERT OR IGNORE INTO orders(client_order_id, created, updated, venue, pair, side, order_type, "
        "price, amount_base, amount_quote, status, exchange_order_id, agent, source, mode, reason, "
        "expires, snapshot, outlay, raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("kr_" + str(txid), _now(), _now(), _active_venue() or "kraken", pair, side, order_type,
         price, volume, (price or 0) * volume if price else None, status, txid, agent,
         "agent:" + str(agent), "LIVE", (plan or {}).get("reason"), expires,
         json.dumps(snapshot) if snapshot else None, outlay,
         json.dumps({k: v for k, v in (plan or {}).items() if k in
                     ("channel", "entry", "stop", "target", "size_quote", "outlay_quote",
                      "order_type", "expiry_s", "rr", "evidence")}, default=str)))
    con.commit()
    return True


def live_order_result(txid, status, vol_exec=0.0, avg_price=None, fee=0.0) -> bool:
    """Exchange outcome for an order: FILLED | PARTIAL | EXPIRED | CANCELED | OPEN."""
    init()
    con = connect()
    con.execute("UPDATE orders SET status=?, updated=?, vol_exec=?, avg_price=?, fee=? "
                "WHERE client_order_id=?",
                (status, _now(), vol_exec, avg_price, fee, "kr_" + str(txid)))
    if vol_exec and vol_exec > 0:
        row = con.execute("SELECT id, pair FROM orders WHERE client_order_id=?", ("kr_" + str(txid),)).fetchone()
        if row:
            con.execute("INSERT OR REPLACE INTO fills(order_id, ts, price, amount, fee, fee_currency, "
                        "exchange_fill_id) VALUES(?,?,?,?,?,?,?)",
                        (row["id"], _now(), avg_price, vol_exec, fee, "USD", "ord:" + str(txid)))
    con.commit()
    return True


def position_open(pos: dict) -> bool:
    """Mirror a portfolio position (full fidelity incl. entry snapshot + real fee)."""
    import re
    init()
    m = re.search(r"\b([A-Z0-9]{5,6}-[A-Z0-9]{5}-[A-Z0-9]{6})\b", str(pos.get("note") or ""))
    con = connect()
    con.execute(
        "INSERT INTO positions(id, opened, agent, pair, side, entry, qty, stop, target, status, "
        "interval, edge_cell, mode, fee_entry, entry_txid, entry_snapshot) "
        "VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET entry=excluded.entry, qty=excluded.qty, "
        "fee_entry=excluded.fee_entry, entry_snapshot=COALESCE(excluded.entry_snapshot, entry_snapshot), "
        "entry_txid=COALESCE(excluded.entry_txid, entry_txid)",
        (pos["id"], pos.get("opened"), pos.get("agent"), pos.get("pair"), pos.get("side"),
         pos.get("entry"), pos.get("qty"), pos.get("stop"), pos.get("target"),
         pos.get("interval"), pos.get("edge_cell"), pos.get("mode"), pos.get("fee_entry"),
         m.group(1) if m else None,
         json.dumps(pos["entry_snapshot"]) if pos.get("entry_snapshot") else None))
    con.commit()
    return True


def position_close(pos_id, exit_price, net, fees_total, fee_exit, reason, exit_txid=None) -> bool:
    init()
    con = connect()
    con.execute("UPDATE positions SET closed=?, exit=?, status='CLOSED', realized_pnl=?, net=?, fees=?, "
                "fee_exit=?, reason=?, exit_txid=COALESCE(?, exit_txid) WHERE id=?",
                (_now(), exit_price, net, net, fees_total, fee_exit, reason, exit_txid, pos_id))
    con.commit()
    return True


def update_position_snapshot(pos_id, snapshot) -> bool:
    con = connect()
    con.execute("UPDATE positions SET entry_snapshot=? WHERE id=?", (json.dumps(snapshot), pos_id))
    con.commit()
    return True


def verify_against_portfolio() -> dict:
    """The DB and logs/portfolio.json must agree on what is open."""
    try:
        import portfolio
        js = {p["id"] for p in portfolio.open_positions()}
    except Exception as e:
        return {"ok": None, "error": str(e)}
    db = {p["id"] for p in open_positions()}
    return {"ok": js == db, "only_in_json": sorted(js - db), "only_in_db": sorted(db - js)}


def report() -> dict:
    init()
    con = connect()
    q = lambda sql: [dict(r) for r in con.execute(sql).fetchall()]
    return {"stats": stats(),
            "orders": q("SELECT id, datetime(created,'unixepoch') t, pair, side, order_type, price, "
                        "amount_base vol, vol_exec, avg_price, fee, status, agent, exchange_order_id "
                        "FROM orders ORDER BY created DESC LIMIT 10"),
            "positions": q("SELECT id, status, agent, pair, entry, qty, fee_entry, exit, net, mode, "
                           "entry_txid, exit_txid, reason, entry_snapshot IS NOT NULL has_snapshot "
                           "FROM positions ORDER BY opened DESC LIMIT 10")}


def backfill_from_kraken(since_ts: float = 1791072000) -> dict:
    """Pull REAL fills from Kraken TradesHistory into orders+fills (idempotent), and
    mirror portfolio.json / trades.jsonl positions. Read-only on the exchange."""
    import execute, portfolio
    init()
    t = execute._kraken_private("/0/private/TradesHistory", {"nonce": str(int(time.time() * 1000))})
    trades = (t.get("result") or {}).get("trades") or {}
    by_order = {}
    for tid, v in trades.items():
        if v["time"] < since_ts or v["pair"].startswith("ZUSD") or v["pair"].endswith("ZCAD"):
            continue
        by_order.setdefault(v["ordertxid"], []).append(v)
    n = 0
    for otx, vs in by_order.items():
        vol = sum(float(x["vol"]) for x in vs); cost = sum(float(x["cost"]) for x in vs)
        fee = sum(float(x["fee"]) for x in vs); avg = cost / vol if vol else None
        pair = vs[0]["pair"]; pair = pair[:-3] + "/USD" if pair.endswith("USD") else pair
        live_order(otx, None, pair, vs[0]["type"], vs[0]["ordertype"], avg, vol, status="FILLED")
        live_order_result(otx, "FILLED", vol, avg, fee)
        con = connect()
        con.execute("UPDATE orders SET created=?, source='backfill:kraken' WHERE client_order_id=?",
                    (min(float(x["time"]) for x in vs), "kr_" + otx))
        con.commit(); n += 1
    pos_n = 0
    for p in portfolio.open_positions():
        position_open(p); pos_n += 1
    try:
        for line in (BH / "logs" / "trades.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r.get("type") == "open" and r["position"]["id"] not in {p["id"] for p in portfolio.open_positions()}:
                position_open(r["position"]); pos_n += 1
            elif r.get("type") == "close":
                position_close(r["position_id"], r.get("exit"), r.get("net"), r.get("fees"), None,
                               r.get("reason"))
    except (OSError, json.JSONDecodeError):
        pass
    return {"orders_backfilled": n, "positions_mirrored": pos_n}


def record_decision_changed(source, action, pair=None, agent=None, min_gap_s=3600, **kw):
    """Decision log WITHOUT the noise. BUY/SELL always recorded. HOLD/SKIP only when the
    action or the reason (digits ignored) changed since this agent's last row for the pair,
    or at most once per min_gap_s as a heartbeat. Keeps the table small."""
    import re
    init()
    con = connect()
    reason = kw.get("reason")
    if action not in ("BUY", "SELL"):
        last = con.execute("SELECT ts, action, reason FROM decisions WHERE source=? AND agent IS ? AND pair IS ? "
                           "ORDER BY ts DESC LIMIT 1", (source, agent, pair)).fetchone()
        norm = lambda r: re.sub(r"[0-9.$%]+", "#", str(r or ""))
        if last and last["action"] == action and norm(last["reason"]) == norm(reason) \
                and _now() - last["ts"] < min_gap_s:
            return None
    return record_decision(source, action, pair=pair, agent=agent, **kw)


def checkpoint() -> dict:
    """Flush the trading WAL back into the main file so a poweroff can't strand
    it. Safe to call with nothing pending."""
    con = connect()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"db": str(DB), "db_bytes": DB.stat().st_size if DB.exists() else 0}


def prune(hold_days: int = 14) -> dict:
    """Drop old HOLD/SKIP decisions + old heart ALLOW rows. BUY/SELL decisions, orders,
    fills and positions are NEVER deleted (audit trail)."""
    init()
    con = connect()
    cut = _now() - hold_days * 86400
    a = con.execute("DELETE FROM decisions WHERE ts<? AND action NOT IN ('BUY','SELL')", (cut,)).rowcount
    b = con.execute("DELETE FROM heart_verdicts WHERE ts<? AND decision='ALLOW' AND action NOT IN ('BUY','SELL')",
                    (cut,)).rowcount
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"decisions_pruned": a, "heart_pruned": b}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "init":
        print(json.dumps(init(), indent=2))
    elif cmd == "orders":
        print(json.dumps(open_orders(), indent=2))
    elif cmd == "positions":
        print(json.dumps(open_positions(), indent=2))
    elif cmd == "prune":
        print(json.dumps(prune(), indent=2))
    elif cmd == "report":
        print(json.dumps(report(), indent=2, default=str))
    elif cmd == "backfill":
        print(json.dumps(backfill_from_kraken(), indent=2))
    elif cmd == "verify":
        print(json.dumps(verify_against_portfolio(), indent=2))
    else:
        print(json.dumps(stats(), indent=2))
