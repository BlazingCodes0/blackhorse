"""BlackHorse treasury — the capital object ("I gave it 100").

Source of truth is an APPEND-ONLY ledger on disk; treasury.json is just a
materialized view that can always be rebuilt by replaying the ledger. This
means capital can never be silently mutated — every change is a new record.

Nothing here places orders. This only tracks money: what you allocated, how
much is available, how much is at risk, realized P&L, and a reserve floor the
brain may never spend below.

CLI:
  python3 trade/treasury.py init --currency USD
  python3 trade/treasury.py allocate 100 --note "seed"     # user gives 100
  python3 trade/treasury.py debit 20 --reason "paper entry DOT"
  python3 trade/treasury.py credit 23 --reason "paper exit DOT"
  python3 trade/treasury.py set-reserve 50
  python3 trade/treasury.py status
  python3 trade/treasury.py ledger [--limit 20]
  python3 trade/treasury.py rebuild
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "memory"))
STATE = BH / "config" / "treasury.json"
LEDGER = BH / "logs" / "treasury_ledger.jsonl"

DEFAULT = {"currency": "USD", "allocated": 0.0, "cash": 0.0, "reserve_floor": 0.0,
           "open_risk": 0.0, "realized_pnl": 0.0, "updated": None}


def _append(evt: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    evt = {"ts": time.time(), "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **evt}
    with LEDGER.open("a") as f:
        f.write(json.dumps(evt) + "\n")
    return evt


def _events() -> list:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def rebuild() -> dict:
    """Replay the ledger into the materialized state."""
    st = dict(DEFAULT)
    for e in _events():
        t = e.get("type")
        amt = float(e.get("amount", 0) or 0)
        if t == "init":
            st["currency"] = e.get("currency", st["currency"])
        elif t == "reconcile":
            st["allocated"] = e.get("total", st["allocated"])
            st["cash"] = e.get("cash", st["cash"])
            st["holdings"] = e.get("holdings", {})
        elif t == "allocate":
            st["allocated"] += amt
            st["cash"] += amt
        elif t == "debit":
            st["cash"] -= amt
        elif t == "credit":
            st["cash"] += amt
        elif t == "set_reserve":
            st["reserve_floor"] = amt
        elif t == "set_open_risk":
            st["open_risk"] = amt
        elif t == "realized":
            st["realized_pnl"] += amt
        st["updated"] = e.get("ts_iso")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))
    return st


def status() -> dict:
    try:
        st = json.loads(STATE.read_text())
    except (json.JSONDecodeError, OSError):
        st = rebuild()
    st["available"] = round(st.get("cash", 0) - st.get("reserve_floor", 0), 8)
    st["equity"] = round(st.get("cash", 0) + st.get("open_risk", 0), 8)
    return st


def _require_init():
    if STATE.exists():
        return
    _append({"type": "init", "currency": "USD"})
    rebuild()


def allocate(amount: float, note: str = None) -> dict:
    _require_init()
    if amount <= 0:
        raise ValueError("allocate amount must be > 0")
    _append({"type": "allocate", "amount": amount, "note": note, "source": "user"})
    return rebuild()


def debit(amount: float, reason: str = None) -> dict:
    _require_init()
    if amount <= 0:
        raise ValueError("debit amount must be > 0")
    st = status()
    if st["cash"] - amount < st.get("reserve_floor", 0) - 1e-9:
        raise RuntimeError(f"debit {amount} would breach reserve floor "
                           f"({st['cash']} - {amount} < {st['reserve_floor']})")
    _append({"type": "debit", "amount": amount, "reason": reason})
    return rebuild()


def credit(amount: float, reason: str = None) -> dict:
    _require_init()
    if amount <= 0:
        raise ValueError("credit amount must be > 0")
    _append({"type": "credit", "amount": amount, "reason": reason})
    return rebuild()


def set_reserve(amount: float) -> dict:
    _require_init()
    _append({"type": "set_reserve", "amount": float(amount)})
    return rebuild()


def set_open_risk(amount: float) -> dict:
    _require_init()
    _append({"type": "set_open_risk", "amount": float(amount)})
    return rebuild()


def reconcile_from_exchange() -> dict:
    """LIVE: the exchange IS the truth. Read the real balance, value it, and set
    treasury to match. PAPER: a no-op (keeps the hand-set book)."""
    try:
        import execute
        if execute._mode() != "LIVE":
            return {"reconciled": False, "reason": "not in LIVE mode"}
        bal = execute.kraken_balance()
    except Exception as e:
        return {"reconciled": False, "reason": f"balance unavailable: {e}"}
    quote_assets = {"USD", "USDT", "USDC", "EUR", "CAD", "GBP", "ZUSD"}
    cash = 0.0
    holdings = {}
    for asset, amt in bal.items():
        if amt <= 0:
            continue
        if asset in quote_assets or asset.endswith("USD"):
            cash += float(amt)
            continue
        price = execute.kraken_last_price(f"{asset}/USD") or 0.0
        val = float(amt) * price
        if val > 0.5:                     # ignore dust
            holdings[asset] = round(val, 2)
    total = round(cash + sum(holdings.values()), 2)
    _require_init()
    _append({"type": "reconcile", "cash": round(cash, 2), "holdings": holdings,
             "total": total, "source": "exchange"})
    st = rebuild()
    st["holdings"] = holdings
    return {"reconciled": True, "total": total, "cash": round(cash, 2), "holdings": holdings}


def record_realized(amount: float, reason: str = None) -> dict:
    _require_init()
    _append({"type": "realized", "amount": float(amount), "reason": reason})
    return rebuild()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("init"); i.add_argument("--currency", default="USD")
    a = sub.add_parser("allocate"); a.add_argument("amount", type=float); a.add_argument("--note")
    d = sub.add_parser("debit"); d.add_argument("amount", type=float); d.add_argument("--reason")
    c = sub.add_parser("credit"); c.add_argument("amount", type=float); c.add_argument("--reason")
    r = sub.add_parser("set-reserve"); r.add_argument("amount", type=float)
    o = sub.add_parser("set-open-risk"); o.add_argument("amount", type=float)
    sub.add_parser("status"); sub.add_parser("rebuild")
    l = sub.add_parser("ledger"); l.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    cmd = args.cmd or "status"
    if cmd == "init":
        if not STATE.exists():
            _append({"type": "init", "currency": args.currency})
        print(json.dumps(rebuild(), indent=2))
    elif cmd == "allocate": print(json.dumps(allocate(args.amount, args.note), indent=2))
    elif cmd == "debit": print(json.dumps(debit(args.amount, args.reason), indent=2))
    elif cmd == "credit": print(json.dumps(credit(args.amount, args.reason), indent=2))
    elif cmd == "set-reserve": print(json.dumps(set_reserve(args.amount), indent=2))
    elif cmd == "set-open-risk": print(json.dumps(set_open_risk(args.amount), indent=2))
    elif cmd == "rebuild": print(json.dumps(rebuild(), indent=2))
    elif cmd == "ledger":
        for e in _events()[-args.limit:]:
            print(json.dumps(e))
    else: print(json.dumps(status(), indent=2))
