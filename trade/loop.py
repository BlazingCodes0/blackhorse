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


def scan_pairs() -> list:
    """Every pair the enabled agents trade (primary pair first). Agents with no pair
    list trade the primary pair."""
    primary = _setup().get("pair") or "DOT/USD"
    pairs = [primary]
    try:
        import agents
        for a in agents.trading_agents(enabled_only=True):
            for p in (a.get("pairs") or []):
                if p not in pairs:
                    pairs.append(p)
    except Exception:
        pass
    return pairs


def _pair_step(pair, primary, refresh_signal, ask_brain, cfg, setup) -> dict:
    """Refresh + observe + think + decide + act for ONE pair, on that pair's own signal."""
    import signals
    prec = {}
    channels = setup.get("channels") or None
    if refresh_signal and signals.age(pair) > cfg.get("signal_max_age", 600):
        try:
            import signalbot
            sig = signalbot.run(pair=pair, channels=channels, merge_daytrade=True,
                                latest=(pair == primary))
            prec["signal_refresh"] = "ok" if sig.get("available") else sig.get("error")
        except Exception as e:
            prec["signal_refresh"] = f"error: {e}"
    try:
        import observer
        prec["observation"] = observer.note(pair, use_latest=True).get("id")
    except Exception as e:
        prec["observation"] = f"error: {e}"

    sig = signals.load(pair)
    price = next((c.get("currPrice") for c in (sig.get("channels") or {}).values() if c.get("currPrice")), None)
    try:
        if price:
            import execute
            prec["marks"] = execute.mark(pair, float(price)).get("closed", [])
    except Exception as e:
        prec["marks"] = f"error: {e}"

    # think (budgeted; PRIMARY pair only — the brain verdict is global context)
    verdict = None
    if ask_brain and pair == primary:
        try:
            last = _load(BH / "logs" / "loop_brain_meta.json", {})
            last_ts = last.get("ts", 0) or 0
            last_price = last.get("price")
            move_pct = (abs(price - last_price) / last_price * 100) if (price and last_price) else 999
            due = (time.time() - last_ts) > cfg.get("brain_stale_seconds", 3600) or move_pct >= cfg.get("brain_price_trigger_pct", 0.5)
            if due:
                import coach
                v = coach.ask_brain(coach.build_packet())
                verdict = None if v.get("deferred") else v
                prec["brain"] = "deferred" if v.get("deferred") else v.get("stance")
                if not v.get("deferred"):
                    _save(BH / "logs" / "loop_brain_meta.json", {"ts": time.time(), "price": price})
            else:
                prec["brain"] = f"skipped (price moved {round(move_pct,3)}% in {int(time.time()-last_ts)}s)"
        except Exception as e:
            prec["brain"] = f"error: {e}"

    # decide -> gate -> act
    try:
        import broker, execute
        plan = broker.decide_all(verdict, pair=pair)
        actions = plan.get("actions") or []
        prec["decision"] = plan.get("action")
        prec["reason"] = plan.get("reason")
        try:
            import store
            for r in plan.get("agents") or []:
                d = r.get("decision") or {}
                store.record_decision_changed("loop", d.get("action", "HOLD"), pair=pair, agent=r.get("agent"),
                                      channel=d.get("channel"), entry=d.get("entry"), stop=d.get("stop"),
                                      target=d.get("target"), size_quote=d.get("size_quote"), rr=d.get("rr"),
                                      edge=d.get("edge"), reason=d.get("reason") or r.get("reason"),
                                      detail={"explain": d.get("explain"), "skipped": r.get("skipped"),
                                              "order_type": d.get("order_type"),
                                              "outlay_quote": d.get("outlay_quote")})
        except Exception:
            pass
        if actions:
            execed = []
            for action_plan in actions:
                res = execute.execute(action_plan, pair=pair)
                execed.append({"pair": pair, "ok": res.get("ok"), "mode": res.get("mode"),
                               "id": (res.get("position") or {}).get("id"), "blocked": res.get("reason"),
                               "agent": action_plan.get("agent"), "action": action_plan.get("action"),
                               "pending": res.get("pending"), "txid": res.get("txid"),
                               "price": res.get("price")})
            prec["executed"] = execed
        else:
            prec["executed"] = None
    except Exception as e:
        prec["decision"] = f"error: {e}"
    return prec


