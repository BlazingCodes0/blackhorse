"""edge.py — the house's measured edge, consulted before every trade.

This is the mechanism that turns the one-time finding into a STANDING discipline:
agents and the brain must reason evidence-first, net of fees, and act only where
the edge is real. Every decision cites the measured cell it relies on.

It reads logs/sb_rows.json (SignalBot's own saved rows) and scores each
(interval, side) as PROVEN / UNPROVEN / LOSING under explicit criteria:
    proven  : avg net > 0 AND win% >= min_win AND n >= min_n
    losing  : avg net < 0
    unproven: everything else (not enough data / not clearly positive)

Criteria live in config/edge_criteria.json (data, not code).

CLI:
  python3 trade/edge.py report         # the edge table + verdicts
  python3 trade/edge.py cell 1440 SELL # one cell
  python3 trade/edge.py refresh        # recompute from SB rows
"""
import json, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
ROWS = BH / "logs" / "sb_rows.json"
REPORT = BH / "logs" / "edge_report.json"
CRIT = BH / "config" / "edge_criteria.json"

DEFAULT_CRIT = {"min_win_pct": 55.0, "min_n": 30, "min_avg_net_pct": 0.0,
                "fee_pct_per_side": 0.26, "slip_pct_per_side": 0.1, "horizon": 5}


def criteria() -> dict:
    try:
        return {**DEFAULT_CRIT, **json.loads(CRIT.read_text())}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CRIT)


def refresh() -> dict:
    import edge_scan
    if not ROWS.exists():
        edge_scan.dump_rows()
    rows = json.loads(ROWS.read_text())
    rep = edge_scan.scan(rows, criteria()["horizon"])
    rep = _tag(rep, criteria())
    REPORT.write_text(json.dumps(rep, indent=2))
    return rep


def _tag(rep: dict, crit: dict) -> dict:
    for iv, cell in rep.get("by_interval", {}).items():
        for side, c in cell.items():
            if not c:
                cell[side] = {"status": "NO_DATA"}; continue
            if c["avg_net_pct"] < 0:
                c["status"] = "LOSING"
            elif (c["avg_net_pct"] > crit["min_avg_net_pct"]
                  and c["win_pct"] >= crit["min_win_pct"] and c["n"] >= crit["min_n"]):
                c["status"] = "PROVEN"
            else:
                c["status"] = "UNPROVEN"
    return rep


_CACHE = {"ts": 0, "rep": None}


def load(max_age=3600) -> dict:
    if _CACHE["rep"] and time.time() - _CACHE["ts"] < max_age:
        return _CACHE["rep"]
    try:
        rep = json.loads(REPORT.read_text())
    except (json.JSONDecodeError, OSError):
        rep = refresh()
    _CACHE.update(ts=time.time(), rep=rep)
    return rep


def cell(interval, side, pair=None) -> dict:
    """Measured edge for a timeframe+side. interval like '1440m' or 1440.
    If `pair` is given and we have a per-pair verdict, that wins; otherwise
    fall back to the (DOT-era) global by_interval table."""
    rep = load()
    key = str(interval)
    if key.isdigit():
        key = f"{key}m"
    if pair:
        short = str(pair).split("/")[0].upper()
        by_pair = rep.get("by_pair", {}) or {}
        if short in by_pair:
            cell_obj = (by_pair[short].get("by_interval", {}) or {}).get(key, {})
            if isinstance(cell_obj, dict):
                if side == "BUY":
                    buy = cell_obj.get("BUY")
                    if buy and buy.get("status"):
                        return buy
                    # if top-level was written as the BUY dict
                    if cell_obj.get("status") and "SELL" not in cell_obj:
                        return cell_obj
                    return {"status": "NO_DATA"}
                sell = cell_obj.get("SELL")
                return sell if sell else {"status": "NO_DATA"}
            return {"status": "NO_DATA"}
    c = (rep.get("by_interval", {}).get(key, {}) or {}).get(side)
    return c or {"status": "NO_DATA"}


def is_proven(interval, side, pair=None) -> bool:
    return cell(interval, side, pair).get("status") == "PROVEN"


def proven_cells(pair=None) -> list:
    rep = load()
    if pair:
        short = str(pair).split("/")[0].upper()
        intervals = ((rep.get("by_pair", {}) or {}).get(short, {}) or {}).get("by_interval", {})
        return [{"interval": iv, "side": "BUY", **c} for iv, c in intervals.items()
                if c.get("status") == "PROVEN"]
    out = []
    for iv, cc in rep.get("by_interval", {}).items():
        for side, c in cc.items():
            if c and c.get("status") == "PROVEN":
                out.append({"interval": iv, "side": side, **c})
    return out


def brief() -> str:
    """One-line summary the brain can cite."""
    pc = proven_cells()
    if not pc:
        return "no interval/side has a proven post-fee edge yet"
    return "proven edge: " + "; ".join(f"{c['interval']} {c['side']} "
                                       f"({c['win_pct']}% win, {c['avg_net_pct']}% net, n={c['n']})"
                                       for c in pc)


def table() -> list:
    rep = load()
    rows = []
    for iv, cc in rep.get("by_interval", {}).items():
        for side in ("BUY", "SELL"):
            c = cc.get(side) or {}
            rows.append({"interval": iv, "side": side, "status": c.get("status", "NO_DATA"),
                         "n": c.get("n"), "win_pct": c.get("win_pct"),
                         "avg_net_pct": c.get("avg_net_pct")})
    return rows


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "refresh":
        print(json.dumps(refresh(), indent=2))
    elif cmd == "cell":
        print(json.dumps(cell(sys.argv[2], sys.argv[3]), indent=2))
    else:
        for r in table():
            print(f"{r['interval']:>7} {r['side']:>5} {r['status']:>9} "
                  f"n={r['n']} win={r['win_pct']} net={r['avg_net_pct']}")
        print("\n" + brief())
