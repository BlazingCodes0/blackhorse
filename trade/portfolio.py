"""portfolio.py — positions + P&L ledger (paper and, later, live).

Append-only on disk. Tracks open positions, closed trades, fees, realized P&L.
Paper fills use the current price + configured slippage + fees so outcomes are
honest (not free money).

CLI:
  python3 trade/portfolio.py status
  python3 trade/portfolio.py open
  python3 trade/portfolio.py history [--limit 20]
"""
import json, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
STATE = BH / "logs" / "portfolio.json"
LEDGER = BH / "logs" / "trades.jsonl"
DEFAULT_FEE_PCT = 0.80          # conservative taker fallback; real value from fees.rates()


def _fee_pct(pair: str = "DOT/USD", side: str = "taker") -> float:
    """Real per-side fee (percent) from fees.py (snapshot/account tier); 0.80 fallback.
    Clamped to a sane range: a corrupt fee snapshot must never inflate a booked fee."""
    try:
        import fees
        mk, tk = fees.rates(pair or "DOT/USD")
        pct = (mk if str(side).lower() == "maker" else tk) * 100.0
        return pct if 0 < pct <= 5.0 else DEFAULT_FEE_PCT
    except Exception:
        return DEFAULT_FEE_PCT


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"positions": [], "realized_pnl": 0.0, "fees_paid": 0.0, "closed": 0, "wins": 0}


