"""advisor_combo.py — blend adaBot advisor prices across [60,240,1440] into one
buy/sell reference per pair. Missing/zero prices are excluded, never averaged.

CLI: python3 trade/advisor_combo.py [path_to_signal_latest.json]
Output: logs/advisor_combo.json + stdout summary.
"""
import json, sys
from pathlib import Path

BH = Path.home() / "blackhorse"
IN = Path(sys.argv[1]) if len(sys.argv) > 1 else BH / "logs" / "signal_latest.json"
OUT = BH / "logs" / "advisor_combo.json"
TFS = ["ripple", "wave", "tide"]  # 60m, 240m, 1440m

def blend(vals):
    vals = [v for v in vals if isinstance(v, (int, float)) and v > 0]
    return sum(vals) / len(vals) if vals else None, len(vals)

def main():
    d = json.loads(IN.read_text())
    chans = d.get("channels", {})
    buys, sells, per_tf = [], [], {}
    for name in TFS:
        ch = chans.get(name)
        if not ch: continue
        a = ch.get("advisor", {})
        per_tf[ch.get("interval")] = {"buy": a.get("buy"), "sell": a.get("sell"),
                                      "buy_missing": a.get("buy_missing"),
                                      "sell_missing": a.get("sell_missing")}
        if not a.get("buy_missing") and a.get("buy"): buys.append(a["buy"])
        if not a.get("sell_missing") and a.get("sell"): sells.append(a["sell"])
    buy, n_buy = blend(buys)
    sell, n_sell = blend(sells)
    avg_buys, avg_sells = [], []
    for name in TFS:
        ch = chans.get(name)
        if not ch: continue
        a = ch.get("advisor", {}).get("prices", {}).get("average", {})
        if a.get("buy") and a["buy"] > 0: avg_buys.append(a["buy"])
        if a.get("sell") and a["sell"] > 0: avg_sells.append(a["sell"])
    ab, nab = blend(avg_buys)
    as_, nas = blend(avg_sells)
    out = {"pair": d.get("pair"), "ts": d.get("ts"),
           "buy": round(buy, 6) if buy else None, "n_buy": n_buy,
           "sell": round(sell, 6) if sell else None, "n_sell": n_sell,
           "buy_tf_avg": round(ab, 6) if ab else None, "n_buy_avg": nab,
           "sell_tf_avg": round(as_, 6) if as_ else None, "n_sell_avg": nas,
           "per_tf": per_tf,
           "currPrice": (chans.get("ripple") or {}).get("currPrice")}
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
