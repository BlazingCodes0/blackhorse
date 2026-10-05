"""Market news + macro context for the brain.

SmartBot sees candles. It cannot see WHY the market is moving. This module
gathers free, key-less context and normalizes it into one object the brain can
reason over:

  * sentiment   : Fear & Greed index (alternative.me)
  * global      : total market cap, BTC dominance, 24h change (CoinGecko)
  * prices      : BTC/ETH/SOL + 24h change (CoinGecko)
  * headlines   : recent crypto news titles (CoinDesk RSS + CoinTelegraph RSS)
  * funding     : (optional) perpetual funding via a public endpoint if reachable
  * events      : (optional) known macro calendar hooks

Everything is best-effort: a source that fails is simply omitted, never fatal.
Results are cached to logs/news_cache.json for offline/stale fallback.

CLI:  python3 trade/news.py            # fetch + print JSON
      python3 trade/news.py --summary  # one-line human summary
"""
import json, time, sys, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

BH = Path.home() / "blackhorse"
CACHE = BH / "logs" / "news_cache.json"
TTL = 300  # seconds

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
}
COINS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "BlackHorse/1.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url, timeout=8):
    return json.loads(_get(url, timeout).decode("utf-8", "ignore"))


def fear_greed():
    try:
        d = _get_json("https://api.alternative.me/fng/?limit=1")["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception as e:
        return {"error": str(e)}


def global_market():
    try:
        d = _get_json("https://api.coingecko.com/api/v3/global")["data"]
        return {
            "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
            "market_cap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
            "btc_dominance": round(d.get("market_cap_percentage", {}).get("btc", 0), 2),
            "eth_dominance": round(d.get("market_cap_percentage", {}).get("eth", 0), 2),
        }
    except Exception as e:
        return {"error": str(e)}


def prices():
    try:
        ids = ",".join(COINS.values())
        d = _get_json(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}"
            "&vs_currencies=usd&include_24hr_change=true"
        )
        out = {}
        for sym, cid in COINS.items():
            row = d.get(cid, {})
            out[sym] = {"usd": row.get("usd"), "change_24h_pct": round(row.get("usd_24h_change", 0), 2)}
        return out
    except Exception as e:
        return {"error": str(e)}


def headlines(limit_per_feed=8):
    items = []
    for src, url in FEEDS.items():
        try:
            root = ET.fromstring(_get(url))
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                if title:
                    items.append({"source": src, "title": title[:200]})
                if len(items) >= limit_per_feed * len(FEEDS):
                    break
        except Exception:
            continue
    return items[: limit_per_feed * len(FEEDS)]


def fetch() -> dict:
    ctx = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fear_greed": fear_greed(),
        "global": global_market(),
        "prices": prices(),
        "headlines": headlines(),
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE.write_text(json.dumps(ctx))
    except OSError:
        pass
    return ctx


def get(max_age=TTL) -> dict:
    """Cached fetch; falls back to stale cache on network failure."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age:
        try:
            return json.loads(CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    try:
        return fetch()
    except Exception as e:
        if CACHE.exists():
            d = json.loads(CACHE.read_text())
            d["_stale"] = str(e)
            return d
        return {"ts": None, "error": str(e)}


def summary(ctx: dict) -> str:
    fg = ctx.get("fear_greed", {})
    g = ctx.get("global", {})
    p = ctx.get("prices", {})
    btc = p.get("BTC", {})
    return (
        f"F&G {fg.get('value','?')} ({fg.get('label','?')}) | "
        f"mcap 24h {g.get('market_cap_change_24h_pct','?')}% | "
        f"BTC dom {g.get('btc_dominance','?')}% | "
        f"BTC ${btc.get('usd','?')} ({btc.get('change_24h_pct','?')}% 24h) | "
        f"{len(ctx.get('headlines', []))} headlines"
    )


if __name__ == "__main__":
    ctx = get(0)
    if "--summary" in sys.argv:
        print(summary(ctx))
    else:
        print(json.dumps(ctx, indent=2))
