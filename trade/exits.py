"""exits.py — entry snapshots + profit-checked exits + OPT-IN stop loss.

House rule stays the DEFAULT: NO stops — we wait and test the edges, and a position
only sells at a net profit after real base-tier fees. The stop loss is an explicit,
per-agent opt-in (GUI Settings tab -> Stop loss).

ENTRY (every BUY): take_snapshot() stores, next to the order/position, each strategy
interval's levels + advisor price sets (sweet/perfected/median/average buy+sell) as they
were at decision time. Those are the "possible prices" for the exit.

EXIT (manage(), called every loop cycle AND every ~15s while a position is open):
  A. profit exit (unchanged): candidates = stored sell prices (cfg exit_price_sets, default
     ["average"]) that PASS the profit check (net >= exit_min_net_pct after entry fee paid +
     taker exit fee); sell when live BID >= the lowest passing candidate, or when the
     strategy is in its exit zone and the live bid is itself profitable.
  B. optional stop loss (agent policy stop_loss_enabled): when ON, a protective stop is
     watched. Both modes REST A LIMIT (maker, never cross) instead of dumping at market:
       * price_watch — fixed stop level (flat stop_loss_pct if set, else the decider's
         structural pos["stop"]); when the bid comes within watch_pct% of it, rest the limit.
       * trailing    — the level rides up with price (distance = trail_pct% of ENTRY, never
         looser than the structural stop); same limit-arming rule.
     A flagged, still-gated profit exit WINS: manage() cancels the resting stop limit and
     rests the profit exit instead.
State is written to logs/exit_watch.json so you can see every number.

CLI: python3 trade/exits.py        # one watch pass, prints state (no orders unless --live)
"""
import json, sys, time, urllib.request
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade")); sys.path.insert(0, str(BH / "memory"))
SIGNAL = BH / "logs" / "signal_latest.json"
WATCH = BH / "logs" / "exit_watch.json"
STRATEGY_CHANNELS = ("ripple", "wave", "tide")      # 15m / 60m / 240m lanes


def _cfg() -> dict:
    d = {"exit_price_sets": ["average"], "exit_min_net_pct": 0.25, "exit_poll_seconds": 15,
         "exit_strength_min": 0.7,
         "stop_loss_enabled": False, "stop_mode": "price_watch",
         "stop_loss_pct": None, "trail_pct": None, "watch_pct": 0.5}
    try:
        return {**d, **json.loads((BH / "config" / "decider.json").read_text())}
    except (OSError, json.JSONDecodeError):
        return d


def _sets(adv: dict) -> dict:
    p = dict((adv or {}).get("prices") or {})
    if "average" not in p and "averge" in p:        # SignalBot's typo'd key
        p["average"] = p["averge"]
    p.pop("averge", None)
    return p


def take_snapshot(agent: str = None, entry_channel: str = None, signal: dict = None, pair: str = None) -> dict:
    """Compact picture of every strategy interval at decision time (for THIS pair)."""
    import signals
    sig = signal or signals.load(pair)
    if not sig:
        return {"ts": time.time(), "channels": {}, "note": "no signal available"}
    chans = sig.get("channels") or {}
    want = list(STRATEGY_CHANNELS)
    try:
        import agents
        for c in (agents.policy_for(agent).get("channels") or []):
            if c not in want:
                want.append(c)
    except Exception:
        pass
    out = {}
    for name in want:
        c = chans.get(name)
        if not c:
            continue
        adv = c.get("advisor") or {}
        out[name] = {"interval": c.get("interval"), "curr": c.get("currPrice"),
                     "strength": c.get("strength"), "direction": c.get("strengthDirection"),
                     "action": c.get("action"), "range": c.get("rangeSignal"),
                     "entry_eligible": c.get("entryEligible"),
                     "levels": {k: c.get(k) for k in ("floor", "low", "high", "roof")},
                     "advisor": {"buy": adv.get("buy"), "sell": adv.get("sell"), "sets": _sets(adv)}}
    return {"ts": time.time(), "signal_ts": sig.get("ts"), "pair": sig.get("pair"),
            "agent": agent, "entry_channel": entry_channel,
            "strategy_intervals": sorted({v["interval"] for v in out.values() if v.get("interval")}),
            "channels": out}


def _fees(pair):
    import fees
    return fees.rates(pair)


def cost_basis(pos: dict) -> float:
    return float(pos["entry"]) * float(pos["qty"]) + float(pos.get("fee_entry") or 0)


