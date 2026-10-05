"""Shared, disk-backed rate-limit budget for brain (opencode) calls.

Every path that might talk to the brain — the periodic sweep, whitecell
escalations, and the GUI's one-shot asks — must pass through allow() first.
State lives in logs/brain_budget.json behind an flock so the counter is
shared and race-free across processes.

Policy thresholds come from skin_settings.json:
    brain_min_seconds_between_calls : hard minimum gap between ANY two calls
    brain_max_calls_per_hour        : rolling 60-minute ceiling

Both are deliberately *minutes* to hours, not per-second: the brain is the
expensive expert tier and must not be spammed.
"""
import json, os, time
from pathlib import Path

try:
    import fcntl  # POSIX only; we degrade gracefully otherwise
except ImportError:
    fcntl = None

STATE = Path.home() / "blackhorse" / "logs" / "brain_budget.json"

DEFAULT_MIN_GAP = 300      # seconds between calls (5 min)
DEFAULT_MAX_PER_HOUR = 10  # rolling ceiling


def _cfg() -> tuple[int, int]:
    try:
        import settings
        s = settings.load()
        return (int(s.get("brain_min_seconds_between_calls", DEFAULT_MIN_GAP)),
                int(s.get("brain_max_calls_per_hour", DEFAULT_MAX_PER_HOUR)))
    except Exception:
        return DEFAULT_MIN_GAP, DEFAULT_MAX_PER_HOUR


def _read() -> dict:
    try:
        d = json.loads(STATE.read_text())
        if isinstance(d, dict):
            return d
    except (json.JSONDecodeError, OSError):
        pass
    return {"calls": []}


def _write(d: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(STATE)


def _prune(calls, now):
    return sorted(t for t in calls if now - t < 3600)


def status() -> dict:
    d = _read()
    now = time.time()
    calls = _prune(d.get("calls", []), now)
    gap, cap = _cfg()
    last = calls[-1] if calls else 0
    wait = max(0, gap - int(now - last)) if last else 0
    return {
        "calls_last_hour": len(calls),
        "max_calls_per_hour": cap,
        "min_seconds_between_calls": gap,
        "seconds_until_next": wait,
        "allowed": wait == 0 and len(calls) < cap,
    }


def allow(record: bool = True) -> tuple[bool, str]:
    """Return (allowed, reason). If allowed and record=True, reserve a slot."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(STATE.with_suffix(".lock"), "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        d = _read()
        now = time.time()
        calls = _prune(d.get("calls", []), now)
        gap, cap = _cfg()
        last = calls[-1] if calls else 0
        if last and now - last < gap:
            return False, f"cooldown — {int(gap - (now - last))}s until next call"
        if len(calls) >= cap:
            return False, f"hourly cap reached ({len(calls)}/{cap})"
        if record:
            calls.append(now)
            d["calls"] = calls
            _write(d)
        return True, "ok"
    finally:
        try:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


def reset():
    """Clear the rolling window (does not touch settings)."""
    _write({"calls": []})
