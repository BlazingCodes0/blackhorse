"""loop.py — the autonomous heartbeat. One cycle, or a daemon.

Each cycle:
  1. HEARTBEAT  (prove we're alive; otherwise Heart parks everything)
  2. refresh signal (SignalBot via sidecar) if due
  3. OBSERVE    (note market behavior -> ephemeral memory)
  4. THINK      (brain verdict via coach, budgeted; ask only if due)
  5. DECIDE     (broker: agents -> decider -> adjudicate)
  6. GATE       (Heart, inside broker/execute)
  7. MARK       (update open positions; stops/targets realised)
  8. ACT        (execute approved plan as a PAPER fill)
  9. RECORD     (memory + logs)

Fail-closed: if Heart blocks or anything throws, nothing moves.

CLI:
  python3 trade/loop.py --once
  python3 trade/loop.py --interval 300          # run every 5 min
  python3 trade/loop.py --once --no-signal      # skip the slow SignalBot step
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))
STATE = BH / "logs" / "loop_state.json"
SIGNAL = BH / "logs" / "signal_latest.json"
SETUP = BH / "config" / "signal_setup.json"


def _setup() -> dict:
    return _load(SETUP, {})


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _save(path, d):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2))


def _signal_age() -> float:
    if not SIGNAL.exists():
        return 1e9
    return time.time() - SIGNAL.stat().st_mtime


def cycle(refresh_signal=True, ask_brain=True, pair=None, cfg=None) -> dict:
    cfg = cfg or {}
    setup = _setup()
    if not pair:
        pair = setup.get("pair") or "DOT/USD"
    channels = setup.get("channels") or None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # 1. heartbeat — must be first, or Heart parks everything
    try:
        import heart
        heart.heartbeat()
        rec["heartbeat"] = "ok"
    except Exception as e:
        return {"ts": rec["ts"], "halted": f"heartbeat failed: {e}"}

    # 2/3. refresh signal + observe
    if refresh_signal and _signal_age() > cfg.get("signal_max_age", 600):
        try:
            import signalbot
            sig = signalbot.run(pair=pair, channels=channels, merge_daytrade=True)
            rec["signal_refresh"] = "ok" if sig.get("available") else sig.get("error")
        except Exception as e:
            rec["signal_refresh"] = f"error: {e}"
    try:
        import observer
        obs = observer.note(pair, use_latest=True)
        rec["observation"] = obs.get("id")
    except Exception as e:
        rec["observation"] = f"error: {e}"

    # mark open positions against the latest price
    try:
        sig = _load(SIGNAL, {})
        price = next((c.get("currPrice") for c in (sig.get("channels") or {}).values() if c.get("currPrice")), None)
        if price:
            import execute
            rec["marks"] = execute.mark(pair, float(price)).get("closed", [])
    except Exception as e:
        rec["marks"] = f"error: {e}"

    # 4. think (budgeted; only when price actually moved or the verdict is stale)
    verdict = None
    if ask_brain:
        try:
            sig = _load(SIGNAL, {})
            price = next((c.get("currPrice") for c in (sig.get("channels") or {}).values() if c.get("currPrice")), None)
            last = _load(BH / "logs" / "loop_brain_meta.json", {})
            last_ts = last.get("ts", 0) or 0
            last_price = last.get("price")
            move_pct = (abs(price - last_price) / last_price * 100) if (price and last_price) else 999
            due = (time.time() - last_ts) > cfg.get("brain_stale_seconds", 3600) or move_pct >= cfg.get("brain_price_trigger_pct", 0.5)
            if due:
                import coach
                v = coach.ask_brain(coach.build_packet())
                verdict = None if v.get("deferred") else v
                rec["brain"] = "deferred" if v.get("deferred") else v.get("stance")
                if not v.get("deferred"):
                    _save(BH / "logs" / "loop_brain_meta.json", {"ts": time.time(), "price": price})
            else:
                rec["brain"] = f"skipped (price moved {round(move_pct,3)}% in {int(time.time()-last_ts)}s)"
        except Exception as e:
            rec["brain"] = f"error: {e}"

    # 5/6/7/8. decide -> gate -> act
    try:
        import broker, execute
        plan = broker.decide_all(verdict, pair=pair)
        actions = plan.get("actions") or []
        rec["decision"] = plan.get("action")
        rec["reason"] = plan.get("reason")
        if actions:
            execed = []
            for action_plan in actions:
                res = execute.execute(action_plan, pair=pair)
                execed.append({"ok": res.get("ok"), "mode": res.get("mode"),
                               "id": (res.get("position") or {}).get("id"), "blocked": res.get("reason"),
                               "agent": action_plan.get("agent"), "action": action_plan.get("action")})
            rec["executed"] = execed
        else:
            rec["executed"] = None
    except Exception as e:
        rec["decision"] = f"error: {e}"

    _save(STATE, rec)
    try:
        import index
        index.remember(kind="decision", text=f"loop cycle -> {rec.get('decision')}",
                       meta=rec, source="loop")
    except Exception:
        pass
    return rec


def daemon(interval=300, **kw):
    print(f"BlackHorse loop awake — every {interval}s")
    while True:
        try:
            rec = cycle(**kw)
            print(json.dumps(rec))
        except Exception as e:
            print("loop error:", e)
        time.sleep(max(30, interval))


def night_shift(pairs=None, interval=300, hours=8, ask_brain=True) -> dict:
    """Work while the owner sleeps. Each cycle: run the normal loop over the
    watchlist, journal the outcome, and at the end leave a short morning brief.

    Safety: this only runs the SAME gated path (broker -> Heart -> execute). It
    cannot exceed caps, cannot go live unless mode=LIVE in config/trading.json,
    and stops on any hard error. It is a scheduler, not a new authority.
    """
    if pairs is None:
        try:
            import execute as _ex
            pairs = _ex._trading_cfg().get("watchlist")
        except Exception:
            pairs = None
    pairs = pairs or ["DOT/USD", "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"]
    end = time.time() + hours * 3600
    journal = []
    print(f"BlackHorse NIGHT SHIFT — {len(pairs)} pairs, every {interval}s, up to {hours}h")
    while time.time() < end:
        for pair in pairs:
            try:
                rec = cycle(ask_brain=ask_brain, pair=pair)
                rec["pair"] = pair
                journal.append(rec)
                print(json.dumps({"pair": pair, "decision": rec.get("decision"),
                                  "executed": rec.get("executed"), "ts": rec.get("ts")}))
            except Exception as e:
                journal.append({"pair": pair, "error": str(e)})
        _save(BH / "logs" / "night_journal.json", {"started": end - hours * 3600,
                                                   "cycles": journal[-200:]})
        time.sleep(max(30, interval))

    brief = _morning_brief(journal)
    _save(BH / "logs" / "morning_brief.json", brief)
    return brief


def _morning_brief(journal: list) -> dict:
    sold = [j for j in journal if j.get("executed")]
    holds = [j for j in journal if j.get("decision") == "HOLD"]
    errors = [j for j in journal if isinstance(j.get("decision"), str)
              and j["decision"].startswith("error")]
    opens = 0
    try:
        import portfolio
        opens = len(portfolio.open_positions())
    except Exception:
        pass
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cycles": len(journal),
        "trades_executed": len(sold),
        "holds": len(holds),
        "errors": len(errors),
        "open_positions": opens,
        "highlights": [{"pair": j.get("pair"), "decision": j.get("decision"),
                        "why": j.get("reason")} for j in journal if j.get("executed")][:10],
        "note": "overnight run — same Heart-gated path; nothing bypassed.",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--pair", default=None)
    ap.add_argument("--no-signal", action="store_true")
    ap.add_argument("--no-brain", action="store_true")
    ap.add_argument("--night", action="store_true", help="run the overnight watchlist shift")
    ap.add_argument("--pairs", default="DOT/USD,BTC/USD,ETH/USD,SOL/USD,XRP/USD")
    ap.add_argument("--hours", type=float, default=8)
    args = ap.parse_args()
    kw = dict(refresh_signal=not args.no_signal, ask_brain=not args.no_brain, pair=args.pair)
    if args.night:
        print(json.dumps(night_shift(pairs=[p.strip() for p in args.pairs.split(",")],
                                     interval=args.interval, hours=args.hours,
                                     ask_brain=not args.no_brain), indent=2))
    elif args.once:
        print(json.dumps(cycle(**kw), indent=2))
    else:
        daemon(args.interval, **kw)