def _save(d: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(STATE)


def _log(rec: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def open_positions() -> list:
    return _load().get("positions", [])


def pnl_by_agent() -> dict:
    """Total P&L broken down by the agent that owns each position.

    Realized comes from the store's CLOSED positions (portfolio.json drops them on
    close); unrealized is marked from the open positions. Returns per-agent rows plus
    the grand totals so a dashboard can pretty-print the total and attribute it."""
    d = _load()
    out: dict = {}

    def row(a):
        return out.setdefault(a or "house",
                              {"realized": 0.0, "unrealized": 0.0, "open": 0, "closed": 0})

    opens = []
    for p in d.get("positions", []):
        r = row(p.get("agent"))
        mark = p.get("mark") if p.get("mark") is not None else p.get("entry")
        mult = 1 if str(p.get("side")).upper() == "BUY" else -1
        upl = (float(mark) - float(p["entry"])) * float(p["qty"]) * mult
        r["unrealized"] += upl
        r["open"] += 1
        opens.append({"id": p.get("id"), "agent": p.get("agent") or "house",
                      "pair": p.get("pair"), "side": str(p.get("side") or "").upper(),
                      "entry": float(p["entry"]), "mark": float(mark),
                      "qty": float(p["qty"]), "mode": p.get("mode"),
                      "unrealized": round(upl, 8)})
    try:
        import store
        store.init()
        con = store.connect()
        for r in con.execute("SELECT agent, COALESCE(SUM(net),0) net, COUNT(*) n FROM positions "
                             "WHERE status='CLOSED' GROUP BY agent").fetchall():
            a = row(r["agent"])
            a["realized"] += float(r["net"] or 0)
            a["closed"] += int(r["n"] or 0)
    except Exception:
        pass
    for a in out.values():
        a["realized"] = round(a["realized"], 8)
        a["unrealized"] = round(a["unrealized"], 8)
        a["total"] = round(a["realized"] + a["unrealized"], 8)
    return {"agents": out,
            "open_positions": opens,
            "realized": round(sum(a["realized"] for a in out.values()), 8),
            "unrealized": round(sum(a["unrealized"] for a in out.values()), 8),
            "total": round(sum(a["total"] for a in out.values()), 8)}


def status() -> dict:
    d = _load()
    unreal = 0.0
    for p in d["positions"]:
        if p.get("mark"):
            mult = 1 if p["side"] == "BUY" else -1
            unreal += (p["mark"] - p["entry"]) * p["qty"] * mult
    d["unrealized_pnl"] = round(unreal, 8)
    d["equity"] = round(d["realized_pnl"] + unreal, 8)
    return d


def open_position(agent: str, pair: str, side: str, entry: float, qty: float,
                  stop: float = None, target: float = None, fee_pct: float = None,
                  venue: str = None, note: str = None, mode: str = "PAPER", interval=None, edge_cell=None,
                  fee: float = None, entry_snapshot: dict = None) -> dict:
    d = _load()
    if fee is None:
        pct = fee_pct if fee_pct is not None else _fee_pct(pair)
        fee = entry * qty * (pct / 100)
    pos = {"id": f"pos_{int(time.time()*1000)}", "agent": agent, "pair": pair, "side": side,
           "entry": entry, "qty": qty, "stop": stop, "target": target, "mode": mode,
           "fee_entry": fee, "opened": time.time(), "venue": venue, "note": note, "mark": entry,
           "interval": interval, "edge_cell": edge_cell,
           "entry_snapshot": entry_snapshot}
    d["positions"].append(pos)
    d["fees_paid"] += fee
    _save(d)
    _log({"type": "open", "position": pos})
    try:
        import store
        store.position_open(pos)
    except Exception:
        pass
    return pos


def mark(pair: str, price: float) -> list:
    d = _load()
    hit = []
    for p in list(d["positions"]):
        if p["pair"] != pair:
            continue
        # Refresh the mark for EVERY open order so unrealized P&L is honest, LIVE
        # included. exits.py still owns real-money exits; this only marks to market.
        for q in d["positions"]:
            if q["id"] == p["id"]:
                q["mark"] = price
        if p.get("mode") == "LIVE":
            continue          # NO STOPS / ledger-only exits for real money: exits.py sells for real
        # When the owning agent has the opt-in stop engine ON, exits.manage() owns the
        # stop (it may trail or arm a limit) — so don't fire the raw structural stop here.
        engine = False
        try:
            import agents
            engine = bool(agents.policy_for(p.get("agent")).get("stop_loss_enabled"))
        except Exception:
            engine = False
        reason = None
        if p["side"] == "BUY":
            if not engine and p.get("stop") and price <= p["stop"]:
                reason = "stop"
            elif p.get("target") and price >= p["target"]:
                reason = "target"
        else:
            if not engine and p.get("stop") and price >= p["stop"]:
                reason = "stop"
            elif p.get("target") and price <= p["target"]:
                reason = "target"
        if reason:
            hit.append((reason, p["id"]))
    _save(d)                      # persist refreshed marks before any close
    for reason, pos_id in hit:
        close_position(pos_id, price, reason)
    return [{"id": pos_id, "reason": r, "price": price} for r, pos_id in hit]


def close_position(pos_id: str, price: float, reason: str = "manual", fee_pct: float = None,
                   fee: float = None) -> dict:
    d = _load()
    pos = next((p for p in d["positions"] if p["id"] == pos_id), None)
    if not pos:
        # allow closing by id already removed (idempotent)
        return {"ok": False, "error": "position not found"}
    mult = 1 if pos["side"] == "BUY" else -1
    gross = (price - pos["entry"]) * pos["qty"] * mult
    pct = fee_pct if fee_pct is not None else _fee_pct(pos.get("pair", "DOT/USD"))
    fee_exit = fee if fee is not None else price * pos["qty"] * (pct / 100)
    net = gross - pos["fee_entry"] - fee_exit
    d["positions"] = [p for p in d["positions"] if p["id"] != pos_id]
    d["realized_pnl"] += net
    d["fees_paid"] += fee_exit
    d["closed"] += 1
    if net > 0:
        d["wins"] += 1
    _save(d)
    rec = {"type": "close", "position_id": pos_id, "pair": pos["pair"], "side": pos["side"],
           "entry": pos["entry"], "exit": price, "qty": pos["qty"], "reason": reason,
           "gross": round(gross, 8), "fees": round(pos["fee_entry"] + fee_exit, 8),
           "net": round(net, 8)}
    _log(rec)
    try:
        import store
        store.position_close(pos_id, price, round(net, 8), round(pos["fee_entry"] + fee_exit, 8),
                             fee_exit, reason, pos.get("exit_txid"))
    except Exception:
        pass
    return {"ok": True, "net": round(net, 8), "record": rec}


def reduce_position(pos_id: str, sell_qty: float, price: float,
                    reason: str = "external-close (reconciled)", fee_pct: float = None,
                    fee: float = None) -> dict:
    """Realise P&L for a slice of a position that left the exchange without a
    BlackHorse order (manual sell / withdrawal / closed while we were down).
    Shrinks the position; closes it entirely when the slice is (near) the whole.
    Applies exchange truth to the local ledger — it never sends an order."""
    d = _load()
    pos = next((p for p in d["positions"] if p["id"] == pos_id), None)
    if not pos:
        return {"ok": False, "error": "position not found"}
    qty_have = float(pos["qty"])
    sell_qty = min(float(sell_qty), qty_have)
    if sell_qty <= 0:
        return {"ok": False, "error": "nothing to reduce"}
    if sell_qty >= qty_have - 1e-12:
        return close_position(pos_id, price, reason=reason, fee=fee)
    mult = 1 if pos["side"] == "BUY" else -1
    gross = (price - pos["entry"]) * sell_qty * mult
    pct = fee_pct if fee_pct is not None else _fee_pct(pos.get("pair", "DOT/USD"))
    fee_exit = fee if fee is not None else price * sell_qty * (pct / 100)
    fee_entry_slice = float(pos.get("fee_entry") or 0) * (sell_qty / qty_have)
    net = gross - fee_entry_slice - fee_exit
    pos["qty"] = qty_have - sell_qty
    pos["fee_entry"] = float(pos.get("fee_entry") or 0) - fee_entry_slice
    d["realized_pnl"] += net
    d["fees_paid"] += fee_exit
    _save(d)
    rec = {"type": "reduce", "position_id": pos_id, "pair": pos["pair"], "side": pos["side"],
           "entry": pos["entry"], "exit": price, "qty": sell_qty, "reason": reason,
           "gross": round(gross, 8), "fees": round(fee_entry_slice + fee_exit, 8),
           "net": round(net, 8), "remaining_qty": pos["qty"]}
    _log(rec)
    try:
        import store
        con = store.connect()
        con.execute("UPDATE positions SET qty=?, fee_entry=? WHERE id=?",
                    (pos["qty"], pos["fee_entry"], pos_id))
        con.commit()
    except Exception:
        pass
    return {"ok": True, "net": round(net, 8), "record": rec, "remaining": pos["qty"]}


def history(limit=20) -> list:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(errors="ignore").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "open":
        print(json.dumps(status(), indent=2))
    elif cmd == "history":
        lim = int(sys.argv[sys.argv.index("--limit")+1]) if "--limit" in sys.argv else 20
        print(json.dumps(history(lim), indent=2))
    else:
        print(json.dumps(status(), indent=2))


def reconcile_live(tol_pct: float = 1.0) -> dict:
    """Compare the ledger's open LIVE positions to the real exchange balance.
    The exchange is the truth. Writes logs/ledger_mismatch.json and returns
    {"ok": bool, "mismatches": [...]}. Read-only on the exchange."""
    try:
        import execute
        bal = execute.kraken_balance()
    except Exception as e:
        return {"ok": None, "error": f"exchange unavailable: {e}"}
    if not bal:                                   # empty = the read failed, NOT "you hold nothing"
        return {"ok": None, "error": "balance unavailable (read failed) - not compared"}
    ledger = {}
    for p in open_positions():
        if p.get("mode") == "LIVE" and str(p.get("side")).upper() == "BUY":
            base = p["pair"].split("/")[0].upper()
            ledger[base] = ledger.get(base, 0.0) + float(p["qty"])
    out = []
    for base, q in ledger.items():
        have = float(bal.get(base, 0) or 0)
        if q > 0 and abs(have - q) / q * 100 > tol_pct:
            out.append({"asset": base, "ledger_qty": round(q, 8), "exchange_qty": round(have, 8),
                        "note": "exchange holds LESS than ledger" if have < q else "exchange holds MORE than ledger"})
    res = {"ok": not out, "mismatches": out, "ts": time.time()}
    try:
        (LEDGER.parent / "ledger_mismatch.json").write_text(json.dumps(res, indent=2))
    except OSError:
        pass
    return res
