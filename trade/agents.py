"""agents.py — BlackHorse is the brain HOUSE; agents are the bodies.

An agent is a persistent "body" owned by the brain. Kinds include:
  * trading   — trades via the decider under its own policy
  * youtube   — runs a channel (body with a voice) [scaffold]
  * other     — reserved

Each trading agent has a PROFILE (its personality/risk posture) and its own
capital allocation from the treasury. Agents can be created, enabled/disabled,
and capitalized entirely from the GUI. Nothing hardcoded — profiles and agents
live in config/agents.json.

CLI:
  python3 trade/agents.py profiles
  python3 trade/agents.py list
  python3 trade/agents.py create --kind trading --profile quick --capital 50
  python3 trade/agents.py create --kind trading --profile slow --capital 50
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

# Profiles are personalities: rule overrides layered over the decider defaults.
PROFILES = {
    "quick": {
        "label": "Quick / scalper — fast money",
        "description": "Hunts fast lower-timeframe moves (ripple/wave). Buys strength near 0, "
                       "sells near 1, tight stops, small risk per trade. Wants turnover.",
        "kind": "trading",
        "cfg": {
            "channels": ["ripple", "wave"],
            "entry_strength_max": 0.20,
            "exit_strength_min": 0.60,
            "min_reward_risk": 1.2,
            "stop_max_pct": 1.5,
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 10.0,
            "require_tide_context": False,
        },
    },
    "slow": {
        "label": "Slow / higher-timeframe — patient, best average price",
        "description": "Only trades the daily (tide/1440). Waits for deep strength~0 to buy and "
                       "strength~1 to sell near the best average price. Wide stop, larger target. "
                       "Holds out for the best range level.",
        "kind": "trading",
        "cfg": {
            "channels": ["tide"],
            "entry_strength_max": 0.15,
            "exit_strength_min": 0.80,
            "min_reward_risk": 2.0,
            "stop_max_pct": 5.0,
            "risk_per_trade_pct": 2.0,
            "max_position_pct": 25.0,
            "require_tide_context": False,
        },
    },
    "lifecycle": {
        "label": "Lifecycle (spot) — trade the REAL proven cells",
        "description": "Spot-only money path built on the REAL walk-forward result: the 60m "
                       "interval is OUT_OF_SAMPLE_EDGE (entry strength<=0.20, exit>=0.85, "
                       "recovery RISING, net of fees). ENTER on a proven low-strength floor, "
                       "EXIT on a proven high-strength roof. No shorting — a SELL only fires "
                       "when inventory is held.",
        "kind": "trading",
        "cfg": {
            "channels": ["ripple"],                 # ripple = 60m (the proven interval)
            "entry_strength_max": 0.20,             # real promoted setting
            "exit_strength_min": 0.85,              # real promoted setting
            "min_reward_risk": 1.2,
            "stop_max_pct": 3.0,
            "stop_buffer_pct": 0.6,
            "risk_per_trade_pct": 2.0,
            "max_position_pct": 30.0,
            "require_tide_context": False,
            "require_proven_edge": True,            # only proven cells (60m now)
            "spot_only": True,                       # exits only, never a short
        },
    },
}


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


def profiles() -> dict:
    return PROFILES


def list_agents(kind: str | None = None) -> list:
    d = _load()
    out = []
    for a in d["agents"].values():
        if kind and a.get("kind") != kind:
            continue
        out.append(_summary(a))
    return out


def _summary(a: dict) -> dict:
    return {"id": a["id"], "name": a.get("name"), "kind": a.get("kind"),
            "profile": a.get("profile"), "enabled": a.get("enabled", False),
            "capital": a.get("capital", 0), "created": a.get("created"),
            "profile_label": PROFILES.get(a.get("profile"), {}).get("label")}


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
    """Real spendable capital: the exchange truth in LIVE, else treasury."""
    try:
        import treasury
        return float(treasury.status().get("available", 0))
    except Exception:
        return 0.0


def create_agent(kind: str, profile: str, name: str = None, capital: float = 0,
                 allow_over: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "trading" and profile not in PROFILES:
        raise ValueError(f"unknown profile '{profile}'. Known: {', '.join(PROFILES)}")
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
    d["agents"][agent_id] = {
        "id": agent_id,
        "name": name or f"{PROFILES.get(profile, {}).get('label', kind).split(' ')[0]} {n}",
        "kind": kind,
        "profile": profile if kind == "trading" else None,
        "enabled": False,
        "capital": float(capital),
        "policy_overrides": {},
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


def audited() -> dict:
    """Truth check: how much real capital vs what agents claim."""
    avail = _available_capital()
    claimed = _total_allocated()
    return {"available": round(avail, 2), "agents_claim": round(claimed, 2),
            "over_allocated": claimed > avail + 1e-9,
            "overshoot": round(max(0.0, claimed - avail), 2)}


def set_override(agent_id: str, key: str, value) -> dict:
    d = _load(); a = get_agent(agent_id)
    a.setdefault("policy_overrides", {})[key] = value
    d["agents"][agent_id] = a
    _save(d)
    return _summary(a)


def policy_for(agent_id: str) -> dict:
    """Effective decider policy: defaults -> profile -> agent overrides."""
    a = get_agent(agent_id)
    cfg = dict(_decider_defaults())
    prof = PROFILES.get(a.get("profile"))
    if prof:
        cfg.update(prof.get("cfg", {}))
    cfg.update(a.get("policy_overrides") or {})
    cfg["_agent"] = {"id": a["id"], "name": a.get("name"), "profile": a.get("profile"),
                     "capital": a.get("capital")}
    return cfg


def trading_agents(enabled_only: bool = True) -> list:
    return [a for a in list_agents("trading") if (a["enabled"] or not enabled_only)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("profiles"); sub.add_parser("list")
    c = sub.add_parser("create"); c.add_argument("--kind", default="trading"); c.add_argument("--profile", required=True); c.add_argument("--name"); c.add_argument("--capital", type=float, default=0)
    e = sub.add_parser("enable"); e.add_argument("id")
    di = sub.add_parser("disable"); di.add_argument("id")
    ca = sub.add_parser("capital"); ca.add_argument("id"); ca.add_argument("amount", type=float)
    po = sub.add_parser("policy"); po.add_argument("id")
    args = ap.parse_args()
    cmd = args.cmd or "list"
    if cmd == "profiles": print(json.dumps(profiles(), indent=2))
    elif cmd == "list": print(json.dumps(list_agents(), indent=2))
    elif cmd == "create": print(json.dumps(create_agent(args.kind, args.profile, args.name, args.capital), indent=2))
    elif cmd == "enable": print(json.dumps(set_enabled(args.id, True), indent=2))
    elif cmd == "disable": print(json.dumps(set_enabled(args.id, False), indent=2))
    elif cmd == "capital": print(json.dumps(set_capital(args.id, args.amount), indent=2))
    elif cmd == "policy": print(json.dumps(policy_for(args.id), indent=2))
