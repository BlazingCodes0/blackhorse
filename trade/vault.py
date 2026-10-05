"""BlackHorse vault — encrypted storage for exchange API keys.

Design goals:
  * Secrets NEVER sit in git, in prompts, or in logs.
  * AES-256-GCM (authenticated) exactly like the professional standard already
    used in qubitFlow, so the crypto is boring and correct.
  * A single local master key (0600) unlocks the vault. In production you can
    instead set BLACKHORSE_MASTER_KEY in the environment (never committed).

Storage:
  config/.master_key        0600  raw key (32 random bytes, base64url)
  config/exchange_keys.json 0600  { name: "v1.iv.tag.ciphertext" }

CLI (secret is read with getpass, never via argv so it stays out of history):
  python3 trade/vault.py set kraken.api_key
  python3 trade/vault.py set kraken.private_key
  python3 trade/vault.py list
  python3 trade/vault.py delete kraken.api_key
  python3 trade/vault.py selfcheck
"""
import os, sys, json, base64, getpass
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

BH = Path.home() / "blackhorse"
CONFIG = BH / "config"
KEY_FILE = CONFIG / ".master_key"
VAULT_FILE = CONFIG / "exchange_keys.json"
ALGO = "v1"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _config_error(msg):
    raise RuntimeError(
        f"{msg}\nSet BLACKHORSE_MASTER_KEY in the environment, or let a local "
        f"key file be created at {KEY_FILE} (chmod 600)."
    )


def _master_key() -> bytes:
    env = os.environ.get("BLACKHORSE_MASTER_KEY", "").strip()
    if env:
        return _b64d(env) if len(env) < 64 else env.encode()
    if AESGCM is None:
        _config_error("cryptography is not installed; cannot encrypt secrets.")
    CONFIG.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    return base64.urlsafe_b64decode(KEY_FILE.read_bytes())


def _ensure_gitignored():
    gi = BH / ".gitignore"
    needed = ["config/exchange_keys.json", "config/.master_key"]
    try:
        text = gi.read_text() if gi.exists() else ""
    except OSError:
        return
    missing = [n for n in needed if n not in text]
    if missing:
        with gi.open("a") as f:
            f.write("\n# BlackHorse secret vault — never commit\n" + "\n".join(missing) + "\n")


def _load_vault() -> dict:
    if not VAULT_FILE.exists():
        return {}
    try:
        return json.loads(VAULT_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_vault(v: dict):
    CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(VAULT_FILE)


def encrypt(value: str) -> str:
    if AESGCM is None:
        _config_error("cryptography is not installed; cannot encrypt secrets.")
    key = _master_key()
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, str(value).encode(), None)
    return ".".join([ALGO, _b64e(iv), _b64e(ct)])


def decrypt(payload: str) -> str:
    parts = str(payload).split(".")
    if len(parts) != 3 or parts[0] != ALGO:
        raise ValueError("Unsupported vault entry format.")
    _, iv, ct = parts
    key = _master_key()
    return AESGCM(key).decrypt(_b64d(iv), _b64d(ct), None).decode()


def hint(value: str) -> str:
    v = str(value or "")
    return "••••" if len(v) <= 4 else f"••••{v[-4:]}"


# --- public API (used by other BlackHorse modules) ---------------------------

def set_secret(name: str, value: str):
    if not name or not str(name).strip():
        raise ValueError("secret name is required")
    if value is None or str(value) == "":
        raise ValueError("secret value is empty — refusing to store")
    _ensure_gitignored()
    vault = _load_vault()
    vault[str(name)] = encrypt(value)
    _save_vault(vault)


def get_secret(name: str):
    vault = _load_vault()
    if name not in vault:
        return None
    return decrypt(vault[name])


def delete_secret(name: str):
    vault = _load_vault()
    if name in vault:
        del vault[name]
        _save_vault(vault)
        return True
    return False


def list_secrets() -> list:
    """Names + masked hints only — never the values."""
    out = []
    for name, payload in _load_vault().items():
        try:
            out.append({"name": name, "hint": hint(decrypt(payload))})
        except Exception:
            out.append({"name": name, "hint": "(undecryptable — wrong master key?)"})
    return sorted(out, key=lambda x: x["name"])


def selfcheck() -> dict:
    """Round-trip test without touching real secrets."""
    probe = "probe-" + os.urandom(8).hex()
    ct = encrypt(probe)
    ok = decrypt(ct) == probe
    return {
        "cryptography": AESGCM is not None,
        "master_key_source": "env" if os.environ.get("BLACKHORSE_MASTER_KEY") else "file",
        "vault_file": str(VAULT_FILE),
        "vault_exists": VAULT_FILE.exists(),
        "roundtrip_ok": ok,
        "entries": len(_load_vault()),
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "set":
        if len(sys.argv) < 3:
            print("usage: vault.py set <name>"); sys.exit(2)
        name = sys.argv[2]
        value = getpass.getpass(f"value for '{name}' (hidden): ")
        set_secret(name, value)
        print(f"stored '{name}' (encrypted)")
    elif cmd == "get":
        v = get_secret(sys.argv[2]) if len(sys.argv) > 2 else None
        print("(found)" if v else "(not found)")
    elif cmd == "delete":
        print("deleted" if delete_secret(sys.argv[2]) else "not found")
    elif cmd == "selfcheck":
        print(json.dumps(selfcheck(), indent=2))
    else:
        print(json.dumps(list_secrets(), indent=2))
