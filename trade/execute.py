"""execute.py — turn an approved plan into a real (paper) fill.

Default mode is PAPER: fills at the plan's entry +/- slippage, charges fees,
opens a position in portfolio.py, and debits treasury when the trade closes
(or reserves risk while open). Live mode is refused until Heart's unlock passes.

Nothing bypasses Heart: execute() re-checks Heart before doing anything.

CLI:
  python3 trade/execute.py --plan            # broker decision -> paper fill
  python3 trade/execute.py --mark DOT/USD 1.19
  python3 trade/execute.py --close pos_123 1.19
"""
import json, os, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))
import venues   # the exchange adapter layer (kraken / binance / coinbase)
SLIPPAGE_PCT_PER_SIDE = 0.1
FEE_PCT_PER_SIDE = 0.80          # conservative taker fallback; real value from fees.rates()


def _entry_fee_pct(pair: str = "DOT/USD", order_type: str = "market") -> float:
    """Real per-side fee (percent) for a paper entry: maker for a resting limit,
    taker otherwise. Sourced from fees.py (snapshot/account tier); 0.80 fallback."""
    try:
        import fees
        mk, tk = fees.rates(pair or "DOT/USD")
        return (mk if str(order_type).lower() == "limit" else tk) * 100.0
    except Exception:
        return FEE_PCT_PER_SIDE


def _norm_pair(raw: str) -> str:
    """'dot' -> 'DOT/USD', 'DOTUSD' -> 'DOT/USD', 'BTCUSD' -> 'BTC/USD', 'DOT/USD' -> itself."""
    up = raw.strip().upper()
    if "/" in up:
        return up
    if up.endswith("USDT") and len(up) > 4:
        return up[:-4] + "/USDT"
    if up.endswith("USD") and len(up) > 3:
        return up[:-3] + "/USD"
    # bare base symbol like DOT / BTC / ETH
    return f"{up}/USD"


def cancel(txid: str, pair: str = None) -> dict:
    """Cancel a resting order on the active exchange (order id is native to it)."""
    try:
        r = venues.cancel(txid, pair)
    except Exception as e:
        return {"ok": False, "reason": f"cancel error: {e}"}
    if not r.get("ok"):
        return {"ok": False, "reason": r.get("error")}
    return {"ok": True, "result": r.get("result")}


def manual_order(text: str):
    """Parse: create order BUY|SELL DOT/USD market 5
              create order BUY|SELL DOT/USD limit 1.20 5
              cancel order <txid>
    Returns a friendly string result, or None if not a manual-order command."""
    import re
    t = text.strip().lower()
    m = re.match(r"create order (buy|sell) (\S+) (market|limit) ([0-9.]+)(?: ([0-9.]+))?", t)
    if m:
        side, pair_raw, typ, p1, p2 = m.group(1), m.group(2), m.group(3), float(m.group(4)), (float(m.group(5)) if m.group(5) else None)
        pair = _norm_pair(pair_raw)
        plan = {"action": side.upper(), "order_type": typ, "entry": p1,
                "size_quote": p2 or p1}
        if typ == "limit":
            plan["price"] = p1
        try:
            import execute as _self
            res = _self.execute(plan, pair=pair)
            return f"manual order -> {res}"
        except Exception as e:
            return f"manual order error: {e}"
    m = re.match(r"cancel order (\S+)", text.strip(), flags=re.IGNORECASE)
    if m:
        return f"cancel -> {cancel(m.group(1))}"

    return None


def _trading_cfg() -> dict:
    try:
        return json.loads((Path.home() / "blackhorse" / "config" / "trading.json").read_text())
    except (json.JSONDecodeError, OSError):
        return {"mode": "PAPER", "autonomous": False}


def _mode() -> str:
    """Explicit mode from config: PAPER unless the owner deliberately set LIVE.
    Autonomy being ON does NOT imply live — those are separate switches. The
    validation gate is surfaced as a WARNING (see gate_warning), not a silent
    override of the owner's explicit LIVE choice."""
    cfg = _trading_cfg()
    return "LIVE" if str(cfg.get("mode", "PAPER")).upper() == "LIVE" else "PAPER"


