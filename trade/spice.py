"""spice.py — Spice, the owner's JARVIS. Dynamic, not curated.

Design rules that keep this DYNAMIC:
  * Capabilities live in trade/tools.py as a registry; Spice's tool list is
    GENERATED from it. Adding a tool never means editing a prompt or this file.
  * Behaviour lives in config/spice_persona.md (a file, not code).
  * Preferences are LEARNED: when the owner tells Spice something ("always paper",
    "I like DOT"), Spice calls remember(); recall() feeds them back. The model
    decides; the code does not enumerate cases.
  * The model answers with strict JSON {say|ask|tool}; Spice executes and loops.

Division of labour (unchanged): Spice acts on the owner's DIRECT requests; agents
run through the playbook/decider/broker/Heart. Spice never bypasses that.
"""
import json, sys, time, argparse
from pathlib import Path

BH = Path.home() / "blackhorse"
for d in ("trade", "memory", "skin"):
    sys.path.insert(0, str(BH / d))
PERSONA = BH / "config" / "spice_persona.md"
CONVO = BH / "logs" / "spice_convo.jsonl"


def _persona() -> str:
    try:
        return PERSONA.read_text()
    except OSError:
        return "You are Spice, the owner's JARVIS. Reply with strict JSON {say|ask|tool}."


def _log(role, text):
    CONVO.parent.mkdir(parents=True, exist_ok=True)
    with CONVO.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "role": role, "text": str(text)[:4000]}) + "\n")


def _recent(n=8):
    if not CONVO.exists():
        return []
    out = []
    for line in CONVO.read_text(errors="ignore").splitlines()[-n:]:
        try:
            e = json.loads(line)
            out.append({"role": e.get("role"), "text": e.get("text")})
        except json.JSONDecodeError:
            pass
    return out


def _state() -> str:
    """Awareness comes from the self-registering provider registry (trade/state.py),
    not a hardcoded list. Add a provider and Spice sees it; remove one and it doesn't."""
    try:
        import state as _state_mod
        return json.dumps(_state_mod.snapshot())[:3500]
    except Exception as e:
        return json.dumps({"error": str(e)})


def _ask_model(user_msg: str, extra: str = "") -> dict:
    import brain as b
    import tools
    prompt = (
        _persona() + "\n\n" + tools.spec_text() +
        "\n\nLIVE STATE (trust over memory):\n" + _state() +
        "\n\nRECENT:\n" + json.dumps(_recent(8)) +
        ("\n\n" + extra if extra else "") +
        "\n\nOWNER: " + user_msg +
        "\n\nReply with ONLY the JSON object."
    )
    raw = b.ask(prompt, force=True, session=b.SESSION, kind="chat", route=False)
    return _parse(raw)