def net_pct_at(pos: dict, price: float, taker: float) -> float:
    cost = cost_basis(pos)
    proceeds = float(price) * float(pos["qty"]) * (1 - taker)
    return (proceeds - cost) / cost * 100


def price_for_net(pos: dict, net_pct: float, taker: float) -> float:
    """The sell price that yields exactly net_pct after the entry fee paid + taker exit."""
    return cost_basis(pos) * (1 + net_pct / 100) / (float(pos["qty"]) * (1 - taker))


def candidates(pos: dict, cfg: dict, taker: float) -> list:
    """Every stored sell price, with its profit verdict."""
    snap = pos.get("entry_snapshot") or {}
    out = []
    for name, ch in (snap.get("channels") or {}).items():
        sets = ((ch.get("advisor") or {}).get("sets")) or {}
        for sname in cfg["exit_price_sets"]:
            sp = (sets.get(sname) or {}).get("sell")
            if sp in (None, 0):
                continue
            n = net_pct_at(pos, float(sp), taker)
            out.append({"channel": name, "interval": ch.get("interval"), "set": sname,
                        "price": float(sp), "net_pct": round(n, 3), "passes": n >= cfg["exit_min_net_pct"]})
    return sorted(out, key=lambda x: x["price"])


def ticker(pair: str):
    """Public (bid, ask) on the ACTIVE exchange (via venues)."""
    import venues
    return venues.ticker(pair)


def _exit_zone(pos: dict, cfg: dict):
    """Strategy says sell time: check exit zone respecting entry channel (avoid premature TF exits)."""
    try:
        import agents
        pol = agents.policy_for(pos.get("agent"))
        band = float(pol.get("exit_strength_min", cfg["exit_strength_min"]))
        chans = pol.get("channels") or list(STRATEGY_CHANNELS)
        import signals
        sig = signals.load(pos.get("pair"))
        if not sig:
            return False, "no signal snapshot for this pair yet"
        sc = (pos.get("entry_snapshot") or {})
        try:
            if isinstance(sc, str):
                import json as _json
                sc = _json.loads(sc)
        except Exception:
            sc = {}
        entry_ch = sc.get("entry_channel") or sc.get("channel")
        # Prefer entry channel first
        order = []
        if entry_ch:
            order.append(entry_ch)
        for n in chans:
            if n not in order:
                order.append(n)
        # For swing-style (no 5m), avoid treating very short TF (ripple5/wave15 variants?) but also ripple can be noisy; prefer entry + higher/entry-aligned
        for n in order:
            c = (sig.get("channels", {}).get(n) or {})
            s = c.get("strength")
            if s is not None and float(s) >= band:
                return True, f"{n} strength {round(float(s), 3)} >= {band}"
        return False, "no strategy channel in exit zone"
    except Exception as e:
        return False, f"exit-zone unreadable: {e}"


# --------------------------------------------------------------- stop loss (opt-in)
def _stop_cfg(cfg: dict) -> dict:
    """Normalised stop settings for one agent (decider defaults + policy overrides)."""
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    mode = str(cfg.get("stop_mode") or "price_watch").lower()
    if mode not in ("trailing", "price_watch"):
        mode = "price_watch"
    watch = _f(cfg.get("watch_pct"))
    return {"stop_loss_enabled": bool(cfg.get("stop_loss_enabled")),
            "stop_mode": mode,
            "stop_loss_pct": _f(cfg.get("stop_loss_pct")),
            "trail_pct": _f(cfg.get("trail_pct")),
            "watch_pct": watch if watch is not None else 0.5}


def _stop_level(pos: dict, sc: dict):
    """The allowed stop loss -> (price, source). A flat stop_loss_pct overrides the
    decider's structural pos["stop"] when the user set one."""
    entry = float(pos["entry"])
    if sc.get("stop_loss_pct") and sc["stop_loss_pct"] > 0:
        return entry * (1 - sc["stop_loss_pct"] / 100), "flat"
    try:
        s = float(pos.get("stop"))
    except (TypeError, ValueError):
        s = None
    return (s, "structural") if s else (None, "none")


