# BlackHorse

A local, autonomous **paper-trading** horse with its own defense layer — a PyQt6
desktop app over a file-driven trading loop and a host monitor, all on your own
machine.

> **Trading is paper-only by default.** BlackHorse is designed to reason, decide
> and record as if it traded, through config-driven exchanges, without moving real
> money unless *you* explicitly wire it up. It is not financial advice.

BlackHorse is one of a family of apps. Its sister project **Mr.Fluffy** reads
BlackHorse's own files to build its market views; BlackHorse itself depends on
neither.

## What it is

- **The trade house** — agents are "bodies" with personalities (`quick` scalper,
  `slow` patient HTF). A decider turns signal + verdict + treasury into one sized
  plan; a broker reconciles conflicting agents with evidence; a **heart** referee
  vets every action ALLOW/BLOCK. Spot only — no margin, leverage, futures or
  shorting (derivatives are *read* as information, never traded).
- **The skin** — a defense/monitoring layer that watches connections, processes
  and logs, and reacts **dry-run by default** (log + alert; blocking is opt-in).
- **Memory** — permanent (never deleted) and ephemeral (must be deleted),
  disk-first.
- **Everything is a file** — agents, treasury, decisions, signals, logs and P&L
  live in `config/` and `logs/`; the GUI and the loop re-read them.

## Requirements

- Python 3.10+
- Linux (the desktop app and the defense helpers assume a Linux host)
- See `requirements.txt` — core: `PyQt6`, `psutil`; optional: `cryptography`,
  `Pillow`, `qdarkstyle`

```bash
pip install -r requirements.txt
```

## Quick start

```bash
# desktop app (closing the window hides to tray; Quit stops it)
python3 app/main.py

# one trading-loop tick
python3 trade/loop.py

# the conversational entry point
./spice
```

The defense service can be installed from the app's **Skin (Defense)** page
(a user systemd unit, `blackhorse-defense.service`).

## Layout

```
app/        PyQt6 desktop app (main.py + agents_ui.py)
trade/      the loop, agents, decider, broker, heart, exchanges, portfolio
skin/       defense/monitoring layer (dry-run by default)
heart/      referee policies (rules.json) + playbooks
memory/     permanent + ephemeral memory (disk-first)
config/     agents, treasury, exchanges, decider/heart rules, settings
docs/       design notes and snapshots
plugins/    extra abilities for the conversational entry point
spice       CLI: talk to the house in plain language
```

## Secrets

The secret vault is **never** committed. `config/exchange_keys.json` and
`config/.master_key` are gitignored; the optional vault encrypts secrets at rest.
Keep it that way.

## Safety & scope

- Blocking/killing acts on **this host only** — never a remote machine.
- Defense is dry-run until explicitly enabled.
- Agents act only on evidence, and only when the heart allows it.

## License

MIT — see [LICENSE](LICENSE). © 2026 Mustafa Bashiir Ahmed (Mr.Fluffy).
