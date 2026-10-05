"""mastermind — compatibility shim.

The brain now lives in brain.py (opencode client + budget + periodic sweep).
This file is kept so existing callers keep working:
    python3 mastermind.py ask "question"   → one-shot ask
    python3 mastermind.py daemon           → periodic sweep loop
    python3 mastermind.py sweep            → one sweep now
    python3 mastermind.py status           → budget status JSON
    python3 mastermind.py session          → session id
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import brain
import brain_budget

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if mode == "daemon":
        brain.daemon()
    elif mode == "sweep":
        print(brain.sweep())
    elif mode == "status":
        import json
        print(json.dumps(brain_budget.status()))
    elif mode == "session":
        print(brain.SESSION)
    else:
        print(brain.ask(" ".join(sys.argv[2:]) or sys.stdin.read(), force=True))