def _stop_eval(pos: dict, sc: dict, bid: float) -> dict:
    """Pure read of the stop for this pass: level, proximity/trigger, and (trailing)
    the new high-water mark. Never touches disk or places orders."""
    if not sc.get("stop_loss_enabled"):
        return {}
    entry = float(pos["entry"])
    sl, src = _stop_level(pos, sc)
    watch = float(sc.get("watch_pct") or 0.5)
    out = {"enabled": True, "mode": sc["stop_mode"], "stop": sl, "stop_src": src,
           "watch_pct": watch, "action": "watching"}
    if sl is None:
        out["note"] = "no stop level available (no structural stop and no flat stop_loss_pct)"
        out["action"] = "no-stop"
        return out
    if sc["stop_mode"] == "trailing":
        high = max(float(pos.get("trail_high") or entry), float(bid))
        tpct = sc.get("trail_pct")
        if not tpct or tpct <= 0:                       # fall back to the initial stop distance
            tpct = max(0.0, (entry - sl) / entry * 100) if sl < entry else 1.0
        dist = entry * tpct / 100                        # % of ENTRY, anchored at entry
        level = max(high - dist, sl)                     # never looser than the structural stop
        out.update({"trail_high": round(high, 10), "trail_pct": round(tpct, 4),
                    "dist": round(dist, 10)})
    else:
        level = sl
    arm = level * (1 + watch / 100)
    out.update({"level": round(level, 10), "arm_level": round(arm, 10),
                "near": bid <= arm, "triggered": bid <= level, "fill_price": round(level, 10)})
    if out["triggered"]:
        if sc["stop_mode"] == "trailing":
            out["trigger_reason"] = (f"trailing stop {round(level, 8)} hit "
                                     f"(high {out.get('trail_high', entry)}, "
                                     f"{out.get('trail_pct')}% of entry), bid {bid}")
        else:
            out["trigger_reason"] = f"stop watch {round(sl, 8)} hit, bid {bid}"
    return out


def _set_pos_field(pos_id: str, key: str, value, remove: bool = False):
    """Persist one field on an open position in portfolio.json."""
    try:
        import portfolio
        d = portfolio._load()
        for p in d["positions"]:
            if p["id"] == pos_id:
                if remove:
                    p.pop(key, None)
                else:
                    p[key] = value
        portfolio._save(d)
    except Exception:
        pass


def _record_exit(pair, agent, entry, reason, st):
    try:
        import store
        store.record_decision("exits", "SELL", pair=pair, agent=agent, entry=entry,
                              reason=reason, detail={"watch": st})
    except Exception:
        pass


def _exit_now(portfolio, execute, pos, pair, mode, reason, stop, need, ask, st, events):
    """Execute a flagged exit: LIVE rests the maker limit; PAPER closes at the level."""
    pid = pos["id"]
    if mode == "LIVE":
        if pos.get("exit_txid"):                         # a resting stop was armed -> drop it
            try:
                execute.cancel(pos["exit_txid"], pair)
            except Exception:
                pass
            _set_pos_field(pid, "exit_txid", None, remove=True)
        is_stop = bool(stop.get("trigger_reason")) and not st.get("profit_exit")
        px = ask if is_stop else max(need, ask)
        res = execute.sell_position(pid, reason, price=px,
                                    kind=("stop" if is_stop else "profit"),
                                    level=stop.get("level"))
        st["result"] = {k: res.get(k) for k in ("ok", "reason", "txid", "price", "net",
                                                "pending", "maker", "volume")}
        _record_exit(pair, pos.get("agent"), pos["entry"], reason, st)
        events.append({"position": pid, "reason": reason, **st["result"]})
    else:
        # PAPER: fill near the market, never at a fantasy level. If price is already
        # below the stop (gapped through), take the bid; otherwise the stop level is fine.
        lvl = stop.get("fill_price")
        px = float(st.get("bid"))
        if lvl is not None:
            px = min(float(lvl), px)
        rec = portfolio.close_position(pid, px, reason=reason)
        st["result"] = {"ok": rec.get("ok"), "price": px, "net": rec.get("net")}
        _record_exit(pair, pos.get("agent"), pos["entry"], reason, st)
        events.append({"position": pid, "reason": reason, "price": px, "net": rec.get("net")})


def _manage_stop_limit(portfolio, execute, pos, pair, stop, st):
    """Keep a resting maker stop limit in step with price: arm when near, withdraw
    when price recovers out of the band. (LIVE only — paper closes directly.)"""
    pid = pos["id"]
    armed = pos.get("exit_txid")
    near = stop.get("near")
    if not near and not armed:
        st["stop"]["action"] = "watching"
    elif near and not armed:
        res = execute.sell_position(pid, reason=f"{stop['mode']} stop armed @ {stop.get('level')}",
                                    price=stop.get("level"), kind="stop", level=stop.get("level"))
        st["stop"]["action"] = "armed" if res.get("ok") else "arm-failed"
        st["stop"]["arm_result"] = {k: res.get(k) for k in ("ok", "reason", "txid", "price", "maker")}
    elif armed and not near:                             # price recovered -> withdraw
        try:
            execute.cancel(armed, pair)
        except Exception:
            pass
        _set_pos_field(pid, "exit_txid", None, remove=True)
        st["stop"]["action"] = "withdrawn"
    else:
        st["stop"]["action"] = "held"


