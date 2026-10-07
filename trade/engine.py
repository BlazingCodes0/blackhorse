"""BlackHorse trade engine — read-only snapshot + orchestration surface.

This is the single place the GUI (and later the autonomous loop) calls to learn
the current state. It performs NO orders and NO live-account mutation. It
aggregates, from config + logs + the encrypted vault:

  * active exchange + full connection descriptor (credential readiness)
  * market news/macro context
  * latest SignalBot signal (if the sidecar has written one)
  * latest brain (opencode) market verdict
  * governance flags: autonomous on/off, master switch, trading mode

CLI:
  python3 trade/engine.py snapshot     # JSON snapshot
  python3 trade/engine.py exchanges    # list exchanges
  python3 trade/engine.py news         # news context
  python3 trade/engine.py ask          # ask the brain now (budgeted)
"""
import json, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "skin"))

SIGNAL = BH / "logs" / "signal_latest.json"
BRAIN_LOG = BH / "logs" / "brain_market.jsonl"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _tail_jsonl(path: Path, n=1):
    try:
        lines = path.read_text(errors="ignore").splitlines()[-n:]
        return [json.loads(l) for l in lines if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def governance() -> dict:
    try:
        import settings
        s = settings.load()
    except Exception:
        s = {}
    try:
        import heart
        can_arm = heart.unlock().get("can_arm", False)
    except Exception:
        can_arm = False
    return {
        "master_enabled": bool(s.get("master_enabled", True)),
        "brain_enabled": bool(s.get("brain_enabled", True)),
        "autonomous": can_arm,
        "trading_mode": "PAPER",
    }


def snapshot() -> dict:
    import exchanges
    active = None
    try:
        active = exchanges.active_exchange()["name"]
    except Exception:
        pass
    desc = exchanges.connection_descriptor(active) if active else None
    sig = _load_json(SIGNAL, None)
    brain = _tail_jsonl(BRAIN_LOG, 1)
    try:
        import news
        nctx = news.get()
        news_summary = news.summary(nctx)
    except Exception as e:
        nctx, news_summary = {}, f"(news unavailable: {e})"
    try:
        import treasury
        treasury_status = treasury.status()
    except Exception as e:
        treasury_status = {"error": str(e)}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "governance": governance(),
        "active_exchange": active,
        "exchange": desc,
        "treasury": treasury_status,
        "news": {"summary": news_summary,
                 "fear_greed": nctx.get("fear_greed"),
                 "global": nctx.get("global"),
                 "headlines": [h.get("title") for h in nctx.get("headlines", [])][:10]},
        "signal": sig if sig else {"available": False},
        "brain_verdict": next((b.get("verdict") for b in reversed(brain)
                               if isinstance(b.get("verdict"), dict)
                               and b["verdict"].get("bias")
                               and not b["verdict"].get("parse_error")), None),
        "brain_last_error": brain[-1].get("verdict", {}).get("notes")
                            if brain and not (isinstance(brain[-1].get("verdict"), dict) and brain[-1]["verdict"].get("bias"))
                            else None,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    if cmd == "exchanges":
        import exchanges
        print(json.dumps(exchanges.list_exchanges(), indent=2))
    elif cmd == "news":
        import news
        print(json.dumps(news.get(), indent=2))
    elif cmd == "ask":
        import coach
        print(json.dumps(coach.ask_brain(coach.build_packet()), indent=2))
    else:
        print(json.dumps(snapshot(), indent=2))
