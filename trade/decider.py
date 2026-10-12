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
import json, math, sys, argparse
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
                "fee_pct_per_side": 0.80, "require_tide_context": True}
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


def _iv_minutes(iv):
    """'15m' -> 15, '120m' -> 120; None when the label is not '<n>m'."""
    try:
        s = str(iv or "")
        return int(s.rstrip("m")) if s.endswith("m") else None
    except (TypeError, ValueError):
        return None


def _slots_intervals(slots) -> list:
    """Channel slots -> interval labels (mirror of the GUI's mapping)."""
    m = {"ripple": "15m", "wave": "60m", "tide": "240m",
         "ripple5": "5m", "wave15": "15m", "tide60": "60m"}
    seen, out = set(), []
    for s in (slots or []):
        v = m.get(str(s), str(s))
        if v not in seen:
            seen.add(v); out.append(v)
    return out


def _chain_from(cfg: dict, channels: dict) -> dict:
    """The agent's intervals, turned into (entry lane, climb rungs).

    Smallest listed interval = the ENTRY (trading) lane. Every OTHER listed
    interval is a climb rung that must clear its own band before the entry lane
    may move. There is NO hidden default rung: if you list only your entry
    interval, the lane trades on its own strength alone (climb off)."""
    ivs = cfg.get("strategy_intervals")
    if not isinstance(ivs, list) or not ivs:
        ivs = _slots_intervals(cfg.get("channels"))
    if not ivs:                                  # nothing configured at all (e.g. bare
        ivs = ["15m", "60m", "240m"]             # --explain): the classic owner trio
    ivs = [str(x) for x in ivs]

    def _key(x):
        m = _iv_minutes(x)
        return (m if m is not None else 9e9, x)

    ordered = sorted(set(ivs), key=_key)
    entry = ordered[0]
    rungs = [x for x in ordered if x != entry]
    return {"entry": entry, "rungs": rungs, "intervals": ordered}


def _entry_lane(channels: dict, cfg: dict, entry_iv: str):
    """The channel carrying the entry interval — the agent's own channels first."""
    prefer = cfg.get("channels")
    if isinstance(prefer, list) and prefer:
        for n in prefer:
            c = channels.get(n)
            if c and str(c.get("interval")) == entry_iv:
                return n, c
    for n, c in channels.items():
        if str(c.get("interval")) == entry_iv:
            return n, c
    return None, None


def _pull_strength(interval_min: int, pair="DOT/USD", lookback=20):
    """(close-low)/(high-low) over the last `lookback` CLOSED candles at any
    interval, from Kraken public OHLC. Same formula as validate.py's _strength.
    Returns None on any failure (never blocks, just informs)."""
    import urllib.request, urllib.parse
    try:
        sym = str(pair or "DOT/USD").replace("/", "").upper()
        q = urllib.parse.urlencode({"pair": sym, "interval": int(interval_min)})
        d = json.loads(urllib.request.urlopen(
            f"https://api.kraken.com/0/public/OHLC?{q}", timeout=10).read())
        res = d.get("result", {}) or {}
        rows = next((v for k, v in res.items() if k != "last"), [])
        if len(rows) < lookback + 1:
            return None
        hist = rows[-(lookback + 1):-1]
        lo = min(float(r[3]) for r in hist)
        hi = max(float(r[2]) for r in hist)
        close = float(rows[-1][4])
        if hi <= lo:
            return None
        return (close - lo) / (hi - lo)
    except Exception:
        return None


def _rung_read(channels: dict, cfg: dict, pair, rung_iv: str):
    """A rung's range strength: the snapshot's own lane when it exists, else pulled
    LIVE from Kraken candles at that interval. -> (value, source)."""
    for n, c in channels.items():
        if str(c.get("interval")) == rung_iv:
            v = _strength(c.get("strength"))
            if v is not None:
                return v, f"from {rung_iv} lane '{n}'"
    m = _iv_minutes(rung_iv)
    if m is not None:
        v = _pull_strength(m, pair)
        if v is not None:
            return v, f"pulled live from {rung_iv} candles"
    return None, "unavailable"


def _rung_bands(cfg: dict, rung_iv: str):
    """Per-rung buy/sell bands from the GUI keys (…_60m / …_240m / …_1440m),
    else the agent's global entry_strength_max / exit_strength_min."""
    em = cfg.get(f"entry_strength_max_{rung_iv}", cfg.get("entry_strength_max", 0.25))
    xm = cfg.get(f"exit_strength_min_{rung_iv}", cfg.get("exit_strength_min", 0.75))
    try:
        return float(em), float(xm)
    except (TypeError, ValueError):
        return float(cfg.get("entry_strength_max", 0.25)), float(cfg.get("exit_strength_min", 0.75))


