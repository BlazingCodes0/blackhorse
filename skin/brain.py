"""brain — BlackHorse's opencode-backed decision layer.

Current phase design:
  * opencode IS the brain. It is the expensive expert tier, reached on a
    rhythm of minutes (see brain_budget.py), never per-event.
  * The skin (whitecells.py + hard rules) is the war room: it resolves
    everything its deterministic rules can, and only ESCALATES genuinely
    ambiguous / high-severity signals upward.
  * Communication is bidirectional:
        skin  -> brain : whitecells escalate() into logs/brain_inbox.jsonl
        brain -> skin  : sweep() reads threats.jsonl ("any threats?") and
                         writes verdicts to logs/mastermind.jsonl
  * Every call passes brain_budget.allow(), so the two directions share one
    rate limit and cannot flood opencode.

CLI:
  python3 brain.py ask "question"   # one-shot, user-initiated (bypasses budget)
  python3 brain.py sweep            # one periodic scan, budget permitting
  python3 brain.py daemon           # loop: sweep every brain_sweep_seconds
  python3 brain.py status           # print budget status (JSON)
  python3 brain.py session          # print session id
"""
import json, shutil, subprocess, sys, time
from pathlib import Path

BH = Path.home() / "blackhorse"
THREATS = BH / "logs" / "threats.jsonl"
INBOX = BH / "logs" / "brain_inbox.jsonl"
REVIEWED = BH / "logs" / "brain_reviewed.json"
STATE = BH / "logs" / "mastermind_state.json"
OUT = BH / "logs" / "mastermind.jsonl"
SESSION = "ses_efc6395aaffeRebNNBPHMywYSD"  # BlackHorse Guardian session (user chat)
TRADE_SESSION_FILE = BH / "logs" / "trade_session.id"


def _live_brief() -> str:
    """One-line ground truth attached to each brain message so Spice
    doesn't quote stale gate state."""
    bits = []
    try:
        lp = BH / "logs" / "loop_state.json"
        if lp.exists():
            s = json.loads(lp.read_text())
            bits.append(f"loop={s.get('decision')}")
    except Exception:
        pass
    try:
        import heart as _h
        bits.append(f"can_arm={_h.unlock().get('can_arm')}")
    except Exception:
        pass
    try:
        bp = (BH / "logs" / "backtest_passed.flag").exists()
        pp = (BH / "logs" / "paper_period.flag").exists()
        bits.append(f"backtest={'yes' if bp else 'no'},paper_period={'yes' if pp else 'no'}")
    except Exception:
        pass
    bits.append("execute_live=enabled (market,limit,trailing,...)")
    # user-tailored facts the brain carries into every prompt: interests,
    # capabilities, device capacity — i.e. "who it's helping"
    try:
        import growth
        notes = []
        for r in growth.list_recent(kind="interest", limit=3):
            notes.append(r["text"])
        for r in growth.list_recent(kind="capability", limit=3):
            notes.append(r["text"])
        for r in growth.list_recent(kind="device", limit=2):
            notes.append(r["text"])
        if notes:
            bits.append("user: " + "; ".join(notes[:6]))
    except Exception:
        pass
    return " · ".join(bits) if bits else ""


_PENDING_CANCELS_FILE = Path.home() / "blackhorse" / "logs" / "pending_cancel_all.json"