def gate_warning() -> str:
    """Honest quality signal: is the validation gate actually passed? Empty = ok."""
    try:
        import heart
        u = heart.unlock()
        if not u.get("can_arm"):
            det = u.get("detail", {})
            return "validation gate OPEN (unproven): " + "; ".join(
                f"{k}={v}" for k, v in det.items()) if det else \
                "validation gate not passed: " + ", ".join(u.get("missing", []))
    except Exception:
        pass
    return ""


def autonomous() -> bool:
    return bool(_trading_cfg().get("autonomous", False))


def execute(plan: dict, pair: str = None, mode: str = None) -> dict:
    import heart, portfolio, treasury
    mode = mode or plan.get("mode") or _mode()
    if plan.get("action") not in ("BUY", "SELL"):
        return {"ok": False, "skipped": True, "reason": f"action {plan.get('action')} not executable"}

    # Heart must allow (fail-closed). Re-check here, not just in broker.
    # manual=True (a direct owner/Spice order) skips the autonomous dead-man but
    # still enforces spot-only, caps, and daily-loss.
    holding = _held(pair, mode, plan)
    hv = heart.check(action=plan["action"], quote=plan.get("size_quote", 0) or 0,
                     venue=heart_venue(), positions=len(portfolio.open_positions()),
                     holding_qty=holding, manual=bool(plan.get("manual")))
    if hv["decision"] != "ALLOW":
        return {"ok": False, "blocked": True, "reason": f"heart {hv['decision']}: {hv['reason']}"}
    if mode == "LIVE":
        try:
            return _live_order(plan, pair)
        except Exception as e:
            return {"ok": False, "blocked": True, "reason": f"live error: {e}"}

    entry = float(plan["entry"])
    size_quote = float(plan.get("size_quote", 0))
    if entry <= 0 or size_quote <= 0:
        return {"ok": False, "reason": "invalid entry/size"}
    # paper slippage
    fill = entry * (1 + SLIPPAGE_PCT_PER_SIDE / 100) if plan["action"] == "BUY" \
        else entry * (1 - SLIPPAGE_PCT_PER_SIDE / 100)
    qty = size_quote / fill
    agent = plan.get("agent", "house")
    ch = plan.get("channel") or ""
    interval = {"ripple": "60m", "wave": "240m", "tide": "1440m", "wave15": "15m", "ripple5": "5m", "tide60": "60m"}.get(str(ch).lower(), None)
    edge_cell = f"{interval or '?'}{plan['action']}"
    pos = portfolio.open_position(agent=agent, pair=pair or "DOT/USD", side=plan["action"],
                                  entry=fill, qty=qty, stop=plan.get("stop"),
                                  target=plan.get("target"),
                                  fee_pct=_entry_fee_pct(pair, plan.get("order_type", "market")),
                                  venue=heart_venue(), note=f"{mode} fill", mode=mode,
                                  interval=interval, edge_cell=edge_cell)
    # record the DB trail (idempotent order, fill, position) — non-fatal if absent
    try:
        import store
        src = "spice" if plan.get("manual") else ("agent:" + str(agent))
        o = store.create_order(source=src, pair=pair or "DOT/USD", side=plan["action"],
                               order_type=plan.get("order_type", "market"),
                               price=plan.get("entry"), amount_quote=size_quote,
                               agent=agent, mode=mode, reason=plan.get("reason"))
        oid = (o.get("order") or {}).get("id")
        if oid:
            store.set_order_status(oid, "FILLED", raw={"mode": mode, "fill": fill})
            store.add_fill(oid, fill, qty, fee=pos.get("fee_entry", 0.0), fee_currency="USD")
        store.open_position(pos["id"], agent, pair or "DOT/USD", plan["action"], fill, qty,
                            stop=plan.get("stop"), target=plan.get("target"), mode=mode,
                            interval=interval, edge_cell=edge_cell)
    except Exception:
        pass
    # reserve risk from treasury (so available shrinks while position is open)
    try:
        cur = treasury.status()
        treasury.set_open_risk(round(cur.get("open_risk", 0) + float(plan.get("risk_quote", 0)), 8))
    except Exception:
        pass
    heart.heartbeat()  # execution is alive
    return {"ok": True, "mode": mode, "position": pos, "fill": fill, "qty": qty}


def heart_venue():
    try:
        import exchanges
        return exchanges.active_exchange()["name"]
    except Exception:
        return None


