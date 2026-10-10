"""CPU knobs for the skin — tune or turn DOWN the expensive parts.
Edit this file and the service picks it up on its next cycle (no restart needed)."""
import json, os
from pathlib import Path

SETTINGS_FILE = Path.home() / "blackhorse" / "config" / "skin_settings.json"

DEFAULTS = {
    # ----------------- MASTER SWITCH -----------------
    # The one true ON/OFF. False gates EVERYTHING: engines, whitecells, the
    # local fallback model, escalations, and the brain. Nothing runs, nothing
    # calls out, nothing is killed or blocked. Set by the GUI master button.
    "master_enabled": True,

    # Local-model fallback ONLY. The deterministic rule engine is primary; this
    # qwen/Ollama tier is a cheap second opinion for ambiguous local traces. It
    # is NOT the brain — the brain is opencode (see brain_* keys below).
    "llm_enabled": True,

    # Cap how often the local fallback model may be invoked (0 = fallback OFF).
    # Must be > 0 for the local tier to run at all.
    "max_llm_calls_per_min": 6,

    # Hard seconds cap on any single LLM call
    "llm_timeout_seconds": 25,

    # Which model the skin uses (change to e.g. "llama3.2:3b" or "qwen2.5:7b" on a high-end PC,
    # flip llm_enabled on, and the model is pulled + warmed automatically on first use)
    "llm_model": "qwen2.5:0.5b",

    # keep_alive so the model stays resident between verdicts/chats (seconds; -1 = forever)
    "llm_keep_alive_seconds": 0,

    # ---- BRAIN — REMOVED (moved to the separate Mr.Fluffy app, ~/mrfluffy) ----
    # BlackHorse is the trading horse: it never calls opencode. brain_enabled
    # stays false; the knob is kept only so stale config can't re-arm it.
    "brain_enabled": False,

    # Whitecells never escalate to a brain that no longer lives here.
    "whitecells_may_escalate": False,

    # Hard minimum gap between ANY two brain calls, in seconds (default 5 min).
    # Every call path shares this one budget (see brain_budget.py).
    "brain_min_seconds_between_calls": 300,

    # Rolling 60-minute ceiling on brain calls.
    "brain_max_calls_per_hour": 10,

    # How often the brain wakes to sweep threats and talk to the skin.
    "brain_sweep_seconds": 300,

    # Seconds cap on a single opencode call.
    "brain_timeout_seconds": 180,

    # Engine on/off switches (net/egress/integrity/watchdog)
    "engines": {
        "net_check": True,
        "watchdog": True,
        "egress": True,
        "integrity": True,
        "bluetooth": True,
    },
}

def load() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)

def save(cfg: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg, indent=2))

if not SETTINGS_FILE.exists():
    save(DEFAULTS)