def _pending_get() -> dict:
    try:
        return json.loads(_PENDING_CANCELS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _pending_set(d: dict):
    try:
        _PENDING_CANCELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PENDING_CANCELS_FILE.write_text(json.dumps(d))
    except OSError:
        pass


def _open_orders_by_pair(pair: str):
    """Fetch open Kraken orders and return [(txid, order_text)] for the given pair."""
    import time, urllib.request, urllib.parse, hmac, hashlib, base64, json
    from pathlib import Path as _P
    sys.path.insert(0, str(_P.home() / "blackhorse" / "trade"))
    import exchanges
    name = exchanges.active_exchange()["name"]
    creds = exchanges.require_credentials(name)
    key, secret = creds["api_key"], creds["private_key"]
    path = "/0/private/OpenOrders"
    nonce = str(int(time.time() * 1000))
    body = {"nonce": nonce}
    data = urllib.parse.urlencode(body).encode()
    sha = hashlib.sha256(nonce.encode() + data).digest()
    sig = hmac.new(base64.b64decode(secret), path.encode() + sha, hashlib.sha512)
    req = urllib.request.Request(f"https://api.kraken.com{path}", data=data, method="POST",
                                 headers={"API-Key": key,
                                          "API-Sign": base64.b64encode(sig.digest()).decode()})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if resp.get("error"):
        return None, f"kraken: {resp['error']}"
    up = pair.replace("/", "").upper()
    if up.startswith("BTC"):
        up = up.replace("BTC", "XBT", 1)
    out = []
    for txid, o in resp.get("result", {}).get("open", {}).items():
        p = (o.get("descr", {}).get("pair") or "").upper()
        if p == up:
            out.append((txid, o.get("descr", {}).get("order", "")))
    return out, None


def _route_intent(msg: str):
    """Turn natural requests into actions without bothering the LLM.
    Returns a reply string, or None when the LLM should answer."""
    import re, sys
    t = msg.strip().lower()
    t = re.sub(r"^(hey\s+)?spice[,:]?\s*", "", t)
    # pending "confirm yes" for a cancel-all
    if t in ("yes", "y", "confirm", "do it", "yeah", "go ahead"):
        pend = _pending_get()
        if pend:
            pair = next(iter(pend))
            txids = pend.pop(pair)
            _pending_set(pend)
            results = []
            for txid in txids:
                sys.path.insert(0, str(Path.home() / "blackhorse" / "trade"))
                import execute as _ex
                results.append(_ex.cancel(txid))
            return f"cancelled {len(txids)} open {pair} order(s): {results}"
    BH = Path.home() / "blackhorse"
    # status-ish
    if re.search(r"\bwhat.?s up\b|\bstatus\b|\bhow are we\b", t):
        sys.path.insert(0, str(BH / "trade"))
        import console
        return console.human_status()
    # fast: delete all agents except one
    dm = re.search(r"delete.*all.*agent.*except\s+(?:for\s+)?agent[_ ]?(\w+)", t)
    if dm:
        keep = dm.group(1)
        numwords = {"one":"1","two":"2","three":"3","four":"4","five":"5"}
        keep_id = keep if keep.startswith("agent_") else f"agent_{numwords.get(keep, re.sub(r'\D', '', keep))}"
        sys.path.insert(0, str(BH / "trade"))
        import agents as _ag
        deleted = []
        for a in list(_ag.list_agents()):
            if a["id"] != keep_id:
                try:
                    _ag.delete_agent(a["id"]); deleted.append(a["id"])
                except Exception:
                    pass
        return f"deleted {deleted}. Kept {keep_id}. Remaining: {[a['id'] for a in _ag.list_agents()]}"

    # fast: 'sweet buy of <intervals>' price resolution -> build & place a limit order
    if "sweet" in t and re.search(r"(order|buy|sell)", t):
        sys.path.insert(0, str(BH / "trade"))
        import execute as _ex
        side_m = re.search(r"\b(buy|sell)\b", t)
        if side_m:
            pair_m = re.search(r"(dot|btc|xbt|eth|DOT|BTC|ETH)\b", t)
            pair = pair_m.group(0) if pair_m else "DOT/USD"
            if "/" not in pair:
                pair = pair.upper() + "/USD"
            amt_m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t) or re.search(r"(\d+(?:\.\d+)?)\s*(?:usd|usdt)\b", t)
            amount = amt_m.group(1) if amt_m else None
            ivs = re.findall(r"(\d+)\s*m", t)  # require the 'm' suffix so '$15' isn't read as 15m
            intervals = sorted({x for x in ivs if x in ("1","5","15","60","240","1440")}) or ["240"]
            try:
                import subprocess as _sp, json as _j
                qf = Path.home()/"qubitFlowBeta"/"qubitFlowBeta"/"SignalBot"/"adaBot"
                kpair = pair.replace("/", "").upper()
                if kpair.startswith("BTC"): kpair = kpair.replace("BTC", "XBT", 1)
                out = _sp.run(["node","-e",
                    f"const {{runAdaBot}}=require('{qf}/adaBot.js');runAdaBot({{pair:'{kpair}',intervals:{intervals}}}).then(r=>process.stdout.write(JSON.stringify(r)))"],
                    cwd=str(qf), capture_output=True, text=True, timeout=120).stdout
                rr = _j.loads(out)
                price = rr.get("sweetBuy") if side_m.group(0) == "buy" else rr.get("sweetSell")
                if price is None:
                    price = rr.get("buyAvg") if side_m.group(0) == "buy" else rr.get("sellAvg")
                if price and amount:
                    manual = _ex.manual_order(f"create order {side_m.group(0)} {pair} limit {price} {amount}")
                    return f"{manual}\n(price from adaBot sweetBuy/sweetSell [{','.join(intervals)}] = {price})"
            except Exception as e:
                return f"sweet-price order failed: {e}"
    # general action routing: if the verb family looks like a request, ask the LLM
    # to emit a strict JSON command and we execute it. This is what keeps Spice
    # conversational — it understands, and only asks when truly ambiguous.
    if re.search(r"\b(order|orders|trade|buy|sell|place|cancel|create|agent|capital|amount|trailing|limit|market|pair|swap|stop)\b", t):
        try:
            parsed = _llm_intent(msg)
        except Exception:
            parsed = None
        if parsed and parsed.get("intent") not in (None, "chat", "status"):
            intent = parsed.get("intent")
            if parsed.get("missing"):
                return parsed.get("question") or ("I need: " + ", ".join(parsed["missing"]))
            sys.path.insert(0, str(BH / "trade"))
            import execute, agents
            if intent == "order":
                side = parsed.get("side") or "buy"
                pair = parsed.get("pair") or "DOT/USD"
                amt = parsed.get("amount_usd") or parsed.get("amount")
                typ = (parsed.get("order_type") or "market").lower()
                price = parsed.get("limit_price") or parsed.get("price")
                arg_str = f"create order {side} {pair} {typ} {price or amt} {amt or ''}".strip()
                return execute.manual_order(arg_str)
            if intent == "cancel" and parsed.get("txid"):
                return execute.cancel(parsed["txid"])
            if intent == "cancel_all" and parsed.get("pair"):
                import execute as _ex2
                open_orders, err = _open_orders_by_pair(parsed["pair"])
                if err: return err
                if not open_orders: return f"no open orders for {parsed['pair']} right now."
                if "yes" in t or "confirm" in t or "go ahead" in t or "do it" in t:
                    results = [ _ex2.cancel(txid) for txid, _ in open_orders ]
                    return f"cancelled {len(open_orders)} open {parsed['pair']} order(s): {results}"
                _pending_set({parsed["pair"]: [txid for txid, _ in open_orders]})
                lines = "\n".join(f"  {txid} — {ot}" for txid, ot in open_orders)
                return f"Found {len(open_orders)} open {parsed['pair']} order(s). Reply 'yes' to cancel them all:\n{lines}"
            if intent == "agent_capital" and parsed.get("agent_id") and parsed.get("capital_usd"):
                return str(agents.set_capital(parsed["agent_id"], float(parsed["capital_usd"])))
            if intent == "create_agent":
                kind = parsed.get("kind") or parsed.get("side") or "trading"
                prof = parsed.get("profile") or "slow"
                cap = parsed.get("capital_usd")
                if cap:
                    a = agents.create_agent(kind if kind in ("trading","youtube") else "trading", prof, capital=float(cap))
                    agents.set_enabled(a["id"], True)
                    return f"agent ready: {a['id']} ({a['profile']}, ${a['capital']}) — enabled"
                return ("I can spin up an agent. Need: kind (trading | youtube), profile (quick | slow), capital. "
                        "Say like:  create agent trading slow 100 usd")
        # fall through to the fast regex paths for exact forms when LLM is off/slow
    # agent capital change: "change agent three capital to $30" / "agent_3 capital 30"
    caps = re.search(r"agent[_ ]?(\w+)\s+capital\s*(?:to\s+)?\$?(\d+(?:\.\d+)?)", t)
    capital_reply = None
    if caps:
        sys.path.insert(0, str(BH / "trade"))
        import agents
        aidraw, amount_s = caps.group(1), caps.group(2)
        numwords = {"one":"1","two":"2","three":"3","four":"4","five":"5"}
        if aidraw in numwords: aidraw = numwords[aidraw]
        aid = f"agent_{aidraw}"
        amount = float(amount_s)
        try:
            res = agents.set_capital(aid, amount)
            capital_reply = f"agent {aid} capital -> ${res.get('capital', amount)}"
        except Exception as e:
            capital_reply = f"could not change {aid}: {e}"
    # cancel all orders on a pair
    if re.search(r"cancel.*(all|every).*(order)", t) or re.search(r"cancel.*order.*(all|every)", t):
        import re as _re2
        m = _re2.search(r"(?:on\s+)?([A-Za-z/.]+)\b(?:\s+for\b|\s+open\b|\s*$)?", t.split("cancel")[-1])
        pair = "DOT/USD"
        for tok in _re2.findall(r"[A-Za-z/]+", t):
            if tok.upper() in ("DOT","BTC","XBT","ETH","DOTUSD","XBTUSD","BTCUSD","ETHUSD"):
                t2 = tok.upper()
                pair = t2 if t2.endswith("USD") else f"{t2}/USD"
                break
        sys.path.insert(0, str(BH / "trade"))
        open_orders, err = _open_orders_by_pair(pair)
        if err:
            return err
        if not open_orders:
            return f"no open orders for {pair} right now."
        if "yes" in t or "confirm" in t or "go ahead" in t or "do it" in t:
            sys.path.insert(0, str(BH / "trade"))
            import execute as _ex3
            results = [_ex3.cancel(txid) for txid, _ in open_orders]
            return f"cancelled {len(open_orders)} open {pair} order(s): {results}"
        _pending_set({pair: [txid for txid, _ in open_orders]})
        lines = "\n".join(f"  {txid} — {ot}" for txid, ot in open_orders)
        return f"Found {len(open_orders)} open {pair} order(s). Reply 'yes' to cancel them all:\n{lines}"
        sys.path.insert(0, str(BH / "trade"))
        import execute
        manual = execute.manual_order(msg)
        if manual:
            return (f"{manual}" + (f"\n{capital_reply}" if capital_reply else ""))
        missing = []
        side_m = re.search(r"\b(buy|sell)\b", t)
        pair_m = re.search(r"\b(dot|dotusd|btc|xbt|eth|/)\b", t)
        type_m = re.search(r"\b(market|limit|trailing|stop|take.?profit)\b", t)
        amt_m = None
        if capital_reply:
            # don't let the agent-capital dollar amount bleed into the order size
            t_no_cap = re.sub(r"agent[_ ]?\w+\s+capital\s*(?:to\s+)?\$?\d+(?:\.\d+)?", " ", t)
            amt_m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t_no_cap) or re.search(r"(\d+(?:\.\d+)?)\s*(?:usd|usdt)\b", t_no_cap)
        if amt_m is None:
            amt_m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t) or re.search(r"(\d+(?:\.\d+)?)\s*(?:usd|usdt)\b", t)
        if not side_m: missing.append("side")
        if not pair_m: missing.append("pair")
        if not type_m: missing.append("order_type")
        if not amt_m: missing.append("amount_usd")
        # limit but no explicit price -> try adabot sweet/average buy/sell
        price = None
        if type_m and "limit" in t and not re.search(r"\d+\.\d+", t) and re.search(r"sweet|avg|adabot|interval", t):
            try:
                pair = pair_m.group(0).upper() if pair_m else "DOT"
                kpair = "DOTUSD" if pair.startswith("DOT") or pair in ("/", "DOTUSD") else (pair.replace("/","") + "USD")
                import subprocess as _sp, json as _j
                qf = Path.home()/"qubitFlowBeta"/"qubitFlowBeta"/"SignalBot"/"adaBot"
                out = _sp.run(["node","-e",
                    f"const {{runAdaBot}}=require('{qf}/adaBot.js');runAdaBot({{pair:'{kpair}',intervals:['60','240','1440']}}).then(r=>process.stdout.write(JSON.stringify(r)))"],
                    cwd=str(qf), capture_output=True, text=True, timeout=120).stdout
                r = _j.loads(out)
                price = r.get("sweetBuy") if side_m and side_m.group(0)=="buy" else r.get("sweetSell")
                if price is None: price = r.get("buyAvg") if side_m and side_m.group(0)=="buy" else r.get("sellAvg")
                price = float(price) if price else None
            except Exception:
                price = None
        if price is not None and side_m and pair_m and amt_m:
            side = side_m.group(0)
            raw_pair = pair_m.group(0) if (pair_m and pair_m.group(0) not in ("/", "")) else "DOT/USD"
            amount = amt_m.group(1)
            manual_str = f"create order {side} {raw_pair} limit {price} {amount}"
            manual = execute.manual_order(manual_str)
            return (f"{manual}\n(price source: adabot sweetBuy/sweetSell [{', '.join(['60','240','1440'])}])"
                    + (f"\n{capital_reply}" if capital_reply else ""))
        try:
            parsed = _llm_intent(msg)
            if parsed and parsed.get("intent") not in (None, "chat", "status"):
                if parsed.get("missing"):
                    return parsed.get("question") or ("I need: " + ", ".join(parsed["missing"]))
                if parsed.get("intent") == "order":
                    side = parsed.get("side"); pair = parsed.get("pair") or "DOT/USD"
                    amt = parsed.get("amount_usd"); price = parsed.get("limit_price")
                    typ = parsed.get("order_type") or "market"
                    manual = execute.manual_order(f"create order {side} {pair} {typ} {price or amt} {amt or ''}".strip())
                    return manual or ("I couldn't place it — missing fields.")
                if parsed.get("intent") == "cancel" and parsed.get("txid"):
                    return execute.cancel(parsed["txid"])
                if parsed.get("intent") == "agent_capital" and parsed.get("agent_id") and parsed.get("capital_usd"):
                    import agents
                    return str(agents.set_capital(parsed["agent_id"], float(parsed["capital_usd"])))
                if parsed.get("intent") == "create_agent":
                    import agents
                    k = parsed.get("side") or "trading"
                    prof = parsed.get("profile") or "slow"
                    cap = parsed.get("capital_usd")
                    if cap:
                        a = agents.create_agent(k, prof, capital=float(cap))
                        agents.set_enabled(a["id"], True)
                        return f"agent ready: {a['id']} ({a['profile']}, ${a['capital']}) — enabled"
        except Exception:
            pass
        if missing:
            return ("I can fire that. Missing: " + ", ".join(missing) +
                    (f"\n{capital_reply}" if capital_reply else "") +
                    "\nTip: say  create order buy DOT/USD limit 1.20 12  |  or tell me  sweet avg  for adabot's combined lanes.")
    if capital_reply:
        return capital_reply
    # agent
    if "agent" in t and any(w in t for w in ("create", "set up", "setup", "add", "new")):
        sys.path.insert(0, str(BH / "trade"))
        import agents
        if "enable" in t and re.search(r"agent_\d", t):
            return str(agents.set_enabled(re.search(r"agent_\d+", t).group(0), True))
        missing = []
        if not re.search(r"\b(trading|youtube)\b", t): missing.append("kind (trading | youtube)")
        if not re.search(r"\b(quick|slow)\b", t): missing.append("profile (quick | slow)")
        if not re.search(r"\$?\d+(\.\d+)?\s*(usd|\$)", t): missing.append("capital")
        if missing:
            return ("I can spin up an agent. Need: " + ", ".join(missing) +
                    ". Tell me like:  create agent trading slow 100 usd")
        m = re.search(r"(trading|youtube)\s+(quick|slow)\s+\$?(\d+(?:\.\d+)?)", t)
        if m:
            a = agents.create_agent(m.group(1), m.group(2), capital=float(m.group(3)))
            agents.set_enabled(a["id"], True)
            return f"agent ready: {a['id']} ({a['profile']}, ${a['capital']}) — enabled"
        return ("Say:  create agent <kind> <quick|slow> <usd>   e.g.  create agent trading slow 100")
    return None


