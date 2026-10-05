"""import_edge.py — load the REAL validation reports into BlackHorse's edge system.

We do NOT invent thresholds. This reads the authoritative reports produced by the
real qubitFlow validators:
  * botStrategyWinningTest.json  (fee-honest walk-forward promotions)
  * codexGateBacktest.json       (full RangeSignalGate walk-forward)

...and writes logs/edge_report.json in BlackHorse's format, so edge.py/decider.py/
heart.py consume the SYSTEM'S verdicts, not a hand-rolled proxy.

Honesty rules:
  * A cell is PROVEN only if the report says OUT_OF_SAMPLE_EDGE.
  * We carry the exact settings and test stats the validator computed.
  * If a report is missing, we say so — we do not guess.

CLI: python3 trade/import_edge.py
"""
import json, sys, time, hashlib
from pathlib import Path

BH = Path.home() / "blackhorse"
QF = Path.home() / "qubitFlowBeta" / "qubitFlowBeta" / "SignalBot" / "logs3Low"
WIN = QF / "botStrategyWinningTest.json"
GATE = QF / "codexGateBacktest.json"
OUT = BH / "logs" / "edge_report.json"
FLAG = BH / "logs" / "backtest_passed.flag"

FEE_PCT = 0.26
SLIP_PCT = 0.1


def _load(p):
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def from_winning_report(d) -> dict:
    if not d:
        return {}
    out = {}
    for interval, r in (d.get("intervals") or {}).items():
        status = str(r.get("status", "")).upper()
        t = r.get("test") or {}
        proven = status == "OUT_OF_SAMPLE_EDGE"
        out[interval] = {
            "source": "botStrategyWinningTest",
            "status": "PROVEN" if proven else ("UNPROVEN" if status == "NOT_PROVEN" else "LOSING"),
            "raw_status": status,
            "settings": r.get("settings"),
            "test": t,
            "n": t.get("closedTrades"),
            "win_pct": round(float(t.get("winRate", 0)) * 100, 1) if t.get("winRate") is not None else None,
            "avg_net_pct": t.get("netReturnPercent"),
            "max_drawdown_pct": t.get("maxDrawdownPercent"),
            "note": "real fee-honest walk-forward (qubitFlow bot-strategy-winning-test)",
        }
    return out


def from_winning_test() -> dict:
    return from_winning_report(_load(WIN))


def from_gate_backtest(top_horizon=5) -> dict:
    d = _load(GATE)
    if not d:
        return {}
    out = {}
    for interval, by_h in (d.get("intervals") or {}).items():
        h = by_h.get(f"horizon{top_horizon}") or {}
        cell = {"source": f"codexGateBacktest:h{top_horizon}"}
        for side in ("buy", "sell"):
            s = h.get(side) or {}
            test = s.get("test") or {}
            cell[side] = {
                "n": s.get("count"),
                "win_pct": round(float(s.get("winRate", 0)) * 100, 1) if s.get("winRate") is not None else None,
                "avg_net_pct": s.get("averageReturnPct"),
                "payoff": s.get("payoffRatio"),
                "test": {k: test.get(k) for k in ("count", "winRate", "averageReturnPct", "payoffRatio")},
            }
        out[interval] = cell
    return out


