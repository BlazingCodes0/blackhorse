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


def run(pair="DOT/USD", strategy="swing", exchange=None, timeout=180, channels=None, merge_daytrade=False, out=None, latest=True) -> dict:
    if exchange is None:
        try:
            import exchanges
            exchange = exchanges.active_exchange()["name"]
        except Exception:
            exchange = "kraken"
    RAW = RAW_OUT.with_name(f"signal_raw_{str(pair).replace('/', '-').upper()}.json")
    cmd = [_node(), str(SIDECAR), "--pair", pair, "--strategy", strategy,
           "--exchange", exchange, "--out", str(RAW)]
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

    raw = json.loads(RAW.read_text())
    snap = normalize(raw, meta)
    if merge_daytrade:
        # separate sidecar run for an extra dayTrade lane, so trading can see 15m
        try:
            cmd2 = [_node(), str(SIDECAR), "--pair", pair, "--strategy", "dayTrade",
                    "--exchange", exchange, "--out", str(RAW)]
            r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=timeout, cwd=str(BH))
            meta2 = json.loads(r2.stdout.strip().splitlines()[-1]) if r2.stdout.strip() else {}
            if meta2.get("ok"):
                raw2 = json.loads(RAW.read_text())
                snap2 = normalize(raw2, meta2)
                for ch, label in (("wave","wave15"), ("ripple","ripple5"), ("tide","tide60")):
                    if ch in snap2.get("channels", {}):
                        snap["channels"][label] = dict(snap2["channels"][ch])
                        snap["channels"][label]["role"] = f"{snap2['channels'][ch].get('role','mid') or 'mid'}/dayTrade"
                snap["dayTrade_ts"] = snap2.get("ts")
        except Exception:
            pass
    if out:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=2))
        return snap
    import signals
    signals.save(pair, snap)                       # always: this pair's own snapshot
    if latest:                                     # GUI/brain view = the primary pair only
        LATEST.parent.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(json.dumps(snap, indent=2))
    return snap



def _spread_ma(raw: dict, ch: str) -> dict:
    """smartbot-style ma10/ma30/maSpreadPct — smartbot aliases ma10=maFast,
    ma30=maSlow (MA20/MA50), maSpreadPct=(fast-slow)/slow*100, signal at ±3%."""
    mx = ((raw.get("signals") or {}).get("maCrossTripple") or {}).get(ch) or {}
    fast, slow = mx.get("fast"), mx.get("slow")
    def _last(v):
        if isinstance(v, list):
            v = [x for x in v if isinstance(x, (int, float))]
            v = v[-1] if v else None
        return v if isinstance(v, (int, float)) else None
    fast, slow = _last(fast), _last(slow)
    if fast is None or slow in (None, 0):
        return {"available": False}
    pct = (fast - slow) / slow * 100
    signal = "bullish" if pct >= 3 else ("bearish" if pct <= -3 else "neutral")
    return {"available": True, "ma10": fast, "ma30": slow,
            "diff": round(fast - slow, 6), "diffPct": round(pct, 4), "signal": signal}

def _spread_verdict(m) -> dict:
    """Smart 15m spread read: separate genuine drift from chop/noise."""
    a = m.get("maSpreadsAnalysis") or {}
    bars = a.get("bars") or 0
    bias = a.get("directionBias") or 0
    chop = a.get("chopLevel") or 0
    strength = a.get("strength") or 0
    if bars < 8:
        v = "insufficient history"
    elif chop >= 0.6:
        v = "choppy (noise)"
    elif bias < 0.6:
        v = "noise (no dominant direction)"
    elif strength < 0.35:
        v = "weak drift"
    elif a.get("trend") == "up":
        v = "improving" + (" (strong)" if strength >= 0.6 else "")
    else:
        v = "weakening" + (" (strong)" if strength >= 0.6 else "")
    return {"verdict": v, "trend": a.get("trend"), "bars": bars,
            "directionBias": round(bias, 3), "chopLevel": round(chop, 3),
            "strength": round(strength, 3)}

def _entry_gate(c: dict) -> dict:
    """Explicit 15m-style entry-gate checklist from the live numbers —
    observability only, does NOT change any decision."""
    r = c.get("RSI") or {}
    rsi_gate_ok = bool(r.get("buy")) or (r.get("currentRSI") is not None and r.get("currentRSI") <= (r.get("buyThreshold") or 35))
    mr = c.get("marketResult") or {}
    against = str(mr.get("spreadDirection") or "").lower() in ("weakening", "down")
    score_ok = (c.get("buyScore") or 0) >= 0.60 and (c.get("buyScore") or 0) > (c.get("sellScore") or 0)
    return {
        "rsiOk": rsi_gate_ok,
        "spreadNotAgainst": not against,
        "scoreOk": score_ok,
        "entryEligible": bool(c.get("entryEligible")),
        "trendOk": mr.get("trendState") in ("up", "bull", "rising"),
        "allPass": rsi_gate_ok and (not against) and score_ok and bool(c.get("entryEligible")),
    }

