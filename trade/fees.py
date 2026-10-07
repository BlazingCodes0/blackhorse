"""fees.py — the exchange's BASE (entry-level, non-VIP) maker/taker fee.

Source of truth: Kraken private TradeVolume -> `maxfee` per pair, which is the
TIER-1 rate no matter what volume discount the account has earned. Fallback:
config/exchange_fees.json. Cached 6h in logs/fee_cache.json. Every order sizes
itself with these numbers so cost + fee never exceeds the cash available.

CLI: python3 trade/fees.py [PAIR]
"""
import json, sys, time, math, urllib.request
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
CACHE = BH / "logs" / "fee_cache.json"
FALLBACK = BH / "config" / "exchange_fees.json"
TTL = 6 * 3600


def _kpair(pair: str) -> str:
    k = pair.replace("/", "").upper()
    return k.replace("BTC", "XBT", 1) if k.startswith("BTC") else k


def _load(p, d):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return d


def _fallback() -> dict:
    f = _load(FALLBACK, {}).get("kraken", {})
    return {"maker_pct": float(f.get("maker_pct", 0.40)), "taker_pct": float(f.get("taker_pct", 0.80)),
            "source": "config/exchange_fees.json"}


def base_fees(pair: str = "DOT/USD") -> dict:
    """{"maker_pct","taker_pct","source"} — base tier, percent."""
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
    """pair_decimals / lot_decimals / ordermin / costmin from Kraken (cached 24h)."""
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
