"""edge_rules.py — does layering RULES on the SignalBot history beat the raw signal?

Reads logs/sb_rows.json (SignalBot's own logged rows: rangeSignal, trend,
maSpreadPct, strength, support, forward returns) and measures the NET-of-fee
edge of several rule combinations, split train/test. No look-ahead: rules use
only fields present on the same row as the signal.

CLI: python3 trade/edge_rules.py [horizon]
"""
import json, sys
from pathlib import Path

BH = Path.home() / "blackhorse"
ROWS = BH / "logs" / "sb_rows.json"
CRIT = BH / "config" / "edge_criteria.json"
SLIP = 0.1


def _fee_round_trip() -> float:
    """Real round-trip cost: 2 sides of the exchange fee + slippage. Single source:
    config/edge_criteria.json (kept in step with the exchange_watch snapshot)."""
    try:
        per_side = float(json.loads(CRIT.read_text()).get("fee_pct_per_side", 0.80))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        per_side = 0.80
    return per_side * 2 + SLIP

RULES = {
    "raw BUY signal": lambda r: r.get("rangeSignal") == "BUY",
    "raw SELL signal": lambda r: r.get("rangeSignal") == "SELL",
    "BUY + rising": lambda r: r.get("rangeSignal") == "BUY" and r.get("trend") == "rising",
    "BUY + falling": lambda r: r.get("rangeSignal") == "BUY" and r.get("trend") == "falling",
    "SELL + falling": lambda r: r.get("rangeSignal") == "SELL" and r.get("trend") == "falling",
    "SELL + rising": lambda r: r.get("rangeSignal") == "SELL" and r.get("trend") == "rising",
    "BUY + maSpread>0": lambda r: r.get("rangeSignal") == "BUY" and (r.get("maSpreadPct") or 0) > 0,
    "BUY + maSpread<0": lambda r: r.get("rangeSignal") == "BUY" and (r.get("maSpreadPct") or 0) < 0,
    "SELL + maSpread<0": lambda r: r.get("rangeSignal") == "SELL" and (r.get("maSpreadPct") or 0) < 0,
    "BUY + trend up + maSpread>0": lambda r: r.get("rangeSignal") == "BUY" and r.get("trend") == "rising" and (r.get("maSpreadPct") or 0) > 0,
    "SELL + trend down + maSpread<0": lambda r: r.get("rangeSignal") == "SELL" and r.get("trend") == "falling" and (r.get("maSpreadPct") or 0) < 0,
    "BUY + supportRecent": lambda r: r.get("rangeSignal") == "BUY" and r.get("supportRecent"),
}

def score(rows, horizon):
    fee = _fee_round_trip()
    out = {}
    half = len(rows) // 2
    for name, f in RULES.items():
        for tag, seg in (("train", rows[:half]), ("test", rows[half:])):
            sel = [r for r in seg if f(r) and r.get(f"return{horizon}") is not None]
            if not sel:
                continue
            sign = 1 if "BUY" in name or "raw BUY" in name else -1
            if name.startswith("SELL") or name == "raw SELL signal":
                sign = -1
            elif name.startswith("BUY") or name == "raw BUY signal":
                sign = 1
            nets = [r[f"return{horizon}"] * sign - fee for r in sel]
            wins = sum(1 for x in nets if x > 0)
            out.setdefault(name, {})[tag] = {
                "n": len(nets), "win_pct": round(100 * wins / len(nets), 1),
                "avg_net_pct": round(sum(nets) / len(nets), 3)}
        if name in out:
            t = out[name].get("train", {}).get("avg_net_pct")
            s = out[name].get("test", {}).get("avg_net_pct")
            n_t = out[name].get("test", {}).get("n", 0)
            ok = t is not None and s is not None and t > 0 and s > 0 and n_t >= 10
            out[name]["VERDICT"] = "PROVEN-BOTH" if ok else ("edge-decays" if (t and t > 0 and s is not None and s <= 0) else "no-edge")
    return out

if __name__ == "__main__":
    horizon = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    data = json.loads(ROWS.read_text())
    for iv, rows in data.items():
        res = score(rows, horizon)
        print(f"\n=== interval {iv} (horizon return{horizon}, fee {_fee_round_trip():.2f}% round trip) ===")
        for name, r in res.items():
            print(f"  {name:38s} {r.get('train')} {r.get('test')} -> {r.get('VERDICT')}")
