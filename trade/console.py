"""console.py — stateful chat with the brain. Answers with CONTEXT, not vibes.

"I gave it 100, what's up" -> the brain sees: capital, positions, today's P&L,
last decision, market read, lessons — and replies.

Conversation is append-only (narrative memory) and the brain gets a compact
context packet each turn, so it has continuity without hoarding RAM.

Also supports simple imperatives so chat can DRIVE the house:
  "allocate 100"     -> treasury.allocate
  "reserve 50"       -> treasury.set_reserve
  "status"           -> a built-in snapshot (no brain call)
  anything else      -> ask the brain with full context

CLI:
  python3 trade/console.py "I gave it 100 what's up"
  python3 trade/console.py --status
  python3 trade/console.py --transcript
"""
import json, sys, time, argparse, re
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))
CONVO = BH / "logs" / "conversations.jsonl"


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def context() -> dict:
    """The compact state the brain reasons over each turn."""
    import portfolio
    st = portfolio.status()
    try:
        import treasury
        tre = treasury.status()
    except Exception:
        tre = {}
    try:
        import engine
        snap = engine.snapshot()
    except Exception:
        snap = {}
    try:
        import index
        lessons = [r["text"] for r in index.recent(kind="lesson", limit=5)]
        last_dec = [r["text"] for r in index.recent(kind="decision", limit=3)]
    except Exception:
        lessons, last_dec = [], []
    ctx = {
        "capital": {k: tre.get(k) for k in ("allocated", "cash", "available", "reserve_floor",
                                            "open_risk", "realized_pnl", "currency")},
        "portfolio": {"open": len(st.get("positions", [])), "positions": st.get("positions", []),
                      "realized_pnl": st.get("realized_pnl"), "unrealized_pnl": st.get("unrealized_pnl"),
                      "equity": st.get("equity"), "wins": st.get("wins"), "closed": st.get("closed"),
                      "fees_paid": round(st.get("fees_paid", 0), 5)},
        "market": (snap.get("news", {}) or {}).get("summary"),
        "signal": _signal_brief(snap.get("signal")),
        "last_verdict": snap.get("brain_verdict"),
        "recent_decisions": last_dec,
        "lessons": lessons,
        "governance": snap.get("governance"),
    }
    # --- live-system facts so Spice doesn't quote stale gate state ---
    try:
        import heart
        ctx["heart_unlock"] = heart.unlock()
    except Exception as e:
        ctx["heart_unlock"] = {"error": str(e)}
    try:
        import execute as _ex
        ctx["execute"] = {"live_modes": ["market", "limit", "stop-loss", "take-profit",
                                         "stop-loss-limit", "take-profit-limit",
                                         "trailing-stop", "trailing-stop-limit"],
                           "mode": _ex._mode()}
    except Exception as e:
        ctx["execute"] = {"error": str(e)}
    try:
        loop_state = json.loads((Path.home()/"blackhorse"/"logs"/"loop_state.json").read_text())
        ctx["loop"] = {k: loop_state.get(k) for k in ("ts", "heartbeat", "decision", "reason", "brain")}
    except Exception:
        pass
    return ctx


def _signal_brief(sig: dict) -> dict:
    if not sig or not sig.get("available"):
        return {"available": False}
    out = {"available": True, "pair": sig.get("pair")}
    for name, c in (sig.get("channels") or {}).items():
        out[name] = {"interval": c.get("interval"), "action": c.get("action"),
                     "strength": round(c.get("strength") or 0, 3),
                     "direction": c.get("strengthDirection"),
                     "advisor_buy": (c.get("advisor") or {}).get("buy"),
                     "advisor_sell": (c.get("advisor") or {}).get("sell")}
    return out


def human_status() -> str:
    c = context()
    cap = c["capital"]
    p = c["portfolio"]
    pos = ", ".join(f"{x['side']} {x['pair']} @ {round(x['entry'],5)}" for x in p["positions"]) or "none"
    return (f"Capital: {cap.get('allocated')} {cap.get('currency')} · available {cap.get('available')} "
            f"(reserve {cap.get('reserve_floor')})\n"
            f"P&L: realized {round(p.get('realized_pnl') or 0,4)} · unrealized {round(p.get('unrealized_pnl') or 0,4)} "
            f"· equity {round(p.get('equity') or 0,4)} · {p.get('wins')}/{p.get('closed')} wins\n"
            f"Open: {pos}\n"
            f"Market: {c.get('market')}")


def _append(role: str, text: str):
    CONVO.parent.mkdir(parents=True, exist_ok=True)
    with CONVO.open("a") as f:
        f.write(json.dumps({"ts": time.time(),
                            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "role": role, "text": str(text)[:4000]}) + "\n")


