"""signal_acq.py — agent-union based SignalBot acquisition with batching + merge.

SignalBot sidecar only accepts up to 3 intervals per run (mapped to ripple/wave/tide).
We union all strategy_intervals from enabled trading agents per pair, batch into groups
of <=3, run sidecar per batch, merge all channels into one snapshot.
"""
import json, os, time, subprocess, shutil
from pathlib import Path

BH = Path.home() / "blackhorse"
SIDECAR = BH / "trade" / "sidecar" / "signal.js"
SIGNALS_DIR = BH / "logs" / "signals"
LATEST = BH / "logs" / "signal_latest.json"
SETUP = BH / "config" / "signal_setup.json"
TRADING_CFG = BH / "config" / "trading.json"
LOG = BH / "logs" / "signal_acq.log"

# cache: (pair, interval) -> last normalized channel with ts
_CACHE = {}  # key -> {"ts": float, "ch": dict}


def _node():
    return shutil.which("node") or "node"


def _resolve_exchange(explicit=None):
    if explicit:
        return explicit
    try:
        import exchanges
        reg = exchanges.list_exchanges()
        active = reg.get("active")
        ready = [e["name"] for e in reg.get("exchanges", [])
                 if e.get("enabled", True) and (e.get("credentials") or {}).get("ready")]
        if active in ready:
            return active
        if ready:
            return ready[0]
        return active or "kraken"
    except Exception:
        return "kraken"


def _load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default if default is not None else {}


def _save_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)


def union_intervals_for_pair(pair: str) -> list:
    ivs = []
    seen = set()
    try:
        import agents
        for a in agents.trading_agents(enabled_only=True):
            pairs = a.get("pairs") or []
            if pairs and pair not in pairs:
                continue
            pol = agents.policy_for(a["id"]) or {}
            for iv in (pol.get("strategy_intervals") or []):
                s = str(iv).strip()
                if not s or s in seen:
                    continue
                # must look like interval
                if len(s) > 1 and s.endswith("m") and s[:-1].isdigit():
                    seen.add(s)
                    ivs.append(s)
    except Exception:
        pass
    # fallback to signal_setup if nothing
    if not ivs:
        try:
            cfg = _load_json(SETUP, {})
            for iv in (cfg.get("channels") or []):
                s = str(iv).strip()
                if len(s) > 1 and s.endswith("m") and s[:-1].isdigit() and s not in seen:
                    seen.add(s)
                    ivs.append(s)
        except Exception:
            pass
    # sort by minutes
    def _m(x):
        try:
            return int(x.rstrip("m"))
        except Exception:
            return 9999
    ivs.sort(key=_m)
    return ivs


