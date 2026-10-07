"""observer.py — notes how the market is behaving. Notes only. No rules, no orders.

Every cycle it records an OBSERVATION (ephemeral memory) combining:
  * direction      (from price run / trend / MA spread — read independently of advisor)
  * per channel    strength + strengthDirection, trend, spread
  * advisor sides  buy/sell VALUE and whether each side is MISSING (null/0/empty)
  * fees context   (so outcomes can be judged net of cost later)

The point (user's insight): a missing side isn't "no data" — it's *which side has
no flow*. Falling + buy-missing likely CONFIRMS the fall; but that is a hypothesis
to be tested by outcomes, not a rule. So we NOTE, tag an outcome horizon, and let
the data decide.

Observations are ephemeral memory (deletable). Lessons (permanent) are only ever
written by consolidation and approved by the human.

CLI:
  python3 trade/observer.py note [--pair DOT/USD]     # run signal + note one cycle
  python3 trade/observer.py note --from-latest        # note from existing signal_latest.json
  python3 trade/observer.py report [--limit 20]
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "memory"))
sys.path.insert(0, str(BH / "trade"))
LATEST = BH / "logs" / "signal_latest.json"


def _load_signal():
    try:
        return json.loads(LATEST.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _missing(v) -> bool:
    if v is None or v == "":
        return True
    try:
        return float(v) == 0
    except (TypeError, ValueError):
        return True


def _direction(channel: dict) -> str:
    """Read direction independently of the advisor side availability."""
    sd = channel.get("strengthDirection")
    ts = channel.get("trendState")
    spread = channel.get("spreadDirection")
    if ts in ("rising",):
        return "rising"
    if ts in ("falling",):
        return "falling"
    if sd == "rising" and ts in ("about_to_turn", "range", None):
        return "rising"
    if sd == "falling":
        return "falling"
    if spread == "improving":
        return "rising"
    if spread == "weakening":
        return "falling"
    return "flat"


def observe(signal: dict) -> dict:
    chans = {}
    for name, c in (signal.get("channels") or {}).items():
        adv = c.get("advisor") or {}
        buy = adv.get("buy")
        sell = adv.get("sell")
        buy_missing = _missing(buy)
        sell_missing = _missing(sell)
        chans[name] = {
            "interval": c.get("interval"),
            "price": c.get("currPrice"),
            "direction": _direction(c),
            "strength": c.get("strength"),
            "strengthDirection": c.get("strengthDirection"),
            "trendState": c.get("trendState"),
            "spreadDirection": c.get("spreadDirection"),
            "action": c.get("action"),
            "buy": buy, "buy_missing": buy_missing,
            "sell": sell, "sell_missing": sell_missing,
            "sides_missing": (int(buy_missing) + int(sell_missing)),
        }
    return {
        "pair": signal.get("pair"),
        "exchange": signal.get("exchange"),
        "strategy": signal.get("strategy"),
        "price": next((c["price"] for c in chans.values() if c.get("price")), None),
        "channels": chans,
        "crowd": _crowd(),
    }


def _crowd() -> dict:
    """Read-only derivatives sentiment (open interest, funding, long/short)."""
    try:
        import crowd
        c = crowd.get()
        interp = c.get("interpretation") or {}
        return {"funding_rate": c.get("funding_rate"), "long_short_ratio": c.get("long_short_ratio"),
                "oi_change_pct_5h": c.get("oi_change_pct_5h"),
                "bias_hint": interp.get("bias_hint"), "notes": interp.get("notes")}
    except Exception as e:
        return {"error": str(e)}


def _summary(obs: dict) -> str:
    parts = []
    for name in ("ripple", "wave", "tide"):
        c = obs["channels"].get(name)
        if not c:
            continue
        b = "n/a" if c["buy_missing"] else c["buy"]
        s = "n/a" if c["sell_missing"] else c["sell"]
        parts.append(f"{name} {c['interval']} {c['direction']} "
                     f"str={round(c['strength'] or 0,3)} buy={b} sell={s}")
    crowd = obs.get("crowd") or {}
    extra = f" | crowd={crowd.get('bias_hint')}" if crowd.get("bias_hint") else ""
    return f"{obs['pair']} @ {round(obs['price'] or 0,6)} :: " + " | ".join(parts) + extra


def note(pair="DOT/USD", use_latest=False) -> dict:
    if use_latest:
        import signals
        sig = signals.load(pair) or _load_signal()
        if not sig or not sig.get("available"):
            return {"ok": False, "error": "no signal_latest.json available"}
    else:
        import signalbot
        sig = signalbot.run(pair=pair)
        if not sig.get("available"):
            return {"ok": False, "error": sig.get("error", "signal failed")}
    obs = observe(sig)
    import index
    mem_id = index.remember(
        kind="observation",
        text=_summary(obs),
        meta=obs,
        source="observer",
    )
    return {"ok": True, "id": mem_id, "observation": obs}


def report(limit=20) -> list:
    import index
    return index.recent(kind="observation", limit=limit)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("note"); n.add_argument("--pair", default="DOT/USD"); n.add_argument("--from-latest", action="store_true")
    r = sub.add_parser("report"); r.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    cmd = args.cmd or "note"
    if cmd == "report":
        print(json.dumps(report(args.limit), indent=2))
    else:
        print(json.dumps(note(args.pair, args.from_latest), indent=2))
