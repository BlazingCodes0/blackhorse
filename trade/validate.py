"""validate.py — fee-honest, walk-forward backtest of the decider's range concept.

This is the gate between "well-built" and "trusted with cash". It does NOT
validate the full SignalBot (the advisor VWAP prices need live trades and cannot
be replayed) — it validates the DECIDER'S CORE IDEA (buy low range strength,
sell high range strength, with stops/targets) on real historical candles.

Method:
  * fetch real OHLC from the exchange (paginated; public endpoint)
  * WALK-FORWARD: optimize strength thresholds on a TRAIN window, then measure on
    an untouched TEST window. No look-ahead.
  * honest costs: entry+exit fees and slippage on every trade.
  * report net return, win rate, trades, max drawdown; and a clear PASS/FAIL.

The flag logs/backtest_passed.flag is written ONLY if the untouched out-of-sample
test is profitable after costs with enough trades and bounded drawdown. If the
edge is not there, we say so — we do not fake a pass.

CLI:
  python3 trade/validate.py --pair DOT/USD --interval 60 --limit 1500
  python3 trade/validate.py --report        # show the last report
"""
import json, sys, time, urllib.request, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
REPORT = BH / "logs" / "backtest_report.json"
PASS_FLAG = BH / "logs" / "backtest_passed.flag"
FEE_PCT = 0.80          # per side (real Kraken base taker; was stale 0.26)
SLIP_PCT = 0.1          # per side
LOOKBACK = 20           # range window (candles)


def _kraken_symbol(pair: str) -> str:
    return pair.replace("/", "").replace("BTC", "XBT") if pair.startswith("BTC") else pair.replace("/", "")


def _binance_symbol(pair: str) -> str:
    base, _, quote = pair.upper().partition("/")
    if quote == "USD":
        quote = "USDT"
    return f"{base}{quote}"


_BINANCE_TF = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}


