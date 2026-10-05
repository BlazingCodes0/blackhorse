#!/usr/bin/env python3
"""Run trade/edge_rules.py, persist the latest study to logs + memory."""
import json, subprocess, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
OUT = BH / "logs" / "edge_rules_latest.txt"

def _run():
    p = subprocess.run([sys.executable, str(BH / "trade" / "edge_rules.py"), "5"],
                       cwd=str(BH), capture_output=True, text=True, timeout=180)
    txt = p.stdout.strip() or p.stderr.strip()
    OUT.write_text(txt)
    return txt

if __name__ == "__main__":
    txt = _run()
    lines = [l for l in txt.splitlines() if l.strip()]
    snippet = "\n".join(lines[:120] + ["..."] + lines[-40:])
    meta = {"source": "edge_rules", "horizon": 5, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    sys.path.insert(0, str(BH / "memory"))
    import index
    index.init()
    mid = index.remember(kind="digest", text=f"edge_rules study (horizon5)\n{snippet}",
                         meta=meta, source="edge_rules_update")
    print(json.dumps({"stored": mid, "file": str(OUT), "lines": len(lines), "bytes": len(txt)}))
