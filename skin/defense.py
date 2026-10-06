"""Main defense loop: runs net checks + watchdog on an interval.
Usage:  python3 defense.py            # single sweep
        python3 defense.py --loop 60  # every 60s
"""
import sys, time
from net_check import run_all
from watchdog import sweep
from egress import scan as egress_scan
from integrity import scan as integrity_scan
from bluetooth import scan as bluetooth_scan
import settings

ENGINES = {"net_check": run_all, "watchdog": sweep, "egress": egress_scan,
           "integrity": integrity_scan, "bluetooth": bluetooth_scan}

def run_enabled():
    if not settings.load().get("master_enabled", True):
        return  # master OFF — run nothing
    cfg = settings.load()["engines"]
    for name, fn in ENGINES.items():
        if cfg.get(name, True):
            fn()
from firewall import patrol as firewall_patrol, reapply_blocks
from whitecells import hunt as whitecell_hunt
import firewall
if not firewall.DRY_RUN:
    reapply_blocks()

if __name__ == "__main__":
    if "--loop" in sys.argv:
        interval = int(sys.argv[sys.argv.index("--loop") + 1])
        while True:
            if settings.load().get("master_enabled", True):
                run_enabled()
                # firewall.patrol() held in reserve — whitecells(qwen LLM + rules) respond per design
                whitecell_hunt()
            print(f"--- sleeping {interval}s ---")
            time.sleep(interval)
    else:
        run_enabled()
        # firewall.patrol() held in reserve — whitecells(qwen LLM + rules) respond per design
        whitecell_hunt()