def _bottom_watch(c: dict) -> dict:
    """Armed when price kisses floor/low with 15m strength ~0 — watch, not trigger."""
    price = c.get("currPrice")
    floor = c.get("floor"); low = c.get("low")
    strg = c.get("strength") or 0
    try:
        near = price is not None and low is not None and price <= max(floor or low, low) * 1.01
    except Exception:
        near = False
    armed = near and strg < 0.15
    return {"armed": armed, "price": price, "low": low, "strength": strg,
            "waitFor": "RSI<=35, spreadVerdict improving, spreadMA bullish, buyScore>=0.60"}

def _setup_scenario(c: dict) -> str:
    """The 6-scenario rubric as a label — brain context, no gate power."""
    r = c.get("RSI") or {}
    mr = c.get("marketResult") or {}
    sm = c.get("spreadMA") or {}
    price = c.get("currPrice"); low = c.get("low")
    buy_score = c.get("buyScore") or 0
    rsi = r.get("currentRSI") or 99
    against = str(mr.get("spreadDirection") or "").lower() in ("weakening", "down")
    if price is not None and low is not None and price < low:
        return "4: price broke low — range invalidated"
    if sm.get("signal") == "bullish" and mr.get("trendState") in ("up", "bull", "rising") and price and low and price >= low:
        return "3: spreadMA bullish + trend up — real one"
    if rsi <= 35 and not against and buy_score >= 0.60:
        return "1: RSI washout, spread ok, score ok — BUY candidate"
    if rsi <= 35 and against:
        return "2: RSI oversold but spread weakening — wait"
    strg = c.get("strength") or 0
    if strg < 0.10 and buy_score >= 0.85:
        return "5/6: higher-MF score high, 15m strength ~0 — wait for 15m tick"
    if (c.get("rangeSignal") or "") == "BUY" and strg < 0.15:
        return "5: range BUY but strength ~0 — bottom-watch, do not chase"
    return "none: no scenario matches cleanly"

def _ma_verdict(mx: dict) -> str:
    """Gap-size based cross verdict — a 0.02% gap is a coil, not a bias."""
    gap = (mx or {}).get("gap_pct")
    if not isinstance(gap, (int, float)):
        return "no data"
    g = abs(gap)
    if g < 0.15:
        return "coil (gap ~0, noise)"
    if g < 0.5:
        return f"borderline {mx.get('bias')} bias"
    return f"real {mx.get('bias')} bias"

def _ma_cross(mx) -> dict:
    """Compact MA cross read: latest fast(ma20)/slow(ma50), who is on top, % gap."""
    if not mx:
        return {"available": False}
    import statistics
    fast = mx.get("fast")
    slow = mx.get("slow")
    if isinstance(fast, list):
        fast = [x for x in fast if isinstance(x, (int, float))]
        fast = fast[-1] if fast else None
    if isinstance(slow, list):
        slow = [x for x in slow if isinstance(x, (int, float))]
        slow = slow[-1] if slow else None
    if not isinstance(fast, (int, float)) or not isinstance(slow, (int, float)):
        return {"available": False}
    f, s_ = fast, slow
    gap = round((f - s_) / s_ * 100, 3) if s_ else None
    crossed_up = False
    crossed_down = False
    return {"available": True, "ma20": f, "ma50": s_, "gap_pct": gap,
            "bias": "bullish" if f > s_ else "bearish",
            "cross": "STRONG_UP" if crossed_up else ("STRONG_DOWN" if crossed_down else "none")}

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
                "spreadChange": (m.get("maSpreadRun") or {}).get("change"),
                "spreadCount": (m.get("maSpreadRun") or {}).get("count"),
                "maSpreadRun": m.get("maSpreadRun"),
                "maSpreadsAnalysis": m.get("maSpreadsAnalysis"),
                "spreadVerdict": _spread_verdict(m) if str(s.get("interval")) == "15m" else None,
                "stopLoss": m.get("stopLoss"), "takeProfit": m.get("takeProfit"),
            },
            "RSI": s.get("RSI"),
            # smartbot-style ma10/ma30 spread (aliases of maFast/maSlow)
            "spreadMA": _spread_ma(raw, ch),
            # (entryGate / bottomWatch / setupScenario are filled in below,
            #  once the normalized channel dict exists)
            # REAL MA20/MA50 cross data straight from SignalBot
            "maCross": {**_ma_cross((raw.get("signals") or {}).get("maCrossTripple", {}).get(ch)),
                        "verdict": _ma_verdict(_ma_cross((raw.get("signals") or {}).get("maCrossTripple", {}).get(ch))) if str(s.get("interval")) == "15m" else None},
            # advisor lanes — NOTE which side is missing (null/0/empty = no flow)
            "advisor": {
                "buy": buy, "sell": sell,
                "buy_missing": _missing(buy), "sell_missing": _missing(sell),
                "prices": adv.get("prices") or {},
            },
        }
    # observability layer: explicit entry-gate checklist + bottom-watch + scenario
    # label. Context for the brain and the owner — changes NO decision.
    for _ch in channels.values():
        try:
            _ch["entryGate"] = _entry_gate(_ch)
            _ch["bottomWatch"] = _bottom_watch(_ch)
            _ch["setupScenario"] = _setup_scenario(_ch)
        except Exception:
            pass
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
