"""venues.py — one interface, three exchanges (Kraken, Binance, Coinbase Advanced).

The registry (config/exchanges.json + trade/exchanges.py) knows the exchanges and
their credentials; THIS module knows how to actually talk to each one. Every
adapter exposes the same methods, so the engine above never branches on venue:

    native(pair)                       -> exchange symbol (XBTUSD / DOTUSDT / DOT-USD)
    last_price(pair)                   -> float | None
    ticker(pair)                       -> (bid, ask)
    ohlc(pair, interval="1h")          -> [[t, open, high, low, close, vol], ...]  oldest->newest
    pair_rules(pair)                   -> {pair_decimals, lot_decimals, ordermin, costmin, tick_size}
    fee_rates(pair)                    -> {maker_pct, taker_pct, source}   (percent)
    balance()                          -> {BASE_ASSET: amount}     ({} == UNKNOWN, never 0)
    place_order(pair, side, otype, volume, price=None, expire_s=None)
                                       -> {ok:True, order_id} | {ok:False, error}
    cancel(order_id[, pair])           -> {ok, ...}
    orders([(id, pair), ...])          -> {id: {status, vol_exec, price, cost, fee}}
    order_status(order_id[, pair])     -> single status-dict | None

Status is normalised to the tokens the rest of the code already speaks:
open / partial / closed (=fully filled) / canceled / expired.

This module only signs and sends the request a caller asked for. It never decides
to trade; PAPER/LIVE + Heart gating live in execute.py.

CLI (public only, no keys):  python3 trade/venues.py list | quote DOT/USD | candles DOT/USD 1h kraken
"""
import sys, json, time, hmac, hashlib, base64, urllib.parse, urllib.request
import urllib.error
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
REGISTRY = BH / "config" / "exchanges.json"
FEE_FALLBACK = BH / "config" / "exchange_fees.json"
INFO = BH / "logs" / "exchange_info.json"       # Kraken snapshot (exchange_watch.py)
FEE_CACHE = BH / "logs" / "fee_cache.json"
PAIR_CACHE = BH / "logs" / "pair_info.json"
UA = {"User-Agent": "BlackHorse/1.0"}
_QUOTES = ("USDT", "USDC", "FDUSD", "USD", "EUR", "GBP", "CAD")


class ExchangeError(Exception):
    pass


# --- helpers -----------------------------------------------------------------