def _llm_intent(msg: str):
    """Fallback: ask the LLM to turn a free-form trading request into a
    strict JSON command. Falls back to None on parse/timeout."""
    import subprocess as _sp, json as _j
    prompt = (
        "Extract a trading command from the user message. Reply ONLY with a JSON object, no prose.\n"
        'Format: {"intent": "order|agent_capital|create_agent|cancel|cancel_all|status|chat", '
        '"side": "buy|sell|null", "pair": "DOT/USD|...", "order_type": "market|limit|...|null", '
        '"amount_usd": <number>|null, "limit_price": <number>|null, "agent_id": "agent_N"|null, '
        '"kind": "trading|youtube|null", "profile": "quick|slow|null", "capital_usd": <number>|null, "txid": "..."|null, '
        '"missing": ["side", ...], "question": "one short question if fields are missing, else null"}\n\n'
        f"User: {msg}"
    )
    try:
        fast = _ask_via_api(SESSION, prompt, timeout=60)
        if fast is not None:
            txt = fast
        else:
            r = _sp.run([opencode_bin(), "run", prompt], cwd=BH, capture_output=True, text=True, timeout=60)
            txt = (r.stdout or "").strip()
        if txt.startswith("```"):
            txt = txt.strip('`').split('\n', 1)[-1] if '\n' in txt else txt.strip('`')
        start, end = txt.find('{'), txt.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        return _j.loads(txt[start:end])
    except Exception:
        return None