def _parse(raw: str) -> dict:
    """Parse Spice's reply, tolerating double-encoded JSON (a JSON string whose
    content is itself the {say|ask|tool} object).

    Handles: a proper object; an object whose 'say' is a JSON string; a bare
    JSON string; and a string containing an embedded object."""
    def _extract(s):
        """Brace-balanced extraction with repair: tolerates a trailing missing
        '}' (the model often drops one)."""
        if not isinstance(s, str) or "{" not in s:
            return None
        start = s.find("{")
        depth = 0
        in_str = False
        esc = False
        cut = None
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cut = i + 1
                    break
        frag = s[start:cut] if cut else s[start:]
        for attempt in (frag, frag + "}" * max(depth, 0), frag + "}" * max(depth + 1, 0)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
        return None

    d = _extract(raw)
    if d is None:
        return {"say": str(raw).strip()}
    for _ in range(4):
        # unwrap -------------------------------------------------------------
        if isinstance(d, dict) and isinstance(d.get("say"), str) and d["say"].strip().startswith("{"):
            inner = _extract(d["say"])
            if inner is None:
                break
            d = inner
            continue
        if isinstance(d, str):
            inner = _extract(d)
            if inner is None:
                break
            d = inner
            continue
        break
    return d if isinstance(d, dict) else {"say": str(raw).strip()}


def _recall(query: str, limit: int = 6) -> list:
    """Long-term memory recall: relevant past (lessons, decisions, episodes) + the
    rolling narrative summary. This is what makes Spice continuous, not stateless."""
    out = []
    try:
        import index
        for r in index.search(query, limit=limit):
            out.append({"kind": r["kind"], "text": r["text"]})
    except Exception:
        pass
    try:
        import growth
        for r in growth.search(query, limit=4):
            out.append({"kind": r["kind"], "text": r["text"]})
    except Exception:
        pass
    try:
        import store
        for d in store.recent_decisions(limit=3):
            out.append({"kind": "decision", "text": f"{d.get('action')} {d.get('pair')} — {d.get('reason')}"})
    except Exception:
        pass
    return out[:12]


def _remember_turn(user_msg: str, reply: str):
    """Persist the turn to long-term memory and, periodically, summarize."""
    try:
        import index
        index.remember(kind="conversation", text=f"owner: {user_msg} || spice: {reply}",
                       meta={"role": "exchange"}, source="spice")
    except Exception:
        pass


def _learn_from_correction(user_msg: str, reply: str):
    """Detect the owner correcting Spice and store it as a durable rule so Spice
    does not need to be told twice."""
    t = user_msg.lower()
    cues = ("no,", "no ", "don't", "do not", "instead", "i said", "thats wrong",
            "that's wrong", "not like that", "stop ", "always ", "never ", "remember")
    if not any(c in t for c in cues) or len(user_msg) < 8:
        return None
    # ask the model to distill the rule, then store it
    try:
        import brain as b
        p = (f"The owner said: {user_msg!r}. If this is a standing correction or "
             f"preference, reply with ONE imperative rule (max 2 sentences), else reply NONE.")
        rule = b.ask(p, force=False, session=b.SESSION, kind="chat", route=False).strip()
        if rule and rule.upper() != "NONE" and len(rule) < 240:
            import growth
            growth.add("rule", rule, source="spice-correction")
            import index
            index.remember(kind="rule", text=rule, meta={"learned_from": user_msg[:120]},
                           source="spice")
            return rule
    except Exception:
        pass
    return None


def handle(user_msg: str, max_steps: int = 5) -> str:
    """Agentic loop: think -> act -> observe -> rethink, up to max_steps, then
    answer. This is what separates a mind from a one-shot command router."""
    import tools
    _log("owner", user_msg)

    # recall relevant long-term memory for this turn
    recalls = _recall(user_msg)
    scratch = f"RECALLED MEMORY:\n{json.dumps(recalls)[:1500]}" if recalls else ""
    observations = []

    for step in range(max_steps):
        d = _ask_model(user_msg, extra=scratch)
        if not ("tool" in d and isinstance(d["tool"], dict)):
            reply = d.get("ask") or d.get("say") or "(no reply)"
            break
        name = d["tool"].get("name")
        args = d["tool"].get("args", {}) or {}
        result = tools.run(name, args)
        if isinstance(result, str) and result.startswith("NEEDS:"):
            param = result.split(":", 1)[1]
            reply = d.get("ask") or f"Quick check — what's the {param}?"
            break
        observations.append({"tool": name, "args": args, "result": str(result)[:800]})
        # feed observations back so the model can chain or conclude
        scratch = (f"RECALLED MEMORY:\n{json.dumps(recalls)[:800]}\n\n"
                   f"OBSERVATIONS SO FAR:\n{json.dumps(observations)[:1800]}\n\n"
                   "Continue: call another tool if needed, or reply to the owner.")
    else:
        reply = observations[-1]["result"] if observations else "(no reply)"

    _log("spice", reply)
    _remember_turn(user_msg, reply)
    rule = _learn_from_correction(user_msg, reply)
    if rule:
        reply += f"\n(noted: {rule})"
    return reply


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="*")
    ap.add_argument("--chat", action="store_true")
    args = ap.parse_args()
    if args.chat:
        print("Spice> online. (type 'exit' to quit)")
        while True:
            try:
                q = input("you> ")
            except (EOFError, KeyboardInterrupt):
                print("\nSpice> bye"); break
            if q.strip().lower() in ("exit", "quit"):
                print("Spice> bye"); break
            print("Spice>", handle(q))
    else:
        print(handle(" ".join(args.message) or "status"))
