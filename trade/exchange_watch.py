"""exchange_watch.py — slow periodic fetch of REAL exchange info for ALL pairs.

Why: orders size themselves off fees + decimals, and the edge/profit proof must
use the SAME numbers the exchange will charge. Guessing, or caching a stale fee,
is how fees end up "eating the profit". So we watch the exchange on a slow timer.

Each sweep is BATCHED (no per-pair hammering):
  * public  /0/public/AssetPairs   -> metadata for every pair: pair_decimals,
      lot_decimals, ordermin, costmin, tick_size, status, base/quote, and the
      public per-volume-tier maker/taker fee schedule.
  * private /0/private/TradeVolume -> the account's REAL maker/taker fee per pair
      (`fee`) plus the tier-1 base (`maxfee`).

Result: one consolidated file, logs/exchange_info.json. trade/fees.py reads it and
prefers the ACCOUNT fee (your real tier) over the base fee. Disk-first: a restart
keeps the last good snapshot.

CLI:
  python3 trade/exchange_watch.py --once         # one sweep
  python3 trade/exchange_watch.py --loop 21600   # sweep every 6h (slow)
  python3 trade/exchange_watch.py --show         # summarize the current cache
"""
import argparse, json, os, sys, time, urllib.request
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
INFO = BH / "logs" / "exchange_info.json"
FALLBACK = BH / "config" / "exchange_fees.json"
TRADING = BH / "config" / "trading.json"
UA = {"User-Agent": "BlackHorse/1.0"}
DEFAULT_INTERVAL = 6 * 3600            # slow: every 6 hours
BASE_FALLBACK = {"maker_pct": 0.40, "taker_pct": 0.80}


def interval_seconds() -> int:
    """Slow cadence, tunable via config/trading.json: exchange_watch_seconds."""
    cfg = _load(TRADING, {})
    try:
        return max(60, int(cfg.get("exchange_watch_seconds", DEFAULT_INTERVAL)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


def _load(p, d):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return d


def _fallback() -> dict:
    f = _load(FALLBACK, {}).get("kraken", {})
    return {"maker_pct": float(f.get("maker_pct", BASE_FALLBACK["maker_pct"])),
            "taker_pct": float(f.get("taker_pct", BASE_FALLBACK["taker_pct"])),
            "source": "config/exchange_fees.json"}


def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _int(x, default=None):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _first_tier(arr):
    """Lowest-volume tier of a Kraken [[volume, fee], ...] schedule (percent)."""
    try:
        return float(arr[0][1])
    except (TypeError, ValueError, IndexError):
        return None


def _split(entry, wsname):
    if wsname and "/" in wsname:
        b, q = wsname.split("/", 1)
        return b, q
    return str(entry.get("base", "") or "").upper(), str(entry.get("quote", "") or "").upper()


def asset_pairs() -> dict:
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.kraken.com/0/public/AssetPairs", headers=UA), timeout=30).read())
    return d.get("result") or {}


def trade_volume():
    """Private account fees; None if creds/API unavailable (public still works)."""
    try:
        import execute
        r = execute._kraken_private("/0/private/TradeVolume", {"nonce": str(int(time.time() * 1000))})
        if r.get("error"):
            return None
        return r.get("result") or {}
    except Exception:
        return None


def sweep(write: bool = True) -> dict:
    fb = _fallback()
    ap = asset_pairs()
    tv = trade_volume() or {}
    fees = tv.get("fees") or {}
    makers = tv.get("fees_maker") or {}

    pairs = {}
    for key, entry in ap.items():
        wsname = entry.get("wsname") or ""
        base, quote = _split(entry, wsname)
        pub_taker = _first_tier(entry.get("fees"))
        pub_maker = _first_tier(entry.get("fees_maker"))
        acc_taker = _num((fees.get(key) or {}).get("fee"))
        acc_maker = _num((makers.get(key) or {}).get("fee"))
        base_taker = _num((fees.get(key) or {}).get("maxfee"), pub_taker)
        base_maker = _num((makers.get(key) or {}).get("maxfee"), pub_maker)

        taker = acc_taker if acc_taker is not None else (base_taker if base_taker is not None else fb["taker_pct"])
        maker = acc_maker if acc_maker is not None else (base_maker if base_maker is not None else fb["maker_pct"])
        if acc_taker is not None or acc_maker is not None:
            src = "account"
        elif base_taker is not None or base_maker is not None:
            src = "tier1"
        elif pub_taker is not None or pub_maker is not None:
            src = "public"
        else:
            src = "fallback"

        rec = {
            "wsname": wsname or (f"{base}/{quote}" if base and quote else key),
            "base": base, "quote": quote,
            "status": entry.get("status"),
            "pair_decimals": _int(entry.get("pair_decimals")),
            "lot_decimals": _int(entry.get("lot_decimals")),
            "tick_size": entry.get("tick_size"),
            "ordermin": _num(entry.get("ordermin"), 0.0),
            "costmin": _num(entry.get("costmin"), 0.0),
            "lot_multiplier": _num(entry.get("lot_multiplier")),
            "maker_pct": maker, "taker_pct": taker,
            "base_maker_pct": base_maker, "base_taker_pct": base_taker,
            "source": src,
        }
        if acc_maker is not None or acc_taker is not None:
            rec["account_maker_pct"] = acc_maker
            rec["account_taker_pct"] = acc_taker
        pairs[key] = rec

    out = {
        "exchange": "kraken",
        "updated": time.time(),
        "updated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "have_account_fees": bool(fees or makers),
        "n_pairs": len(pairs),
        "pairs": pairs,
    }
    if write:
        INFO.parent.mkdir(parents=True, exist_ok=True)
        tmp = INFO.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=2))
        os.replace(tmp, INFO)
    return {"ok": True, "n_pairs": len(pairs), "have_account_fees": out["have_account_fees"],
            "updated": out["updated_iso"], "file": str(INFO)}


def age() -> float:
    """Seconds since the last snapshot (1e9 if none) — cheap, no JSON parse."""
    try:
        return time.time() - INFO.stat().st_mtime
    except OSError:
        return 1e9


def load() -> dict:
    return _load(INFO, {})


def find(pair: str, data: dict = None) -> dict:
    """Look up one pair's record. Accepts 'DOT/USD', 'DOTUSD', 'dot/usd'."""
    data = data if data is not None else load()
    pairs = data.get("pairs") or {}
    want = str(pair).replace("/", "").replace("-", "").replace("XBT", "BTC").upper()
    for key, rec in pairs.items():
        if key.upper() == want:
            return rec
        w = str(rec.get("wsname", "")).replace("/", "").replace("-", "").replace("XBT", "BTC").upper()
        if w == want:
            return rec
    return {}


def summary() -> dict:
    d = load()
    if not d:
        return {"have": False, "hint": "run: python3 trade/exchange_watch.py --once"}
    age = round(time.time() - d.get("updated", 0))
    return {"have": True, "n_pairs": d.get("n_pairs"), "updated": d.get("updated_iso"),
            "age_seconds": age, "have_account_fees": d.get("have_account_fees"),
            "sample": {k: d["pairs"][k] for k in list(d.get("pairs", {}))[:3]}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, nargs="?", const=interval_seconds(), default=None)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    if args.show:
        print(json.dumps(summary(), indent=2))
    elif args.loop:
        interval = max(60, args.loop)
        print(f"exchange watch — sweep every {interval}s")
        while True:
            try:
                print(json.dumps(sweep(), indent=2))
            except Exception as e:
                print(json.dumps({"ok": False, "error": str(e)}))
            time.sleep(interval)
    else:
        print(json.dumps(sweep(), indent=2))
