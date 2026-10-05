"""Exchange registry + connection manager. Config-driven, nothing hardcoded.

The list of exchanges, their credential schema, quote assets, and public
endpoints all live in `config/exchanges.json`. Add an exchange by adding an
entry there — no code change.

Credentials are stored in the encrypted vault (trade/vault.py) under the key
"<exchange>.<field>", e.g. "kraken.api_key". This module only ever checks for
PRESENCE and builds a descriptor; it never prints secret values.

CLI:
  python3 trade/exchanges.py list                 # all exchanges + active + credential status
  python3 trade/exchanges.py show kraken          # expected connection for one exchange
  python3 trade/exchanges.py use kraken           # set active exchange
  python3 trade/exchanges.py connect kraken       # guided entry of expected credentials -> vault
  python3 trade/exchanges.py status               # active exchange readiness
  python3 trade/exchanges.py add-key kraken api_key
"""
import json, sys, os, getpass
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
REGISTRY = BH / "config" / "exchanges.json"

import vault


def _load() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"cannot read {REGISTRY}: {e}")


def _save(reg: dict):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY)


def list_exchanges() -> dict:
    reg = _load()
    out = []
    for name, ex in reg.get("exchanges", {}).items():
        out.append({
            "name": name,
            "label": ex.get("label", name),
            "enabled": ex.get("enabled", True),
            "active": name == reg.get("active"),
            "ccxt_id": ex.get("ccxt_id"),
            "credentials": credential_status(name),
        })
    return {"active": reg.get("active"), "exchanges": out}


def get_exchange(name: str) -> dict:
    reg = _load()
    ex = reg.get("exchanges", {}).get(name)
    if not ex:
        raise KeyError(f"unknown exchange '{name}'. Known: {', '.join(reg.get('exchanges', {}))}")
    ex = dict(ex)
    ex["name"] = name
    return ex


def set_active(name: str):
    get_exchange(name)  # validate
    reg = _load()
    reg["active"] = name
    _save(reg)


def active_exchange() -> dict:
    return get_exchange(_load().get("active"))


def _vault_name(exchange: str, field: str) -> str:
    return f"{exchange}.{field}"


def credential_status(name: str) -> dict:
    """Presence of each required/optional credential — never the value."""
    ex = get_exchange(name)
    fields = []
    missing_required = []
    for f in ex.get("credential_fields", []):
        present = vault.get_secret(_vault_name(name, f["name"])) is not None
        fields.append({"field": f["name"], "label": f.get("label", f["name"]),
                       "required": f.get("required", False), "present": present})
        if f.get("required") and not present:
            missing_required.append(f["name"])
    return {
        "ready": len(missing_required) == 0,
        "missing_required": missing_required,
        "fields": fields,
    }


def connection_descriptor(name: str) -> dict:
    """Everything needed to connect, from registry + vault presence.
    Secrets are NOT included — only booleans and public config."""
    ex = get_exchange(name)
    return {
        "exchange": name,
        "label": ex.get("label", name),
        "ccxt_id": ex.get("ccxt_id"),
        "enabled": ex.get("enabled", True),
        "quote_assets": ex.get("quote_assets", []),
        "public_endpoints": ex.get("public_endpoints", {}),
        "permissions_needed": ex.get("permissions_needed", []),
        "permissions_forbidden": ex.get("permissions_forbidden", []),
        "credentials": credential_status(name),
    }


def store_credential(exchange: str, field: str, value: str):
    ex = get_exchange(exchange)  # validates exchange
    valid_fields = {f["name"] for f in ex.get("credential_fields", [])}
    if field not in valid_fields:
        raise KeyError(f"'{field}' is not a field for {exchange}. Expected: {', '.join(valid_fields)}")
    vault.set_secret(_vault_name(exchange, field), value)


def get_credential(exchange: str, field: str):
    """For the execution layer only. Never printed by this module."""
    return vault.get_secret(_vault_name(exchange, field))


def require_credentials(exchange: str):
    """Return {field: value} for all fields, raising if any required is missing."""
    st = credential_status(exchange)
    if not st["ready"]:
        raise RuntimeError(
            f"{exchange} is missing required credentials: {', '.join(st['missing_required'])}. "
            f"Run: python3 trade/exchanges.py connect {exchange}"
        )
    return {f["name"]: get_credential(exchange, f["name"]) for f in get_exchange(exchange)["credential_fields"]}


# --- CLI ---------------------------------------------------------------------

def _cmd_list():
    data = list_exchanges()
    print(f"active: {data['active']}")
    for e in data["exchanges"]:
        mark = "*" if e["active"] else " "
        cred = "ready" if e["credentials"]["ready"] else \
            "missing " + ",".join(e["credentials"]["missing_required"])
        print(f" {mark} {e['name']:<10} {e['label']:<20} ccxt={e['ccxt_id']:<10} creds={cred}")


def _cmd_show(name):
    d = connection_descriptor(name)
    print(json.dumps(d, indent=2))


def _cmd_connect(name):
    ex = get_exchange(name)
    print(f"Entering credentials for {ex.get('label', name)} (values hidden, stored encrypted).")
    print(f"Permissions needed:    {', '.join(ex.get('permissions_needed', [])) or '-'}")
    print(f"Permissions forbidden: {', '.join(ex.get('permissions_forbidden', [])) or '-'}")
    for f in ex.get("credential_fields", []):
        existing = vault.get_secret(_vault_name(name, f["name"])) is not None
        label = f.get("label", f["name"])
        req = "required" if f.get("required") else "optional"
        prompt = f"{label} [{req}{', already set — blank to keep' if existing else ''}]: "
        val = getpass.getpass(prompt)
        if not val and existing:
            continue
        if not val and f.get("required"):
            print(f"  skipped {f['name']} (required — will show as missing)")
            continue
        if val:
            store_credential(name, f["name"], val)
            print(f"  stored {f['name']} (encrypted)")
    status = credential_status(name)
    print(f"{name} ready: {status['ready']}" + ("" if status["ready"] else f" (missing: {status['missing_required']})"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        _cmd_list()
    elif cmd == "show" and len(sys.argv) > 2:
        _cmd_show(sys.argv[2])
    elif cmd == "use" and len(sys.argv) > 2:
        set_active(sys.argv[2]); print(f"active exchange set to {sys.argv[2]}")
    elif cmd == "connect" and len(sys.argv) > 2:
        _cmd_connect(sys.argv[2])
    elif cmd == "status":
        name = sys.argv[2] if len(sys.argv) > 2 else active_exchange()["name"]
        print(json.dumps(connection_descriptor(name), indent=2))
    elif cmd == "add-key" and len(sys.argv) > 3:
        store_credential(sys.argv[2], sys.argv[3], getpass.getpass(f"{sys.argv[3]} (hidden): "))
        print("stored")
    else:
        print(__doc__)