def trade_session() -> str | None:
    """The trading brain's OWN session, separate from the user's guardian chat.

    Kept in a file so the two conversations never collide. If the file is
    missing/invalid we return None — we NEVER fall back to the guardian
    session, or trade prompts would leak into the user's chat again."""
    try:
        s = TRADE_SESSION_FILE.read_text().strip()
        if s.startswith("ses_"):
            return s
    except OSError:
        pass
    return None


def set_trade_session(sid: str):
    if not str(sid).startswith("ses_"):
        raise ValueError("session id must start with 'ses_'")
    TRADE_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRADE_SESSION_FILE.write_text(str(sid).strip())

sys.path.insert(0, str(BH / "skin"))
import settings
import brain_budget

SELF_SOURCES = ("whitecell", "firewall", "brain", "mastermind")

HEALTH = BH / "logs" / "brain_health.json"
OUTBOX = BH / "logs" / "brain_outbox.jsonl"


def _record_health(ok: bool, err: str | None):
    try:
        d = _load_json(HEALTH, {})
        now = time.time()
        if ok:
            d.update(last_ok_ts=now, last_error_ts=d.get("last_error_ts"),
                     last_error=d.get("last_error"), consecutive_failures=0)
        else:
            d.update(last_error_ts=now, last_error=err,
                     consecutive_failures=int(d.get("consecutive_failures", 0)) + 1)
        HEALTH.parent.mkdir(parents=True, exist_ok=True)
        HEALTH.write_text(json.dumps(d, indent=2))
        if not ok and d.get("consecutive_failures", 0) >= 3:
            # escalate once: brain is repeatedly unreachable -> skin/user should know
            try:
                with INBOX.open("a") as f:
                    f.write(json.dumps({
                        "id": f"brain-down-{int(now)}", "ts": now,
                        "level": "CRITICAL", "source": "brain",
                        "message": f"brain unreachable {d['consecutive_failures']}x in a row: {err}",
                        "trace": {}}) + "\n")
            except OSError:
                pass
    except OSError:
        pass


