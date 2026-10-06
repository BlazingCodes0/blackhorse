"""decider.py — turn signal + brain verdict + treasury into ONE concrete plan.

Deterministic. No orders. No LLM. It combines:
  * the SignalBot read (per channel strength/direction/advisor targets)
  * the brain's stance (coach verdict: bias/regime/stance/risk_level)
  * the treasury (available capital)
and emits a single explainable decision: BUY / SELL / HOLD with side, entry,
size, stop, target, R:R — or a HOLD with reasons.

Your rules live in config/decider.json (data, not code):
  entry_strength_max   (strength near 0 = buy zone)
  exit_strength_min    (strength near 1 = sell zone)
  min_reward_risk, stop_max_pct, risk_per_trade_pct, max_position_pct, fee_pct_per_side
  require_tide_context (HTF confirmation)

Status: UNVALIDATED. The rules are a hypothesis. Nothing here is proven until a
fee-honest out-of-sample backtest says so. Paper only.

CLI:
  python3 trade/decider.py            # decide from latest signal + brain + treasury
  python3 trade/decider.py --explain  # human-readable reasoning
"""
import json, sys, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))
CFG = BH / "config" / "decider.json"
SIGNAL = BH / "logs" / "signal_latest.json"
BRAIN_LOG = BH / "logs" / "brain_market.jsonl"


def _cfg() -> dict:
    defaults = {"risk_per_trade_pct": 1.0, "max_position_pct": 10.0,
                "entry_strength_max": 0.25, "exit_strength_min": 0.75,
                "min_reward_risk": 1.5, "stop_max_pct": 3.0,
                "fee_pct_per_side": 0.26, "require_tide_context": True}
    try:
        return {**defaults, **json.loads(CFG.read_text())}
    except (json.JSONDecodeError, OSError):
        return defaults


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _missing(v) -> bool:
    if v is None or v == "":
        return True
    try:
        return float(v) == 0
    except (TypeError, ValueError):
        return True


def _num(v):
    try:
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _rsi(interval_min: int, periods: int = 14, url_base: str = "https://api.kraken.com/0/public/OHLC"):
    """RSI(periods) of DOT/USD closes at the given interval, from Kraken public data.
    Returns None when unavailable (never blocks, just informs)."""
    import urllib.request, urllib.parse
    try:
        q = urllib.parse.urlencode({"pair": "DOTUSD", "interval": interval_min})
        d = json.loads(urllib.request.urlopen(f"{url_base}?{q}", timeout=10).read())
        rows = d.get("result", {}).get("DOTUSD", [])
        closes = [float(r[4]) for r in rows[-(periods * 2 + 2):]]
        if len(closes) < periods + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            ch = closes[i] - closes[i - 1]
            gains.append(max(ch, 0)); losses.append(max(-ch, 0))
        ag = sum(gains[-periods:]) / periods
        al = sum(losses[-periods:]) / periods
        return 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    except Exception:
        return None