def sweep(refresh_signal=True, ask_brain=True, pair=None, cfg=None, pairs=None) -> dict:
    """One full pass. Global housekeeping runs ONCE (heartbeat, pending orders, ledger
    checks, exits); then every scanned pair gets its own signal + decision.
    pair=X restricts the sweep to that single pair."""
    cfg = cfg or {}
    setup = _setup()
    primary = setup.get("pair") or "DOT/USD"
    pairs = [pair] if pair else (pairs or scan_pairs())
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pairs": pairs}

    # 1. heartbeat — must be first, or Heart parks everything
    try:
        import heart
        heart.heartbeat()
        rec["heartbeat"] = "ok"
    except Exception as e:
        return {"ts": rec["ts"], "halted": f"heartbeat failed: {e}"}

    # settle resting limit entries (fill -> position at the real fill; expired -> drop)
    try:
        import execute as _ex2
        ev = _ex2.sync_pending()
        if ev:
            rec["pending_sync"] = ev
    except Exception:
        pass

    # exchange is the truth: flag any ledger/exchange drift every sweep
    try:
        import portfolio as _pf
        rl = _pf.reconcile_live()
        if rl.get("ok") is False:
            rec["ledger_mismatch"] = rl["mismatches"]
    except Exception:
        pass
    try:                                   # once a day: trim old HOLD noise, checkpoint the WAL
        import store
        pf = BH / "logs" / "store_prune.json"
        if time.time() - _load(pf, {}).get("ts", 0) > 86400:
            _save(pf, {"ts": time.time(), **store.prune()})
    except Exception:
        pass
    try:
        import store
        v = store.verify_against_portfolio()
        if v.get("ok") is False:
            rec["db_mismatch"] = v
    except Exception:
        pass

    # exits: profit-checked, no stops. Sells only at a net profit after real fees.
    try:
        import exits
        ex = exits.manage()
        if ex:
            rec["exits"] = ex
    except Exception as e:
        rec["exits"] = f"error: {e}"

    # per-pair: signal -> decide -> act
    per, executed = {}, []
    for pr in pairs:
        per[pr] = _pair_step(pr, primary, refresh_signal, ask_brain, cfg, setup)
        executed += per[pr].get("executed") or []
    rec["per_pair"] = per
    first = per.get(primary) or next(iter(per.values()), {})
    acted = next((v for v in per.values() if v.get("decision") in ("BUY", "SELL")), None)
    rec["decision"] = (acted or first).get("decision")
    rec["reason"] = (acted or first).get("reason")
    rec["executed"] = executed or None
    for k in ("signal_refresh", "observation", "marks", "brain"):
        if k in first:
            rec[k] = first[k]

    _save(STATE, rec)
    try:
        import index
        index.remember(kind="decision", text=f"loop sweep {len(pairs)} pair(s) -> {rec.get('decision')}",
                       meta=rec, source="loop")
    except Exception:
        pass
    return rec


def cycle(refresh_signal=True, ask_brain=True, pair=None, cfg=None) -> dict:
    """Single-pair pass (CLI --once, night shift). Default pair = the primary pair."""
    return sweep(refresh_signal, ask_brain, pair=pair or _setup().get("pair") or "DOT/USD", cfg=cfg)


def daemon(interval=300, **kw):
    print(f"BlackHorse loop awake — every {interval}s")
    while True:
        try:
            rec = sweep(**kw)
            print(json.dumps(rec))
        except Exception as e:
            print("loop error:", e)
        # sleep in short steps: while any LIVE position is open, watch bid/ask for the exit
        end = time.time() + max(30, interval)
        while time.time() < end:
            try:
                import portfolio, exits
                if any(p.get("mode") == "LIVE" and not p.get("exit_txid") for p in portfolio.open_positions()):
                    for ev in exits.manage():
                        print(json.dumps({"exit": ev}))
                    time.sleep(max(5, int(exits._cfg().get("exit_poll_seconds", 15))))
                else:
                    time.sleep(min(30, max(1, end - time.time())))
            except Exception as e:
                print("exit watch error:", e)
                time.sleep(15)


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
