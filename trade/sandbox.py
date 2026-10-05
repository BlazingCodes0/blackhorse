"""sandbox.py — guardrails for Spice's self-generated tools.

Two layers, honestly labelled:
  1. STATIC scan (AST): refuse code that imports dangerous modules, calls
     eval/exec/compile/__import__/open-for-write, touches dunders, subprocess,
     sockets, os.system, etc. Runs at propose AND approve time.
  2. RUNTIME guard: generated modules are exec'd with a restricted __import__
     (allowlist only) and an attribute block on dunder access. Plus a per-call
     timeout when a generated tool is invoked.

This is guardrails, NOT a true OS sandbox. The owner still approves code; this
makes a mistake contained and obvious rather than silent.
"""
import ast, sys, time, signal, builtins, json, os, shutil as _shutil, subprocess as _sp, resource as _resource
from pathlib import Path

BH_OK_PREFIXES = ("json", "math", "time", "datetime", "statistics", "decimal",
                  "collections", "itertools", "functools", "re", "typing", "random",
                  # BlackHorse's own read-only helpers are fine to import
                  "edge", "crowd", "news", "treasury", "portfolio", "agents",
                  "state", "growth", "tools", "signalbot", "exchanges")

BLOCKED_NAMES = {"eval", "exec", "compile", "__import__", "open", "input",
                 "globals", "locals", "vars", "breakpoint", "memoryview"}
BLOCKED_ATTRS = {"__globals__", "__code__", "__subclasses__", "__bases__",
                 "__mro__", "__reduce__", "__reduce_ex__", "__getattribute__"}


def scan(code: str) -> list:
    """Return a list of violation strings ([] means it passed)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                if not _ok_module(n.name):
                    bad.append(f"import not allowed: {n.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and not _ok_module(node.module):
                bad.append(f"import not allowed: {node.module}")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BLOCKED_NAMES:
                bad.append(f"call not allowed: {fn.id}()")
            if isinstance(fn, ast.Attribute) and fn.attr in BLOCKED_NAMES:
                bad.append(f"call not allowed: .{fn.attr}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS or node.attr.startswith("__"):
                bad.append(f"attribute not allowed: {node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                bad.append(f"name not allowed: {node.id}")
    return sorted(set(bad))


def _ok_module(name: str) -> bool:
    root = name.split(".")[0]
    return any(root == p or name.startswith(p + ".") for p in BH_OK_PREFIXES)


def safe_import(original=builtins.__import__):
    """A restricted __import__ for generated code."""
    def _imp(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or not _ok_module(name):
            raise ImportError(f"sandbox: import of '{name}' is not allowed")
        return original(name, globals, locals, fromlist, level)
    return _imp


class _Timeout(Exception):
    pass


def run_with_timeout(fn, seconds=10):
    """Run a callable with a wall-clock timeout (SIGALRM; main thread only)."""
    def _handler(signum, frame):
        raise _Timeout()
    try:
        old = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(int(seconds))
    except (ValueError, AttributeError):
        old = None  # not main thread — best effort, no alarm
    try:
        return fn()
    finally:
        if old is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)


# ---- real OS jail (bubblewrap) for UNTRUSTED code ---------------------------
# Boundary: a jailed plugin can NOT see /home (no vault, no keys, no .git), has
# NO network, and is capped on RAM/CPU. It receives a JSON context on stdin and
# returns JSON on stdout. This is for plugins/ and anything Spice did not write
# and the owner has not vouched for. MONEY tools are NOT jailed — they run
# in-process behind Heart + spot-only + caps.

_BWRAP = _shutil.which("bwrap")
_JAIL_RO = ["/usr", "/lib", "/lib64", "/bin"]
_JAIL_RO_FILES = ["/etc/localtime", "/etc/ssl/certs"]


def jail_available() -> bool:
    return bool(_BWRAP) and sys.platform.startswith("linux")


def _bwrap_argv() -> list:
    argv = [_BWRAP]
    for p in _JAIL_RO:
        if os.path.exists(p):
            argv += ["--ro-bind", p, p]
    for p in _JAIL_RO_FILES:
        if os.path.exists(p):
            argv += ["--ro-bind", p, p]
    argv += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
             "--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"]
    return argv


def jailed_exec(code: str, context: dict = None, timeout: int = 10, mem_mb: int = 256) -> dict:
    """Run Python SOURCE in the jail with a JSON context; return parsed stdout.

    Returns {"ok": True, "result": <json>} or {"ok": False, "error": str}.
    The code should read JSON from stdin and print JSON to stdout.
    """
    if not jail_available():
        return {"ok": False, "error": "bwrap unavailable — refusing to run untrusted code"}
    ctx = json.dumps(context or {})
    argv = _bwrap_argv() + ["python3", "-I", "-c", code]

    def _limit():
        try:
            _resource.setrlimit(_resource.RLIMIT_AS, (mem_mb * 1024 * 1024,) * 2)
            _resource.setrlimit(_resource.RLIMIT_CPU, (timeout, timeout + 2))
        except Exception:
            pass

    try:
        p = _sp.run(argv, input=ctx, capture_output=True, text=True, timeout=timeout + 3,
                    preexec_fn=_limit)
    except _sp.TimeoutExpired:
        return {"ok": False, "error": f"jailed code timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"jail launch failed: {e}"}
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or "nonzero exit").strip()[:400]}
    out = (p.stdout or "").strip()
    try:
        return {"ok": True, "result": json.loads(out) if out else None}
    except json.JSONDecodeError:
        return {"ok": True, "result": out[:2000]}


def jailed_run_file(path: str, context: dict = None, timeout: int = 10, mem_mb: int = 256) -> dict:
    """Convenience: run a plugin FILE jailed (module-level code + main(ctx))."""
    try:
        code = Path(path).read_text()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    violations = scan(code)
    if violations:
        return {"ok": False, "error": f"static scan flagged: {violations[:5]}"}
    return jailed_exec(code, context, timeout, mem_mb)