def _outbox_add(msg: str, kind: str, session: str, err: str):
    try:
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "session": session,
                                "q": msg[:500], "err": err, "attempts": 1}) + "\n")
    except OSError:
        pass


def retry_outbox(max_per_sweep: int = 1) -> str:
    """Give failed brain asks one more shot when the budget allows.
    Items past 3 attempts are dropped (kept in the file for audit)."""
    if not OUTBOX.exists():
        return "(outbox empty)"
    lines = OUTBOX.read_text(errors="ignore").splitlines()[-50:]
    done = 0
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("attempts", 0) >= 3 or e.get("done"):
            continue
        ok, why = brain_budget.allow()
        if not ok:
            return f"(outbox deferred: {why})"
        sess = e.get("session") or None
        if sess is None and e.get("kind") in ("trade", "console"):
            sess = trade_session()
        ans = ask(e["q"], force=True, session=sess, kind=e.get("kind", "chat"))
        e["attempts"] = e.get("attempts", 0) + 1
        e["done"] = not (ans.startswith("(brain error") or ans.startswith("(brain offline"))
        e["last"] = ans[:200]
        # rewrite that line in place (small file)
        try:
            all_lines = OUTBOX.read_text(errors="ignore").splitlines()
            for i in range(len(all_lines) - 1, -1, -1):
                try:
                    cur = json.loads(all_lines[i])
                except json.JSONDecodeError:
                    continue
                if cur.get("ts") == e.get("ts") and cur.get("q") == e.get("q"):
                    all_lines[i] = json.dumps(e)
                    break
            OUTBOX.write_text("\n".join(all_lines) + "\n")
        except OSError:
            pass
        done += 1
        if done >= max_per_sweep:
            break
    return f"retried {done}"



