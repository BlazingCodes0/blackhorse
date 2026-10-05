"""broker.py — BlackHorse arbitrates between enabled trading agents.

Each enabled trading agent runs the decider under its OWN policy and capital.
The broker combines their plans into one house decision:

  * one agent enabled        -> use its plan
  * both agree (same side)   -> take the higher-conviction plan
  * only one sees a setup    -> take it (the other simply has no signal on its frame)
  * CONFLICT (one BUY, one SELL) -> HOLD. Never fight yourself.
  * both HOLD                -> HOLD

Sizing respects each agent's own capital. Nothing here places orders.

CLI:
  python3 trade/broker.py            # decide across enabled trading agents
  python3 trade/broker.py --explain
"""
import json, sys, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))


def _verdict():
    try:
        import decider
        return decider._last_verdict()
    except Exception:
        return None


def decide(verdict=None, pair=None) -> dict:
    import agents, decider
    verdict = verdict if verdict is not None else _verdict()
    enabled = agents.trading_agents(enabled_only=True)
    if not enabled:
        return {"action": "HOLD", "reason": "no trading agents enabled",
                "agents": [], "explain": ["create/enable a trading agent first"]}

    plans = []
    for a in enabled:
        cfg = agents.policy_for(a["id"])
        # ONE shared pool: an agent's spendable is its own cap bounded by the free
        # cash pool (so two agents can't plan against the same dollar).
        try:
            import capital
            avail_for_agent = capital.agent_available(a["id"], a.get("capital", 0))
        except Exception:
            avail_for_agent = a.get("capital", 0)
        tre = {"available": avail_for_agent, "currency": "USD"}
        # pass held inventory for THIS agent's pairs so spot exits can fire
        holding = _held_by_agent(a["id"])
        d = decider.decide(verdict=verdict, treasury=tre, cfg=cfg, holding=holding, pair=pair)
        plans.append({"agent": a["id"], "name": a.get("name"), "profile": a.get("profile"),
                      "capital": a.get("capital"), "spendable": avail_for_agent,
                      "holding": holding, "decision": d})

    actionable = [p for p in plans if p["decision"].get("action") in ("BUY", "SELL")]
    sides = {p["decision"]["action"] for p in actionable}

    if not actionable:
        return {"action": "HOLD", "reason": "no agent has a setup", "agents": plans,
                "explain": [f"{p['agent']}({p['profile']}): HOLD — {p['decision'].get('reason','')}"
                            for p in plans]}

    # --- NO ESCALATION, NO AGENT ARGUMENTS.
    # If agents disagree on side, we stand down and record who wanted what.
    # It does NOT choose a winner. If they all want the same side, that is
    # same-signal confirmation, not an argument.
        d = p["decision"]
        # logical strength = conviction (score) * reward:risk, split out so the
        # broker can explain WHY one side won rather than silently picking one.
        return (d.get("score", 0) or 0) * (d.get("rr", 0) or 0)

    ranked = sorted(actionable, key=strength_of, reverse=True)
    if len(sides) == 1:
        best = ranked[0]
        winner_note = "agents agree on the same side"
    else:
        return {"action": "HOLD",
                "reason": "agents disagree (BUY vs SELL) — nothing moves, no argument",
                "agents": plans, "status": "UNVALIDATED",
                "explain": [f"{p['agent']}({p['profile']}): {p['decision']['action']} "
                            f"{p['decision'].get('channel','')} lane {p['decision'].get('reason','')}"
                            for p in actionable]}

    d = best["decision"]

    # --- HEART GATE: nothing proceeds without a stamp ---
    try:
        import heart
        hv = heart.check(action=d["action"], quote=d.get("size_quote", 0) or 0,
                         venue=_active_venue())
        if hv["decision"] != "ALLOW":
            return {"action": "BLOCKED", "reason": f"heart {hv['decision']}: {hv['reason']}",
                    "level": hv.get("level"), "agents": plans, "status": "UNVALIDATED",
                    "explain": [f"HEART {hv['decision']} — {hv['reason']}"] +
                               [f"{p['agent']}({p['profile']}): {p['decision']['action']}"
                                for p in plans]}
    except Exception as e:
        return {"action": "BLOCKED", "reason": f"heart unavailable ({e}) — fail-closed",
                "agents": plans, "status": "UNVALIDATED"}

    return {
        "action": d["action"], "channel": d.get("channel"), "profile": best["profile"],
        "agent": best["agent"], "entry": d.get("entry"), "stop": d.get("stop"),
        "target": d.get("target"), "size_quote": d.get("size_quote"),
        "risk_quote": d.get("risk_quote"), "rr": d.get("rr"),
        "score": d.get("score"),
        "agreement": len(actionable) > 1 and len(sides) == 1,
        "heart": "ALLOW", "agents": plans, "status": "UNVALIDATED",
        "explain": [f"{p['agent']}({p['profile']}): {p['decision']['action']} "
                    f"{p['decision'].get('channel','')} {p['decision'].get('reason','')}"
                    for p in plans] + [winner_note, "HEART: ALLOW"] +
                   [f"  evidence[{best['agent']}]: {e}" for e in (d.get("evidence") or [])],
    }




