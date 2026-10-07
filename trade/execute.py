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
SLIPPAGE_PCT_PER_SIDE = 0.1
FEE_PCT_PER_SIDE = 0.26


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


def cancel(txid: str) -> dict:
    """Cancel a live order by Kraken txid."""
    import exchanges, time, urllib.request, urllib.parse, hmac, hashlib, base64, json
    name = exchanges.active_exchange()["name"]
    creds = exchanges.require_credentials(name)
    key = creds["api_key"]; secret = creds["private_key"]
    url = "https://api.kraken.com"
    path = "/0/private/CancelOrder"
    nonce = str(int(time.time() * 1000))
    body = {"nonce": nonce, "txid": txid}
    data = urllib.parse.urlencode(body).encode()
    sha = hashlib.sha256(nonce.encode() + data).digest()
    sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
    req = urllib.request.Request(url + path, data=data, method="POST",
                                 headers={"API-Key": key, "API-Sign": base64.b64encode(sig.digest()).decode()})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        return {"ok": False, "reason": f"cancel error: {e}"}
    if resp.get("error"):
        return {"ok": False, "reason": f"kraken: {resp['error']}"}
    return {"ok": True, "result": resp.get("result")}


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
                                  target=plan.get("target"), fee_pct=FEE_PCT_PER_SIDE,
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
    _BAL["ts"] = 0.0


def _kraken_private(path: str, body: dict) -> dict:
    """Signed Kraken private request. body must already contain 'nonce'."""
    import exchanges, time, urllib.request, urllib.parse, hmac, hashlib, base64, json
    global _LAST_NONCE
    _LAST_NONCE = max(int(time.time() * 1000), _LAST_NONCE + 1)   # strictly increasing, no same-ms collisions
    body = {**body, "nonce": str(_LAST_NONCE)}
    creds = exchanges.require_credentials(heart_venue())
    key, secret = creds["api_key"], creds["private_key"]
    data = urllib.parse.urlencode(body).encode()
    sha = hashlib.sha256(str(body["nonce"]).encode() + data).digest()
    sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
    req = urllib.request.Request("https://api.kraken.com" + path, data=data, method="POST",
                                 headers={"API-Key": key,
                                          "API-Sign": base64.b64encode(sig.digest()).decode()})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def kraken_balance() -> dict:
    """Base-asset balances available to sell, keyed by base symbol."""
    import time
    if _BAL["v"] is not None and time.time() - _BAL["ts"] < 4:
        return dict(_BAL["v"])                     # same cycle: one consistent read
    r = {}
    for attempt in range(3):                       # a failed read must never look like "zero funds"
        try:
            r = _kraken_private("/0/private/Balance", {"nonce": str(int(time.time() * 1000))})
        except Exception as e:
            r = {"error": [f"request failed: {e}"]}
        if not r.get("error"):
            break
        _BAL["err"] = r.get("error")
        time.sleep(1.5)
    if r.get("error"):
        return {}                                  # {} == UNKNOWN (callers must not read it as 0)
    out = {}
    for asset, amt in (r.get("result") or {}).items():
        a = asset.upper().replace("X", "", 1) if asset.startswith("X") and len(asset) > 3 else asset
        a = {"XXBT": "BTC", "XBT": "BTC"}.get(asset.upper(), asset.upper().lstrip("XZ"))
        out[a] = out.get(a, 0) + float(amt)
    _BAL["v"], _BAL["ts"] = dict(out), time.time()
    return out


def kraken_last_price(pair: str) -> float | None:
    import urllib.request, json
    kpair = _norm_pair(pair).replace("/", "")
    if kpair.startswith("BTC"):
        kpair = kpair.replace("BTC", "XBT", 1)
    try:
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"https://api.kraken.com/0/public/Ticker?pair={kpair}",
                                   headers={"User-Agent": "BlackHorse/1.0"}), timeout=15).read())
        res = d.get("result") or {}
        row = next(iter(res.values()))
        return float(row["c"][0])
    except Exception:
        return None


def kraken_fill(txid: str, tries: int = 6) -> dict | None:
    """Actual fill of a Kraken order: {price, vol, cost, fee}. The exchange is the
    truth, not the plan. Returns None if it can't be read."""
    import time
    for _ in range(tries):
        try:
            r = _kraken_private("/0/private/QueryOrders",
                                {"nonce": str(int(time.time() * 1000)), "txid": txid})
            o = (r.get("result") or {}).get(txid)
            if o and o.get("status") == "closed" and float(o.get("vol_exec") or 0) > 0:
                return {"price": float(o["price"]), "vol": float(o["vol_exec"]),
                        "cost": float(o["cost"]), "fee": float(o["fee"])}
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


