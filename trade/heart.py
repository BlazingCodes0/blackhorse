"""heart.py — the referee. ALLOW / BLOCK / REVIEW for every proposed action.

Authority is separate from intelligence: Heart is its own process/module. The
brain and agents can PROPOSE, but they cannot disable Heart, raise its caps, or
stop the dead-man switch. Nothing reaches the outside world without a stamp.

Enforces:
  * hard bans (withdrawals, moving funds, unknown venues)
  * caps (max position, open positions, daily loss, drawdown, order rate/value)
  * dead-man switch (no fresh heartbeat -> PARK / fail-closed)
  * autonomy unlock (credentials + heart + backtest + paper all required)

Writes every verdict to the hash-chained incidents log via the memory index, and
maintains a heartbeat file the money loop must refresh.

CLI:
  python3 trade/heart.py check --action BUY --quote 10 --venue kraken --positions 0 --daily-loss-pct 0
  python3 trade/heart.py heartbeat
  python3 trade/heart.py status
  python3 trade/heart.py unlock          # can autonomy arm?
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))
RULES = BH / "heart" / "policies" / "rules.json"
HEARTBEAT = BH / "logs" / "heartbeat.json"
ORDERS = BH / "logs" / "heart_orders.json"     # rolling order timestamps (rate cap)


def _rules() -> dict:
    try:
        return json.loads(RULES.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def heartbeat():
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({"ts": time.time(),
                                     "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    return {"ok": True, "ts": time.time()}


def _deadman_ok(rules: dict) -> tuple[bool, str]:
    dm = rules.get("deadman", {})
    max_age = float(dm.get("heartbeat_max_age_seconds", 90))
    hb = _load(HEARTBEAT, None)
    if not hb:
        return False, "no heartbeat recorded (fail-closed: PARK)"
    age = time.time() - float(hb.get("ts", 0))
    if age > max_age:
        return False, f"heartbeat stale ({int(age)}s > {max_age}s) -> PARK"
    return True, "ok"


def _orders_last_hour() -> int:
    now = time.time()
    times = [t for t in _load(ORDERS, []) if now - t < 3600]
    ORDERS.write_text(json.dumps(times))
    return len(times)


def _record_order():
    times = _load(ORDERS, [])
    times.append(time.time())
    ORDERS.write_text(json.dumps(times[-200:]))


def check(action: str, quote: float = 0, venue: str = None, positions: int = 0,
          daily_loss_pct: float = 0, drawdown_pct: float = 0, leverage: float = 1.0,
          margin: bool = False, borrow: bool = False, holding_qty: float = 0,
          manual: bool = False, record: bool = False) -> dict:
    rules = _rules()
    bans = rules.get("hard_bans", {})
    caps = rules.get("caps", {})
    spot = rules.get("spot_only", {})
    reasons, level = [], "L0_observe"

    # hard bans
    if action in ("WITHDRAW", "TRANSFER") and bans.get("withdrawals", True):
        return _verdict("BLOCK", f"hard ban: {action} is never autonomous", ["hard ban"], "L3_lockdown", rules)
    if venue and venue not in ("kraken", "coinbase", "binance") and bans.get("unknown_venue", True):
        return _verdict("BLOCK", f"unknown venue '{venue}'", ["venue whitelist"], "L2_halt", rules)

    # SPOT-ONLY / no interest / no margin / no leverage — absolute
    if spot.get("enforced", True):
        if margin:
            return _verdict("BLOCK", "spot-only: margin is banned", ["no margin"], "L3_lockdown", rules)
        if borrow:
            return _verdict("BLOCK", "spot-only: borrowing is banned", ["no borrowing"], "L3_lockdown", rules)
        if leverage and float(leverage) > float(spot.get("max_leverage", 1.0)) + 1e-9:
            return _verdict("BLOCK", f"spot-only: leverage {leverage}x > max {spot.get('max_leverage',1.0)}x",
                            ["no leverage"], "L3_lockdown", rules)
        if action in ("SHORT", "SHORT_SELL", "FUTURES", "PERP", "SWAP", "BORROW", "INTEREST", "LOAN"):
            return _verdict("BLOCK", f"spot-only: {action} is banned", ["no derivatives/short/borrow"],
                            "L3_lockdown", rules)
        # a SELL must be a spot sell of INVENTORY you already own (no naked short)
        if action == "SELL" and spot.get("allow_short") is False and float(holding_qty) <= 0:
            return _verdict("BLOCK", "spot-only: SELL without held inventory = short (banned)",
                            ["no naked short"], "L3_lockdown", rules)

    # dead-man (fail-closed) — protects AUTONOMOUS runs, not the owner's direct
    # manual orders (manual=True skips it, but spot-only + caps still apply).
    if action in ("BUY", "SELL") and not manual:
        ok, why = _deadman_ok(rules)
        if not ok:
            return _verdict("BLOCK", why, [why], "L2_halt", rules)

    # caps
    if action in ("BUY", "SELL"):
        if quote > caps.get("max_order_quote", 1e9):
            reasons.append(f"order quote {quote} > max {caps['max_order_quote']}"); level = "L1_soft"
        if positions >= caps.get("max_open_positions", 1e9):
            reasons.append(f"open positions {positions} >= max {caps['max_open_positions']}"); level = "L1_soft"
        if daily_loss_pct >= caps.get("max_daily_loss_pct", 1e9):
            reasons.append(f"daily loss {daily_loss_pct}% >= max {caps['max_daily_loss_pct']}%"); level = "L2_halt"
        if drawdown_pct >= caps.get("max_drawdown_pct", 1e9):
            reasons.append(f"drawdown {drawdown_pct}% >= max {caps['max_drawdown_pct']}%"); level = "L3_lockdown"
        if _orders_last_hour() >= caps.get("max_orders_per_hour", 1e9):
            reasons.append("order rate cap reached"); level = "L1_soft"

    if any("drawdown" in r for r in reasons):
        decision = "BLOCK"
    elif reasons:
        decision = "BLOCK"
    else:
        decision = "ALLOW"

    if decision == "ALLOW" and action in ("BUY", "SELL") and record:
        _record_order()
    v = _verdict(decision, "; ".join(reasons) or "ok", reasons, level, rules)
    try:
        import store
        store.record_heart(action, quote, venue, decision, level, v["reason"], detail=reasons)
    except Exception:
        pass
    return v


def _verdict(decision, reason, reasons, level, rules) -> dict:
    v = {"decision": decision, "reason": reason, "reasons": reasons, "level": level,
         "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        import index
        index.remember(kind="decision", text=f"HEART {decision}: {reason}", meta=v, source="heart")
    except Exception:
        pass
    return v


def _backtest_truth() -> dict:
    """A backtest must be REAL and verifiable. We check the flag's sha256 against
    the actual report files; a hand-written flag with no matching hash does NOT
    count. Accepts either our validator's report or the imported real-report edge."""
    import hashlib
    flag = BH / "logs" / "backtest_passed.flag"
    if not flag.exists():
        return {"passed": False, "why": "no backtest flag"}
    try:
        flag_text = flag.read_text()
    except OSError:
        return {"passed": False, "why": "flag unreadable"}
    # which report does the flag claim?
    candidates = [BH / "logs" / "edge_report.json",
                  BH / "logs" / "backtest_report.json"]
    for rep_path in candidates:
        if not rep_path.exists():
            continue
        digest = hashlib.sha256(rep_path.read_bytes()).hexdigest()[:16]
        if digest in flag_text:
            try:
                rep = json.loads(rep_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            proven = (rep.get("verdict", {}).get("positive_edge_cells")
                      or rep.get("proven") or [])
            if proven and (rep.get("pass") or rep.get("verdict") or rep.get("proven")):
                return {"passed": True, "why": f"verified against {rep_path.name}", "report": rep_path.name}
            return {"passed": False, "why": f"{rep_path.name} reports no proven edge"}
    return {"passed": False, "why": "flag hash does not match any report (hand-written?)"}


def _paper_truth() -> dict:
    """A real paper period: at least N closed paper trades in the store, spanning
    at least M hours. A bare flag file does not count."""
    import time
    flag = BH / "logs" / "paper_period.flag"
    if not flag.exists():
        return {"passed": False, "why": "no paper flag"}
    n_min, hours_min = 10, 6
    try:
        import store
        con = store.connect()
        rows = con.execute("SELECT COUNT(*) c, MIN(opened) mn, MAX(closed) mx "
                           "FROM positions WHERE status='CLOSED'").fetchone()
        n = rows["c"] or 0
        span_h = ((rows["mx"] or 0) - (rows["mn"] or 0)) / 3600 if rows["mn"] else 0
        return {"passed": n >= n_min and span_h >= hours_min,
                "why": f"{n} closed paper trades over {round(span_h,1)}h (need {n_min}/{hours_min}h)"}
    except Exception as e:
        return {"passed": False, "why": f"store unavailable: {e}"}


_UNLOCK_CACHE = {"ts": 0.0, "val": None}

def unlock() -> dict:
    """Can autonomous trading arm? Gates must be REAL, not hand-written flags."""
    import time as _t
    if _UNLOCK_CACHE["val"] is not None and _t.time() - _UNLOCK_CACHE["ts"] < 30:
        return _UNLOCK_CACHE["val"]
    rules = _rules().get("autonomy_unlock", {})
    checks = {}
    try:
        import exchanges
        name = exchanges.active_exchange()["name"]
        checks["credentials"] = exchanges.credential_status(name)["ready"]
    except Exception:
        checks["credentials"] = False
    checks["heart"] = True
    bt = _backtest_truth()
    pp = _paper_truth()
    checks["backtest_passed"] = bt["passed"]
    checks["paper_period"] = pp["passed"]
    armed = all(checks.values())
    out = {"can_arm": armed, "checks": checks,
           "detail": {"backtest": bt["why"], "paper_period": pp["why"]},
           "missing": [k for k, v in checks.items() if not v]}
    _UNLOCK_CACHE.update(ts=_t.time(), val=out)
    return out


def status() -> dict:
    rules = _rules()
    ok, why = _deadman_ok(rules)
    return {"heartbeat_ok": ok, "heartbeat_note": why,
            "orders_last_hour": _orders_last_hour(),
            "caps": rules.get("caps", {}), "unlock": unlock()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--action", required=True); c.add_argument("--quote", type=float, default=0)
    c.add_argument("--venue"); c.add_argument("--positions", type=int, default=0)
    c.add_argument("--daily-loss-pct", type=float, default=0); c.add_argument("--drawdown-pct", type=float, default=0)
    c.add_argument("--record", action="store_true")
    sub.add_parser("heartbeat"); sub.add_parser("status"); sub.add_parser("unlock")
    args = ap.parse_args()
    cmd = args.cmd or "status"
    if cmd == "check":
        print(json.dumps(check(args.action, args.quote, args.venue, args.positions,
                               args.daily_loss_pct, args.drawdown_pct, args.record), indent=2))
    elif cmd == "heartbeat": print(json.dumps(heartbeat(), indent=2))
    elif cmd == "unlock": print(json.dumps(unlock(), indent=2))
    else: print(json.dumps(status(), indent=2))
