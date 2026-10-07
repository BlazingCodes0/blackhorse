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
                  stop: float = None, target: float = None, fee_pct: float = 0.26,
                  venue: str = None, note: str = None, mode: str = "PAPER", interval=None, edge_cell=None,
                  fee: float = None, entry_snapshot: dict = None) -> dict:
    d = _load()
    if fee is None:
        fee = entry * qty * (fee_pct / 100)
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
        if p.get("mode") == "LIVE":
            continue          # NO STOPS / ledger-only exits for real money: exits.py sells for real
        reason = None
        if p["side"] == "BUY":
            if p.get("stop") and price <= p["stop"]:
                reason = "stop"
            elif p.get("target") and price >= p["target"]:
                reason = "target"
        else:
            if p.get("stop") and price >= p["stop"]:
                reason = "stop"
            elif p.get("target") and price <= p["target"]:
                reason = "target"
        if reason:
            hit.append((reason, p["id"]))
        else:
            # just update the mark
            for q in d["positions"]:
                if q["id"] == p["id"]:
                    q["mark"] = price
    if not hit:
        _save(d)
    for reason, pos_id in hit:
        close_position(pos_id, price, reason)
    return [{"id": pos_id, "reason": r, "price": price} for r, pos_id in hit]


def close_position(pos_id: str, price: float, reason: str = "manual", fee_pct: float = 0.26,
                   fee: float = None) -> dict:
    d = _load()
    pos = next((p for p in d["positions"] if p["id"] == pos_id), None)
    if not pos:
        # allow closing by id already removed (idempotent)
        return {"ok": False, "error": "position not found"}
    mult = 1 if pos["side"] == "BUY" else -1
    gross = (price - pos["entry"]) * pos["qty"] * mult
    fee_exit = fee if fee is not None else price * pos["qty"] * (fee_pct / 100)
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