def sync_pending() -> list:
    """Reconcile resting limit entries with Kraken. FILLED (or partially filled then
    expired) -> open the position at the REAL fill. Expired unfilled -> drop. Open past
    expiry -> cancel. The exchange is the truth. Returns the events."""
    import time, portfolio
    pend = _pend_load()
    if not pend:
        return []
    try:
        r = _kraken_private("/0/private/QueryOrders", {"nonce": str(int(time.time() * 1000)),
                                                       "txid": ",".join(p["txid"] for p in pend)})
    except Exception:
        return []
    if r.get("error"):
        return []
    res = r.get("result") or {}
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
                    _kraken_private("/0/private/CancelOrder",
                                    {"nonce": str(int(time.time() * 1000)), "txid": p["txid"]})
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
    kpair = pair.replace("/", "")
    if kpair.startswith("BTC"):
        kpair = kpair.replace("BTC", "XBT", 1)
    side = "buy" if plan["action"] == "BUY" else "sell"
    otype = str(plan.get("order_type") or "market").lower()
    if side == "sell":
        otype = "market"
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
    else:
        if plan.get("agent") and has_pending(plan["agent"], pair):
            return {"ok": False, "blocked": True, "reason": "an entry order is already working for this agent/pair"}
        quote = pair.split("/")[1].upper()
        bals = kraken_balance()
        cash = float(bals.get(quote, 0)) if quote in ("USD", "USDT", "USDC", "EUR", "CAD", "GBP") else 0.0
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
    nonce = str(int(time.time() * 1000))
    body = {"nonce": nonce, "pair": kpair, "type": side, "ordertype": otype, "volume": str(volume)}
    if otype == "limit":
        body["price"] = str(price)
        if expire:
            body["expiretm"] = f"+{expire}"
    if os.environ.get("BH_VALIDATE_ONLY"):
        body["validate"] = "true"
    r = _kraken_private("/0/private/AddOrder", body)
    invalidate_balance()
    if r.get("error"):
        return {"ok": False, "blocked": True, "reason": f"kraken: {r['error']}"}
    if os.environ.get("BH_VALIDATE_ONLY"):
        return {"ok": True, "validated": True, "body": {k: v for k, v in body.items() if k != "nonce"},
                "kraken": r.get("result")}

    txid = (r.get("result") or {}).get("txid", [None])[0]
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
                    "volume": volume, "expires_in_s": expire, "kraken": r.get("result")}
        fill = float(price) or (kraken_last_price(pair) or 0)
        qty_rec, fee_rec = volume, None
        real = kraken_fill(txid) if txid else None
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
            real = kraken_fill(txid) if txid else None
            try:
                import store
                if real:
                    store.live_order_result(txid, "FILLED", real["vol"], real["price"], real["fee"])
            except Exception:
                pass
            portfolio.close_position(opened[0]["id"], real["price"] if real else last,
                                     reason="live exit", fee=real["fee"] if real else None)
    return {"ok": True, "mode": "LIVE", "txid": txid, "volume": volume, "kraken": r.get("result")}


def sell_position(pos_id: str, reason: str) -> dict:
    """Market-sell exactly ONE position's quantity, record the REAL fill, close it."""
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
    hv = heart.check(action="SELL", quote=volume * float(pos["entry"]), venue=heart_venue(),
                     positions=len(portfolio.open_positions()), holding_qty=held, manual=False)
    if hv["decision"] != "ALLOW":
        return {"ok": False, "reason": f"heart {hv['decision']}: {hv['reason']}"}
    kpair = pair.replace("/", "")
    kpair = kpair.replace("BTC", "XBT", 1) if kpair.startswith("BTC") else kpair
    body = {"nonce": str(int(time.time() * 1000)), "pair": kpair, "type": "sell",
            "ordertype": "market", "volume": str(volume)}
    if os.environ.get("BH_VALIDATE_ONLY"):
        body["validate"] = "true"
    r = _kraken_private("/0/private/AddOrder", body)
    invalidate_balance()
    if r.get("error"):
        return {"ok": False, "reason": f"kraken: {r['error']}"}
    if os.environ.get("BH_VALIDATE_ONLY"):
        return {"ok": True, "validated": True, "body": {k: v for k, v in body.items() if k != "nonce"}}
    txid = (r.get("result") or {}).get("txid", [None])[0]
    try:
        import store
        store.live_order(txid, pos.get("agent"), pair, "sell", "market", None, volume,
                         plan={"reason": reason}, status="OPEN")
    except Exception:
        pass
    d = portfolio._load()                         # mark it selling so we never double-fire
    for p in d["positions"]:
        if p["id"] == pos_id:
            p["exit_txid"] = txid
    portfolio._save(d)
    real = kraken_fill(txid) if txid else None
    price = real["price"] if real else (kraken_last_price(pair) or float(pos["entry"]))
    try:
        import store
        if real:
            store.live_order_result(txid, "FILLED", real["vol"], real["price"], real["fee"])
    except Exception:
        pass
    res = portfolio.close_position(pos_id, price, reason=reason, fee=real["fee"] if real else None)
    try:
        heart.heartbeat()
    except Exception:
        pass
    return {"ok": True, "txid": txid, "price": price, "net": res.get("net"),
            "fill_verified": bool(real)}


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
