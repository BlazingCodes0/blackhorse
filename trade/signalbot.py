"""signal.py — run the SignalBot sidecar and normalize its output for the brain.

Calls the Node sidecar (trade/sidecar/signal.js), which runs the REAL SignalBot,
then writes a compact, brain-friendly snapshot to logs/signal_latest.json that
coach.py reads into its packet.

Nothing about the pair/exchange is hardcoded — they come from the active
exchange registry and the caller.

CLI:
  python3 trade/signal.py                 # active exchange, default pair
  python3 trade/signal.py --pair BTC/USD --strategy swing
  python3 trade/signal.py --show          # print the last snapshot
"""
import json, subprocess, sys, time, argparse, shutil
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
SIDECAR = BH / "trade" / "sidecar" / "signal.js"
RAW_OUT = BH / "logs" / "signal_raw.json"
LATEST = BH / "logs" / "signal_latest.json"


def _node() -> str:
    return shutil.which("node") or "node"


def run(pair="DOT/USD", strategy="swing", exchange=None, timeout=180, channels=None) -> dict:
    if exchange is None:
        try:
            import exchanges
            exchange = exchanges.active_exchange()["name"]
        except Exception:
            exchange = "kraken"
    cmd = [_node(), str(SIDECAR), "--pair", pair, "--strategy", strategy,
           "--exchange", exchange, "--out", str(RAW_OUT)]
    if channels:
        cmd += ["--channels", ",".join(channels)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(BH))
    except subprocess.TimeoutExpired:
        return {"available": False, "error": f"sidecar timed out after {timeout}s"}
    try:
        meta = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        meta = {"ok": False, "error": (r.stderr or "no sidecar output")[-300:]}
    if not meta.get("ok"):
        return {"available": False, "error": meta.get("error", "sidecar failed")}

    raw = json.loads(RAW_OUT.read_text())
    snap = normalize(raw, meta)
    # second sidecar strategy for the 15m lane: dayTrade maps wave to 15m
    try:
        cfg2 = None
        cmd2 = [_node(), str(SIDECAR), "--pair", pair, "--strategy", "dayTrade",
                "--exchange", exchange, "--out", str(RAW_OUT)]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=timeout, cwd=str(BH))
        meta2 = json.loads(r2.stdout.strip().splitlines()[-1]) if r2.stdout.strip() else {}
        if meta2.get("ok"):
            raw2 = json.loads(RAW_OUT.read_text())
            snap2 = normalize(raw2, meta2)
            for ch, label in (("wave","wave15"), ("ripple","ripple5"), ("tide","tide60")):
                if ch in snap2.get("channels", {}):
                    snap["channels"][label] = dict(snap2["channels"][ch])
                    snap["channels"][label]["role"] = f"{snap2['channels'][ch].get('role','mid')}/dayTrade"
            snap["dayTrade_ts"] = snap2.get("ts")
    except Exception:
        pass
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(snap, indent=2))
    return snap


def normalize(raw: dict, meta: dict) -> dict:
    """Flatten SignalBot channels into one compact per-interval read."""
    channel_map = {"ripple": "lower", "wave": "mid", "tide": "higher"}
    channels = {}
    for ch, role in channel_map.items():
        s = raw.get(ch) or {}
        f = s.get("factorResult") or {}
        m = s.get("marketResult") or {}
        d = s.get("descisionObj") or {}
        adv = s.get("advisorPrices") or {}
        buy = adv.get("buy")
        sell = adv.get("sell")
        channels[ch] = {
            "role": role,
            "interval": s.get("interval"),
            "currPrice": s.get("currPrice"),
            "action": d.get("action"),
            "reason": d.get("reson") or d.get("reason"),
            "confidence": d.get("confidence"),
            "floor": f.get("floor"), "low": f.get("low"),
            "high": f.get("high"), "roof": f.get("roof"),
            "strength": f.get("strength"),
            "strengthDirection": f.get("strengthDirection"),
            "rangeSignal": f.get("rangeSignal"),
            "rangeBias": f.get("rangeBias"),
            "buyScore": f.get("buyScore"), "sellScore": f.get("sellScore"),
            "entryEligible": f.get("entryEligible"), "exitEligible": f.get("exitEligible"),
            "intervalRole": f.get("intervalRole"),
            "scoreReasons": f.get("scoreReasons"),
            # the REAL market context the SignalBot produced (was being dropped):
            "marketResult": {
                "trendState": m.get("trendState"),
                "spreadDirection": m.get("spreadDirection"),
                "latestSpread": m.get("latestSpread"),
                "intialSpread": m.get("intialSpread"),
                "maSpreadRun": m.get("maSpreadRun"),
                "maSpreadsAnalysis": m.get("maSpreadsAnalysis"),
                "stopLoss": m.get("stopLoss"), "takeProfit": m.get("takeProfit"),
            },
            "RSI": s.get("RSI"),
            # advisor lanes — NOTE which side is missing (null/0/empty = no flow)
            "advisor": {
                "buy": buy, "sell": sell,
                "buy_missing": _missing(buy), "sell_missing": _missing(sell),
                "prices": adv.get("prices") or {},
            },
        }
    grids = raw.get("grids") or {}
    ms = raw.get("marketStructure") or {}
    return {
        "available": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pair": meta.get("pair"),
        "exchange": meta.get("exchange"),
        "strategy": meta.get("strategy"),
        "run_ms": meta.get("ms"),
        "channels": channels,
        "grids": {k: {"floor": (v or {}).get("floor"), "low": (v or {}).get("low"),
                      "high": (v or {}).get("high"), "roof": (v or {}).get("roof")}
                  for k, v in grids.items()},
        "structure": {
            "aggregate": (ms.get("aggregate") or {}),
            "executionIntervals": ms.get("executionIntervals"),
        },
    }


def _missing(v) -> bool:
    """null / 0 / empty all mean 'no price' — never a real number."""
    if v is None or v == "":
        return True
    try:
        return float(v) == 0
    except (TypeError, ValueError):
        return True


def load() -> dict:
    try:
        return json.loads(LATEST.read_text())
    except (json.JSONDecodeError, OSError):
        return {"available": False}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="DOT/USD")
    ap.add_argument("--strategy", default="swing")
    ap.add_argument("--exchange", default=None)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    if args.show:
        print(json.dumps(load(), indent=2))
    else:
        print(json.dumps(run(args.pair, args.strategy, args.exchange), indent=2))