_NET_CACHE = {"ts": 0.0, "ok": None}

def _online(max_age: float = 30.0) -> bool:
    """Cheap connectivity gate, cached ~30s. When False, callers must skip
    LLM work instead of spawning opencode and hanging to the timeout."""
    now = time.time()
    if _NET_CACHE["ok"] is not None and now - _NET_CACHE["ts"] < max_age:
        return _NET_CACHE["ok"]
    try:
        import socket
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            ok = True
    except OSError:
        ok = False
    _NET_CACHE["ts"] = now
    _NET_CACHE["ok"] = ok
    return ok


def _ask_via_api(session: str, full: str, timeout: int = 180) -> str | None:
    """Fast path: talk to the running opencode server over HTTP (~0.4s per call)
    instead of spawning the opencode CLI for every ask."""
    import subprocess as _sp, json as _j, time as _t
    try:
        post = _sp.run(
            [opencode_bin(), "api", "POST", f"/api/session/{session}/prompt",
             "-d", _j.dumps({"text": full})],
            capture_output=True, text=True, timeout=30, cwd=BH)
        if post.returncode != 0:
            return None
        msg = _j.loads(post.stdout)["data"]
        created_after = msg.get("time", {}).get("created", 0)
    except Exception:
        return None
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        try:
            g = _sp.run(
                [opencode_bin(), "api", "GET", f"/api/session/{session}/message"],
                capture_output=True, text=True, timeout=15, cwd=BH)
            items = _j.loads(g.stdout).get("data", [])
            for m in items:
                if m.get("type") != "assistant":
                    continue
                if (m.get("time", {}).get("created", 0) or 0) <= created_after:
                    continue
                content = m.get("content") or m.get("payload", {}).get("content") or []
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                            return part["text"].strip().splitlines()[-1]
                if m.get("finish") and m.get("finish") != "tool-calls":
                    break
                break
        except Exception:
            pass
        _t.sleep(1.0)
    return None


