"""coach.py — the brain's market read, spoken as strict JSON.

This is where opencode becomes a *decider* rather than a signal follower.
It is called on a cadence (minutes), NOT per tick: the brain is the expensive
strategist tier. It receives a compact packet — SmartBot's signal summary (once
the sidecar lands) plus our market/theme context — and returns a single,
machine-readable verdict the deterministic trader can act on.

Contract (the brain MUST return exactly these keys):
{
  "bias":       "BULLISH" | "BEARISH" | "NEUTRAL",
  "regime":     "TREND_UP" | "TREND_DOWN" | "RANGE" | "COMPRESSION" |
                "EXPANSION" | "UNCERTAIN",
  "confidence": 0.0 - 1.0,
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
  "stance":     "TRADE" | "REDUCE" | "STAND_DOWN",
  "notes":      "one short paragraph explaining the read",
  "watch":      ["things that would change this view"]
}

Safety: the brain may only *advise*. It can veto (STAND_DOWN / EXTREME) which
forces caution, but it cannot place trades, bypass Heart, or invent prices.

CLI:
  python3 trade/coach.py                 # assemble packet from news + signal, ask brain
  python3 trade/coach.py --packet p.json # use a saved packet
  python3 trade/coach.py --dry-run       # print the prompt, don't call opencode
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "skin"))
NEWS = BH / "logs" / "news_cache.json"
SIGNAL = BH / "logs" / "signal_latest.json"   # produced later by the sidecar
BRAIN_LOG = BH / "logs" / "brain_market.jsonl"

import news as news_mod

VALID = {
    "bias": {"BULLISH", "BEARISH", "NEUTRAL"},
    "regime": {"TREND_UP", "TREND_DOWN", "RANGE", "COMPRESSION", "EXPANSION", "UNCERTAIN"},
    "risk_level": {"LOW", "MEDIUM", "HIGH", "EXTREME"},
    "stance": {"TRADE", "REDUCE", "STAND_DOWN"},
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def signal_summary() -> dict:
    """Compact view of the latest SignalBot output, if present."""
    s = _load_json(SIGNAL, None)
    if not s:
        return {"available": False}
    return s


def build_packet() -> dict:
    ctx = news_mod.get()
    packet = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "news": {
            "summary": news_mod.summary(ctx),
            "fear_greed": ctx.get("fear_greed"),
            "global": ctx.get("global"),
            "prices": ctx.get("prices"),
            "headlines": [h.get("title") for h in ctx.get("headlines", [])][:15],
        },
        "signal": signal_summary(),
    }
    # capital context ("I gave it 100")
    packet["treasury"] = _treasury_context()
    # remembered lessons (permanent memory) relevant to this decision
    packet["lessons"] = _recall_lessons()
    # the measured edge — so the brain reasons evidence-first, net of fees
    packet["edge"] = _edge_table()
    # crowd/derivatives sentiment — READ ONLY market info, never a trading venue
    packet["crowd"] = _crowd()
    return packet


def _crowd() -> dict:
    try:
        import crowd
        return crowd.get()
    except Exception as e:
        return {"error": str(e)}


def _edge_table() -> dict:
    try:
        import edge
        return {"proven": [{"interval": c["interval"], "side": c["side"], "win_pct": c.get("win_pct"),
                            "avg_net_pct": c.get("avg_net_pct"), "n": c.get("n")}
                           for c in edge.proven_cells()],
                "table": edge.table()}
    except Exception as e:
        return {"error": str(e)}


def _treasury_context() -> dict:
    try:
        import treasury
        s = treasury.status()
        return {"allocated": s.get("allocated"), "cash": s.get("cash"),
                "available": s.get("available"), "reserve_floor": s.get("reserve_floor"),
                "open_risk": s.get("open_risk"), "realized_pnl": s.get("realized_pnl"),
                "currency": s.get("currency")}
    except Exception as e:
        return {"error": str(e)}


def _recall_lessons(limit=5) -> list:
    try:
        import index
        return [{"text": r["text"], "meta": r.get("meta")} for r in index.recent(kind="lesson", limit=limit)]
    except Exception:
        return []


def render_prompt(packet: dict) -> str:
    news = packet.get("news", {})
    sig = packet.get("signal", {})
    tre = packet.get("treasury", {})
    lessons = packet.get("lessons", [])
    edge = packet.get("edge", {})
    crowd = packet.get("crowd", {})
    lines = [
        "You are the BlackHorse trading brain. Decide the market stance for the next cycle.",
        "You may only ADVISE. You cannot place trades. Be honest about uncertainty.",
        "TRADING IS SPOT-ONLY: no margin, leverage, futures, perps, borrowing, or shorting. "
        "But you MAY read derivatives data (open interest, funding, long/short) as MARKET INFO "
        "to judge how the crowd is positioned and how the market may move — read it, never trade it.",
        "Reason EVIDENCE-FIRST: quote the MEASURED, post-fee edge for each interval/side. "
        "If an interval/side is losing or unproven, say so and do not recommend it. A conclusion "
        "must be quantified and net of fees — like this: '1440m SELL is the only proven edge "
        "(67% win, +1.79% net, n=76); everything under 240m is fee-eaten.'",
        "",
        "== MEASURED EDGE (post-fee) ==",
        f"proven cells: {json.dumps(edge.get('proven'))}",
        f"full table: {json.dumps(edge.get('table'))[:1500]}",
        "",
        "== CROWD / DERIVATIVES SENTIMENT (read-only; spot-only to trade) ==",
        f"{json.dumps(crowd)[:800]}",
        "",
        "== CAPITAL (what the user gave you) ==",
        f"Treasury: {json.dumps(tre)}",
        "(available = spendable above the reserve floor; never plan beyond it.)",
        "",
        "== WHAT YOU REMEMBER (lessons) ==",
    ]
    for l in lessons:
        lines.append(f"  - {l.get('text')}")
    if not lessons:
        lines.append("  (no lessons yet)")
    lines += [
        "",
        "== MARKET / MACRO CONTEXT ==",
        f"Summary: {news.get('summary')}",
        f"Fear&Greed: {json.dumps(news.get('fear_greed'))}",
        f"Global: {json.dumps(news.get('global'))}",
        f"Prices: {json.dumps(news.get('prices'))}",
        "Headlines:",
    ]
    for h in news.get("headlines", []):
        lines.append(f"  - {h}")
    lines.append("")
    lines.append("== SMARTBOT SIGNAL SUMMARY ==")
    if sig.get("available") is False:
        lines.append("(no signal snapshot available yet — reason from market context only)")
    else:
        lines.append(json.dumps(sig)[:3500])
    lines += [
        "",
        "== OUTPUT ==",
        "Reply with ONLY a JSON object, no prose, no markdown, with exactly these keys:",
        '{"bias":"BULLISH|BEARISH|NEUTRAL","regime":"TREND_UP|TREND_DOWN|RANGE|COMPRESSION|EXPANSION|UNCERTAIN",'
        '"confidence":0.0,"risk_level":"LOW|MEDIUM|HIGH|EXTREME","stance":"TRADE|REDUCE|STAND_DOWN",'
        '"notes":"one short paragraph","watch":["..."]}',
    ]
    return "\n".join(lines)


def parse_verdict(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        return {"raw": text, "parse_error": "no JSON object found"}
    try:
        v = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        return {"raw": text, "parse_error": str(e)}
    for k, allowed in VALID.items():
        if isinstance(v.get(k), str):
            v[k] = v[k].upper()
            if v[k] not in allowed:
                v[k + "_invalid"] = v[k]
    try:
        v["confidence"] = max(0.0, min(1.0, float(v.get("confidence", 0))))
    except (TypeError, ValueError):
        v["confidence"] = 0.0
    return v


def ask_brain(packet: dict) -> dict:
    """Ask opencode for a verdict, subject to the shared budget."""
    import brain_budget  # from skin/
    import brain as brain_mod

    if not brain_mod.brain_enabled():
        return {"stance": "STAND_DOWN", "risk_level": "EXTREME",
                "notes": "brain disabled", "bias": "NEUTRAL", "regime": "UNCERTAIN",
                "confidence": 0.0, "watch": []}
    ok, why = brain_budget.allow()
    if not ok:
        return {"deferred": True, "reason": why}
    prompt = render_prompt(packet)
    ts = brain_mod.trade_session()
    if not ts:
        return {"deferred": True, "reason": "trade session not provisioned (logs/trade_session.id)"}
    answer = brain_mod.ask(prompt, force=True, session=ts, kind="trade", route=False)
    verdict = parse_verdict(answer)
    verdict["_asked_at"] = time.time()
    with BRAIN_LOG.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "packet": packet, "verdict": verdict}) + "\n")
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    packet = _load_json(Path(args.packet), None) if args.packet else build_packet()
    if args.dry_run:
        print(render_prompt(packet))
        return
    print(json.dumps(ask_brain(packet), indent=2))


if __name__ == "__main__":
    main()
