"""agents.py — BlackHorse is the brain HOUSE; agents are the bodies.

An agent is a persistent "body" owned by the brain. Kinds include:
  * trading   — trades via the decider under its own policy
  * youtube   — runs a channel (body with a voice) [scaffold]
  * other     — reserved

Each trading agent trades exactly ONE entry interval and carries its own settings
(the interval's defaults, editable). Above the entry interval it may climb a ladder
of higher intervals (the strength climb), toggled per agent. Nothing hardcoded —
interval defaults live here; agents live in config/agents.json.

CLI:
  python3 trade/agents.py intervals
  python3 trade/agents.py list
  python3 trade/agents.py create --kind trading --profile 15m --capital 50
  python3 trade/agents.py enable agent_1 | disable agent_1
  python3 trade/agents.py capital agent_1 75
  python3 trade/agents.py policy agent_1     # the effective decider policy
"""
import json, sys, time, argparse, secrets as _secrets
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
REG = BH / "config" / "agents.json"
DECIDER = BH / "config" / "decider.json"

KINDS = ("trading", "youtube", "other")

# INTERVALS ARE THE PROFILES. There is no global personality layer anymore: the
# agent's entry interval carries its own default settings, and the ladder above
# it (the strength climb) is user-controlled in the GUI, one toggle per interval.
# These are just the DEFAULT settings each interval starts with — editable per agent.
INTERVAL_DEFAULTS = {
    "5m": {
        "label": "5m — scalps at speed",
        "description": "Fastest lane. Trades the 5m floor/roof; by default it must climb "
                       "15m and 60m before it may move (confirm we are at the floor).",
        "cfg": {"entry_strength_max": 0.25, "exit_strength_min": 0.75,
                "min_reward_risk": 1.2, "stop_max_pct": 1.5,
                "risk_per_trade_pct": 1.0, "max_position_pct": 10.0},
        "climb": ["15m", "60m"],
    },
    "15m": {
        "label": "15m — the day trade",
        "description": "The workhorse lane. By default it must climb 60m and 240m before it "
                       "may move. Turn the climb off to reproduce the old flat scalp.",
        "cfg": {"entry_strength_max": 0.25, "exit_strength_min": 0.75,
                "min_reward_risk": 1.2, "stop_max_pct": 2.0,
                "risk_per_trade_pct": 1.5, "max_position_pct": 10.0},
        "climb": ["60m", "240m"],
    },
    "60m": {
        "label": "60m — the proven interval",
        "description": "The walk-forward proven lane (OUT_OF_SAMPLE edge at 0.20/0.85). "
                       "By default it must climb 240m and 1440m before it may move.",
        "cfg": {"entry_strength_max": 0.20, "exit_strength_min": 0.85,
                "min_reward_risk": 1.5, "stop_max_pct": 3.0,
                "risk_per_trade_pct": 2.0, "max_position_pct": 30.0},
        "climb": ["240m", "1440m"],
    },
    "240m": {
        "label": "240m — slow swing",
        "description": "Patient lane. Only 1440m sits above it, so its lone default climb "
                       "rung is the daily — deep-floor confirmation only.",
        "cfg": {"entry_strength_max": 0.15, "exit_strength_min": 0.80,
                "min_reward_risk": 2.0, "stop_max_pct": 5.0,
                "risk_per_trade_pct": 2.0, "max_position_pct": 25.0},
        "climb": ["1440m"],
    },
    "1440m": {
        "label": "1440m — the daily (top of the ladder)",
        "description": "The biggest lane in the house — nothing sits above it, so there is "
                       "nothing to climb. Trades its own strength alone.",
        "cfg": {"entry_strength_max": 0.15, "exit_strength_min": 0.80,
                "min_reward_risk": 2.0, "stop_max_pct": 5.0,
                "risk_per_trade_pct": 2.0, "max_position_pct": 25.0},
        "climb": [],
    },
}

# Intervals are the profiles; snapshot channels are ANONYMOUS lanes. We never
# assume ripple=15m / wave=60m / tide=240m — a lane is identified only by its own
# 'interval' field, so the user can point any interval at any slot.
_IV_ORDER = ["5m", "15m", "60m", "240m", "1440m"]


def _load() -> dict:
    try:
        d = json.loads(REG.read_text())
        d.setdefault("agents", {})
        d.setdefault("next_id", 1)
        return d
    except (json.JSONDecodeError, OSError):
        return {"agents": {}, "next_id": 1}


def _save(d: dict):
    REG.parent.mkdir(parents=True, exist_ok=True)
    tmp = REG.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(REG)


