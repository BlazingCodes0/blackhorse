"""fees.py — the ACTIVE exchange's maker/taker fee + pair precision.

Delegates to trade/venues.py, so it follows whichever exchange is active:
  * Kraken  — the slow snapshot (logs/exchange_info.json, written by
              trade/exchange_watch.py) -> live TradeVolume/AssetPairs (cached in
              logs/fee_cache.json / logs/pair_info.json) -> config/exchange_fees.json.
  * Binance — exchangeInfo filters (pair precision) + the account's own tier from
              /sapi/v1/asset/tradeFee (cached), else config/exchange_fees.json.
  * Coinbase— product info (quote/base increment) + the account's own tier from
              /api/v3/brokerage/transaction_summary (cached); else config/exchange_fees.json.

Every order sizes itself with these numbers so cost + fee never exceeds cash.

CLI: python3 trade/fees.py [PAIR]
     python3 trade/exchange_watch.py --once   # refresh the Kraken snapshot
"""
import sys, json, math
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))

import venues


def base_fees(pair: str = "DOT/USD") -> dict:
    """{maker_pct, taker_pct, source} for the active exchange (percent)."""
    return venues.fee_rates(pair)


def rates(pair: str = "DOT/USD"):
    """(maker_frac, taker_frac) as fractions, e.g. (0.004, 0.008)."""
    f = base_fees(pair)
    return f["maker_pct"] / 100.0, f["taker_pct"] / 100.0


def pair_info(pair: str) -> dict:
    """pair_decimals / lot_decimals / ordermin / costmin (+ tick_size) for the
    active exchange, from its own product metadata."""
    return venues.pair_rules(pair)


def floor_to(x: float, decimals: int) -> float:
    m = 10 ** decimals
    return math.floor(x * m + 1e-9) / m


if __name__ == "__main__":
    pr = sys.argv[1] if len(sys.argv) > 1 else "DOT/USD"
    print(json.dumps({"exchange": venues.active_name(), "fees": base_fees(pr),
                      "pair": pair_info(pr)}, indent=2))