def transcript(limit=20) -> list:
    if not CONVO.exists():
        return []
    out = []
    for line in CONVO.read_text(errors="ignore").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _handle_imperative(text: str):
    """Let chat drive the house for simple commands. Returns reply or None."""
    import re
    t = text.strip().lower()
    m = re.match(r"^(?:i (?:gave|give)(?: it)?|allocate|add|fund)\s+\$?([0-9.]+)", t)
    if m:
        try:
            import treasury
            treasury.allocate(float(m.group(1)), note="console")
            _append("system", f"allocated {m.group(1)}")
            return f"Done — allocated {m.group(1)}. " + human_status()
        except Exception as e:
            return f"Could not allocate: {e}"
    m = re.match(r"^(?:reserve|set reserve)\s+\$?([0-9.]+)", t)
    if m:
        try:
            import treasury
            treasury.set_reserve(float(m.group(1)))
            return f"Reserve set to {m.group(1)}. " + human_status()
        except Exception as e:
            return f"Could not set reserve: {e}"
    return None


def reply(text: str) -> dict:
    _append("user", text)
    # built-ins first (no brain call needed)
    if text.strip().lower() in ("status", "what's up", "whats up", "how are we"):
        body = human_status()
        _append("brain", body)
        return {"reply": body, "source": "status"}
    imp = _handle_imperative(text)
    if imp:
        _append("brain", imp)
        return {"reply": imp, "source": "imperative"}
    # manual order commands -> direct execution path (no brain needed)
    try:
        import execute as _ex
        manual = _ex.manual_order(text)
        if manual:
            _append("brain", manual)
            return {"reply": manual, "source": "manual_order"}
    except Exception as e:
        body = f"manual order error: {e}"
        _append("brain", body)
        return {"reply": body, "source": "error"}

    t = text.strip().lower()
    t = re.sub(r"^(hey\s+)?spice[,:]?\s*", "", t)
    # order intent but missing details -> ask for the missing pieces
    if any(w in t for w in ("place", "submit", "create", "open", "new")) and "order" in t:
        have = []
        if re.search(r"\b(buy|sell)\b", t): have.append("side")
        if re.search(r"(usd|usdt|dot|\$|\d+\s*usd)", t): have.append("amount")
        if re.search(r"\b(market|limit|trailing|stop|take.?profit)\b", t): have.append("order_type")
        if re.search(r"\b(DOT|BTC|ETH|XBT|/)\b", t): have.append("pair")
        missing = [x for x in ("side", "pair", "order_type", "amount_usd", "price_if_limit") if x not in have]
        guide = ("I can place that. Still need: " + (", ".join(missing) if missing else "none") +
                 ". e.g.  create order buy DOT/USD limit 1.20 10   |   create order sell BTC/USD market 50")
        _append("brain", guide)
        return {"reply": guide, "source": "order_guide"}

    # agent creation intent -> ask which profile/capital
    if any(w in t for w in ("create", "set up", "setup", "add", "new")) and "agent" in t:
        missing = []
        if not re.search(r"\b(trading|youtube)\b", t): missing.append("kind (trading | youtube)")
        if not re.search(r"\b(quick|slow)\b", t): missing.append("profile (quick | slow)")
        if not re.search(r"\b\d+(\.\d+)?\s*(usd|\$)", t): missing.append("capital (e.g. 50 usd)")
        guide = ("I can spin up an agent. Still need: " + (", ".join(missing) if missing else "none") +
                 ". e.g.  python3 trade/agents.py create --kind trading --profile slow --capital 100   then   python3 trade/agents.py enable agent_3")
        _append("brain", guide)
        return {"reply": guide, "source": "agent_guide"}

    # brain with full context
    try:
        import brain as brain_mod
        import brain_budget
        if not brain_mod.brain_enabled():
            body = "(brain disabled) " + human_status()
            _append("brain", body)
            return {"reply": body, "source": "disabled"}
        ok, why = brain_budget.allow()
        if not ok:
            body = f"(brain cooling down: {why}) Here's the state:\n" + human_status()
            _append("brain", body)
            return {"reply": body, "source": "budget", "deferred": True}
        prompt = (
            "You are the BlackHorse trading brain, talking with your owner. "
            "Answer conversationally and concretely, using the STATE. If asked what's up, "
            "summarize capital, positions, P&L, market, and your current stance. Be honest.\n\n"
            f"STATE: {json.dumps(context())[:3500]}\n\n"
            f"OWNER: {text}"
        )
        ts = brain_mod.trade_session()
        if not ts:
            return "(trading brain session not provisioned — set logs/trade_session.id)"
        answer = brain_mod.ask(prompt, force=True, session=ts, kind="console")
        _append("brain", answer)
        return {"reply": answer, "source": "brain"}
    except Exception as e:
        body = f"(brain error: {e})\n" + human_status()
        _append("brain", body)
        return {"reply": body, "source": "error"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="*")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--transcript", action="store_true")
    args = ap.parse_args()
    if args.status:
        print(human_status())
    elif args.transcript:
        print(json.dumps(transcript(40), indent=2))
    else:
        msg = " ".join(args.message) or "status"
        print(reply(msg)["reply"])