def manage(live: bool = True) -> list:
    """One watch pass over every open LONG position. Returns events (sells/state).

    Profit exits behave exactly as before. When the owning agent has the opt-in stop
    loss enabled, a protective stop is watched too — resting a maker LIMIT (never a
    market dump) whenever price nears it. A still-gated, flagged profit exit always
    wins and cancels the resting stop. See the module docstring for the two modes."""
    import portfolio, execute
    base = _cfg()
    pass_keys = ("exit_min_net_pct", "exit_price_sets", "stop_loss_enabled", "stop_mode",
                 "stop_loss_pct", "trail_pct", "watch_pct")
    events, state = [], {}
    for pos in list(portfolio.open_positions()):
        if str(pos.get("side")).upper() != "BUY":
            continue
        pair = pos["pair"]
        mode = pos.get("mode")
        cfg = base
        sc = _stop_cfg(base)
        try:                                   # per-agent overrides (GUI Settings tab)
            import agents as _ag
            _pol = _ag.policy_for(pos.get("agent"))
            cfg = {**base, **{k: _pol[k] for k in pass_keys if k in _pol}}
            sc = _stop_cfg(cfg)
        except Exception:
            pass
        try:
            _, taker = _fees(pair)
            bid, ask = ticker(pair)
        except Exception as e:
            state[pos["id"]] = {"error": f"no live bid/ask: {e}"}
            continue
        cands = candidates(pos, cfg, taker)
        passing = [c for c in cands if c["passes"]]
        need = price_for_net(pos, cfg["exit_min_net_pct"], taker)
        net_now = net_pct_at(pos, bid, taker)
        zone, zwhy = _exit_zone(pos, cfg)

        # A. profit exit — LIVE only; PAPER profit exits are handled by portfolio.mark
        profit_reason = None
        if mode == "LIVE":
            if passing and bid >= passing[0]["price"]:
                c = passing[0]
                profit_reason = (f"stored target hit: {c['channel']}/{c['interval']} {c['set']} sell "
                                 f"{c['price']} (net {c['net_pct']}%), bid {bid}")
            elif zone and net_now >= cfg["exit_min_net_pct"]:
                profit_reason = f"exit zone ({zwhy}) and bid {bid} is profitable (net {round(net_now, 3)}%)"

        # B. opt-in stop loss
        stop = _stop_eval(pos, sc, bid)
        stop_reason = stop.get("trigger_reason")

        st = {"pair": pair, "agent": pos.get("agent"), "mode": mode, "qty": pos["qty"],
              "entry": pos["entry"], "bid": bid, "ask": ask,
              "net_pct_if_sold_at_bid": round(net_now, 3),
              "breakeven_price": round(price_for_net(pos, 0.0, taker), 6),
              "min_profit_price": round(need, 6), "exit_zone": zone, "exit_zone_why": zwhy,
              "has_snapshot": bool(pos.get("entry_snapshot")),
              "candidates": cands}
        if stop:
            st["stop"] = stop
        reason = profit_reason or stop_reason
        st["profit_exit"] = bool(profit_reason)
        st["action"] = "SELL" if reason else "WATCH"
        st["why"] = reason
        state[pos["id"]] = st

        # remember the trailing high-water mark so the stop only ever ratchets up
        if stop.get("trail_high") is not None and pos.get("trail_high") != stop["trail_high"]:
            _set_pos_field(pos["id"], "trail_high", stop["trail_high"])

        if not live:
            continue
        if reason:
            # a stop that is already resting stays put — sync_exits() re-prices/releases
            # it on its own requote timer, so we must not cancel-and-replace every pass.
            resting = bool(stop_reason) and not profit_reason and pos.get("exit_txid")
            if resting:
                st["action"] = "held"
            else:
                _exit_now(portfolio, execute, pos, pair, mode, reason, stop, need, ask, st, events)
        elif mode == "LIVE" and stop.get("enabled") and stop.get("stop") is not None:
            _manage_stop_limit(portfolio, execute, pos, pair, stop, st)

    try:
        WATCH.write_text(json.dumps({"ts": time.time(), "positions": state}, indent=2))
    except OSError:
        pass
    return events


if __name__ == "__main__":
    ev = manage(live="--live" in sys.argv)
    print(json.dumps(json.loads(WATCH.read_text()), indent=2))
    if ev:
        print("EVENTS", json.dumps(ev))