def _last_verdict():
    try:
        lines = BRAIN_LOG.read_text(errors="ignore").splitlines()
        for l in reversed(lines):
            if l.strip():
                rec = json.loads(l)
                import time as _t
                if _t.time() - float(rec.get("ts") or 0) > 1800:
                    return None            # stale verdict (>30 min) is not a veto
                return rec.get("verdict")
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
    if not signal:
        import signals
        signal = (signals.load(pair) if pair else _load(SIGNAL, {})) or {"available": False}
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

    # OWNER OVERRIDE (mtf_buy_override): 60m range signal = BUY AND the 15m lane is
    # entry-eligible -> BUY may fire even if the measured edge is not PROVEN.
    buy_override = False
    if cfg.get("mtf_buy_override", False):
        def _lane(iv):
            for _n, _c in ch.items():
                if str(_c.get("interval")) == iv:
                    yield _c
        r60 = any(str(c.get("rangeSignal") or "").upper() == "BUY" for c in _lane("60m"))
        e15 = any(bool(c.get("entryEligible")) for c in _lane("15m"))
        buy_override = r60 and e15
        reasons.append(f"mtf_buy_override: 60m range BUY={r60}, 15m entry-eligible={e15} -> "
                       f"{'ACTIVE (proven-edge check skipped for BUY)' if buy_override else 'inactive'}")
    # --- brain stance gate ---
    stance = (verdict or {}).get("stance")
    risk = (verdict or {}).get("risk_level")
    if stance == "STAND_DOWN" and not buy_override:
        reasons.append("brain says STAND_DOWN")
        return _hold("brain stand-down", reasons)
    if risk == "EXTREME":
        reasons.append("brain risk EXTREME")
        return _hold("brain extreme risk", reasons)

    # --- the strength climb (owner rule): listed intervals are the ladder -------------
    # The SMALLEST listed interval is the ENTRY LANE — the interval this agent
    # actually trades. Every OTHER interval listed above it is a climb rung: each
    # rung's range strength must sit inside ITS OWN band before the entry lane may
    # buy (rung ≤ its entry_max) or sell (rung ≥ its exit_min). A listed rung with
    # no lane in the snapshot is PULLED live from Kraken OHLC. Nothing is hard-coded
    # and there is no back-room default: only what the user lists (in the GUI) is
    # in the climb; a single listed interval trades flat on its own strength. The
    # old pick-the-best-interval dance is gone — we decide on the entry lane only.
    chain = _chain_from(cfg, ch)
    entry_iv = chain["entry"]
    rungs = chain["rungs"]
    chain_used = bool(rungs)
    entry_name, entry_ch = _entry_lane(ch, cfg, entry_iv)
    if not entry_ch:
        return _hold(f"no {entry_iv} lane for the entry interval in the snapshot "
                     f"(listed intervals: {', '.join(chain['intervals'])})", reasons)
    s = _strength(entry_ch.get("strength"))
    direction = entry_ch.get("strengthDirection")
    gauge = s                                   # strength reported to the plan
    chain_reads = []
    if chain_used:
        for r in rungs:
            g, src = _rung_read(ch, cfg, pair or signal.get("pair"), r)
            em, xm = _rung_bands(cfg, r)
            if g is None:
                chain_reads.append({"interval": r, "strength": None, "source": src,
                                    "entry_strength_max": em, "exit_strength_min": xm,
                                    "buy_allowed": False, "sell_allowed": False,
                                    "note": f"{r} strength unavailable ({src})"})
                reasons.append(f"strength chain: {r} unavailable ({src})")
                continue
            buy_ok = g <= em
            sell_ok = g >= xm
            note = (f"{r} str {round(g, 3)} ({src}) · BUY ≤ {em:g}: "
                    f"{'ALLOW' if buy_ok else 'BLOCK'} · SELL ≥ {xm:g}: "
                    f"{'ALLOW' if sell_ok else 'BLOCK'}")
            reasons.append(f"strength chain: {note}")
            chain_reads.append({"interval": r, "strength": round(g, 4), "source": src,
                                "entry_strength_max": em, "exit_strength_min": xm,
                                "buy_allowed": buy_ok, "sell_allowed": sell_ok, "note": note})
        if chain_reads:
            gauge = chain_reads[-1]["strength"]     # the acting (highest) gauge
            reasons.append(f"strength chain: acting gauge = {chain_reads[-1]['interval']} "
                           f"str {chain_reads[-1]['strength']} "
                           f"(entry lane {entry_name}/{entry_iv})")
        chain_buy = all(r.get("buy_allowed") for r in chain_reads)
        chain_sell = all(r.get("sell_allowed") for r in chain_reads)
    else:
        chain_buy = chain_sell = None
    # SPREAD TRUTH (owner rule): trust ONLY the 15m spread read. Other timeframes
    # are noise or too slow. Find the 15m lane regardless of which channel we trade.
    shared15 = _shared_15m(ch)
    cfg_buy = dict(cfg, require_proven_edge=False) if buy_override else cfg
    adv = entry_ch.get("advisor") or {}
    buy, sell = _num(adv.get("buy")), _num(adv.get("sell"))
    plans = []
    # BUY
    if chain_used:
        buy_ok_total = bool(chain_buy) and not _missing(buy)
    else:
        buy_ok_total = (s is not None and s <= cfg["entry_strength_max"] and not _missing(buy))
    if buy_ok_total:
        p = _build(entry_name, entry_ch, "BUY", buy, gauge, direction, available,
                   cfg_buy, reasons, _edge, pair, shared15)
        for r in chain_reads:
            if p and chain_used:
                p["evidence"].append(f"strength chain {r['note']}")
                p["explain"].append(f"strength chain {r['note']}")
        if p:
            plans.append(p)
    # SELL (only if we can actually sell — advisor sell present)
    if chain_used:
        sell_ok_total = bool(chain_sell) and not _missing(sell)
    else:
        sell_ok_total = (s is not None and s >= cfg["exit_strength_min"] and not _missing(sell))
    if sell_ok_total and (not spot_only or float(holding or 0) > 0):
        p = _build(entry_name, entry_ch, "SELL", sell, gauge, direction, available,
                   cfg, reasons, _edge, pair, shared15)
        for r in chain_reads:
            if p and chain_used:
                p["evidence"].append(f"strength chain {r['note']}")
                p["explain"].append(f"strength chain {r['note']}")
        if p:
            plans.append(p)
    if not plans:
        if _edge:
            reasons.append("evidence gate: no channel+side has a PROVEN post-fee edge "
                           "(see logs/edge_report.json)")
        reasons.append("entry lane did not clear the strength chain bands")
        return _hold("no strength-chain setup", reasons)

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
        "target": round(best["target"], 8), "size_quote": best["size_quote"],
        "order_type": best.get("order_type", "limit"), "expiry_s": best.get("expiry_s"),
        "outlay_quote": best.get("outlay_quote"), "fee_reserve_pct": best.get("fee_reserve_pct"),
        "fee_entry_pct": best.get("fee_entry_pct"), "fee_exit_pct": best.get("fee_exit_pct"),
        "risk_quote": round(best["risk_quote"], 2), "rr": round(best["rr"], 2),
        "score": round(best["score"], 4),
        "edge": best.get("edge"),
        "brain_stance": stance, "brain_bias": bias, "tide_ctx": best.get("tide_ctx"),
        "evidence": best.get("evidence", []),
        "explain": best["explain"] + [f"single entry lane {best['channel']} (no best-interval selection)"],
        "status": "UNVALIDATED",
    }