def _log(rec: dict):
    """Append one acquisition event so a short refresh can be explained later."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _merge_channels(snap, merged_channels, pair, now):
    """Merge one normalized sidecar run into the growing per-interval map."""
    added = []
    for ch in (snap.get("channels") or {}).values():
        iv = ch.get("interval")
        if iv and iv not in merged_channels:
            merged_channels[iv] = ch
            _CACHE[f"{pair}:{iv}"] = {"ts": now, "ch": ch}
            added.append(iv)
    return added


def _run_batch(pair, exchange, batch, timeout):
    """Run one sidecar batch. Never raises: returns (snapshot|None, info)."""
    import signalbot
    slug = str(pair).replace("/", "-").upper()
    RAW = BH / "logs" / f"signal_raw_{slug}_acq_{int(time.time() * 1000)}.json"
    cmd = [_node(), str(SIDECAR), "--pair", pair, "--strategy", "custom",
           "--exchange", exchange, "--out", str(RAW)]
    cmd += ["--channels", ",".join(batch)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BH))
    except subprocess.TimeoutExpired:
        info = {"ok": False, "error": f"timeout after {timeout}s",
                "ms": int((time.time() - t0) * 1000)}
        _log({"event": "batch", "pair": pair, "batch": batch, **info})
        return None, info
    ms = int((time.time() - t0) * 1000)
    try:
        meta = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except Exception:
        meta = {}
    if not meta.get("ok"):
        err = meta.get("error") or (r.stderr or "no sidecar output")[-300:]
        info = {"ok": False, "error": str(err)[-300:], "ms": ms, "rc": r.returncode}
        _log({"event": "batch", "pair": pair, "batch": batch, **info})
        return None, info
    try:
        raw = json.loads(RAW.read_text())
        snap = signalbot.normalize(raw, meta)
    except Exception as e:
        info = {"ok": False, "error": f"parse/normalize: {e}", "ms": ms}
        _log({"event": "batch", "pair": pair, "batch": batch, **info})
        return None, info
    finally:
        try:
            RAW.unlink()
        except Exception:
            pass
    ivs = sorted({c.get("interval") for c in (snap.get("channels") or {}).values()
                  if c.get("interval")})
    info = {"ok": True, "ms": ms, "channels": ivs}
    _log({"event": "batch", "pair": pair, "batch": batch, **info})
    return snap, info


def acquire(pair="DOT/USD", strategy="swing", exchange=None, timeout=180,
            max_age=300, force=False, merge_daytrade=True) -> dict:
    exchange = _resolve_exchange(exchange)
    import sys
    if str(BH / "trade") not in sys.path:
        sys.path.insert(0, str(BH / "trade"))
    if str(BH / "memory") not in sys.path:
        sys.path.insert(0, str(BH / "memory"))
    import signals
    import signalbot

    union = union_intervals_for_pair(pair)
    if not union:
        union = ["15m", "60m", "240m"]

    prev = {}
    try:
        prev = signals.load(pair) or {}
    except Exception:
        prev = {}
    prev_by_iv = {}
    for ch in (prev.get("channels") or {}).values():
        iv = ch.get("interval")
        if iv and iv not in prev_by_iv:
            prev_by_iv[iv] = ch

    now = time.time()
    merged_channels = {}

    # check cache per (pair, iv)
    to_fetch = []
    for iv in union:
        key = f"{pair}:{iv}"
        cached = _CACHE.get(key)
        if not force and cached and (now - cached["ts"]) < max_age:
            merged_channels[iv] = cached["ch"]
        else:
            to_fetch.append(iv)

    # batch to_fetch in groups of 3
    batches = [to_fetch[i:i+3] for i in range(0, len(to_fetch), 3)]
    failed = []
    started = time.time()
    budget = max(timeout * 2, 240)
    for batch in batches:
        if not batch:
            continue
        snap, info = _run_batch(pair, exchange, batch, timeout)
        if snap:
            _merge_channels(snap, merged_channels, pair, now)
            continue
        failed.append({"batch": list(batch), **info})
        # Recover lane by lane: the market-structure cache is warm by now, so a
        # single-interval retry is fast even when the 3-slot batch stalled.
        if time.time() - started > budget:
            _log({"event": "retry_budget_exhausted", "pair": pair, "batch": batch})
            continue
        for iv in batch:
            if iv in merged_channels:
                continue
            snap_i, info_i = _run_batch(pair, exchange, [iv], min(timeout, 120))
            if snap_i:
                _merge_channels(snap_i, merged_channels, pair, now)
            else:
                failed.append({"batch": [iv], **info_i})

    fb = {}
    if not merged_channels:
        # old single-run fallback; may still succeed where the batches did not
        fb = signalbot.run(pair=pair, strategy=strategy, exchange=exchange,
                           channels=union[:3] if len(union)>=3 else union,
                           merge_daytrade=merge_daytrade)
        if fb.get("available"):
            _merge_channels(fb, merged_channels, pair, now)

    # A transient failed batch must never make a rung disappear: re-use the last
    # good lane for that interval, clearly marked as carried so it is auditable.
    carried = []
    for iv in union:
        if iv in merged_channels:
            continue
        pc = prev_by_iv.get(iv)
        if not pc:
            continue
        c = dict(pc)
        c["_carried"] = True
        c["_carried_ts"] = prev.get("ts")
        merged_channels[iv] = c
        carried.append(iv)
    if carried:
        _log({"event": "carried_forward", "pair": pair, "intervals": carried,
              "from": prev.get("ts")})

    if not merged_channels:
        return fb or {"available": False, "error": "no signal lanes acquired"}

    # build merged snapshot similar to signalbot.normalize structure
    # rank by interval
    def _iv_m(iv):
        try:
            s = str(iv or "")
            return int(s.rstrip("m")) if s.endswith("m") else 9999
        except Exception:
            return 9999

    # deduplicate by interval, keep freshest
    by_iv = {}
    for c in merged_channels.values():
        iv = c.get("interval")
        if iv:
            by_iv[iv] = c
    ranked = sorted(by_iv.values(), key=lambda x: _iv_m(x.get("interval")))
    # assign roles
    bands = ("lower", "mid", "higher")
    for i, c in enumerate(ranked):
        if i < len(bands):
            c["role"] = bands[i]
        else:
            c["role"] = f"rank{i}"
    # rekey - keep all intervals, use role-based keys but also allow lookup by interval
    out_ch = {}
    for i, c in enumerate(ranked):
        if i == 0:
            out_ch["lower"] = c
        elif i == 1:
            out_ch["mid"] = c
        elif i == 2:
            out_ch["higher"] = c
        else:
            out_ch[f"r{i}"] = c
    # also add by interval key for compatibility
    for c in ranked:
        iv = c.get("interval")
        if iv:
            out_ch[iv] = c

    snap = {
        "available": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pair": pair,
        "exchange": exchange,
        "strategy": "custom",
        "channels": out_ch,
        "grids": {},
        "structure": {"aggregate": {}, "executionIntervals": None},
        "_acq": {
            "union": union,
            "batches": len(batches),
            "cached": len(union) - len(to_fetch),
            "failed": failed,
            "carried": carried,
            "missing": [iv for iv in union if iv not in by_iv],
        },
    }

    # try to attach grids/structure from latest if available
    try:
        import signals as _sigmod
        old = _sigmod.load(pair)
        if old:
            snap["grids"] = old.get("grids") or {}
            snap["structure"] = old.get("structure") or snap["structure"]
            snap["marketResult"] = old.get("marketResult")
    except Exception:
        pass

    signals.save(pair, snap)
    try:
        setup = _load_json(SETUP, {})
        if setup.get("pair") == pair:
            _save_json(LATEST, snap)
    except Exception:
        pass
    return snap