def _decider_defaults() -> dict:
    try:
        return json.loads(DECIDER.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def interval_defaults() -> dict:
    return INTERVAL_DEFAULTS


def list_agents(kind: str | None = None) -> list:
    d = _load()
    out = []
    for a in d["agents"].values():
        if kind and a.get("kind") != kind:
            continue
        out.append(_summary(a))
    return out


def _summary(a: dict) -> dict:
    ov = a.get("policy_overrides") or {}
    ivs = _as_intervals(ov.get("strategy_intervals")) or _as_intervals(ov.get("channels"))
    entry = ivs[0] if ivs else None
    return {"id": a["id"], "name": a.get("name"), "kind": a.get("kind"),
            "profile": a.get("profile"), "enabled": a.get("enabled", False),
            "capital": a.get("capital", 0), "created": a.get("created"),
            "pairs": a.get("pairs"), "mode": a.get("mode"),
            "profile_label": f"{entry} entry" if entry else "no interval",
            "strategy_intervals": ivs}


def _as_intervals(seq):
    """Keep only entries that are real interval labels ('5m','60m','1440m',...).
    Legacy channel NAMES (ripple/wave/tide) cannot be mapped to an interval here
    (that needs the live snapshot), so they are dropped — the config now stores
    intervals directly."""
    seen, out = set(), []
    for x in (seq or []):
        s = str(x or "").strip()
        if len(s) > 1 and s.endswith("m") and s[:-1].isdigit() and s not in seen:
            seen.add(s); out.append(s)
    return out


def get_agent(agent_id: str) -> dict:
    d = _load()
    a = d["agents"].get(agent_id)
    if not a:
        raise KeyError(f"unknown agent '{agent_id}'")
    return a


def _total_allocated(exclude: str = None) -> float:
    return sum(float(a.get("capital", 0)) for a in list_agents()
               if a["id"] != exclude and a.get("kind") == "trading")


def _available_capital() -> float:
    """Capital that exists to be allocated: the exchange truth (free cash + cash already
    deployed in positions/orders), else treasury."""
    try:
        import capital
        eq = capital.equity()
        if eq > 0:
            return eq
    except Exception:
        pass
    try:
        import treasury
        return float(treasury.status().get("available", 0))
    except Exception:
        return 0.0


def create_agent(kind: str, profile: str = "15m", name: str = None, capital: float = 0,
                 allow_over: bool = False, mode: str = "PAPER") -> dict:
    """Create an agent. `profile` is now just a START TEMPLATE: pass an interval key
    (5m/15m/60m/240m/1440m) and the agent is seeded with that interval's default
    settings + default strength climb. Anything may still be edited afterwards."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    seed = INTERVAL_DEFAULTS.get(profile) if kind == "trading" else None
    if kind == "trading" and not allow_over:
        avail = _available_capital()
        would = _total_allocated() + float(capital)
        if would > avail + 1e-9:
            raise ValueError(
                f"over-allocation: agents would hold {would:.2f} but only {avail:.2f} is "
                f"available. Fund the account or allocate less."
                + (" (set allow_over to override)" if False else ""))
    d = _load()
    n = d["next_id"]
    agent_id = f"agent_{n}"
    d["next_id"] = n + 1
    mode = str(mode or "PAPER").upper()
    if mode not in ("PAPER", "LIVE"):
        mode = "PAPER"
    name = name or f"{seed['label'].split(' ')[0] if seed else kind} {n}"
    ov = {}
    if seed:
        ov = dict(seed["cfg"])
        ivs = [profile] + [x for x in _IV_ORDER if x in seed.get("climb", [])]
        ov["strategy_intervals"] = ivs
    d["agents"][agent_id] = {
        "id": agent_id,
        "name": name,
        "kind": kind,
        "profile": None,
        "enabled": False,
        "capital": float(capital),
        "mode": mode,
        "policy_overrides": ov,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save(d)
    return d["agents"][agent_id]


def delete_agent(agent_id: str) -> dict:
    d = _load()
    if agent_id not in d["agents"]:
        raise KeyError(f"unknown agent {agent_id}")
    del d["agents"][agent_id]
    _save(d)
    return {"deleted": agent_id}


def set_enabled(agent_id: str, enabled: bool) -> dict:
    d = _load(); a = get_agent(agent_id)
    a["enabled"] = bool(enabled)
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def set_capital(agent_id: str, amount: float, allow_over: bool = False) -> dict:
    d = _load(); a = get_agent(agent_id)
    if a.get("kind") == "trading" and not allow_over:
        avail = _available_capital()
        would = _total_allocated(exclude=agent_id) + float(amount)
        if would > avail + 1e-9:
            raise ValueError(
                f"over-allocation: agents would hold {would:.2f} but only {avail:.2f} "
                f"is available (other agents already claim "
                f"{_total_allocated(exclude=agent_id):.2f}).")
    a["capital"] = float(amount)
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def _pair_price(pair: str):
    try:
        import signals
        sig = signals.load(pair)
        for c in (sig.get("channels") or {}).values():
            if c.get("currPrice"):
                return float(c["currPrice"])
    except Exception:
        pass
    try:
        import execute
        return execute.kraken_last_price(pair)
    except Exception:
        return None


def audited() -> dict:
    """Truth check. Capital is each agent's TOTAL budget; what it has deployed counts
    against it. Over-allocated only if budgets can't all be funded from what exists."""
    import capital
    free = capital.free(); dep_all = capital.deployed(); equity = round(free + dep_all, 2)
    rows, remaining_total, cap_total = [], 0.0, 0.0
    for a in list_agents("trading"):
        cap = float(a.get("capital", 0)); dep = capital.deployed(a["id"])
        left = max(0.0, cap - dep)
        cap_total += cap
        if a.get("enabled"):
            remaining_total += left
        row = {"id": a["id"], "name": a.get("name"), "enabled": a.get("enabled"), "capital": round(cap, 2),
               "deployed": round(dep, 2), "remaining": round(left, 2),
               "spendable": round(min(left, free), 2)}
        try:                                    # can this agent's own caps even place a minimum order?
            import fees
            pol = policy_for(a["id"])
            pair = (a.get("pairs") or ["DOT/USD"])[0]
            px = _pair_price(pair); info = fees.pair_info(pair); _, tk = fees.rates(pair)
            if px:
                mp = float(pol.get("max_position_pct", 100)) / 100
                min_q = max(float(info.get("ordermin") or 0) * px, float(info.get("costmin") or 0)) * (1 + tk)
                max_q = min(left, free) * mp / (1 + tk)
                row.update({"pair": pair, "min_order": round(min_q, 2), "max_order": round(max_q, 2),
                            "can_trade": max_q + 1e-9 >= min_q,
                            "capital_needed": round(min_q * (1 + tk) / mp, 2)})
        except Exception:
            pass
        rows.append(row)
    over = remaining_total > free + 1e-9 or cap_total > equity + 1e-9
    return {"available": round(free, 2), "agents_claim": round(remaining_total, 2),
            "capital_total": round(cap_total, 2), "deployed": round(dep_all, 2), "equity": equity,
            "over_allocated": over,
            "overshoot": round(max(0.0, remaining_total - free, cap_total - equity), 2),
            "agents": rows}


def set_pairs(agent_id: str, pairs: list) -> dict:
    d = _load(); a = get_agent(agent_id)
    a['pairs'] = pairs if isinstance(pairs, list) else pairs
    d['agents'][agent_id] = a
    _save(d)
    return _summary(a)


def set_mode(agent_id: str, mode: str) -> dict:
    """Per-agent PAPER/LIVE. PAPER never places a live order — fills are simulated
    and booked as paper positions. LIVE sends real orders to the venue."""
    m = str(mode or "PAPER").upper()
    if m not in ("PAPER", "LIVE"):
        raise ValueError("mode must be PAPER or LIVE")
    d = _load(); a = get_agent(agent_id)
    a["mode"] = m
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def set_override(agent_id: str, key: str, value) -> dict:
    d = _load(); a = get_agent(agent_id)
    a.setdefault("policy_overrides", {})[key] = value
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def clear_override(agent_id: str, key: str) -> dict:
    """Remove one policy override (e.g. a legacy 'channels' name list)."""
    d = _load(); a = get_agent(agent_id)
    (a.get("policy_overrides") or {}).pop(key, None)
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def policy_for(agent_id: str) -> dict:
    """Effective decider policy: decider.json defaults -> this agent's own overrides.
    There are no profiles anymore — the agent's settings ARE its rulebook."""
    a = get_agent(agent_id)
    cfg = dict(_decider_defaults())
    cfg.update(a.get("policy_overrides") or {})
    cfg["_agent"] = {"id": a["id"], "name": a.get("name"), "profile": a.get("profile"),
                     "capital": a.get("capital")}
    return cfg


def trading_agents(enabled_only: bool = True) -> list:
    return [a for a in list_agents("trading") if (a["enabled"] or not enabled_only)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("intervals"); sub.add_parser("list")
    c = sub.add_parser("create"); c.add_argument("--kind", default="trading"); c.add_argument("--profile", default="15m"); c.add_argument("--name"); c.add_argument("--capital", type=float, default=0)
    e = sub.add_parser("enable"); e.add_argument("id")
    di = sub.add_parser("disable"); di.add_argument("id")
    ca = sub.add_parser("capital"); ca.add_argument("id"); ca.add_argument("amount", type=float)
    po = sub.add_parser("policy"); po.add_argument("id")
    args = ap.parse_args()
    cmd = args.cmd or "list"
    if cmd == "intervals": print(json.dumps(interval_defaults(), indent=2))
    elif cmd == "list": print(json.dumps(list_agents(), indent=2))
    elif cmd == "create": print(json.dumps(create_agent(args.kind, args.profile, args.name, args.capital), indent=2))
    elif cmd == "enable": print(json.dumps(set_enabled(args.id, True), indent=2))
    elif cmd == "disable": print(json.dumps(set_enabled(args.id, False), indent=2))
    elif cmd == "capital": print(json.dumps(set_capital(args.id, args.amount), indent=2))
    elif cmd == "policy": print(json.dumps(policy_for(args.id), indent=2))
