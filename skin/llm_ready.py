"""oli_ready — one-button model prep for the skin.

Usage: python3 llm_ready.py [model]
Checks Ollama, pulls the configured model if missing, and warms it so the
first real security verdict / chat answer is not stuck on model load.
"""
import json, subprocess, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import settings

def have(model: str) -> bool:
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10).stdout
        return any(line.split()[0] == model for line in out.splitlines()[1:] if line.strip())
    except Exception:
        return False

def ensure(model: str | None = None) -> bool:
    cfg = settings.load()
    model = model or cfg["llm_model"]
    if not cfg["llm_enabled"]:
        print("llm_enabled is false — flipping it on")
        cfg["llm_enabled"] = True
        settings.save(cfg)
    if not have(model):
        print(f"pulling {model} ...")
        subprocess.run(["ollama", "pull", model], check=False)
    print(f"warming {model} ...")
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": model, "prompt": "reply OK", "stream": False,
                             "keep_alive": cfg["llm_keep_alive_seconds"] or 300}).encode(),
            headers={"Content-Type": "application/json"})
        json.load(urllib.request.urlopen(req, timeout=cfg["llm_timeout_seconds"] * 4))
        print(f"ready: {model}")
        return True
    except Exception as e:
        print(f"warm failed: {e}")
        return False

if __name__ == "__main__":
    ensure(sys.argv[1] if len(sys.argv) > 1 else None)
