# SKIN — defense & monitoring layer ("skin & skeleton")

Purpose: before any brain/heart/motar work happens, the skin watches the machine,
checks connections, detects suspicious activity, logs it, and reacts safely.

Design rules:
- Everything defaults to **dry-run** (log + alert only). Real blocking must be opted in.
- Every incident goes to `~/blackhorse/logs/threats.jsonl` (append-only JSONL).
- Modules have no hard dependency on psutil; it's optional for deeper checks.

Modules:
- `threat_log.py`  — append-only incident logger
- `net_check.py`   — connection health: internet, DNS, Ollama, Phoenix, exchanges
- `watchdog.py`    — scans system state/logs for suspicious patterns
- `firewall.py`    — response actions (alert, kill, block) with dry_run default
- `defense.py`     — main loop / entrypoint
