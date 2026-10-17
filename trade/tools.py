"""tools.py — self-describing tool registry for Spice.

This is the dynamic core: a tool is just a Python function plus a declaration.
Spice's tool list is GENERATED from this registry at runtime — so adding a
capability never means editing a prompt. If it's registered, Spice sees it and
can call it. The model decides when and why.

Register:
    @tool("place_order", "Place the owner's order (gated).",
          {"side": "buy|sell", "pair": "DOT/USD", "amount_usd": "number",
           "mode": "paper|live", "order_type": "market|limit (optional)",
           "price": "number (for limit)"})
    def place_order(side, pair, amount_usd, mode, order_type="market", price=None): ...

Introspect:
    spec()          -> [{name, description, params}, ...]  (fed to the model)
    run(name, args) -> str  (dispatch; unknown name returns an error string)
"""
import json, sys, inspect, builtins
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))
sys.path.insert(0, str(BH / "memory"))

_REGISTRY = {}


def tool(name, description, params=None):
    """Decorator: declare a tool. `params` is a name->description/type map."""
    def deco(fn):
        _REGISTRY[name] = {"name": name, "description": description,
                           "params": params or {}, "fn": fn}
        return fn
    return deco


def spec() -> list:
    """The tool list Spice is given — built from what's actually registered."""
    out = []
    for t in _REGISTRY.values():
        out.append({"name": t["name"], "description": t["description"], "params": t["params"]})
    return out


def spec_text() -> str:
    lines = ["AVAILABLE TOOLS (call at most one per turn):"]
    for t in spec():
        params = ", ".join(f"{k}: {v}" for k, v in t["params"].items())
        lines.append(f"  {t['name']}({params}) — {t['description']}")
    return "\n".join(lines)


def run(name: str, args: dict) -> str:
    t = _REGISTRY.get(name)
    if not t:
        return f"(unknown tool '{name}'; available: {', '.join(_REGISTRY)})"
    args = args or {}
    try:
        # macro tools take arbitrary params -> pass them straight through
        if t.get("var_kwargs"):
            return t["fn"](**(args or {}))
        # pass only declared params, filling defaults for any the model omitted
        sig = inspect.signature(t["fn"])
        call = {}
        for p in sig.parameters.values():
            if p.name in args and args[p.name] is not None:
                call[p.name] = args[p.name]
            elif p.default is inspect.Parameter.empty:
                return f"NEEDS:{p.name}"   # caller turns this into a question
            else:
                call[p.name] = p.default
        return t["fn"](**call)
    except Exception as e:
        return f"(tool error in {name}: {e})"


def _is_generated(name: str) -> bool:
    return GEN_ACTIVE.joinpath(f"{name}.py").exists()


# ---- generated tools: Spice's own code, owner-approved, never self-approved ---
GEN = BH / "trade" / "generated"
GEN_ACTIVE = GEN / "active"
GEN_PENDING = GEN / "pending"