def _strength(v):
    """Strength of 0.0 is MEANINGFUL (price at the floor) — keep it."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _last_verdict():
    try:
        lines = BRAIN_LOG.read_text(errors="ignore").splitlines()
        for l in reversed(lines):
            if l.strip():
                return json.loads(l).get("verdict")
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _treasury():
    try:
        import treasury
        return treasury.status()
    except Exception:
        return {}


def decide(signal=None, verdict=None, treasury=None, cfg=None, holding=0.0, pair=None) -> dict:
    signal = signal or _load(SIGNAL, {"available": False})
    verdict = verdict or _last_verdict()
    treasury = treasury if treasury is not None else _treasury()
    cfg = cfg or _cfg()
    reasons = []
    # SPOT-ONLY: a SELL is only possible if we already hold inventory (no shorting).
    spot_only = cfg.get("spot_only", True)
    # the measured edge (evidence-first). Every plan must cite it.
    try:
        import edge as _edge
    except Exception:
        _edge = None

    if not signal or not signal.get("available"):
        return _hold("no signal snapshot", reasons)

    ch = signal.get("channels", {})
    ripple, wave, tide = ch.get("ripple", {}), ch.get("wave", {}), ch.get("tide", {})
    available = _num(treasury.get("available"))
    if not available or available <= 0:
        return _hold("no spendable capital (available <= 0)", reasons)

    # --- brain stance gate ---
    stance = (verdict or {}).get("stance")
    risk = (verdict or {}).get("risk_level")
    if stance == "STAND_DOWN":
        reasons.append("brain says STAND_DOWN")
        return _hold("brain stand-down", reasons)
    if risk == "EXTREME":
        reasons.append("brain risk EXTREME")
        return _hold("brain extreme risk", reasons)

    # --- candidates from channels (restricted to the agent's channels if set) ---
    allowed = cfg.get("channels")
    # SPREAD TRUTH (owner rule): trust ONLY the 15m spread read. Other timeframes
    # are noise or too slow. Find the 15m lane regardless of which channel we trade.
    spread15 = _spread_15m(ch)
    plans = []
    for name, c in (("ripple", ripple), ("wave", wave), ("tide", tide)):
        if allowed and name not in allowed:
            continue
        s = _strength(c.get("strength"))
        if s is None:
            continue
        adv = c.get("advisor") or {}
        buy, sell = _num(adv.get("buy")), _num(adv.get("sell"))
        direction = c.get("strengthDirection")
        # BUY
        if s <= cfg["entry_strength_max"] and not _missing(adv.get("buy")):
            plans.append(_build(name, c, "BUY", buy, s, direction, available, cfg, reasons, _edge, pair, spread15))
        # SELL (only if we can actually sell — advisor sell present)
        if s >= cfg["exit_strength_min"] and not _missing(adv.get("sell")):
            # SPOT-ONLY: cannot sell what we do not hold.
            if spot_only and float(holding or 0) <= 0:
                reasons.append(f"{name} SELL skipped: spot-only, no inventory held")
            else:
                plans.append(_build(name, c, "SELL", sell, s, direction, available, cfg, reasons, _edge, pair, spread15))

    plans = [p for p in plans if p]
    if not plans:
        if _edge:
            reasons.append("evidence gate: no channel+side has a PROVEN post-fee edge "
                           "(see logs/edge_report.json)")
        reasons.append("no channel met entry/exit strength bands")
        return _hold("no proven-edge setup", reasons)

    # --- brain bias alignment + HTF context ---
    bias = (verdict or {}).get("bias")
    for p in plans:
        if bias and bias != "NEUTRAL":
            aligned = (p["side"] == "BUY" and bias == "BULLISH") or (p["side"] == "SELL" and bias == "BEARISH")
            p["aligned"] = aligned
            if not aligned:
                p["score"] -= 0.1
    if cfg.get("require_tide_context"):
        tide_dir = tide.get("trendState")
        for p in plans:
            p["tide_ctx"] = tide_dir
            if p["side"] == "BUY" and tide_dir == "falling":
                p["score"] -= 0.15; reasons.append(f"{p['channel']} buy but tide falling (context risk)")
            if p["side"] == "SELL" and tide_dir == "rising":
                p["score"] -= 0.15; reasons.append(f"{p['channel']} sell but tide rising (context risk)")

    # --- R:R filter ---
    plans = [p for p in plans if p["rr"] >= cfg["min_reward_risk"]]
    if not plans:
        reasons.append(f"no plan cleared min R:R {cfg['min_reward_risk']}")
        return _hold("R:R too low", reasons)

    best = sorted(plans, key=lambda p: (-p["score"], -p["rr"]))[0]
    return {
        "action": best["side"], "channel": best["channel"], "regime": (verdict or {}).get("regime"),
        "entry": round(best["entry"], 8), "stop": round(best["stop"], 8),
        "target": round(best["target"], 8), "size_quote": round(best["size_quote"], 2),
        "risk_quote": round(best["risk_quote"], 2), "rr": round(best["rr"], 2),
        "score": round(best["score"], 4),
        "edge": best.get("edge"),
        "brain_stance": stance, "brain_bias": bias, "tide_ctx": best.get("tide_ctx"),
        "evidence": best.get("evidence", []),
        "explain": best["explain"] + [f"chose {best['channel']} over {len(plans)-1} other plan(s)"],
        "status": "UNVALIDATED",
    }


def _spread_15m(channels: dict) -> dict:
    """The ONLY trustworthy spread read is the 15m timeframe (owner rule).
    Other timeframes are noise or too slow. Locate the 15m lane by its interval —
    it may be labeled wave15 (dayTrade lane) or ripple/wave/tide with interval 15m."""
    if not channels:
        return {}
    for name, c in channels.items():
        if str(c.get("interval")) == "15m":
            mr = c.get("marketResult") or {}
            return {"interval": "15m", "label": name,
                    "change": mr.get("spreadChange"),
                    "latest": mr.get("latestSpread"),
                    "was": mr.get("intialSpread"),
                    "direction": mr.get("spreadDirection"),
                    "count": mr.get("spreadCount")}
    return {}


def _build(channel, c, side, entry, strength, direction, available, cfg, reasons, edge=None, pair=None, spread15=None):
    f = _num(c.get("floor")); lo = _num(c.get("low")); hi = _num(c.get("high")); ro = _num(c.get("roof"))
    adv = c.get("advisor") or {}
    adv_buy, adv_sell = _num(adv.get("buy")), _num(adv.get("sell"))
    entry = _num(entry)
    if not entry:
        return None
    # --- evidence gate: consult the MEASURED edge for this interval+side ---
    interval = c.get("interval") or ""
    edge_cell = edge.cell(interval, side, pair) if edge else {"status": "NO_DATA"}
    edge_status = edge_cell.get("status", "NO_DATA")
    if cfg.get("require_proven_edge", True) and edge_status != "PROVEN":
        # refuse to trade a cell the data says is losing or unproven
        return None
    # --- REAL MA-context gate: the SPREAD TRUTH is the 15m lane only (owner rule).
    # We use the actual numeric spread change, not the text label. Other
    # timeframes are noise or too slow, so we do NOT read their spread here.
    mr = c.get("marketResult") or {}
    trend = (mr.get("trendState") or c.get("trendState") or "").lower()
    if spread15 is None:
        spread15 = _spread_15m({"15m": c})     # fall back to this channel if it IS 15m
    change = (spread15 or {}).get("change")
    eps = float(cfg.get("spread_flat_epsilon", 0.02))   # "flat" band
    if change is not None:
        change = float(change)
        if side == "BUY" and change < -eps:
            return None                       # 15m spread genuinely against a long
        if side == "SELL" and change > eps:
            return None                       # 15m spread genuinely against the exit
    elif (spread15 or {}).get("direction"):
        d = str(spread15["direction"]).lower()
        if side == "BUY" and d == "weakening":
            return None
        if side == "SELL" and d == "improving":
            return None
    if cfg.get("require_trend_agreement", False):
        if side == "BUY" and trend == "falling":
            return None
        if side == "SELL" and trend == "rising":
            return None
    # --- RSI lane (playbook finding): refuse entries that fight extremes ---
    try:
        imin = int(str(interval).rstrip("m")) if str(interval).endswith("m") else None
    except (TypeError, ValueError):
        imin = None
    if imin:
        rsi = _rsi(imin)
        if rsi is not None:
            if side == "BUY" and rsi >= cfg.get("rsi_buy_block_above", 70.0):
                return None  # price at the floor but RSI is overbought -> skip
            if side == "SELL" and rsi <= cfg.get("rsi_sell_block_below", 30.0):
                return None  # price at the roof but RSI is extreme-oversold -> skip
            if side == "BUY" and rsi < cfg.get("rsi_buy_min", 0.0):
                return None
    # stop: structural invalidation on the correct side of entry, else a small buffer
    if side == "BUY":
        below = [x for x in (lo, f) if x and x < entry]
        structural = max(below) if below else None
        stop = structural if (structural and (entry - structural) / entry * 100 <= cfg["stop_max_pct"]) \
            else entry * (1 - cfg["stop_buffer_pct"] / 100)
        target = adv_sell if adv_sell and adv_sell > entry else (hi if hi and hi > entry else ro)
    else:
        above = [x for x in (hi, ro) if x and x > entry]
        structural = min(above) if above else None
        stop = structural if (structural and (structural - entry) / entry * 100 <= cfg["stop_max_pct"]) \
            else entry * (1 + cfg["stop_buffer_pct"] / 100)
        target = adv_buy if adv_buy and adv_buy < entry else (lo if lo and lo < entry else f)
    if not target:
        return None
    stop_pct = abs(entry - stop) / entry * 100
    if stop_pct <= 0 or stop_pct > cfg["stop_max_pct"]:
        return None
    rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
    # fee-aware: net target gain must clear round-trip fees
    fee_frac = cfg["fee_pct_per_side"] / 100 * 2
    slip_frac = cfg.get("slippage_pct_per_side", 0.1) / 100 * 2
    gross_move = abs(target - entry) / entry
    if gross_move <= fee_frac:
        return None
    # PRE-ESTABLISHED at buy creation: expected net P&L % if target hits, net of
    # round-trip fees + slippage. This is the "beauty" number the owner sees.
    expected_net_pct = round(gross_move * 100 - fee_frac * 100 - slip_frac * 100, 3)
    risk_quote = available * (cfg["risk_per_trade_pct"] / 100)
    size_quote = risk_quote / (stop_pct / 100)
    size_quote = min(size_quote, available * (cfg["max_position_pct"] / 100))
    score = (1 - strength) if side == "BUY" else strength
    if direction == "rising" and side == "BUY":
        score += 0.1
    if direction == "falling" and side == "SELL":
        score += 0.1
    # --- the logical case for this plan (evidence the broker can weigh) ---
    mult = "near floor" if side == "BUY" else "near roof"
    evidence = [
        f"measured edge: {interval} {side} is {edge_status}"
        + (f" (win {edge_cell.get('win_pct')}%, net {edge_cell.get('avg_net_pct')}%, n={edge_cell.get('n')})"
           if edge_status == "PROVEN" else " — refused by evidence gate"),
        f"strength {round(strength,3)} on {channel} ({mult} — {'buy' if side=='BUY' else 'sell'} zone)",
        f"strength is {direction or 'flat'}",
        f"R:R {round(rr,2)} (target {round(target,6)} vs stop {round(stop,6)})",
        f"target clears round-trip fees ({round(gross_move*100,2)}% > {round(fee_frac*100,2)}%)",
        f"expected net if target hits: {expected_net_pct}% after fees+slippage",
        f"stop distance {round(stop_pct,2)}% within cap {cfg['stop_max_pct']}%",
        (f"15m spread (the trusted read): change {round(change,4)} over "
         f"{(spread15 or {}).get('count')} bars · latest {(spread15 or {}).get('latest')} "
         f"(was {(spread15 or {}).get('was')})" if change is not None else
         "15m spread: no numeric read available"),
    ]
    return {"channel": channel, "side": side, "entry": entry, "stop": stop, "target": target,
            "size_quote": size_quote, "risk_quote": risk_quote, "rr": rr, "score": score,
            "expected_net_pct": expected_net_pct,
            "fees_roundtrip_pct": round(fee_frac * 100, 3),
            "edge": {"interval": interval, "side": side, "status": edge_status,
                     "win_pct": edge_cell.get("win_pct"), "avg_net_pct": edge_cell.get("avg_net_pct"),
                     "n": edge_cell.get("n")},
            "evidence": evidence,
            "explain": [f"{channel} {side}: strength {round(strength,3)}, expected net {expected_net_pct}% after fees, "
                        f"entry {round(entry,6)} stop {round(stop,6)} target {round(target,6)} "
                        f"stop {round(stop_pct,2)}% R:R {round(rr,2)}, size {round(size_quote,2)} "
                        f"(risk {round(risk_quote,2)}), fees clear {round(gross_move*100,2)}%>"
                        f"{round(fee_frac*100,2)}%"]}


def _hold(reason, reasons):
    return {"action": "HOLD", "reason": reason, "explain": reasons, "status": "UNVALIDATED"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()
    d = decide()
    if args.explain:
        print(f"ACTION: {d['action']}  ({d.get('reason', d.get('channel',''))})")
        for line in d.get("explain", []):
            print("  -", line)
        print("  (UNVALIDATED — hypothesis only; not proof)")
    else:
        print(json.dumps(d, indent=2))
