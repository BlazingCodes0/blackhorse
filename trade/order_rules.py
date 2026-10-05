# pair-level crude notional guards before Kraken can reject the size.
MIN_QUOTE = {"DOT/USD": 5.0, "ETH/USD": 10.0, "BTC/USD": 20.0, "SOL/USD": 15.0, "XRP/USD": 10.0}
MIN_BASE = {"DOT/USD": 1.0, "ETH/USD": 0.002, "BTC/USD": 0.0001, "SOL/USD": 0.5, "XRP/USD": 5.0}

def assess_size(pair, entry, size_quote):
    try:
        quote = float(size_quote or 0)
        ent = float(entry or 0)
    except Exception:
        return "no valid entry size"
    base = {"DOT/USD": "DOT", "ETH/USD": "ETH", "BTC/USD": "BTC", "SOL/USD": "SOL",
            "XRP/USD": "XRP"}.get(pair, "BASE")
    min_quote = MIN_QUOTE.get(pair, 5.0)
    min_base = MIN_BASE.get(pair, 0.001)
    if not ent:
        return "no valid entry price"
    if quote <= 0:
        return "no size quote"
    vol_base = quote / ent
    if quote < min_quote:
        return f"size_quote ${quote:.2f} < min ${min_quote:.2f} for {pair}"
    if vol_base < min_base:
        return f"base volume {vol_base:.8f} {base} < min {min_base:.8f}"
    return None