def _load_generated():
    """Import owner-approved generated tool modules (active/), SANDBOXED."""
    if not GEN_ACTIVE.exists():
        return
    import importlib, sandbox
    for f in sorted(GEN_ACTIVE.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            code = f.read_text()
            # 1. static scan — a generated file that fails is skipped, not run
            violations = sandbox.scan(code)
            if violations:
                continue
            src = compile(code, str(f), "exec")
            ns = {"__name__": f"bh_gen_{f.stem}", "__file__": str(f), "tool": tool}
            # 2. restricted import + dunder block for the module body
            safe_builtins = dict(vars(builtins))
            safe_builtins["__import__"] = sandbox.safe_import()
            ns["__builtins__"] = safe_builtins
            exec(src, ns)   # module registers @tool at import
        except Exception:
            continue


# ---- macros: compose existing gated tools into a new named tool (safe, auto) --
MACROS = BH / "config" / "macros.json"


def _macros() -> dict:
    try:
        return json.loads(MACROS.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_macros(d):
    MACROS.parent.mkdir(parents=True, exist_ok=True)
    MACROS.write_text(json.dumps(d, indent=2))


def _load_macros():
    """Register each saved macro as a callable tool. Macros may declare PARAMS and
    reference them in steps as "${param}" (substituted at call time)."""
    for name, m in _macros().items():
        steps = m.get("steps", [])
        desc = m.get("description", "owner macro")
        params = m.get("params", {}) or {}

        def make(steps, params):
            def _macro(**kwargs):
                import re as _re

                def subst(v):
                    if isinstance(v, str):
                        def rep(match):
                            key = match.group(1)
                            return str(kwargs.get(key, match.group(0)))
                        return _re.sub(r"\$\{(\w+)\}", rep, v)
                    if isinstance(v, dict):
                        return {k: subst(x) for k, x in v.items()}
                    if isinstance(v, list):
                        return [subst(x) for x in v]
                    return v

                results = []
                for s in steps:
                    args = subst(s.get("args", {}))
                    # convert numeric-looking placeholders
                    for k, val in list(args.items()):
                        if isinstance(val, str) and _re.fullmatch(r"-?\d+(\.\d+)?", val):
                            args[k] = float(val) if "." in val else int(val)
                    results.append(f"{s.get('tool')} -> {run(s.get('tool'), args)}")
                return " | ".join(results)
            return _macro

        _REGISTRY[name] = {"name": name, "description": desc, "params": params,
                           "fn": make(steps, params), "var_kwargs": True}



# ------------------------- registered tools ---------------------------------
# Anything added here is instantly visible to Spice. No prompt edits.

def _price(pair):
    try:
        import crowd
        c = crowd.get()
        if c.get("mark_price"):
            return float(c["mark_price"])
    except Exception:
        pass
    try:
        sig = json.loads((BH / "logs" / "signal_latest.json").read_text())
        for c in (sig.get("channels") or {}).values():
            if c.get("currPrice"):
                return float(c["currPrice"])
    except Exception:
        pass
    return None


def _norm_pair(raw):
    if not raw:
        return "DOT/USD"
    up = str(raw).upper()
    if "/" in up:
        return up
    return up[:-4] + "/USDT" if up.endswith("USDT") else (
        up[:-3] + "/USD" if up.endswith("USD") and len(up) > 3 else up + "/USD")


@tool("place_order", "Place the OWNER'S manual order (paper or live, gated). "
                     "Requires an explicit mode.",
      {"side": "buy|sell", "pair": "e.g. DOT/USD", "amount_usd": "number",
       "mode": "paper|live", "order_type": "limit|market (optional, default limit)",
       "price": "number, for limit orders",
       "time_in_force": "GTC|IOC|GTD|FOK (optional, default GTD)"})
def place_order(side, pair, amount_usd, mode, order_type="limit", price=None, time_in_force="GTD"):
    import execute
    side = str(side).lower()
    mode = str(mode).lower()
    if side not in ("buy", "sell"):
        return "need side (buy or sell)"
    if mode not in ("paper", "live"):
        return "need mode (paper or live)"
    tif = str(time_in_force or "GTD").upper()
    if tif not in ("GTC", "IOC", "GTD", "FOK"):
        tif = "GTD"
    p = _norm_pair(pair)
    entry = float(price) if price else (_price(p) or 0)
    plan = {"action": side.upper(), "order_type": str(order_type).lower(),
            "entry": entry or None, "size_quote": float(amount_usd), "manual": True,
            "time_in_force": tif}
    if price:
        plan["price"] = float(price)
    res = execute.execute(plan, pair=p, mode=mode)
    if res.get("blocked"):
        return f"refused ({mode}): {res.get('reason')}"
    if res.get("ok"):
        return (f"{mode.upper()} {side} {p} ${amount_usd} filled @ {round(res.get('fill',0),6)} "
                f"qty {round(res.get('qty',0),4)} (pos {(res.get('position') or {}).get('id')})")
    return f"not done: {res.get('reason', res)}"


@tool("status", "The house snapshot: capital, positions, P&L, market, gates.")
def status():
    import console
    return console.human_status()


@tool("list_agents", "List the agents (the bodies).")
def list_agents():
    import agents
    rows = agents.list_agents()
    return "no agents" if not rows else "\n".join(
        f"{'on' if a['enabled'] else 'off'} {a['id']} {a['name']} [{a['kind']}/"
        f"{a.get('profile_label') or a.get('entry_interval') or '-'}] ${a['capital']}"
        for a in rows)


@tool("create_agent", "Create an agent body (trading or youtube).",
      {"kind": "trading|youtube", "interval": "5m|15m|60m|240m|1440m", "capital": "number", "name": "optional"})
def create_agent(kind="trading", interval="15m", capital=0, name=None):
    import agents
    a = agents.create_agent(kind, interval, name=name, capital=float(capital or 0))
    return f"created {a['id']} ({a.get('profile_label') or interval} entry, ${a['capital']})"


@tool("enable_agent", "Enable/arm an agent (its trades stay governed by the playbook).",
      {"agent_id": "e.g. agent_1", "on": "true|false"})
def enable_agent(agent_id, on=True):
    import agents
    return str(agents.set_enabled(agent_id, str(on).lower() not in ("false", "0", "no", "off")))


@tool("fund_agent", "Set an agent's capital.",
      {"agent_id": "e.g. agent_1", "capital": "number"})
def fund_agent(agent_id, capital):
    import agents
    return str(agents.set_capital(agent_id, float(capital)))


@tool("allocate", "Give the house capital (the owner funding it).", {"amount": "number"})
def allocate(amount):
    import treasury
    return str(treasury.allocate(float(amount), note="spice"))


@tool("set_reserve", "Set the reserve floor the house may never spend below.", {"amount": "number"})
def set_reserve(amount):
    import treasury
    return str(treasury.set_reserve(float(amount)))


@tool("edge_report", "The measured, post-fee edge (which interval/side actually works).")
def edge_report():
    import edge
    return edge.brief()


@tool("crowd", "Read-only derivatives sentiment (funding, open interest, long/short). "
               "We read it; we never trade it.")
def crowd():
    import crowd as c
    d = c.get()
    return json.dumps({"funding": d.get("funding_rate"), "long_short": d.get("long_short_ratio"),
                       "oi_5h": d.get("oi_change_pct_5h"),
                       "read": (d.get("interpretation") or {}).get("notes")})


@tool("cancel_order", "Cancel one order by txid.", {"txid": "string"})
def cancel_order(txid):
    import execute
    return str(execute.cancel(txid))


@tool("cancel_all", "Cancel all open orders for a pair.", {"pair": "e.g. DOT/USD"})
def cancel_all(pair):
    import brain as b
    opens, err = b._open_orders_by_pair(_norm_pair(pair))
    if err:
        return err
    if not opens:
        return f"no open {pair} orders"
    import execute
    return f"cancelled {len(opens)}: {[execute.cancel(t) for t, _ in opens]}"


@tool("remember", "Store a durable owner preference or fact for later (learn, don't re-ask).",
      {"key": "e.g. default_mode", "value": "the preference"})
def remember(key, value):
    import growth
    growth.add("preference", f"{key}={value}", source="spice")
    return f"noted {key}={value}"


@tool("recall", "Look up stored preferences/facts.", {"query": "text to search for"})
def recall(query=""):
    import growth
    hits = growth.search(query) if hasattr(growth, "search") else growth.list_recent(limit=8)
    return json.dumps(hits)[:1500]


# ---- meta-tools: Spice extending itself ------------------------------------
@tool("compose_tool", "Create a reusable MACRO from existing tools (safe, immediate). "
                      "A macro is a named sequence of tool calls. It may declare params "
                      "and use them in steps as ${param}.",
      {"name": "macro name", "description": "what it does",
       "steps": '[{"tool":"...","args":{...}}, ...]  (use ${param} for values)',
       "params": '{"param": "description"} (optional)'})
def compose_tool(name, description, steps, params=None):
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            return "steps must be JSON"
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = {}
    if not isinstance(steps, list) or not steps:
        return "steps must be a non-empty list"
    # only allow composing EXISTING registered tools (cannot fabricate capability)
    unknown = [s.get("tool") for s in steps if s.get("tool") not in _REGISTRY]
    if unknown:
        return f"unknown tools in macro: {unknown}"
    m = _macros()
    m[name] = {"description": description, "steps": steps, "params": params or {},
               "created": __import__("time").time()}
    _save_macros(m)
    _load_macros()
    return f"macro '{name}' created (now callable)"


@tool("propose_tool", "Propose a NEW tool as Python code for the OWNER to approve. "
                      "It will NOT run until approved. Use for capability not covered by macros.",
      {"name": "tool name", "description": "what it does", "code": "python defining @tool(name,...) def ...: ..."})
def propose_tool(name, description, code):
    GEN_PENDING.mkdir(parents=True, exist_ok=True)
    import re
    safe = re.sub(r"[^a-z0-9_]", "_", str(name).lower())[:40] or "generated"
    path = GEN_PENDING / f"{safe}.py"
    header = (f'"""PROPOSED BY SPICE: {description}\nAwaiting owner approval.\n"""\n'
              "# ctx: `tool` decorator is injected; import modules normally.\n")
    # ensure the code can use @tool even if the model forgot to import it
    body = str(code)
    if "@tool" in body and "import tool" not in body and "from tools import" not in body:
        body = "# (tool decorator injected by the loader)\n" + body
    path.write_text(header + body + "\n")
    # static safety scan — warn, do not silently accept
    import sandbox
    violations = sandbox.scan(body)
    if violations:
        return (f"proposed '{name}' BUT the sandbox flagged it ({'; '.join(violations[:5])}). "
                f"Review pending/{safe}.py — it will NOT load until approve passes the scan.")
    return (f"proposed tool '{name}' written to pending/{safe}.py (sandbox: clean). "
            f"Approve with: python3 trade/tools.py approve {safe}")


@tool("list_pending_tools", "List Spice's proposed tools awaiting approval.")
def list_pending_tools():
    if not GEN_PENDING.exists():
        return "none pending"
    files = [f.stem for f in GEN_PENDING.glob("*.py")]
    return "pending: " + ", ".join(files) if files else "none pending"


# load owner-approved generated tools + saved macros at import time
_load_generated()
_load_macros()


def load_plugins() -> int:
    """Discover plugins/ and register trusted ones; untrusted are jailed at call."""
    try:
        import plugins
        return plugins.load_trusted(_jail_context())
    except Exception:
        return 0


def _jail_context() -> dict:
    """A SAFE context for plugins: only public market facts, never secrets."""
    ctx = {}
    try:
        import crowd
        c = crowd.get()
        ctx["funding"] = c.get("funding_rate")
        ctx["long_short"] = c.get("long_short_ratio")
    except Exception:
        pass
    try:
        import news
        n = news.get()
        ctx["fng"] = (n.get("fear_greed") or {}).get("value")
        ctx["headlines"] = [h.get("title") for h in n.get("headlines", [])][:5]
    except Exception:
        pass
    try:
        import edge
        ctx["proven_edge"] = edge.proven_cells()
    except Exception:
        pass
    return ctx


@tool("run_plugin_jailed", "Run an UNTRUSTED plugin inside the OS jail (no filesystem, "
                           "no network, RAM/CPU capped). Use for code you did not write.",
      {"name": "plugin name"})
def run_plugin_jailed(name):
    import plugins
    res = plugins.run_untrusted(name, _jail_context())
    return json.dumps(res)[:1500]


@tool("list_plugins", "List discovered plugins (trusted run in-process; untrusted are jailed).")
def list_plugins():
    import plugins
    rows = plugins.discover()
    if not rows:
        return "no plugins in plugins/ yet"
    return "\n".join(f"{p['trust']} {p['name']} — {p['description'][:50]}" for p in rows)


load_plugins()


def approve(name: str) -> str:
    """OWNER action: move a pending tool into active so it loads next import."""
    import shutil, sandbox
    GEN_ACTIVE.mkdir(parents=True, exist_ok=True)
    src = GEN_PENDING / f"{name}.py"
    if not src.exists():
        return f"no pending tool '{name}'"
    violations = sandbox.scan(src.read_text())
    if violations:
        return f"REFUSED '{name}': sandbox flagged {violations[:5]} — fix and re-propose"
    shutil.move(str(src), str(GEN_ACTIVE / f"{name}.py"))
    return f"approved '{name}' (sandbox: clean) — active on next Spice start"


def reject(name: str) -> str:
    src = GEN_PENDING / f"{name}.py"
    if src.exists():
        src.unlink()
        return f"rejected '{name}'"
    return f"no pending tool '{name}'"



@tool("adabot", "Run the real SignalBot advisor across a pair and return support/resistance, price floors, and lane forecasts (spot, no execution).",
      {"pair": "string e.g. DOT/USD or BTC/USD", "intervals": "e.g. '60,240' or '60,240,1440'"})
def adabot(pair, intervals="60,240,1440"):
    import subprocess as _sp, json as _j
    valid = {"1","5","15","60","240","1440"}
    ivs = []
    for raw in str(intervals).split(","):
        raw = str(raw).strip().replace("m","")
        if raw in valid:
            ivs.append(raw)
    if not ivs:
        return "(error: no valid intervals)"
    kpair = str(pair).replace("/", "").upper()
    if kpair.startswith("BTC"): kpair = kpair.replace("BTC", "XBT", 1)
    qf = Path.home()/"qubitFlowBeta"/"qubitFlowBeta"/"SignalBot"/"adaBot"
    script = f"const {{runAdaBot}}=require('{qf}/adaBot.js');runAdaBot({{pair:'{kpair}',intervals:{ivs}}}).then(r=>process.stdout.write(JSON.stringify(r))).catch(e=>{{console.error(e.message);process.exit(1);}})"
    try:
        r = _sp.run(["node","-e", script], cwd=str(qf.parent), capture_output=True, text=True, timeout=120)
        return _j.dumps(_j.loads(r.stdout), indent=1) if r.returncode == 0 else r.stderr.strip()[:240]
    except Exception as e:
        return f"adabot error: {e}"



@tool("route_images_preview", "Scan a folder, list images and the rotation action BlackHorse would take for each. Does NOT touch files.",
      {"src_dir": "source folder", "dst_folder": "target folder"})
def route_images_preview(src_dir, dst_folder):
    import os, json
    try:
        from PIL import Image
    except ImportError:
        return "(error: Pillow not installed)"
    if not os.path.isdir(src_dir):
        return f"(error: no such folder {src_dir})"
    rows = []
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp')):
            continue
        src = os.path.join(src_dir, f)
        try:
            im = Image.open(src)
            w,h = im.size
            orient = 'portrait' if h>w else 'landscape' if w>h else 'square'
            rows.append({
                "src": f,
                "dst": f,
                "orientation": orient,
                "width": w,
                "height": h,
                "suggested_rotation": 0,
                "action": "copy",
            })
        except Exception as e:
            rows.append({"src": f, "error": str(e)})
    return json.dumps({"src_dir": src_dir, "dst_folder": dst_folder, "count": len(rows), "items": rows}, indent=1)

@tool("route_images_apply", "Copy routed images from src to dst. Owner first sends the edited item list like '[{\"src\":\"photo.jpg\",\"dst\":\"rotated.jpg\",\"rotation\":90}]' to apply; original files are left untouched.",
      {"src_dir": "source folder", "dst_folder": "target folder", "items": "json list of rows like [{\"src\":\"photo.jpg\",\"dst\":\"rotated.jpg\",\"rotation\":90}]"})
def route_images_apply(src_dir, dst_folder, items):
    import os, json, shutil
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return "(error: Pillow not installed)"
    if not os.path.isdir(src_dir):
        return f"(error: no such folder {src_dir})"
    os.makedirs(dst_folder, exist_ok=True)
    try:
        rows = json.loads(items) if isinstance(items, str) else (items or [])
    except Exception as e:
        return f"(error: items must be JSON: {e})"
    out=[]
    for r in rows:
        s = r.get("src") or r.get("file")
        d = r.get("dst") or s
        rot = int(r.get("rotation", r.get("rotate", 0)) or 0)
        if not s or not d:
            out.append({"src": s,"error":"missing src/dst"}); continue
        src_path = os.path.join(src_dir, s)
        dst_path = os.path.join(dst_folder, d)
        try:
            im = Image.open(src_path)
            if rot % 360 == 90: im = im.transpose(Image.ROTATE_90)
            elif rot % 360 == 180: im = im.transpose(Image.ROTATE_180)
            elif rot % 360 == 270: im = im.transpose(Image.ROTATE_270)
            im.save(dst_path)
            out.append({"src": s, "dst": d, "rotation": rot, "copied": True})
        except Exception as e:
            out.append({"src": s, "dst": d, "error": str(e)})
    return json.dumps({"copied_dir": dst_folder, "count": len(out), "items": out}, indent=1)


@tool("storybook", "Create/manage BlackHorse writing projects: book/script, chapters, characters, details. Use actions create_project, add_character_detail, add_chapter, show_characters, show_chapters, show_project, move_chapter, delete_chapter, move_file, delete_file.",
      {"action": "create_project|add_character_detail|add_chapter|show_characters|show_chapters|show_project|move_chapter|delete_chapter|move_file|delete_file",
       "project": "project name (optional for create)",
       "character": "character name for add_character_detail",
       "detail": "text detail for add_character_detail",
       "chapter": "chapter/slug name",
       "text": "chapter note/known summary text"})
def storybook(action, project="default", character=None, detail=None, chapter=None, text=None):
    import json, hashlib, pathlib, time, re, shutil
    root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
    root.mkdir(parents=True, exist_ok=True)
    slug = hashlib.md5((project or 'default').strip().encode()).hexdigest()[:8]
    fp = root/f"{slug}.json"
    def _load():
        if not fp.exists():
            return {"title": project, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "chapters": [], "characters": {}}
        try: return json.loads(fp.read_text())
        except Exception: return {"title": project, "chapters": [], "characters": {}}
    data = _load()
    if action == "create_project":
        data["title"] = project or data.get("title") or "untitled"
        fp.write_text(json.dumps(data, indent=2))
        proj_dir = root/f"{slug}_project"
        try:
            (proj_dir/"ideas").mkdir(parents=True, exist_ok=True)
            (proj_dir/"scenes").mkdir(parents=True, exist_ok=True)
            (proj_dir/"characters").mkdir(parents=True, exist_ok=True)
            (proj_dir/"notes").mkdir(parents=True, exist_ok=True)
            (proj_dir/"chapters").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return f"created/opened project '{data['title']}' with dirs: project/ideas, project/scenes, project/characters, project/notes"
    if action == "add_character_detail":
        if not character or not detail:
            return "(error: need character and detail)"
        data["characters"].setdefault(character, []).append({"detail": detail, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")})
    elif action == "add_chapter":
        if not chapter:
            return "(error: need chapter title)"
        data["chapters"].append({"title": chapter, "text": (text or "")[:2000], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")})
    elif action == "show_characters":
        fp.write_text(json.dumps(data, indent=2))
        return json.dumps(data.get("characters", {}), indent=1)[:1900]
    elif action == "show_chapters":
        fp.write_text(json.dumps(data, indent=2))
        return json.dumps(data.get("chapters", []), indent=1)[:1900]
    elif action == "show_project":
        fp.write_text(json.dumps(data, indent=2))
        return json.dumps({"title": data["title"], "chapters": data["chapters"][:5], "characters": {k:v[:5] for k,v in data["characters"].items()}}, indent=1)[:1900]
    elif action == "move_chapter":
        # move by exact or startswith title to a directory under the project folder
        if not chapter or not text:
            return "(error: need chapter title and target folder name)"
        proj_dir = root/f"{slug}_project"
        matched = None
        for i,ch in enumerate(data.get('chapters',[])):
            if ch.get('title','').startswith(chapter) or ch.get('text','').startswith(chapter):
                matched = i; break
        if matched is None:
            return "(error: chapter not found)"
        ch = data['chapters'].pop(matched)
        folder = proj_dir/(text or 'notes')
        folder.mkdir(parents=True, exist_ok=True)
        import re as _re
        import shutil as _sh
        safe = _re.sub(r'[^a-zA-Z0-9]+','_',ch.get('title','')).strip('_').lower()[:37]
        (folder / f"{safe}.md").write_text(ch.get('text',''))
        fp.write_text(json.dumps(data, indent=2))
        return f"moved chapter '{ch.get('title','')}' -> {text}/{safe}.md"
    elif action == "delete_chapter":
        if not chapter:
            return "(error: need chapter title)"
        for i,ch in enumerate(data.get('chapters',[])):
            if ch.get('title','').startswith(chapter) or ch.get('text','').startswith(chapter):
                data['chapters'].pop(i)
                fp.write_text(json.dumps(data, indent=2))
                return f"deleted chapter '{ch.get('title','')}'"
        return "(error: chapter not found)"
    elif action == "move_file":
        # move a raw file from one project subfolder to another
        if not chapter or not text:
            return "(error: need current section name and target section name like 'scenes->notes')"
        parts = [p.strip() for p in text.split('->')]
        if len(parts)!=2:
            return "(error: target must look like 'ideas->notes')"
        src = root/f"{slug}_project/{parts[0]}"
        dst = root/f"{slug}_project/{parts[1]}"
        if not src.exists():
            return "(error: source folder not found)"
        dst.mkdir(parents=True, exist_ok=True)
        import shutil as _sh
        matched = None
        for f in src.iterdir():
            if f.name.startswith(chapter) or chapter in f.name:
                matched = f; break
        if matched is None:
            return "(error: file not found in source)"
        _sh.move(str(matched), str(dst/matched.name))
        return f"moved file {matched.name} -> {parts[1]}/"
    elif action == "delete_file":
        if not chapter or not text:
            return "(error: need section and keyword/filename fragment)"
        parts=[p.strip() for p in text.split(':')]
        if len(parts)!=2:
            return "(error: use section:filename)"
        src = root/f"{slug}_project/{parts[0]}"
        if not src.exists():
            return "(error: section not found)"
        matched = None
        for f in src.iterdir():
            if parts[1] in f.name:
                matched = f; break
        if matched is None:
            return "(error: file not found)"
        matched.unlink()
        return f"deleted file {matched.name} in {parts[0]}/"
    else:
        return "(error: unknown action)"
    fp.write_text(json.dumps(data, indent=2))
    return f"saved to project '{data['title']}'"

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for t in spec():
            print(f"{t['name']:20s} {t['description'][:70]}")
    elif cmd == "approve" and len(sys.argv) > 2:
        print(approve(sys.argv[2]))
    elif cmd == "reject" and len(sys.argv) > 2:
        print(reject(sys.argv[2]))
    elif cmd == "pending":
        print(list_pending_tools())
    else:
        print("usage: tools.py [list|pending|approve <name>|reject <name>]")