def fetch_candles(pair="DOT/USD", interval=60, limit=1500) -> list:
    """Historical OHLC via Binance klines (paginated, years of history, no key).

    Binance is used as the HISTORICAL DATA source for validation even though we
    trade on Kraken — Kraken's public OHLC only exposes ~720 candles per
    interval, which is too short to prove an edge.
    """
    tf = _BINANCE_TF.get(interval, "1h")
    sym = _binance_symbol(pair)
    rows = []
    end = int(time.time() * 1000)
    per = 1000
    base_ms = interval * 60 * 1000
    start = end - limit * base_ms
    cursor = start
    guard = 0
    while len(rows) < limit and guard < 30:
        guard += 1
        url = (f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={tf}"
               f"&startTime={cursor}&limit={per}")
        try:
            batch = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "BlackHorse/1.0"}), timeout=15))
        except Exception:
            break
        if not isinstance(batch, list) or not batch:
            break
        for r in batch:
            rows.append({"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
                         "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
        cursor = int(batch[-1][0]) + base_ms
        if cursor >= end or len(batch) < per:
            break
    uniq = {r["t"]: r for r in rows}
    return sorted(uniq.values(), key=lambda x: x["t"])


def _strength(candles, i, lookback):
    lo = min(c["l"] for c in candles[i - lookback:i])
    hi = max(c["h"] for c in candles[i - lookback:i])
    if hi <= lo:
        return None, lo, hi
    s = (candles[i]["c"] - lo) / (hi - lo)
    return s, lo, hi


def simulate(candles, entry_max, exit_min, stop_pct, min_rr, lookback=LOOKBACK) -> dict:
    trades = []
    pos = None
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    for i in range(lookback, len(candles) - 1):
        c = candles[i]
        s, lo, hi = _strength(candles, i, lookback)
        if s is None:
            continue
        if pos is None:
            if s <= entry_max:      # buy zone
                entry = c["c"] * (1 + SLIP_PCT / 100)
                stop = entry * (1 - stop_pct / 100)
                target = max(hi, entry * (1 + stop_pct * min_rr / 100))
                pos = {"side": "BUY", "entry": entry, "stop": stop, "target": target}
            elif s >= exit_min and s >= 0.5:  # sell zone
                entry = c["c"] * (1 - SLIP_PCT / 100)
                stop = entry * (1 + stop_pct / 100)
                target = min(lo, entry * (1 - stop_pct * min_rr / 100))
                pos = {"side": "SELL", "entry": entry, "stop": stop, "target": target}
            continue
        # manage open position on this candle
        exit_price = None
        if pos["side"] == "BUY":
            if c["l"] <= pos["stop"]:
                exit_price = pos["stop"]
            elif c["h"] >= pos["target"]:
                exit_price = pos["target"]
        else:
            if c["h"] >= pos["stop"]:
                exit_price = pos["stop"]
            elif c["l"] <= pos["target"]:
                exit_price = pos["target"]
        if exit_price is None:
            continue
        mult = 1 if pos["side"] == "BUY" else -1
        gross = (exit_price - pos["entry"]) / pos["entry"] * mult * 100
        net = gross - FEE_PCT * 2 - SLIP_PCT  # slippage also on exit
        eq *= (1 + net / 100)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak * 100)
        trades.append({"net": net})
        pos = None
    return {"trades": len(trades),
            "wins": sum(1 for t in trades if t["net"] > 0),
            "win_rate": (sum(1 for t in trades if t["net"] > 0) / len(trades)) if trades else 0,
            "net_return_pct": (eq - 1) * 100,
            "max_drawdown_pct": maxdd}


def walk_forward(candles, lookback=LOOKBACK) -> dict:
    split = int(len(candles) * 0.7)
    train, test = candles[:split], candles[split:]
    best = None
    for entry_max in (0.1, 0.15, 0.2, 0.25, 0.3):
        for exit_min in (0.7, 0.75, 0.8, 0.85, 0.9):
            for stop_pct in (1.0, 1.5, 2.0, 3.0):
                for min_rr in (1.0, 1.5, 2.0):
                    r = simulate(train, entry_max, exit_min, stop_pct, min_rr, lookback)
                    if r["trades"] < 3 or r["net_return_pct"] <= 0:
                        continue
                    score = r["net_return_pct"] - r["max_drawdown_pct"] * 0.75 + r["win_rate"] * 2
                    if best is None or score > best["score"]:
                        best = {"score": score, "settings": {
                            "entry_max": entry_max, "exit_min": exit_min,
                            "stop_pct": stop_pct, "min_rr": min_rr}, "train": r}
    if not best:
        return {"status": "NO_TRAINING_EDGE", "sample": len(candles),
                "train_size": len(train), "test_size": len(test)}
    t = best["settings"]
    test_r = simulate(test, t["entry_max"], t["exit_min"], t["stop_pct"], t["min_rr"], lookback)
    robust = (test_r["trades"] >= 3 and test_r["net_return_pct"] > 0
              and test_r["max_drawdown_pct"] <= max(10, test_r["net_return_pct"] * 2))
    return {"status": "OUT_OF_SAMPLE_EDGE" if robust else "NOT_PROVEN",
            "settings": t, "train": best["train"], "test": test_r,
            "sample": len(candles), "train_size": len(train), "test_size": len(test),
            "evidence": ("Profitable after round-trip fees+slippage on untouched test data."
                         if robust else
                         "Selected on train but did NOT meet test return/trade/drawdown bar.")}


def run(pair="DOT/USD", interval=60, limit=1500) -> dict:
    candles = fetch_candles(pair, interval, limit)
    if len(candles) < 200:
        report = {"status": "INSUFFICIENT_DATA", "candles": len(candles), "pair": pair}
    else:
        res = walk_forward(candles)
        report = {"pair": pair, "interval": interval, "candles": len(candles),
                  "fees_pct_per_side": FEE_PCT, "slippage_pct_per_side": SLIP_PCT,
                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "validates": "decider range-strength concept (NOT full SignalBot advisor)",
                  **res}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    if report.get("status") == "OUT_OF_SAMPLE_EDGE":
        import hashlib
        digest = hashlib.sha256(REPORT.read_bytes()).hexdigest()[:16]
        PASS_FLAG.write_text(f"OUT_OF_SAMPLE_EDGE sha256:{digest} {report['ts']} {pair}\n")
    elif PASS_FLAG.exists():
        PASS_FLAG.unlink()  # losing the edge revokes the flag
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="DOT/USD"); ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--limit", type=int, default=1500); ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        print(REPORT.read_text() if REPORT.exists() else "(no report yet — run a backtest)")
    else:
        print(json.dumps(run(args.pair, args.interval, args.limit), indent=2))