def opencode_bin() -> str:
    """Resolve opencode even when the service has a minimal PATH."""
    return shutil.which("opencode") or str(Path.home() / ".opencode" / "bin" / "opencode")


def brain_enabled() -> bool:
    s = settings.load()
    return bool(s.get("master_enabled", True)) and bool(s.get("brain_enabled", True))


def _log(q: str, a: str, kind: str = "chat", session: str | None = None):
    try:
        with OUT.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "session": session,
                                "q": q[:200], "a": str(a)[:2000]}) + "\n")
    except OSError:
        pass


def ask(msg: str, force: bool = False, session: str | None = None, kind: str = "chat",
        route: bool = True) -> str:
    """Send one prompt to the opencode brain session and log the answer.

    force=True skips the budget check (used for user-initiated asks and for
    sweep(), which already reserved a slot).
    session= overrides the session (e.g. coach passes its own trade session).
    kind= tags the log entry so trade prompts don't show in the user's chat.
    route=False skips the hardcoded intent router (Spice does its own JARVIS
    routing and must not be hijacked by it).
    """
    # Keep the shared session from going stale: attach a tiny live-state
    # preface to every user message so the LLM does not quote last week's
    # gates. The user-visible reply is still just the answer; this preface
    # is context only.
    try:
        preface = _live_brief()
        full = f"[current system state: {preface}]\n{msg}" if preface else msg
    except Exception:
        full = msg

    # conversational intent layer: route order/agent/status asks before
    # spending a brain call, so Spice talks like a trading desk, not a shell.
    if route:
        try:
            routed = _route_intent(msg)
        except Exception:
            routed = None
        if routed is not None:
            _log(msg, routed, kind=kind, session=session or SESSION)
            return routed

    if not brain_enabled():
        return "(brain disabled)"
    if not force:
        ok, why = brain_budget.allow()
        if not ok:
            return f"(brain deferred: {why})"
    if session is None and kind in ("trade", "console"):
        return "(brain session missing — trade/console needs its own provisioned session; refusing to leak into guardian chat)"
    sess = session or SESSION
    if not _online():
        answer = "(brain offline — no internet; skipped this cycle, will retry when back online)"
        _record_health(False, "offline")
        _outbox_add(msg, kind, sess, "offline")
        _log(msg, answer, kind=kind, session=sess)
        return answer
    timeout = int(settings.load().get("brain_timeout_seconds", 180))
    try:
        retry_s = int(settings.load().get("brain_retry_seconds", 20))
    except Exception:
        retry_s = 20
    answer = None
    last_err = None
    for attempt in (1, 2):
        try:
            ans = _ask_via_api(sess, full, timeout=timeout)
            if ans is None:
                r = subprocess.run(
                    [opencode_bin(), "run", "--session", sess, full],
                    cwd=BH, capture_output=True, text=True, timeout=timeout)
                ans = (r.stdout.strip().splitlines()[-1] if r.stdout.strip()
                       else (r.stderr.strip()[:200] or "(no output)"))
            if ans.startswith("(brain error") or ans == "(no output)":
                raise RuntimeError(ans)
            answer = ans
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt == 1 and retry_s > 0:
                time.sleep(retry_s)
    if last_err is not None:
        answer = f"(brain error: {last_err})"
        _record_health(False, str(last_err)[:200])
        _outbox_add(msg, kind, sess, str(last_err)[:200])
    else:
        _record_health(True, None)
    _log(msg, answer, kind=kind, session=sess)
    return answer


# --- review bookkeeping -------------------------------------------------------