def _held(pair: str, mode: str, plan: dict) -> float:
    """Base-asset quantity available to sell. LIVE asks the exchange; PAPER uses
    the tracked portfolio. This is what lets a spot SELL be legal (not a short)."""
    if str(plan.get("action")).upper() != "SELL":
        return 0.0
    base = _norm_pair(pair or "DOT/USD").split("/")[0].upper()
    if mode == "LIVE":
        try:
            return float(kraken_balance().get(base, 0))
        except Exception:
            return 0.0
    try:
        import portfolio
        return sum(float(p.get("qty") or 0) for p in portfolio.open_positions()
                   if _norm_pair(p.get("pair", "")).split("/")[0].upper() == base)
    except Exception:
        return 0.0


_LAST_NONCE = 0
_BAL = {"ts": 0.0, "v": None}


def invalidate_balance():
    venues.invalidate_balance()


def _kraken_private(path: str, body: dict) -> dict:
    """Kraken-only signed private request — kept solely for store.backfill_from_kraken.
    The whole live path now goes through venues.py."""
    return venues.get("kraken")._private(path, body)


def kraken_balance() -> dict:
    """Balances available to sell on the active exchange, keyed by base symbol.
    Name kept for existing callers. {} means UNKNOWN — never read it as zero funds."""
    try:
        return venues.balance()
    except Exception:
        return {}


def kraken_last_price(pair: str) -> float | None:
    """Last price on the active exchange (name kept for existing callers)."""
    return venues.last_price(pair)


def _kpair(pair: str) -> str:
    """Native symbol on the active exchange (name kept for existing callers)."""
    return venues.native(pair)


def _ticker(pair: str):
    """Public (bid, ask) for a pair on the active exchange."""
    return venues.ticker(pair)


def kraken_fill(txid: str, pair: str = None, tries: int = 6) -> dict | None:
    """Actual fill of an order on the active exchange: {price, vol, cost, fee}.
    The exchange is the truth, not the plan. Returns None if it can't be read."""
    import time
    for _ in range(tries):
        try:
            o = venues.order_status(txid, pair)
            if o and o.get("status") == "closed" and float(o.get("vol_exec") or 0) > 0:
                return {"price": float(o["price"]), "vol": float(o["vol_exec"]),
                        "cost": float(o.get("cost") or 0), "fee": float(o.get("fee") or 0)}
        except Exception:
            pass
        time.sleep(1.5)
    return None


PENDING = BH / "logs" / "pending_orders.json"


def _pend_load() -> list:
    try:
        return json.loads(PENDING.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _pend_save(lst: list):
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(lst, indent=2))


def pending_outlay() -> float:
    """Cash (cost + reserved fee) locked in resting entry orders."""
    return round(sum(float(p.get("outlay") or 0) for p in _pend_load()), 8)


def has_pending(agent: str, pair: str) -> bool:
    return any(p.get("agent") == agent and p.get("pair") == pair for p in _pend_load())


EXITS = BH / "logs" / "pending_exits.json"