def _shared_15m(channels: dict) -> dict:
    """The 15m lane is the SOURCE OF TRUTH for the shared signals: the MA spread
    AND the MA20/MA50 cross (owner rule). Other intervals keep their own
    range/trend/structure, but spread + MA-cross are read from 15m only.
    The lane may be labeled wave15 (dayTrade merge) or be any channel with
    interval == 15m."""
    if not channels:
        return {}
    for name, c in channels.items():
        if str(c.get("interval")) == "15m":
            mr = c.get("marketResult") or {}
            mx = c.get("maCross") or {}
            return {"label": name, "interval": "15m",
                    # spread
                    "change": mr.get("spreadChange"),
                    "latest": mr.get("latestSpread"),
                    "was": mr.get("intialSpread"),
                    "direction": mr.get("spreadDirection"),
                    "count": mr.get("spreadCount"),
                    # MA cross
                    "ma20": mx.get("ma20"), "ma50": mx.get("ma50"),
                    "gap_pct": mx.get("gap_pct"), "bias": mx.get("bias"),
                    "cross": mx.get("cross"), "trend": mr.get("trendState")}
    return {}


def _build(channel, c, side, entry, strength, direction, available, cfg, reasons, edge=None, pair=None, shared15=None):
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
    # --- SHARED-SIGNAL gate: spread AND MA-cross come from 15m only (owner rule).
    # We use the actual numeric spread change, not the text label. Other
    # timeframes are noise/too slow, so we do NOT read their spread or cross here.
    mr = c.get("marketResult") or {}
    trend = (mr.get("trendState") or c.get("trendState") or "").lower()
    if shared15 is None:
        shared15 = _shared_15m({"15m": c})     # fall back if this channel IS 15m
    change = (shared15 or {}).get("change")
    eps = float(cfg.get("spread_flat_epsilon", 0.02))   # "flat" band
    spread_gate = cfg.get("require_spread_agreement", True)
    if not spread_gate:
        pass                                  # owner: 15m spread no longer blocks entries
    elif change is not None:
        change = float(change)
        if side == "BUY" and change < -eps:
            return None                       # 15m spread genuinely against a long
        if side == "SELL" and change > eps:
            return None                       # 15m spread genuinely against the exit
    elif (shared15 or {}).get("direction"):
        d = str(shared15["direction"]).lower()
        if side == "BUY" and d == "weakening":
            return None
        if side == "SELL" and d == "improving":
            return None
    # MA20/MA50 cross gate (also 15m-trusted). Refuse BUY when 15m cross is bearish
    # and SELL when bullish — but only when a real cross value exists.
    ma_cross = (shared15 or {}).get("cross")
    ma_bias = (shared15 or {}).get("bias")
    if cfg.get("require_ma_cross_agreement", False) and ma_bias:
        if side == "BUY" and ma_bias == "bearish":
            return None
        if side == "SELL" and ma_bias == "bullish":
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
    # REAL base-tier fees from the exchange (maker for a resting limit entry, taker for
    # the market exit). Reserve for the entry uses TAKER (worst case if the limit crosses).
    try:
        import fees as _fees
        _mk, _tk = _fees.rates(pair or "DOT/USD")
        # default LIMIT (maker) for BOTH sides — cheapest; user can override per agent
        _entry_type = str(cfg.get("entry_order_type", "limit")).lower()
        fee_entry_frac = _mk if _entry_type == "limit" else _tk
        fee_exit_frac = _tk
        fee_reserve = _tk
    except Exception:
        _entry_type = "limit"
        fee_entry_frac = fee_exit_frac = fee_reserve = cfg["fee_pct_per_side"] / 100
    fee_frac = fee_entry_frac + fee_exit_frac
    slip_frac = cfg.get("slippage_pct_per_side", 0.1) / 100 * 2
    gross_move = abs(target - entry) / entry
    if gross_move <= fee_frac:
        return None
    # PRE-ESTABLISHED at buy creation: expected net P&L % if target hits, net of
    # round-trip fees + slippage. This is the "beauty" number the owner sees.
    expected_net_pct = round(gross_move * 100 - fee_frac * 100 - slip_frac * 100, 3)
    risk_quote = available * (cfg["risk_per_trade_pct"] / 100)
    # size so that NOTIONAL + entry fee never exceeds the budget; loss-at-stop includes round-trip fees
    size_quote = risk_quote / (stop_pct / 100 + fee_frac)
    size_quote = min(size_quote, available * (cfg["max_position_pct"] / 100) / (1 + fee_reserve))
    size_quote = min(size_quote, available / (1 + fee_reserve))
    size_quote = math.floor(size_quote * 100) / 100          # round DOWN to the cent, never up
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
        (f"15m spread (trusted read): change {round(change,4)} over "
         f"{(shared15 or {}).get('count')} bars · latest {(shared15 or {}).get('latest')} "
         f"(was {(shared15 or {}).get('was')})" if change is not None else
         "15m spread: no numeric read available"),
        (f"15m MA20/MA50 (trusted): {round((shared15 or {}).get('ma20') or 0,6)} vs "
         f"{round((shared15 or {}).get('ma50') or 0,6)} gap {(shared15 or {}).get('gap_pct')}% "
         f"{ma_bias or '?'} cross={ma_cross}" if (shared15 or {}).get('ma20') else
         "15m MA cross: no read available"),
    ]
    return {"channel": channel, "side": side, "entry": entry, "stop": stop, "target": target,
            "size_quote": size_quote, "risk_quote": risk_quote,
            "order_type": _entry_type, "expiry_s": int(cfg.get("entry_expiry_seconds", 600)),
            "fee_reserve_pct": round(fee_reserve * 100, 3),
            "outlay_quote": round(size_quote * (1 + fee_reserve), 4),
            "fee_entry_pct": round(fee_entry_frac * 100, 3), "fee_exit_pct": round(fee_exit_frac * 100, 3), "rr": rr, "score": score,
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