def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def pending_escalations() -> list:
    """Threats a whitecell could not resolve with rules, oldest first."""
    if not INBOX.exists():
        return []
    handled = set(_load_json(REVIEWED, {}).get("escalations", []))
    out = []
    for line in INBOX.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("id") and e["id"] not in handled:
            out.append(e)
    return out


def new_threats() -> list:
    """Unreviewed ALERT/CRITICAL events the skin logged (not our own output)."""
    reviewed = set(_load_json(REVIEWED, {}).get("threats", []))
    out = []
    if THREATS.exists():
        for line in THREATS.read_text(errors="ignore").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (e.get("level") in ("ALERT", "CRITICAL")
                    and e.get("source") not in SELF_SOURCES
                    and e.get("hash") and e["hash"] not in reviewed):
                out.append(e)
    return out


def _mark_handled(escalations: list, threats: list):
    data = _load_json(REVIEWED, {})
    esc = set(data.get("escalations", []))
    thr = set(data.get("threats", []))
    for e in escalations:
        esc.add(e["id"])
    for t in threats:
        thr.add(t["hash"])
    REVIEWED.parent.mkdir(parents=True, exist_ok=True)
    REVIEWED.write_text(json.dumps({"escalations": sorted(esc), "threats": sorted(thr)[-2000:]}))


def sweep() -> str:
    """One periodic brain pass, subject to the shared budget."""
    if not brain_enabled():
        return "(brain disabled)"
    esc = pending_escalations()
    threats = new_threats()
    if not esc and not threats:
        return "(nothing to review)"

    if not esc:
        # ALERT-only batch: local tier already adjudicated — the brain is not
        # needed, and it must not burn a budget slot. CRITICALs still go up.
        crits = [t for t in threats if t.get("level") == "CRITICAL" or t.get("source") == "net_check"]
        if not crits:
            _mark_handled([], threats)
            return "(ALERT-only batch — local tier handled, brain skipped)"
    ok, why = brain_budget.allow()
    if not ok:
        return f"(brain deferred: {why})"

    if esc:
        e = esc[0]
        prompt = (
            "WHITECELL ESCALATION — a skin whitecell could not confidently resolve this "
            "with its rules and is asking the brain for a verdict. "
            f"Threat: {e.get('message', '')}\n"
            f"Trace: {json.dumps(e.get('trace', {}))[:800]}\n"
            "Reply with a one-line verdict (KILL/BLOCK/LOCKDOWN/MONITOR/IGNORE), a "
            "confidence 0-1, and a one-line reason.")
        handled_esc, handled_thr = esc[:1], []
    else:
        # Free the brain from ALERT-only noise: the local rule tier already
        # adjudicated every ALERT. The expensive tier only weighs in on
        # CRITICAL events. ALERT-only ticks are marked handled locally.
        crits = [t for t in threats if t.get("level") == "CRITICAL" or t.get("source") == "net_check"]
        if not crits:
            _mark_handled([], threats)
            return "(ALERT-only batch — local tier handled, brain skipped)"
        lines = [f"- [{t.get('ts')}] {t.get('source')}: {t.get('message')}" for t in crits[-20:]]
        prompt = (
            "BLACKHORSE PERIODIC SECURITY SWEEP. You are the brain; the skin reports the "
            "new CRITICAL + network-drop events below (other ALERTs are handled locally). Are any of these REAL "
            "threats needing action, or is this expected local activity? Reply concisely.\n" + "\n".join(lines))
        handled_esc, handled_thr = [], crits

    answer = ask(prompt, force=True)  # slot already reserved above
    _mark_handled(handled_esc, handled_thr)
    return answer


def daemon():
    interval = int(settings.load().get("brain_sweep_seconds", 300))
    print(f"brain awake — sweep every {interval}s, session {SESSION}")
    while True:
        try:
            res = sweep()
            try:
                print(retry_outbox())
            except Exception as e:
                print("outbox tick error:", e)
            STATE.write_text(json.dumps({"ts": time.time(), "session": SESSION, "last": str(res)[:200]}))
            print(res)
        except Exception as e:
            print("brain tick error:", e)
        time.sleep(max(30, interval))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if mode == "daemon":
        daemon()
    elif mode == "sweep":
        print(sweep())
    elif mode == "status":
        print(json.dumps(brain_budget.status()))
    elif mode == "session":
        print(SESSION)
    else:
        print(ask(" ".join(sys.argv[2:]) or sys.stdin.read(), force=True))