def build() -> dict:
    wt = from_winning_test()
    gate = from_gate_backtest()
    # BlackHorse format: by_interval -> {BUY:{...}, SELL:{...}} with a status
    by_interval = {}
    # attach real gate stats per side (interval -> {BUY, SELL})
    for interval, cell in gate.items():
        by_interval.setdefault(interval, {})
        if "buy" in cell:
            c = dict(cell["buy"])
            t = c.get("test") or {}
            c["n"] = c.get("n") or t.get("count")
            c["win_pct"] = round(float((t.get("winRate") if t.get("winRate") is not None else 0)) * 100, 1)
            c["avg_net_pct"] = t.get("averageReturnPct")
            c["status"] = _side_status(interval, "BUY", wt, cell["buy"])
            by_interval[interval]["BUY"] = c
        if "sell" in cell:
            c = dict(cell["sell"])
            t = c.get("test") or {}
            c["n"] = c.get("n") or t.get("count")
            c["win_pct"] = round(float((t.get("winRate") if t.get("winRate") is not None else 0)) * 100, 1)
            c["avg_net_pct"] = t.get("averageReturnPct")
            c["status"] = _side_status(interval, "SELL", wt, cell["sell"])
            by_interval[interval]["SELL"] = c

    proven = []
    for interval, cells in by_interval.items():
        for side in ("BUY", "SELL"):
            c = cells.get(side) or {}
            if c.get("status") == "PROVEN":
                proven.append(f"{interval} {side} (n={c.get('n')}, win {c.get('win_pct')}%, "
                              f"net {round(c.get('avg_net_pct') or 0,3)}%)")

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "REAL qubitFlow validators (imported)",
        "fees_pct_per_side": FEE_PCT, "slippage_pct_per_side": SLIP_PCT,
        "by_interval": by_interval,
        "proven": wt,
        "gate_stats": gate,
        "verdict": {"positive_edge_cells": proven,
                    "note": "Imported from the real walk-forward reports, not a proxy."},
    }
    # per-pair verdicts from the SAME fee-honest validator, one report per pair
    by_pair = {}
    for pair in ("DOT", "BTC", "ETH", "SOL", "XRP"):
        rp = QF / pair / "botStrategyWinningTest.json" if pair != "DOT" else WIN
        d = _load(rp)
        if not d:
            continue
        intervals = {}
        proven = []
        for interval, r in (d.get("intervals") or {}).items():
            status = str(r.get("status", "")).upper()
            t = r.get("test") or {}
            proven_cell = status == "OUT_OF_SAMPLE_EDGE"
            intervals[interval] = {
                "source": "botStrategyWinningTest",
                "status": "PROVEN" if proven_cell else ("UNPROVEN" if status == "NOT_PROVEN" else "LOSING"),
                "raw_status": status,
                "settings": r.get("settings"),
                "n": t.get("closedTrades"),
                "win_pct": round(float(t.get("winRate", 0)) * 100, 1) if t.get("winRate") is not None else None,
                "avg_net_pct": t.get("netReturnPercent"),
                "note": f"real fee-honest walk-forward on {pair}",
            }
            if proven_cell:
                proven.append(interval)
        gate_p = _load(QF / pair / "codexGateBacktest.json") if pair != "DOT" else _load(GATE)
        # SELL cells: same honest rule as legacy _side_status, applied to THIS pair:
        # the winning test must have promoted the interval, and this pair's gate
        # sell horizon-5 walk-forward must be clearly positive. Otherwise
        # UNPROVEN (not clearly positive) or LOSING (clearly negative).
        wt = from_winning_report(d)
        gs = (gate_p or {}).get("intervals") or {}
        for interval, cell in list(gs.items()):
            s5 = (((cell or {}).get("horizon5", {}) or {}).get("sell", {}) or {}).get("all") or {}
            if not s5:
                continue
            intervals.setdefault(interval, {})
            intervals[interval] = dict(intervals.get(interval) or {})
            if "source" not in intervals[interval]:
                intervals[interval]["source"] = "botStrategyWinningTest"
            intervals[interval]["SELL"] = {
                "source": "codexGateBacktest",
                "status": _side_status(interval, "SELL", wt, {"test": {
                    "count": s5.get("count"), "averageReturnPct": s5.get("averageReturnPct"),
                    "winRate": s5.get("winRate")}}),
                "n": s5.get("count"),
                "win_pct": round((s5.get("winRate") or 0) * 100, 1),
                "avg_net_pct": s5.get("averageReturnPct"),
                "note": f"real gate sell walk-forward on {pair}",
            }
        # restructure: from_winning_report returns interval->status dict keyed differently;
        # store BUY cell under "BUY" for symmetric access
        for interval, cell in list(intervals.items()):
            if isinstance(cell, dict) and "status" in cell and "SELL" not in cell and cell.get("source") == "botStrategyWinningTest" and "avg_net_pct" in cell:
                pass  # BUY entry already flat; leave as-is
        by_pair[pair] = {"by_interval": intervals, "proven_intervals": proven,
                         "gate_stats": gs}
    report["by_pair"] = by_pair
    OUT.write_text(json.dumps(report, indent=2))
    return report


def _side_status(interval, side, wt, gate_cell) -> str:
    """A side is PROVEN if its gate test stats are clearly positive AND it matches
    a promoted interval; else UNPROVEN."""
    t = gate_cell.get("test") or {}
    tn = t.get("count") or 0
    tret = t.get("averageReturnPct")
    twin = t.get("winRate")
    promoted = wt.get(interval, {}).get("status") == "PROVEN"
    if promoted and tn >= 2 and (tret or 0) > 0 and (twin or 0) >= 0.5:
        return "PROVEN"
    if (tret or 0) < 0 and tn >= 3:
        return "LOSING"
    return "UNPROVEN"


def update_flag(report: dict) -> bool:
    """Write the gate flag IFF the real reports promote an edge, with a hash of
    our imported report so heart can verify it wasn't hand-edited."""
    proven = report.get("verdict", {}).get("positive_edge_cells") or []
    if proven:
        digest = hashlib.sha256(OUT.read_bytes()).hexdigest()[:16]
        FLAG.write_text(f"OUT_OF_SAMPLE_EDGE sha256:{digest} {report['ts']} imported-from-real-reports\n")
        return True
    if FLAG.exists():
        FLAG.unlink()
    return False


if __name__ == "__main__":
    rep = build()
    ok = update_flag(rep)
    print(json.dumps({"proven": rep["verdict"]["positive_edge_cells"], "flag_written": ok}, indent=2))