def _exit_load() -> list:
    try:
        return json.loads(EXITS.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _exit_save(lst: list):
    EXITS.parent.mkdir(parents=True, exist_ok=True)
    EXITS.write_text(json.dumps(lst, indent=2))


def _ceil_dex(x: float, decimals: int) -> float:
    q = 10 ** int(decimals)
    import math
    return math.ceil(float(x) * q - 1e-9) / q


def sync_pending() -> list:
    """Reconcile resting limit entries with the active exchange. FILLED (or partially
    filled then expired) -> open the position at the REAL fill. Expired unfilled -> drop.
    Open past expiry -> cancel. The exchange is the truth. Returns the events."""
    import time, portfolio
    pend = _pend_load()
    if not pend:
        return []
    try:
        res = venues.orders([(p["txid"], p.get("pair")) for p in pend])
    except Exception:
        return []
    keep, events = [], []
    for p in pend:
        o = res.get(p["txid"])
        if not o:
            keep.append(p); continue
        st = o.get("status")
        vol = float(o.get("vol_exec") or 0)
        try:
            import store
            if st in ("closed", "canceled", "expired"):
                store.live_order_result(p["txid"], "FILLED" if st == "closed" else
                                        ("PARTIAL" if vol > 0 else st.upper()), vol,
                                        float(o.get("price") or 0) or None, float(o.get("fee") or 0))
        except Exception:
            pass
        if st in ("pending", "open"):
            if time.time() > float(p.get("expires", 0)) + 90:
                try:
                    venues.cancel(p["txid"], p.get("pair"))
                except Exception:
                    pass
            keep.append(p); continue
        if vol > 0:
            pos = portfolio.open_position(agent=p["agent"], pair=p["pair"], side="BUY",
                                          entry=float(o["price"]), qty=vol, stop=p.get("stop"),
                                          target=p.get("target"), venue=heart_venue(),
                                          note=f"LIVE limit buy {p['txid']} ({st})", mode="LIVE",
                                          fee=float(o.get("fee") or 0),
                                          entry_snapshot=p.get("snapshot"))
            events.append({"txid": p["txid"], "result": "filled" if st == "closed" else f"partial/{st}",
                           "qty": vol, "price": float(o["price"]), "position": pos["id"]})
        else:
            events.append({"txid": p["txid"], "result": f"expired-unfilled/{st}",
                           "limit": p.get("price"), "agent": p["agent"]})
    _pend_save(keep)
    for e in events:
        try:
            portfolio._log({"type": "entry_" + e["result"].split("/")[0], **e})
        except Exception:
            pass
    return events


def _live_order(plan: dict, pair: str) -> dict:
    """Place a REAL Kraken order. BUY: sized so notional + BASE-tier fee <= cash
    available (never overshoots); default a LIMIT at the plan's entry with an expiry,
    tracked as pending until it actually fills. SELL: market, sells held inventory."""
    import time, portfolio, fees
    pair = _norm_pair(pair or "DOT/USD")
    side = "buy" if plan["action"] == "BUY" else "sell"
    # default LIMIT (maker) for both buy and sell; explicit market only if the user asks
    otype = str(plan.get("order_type") or "limit").lower()
    if otype not in ("limit", "market"):
        otype = "limit"
    price = plan.get("price") or plan.get("entry")
    base = pair.split("/")[0].upper()
    info = fees.pair_info(pair)
    expire = None

    if plan["action"] == "SELL" and not plan.get("manual"):
        return {"ok": False, "blocked": True,
                "reason": "autonomous exits are handled by exits.py (profit-checked, no stops)"}
    if plan["action"] == "SELL":
        # SPOT: sell only what we actually hold.
        bals = kraken_balance()
        held = float(bals.get(base, 0))
        if held <= 0:
            return {"ok": False, "blocked": True, "reason": f"spot-only: no {base} inventory to sell"}
        volume = fees.floor_to(held * 0.999, info["lot_decimals"])   # keep a hair for fees
        if otype == "limit":
            if not price or float(price) <= 0:
                return {"ok": False, "blocked": True,
                        "reason": "limit sell needs a price (or set order_type=market)"}
            price = round(float(price), info["pair_decimals"])
            expire = int(plan.get("expiry_s") or 600)
    else:
        if plan.get("agent") and has_pending(plan["agent"], pair):
            return {"ok": False, "blocked": True, "reason": "an entry order is already working for this agent/pair"}
        quote = pair.split("/")[1].upper()
        quote_like = ("USD", "USDT", "USDC", "EUR", "CAD", "GBP")
        bals = kraken_balance()
        # sum every quote-like holding (Binance settles in USDT, Coinbase in USD, ...)
        cash = sum(float(v) for k, v in bals.items() if k in quote_like) if quote in quote_like else 0.0
        cash -= pending_outlay()                     # funds already locked in resting orders
        if cash <= 0:
            return {"ok": False, "blocked": True, "reason": f"no {quote} cash available to buy"}
        maker, taker = fees.rates(pair)
        reserve = taker                              # worst case: the limit crosses and fills as taker
        want = float(plan.get("size_quote", 0))      # NOTIONAL
        try:
            import capital
            want = min(want, capital.free() / (1 + reserve))     # shared pool, fee-inclusive
        except Exception:
            pass
        want = min(want, cash / (1 + reserve))       # cost + fee can never exceed real cash
        if not price or float(price) <= 0:
            return {"ok": False, "blocked": True, "reason": "no entry price"}
        price = round(float(price), info["pair_decimals"])
        volume = fees.floor_to(want / price, info["lot_decimals"])
        if volume < info.get("ordermin", 0) or volume * price < info.get("costmin", 0):
            return {"ok": False, "blocked": True,
                    "reason": f"size too small after fees: {volume} {base} (min {info.get('ordermin')})"}
        if otype == "limit":
            expire = int(plan.get("expiry_s") or 600)

    snapshot = None
    if plan["action"] == "BUY":
        try:
            import exits
            snapshot = exits.take_snapshot(plan.get("agent"), plan.get("channel"), pair=pair)
        except Exception:
            snapshot = None
    if os.environ.get("BH_VALIDATE_ONLY"):
        return {"ok": True, "validated": True,
                "body": {"pair": pair, "type": side, "ordertype": otype, "volume": str(volume),
                         **({"price": str(price), "expiretm": f"+{expire}"} if otype == "limit" and price is not None else {})}}
    res = venues.place_order(pair, side, otype, volume, price, expire)
    invalidate_balance()
    if not res.get("ok"):
        return {"ok": False, "blocked": True, "reason": f"order rejected: {res.get('error')}"}
    txid = res.get("order_id")
    try:
        import store
        store.live_order(txid, plan.get("agent", "house"), pair, side, otype, price if otype == "limit" else None,
                         volume, outlay=round(volume * float(price or 0) * (1 + (reserve if side == "buy" else 0)), 8)
                         if price else None, expires=(time.time() + expire) if expire else None,
                         plan=plan, snapshot=snapshot)
    except Exception:
        pass
    if plan["action"] == "BUY":
        if otype == "limit":
            pend = _pend_load()
            pend.append({"txid": txid, "agent": plan.get("agent", "house"), "pair": pair, "price": price,
                         "volume": volume, "stop": plan.get("stop"), "target": plan.get("target"),
                         "outlay": round(volume * price * (1 + reserve), 8), "placed": time.time(),
                         "expires": time.time() + expire, "snapshot": snapshot})
            _pend_save(pend)
            return {"ok": True, "mode": "LIVE", "txid": txid, "pending": True, "price": price,
                    "volume": volume, "expires_in_s": expire, "result": res.get("result")}
        fill = float(price) or (kraken_last_price(pair) or 0)
        qty_rec, fee_rec = volume, None
        real = kraken_fill(txid, pair) if txid else None
        if real:                                  # record what the exchange actually did
            fill, qty_rec, fee_rec = real["price"], real["vol"], real["fee"]
            try:
                import store
                store.live_order_result(txid, "FILLED", real["vol"], real["price"], real["fee"])
            except Exception:
                pass
        pos = portfolio.open_position(agent=plan.get("agent", "house"), pair=pair, side="BUY",
                                      entry=fill, qty=qty_rec, stop=plan.get("stop"),
                                      target=plan.get("target"), venue=heart_venue(),
                                      note=f"LIVE buy {txid}" + ("" if real else " (fill unverified)"),
                                      mode="LIVE", fee=fee_rec, entry_snapshot=snapshot)
    else:
        opened = [p for p in portfolio.open_positions()
                  if p.get("pair") == pair and str(p.get("side")).upper() == "BUY"]
        if opened:
            last = kraken_last_price(pair) or float(price or 0)
            real = kraken_fill(txid, pair) if txid else None
            try:
                import store
                if real:
                    store.live_order_result(txid, "FILLED", real["vol"], real["price"], real["fee"])
            except Exception:
                pass
            portfolio.close_position(opened[0]["id"], real["price"] if real else last,
                                     reason="live exit", fee=real["fee"] if real else None)
    return {"ok": True, "mode": "LIVE", "txid": txid, "volume": volume, "result": res.get("result")}


def sell_position(pos_id: str, reason: str, price: float = None,
                  kind: str = "profit", level: float = None) -> dict:
    """LIMIT-ONLY exit: rest a maker sell at/above the best ask and earn the maker fee.
    It never crosses (no taker fee). sync_exits() settles the fill and chases the price.
    `kind="stop"` marks a protective-stop limit: on requote it is released (not chased to
    the profit minimum) so exits.manage() can re-arm it at the current stop level."""
    import time, portfolio, fees, heart
    pos = next((p for p in portfolio.open_positions() if p["id"] == pos_id), None)
    if not pos or pos.get("exit_txid"):
        return {"ok": False, "reason": "position not open / already selling"}
    pair = _norm_pair(pos["pair"])
    base = pair.split("/")[0].upper()
    info = fees.pair_info(pair)
    held = float(kraken_balance().get(base, 0))
    volume = fees.floor_to(min(float(pos["qty"]), held), info["lot_decimals"])
    if volume <= 0 or volume < info.get("ordermin", 0):
        return {"ok": False, "reason": f"nothing sellable on exchange ({volume} {base}, held {held})"}
    try:
        bid, ask = _ticker(pair)
    except Exception as e:
        return {"ok": False, "reason": f"no live bid/ask: {e}"}
    # maker price: never below the best ask (crossing = taker). Round UP to the pair tick.
    price = _ceil_dex(max(float(price or 0), ask), info["pair_decimals"])
    hv = heart.check(action="SELL", quote=volume * float(pos["entry"]), venue=heart_venue(),
                     positions=len(portfolio.open_positions()), holding_qty=held, manual=False)
    if hv["decision"] != "ALLOW":
        return {"ok": False, "reason": f"heart {hv['decision']}: {hv['reason']}"}
    if os.environ.get("BH_VALIDATE_ONLY"):
        return {"ok": True, "validated": True,
                "body": {"pair": pair, "type": "sell", "ordertype": "limit",
                         "volume": str(volume), "price": str(price)}}
    res = venues.place_order(pair, "sell", "limit", volume, price, None)
    invalidate_balance()
    if not res.get("ok"):
        return {"ok": False, "reason": f"order rejected: {res.get('error')}"}
    txid = res.get("order_id")
    try:
        import store
        store.live_order(txid, pos.get("agent"), pair, "sell", "limit", price, volume,
                         plan={"reason": reason}, status="OPEN")
    except Exception:
        pass
    d = portfolio._load()                         # mark it selling so we never double-fire
    for p in d["positions"]:
        if p["id"] == pos_id:
            p["exit_txid"] = txid
            p["exit_price"] = price
            p["exit_reason"] = reason
            p["exit_kind"] = kind
            if level is not None:
                p["exit_level"] = level
    portfolio._save(d)
    ex = _exit_load()
    ex = [x for x in ex if x.get("pos_id") != pos_id]
    ex.append({"txid": txid, "pos_id": pos_id, "agent": pos.get("agent"), "pair": pair,
               "price": price, "qty": volume, "volume": volume, "placed": time.time(),
               "reason": reason, "done": 0, "kind": kind, "level": level})
    _exit_save(ex)
    return {"ok": True, "txid": txid, "price": price, "volume": volume, "pending": True,
            "maker": True, "bid": bid, "ask": ask, "kind": kind}


def sync_exits() -> list:
    """Settle resting maker-exit sells. closed -> close the position at the REAL fill;
    partial -> book the executed part; still open past exit_requote_seconds -> cancel and
    re-rest at the current best ask (chase the maker price WITHOUT ever crossing to taker);
    canceled/expired unfilled -> release the position so exits.py retries. Exchange = truth."""
    import time, portfolio, fees
    ex = _exit_load()
    if not ex:
        return []
    try:
        cfg = json.loads((BH / "config" / "decider.json").read_text())
    except (OSError, json.JSONDecodeError):
        cfg = {}
    min_net = float(cfg.get("exit_min_net_pct", 0.25))
    requote = float(cfg.get("exit_requote_seconds", 20))
    try:
        res = venues.orders([(e["txid"], e.get("pair")) for e in ex])
    except Exception:
        return []
    open_pos = {p["id"]: p for p in portfolio.open_positions()}
    drop, events = set(), []
    for e in ex:
        pos = open_pos.get(e["pos_id"])
        if not pos:
            drop.add(e["txid"]); continue
        o = res.get(e["txid"])
        if not o:
            continue
        st = o.get("status")
        vol = float(o.get("vol_exec") or 0)
        px = float(o.get("price") or 0)
        fee = float(o.get("fee") or 0)
        try:
            import store
            if st in ("closed", "canceled", "expired"):
                store.live_order_result(e["txid"], "FILLED" if st == "closed" else
                                        ("PARTIAL" if vol > 0 else st.upper()), vol, px or None, fee)
        except Exception:
            pass
        if st == "closed":                            # fully filled -> close at the real fill
            portfolio.close_position(e["pos_id"], px or float(pos["entry"]),
                                     reason=e.get("reason") or "limit exit", fee=fee or None)
            try:
                import heart
                heart.heartbeat()
            except Exception:
                pass
            events.append({"txid": e["txid"], "pos": e["pos_id"], "result": "filled",
                           "price": px, "vol": vol, "fee": fee})
            drop.add(e["txid"]); continue
        if vol > 0:                                   # book the executed part, keep working
            delta = vol - float(e.get("done") or 0)
            if delta > 0:
                d = portfolio._load()
                for p in d["positions"]:
                    if p["id"] == e["pos_id"]:
                        p["qty"] = round(max(float(p["qty"]) - delta, 0), 12)
                portfolio._save(d)
            e["done"] = vol
            events.append({"txid": e["txid"], "pos": e["pos_id"], "result": "partial",
                           "vol": vol, "price": px})
        if st in ("open", "pending"):                 # still working
            if time.time() - float(e.get("placed", 0)) >= requote:
                try:
                    venues.cancel(e["txid"], e.get("pair"))
                except Exception:
                    pass
                d = portfolio._load()
                for p in d["positions"]:
                    if p["id"] == e["pos_id"]:
                        p.pop("exit_txid", None)
                portfolio._save(d)
                if e.get("kind") == "stop":
                    # a protective-stop limit is not chased to the profit minimum;
                    # release it so exits.manage() re-arms at the current level.
                    events.append({"txid": e["txid"], "pos": e["pos_id"],
                                   "result": "stop-requote-deferred"})
                    drop.add(e["txid"]); continue
                try:
                    _, taker = fees.rates(e["pair"])
                    qty = float(pos["qty"])
                    cost = float(pos["entry"]) * qty + float(pos.get("fee_entry") or 0)
                    need = cost * (1 + min_net / 100) / (qty * (1 - taker))
                    _, ask = _ticker(e["pair"])
                    res2 = sell_position(e["pos_id"], e.get("reason") or "exit chase",
                                         price=max(need, ask))
                    events.append({"txid": e["txid"], "pos": e["pos_id"], "result": "requoted",
                                   "new_txid": res2.get("txid"), "price": res2.get("price")})
                except Exception as exc:
                    events.append({"txid": e["txid"], "pos": e["pos_id"],
                                   "result": f"requote-failed: {exc}"})
                drop.add(e["txid"]); continue
            continue
        # canceled/expired with nothing (left) to fill -> release so exits.py retries
        d = portfolio._load()
        for p in d["positions"]:
            if p["id"] == e["pos_id"]:
                p.pop("exit_txid", None)
        portfolio._save(d)
        events.append({"txid": e["txid"], "pos": e["pos_id"], "result": f"{st}-unfilled"})
        drop.add(e["txid"])
    cur = _exit_load()                    # preserve any entry re-placed during requote
    done_map = {e["txid"]: e["done"] for e in ex if "done" in e}
    out = []
    for x in cur:
        if x.get("txid") in drop:
            continue
        if x.get("txid") in done_map:
            x = {**x, "done": done_map[x["txid"]]}
        out.append(x)
    _exit_save(out)
    return events


def mark(pair: str, price: float) -> dict:
    import portfolio
    closed = portfolio.mark(pair, price)
    if closed:
        import treasury
        for c in closed:
            # on close, release reserved risk (rough: relax open_risk)
            pass
    return {"marked": pair, "price": price, "closed": closed}


def close(pos_id: str, price: float) -> dict:
    import portfolio
    rec = portfolio.close_position(pos_id, price, reason="manual")
    try:
        import store
        if rec.get("ok"):
            r = rec.get("record") or {}
            store.close_position(pos_id, price, r.get("net", 0.0), fees=r.get("fees", 0.0),
                                 reason="manual")
    except Exception:
        pass
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--pair", default="DOT/USD")
    ap.add_argument("--mark", nargs=2)
    ap.add_argument("--close", nargs=2)
    args = ap.parse_args()
    if args.mark:
        print(json.dumps(mark(args.mark[0], float(args.mark[1])), indent=2))
    elif args.close:
        print(json.dumps(close(args.close[0], float(args.close[1])), indent=2))
    else:
        import broker
        plan = broker.decide()
        print(json.dumps(execute(plan, pair=args.pair), indent=2))
