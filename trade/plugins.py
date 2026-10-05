"""plugins.py — discovery for Spice's abilities.

Drop a .py into plugins/ and Spice finds it at startup (trusted → in-process) or
marks it untrusted (→ run only through the bwrap jail). A plugin declares itself:

    META = {"name": "my_tool", "description": "...", "params": {...},
            "trust": "trusted" | "untrusted"}

    def run(context: dict) -> dict:      # for BOTH kinds
        return {"result": ...}

  * trusted   : imported in-process; exposes a callable tool directly. Use ONLY
                for code you wrote/reviewed.
  * untrusted : never imported. Executed inside the bwrap jail (no /home, no net,
                RAM/CPU capped) with context JSON; its return is the result.

This is the safe pairing: discovery is open, but untrusted discovery is jailed.

CLI:  python3 trade/plugins.py list | run <name> '<json context>'
"""
import json, sys, importlib.util
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
PLUGINS = BH / "plugins"
PLUGINS.mkdir(exist_ok=True)


def discover() -> list:
    """Return [{name, path, trust, description, params}] for every plugin file."""
    found = []
    for f in sorted(PLUGINS.glob("*.py")):
        if f.name.startswith("_"):
            continue
        meta = _read_meta(f)
        found.append({"name": meta.get("name", f.stem), "path": str(f),
                      "trust": meta.get("trust", "untrusted"),
                      "description": meta.get("description", ""),
                      "params": meta.get("params", {})})
    return found


def _read_meta(path: Path) -> dict:
    """Parse only the META literal — never executes the file."""
    import ast
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "META":
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        return {}
    return {}


def load_trusted(context: dict = None) -> int:
    """Import trusted plugins in-process and register their tools. Untrusted are
    deliberately NOT imported. Returns how many registered."""
    import tools
    import sandbox
    count = 0
    for p in discover():
        if p["trust"] != "trusted":
            continue
        f = Path(p["path"])
        code = f.read_text()
        if sandbox.scan(code):           # even trusted code is scanned for surprises
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"bh_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:
            continue
        if not hasattr(mod, "run"):
            continue
        name, desc, params = p["name"], p["description"], p["params"]

        def make(m):
            def _tool(**_):
                try:
                    return json.dumps(m.run(context or {}))[:2000]
                except Exception as e:
                    return f"(plugin error: {e})"
            return _tool
        tools._REGISTRY[name] = {"name": name, "description": f"[plugin] {desc}",
                                 "params": params, "fn": make(mod), "var_kwargs": True}
        count += 1
    return count


def run_untrusted(name: str, context: dict = None, timeout: int = 10, mem_mb: int = 256) -> dict:
    """Execute an untrusted plugin INSIDE the jail."""
    import sandbox
    p = next((x for x in discover() if x["name"] == name), None)
    if not p:
        return {"ok": False, "error": f"no plugin '{name}'"}
    if p["trust"] == "trusted":
        return {"ok": False, "error": f"'{name}' is trusted — call it as a normal tool"}
    # build a tiny runner that loads the file and calls run(context), all jailed
    runner = (
        "import sys, json\n"
        f"g={{'__name__':'__jail_plugin__'}}\n"
        f"exec(open({p['path']!r}).read(), g)\n"
        "ctx=json.loads(sys.stdin.read() or '{}')\n"
        "out=g['run'](ctx)\n"
        "print(json.dumps(out))\n"
    )
    # NOTE: the backend plugin file itself is read INSIDE the jail, so we mount it
    # read-only. Easiest: copy its source into the runner via bwrap --ro-bind.
    return _jailed_plugin_run(p["path"], context, timeout, mem_mb)


def _jailed_plugin_run(path: str, context: dict, timeout: int, mem_mb: int) -> dict:
    import sandbox
    import json as _j
    code = Path(path).read_text()
    violations = sandbox.scan(code)
    if violations:
        return {"ok": False, "error": f"static scan flagged: {violations[:5]}"}
    # run file contents, then main(context); fails safe if no callable
    wrapper = (
        "import sys, json\n"
        "g = {'__name__': '__jail_plugin__'}\n"
        "exec(compile(" + repr(code) + ", '<plugin>', 'exec'), g)\n"
        "ctx = json.loads(sys.stdin.read() or '{}')\n"
        "fn = g.get('run')\n"
        "if not callable(fn):\n"
        "    print(json.dumps({'error': 'plugin has no run(context)'})); sys.exit(0)\n"
        "print(json.dumps(fn(ctx)))\n"
    )
    return sandbox.jailed_exec(wrapper, context, timeout, mem_mb)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for p in discover():
            print(f"{p['trust']:9s} {p['name']:16s} {p['description'][:60]}")
    elif cmd == "run" and len(sys.argv) > 2:
        ctx = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        print(json.dumps(run_untrusted(sys.argv[2], ctx), indent=2))
    elif cmd == "load":
        print("registered trusted plugins:", load_trusted())
    else:
        print(__doc__)
