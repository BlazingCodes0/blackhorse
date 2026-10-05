"""validate_playbook.py — fee-honest walk-forward validation of the two
candidate playbook cells (from docs/PLAYBOOK_MTF.md):

  CELL A: 240m  BUY when strength <= 0.25 (floor/lower) + RSI(14) in [50, 70)
  CELL B: 60m   BUY when strength <= 0.25 (floor/lower) + RSI(14) in [50, 70)

Method:
  * join SignalBot's real logged rows (logs/sb_rows.json) with RSI(14)
    computed from the matching Kraken candles (logs/candles_*.json)
  * TRAIN = first half of joined rows, TEST = second half (no look-ahead)
  * P&L per trade = forward return over 20 candles NET of 0.62% round trip
  * a cell PASSES only if BOTH halves are net-positive with n >= 10

logs/backtest_passed.flag is written ONLY if BOTH cells pass.
"""
import json, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
FLAG = BH / "logs" / "backtest_passed.flag"
REPORT = BH / "logs" / "backtest_report.json"
FEE = 0.62
N_MIN = 10
H = 20


def _rsi_series(closes, periods=14):
    rsi, g, l = {}, [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g.append(max(ch, 0)); l.append(max(-ch, 0))
        if i >= periods:
            ag = sum(g[i - periods:i]) / periods
            al = sum(l[i - periods:i]) / periods
            rsi[i] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    return rsi


def load_joined(iv):
    sb = json.loads((BH / "logs" / "sb_rows.json").read_text())[iv]
    c = json.loads((BH / "logs" / f"candles_{iv}.json").read_text())
    closes = [x["close"] for x in c]
    rsi = _rsi_series(closes)
    out = []
    for r in sb:
        k = r.get("key")
        if k in rsi and abs(closes[k] - r["price"]) / r["price"] < 0.05:
            r = dict(r); r["rsi"] = rsi[k]
            out.append(r)
    return out


def run(name, iv, cond):
    rows = load_joined(iv)
    sel = [r for r in rows if cond(r) and r.get(f"return{H}") is not None]
    half = len(sel) // 2
    res = {}
    for tag, seg in (("train", sel[:half]), ("test", sel[half:])):
        if seg:
            nets = [r[f"return{H}"] - FEE for r in seg]
            res[tag] = {"n": len(nets),
                        "win_pct": round(100 * sum(1 for x in nets if x > 0) / len(nets), 1),
                        "avg_net_pct": round(sum(nets) / len(nets), 3)}
    passed = (res.get("train", {}).get("n", 0) >= N_MIN
              and res.get("test", {}).get("n", 0) >= N_MIN
              and res.get("train", {}).get("avg_net_pct", -99) > 0
              and res.get("test", {}).get("avg_net_pct", -99) > 0)
    return {"interval": iv, "train": res.get("train"), "test": res.get("test"), "pass": bool(passed)}


def main():
    out = {"cells": {}, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    pa = run("A_240m_BUY_floor_RSI50-70", "240m",
             lambda r: (r.get("strength") or 9) <= 0.25 and r.get("zone") in ("lower", "floor")
                        and 50.0 <= r["rsi"] < 70.0)
    pb = run("B_60m_BUY_floor_RSI50-70", "60m",
             lambda r: (r.get("strength") or 9) <= 0.25 and r.get("zone") in ("lower", "floor")
                        and 50.0 <= r["rsi"] < 70.0)
    out["cells"] = {"A": pa, "B": pb}
    out["pass"] = bool(pa["pass"] and pb["pass"])
    REPORT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    if out["pass"]:
        FLAG.write_text("pass\n")
        print("backtest_passed.flag written")
    else:
        print("NO PASS — flag not written")


if __name__ == "__main__":
    main()
