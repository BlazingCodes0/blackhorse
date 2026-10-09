"""fees.py — the exchange's real maker/taker fee + pair precision.

Source of truth, in order:
  1. logs/exchange_info.json — the slow periodic snapshot written by
     trade/exchange_watch.py (batch AssetPairs + TradeVolume for ALL pairs). This
     carries the ACCOUNT's real tier fee when available, else the tier-1 base.
  2. live per-pair Kraken calls (TradeVolume / AssetPairs), cached in
     logs/fee_cache.json / logs/pair_info.json.
  3. config/exchange_fees.json (BASE tier-1 fallback).

Every order sizes itself with these numbers so cost + fee never exceeds cash.

CLI: python3 trade/fees.py [PAIR]
     python3 trade/exchange_watch.py --once   # refresh the snapshot
"""
import json, sys, time, math, urllib.request
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
CACHE = BH / "logs" / "fee_cache.json"
INFO = BH / "logs" / "exchange_info.json"
FALLBACK = BH / "config" / "exchange_fees.json"
TTL = 6 * 3600
INFO_TTL = 6 * 3600


def _kpair(pair: str) -> str:
    k = pair.replace("/", "").upper()
    return k.replace("BTC", "XBT", 1) if k.startswith("BTC") else k


def _load(p, d):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return d


def _find_info(pair: str) -> dict:
    """One pair's record from the consolidated exchange_watch snapshot (or {})."""
    d = _load(INFO, {})
    pairs = d.get("pairs") or {}
    if not pairs:
        return {}
    want = _kpair(pair).replace("XBT", "BTC")
    for key, rec in pairs.items():
        if _kpair(key).replace("XBT", "BTC") == want:
            return rec
        if _kpair(rec.get("wsname", "")).replace("XBT", "BTC") == want:
            return rec
    return {}


def _fallback() -> dict:
    f = _load(FALLBACK, {}).get("kraken", {})
    return {"maker_pct": float(f.get("maker_pct", 0.40)), "taker_pct": float(f.get("taker_pct", 0.80)),
            "source": "config/exchange_fees.json"}


def base_fees(pair: str = "DOT/USD") -> dict:
    """{"maker_pct","taker_pct","source"} — the account's real fee, percent.
    Falls back to the snapshot's base fee, then to live per-pair, then config."""
    d = _load(INFO, {})
    v = _find_info(pair)
    if v and time.time() - d.get("updated", 0) < INFO_TTL:
        maker = v.get("account_maker_pct", v.get("maker_pct"))
        taker = v.get("account_taker_pct", v.get("taker_pct"))
        if maker is not None and taker is not None:
            return {"maker_pct": float(maker), "taker_pct": float(taker),
                    "account_taker_pct": v.get("account_taker_pct"),
                    "account_maker_pct": v.get("account_maker_pct"),
                    "base_maker_pct": v.get("base_maker_pct"),
                    "base_taker_pct": v.get("base_taker_pct"),
                    "source": f"exchange_info.json ({v.get('source', 'snapshot')})",
                    "ts": d.get("updated", 0)}
    kp = _kpair(pair)
    cache = _load(CACHE, {})
    c = cache.get(kp)
    if c and time.time() - c.get("ts", 0) < TTL:
        return c
    try:
        import execute
        r = execute._kraken_private("/0/private/TradeVolume",
                                    {"nonce": str(int(time.time() * 1000)), "pair": kp})
        t = (r.get("result") or {}).get("fees", {}).get(kp) or {}
        m = (r.get("result") or {}).get("fees_maker", {}).get(kp) or {}
        out = {"maker_pct": float(m["maxfee"]), "taker_pct": float(t["maxfee"]),
               "account_taker_pct": float(t.get("fee", 0)), "account_maker_pct": float(m.get("fee", 0)),
               "source": "kraken TradeVolume maxfee (tier-1 base)", "ts": time.time()}
        cache[kp] = out
        CACHE.write_text(json.dumps(cache, indent=2))
        return out
    except Exception:
        if c:
            return c                      # stale beats guessing
        return {**_fallback(), "ts": 0}


def rates(pair: str = "DOT/USD"):
    """(maker_frac, taker_frac) as fractions, e.g. (0.004, 0.008)."""
    f = base_fees(pair)
    return f["maker_pct"] / 100.0, f["taker_pct"] / 100.0


def pair_info(pair: str) -> dict:
    """pair_decimals / lot_decimals / ordermin / costmin (+ tick_size) for a pair.
    Prefers the consolidated snapshot; falls back to a live per-pair call."""
    d = _load(INFO, {})
    v = _find_info(pair)
    if v and time.time() - d.get("updated", 0) < 86400:
        return {"pair_decimals": int(v.get("pair_decimals") if v.get("pair_decimals") is not None else 4),
                "lot_decimals": int(v.get("lot_decimals") if v.get("lot_decimals") is not None else 8),
                "ordermin": float(v.get("ordermin") or 0),
                "costmin": float(v.get("costmin") or 0),
                "tick_size": v.get("tick_size"),
                "pair": v.get("wsname"),
                "ts": d.get("updated", 0)}
    kp = _kpair(pair)
    p = BH / "logs" / "pair_info.json"
    cache = _load(p, {})
    c = cache.get(kp)
    if c and time.time() - c.get("ts", 0) < 86400:
        return c
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.kraken.com/0/public/AssetPairs?pair={kp}",
            headers={"User-Agent": "BlackHorse/1.0"}), timeout=15).read())
        v = next(iter(d["result"].values()))
        out = {"pair_decimals": int(v["pair_decimals"]), "lot_decimals": int(v["lot_decimals"]),
               "ordermin": float(v.get("ordermin") or 0), "costmin": float(v.get("costmin") or 0),
               "ts": time.time()}
        cache[kp] = out
        p.write_text(json.dumps(cache, indent=2))
        return out
    except Exception:
        return c or {"pair_decimals": 4, "lot_decimals": 8, "ordermin": 0.0, "costmin": 0.0, "ts": 0}


def floor_to(x: float, decimals: int) -> float:
    m = 10 ** decimals
    return math.floor(x * m + 1e-9) / m


if __name__ == "__main__":
    pr = sys.argv[1] if len(sys.argv) > 1 else "DOT/USD"
    print(json.dumps({"fees": base_fees(pr), "pair": pair_info(pr)}, indent=2))