def decide_all(verdict=None, pair=None) -> dict:
    """Per-agent, per-interval, no broker arbitration. Each enabled trading
    agent independently reports its decider plan through its own Heart check;
    allowed actions are returned for execution. No comparison, no peers.
    """
    import agents, decider
    verdict = verdict if verdict is not None else _verdict()
    enabled = agents.trading_agents(enabled_only=True)
    if not enabled:
        return {"action": "HOLD", "reason": "no trading agents enabled", "actions": [],
                "agents": [], "explain": ["create/enable a trading agent first"]}
    reports = []
    approved = []
    for a in enabled:
        try:
            import capital
            avail = capital.agent_available(a["id"], a.get("capital", 0))
        except Exception:
            avail = a.get("capital", 0)
        tre = {"available": avail, "currency": "USD"}
        holding = _held_by_agent(a["id"])
        d = decider.decide(verdict=verdict, treasury=tre, cfg=agents.policy_for(a["id"]),
                           holding=holding, pair=pair)
        reports.append({"agent": a["id"], "name": a.get("name"), "profile": a.get("profile"),
                        "decision": d, "spendable": avail, "holding": holding})
        if d.get("action") in ("BUY","SELL"):
            try:
                import heart
                hv = heart.check(action=d["action"], quote=d.get("size_quote",0) or 0,
                                 venue=_active_venue())
                if hv["decision"] == "ALLOW":
                    approved.append({"agent": a["id"], "name": a.get("name"), "profile": a.get("profile"),
                                     "action": d["action"], "channel": d.get("channel"), "entry": d.get("entry"),
                                     "stop": d.get("stop"), "target": d.get("target"), "size_quote": d.get("size_quote"),
                                     "risk_quote": d.get("risk_quote"), "rr": d.get("rr"), "score": d.get("score"),
                                     "expected_net_pct": d.get("expected_net_pct"), "evidence": d.get("evidence"),
                                     "explain": d.get("explain"), "status": "UNVALIDATED", "heart": "ALLOW"})
                else:
                    reports[-1]["blocked_by_heart"] = hv
            except Exception as e:
                reports[-1]["blocked_by_heart"] = str(e)
    if approved:
        return {"action": approved[0]["action"], "actions": approved, "agents": reports,
                "explain": [f"{r['agent']}({r['profile']}): {r['decision'].get('action')} — {r['decision'].get('reason','')}" for r in reports]}
    reasons = []
    for r in reports:
        d = r["decision"]
        reasons.append(f"{r['agent']}({r['profile']}): {d.get('action','HOLD')} — {d.get('reason','')}")
    return {"action": "HOLD", "reason": "no agent has a setup", "actions": [], "agents": reports,
            "explain": reasons}


def _cfg_margin() -> float:
    try:
        import json
        from pathlib import Path
        cfg = json.loads((Path.home() / "blackhorse" / "config" / "broker.json").read_text())
        return float(cfg.get("conflict_margin", 0.5))
    except Exception:
        return 0.5


def _active_venue():
    try:
        import exchanges
        return exchanges.active_exchange()["name"]
    except Exception:
        return None


def _held_by_agent(agent_id: str) -> float:
    """Total base qty the agent currently holds (for spot-only exit decisions)."""
    try:
        import portfolio
        return sum(float(p.get("qty") or 0)
                   for p in portfolio.open_positions() if p.get("agent") == agent_id
                   and str(p.get("side")).upper() == "BUY")
    except Exception:
        return 0.0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()
    v = _verdict()
    d = decide(v)
    if args.explain:
        print(f"HOUSE ACTION: {d['action']}  ({d.get('reason', d.get('profile',''))})")
        for line in d.get("explain", []):
            print("  -", line)
        print("  (UNVALIDATED — hypothesis only)")
    else:
        print(json.dumps(d, indent=2))
