"""crowd.py — READ-ONLY derivatives sentiment. We do NOT trade derivatives.

Open interest, funding rate, and long/short ratio are MARKET INFORMATION: they
show where the crowd is positioned and how the market is likely to move. Spot-only
bans the *execution venue*; it does not blind the brain. This module reads the
crowd so the brain can reason about positioning and squeeze/contrarian setups.

Hard rule enforced elsewhere (Heart): we never place a futures/perp/margin order.
This file only GETs public data.

Signals it derives (informational):
  * funding: positive = longs pay shorts (crowd long / greedy); negative = crowd short
  * open interest trend: rising OI + rising price = new longs (trend fuel);
    rising OI + falling price = new shorts (squeeze fuel);
    falling OI = positions closing (conviction leaving)
  * long/short ratio: extreme (>2 = crowded long = contrarian risk; <0.7 = crowded short)

CLI:  python3 trade/crowd.py --symbol DOTUSDT
"""
import json, sys, time, urllib.request, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
CACHE = BH / "logs" / "crowd_cache.json"

BASE = "https://fapi.binance.com"
PAIR_TO_SYMBOL = {"DOT/USD": "DOTUSDT", "BTC/USD": "BTCUSDT", "ETH/USD": "ETHUSDT",
                  "SOL/USD": "SOLUSDT", "XRP/USD": "XRPUSDT"}


def _get(url, timeout=10):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "BlackHorse/1.0"}), timeout=timeout))


def _symbol(symbol):
    s = str(symbol).upper().replace("/", "")
    if s in PAIR_TO_SYMBOL:
        return PAIR_TO_SYMBOL[s]
    if s.endswith("USDT") or s.endswith("USDC"):
        return s
    if s.endswith("USD"):
        return s[:-3] + "USDT"
    return s


def read(symbol="DOT/USDT") -> dict:
    s = _symbol(symbol)
    out = {"symbol": s, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "venue": "binance-futures", "tradeable": False,
           "note": "READ-ONLY market info. Derivatives trading is banned (spot-only)."}
    try:
        oi = _get(f"{BASE}/fapi/v1/openInterest?symbol={s}")
        out["open_interest"] = float(oi["openInterest"])
    except Exception as e:
        out["open_interest_error"] = str(e)
    try:
        pi = _get(f"{BASE}/fapi/v1/premiumIndex?symbol={s}")
        out["funding_rate"] = float(pi.get("lastFundingRate", 0))
        out["mark_price"] = float(pi.get("markPrice", 0))
    except Exception as e:
        out["funding_error"] = str(e)
    try:
        ls = _get(f"{BASE}/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=1")
        if ls:
            out["long_short_ratio"] = float(ls[0]["longShortRatio"])
            out["long_account_pct"] = round(float(ls[0]["longAccount"]) * 100, 1)
    except Exception as e:
        out["long_short_error"] = str(e)
    # open-interest trend (last hour vs previous)
    try:
        hist = _get(f"{BASE}/futures/data/openInterestHist?symbol={s}&period=1h&limit=6")
        if hist and len(hist) >= 2:
            first, last = float(hist[0]["sumOpenInterest"]), float(hist[-1]["sumOpenInterest"])
            out["oi_change_pct_5h"] = round((last - first) / first * 100, 2) if first else None
    except Exception as e:
        out["oi_hist_error"] = str(e)
    out["interpretation"] = _interpret(out)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out))
    return out


def _interpret(o: dict) -> dict:
    fr = o.get("funding_rate")
    lsr = o.get("long_short_ratio")
    oi = o.get("oi_change_pct_5h")
    notes = []
    if fr is not None:
        if fr > 0.0003:
            notes.append(f"funding {fr:+.4f} — longs pay shorts; crowd is long/greedy (contrarian caution)")
        elif fr < -0.0003:
            notes.append(f"funding {fr:+.4f} — shorts pay longs; crowd is short (possible short squeeze fuel)")
        else:
            notes.append(f"funding {fr:+.4f} — neutral positioning")
    if lsr is not None:
        if lsr >= 2.0:
            notes.append(f"long/short {lsr:.2f} — crowded long (downside risk if they unwind)")
        elif lsr <= 0.7:
            notes.append(f"long/short {lsr:.2f} — crowded short (upside squeeze risk)")
        else:
            notes.append(f"long/short {lsr:.2f} — balanced")
    if oi is not None:
        notes.append(f"open interest {oi:+.2f}% over 5h — "
                     + ("positions building" if oi > 0 else "positions unwinding"))
    return {"notes": notes,
            "bias_hint": ("crowd-long / contrarian-cautious" if (fr and fr > 0.0003) or (lsr and lsr >= 2)
                          else "crowd-short / squeeze-watch" if (fr and fr < -0.0003) or (lsr and lsr <= 0.7)
                          else "neutral")}


def get(symbol="DOT/USDT", max_age=300) -> dict:
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age:
        try:
            return json.loads(CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    try:
        return read(symbol)
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "tradeable": False}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="DOTUSDT")
    args = ap.parse_args()
    print(json.dumps(read(args.symbol), indent=2))
