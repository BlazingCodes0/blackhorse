"""edge_scan.py — where does the REAL SignalBot signal actually have an edge?

Reads SignalBot's own saved rows (via qubitFlow's loader) and measures the
forward return of its real `rangeSignal` (BUY/SELL), per interval, NET of
round-trip fees + slippage. This is not a caricature: it uses the signal's actual
decisions on its actual logged market state.

Data: qubitFlowBeta/SignalBot/logs3Low/*.json (saveRange* + saveSmart*).
Loader: SignalBot/utils/analyzeLogs3LowCodex.js (run through node).

CLI:
  python3 trade/edge_scan.py --pair DOT/USD --horizon 5
  python3 trade/edge_scan.py --dump          # regenerate the rows json
"""
import json, subprocess, sys, argparse, shutil
from pathlib import Path

BH = Path.home() / "blackhorse"
QF = Path.home() / "qubitFlowBeta" / "qubitFlowBeta"
ROWS = BH / "logs" / "sb_rows.json"
REPORT = BH / "logs" / "edge_report.json"
CRIT = BH / "config" / "edge_criteria.json"
FEE_PCT = 0.80          # real Kraken base taker; overridden by edge_criteria.json
SLIP_PCT = 0.1


def _fee_pct() -> float:
    """Same fee the decider/exchange will actually charge. Single source:
    config/edge_criteria.json (kept in step with the exchange_watch snapshot)."""
    try:
        return float(json.loads(CRIT.read_text()).get("fee_pct_per_side", FEE_PCT))
    except (json.JSONDecodeError, OSError):
        return FEE_PCT


def dump_rows() -> dict:
    loader = QF / "SignalBot" / "utils" / "analyzeLogs3LowCodex.js"
    node = shutil.which("node") or "node"
    js = (
        "const {SOURCES, loadRows} = require(%r);\n"
        "const fs=require('fs'); const out={};\n"
        "for(const s of SOURCES){ try{ out[s.interval]=loadRows(s);}catch(e){ out[s.interval]=[]; } }\n"
        "fs.writeFileSync(%r, JSON.stringify(out));\n"
        "process.stdout.write(JSON.stringify(Object.keys(out).map(k=>k+':'+out[k].length)));\n"
    ) % (str(loader), str(ROWS))
    subprocess.run([node, "-e", js], cwd=str(QF), capture_output=True, text=True, timeout=60)
    return json.loads(ROWS.read_text())


def scan(rows: dict, horizon=5) -> dict:
    fee = _fee_pct() * 2 + SLIP_PCT
    out = {}
    for iv, rs in rows.items():
        cell = {}
        for signal in ("BUY", "SELL"):
            sel = [r for r in rs if r.get("rangeSignal") == signal
                   and r.get(f"return{horizon}") is not None]
            if not sel:
                cell[signal] = None; continue
            sign = 1 if signal == "BUY" else -1
            net = [r[f"return{horizon}"] * sign - fee for r in sel]
            wins = sum(1 for x in net if x > 0)
            cell[signal] = {"n": len(net), "win_pct": round(100 * wins / len(net), 1),
                            "avg_net_pct": round(sum(net) / len(net), 3),
                            "best_pct": round(max(net), 2), "worst_pct": round(min(net), 2)}
        out[iv] = cell
    return {"horizon": horizon, "fee_round_trip_pct": fee, "by_interval": out,
            "verdict": _verdict(out)}


def _verdict(by_interval: dict) -> dict:
    """Which intervals show a positive post-fee edge?"""
    positive = []
    for iv, cell in by_interval.items():
        for side in ("BUY", "SELL"):
            c = cell.get(side)
            if c and c["avg_net_pct"] > 0 and c["win_pct"] >= 55 and c["n"] >= 30:
                positive.append(f"{iv} {side} (n={c['n']}, win {c['win_pct']}%, {c['avg_net_pct']}%)")
    return {"positive_edge_cells": positive,
            "note": "cells = interval+side with avg net>0, win>=55%, n>=30. "
                    "Everything else is fee-eaten or unproven."}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    if args.dump or not ROWS.exists():
        dump_rows()
    rows = json.loads(ROWS.read_text())
    rep = scan(rows, args.horizon)
    REPORT.write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))
