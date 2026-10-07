"""exits.py — entry snapshots + profit-checked exits. NO STOPS (owner rule: we wait and
test the edges). A position only ever sells at a net profit after real base-tier fees.

ENTRY (every BUY): take_snapshot() stores, next to the order/position, each strategy
interval's levels + advisor price sets (sweet/perfected/median/average buy+sell) as they
were at decision time. Those are the "possible prices" for the exit.

EXIT (manage(), called every loop cycle AND every ~15s while a position is open):
  1. candidates = stored sell prices (cfg exit_price_sets, default ["average"]) that PASS
     the profit check (net >= exit_min_net_pct after entry fee paid + taker exit fee).
  2. sell when live BID >= the lowest passing candidate.            [stored target hit]
  3. else, if the strategy says exit zone (strength >= exit_strength_min) AND the live
     BID is itself profitable -> sell.                              [signal sell time]
  4. else keep watching bid/ask. Never a loss-taking sell, never a stop.
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
         "exit_strength_min": 0.7}
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
    k = pair.replace("/", "")
    k = k.replace("BTC", "XBT", 1) if k.startswith("BTC") else k
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://api.kraken.com/0/public/Ticker?pair={k}", headers={"User-Agent": "BlackHorse/1.0"}),
        timeout=10).read())
    row = next(iter(d["result"].values()))
    return float(row["b"][0]), float(row["a"][0])


def _exit_zone(pos: dict, cfg: dict):
    """Strategy says sell time: any of the agent's channels at strength >= its exit band."""
    try:
        import agents
        pol = agents.policy_for(pos.get("agent"))
        band = float(pol.get("exit_strength_min", cfg["exit_strength_min"]))
        chans = pol.get("channels") or list(STRATEGY_CHANNELS)
        import signals
        sig = signals.load(pos.get("pair"))
        if not sig:
            return False, "no signal snapshot for this pair yet"
        for n in chans:
            s = (sig.get("channels", {}).get(n) or {}).get("strength")
            if s is not None and float(s) >= band:
                return True, f"{n} strength {round(float(s), 3)} >= {band}"
        return False, "no strategy channel in exit zone"
    except Exception as e:
        return False, f"exit-zone unreadable: {e}"


def manage(live: bool = True) -> list:
    """One watch pass over every open LIVE position. Returns events (sells/state)."""
    import portfolio, execute
    cfg = _cfg()
    events, state = [], {}
    for pos in list(portfolio.open_positions()):
        if pos.get("mode") != "LIVE" or str(pos.get("side")).upper() != "BUY" or pos.get("exit_txid"):
            continue
        pair = pos["pair"]
        try:
            _, taker = _fees(pair)
            bid, ask = ticker(pair)
        except Exception as e:
            state[pos["id"]] = {"error": f"no live bid/ask: {e}"}
            continue
        try:                                   # per-agent overrides (GUI Settings tab)
            import agents as _ag
            _pol = _ag.policy_for(pos.get("agent"))
            cfg = {**cfg, **{k: _pol[k] for k in ("exit_min_net_pct", "exit_price_sets") if k in _pol}}
        except Exception:
            pass
        cands = candidates(pos, cfg, taker)
        passing = [c for c in cands if c["passes"]]
        need = price_for_net(pos, cfg["exit_min_net_pct"], taker)
        net_now = net_pct_at(pos, bid, taker)
        zone, zwhy = _exit_zone(pos, cfg)
        reason = None
        if passing and bid >= passing[0]["price"]:
            c = passing[0]
            reason = (f"stored target hit: {c['channel']}/{c['interval']} {c['set']} sell "
                      f"{c['price']} (net {c['net_pct']}%), bid {bid}")
        elif zone and net_now >= cfg["exit_min_net_pct"]:
            reason = f"exit zone ({zwhy}) and bid {bid} is profitable (net {round(net_now, 3)}%)"
        st = {"pair": pair, "agent": pos.get("agent"), "qty": pos["qty"], "entry": pos["entry"],
              "bid": bid, "ask": ask, "net_pct_if_sold_at_bid": round(net_now, 3),
              "breakeven_price": round(price_for_net(pos, 0.0, taker), 6),
              "min_profit_price": round(need, 6), "exit_zone": zone, "exit_zone_why": zwhy,
              "has_snapshot": bool(pos.get("entry_snapshot")),
              "candidates": cands, "action": "SELL" if reason else "WATCH", "why": reason}
        state[pos["id"]] = st
        if reason and live:
            res = execute.sell_position(pos["id"], reason)
            st["result"] = {k: res.get(k) for k in ("ok", "reason", "txid", "price", "net")}
            try:
                import store
                store.record_decision("exits", "SELL", pair=pair, agent=pos.get("agent"), entry=pos["entry"],
                                      reason=reason, detail={"watch": st})
            except Exception:
                pass
            events.append({"position": pos["id"], "reason": reason, **st["result"]})
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
