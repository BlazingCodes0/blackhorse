"""state.py — self-registering state providers.

Spice's awareness is not a hardcoded list. Any module can declare a provider:

    from state import provider
    @provider("treasury")
    def _(): return {"available": ...}

snapshot() calls every registered provider and returns {name: value}. Adding a
new kind of awareness = adding a provider, never editing Spice.
"""
import time, sys
from pathlib import Path

BH = Path.home() / "blackhorse"
_PROVIDERS = {}


def provider(name):
    def deco(fn):
        _PROVIDERS[name] = fn
        return fn
    return deco


def names():
    return sorted(_PROVIDERS)


def snapshot(max_chars=3500) -> dict:
    out = {}
    for name, fn in _PROVIDERS.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)}
    return out


# ------------------------- built-in providers --------------------------------
# (each is just a registration; remove one and Spice simply stops seeing it)

@provider("capital")
def _capital():
    import treasury
    return {k: treasury.status().get(k) for k in
            ("allocated", "available", "cash", "reserve_floor", "open_risk", "realized_pnl", "currency")}


@provider("pnl")
def _pnl():
    import portfolio
    s = portfolio.status()
    return {"realized_pnl": s.get("realized_pnl"), "unrealized_pnl": s.get("unrealized_pnl"),
            "equity": s.get("equity"), "open_positions": len(s.get("positions", []))}


@provider("agents")
def _agents():
    import agents
    return agents.list_agents()


@provider("live_gate")
def _gate():
    import heart
    u = heart.unlock()
    return {"can_arm": u.get("can_arm"), "missing": u.get("missing")}


@provider("market")
def _market():
    import engine
    snap = engine.snapshot()
    out = {"news": (snap.get("news") or {}).get("summary")}
    sig = snap.get("signal") or {}
    if sig.get("available"):
        out["signal"] = {k: {"action": v.get("action"),
                             "strength": round(v.get("strength") or 0, 3)}
                         for k, v in (sig.get("channels") or {}).items()}
    return out


@provider("owner_preferences")
def _prefs():
    import growth
    return [r["text"] for r in growth.list_recent(kind="preference", limit=8)]


@provider("measured_edge")
def _edge():
    import edge
    return edge.brief()