def _load_json(p, d=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return {} if d is None else d


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _http(url, method="GET", headers=None, body=None, timeout=30):
    if isinstance(body, dict):
        data = urllib.parse.urlencode(body).encode()
    elif isinstance(body, str):
        data = body.encode()
    else:
        data = body
    req = urllib.request.Request(url, data=data, method=method, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def norm_pair(pair):
    """Canonical 'BASE/QUOTE', uppercase."""
    p = str(pair or "DOT/USD").strip().upper().replace("-", "/")
    if "/" in p:
        b, q = p.split("/", 1)
        return f"{b}/{q}"
    for q in _QUOTES:
        if p.endswith(q) and len(p) > len(q):
            return f"{p[:-len(q)]}/{q}"
    return f"{p}/USD"


def interval_minutes(iv):
    iv = str(iv).lower().strip()
    if iv.endswith("m"):
        return int(iv[:-1])
    if iv.endswith("h"):
        return int(iv[:-1]) * 60
    if iv.endswith("d"):
        return int(iv[:-1]) * 1440
    return int(iv)


def _decimals_from_increment(x):
    s = format(float(x), ".12f").rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0


def _fee_fallback(name):
    f = _load_json(FEE_FALLBACK, {}).get(name) or {}
    return {"maker_pct": float(f.get("maker_pct", 0.40)),
            "taker_pct": float(f.get("taker_pct", 0.80)),
            "source": "config/exchange_fees.json"}


# --- base --------------------------------------------------------------------

class Exchange:
    name = "?"
    label = "?"

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def _native(self, pair):
        raise NotImplementedError

    def native(self, pair):
        return self._native(norm_pair(pair))

    # public
    def last_price(self, pair):
        raise NotImplementedError

    def ticker(self, pair):
        raise NotImplementedError

    def ohlc(self, pair, interval="1h"):
        raise NotImplementedError

    def pair_rules(self, pair):
        return {"pair_decimals": 4, "lot_decimals": 8, "ordermin": 0.0,
                "costmin": 0.0, "tick_size": None, "pair": norm_pair(pair), "ts": 0}

    def fee_rates(self, pair):
        return _fee_fallback(self.name)

    # private
    def _creds(self):
        import exchanges
        c = exchanges.require_credentials(self.name)
        return c["api_key"], c["private_key"]

    def balance(self):
        raise NotImplementedError

    def place_order(self, pair, side, order_type, volume, price=None, expire_s=None):
        raise NotImplementedError

    def cancel(self, order_id, pair=None):
        raise NotImplementedError

    def order_status(self, order_id, pair=None):
        return self.orders([(order_id, pair)]).get(order_id)

    def orders(self, refs):
        return {}


# --- Kraken ------------------------------------------------------------------

class Kraken(Exchange):
    name = "kraken"
    label = "Kraken"
    BASE = "https://api.kraken.com"
    _bal = {"ts": 0.0, "v": None, "err": None}

    def __init__(self, cfg=None):
        super().__init__(cfg)
        import os
        self.BASE = os.environ.get("BH_KRAKEN_BASE", self.BASE)

    def _native(self, pair):
        k = pair.replace("/", "")
        return k.replace("BTC", "XBT", 1) if k.startswith("BTC") else k

    @staticmethod
    def _asset(asset):
        a = asset.upper()
        if a in ("XXBT", "XBT"):
            return "BTC"
        return a.lstrip("XZ") if a.startswith(("X", "Z")) and len(a) > 3 else a

    _last_nonce = 0

    def _private(self, path, body):
        key, secret = self._creds()
        Kraken._last_nonce = max(int(time.time() * 1000), Kraken._last_nonce + 1)
        body = {**body, "nonce": str(Kraken._last_nonce)}
        data = urllib.parse.urlencode(body).encode()
        sha = hashlib.sha256(str(body["nonce"]).encode() + data).digest()
        sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
        req = urllib.request.Request(self.BASE + path, data=data, method="POST",
                                     headers={**UA, "API-Key": key,
                                              "API-Sign": base64.b64encode(sig.digest()).decode()})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def last_price(self, pair):
        try:
            d = _http(f"{self.BASE}/0/public/Ticker?pair={self._native(pair)}")
            return float(next(iter(d["result"].values()))["c"][0])
        except Exception:
            return None

    def ticker(self, pair):
        d = _http(f"{self.BASE}/0/public/Ticker?pair={self._native(pair)}")
        row = next(iter(d["result"].values()))
        return float(row["b"][0]), float(row["a"][0])

    def ohlc(self, pair, interval="1h"):
        d = _http(f"{self.BASE}/0/public/OHLC?pair={self._native(pair)}&interval={interval_minutes(interval)}")
        res = d["result"]
        rows = next(v for k, v in res.items() if k != "last")
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])] for r in rows]

    def _info_rec(self, pair):
        pairs = (_load_json(INFO, {}).get("pairs") or {})
        want = self._native(pair).replace("XBT", "BTC")
        for key, rec in pairs.items():
            if self._native(key).replace("XBT", "BTC") == want:
                return rec
            if rec.get("wsname") and self._native(rec["wsname"]).replace("XBT", "BTC") == want:
                return rec
        return {}

    def pair_rules(self, pair):
        rec = self._info_rec(pair)
        if rec:
            return {"pair_decimals": int(rec.get("pair_decimals") if rec.get("pair_decimals") is not None else 4),
                    "lot_decimals": int(rec.get("lot_decimals") if rec.get("lot_decimals") is not None else 8),
                    "ordermin": float(rec.get("ordermin") or 0), "costmin": float(rec.get("costmin") or 0),
                    "tick_size": rec.get("tick_size"), "pair": rec.get("wsname"),
                    "ts": _load_json(INFO, {}).get("updated", 0)}
        kp = self._native(pair)
        cache = _load_json(PAIR_CACHE, {})
        c = cache.get(kp)
        if c and time.time() - c.get("ts", 0) < 86400:
            return c
        try:
            d = _http(f"{self.BASE}/0/public/AssetPairs?pair={kp}")
            rec = next(iter(d["result"].values()))
            out = {"pair_decimals": int(rec.get("pair_decimals", 4)), "lot_decimals": int(rec.get("lot_decimals", 8)),
                   "ordermin": float(rec.get("ordermin") or 0), "costmin": float(rec.get("costmin") or 0),
                   "tick_size": rec.get("tick_size"), "pair": rec.get("wsname"), "ts": time.time()}
            cache[kp] = out
            PAIR_CACHE.write_text(json.dumps(cache, indent=2))
            return out
        except Exception:
            return c or super().pair_rules(pair)

    def fee_rates(self, pair):
        rec = self._info_rec(pair)
        if rec and time.time() - _load_json(INFO, {}).get("updated", 0) < 6 * 3600:
            return {"maker_pct": float(rec.get("maker_pct", 0.40)), "taker_pct": float(rec.get("taker_pct", 0.80)),
                    "source": f"exchange_info.json ({rec.get('source', 'snapshot')})"}
        kp = self._native(pair)
        cache = _load_json(FEE_CACHE, {})
        c = cache.get(kp)
        if c and time.time() - c.get("ts", 0) < 6 * 3600:
            return c
        try:
            r = self._private("/0/private/TradeVolume", {"nonce": str(int(time.time() * 1000)), "pair": kp})
            t = (r.get("result") or {}).get("fees", {}).get(kp) or {}
            m = (r.get("result") or {}).get("fees_maker", {}).get(kp) or {}
            out = {"maker_pct": float(m["maxfee"]), "taker_pct": float(t["maxfee"]),
                   "source": "kraken TradeVolume maxfee (tier-1 base)", "ts": time.time()}
            cache[kp] = out
            FEE_CACHE.write_text(json.dumps(cache, indent=2))
            return out
        except Exception:
            return c or _fee_fallback("kraken")

    def balance(self):
        if self._bal["v"] is not None and time.time() - self._bal["ts"] < 4:
            return dict(self._bal["v"])                     # same cycle: one consistent read
        r = {}
        for _ in range(3):
            try:
                r = self._private("/0/private/Balance", {"nonce": str(int(time.time() * 1000))})
            except Exception as e:
                r = {"error": [f"request failed: {e}"]}
            if not r.get("error"):
                break
            time.sleep(1.5)
        if r.get("error"):
            return {}                                        # {} == UNKNOWN (never read as 0)
        out = {}
        for asset, amt in (r.get("result") or {}).items():
            a = {"XXBT": "BTC", "XBT": "BTC"}.get(asset.upper(),
                                                 asset.upper().lstrip("XZ") if asset.upper().startswith(("X", "Z")) and len(asset) > 3 else asset.upper())
            out[a] = out.get(a, 0) + float(amt)
        self._bal["v"], self._bal["ts"] = dict(out), time.time()
        return out

    def place_order(self, pair, side, order_type, volume, price=None, expire_s=None):
        body = {"nonce": str(int(time.time() * 1000)), "pair": self._native(pair),
                "type": side.lower(), "ordertype": order_type.lower(), "volume": str(volume)}
        if order_type.lower() == "limit" and price is not None:
            body["price"] = str(price)
            if expire_s:
                body["expiretm"] = f"+{int(expire_s)}"
        r = self._private("/0/private/AddOrder", body)
        if r.get("error"):
            return {"ok": False, "error": "; ".join(r["error"]), "result": r.get("result")}
        return {"ok": True, "order_id": (r.get("result") or {}).get("txid", [None])[0], "result": r.get("result")}

    def cancel(self, order_id, pair=None):
        r = self._private("/0/private/CancelOrder", {"nonce": str(int(time.time() * 1000)), "txid": order_id})
        if r.get("error"):
            return {"ok": False, "error": "; ".join(r["error"])}
        return {"ok": True, "result": r.get("result")}

    def orders(self, refs):
        ids = [r[0] for r in refs if r and r[0]]
        if not ids:
            return {}
        r = self._private("/0/private/QueryOrders", {"nonce": str(int(time.time() * 1000)), "txid": ",".join(ids)})
        if r.get("error"):
            return {}
        out = {}
        for oid, o in (r.get("result") or {}).items():
            out[oid] = {"status": o.get("status"), "vol_exec": float(o.get("vol_exec") or 0),
                        "price": float(o.get("price") or 0), "cost": float(o.get("cost") or 0),
                        "fee": float(o.get("fee") or 0)}
        return out


# --- Binance -----------------------------------------------------------------

class Binance(Exchange):
    name = "binance"
    label = "Binance"

    def __init__(self, cfg=None):
        super().__init__(cfg)
        import os
        default = "https://testnet.binance.vision" if os.environ.get("BH_BINANCE_TESTNET") == "1" else "https://api.binance.com"
        self.BASE = os.environ.get("BH_BINANCE_BASE", default)

    def _native(self, pair):
        base, quote = norm_pair(pair).split("/")
        if quote == "USD":
            quote = "USDT"
        return f"{base}{quote}"

    def last_price(self, pair):
        try:
            return float(_http(f"{self.BASE}/api/v3/ticker/price?symbol={self._native(pair)}")["price"])
        except Exception:
            return None

    def ticker(self, pair):
        d = _http(f"{self.BASE}/api/v3/ticker/bookTicker?symbol={self._native(pair)}")
        return float(d["bidPrice"]), float(d["askPrice"])

    def ohlc(self, pair, interval="1h"):
        table = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h",
                 120: "2h", 240: "4h", 360: "6h", 720: "12h", 1440: "1d"}
        iv = table[min(table, key=lambda k: abs(k - interval_minutes(interval)))]
        d = _http(f"{self.BASE}/api/v3/klines?symbol={self._native(pair)}&interval={iv}&limit=500")
        return [[int(r[0] // 1000), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d]

    def pair_rules(self, pair):
        try:
            d = _http(f"{self.BASE}/api/v3/exchangeInfo?symbol={self._native(pair)}")
            sym = (d.get("symbols") or [{}])[0]
            f = {x["filterType"]: x for x in sym.get("filters", [])}
            return {"pair_decimals": _decimals_from_increment(f.get("PRICE_FILTER", {}).get("tickSize", "0.0001")),
                    "lot_decimals": _decimals_from_increment(f.get("LOT_SIZE", {}).get("stepSize", "0.00000001")),
                    "tick_size": f.get("PRICE_FILTER", {}).get("tickSize"),
                    "ordermin": float(f.get("LOT_SIZE", {}).get("minQty") or 0),
                    "costmin": float((f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}).get("minNotional") or 0),
                    "pair": sym.get("symbol"), "ts": time.time()}
        except Exception:
            return super().pair_rules(pair)

    def _signed(self, method, path, params):
        key, secret = self._creds()
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return _http(f"{self.BASE}{path}?{qs}&signature={sig}", method=method,
                     headers={"X-MBX-APIKEY": key})

    def balance(self):
        d = self._signed("GET", "/api/v3/account", {})
        out = {}
        for b in d.get("balances", []):
            amt = float(b.get("free") or 0) + float(b.get("locked") or 0)
            if amt:
                out[b["asset"].upper()] = out.get(b["asset"].upper(), 0) + amt
        return out

    def place_order(self, pair, side, order_type, volume, price=None, expire_s=None):
        p = {"symbol": self._native(pair), "side": side.upper(), "type": order_type.upper(), "quantity": volume}
        if order_type.lower() == "limit":
            p["price"] = price
            p["timeInForce"] = "GTC"
        try:
            r = self._signed("POST", "/api/v3/order", p)
        except urllib.error.HTTPError as e:
            try:
                return {"ok": False, "error": json.loads(e.read()).get("msg", str(e))}
            except Exception:
                return {"ok": False, "error": str(e)}
        return {"ok": True, "order_id": str(r.get("orderId")), "result": r}

    def cancel(self, order_id, pair=None):
        try:
            return {"ok": True, "result": self._signed("DELETE", "/api/v3/order",
                                                       {"symbol": self._native(pair or "DOT/USD"), "orderId": order_id})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def order_status(self, order_id, pair=None):
        r = self._signed("GET", "/api/v3/order", {"symbol": self._native(pair or "DOT/USD"), "orderId": order_id})
        exq = float(r.get("executedQty") or 0)
        cost = float(r.get("cummulativeQuoteQty") or 0)
        fee = sum(float(f.get("commission") or 0) for f in (r.get("fills") or []))
        status = {"NEW": "open", "PARTIALLY_FILLED": "partial", "FILLED": "closed",
                  "CANCELED": "canceled", "EXPIRED": "expired", "REJECTED": "expired"}.get(r.get("status"), "open")
        return {"status": status, "vol_exec": exq, "price": (cost / exq if exq else 0.0), "cost": cost, "fee": fee}

    def orders(self, refs):
        out = {}
        for oid, pair in refs:
            try:
                s = self.order_status(oid, pair)
                if s:
                    out[oid] = s
            except Exception:
                continue
        return out


# --- Coinbase Advanced (CDP) --------------------------------------------------

class Coinbase(Exchange):
    name = "coinbase"
    label = "Coinbase Advanced"

    def __init__(self, cfg=None):
        super().__init__(cfg)
        import os
        self.PUBLIC = os.environ.get("BH_COINBASE_PUBLIC", "https://api.exchange.coinbase.com")
        self.CDP = os.environ.get("BH_COINBASE_BASE", "https://api.coinbase.com")

    def _native(self, pair):
        base, quote = norm_pair(pair).split("/")
        return f"{base}-{quote}"

    def last_price(self, pair):
        try:
            return float(_http(f"{self.PUBLIC}/products/{self._native(pair)}/ticker")["price"])
        except Exception:
            return None

    def ticker(self, pair):
        d = _http(f"{self.PUBLIC}/products/{self._native(pair)}/ticker")
        return float(d["bid"]), float(d["ask"])

    def ohlc(self, pair, interval="1h"):
        allowed = (60, 300, 900, 3600, 21600, 86400)
        g = min(allowed, key=lambda x: abs(x - interval_minutes(interval) * 60))
        d = _http(f"{self.PUBLIC}/products/{self._native(pair)}/candles?granularity={g}")
        # CB row: [time, low, high, open, close, volume] newest-first -> [t,o,h,l,c,v] oldest-first
        rows = [[int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])] for r in d]
        return sorted(rows, key=lambda x: x[0])

    def pair_rules(self, pair):
        try:
            d = _http(f"{self.PUBLIC}/products/{self._native(pair)}")
            return {"pair_decimals": _decimals_from_increment(d.get("quote_increment", "0.0001")),
                    "lot_decimals": _decimals_from_increment(d.get("base_increment", "0.00000001")),
                    "tick_size": d.get("quote_increment"),
                    "ordermin": float(d.get("base_min_size") or 0),
                    "costmin": float(d.get("min_market_funds") or 0),
                    "pair": d.get("id"), "ts": time.time()}
        except Exception:
            return super().pair_rules(pair)

    def _jwt(self, method, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        key_name, pem = self._creds()
        now = int(time.time())
        host = self.CDP.split("//", 1)[1]
        header = b64url(json.dumps({"alg": "ES256", "kid": key_name, "typ": "JWT"},
                                   separators=(",", ":")).encode())
        payload = b64url(json.dumps({"sub": key_name, "iss": "cdp", "nbf": now, "exp": now + 120,
                                     "uri": f"{method} {host}{path}"}, separators=(",", ":")).encode())
        signing = header + b"." + payload
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        der = key.sign(signing, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return (signing + b"." + b64url(raw)).decode()

    def _cdp(self, method, path, body=None):
        token = self._jwt(method, path)
        return _http(f"{self.CDP}{path}", method=method,
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                     body=json.dumps(body) if body is not None else None)

    def balance(self):
        d = self._cdp("GET", "/api/v3/brokerage/accounts")
        out = {}
        for a in d.get("accounts", []):
            amt = float((a.get("available_balance") or {}).get("value") or 0)
            if amt:
                out[a["currency"].upper()] = out.get(a["currency"].upper(), 0) + amt
        return out

    def place_order(self, pair, side, order_type, volume, price=None, expire_s=None):
        cfg = {"limit_limit_gtc": {"base_size": str(volume), "limit_price": str(price)}} \
            if order_type.lower() == "limit" else {"market_market_ioc": {"base_size": str(volume)}}
        body = {"client_order_id": __import__("uuid").uuid4().hex, "product_id": self._native(pair),
                "side": side.upper(), "order_configuration": cfg}
        try:
            r = self._cdp("POST", "/api/v3/brokerage/orders", body)
        except urllib.error.HTTPError as e:
            try:
                return {"ok": False, "error": json.loads(e.read()).get("message", str(e))}
            except Exception:
                return {"ok": False, "error": str(e)}
        if r.get("success") is False:
            return {"ok": False, "error": r.get("error_response") or "order rejected", "result": r}
        return {"ok": True, "order_id": r.get("order_id") or r.get("success_response", {}).get("order_id"), "result": r}

    def cancel(self, order_id, pair=None):
        try:
            r = self._cdp("POST", "/api/v3/brokerage/orders/batch_cancel", {"order_ids": [order_id]})
            return {"ok": True, "result": r}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def orders(self, refs):
        out = {}
        for oid, pair in refs:
            try:
                s = self.order_status(oid, pair)
                if s:
                    out[oid] = s
            except Exception:
                continue
        return out

    def order_status(self, order_id, pair=None):
        d = self._cdp("GET", f"/api/v3/brokerage/orders/historical/{order_id}")
        o = d.get("order") or {}
        vol = float(o.get("filled_size") or 0)
        px = float(o.get("average_filled_price") or 0)
        fee = float((o.get("total_fees") or 0))
        status = {"PENDING": "pending", "OPEN": "open", "FILLED": "closed",
                  "CANCELLED": "canceled", "EXPIRED": "expired", "FAILED": "expired"}.get(
                      (o.get("status") or "").upper(), "open")
        return {"status": status, "vol_exec": vol, "price": px, "cost": vol * px, "fee": fee}


# --- factory + facade --------------------------------------------------------

_CLASSES = {"kraken": Kraken, "binance": Binance, "coinbase": Coinbase}


def _registry():
    return _load_json(REGISTRY, {})


def get(name):
    cfg = (_registry().get("exchanges") or {}).get(name)
    if not cfg:
        raise ExchangeError(f"unknown exchange '{name}'")
    if name not in _CLASSES:
        raise ExchangeError(f"no adapter implemented for '{name}'")
    return _CLASSES[name](cfg)


def active_name():
    return _registry().get("active", "kraken")


def active():
    return get(active_name())


def native(pair):
    return active().native(pair)


def last_price(pair):
    return active().last_price(pair)


def ticker(pair):
    return active().ticker(pair)


def ohlc(pair, interval="1h"):
    return active().ohlc(pair, interval)


def pair_rules(pair):
    return active().pair_rules(pair)


def fee_rates(pair):
    return active().fee_rates(pair)


def balance():
    return active().balance()


def invalidate_balance():
    """Force the next balance() to hit the exchange (Kraken caches for one cycle)."""
    Kraken._bal["v"] = None
    Kraken._bal["ts"] = 0.0


def place_order(pair, side, order_type, volume, price=None, expire_s=None):
    return active().place_order(pair, side, order_type, volume, price, expire_s)


def cancel(order_id, pair=None):
    return active().cancel(order_id, pair)


def orders(refs):
    return active().orders(refs)


def order_status(order_id, pair=None):
    return active().order_status(order_id, pair)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(f"active: {active_name()}")
        for n in _registry().get("exchanges", {}):
            print(f"  {'*' if n == active_name() else ' '} {n:<10} adapter={'yes' if n in _CLASSES else 'NO'}")
    elif cmd == "quote":
        pair = sys.argv[2] if len(sys.argv) > 2 else "DOT/USD"
        for n in _CLASSES:
            try:
                ex = get(n)
                print(f"{n:<10} {pair:<9} last={ex.last_price(pair)!s:<12} bid/ask={ex.ticker(pair)}")
            except Exception as e:
                print(f"{n:<10} {pair:<9} ERR {e}")
    elif cmd == "candles":
        pair = sys.argv[2] if len(sys.argv) > 2 else "DOT/USD"
        iv = sys.argv[3] if len(sys.argv) > 3 else "1h"
        name = sys.argv[4] if len(sys.argv) > 4 else active_name()
        rows = get(name).ohlc(pair, iv)
        print(f"{name} {pair} {iv}: {len(rows)} bars, last={rows[-1] if rows else None}")
    else:
        print(__doc__)
