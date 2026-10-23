"""Agents page for the BlackHorse desktop app (mixin for BlackHorse).

Left: agent list. Right: tabs per agent —
  Overview  pretty summary + last decision
  Settings  labelled inputs grouped (where it trades / entry / exit), two columns
  Orders    working limit orders, open positions with live bid/net + exit detail,
            recent exchange orders (DB), closed trades
  Decisions why it is (or isn't) entering — the gate checklist the trader produced,
            the decisions it is making, where the market is (SignalBot read), and
            the bot's own six scenarios marked with which one each interval sits in now
Everything on the right scrolls instead of squishing, and the split is draggable.
"""
import json, time, sqlite3, html as _html
from pathlib import Path

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
                             QPushButton, QListWidget, QListWidgetItem, QTabWidget, QTableWidget,
                             QTableWidgetItem, QHeaderView, QSplitter, QGroupBox, QScrollArea,
                             QComboBox, QTextBrowser, QTextEdit, QAbstractItemView, QFrame,
                             QCheckBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

from ui_util import keep_scroll, set_text_keep_scroll, set_html_keep_scroll

BH = Path.home() / "blackhorse"
GREEN, RED, GREY, AMBER = "#16a34a", "#dc2626", "#9ca3af", "#d97706"


INTV = ["5m", "15m", "60m", "240m", "1440m"]
_IVMIN = {"5m": 5, "15m": 15, "60m": 60, "240m": 240, "1440m": 1440}


def _ivmin(iv):
    return _IVMIN.get(str(iv), 10 ** 9)


# ------------------------------------------------------------------ data
def _jload(p, d):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return d


def _db(sql, args=()):
    try:
        con = sqlite3.connect(f"file:{BH / 'logs' / 'trading.db'}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
        con.close()
        return rows
    except Exception:
        return []


def load_agent_orders(agent_id: str) -> dict:
    port = _jload(BH / "logs" / "portfolio.json", {})
    watch = (_jload(BH / "logs" / "exit_watch.json", {}) or {})
    pend = [p for p in _jload(BH / "logs" / "pending_orders.json", []) if p.get("agent") == agent_id]
    pos = [p for p in port.get("positions", []) if p.get("agent") == agent_id]
    orders = _db("SELECT created, pair, side, order_type, price, amount_base vol, vol_exec, avg_price, "
                 "fee, status, exchange_order_id txid FROM orders WHERE agent=? ORDER BY created DESC LIMIT 15",
                 (agent_id,))
    closed = _db("SELECT opened, closed, pair, entry, exit, qty, net, fees, reason FROM positions "
                 "WHERE agent=? AND status='CLOSED' ORDER BY closed DESC LIMIT 10", (agent_id,))
    last = _db("SELECT ts, action, reason FROM decisions WHERE agent=? ORDER BY ts DESC LIMIT 1", (agent_id,))
    net_total = sum(float(c.get("net") or 0) for c in
                    _db("SELECT net FROM positions WHERE agent=? AND status='CLOSED'", (agent_id,)))
    return {"positions": pos, "pending": pend, "orders": orders, "closed": closed,
            "last_decision": last[0] if last else None, "watch": watch, "net_total": net_total}


def _ago(ts):
    try:
        d = int(time.time() - float(ts))
    except (TypeError, ValueError):
        return "?"
    if d < 0:
        return "soon"
    if d < 90:
        return f"{d}s ago"
    if d < 5400:
        return f"{d // 60}m ago"
    if d < 172800:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def _px(v, dp=4):
    try:
        return f"{float(v):.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _usd(v):
    try:
        v = float(v)
        return f"{'+' if v > 0 else ''}${v:,.4f}" if abs(v) < 1 else f"{'+' if v > 0 else ''}${v:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _table(headers, min_h=110):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.setMinimumHeight(54)
    t.setWordWrap(False)
    t._max_rows = max(3, min_h // 28)
    return t


def _fill(t, rows, colors=None):
    # rebuilt on the 5s orders tick — hold the reader's scroll, don't reset it
    with keep_scroll(t):
        t.setRowCount(len(rows))
        n = min(max(len(rows), 1), getattr(t, "_max_rows", 6))
        t.setFixedHeight(t.horizontalHeader().height() + n * 26 + 10)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                it = QTableWidgetItem(str(val))
                col = (colors or {}).get((r, c))
                if col:
                    it.setForeground(QBrush(QColor(col)))
                t.setItem(r, c, it)
        t.resizeColumnsToContents()


def _box(title, widget):
    g = QGroupBox(title)
    l = QVBoxLayout(g)
    l.addWidget(widget)
    return g


# ------------------------------------------------------------------ settings schema
# (key, label, kind, where, tip)
SETTINGS = [
    ("Safety — edge proof (user-controlled, default OFF)", [
        ("require_proven_edge", "Require edge proof to trade", "bool", "policy",
         "ON = refuse any interval+side the measured fee-honest edge report hasn't PROVEN. "
         "OFF = trade the setup anyway — your risk. Default OFF for every agent."),
    ]),
    ("Strength climb gate", [
        ("star_alignment", "Require entry lane eligible (star alignment)", "bool", "policy",
         "OFF (default) = the climb only checks that each rung's strength sits in its zone. "
         "ON = an extra gate: the ENTRY lane's own signal must actually be entryEligible to buy / "
         "exitEligible to sell, while the rungs only need to be in their zone. Ignored when the "
         "climb is off (no rungs listed)."),
    ]),
    ("Rungs only (bypass other gates)", [
        ("rungs_only", "Rungs only — bypass all other signal gates", "bool", "policy",
         "OFF (default) = normal: entry band/eligibility, star alignment, brain/HTF, proven edge, "
         "spread/MA and R:R gates all apply, and every rung must agree. ON = the climb rungs are "
         "the ONLY signal: entry band/eligibility, star alignment, brain stance/risk, MTF, HTF, "
         "proven edge, spread/MA and R:R gates are bypassed. Heart's hard risk caps, spot-only "
         "sells, sizing and stop/target mechanics still apply. Requires at least one climb rung."),
        ("rung_require_all", "Rung agreement required", "combo:ALL,ANY", "policy",
         "Shown when Rungs only is ON. ALL = every rung must agree (default). ANY = one rung "
         "agreeing is enough — no waiting for every rung."),
    ]),
    ("Where it trades", [
        ("pairs", "Pairs", "list", "agent", "Comma list, e.g. DOT/USD,ETH/USD. Empty = every pair."),
        ("mode", "Mode", "combo:PAPER,LIVE", "agent",
         "PAPER = this agent never places a live order (simulated fills only). "
         "LIVE = it sends real orders to the venue. Per agent, independent of the others."),
        ("strategy", "Strategy", "combo:swing,daytrade,custom", "policy", "Interval combo (editable). swing/daytrade are examples."),
        ("intervals", "Intervals", "list", "policy", "SMALLEST = the entry (trading) lane. "
         "Every OTHER interval listed becomes a strength-chain rung that must sit in its own "
         "range before this agent may buy/sell. Listing an interval turns its rung ON; removing "
         "it turns it OFF. e.g. 15m,60m,240m — dynamic: any interval you list becomes a rung "
         "with its own editable buy/sell range."),
        ("capital", "Capital $", "float", "agent", "Budget this agent may size from (bounded by real free cash)."),
    ]),
    ("Entry (buy)", [
        ("entry_strength_max", "Entry buy range top (≤)", "float", "policy",
         "Entry lane buys when its strength is inside [0, this]. 0 = floor only, 1 = whole range."),
        ("entry_order_type", "Order type", "combo:limit,market", "policy", "limit = rests at the entry price (maker fee)."),
        ("entry_time_in_force", "Time in force", "combo:GTC,IOC,GTD,FOK", "policy",
         "How long the order lives (separate from order type). GTC = rest until cancelled. "
         "IOC = fill now or cancel the rest. GTD = rest until the expiry below. "
         "FOK = fill fully right now or kill (needs a limit price)."),
        ("entry_expiry_seconds", "Limit expires (s)", "int", "policy", "Unfilled limit orders are cancelled after this."),
        ("min_reward_risk", "Min R:R", "float", "policy", "Target distance vs invalidation distance."),
        ("risk_per_trade_pct", "Risk % of capital", "float", "policy", "Used for sizing (fees included)."),
        ("max_position_pct", "Max position %", "float", "policy", "Largest single position as % of capital."),
        ("stop_max_pct", "Sizing stop % ≤", "float", "policy",
         "Caps how far the structural stop sits from entry. Used only if you enable stop loss."),
    ]),
    ("Exit (sell) — profit exit", [
        ("exit_strength_min", "Entry sell range floor (≥)", "float", "policy",
         "Entry lane sells when its strength is inside [this, 1]."),
        ("exit_min_net_pct", "Min net profit %", "float", "policy", "After entry fee paid + taker exit fee."),
        ("exit_price_sets", "Stored price sets", "list", "policy", "average, perfected, median, sweet."),
    ]),
    ("Stop loss (opt-in — can sell at a loss)", [
        ("stop_loss_enabled", "Enable stop loss", "bool", "policy",
         "OFF = the house rule: never sell at a loss, only wait for a profitable exit. "
         "ON = arm a protective stop (a maker limit, never a market dump)."),
    ]),
    ("Stop loss — settings (used when enabled)", [
        ("stop_mode", "Stop mode", "combo:price_watch,trailing", "policy",
         "price_watch = rest a limit when price nears the stop level. "
         "trailing = the level rides up with price (distance below the high)."),
        ("stop_loss_pct", "Flat stop % (blank = structural)", "float", "policy",
         "Optional flat loss %. Overrides the structural stop when set; blank keeps the "
         "decider's structural pos[stop]."),
        ("trail_pct", "Trail distance % (of entry)", "float", "policy",
         "Trailing mode: how far below the high the stop rides, as % of entry. "
         "Blank = use the initial stop distance. Never looser than the structural stop."),
        ("watch_pct", "Watch band % (of stop)", "float", "policy",
         "Arm the resting limit when price comes within this % of the stop level. Default 0.5."),
    ]),
    ("Advanced — spread & MA cross (gate interval driven)", [
        ("ma_gate_interval", "MA/spread gate interval", "combo:5m,15m,60m,240m,1440m", "policy",
         "WHICH interval feeds the spread verdict and the MA cross gates. Default 15m. "
         "Both gates read THIS interval's own lane/candles — the entry lane and the climb "
         "rungs never feed them. Pick 15m for the fast tape, 60m/240m for the slower read. "
         "An interval with no lane in the snapshot falls back to the entry lane's spread "
         "verdict and pulls its MAs live from the venue."),
        ("require_spread_agreement", "Spread verdict gate", "bool", "policy",
         "OFF (default) = no spread filtering — exactly the old behavior. ON = block BUY only "
         "when the gate interval's own spread verdict is genuinely weakening, and SELL only "
         "when genuinely improving. Chop / noise / weak drift never block. Read from the "
         "MA/spread gate interval above."),
        ("require_ma_cross_agreement", "MA cross gate", "bool", "policy",
         "OFF (default) = no MA filtering. ON = block BUY while the gate interval's fast MA sits "
         "below the slow (bearish) and SELL while above (bullish) — gaps under the coil "
         "exemption never block. Read from the MA/spread gate interval above."),
        ("ma_cross_fast", "MA fast period", "int", "policy",
         "Fast MA length on the gate interval. Default 20 (= SignalBot's ma20, free). Any other "
         "value is computed locally from the gate interval's own candles."),
        ("ma_cross_slow", "MA slow period", "int", "policy",
         "Slow MA length on the gate interval. Default 50 (= SignalBot's ma50). Any other value "
         "is computed locally from the gate interval's own candles."),
        ("ma_cross_min_gap_pct", "MA coil exemption (%)", "float", "policy",
         "Gaps smaller than this count as neutral (a coil, not a bias) and never block. "
         "Default 0.15."),
        ("spread_ma_fast", "Spread fast period (MA10 context)", "int", "policy",
         "Fast MA length for the spread analysis context. Default 10 (separate from MA cross)."),
        ("spread_ma_slow", "Spread slow period (MA30 context)", "int", "policy",
         "Slow MA length for the spread analysis context. Default 30 (separate from MA cross)."),
    ]),
]


# ------------------------------------------------------------------ decisions tab
# The bot's own six-scenario rubric, verbatim — the same lines the SignalBot
# itself applied (they lived in app/main.py's scenario panel and are kept alive
# in Mr.Fluffy's wiregate). The detector below is a faithful port of
# trade/signalbot.py::_setup_scenario: the SAME rubric, read live from the bot's
# own written channel fields. Labels only — it never gates and never trades.
SCENARIO_RUBRIC = [
    {"n": "1", "title": "RSI washout, price holds above low",
     "why": "RSI oversold but the score gate is still failing — wait for buyScore ≥ 0.60 to hold; "
            "that's the knife having stopped falling."},
    {"n": "2", "title": "RSI ≤ 35 but spread still weakening",
     "why": "RSI is oversold but the spread is still weakening — a falling knife, the gate is right "
            "to refuse. Wait for the spread to stop falling."},
    {"n": "3", "title": "SpreadMA flips bullish, trend turns up, price above low",
     "why": "The green light — spreadMA bullish + trend up. If it drifted off, patient again."},
    {"n": "4", "title": "Price slices through low/floor",
     "why": "The floor broke and the range invalidated — a floor that won't hold isn't a floor. "
            "No buy, no edge."},
    {"n": "5", "title": "15m strength ≈ 0 while 240m strength rising",
     "why": "15m strength is asleep at ~0 — zero isn't a buy signal, it's the tape holding its "
            "breath. Wait for 15m rising off 0 + RSI ≤ 35 before any fill."},
    {"n": "6", "title": "60m/240m scores nearly threshold-broken, 15m weak",
     "why": "Wait for the 15m up-tick — the higher lanes are nearly there, but the trigger lane "
            "stays 15m."},
]


def _num(v, dp=4):
    try:
        return f"{float(v):.{dp}f}"
    except (TypeError, ValueError):
        return "?"


def _iso_ago(ts):
    """ISO ts ('2026-10-14T22:35:48Z') -> '3m ago'. For the bot snapshot's ts."""
    import calendar
    try:
        ep = calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
        return _ago(ep)
    except Exception:
        return "?"


def _scenario_label(c):
    """Which of the six the bot's own fields currently sit in (label only).
    Faithful port of trade/signalbot.py::_setup_scenario."""
    try:
        r = c.get("RSI") or {}
        mr = c.get("marketResult") or {}
        sm = c.get("spreadMA") or {}
        price = c.get("currPrice"); low = c.get("low")
        buy_score = c.get("buyScore") or 0
        rsi = r.get("currentRSI"); rsi = 99 if rsi is None else rsi
        against = str(mr.get("spreadDirection") or "").lower() in ("weakening", "down")
        if price is not None and low is not None and price < low:
            return "4"
        if sm.get("signal") == "bullish" and mr.get("trendState") in ("up", "bull", "rising") \
                and price and low and price >= low:
            return "3"
        if rsi <= 35 and not against and buy_score >= 0.60:
            return "1"
        if rsi <= 35 and against:
            return "2"
        strg = c.get("strength") or 0
        if strg < 0.10 and buy_score >= 0.85:
            return "5/6"
        if (c.get("rangeSignal") or "") == "BUY" and strg < 0.15:
            return "5"
        return "none"
    except Exception:
        pass
    sc = str(c.get("setupScenario") or "none")
    return sc.split(":", 1)[0].strip() or "none"


def load_agent_decisions(agent_id, limit=25):
    """This agent's decisions, newest first, with the detail JSON unpacked
    (explain = the gate checklist the trader produced; skipped; order_type)."""
    rows = _db("SELECT ts, source, action, pair, channel, entry, stop, target, size_quote, rr, reason, detail "
               "FROM decisions WHERE agent=? ORDER BY ts DESC LIMIT ?", (agent_id, limit))
    out = []
    for r in rows:
        d = dict(r)
        try:
            det = json.loads(d.get("detail") or "{}")
        except (TypeError, ValueError):
            det = {}
        if not isinstance(det, dict):
            det = {}
        d["explain"] = det.get("explain") or []
        d["skipped"] = det.get("skipped")
        d["order_type"] = det.get("order_type")
        d["outlay_quote"] = det.get("outlay_quote")
        d["interval"] = d.get("interval") or det.get("interval")
        d["chain"] = det.get("chain") or []
        d["htf_ctx"] = det.get("htf_ctx")
        out.append(d)
    return out


def load_signal(pair=None):
    """The bot's written snapshot for a pair (logs/signals/<PAIR>.json), else the
    primary snapshot (logs/signal_latest.json). Never runs the bot."""
    if pair:
        s = _jload(BH / "logs" / "signals" / (str(pair).replace("/", "-").upper() + ".json"), {})
        if s:
            return s
    return _jload(BH / "logs" / "signal_latest.json", {})


def _signal_channels(sig):
    """Ordered, interval-deduped channels from a snapshot (shortest interval first)."""
    chans = (sig or {}).get("channels") or {}
    def _rank(item):
        _n, c = item
        try:
            s = str((c or {}).get("interval") or "")
            return int(s[:-1]) if s.endswith("m") and s[:-1].isdigit() else 10 ** 9
        except Exception:
            return 10 ** 9
    seen, out = set(), []
    for name, c in sorted(chans.items(), key=_rank):
        c = c or {}
        iv = str(c.get("interval") or name)
        if iv in seen:
            continue
        seen.add(iv)
        out.append((name, iv, c))
    return out


def _why_html(d):
    if not d:
        return (f"<i style='color:{GREY}'>No decision recorded yet — the trader hasn't run its "
                f"cycle, or this agent is disabled.</i>")
    act = str(d.get("action") or "")
    col = {"BUY": GREEN, "SELL": RED}.get(act, GREY)
    where = f"{d.get('pair') or ''}" + (f" · {d.get('channel')}" if d.get("channel") else "")
    out = [f"<p><b style='color:{col};font-size:14px'>{act}</b> "
           f"<span style='color:{GREY}'>· {d.get('source') or ''} · {_ago(d.get('ts'))} · {where}</span></p>"]
    if d.get("reason"):
        out.append(f"<p>Reason: <b>{d['reason']}</b></p>")
    ex = d.get("explain") or []
    if ex:
        out.append("<p style='margin-bottom:2px'>The gate checklist it produced:</p><ul>")
        out += [f"<li>{x}</li>" for x in ex]
        out.append("</ul>")
    extra = []
    if d.get("skipped"):
        extra.append(f"skipped by <b>{d['skipped']}</b>")
    if d.get("order_type"):
        extra.append(f"order type {d['order_type']}")
    if d.get("outlay_quote") is not None:
        extra.append(f"outlay ≈ ${_num(d['outlay_quote'], 2)}")
    if extra:
        out.append(f"<p style='color:{GREY}'>{' · '.join(extra)}</p>")
    lvls = []
    if d.get("entry") not in (None, "", 0):
        lvls.append(f"entry {_px(d['entry'])}")
    if d.get("stop") not in (None, "", 0):
        lvls.append(f"stop {_px(d['stop'])}")
    if d.get("target") not in (None, "", 0):
        lvls.append(f"target {_px(d['target'])}")
    if d.get("rr") not in (None, "", 0):
        lvls.append(f"R:R {_num(d['rr'], 2)}")
    if d.get("size_quote") not in (None, "", 0):
        lvls.append(f"size {_num(d['size_quote'], 4)}")
    if lvls:
        out.append(f"<p style='color:{GREY}'>planned: {' · '.join(lvls)}</p>")
    if act == "HOLD":
        out.append(f"<p style='color:{GREY};font-size:11px'>A HOLD means it looked and passed — the "
                   f"checklist above is why. It re-checks every cycle (≤5 min).</p>")
    return "".join(out)


def _market_html(sig):
    """SignalBot's per-interval read as an HTML table, one column per lane.

    Same treatment as the Trade page's manual preview: strength, trend, the
    entry gate, range map and spread laid out side by side so the lanes are
    read across instead of decoded line by line.
    """
    if not sig or not sig.get("channels"):
        return (f"<i style='color:{GREY}'>No SignalBot snapshot on disk yet — run SignalBot "
                f"(Settings page) or wait for the loop's next scan.</i>")
    MUTED, VAL, HEAD, BORDER, HEAD_BG = "#94a3b8", "#e5e7eb", "#a5b4fc", "#1e293b", "#0d1526"
    ACT = {"BUY": "#22c55e", "SELL": "#ef4444", "HOLD": "#f59e0b"}

    def esc(v):
        return _html.escape(str(v)) if v not in (None, "") else "—"

    def td(inner, color=None, align="left", bg=None, top=True):
        style = "padding:4px 10px;"
        if top:
            style += "border-top:1px solid %s;" % BORDER
        if bg:
            style += "background-color:%s;" % bg
        if color:
            style += "color:%s;" % color
        return '<td style="%s" align="%s">%s</td>' % (style, align, inner)

    def mark(ok):
        return '<span style="color:%s">%s</span>' % ("#22c55e" if ok else "#ef4444", "✓" if ok else "✗")

    lanes = _signal_channels(sig)

    def dir_cell(c):
        d = c.get("strengthDirection")
        arrow, col = {"rising": ("▲", "#22c55e"), "falling": ("▼", "#f59e0b")}.get(d, ("→", MUTED))
        return "<b>%s</b> <span style='color:%s'>%s %s</span>" % (
            _num(c.get("strength"), 3), col, arrow, esc(d))

    def gate_cell(c):
        g = c.get("entryGate") or {}
        badge = ("<b style='color:#22c55e'>READY</b>" if g.get("allPass")
                 else "<span style='color:%s'>not yet</span>" % MUTED)
        return "rsi %s · spread %s · score %s · entry %s · %s" % (
            mark(g.get("rsiOk")), mark(g.get("spreadNotAgainst")), mark(g.get("scoreOk")),
            mark(bool(g.get("entryEligible"))), badge)

    def range_cell(c):
        return "floor %s · low %s · high %s · roof %s" % (
            _px(c.get("floor")), _px(c.get("low")), _px(c.get("high")), _px(c.get("roof")))

    def spread_cell(c):
        mr = c.get("marketResult") or {}
        col = {"improving": "#22c55e", "weakening": "#ef4444"}.get(str(mr.get("spreadDirection")), MUTED)
        out = "<span style='color:%s'>%s</span>" % (col, esc(mr.get("spreadDirection")))
        chg = mr.get("spreadChange")
        if chg is not None:
            out += " <span style='color:%s'>· %+.3f/%s</span>" % (MUTED, chg, mr.get("spreadCount"))
        return out

    def ma_cell(c):
        sm = c.get("spreadMA") or {}
        if not sm.get("available"):
            return "<span style='color:%s'>—</span>" % MUTED
        s = str(sm.get("signal") or "")
        col = "#22c55e" if s.startswith("bull") else ("#ef4444" if s.startswith("bear") else MUTED)
        return "%s <span style='color:%s'>(%s%%)</span> <span style='color:%s'>%s</span>" % (
            _num(sm.get("diff"), 4), MUTED, _num(sm.get("diffPct"), 3), col, esc(sm.get("signal")))

    def bias_cell(c):
        rs, rb = c.get("rangeSignal"), c.get("rangeBias")
        return "<span style='color:%s'>%s</span> <span style='color:%s'>· %s</span>" % (
            ACT.get(str(rs), MUTED), esc(rs), MUTED, esc(rb))

    head = "<tr>" + td("<b>%s</b><br><span style='color:%s'>the bot's own read</span>" % (
        esc(sig.get("pair")), MUTED), color=HEAD, bg=HEAD_BG, top=False)
    for name, iv, c in lanes:
        act = str(c.get("action") or "?")
        head += td("<b>%s</b><br><span style='color:%s'>%s</span> <span style='color:%s'>%s</span>" % (
            esc(name), MUTED, esc(iv), ACT.get(act, MUTED), act), bg=HEAD_BG, top=False, align="center")
    head += "</tr>"

    def row(label, fn):
        cells = td(label, color=MUTED)
        for _n, _iv, c in lanes:
            cells += fn(c)
        return "<tr>" + cells + "</tr>"

    body = "".join([
        row("action", lambda c: td("<b>%s</b>" % esc(c.get("action")),
                                   color=ACT.get(str(c.get("action")), VAL), align="center")),
        row("strength", lambda c: td(dir_cell(c), align="center")),
        row("trend · RSI", lambda c: td("%s · <b>%s</b>" % (
            esc((c.get("marketResult") or {}).get("trendState")),
            esc(_num((c.get("RSI") or {}).get("currentRSI"), 1))), color=VAL)),
        row("entry gate", lambda c: td(gate_cell(c))),
        row("range map", lambda c: td(range_cell(c), color=VAL)),
        row("spread", lambda c: td(spread_cell(c), color=VAL)),
        row("MA10−MA30", lambda c: td(ma_cell(c), color=VAL)),
        row("range · bias", lambda c: td(bias_cell(c))),
        row("scenario", lambda c: td(esc(c.get("setupScenario")), color=VAL)),
    ])

    tbl = ('<table width="100%" cellspacing="0" cellpadding="0" border="0" style="border:1px solid '
           + BORDER + ';">' + head + body + '</table>')
    title = ("<div style='color:%s'><b style='color:%s'>%s</b> @ %s · strategy %s · ran %s · %s</div>"
             "<div style='height:6px'></div>") % (
        MUTED, VAL, esc(sig.get("pair")), esc(sig.get("exchange")), esc(sig.get("strategy")),
        esc(_iso_ago(sig.get("ts"))), "%.1fs" % ((_num_or(sig.get("run_ms"), 0) or 0) / 1000))
    return "<div style='color:%s'>%s%s</div>" % (VAL, title, tbl)


def _num_or(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _scenarios_html(sig):
    active, seen = {}, set()
    for _name, iv, c in _signal_channels(sig):
        if iv in seen:
            continue
        seen.add(iv)
        active.setdefault(_scenario_label(c), []).append(iv)
    known = {s["n"] for s in SCENARIO_RUBRIC}
    out = ["<table cellpadding='4'>"]
    for s in SCENARIO_RUBRIC:
        hit = active.get(s["n"]) or []
        mark = (f" <b style='color:{AMBER}'>◀ active ({', '.join(hit)})</b>" if hit else "")
        out.append(f"<tr><td style='color:{GREY};white-space:nowrap;vertical-align:top'>{s['n']}</td>"
                   f"<td><b>{s['title']}</b>{mark}<br>"
                   f"<span style='color:{GREY}'>{s['why']}</span></td></tr>")
    for key, hits in active.items():
        if key in known or key == "none":
            continue
        out.append(f"<tr><td style='color:{GREY};vertical-align:top'>{key}</td>"
                   f"<td><b>Higher-lane setup</b> <b style='color:{AMBER}'>◀ active ({', '.join(hits)})</b>"
                   f"<br><span style='color:{GREY}'>15m lane quiet while the higher timeframes carry the read.</span>"
                   f"</td></tr>")
    none_hit = active.get("none") or []
    if none_hit:
        out.append(f"<tr><td style='color:{GREY};vertical-align:top'>—</td>"
                   f"<td><b>No scenario matches cleanly</b> "
                   f"<b style='color:{AMBER}'>◀ active ({', '.join(none_hit)})</b><br>"
                   f"<span style='color:{GREY}'>The bot's fields don't line up with any of the six right now.</span>"
                   f"</td></tr>")
    out.append("</table>")
    out.append(f"<p style='color:{GREY};font-size:11px'>The six-scenario rubric is the bot's own "
               f"vocabulary (from its SignalBot read). These labels are descriptive only — they gate "
               f"nothing and trade nothing.</p>")
    return "".join(out)


def strength_chain(pol, sig):
    """This agent's strength climb, read from the current snapshot — NO network.

    The entry lane is the smallest listed interval; every other listed interval is
    a climb rung that must clear its own band. Uses the decider's OWN pure helpers
    (trade/decider.py::_chain_from / _entry_lane / _bands / _strength) so the panel
    shows exactly what the trader sees. A rung with no lane in the snapshot is
    PULLED LIVE by the decider from candles each cycle — we surface the trader's
    own logged read (last decision's chain) for it, so it shows block/buy, not a
    bare placeholder. Lanes are matched by INTERVAL, never by channel name."""
    chans = (sig or {}).get("channels") or {}
    if not chans:
        return None
    try:
        import sys as _s
        if str(BH / "trade") not in _s.path:
            _s.path.insert(0, str(BH / "trade"))
        import decider as _d
    except Exception:
        return None
    try:
        cfg = dict(pol or {})
        star = bool(cfg.get("star_alignment", False))
        chain = _d._chain_from(cfg, chans)
        entry_iv = chain["entry"]
        en, ec = _d._lane_for_interval(chans, entry_iv)
        eb = _d._bands(cfg, entry_iv, entry=True)        # entry lane's own range
        e_s = _d._strength((ec or {}).get("strength")) if ec else None
        e_buy = e_s is not None and _d._in(e_s, eb["buy"])
        e_sell = e_s is not None and _d._in(e_s, eb["sell"])
        # the trader's own live pulls from its last cycle, keyed by interval
        logged = {}
        try:
            aid = (cfg.get("_agent") or {}).get("id")
            if aid:
                decs = load_agent_decisions(aid, 1)
                for read in (decs[0].get("chain") if decs else None) or []:
                    logged[str(read.get("interval"))] = read
        except Exception:
            logged = {}
        out = {"entry": {"interval": entry_iv, "channel": en, "present": ec is not None,
                         "strength": e_s, "buy_range": list(eb["buy"]), "sell_range": list(eb["sell"]),
                         "buy_ok": e_buy, "sell_ok": e_sell, "live": False,
                         "eligible_buy": bool((ec or {}).get("entryEligible")) if ec else None,
                         "eligible_sell": bool((ec or {}).get("exitEligible")) if ec else None},
               "rungs": [], "chain_used": bool(chain["rungs"]), "star": star}
        for r in chain["rungs"]:
            rn, rc = _d._lane_for_interval(chans, r)     # any lane carrying that interval
            rb = _d._bands(cfg, r)                       # rung's own dynamic range
            if rc is not None:
                g = _d._strength(rc.get("strength"))
                out["rungs"].append({"interval": r, "channel": rn, "present": True, "live": False,
                                     "strength": g, "buy_range": list(rb["buy"]),
                                     "sell_range": list(rb["sell"]),
                                     "buy_ok": (g is not None and _d._in(g, rb["buy"])),
                                     "sell_ok": (g is not None and _d._in(g, rb["sell"]))})
            else:                                        # no snapshot lane -> trader's live pull
                lr = logged.get(r) or {}
                g = lr.get("strength")
                out["rungs"].append({"interval": r, "channel": lr.get("source"), "present": False,
                                     "live": bool(lr), "strength": g,
                                     "buy_range": lr.get("buy_range") or list(rb["buy"]),
                                     "sell_range": lr.get("sell_range") or list(rb["sell"]),
                                     "buy_ok": lr.get("buy_allowed") if lr else None,
                                     "sell_ok": lr.get("sell_allowed") if lr else None})
        out["absent"] = [x["interval"] for x in out["rungs"] if not x["present"] and not x["live"]]
        if out["chain_used"]:
            # match the decider: entry lane first, then EVERY rung (unknown = fail-closed)
            out["buy_all"] = e_buy and all(bool(x["buy_ok"]) for x in out["rungs"])
            out["sell_all"] = e_sell and all(bool(x["sell_ok"]) for x in out["rungs"])
            out["gauge"] = next((x for x in reversed(out["rungs"]) if x["strength"] is not None),
                                out["entry"])
        else:
            out["buy_all"] = e_buy
            out["sell_all"] = e_sell
            out["gauge"] = out["entry"]
        if star:                                          # extra entry-eligibility gate
            out["buy_all"] = out["buy_all"] and bool(out["entry"]["eligible_buy"])
            out["sell_all"] = out["sell_all"] and bool(out["entry"]["eligible_sell"])
        return out
    except Exception:
        return None


def _chain_html(chain):
    if chain is None:
        return f"<i style='color:{GREY}'>No SignalBot snapshot to read the chain from yet.</i>"
    e = chain["entry"]

    def cell(ok, judged=True):
        if not judged or ok is None:
            return f"<td style='color:{AMBER}'>—</td>"
        return (f"<td style='color:{GREEN if ok else RED}'>{'ALLOW' if ok else 'block'}</td>")

    def rng(r):
        return f"[{_num(r['buy_range'][0], 2)}–{_num(r['buy_range'][1], 2)}]"

    def rngs(r):
        return f"[{_num(r['sell_range'][0], 2)}–{_num(r['sell_range'][1], 2)}]"

    rows = [f"<table cellpadding='4'><tr style='color:{GREY}'><td>lane</td><td>strength</td>"
            f"<td>BUY range</td><td>SELL range</td><td>buy</td><td>sell</td></tr>"]
    e_src = f" · {e['channel']}" if e["channel"] else " · not in snapshot"
    elig = ""
    if chain.get("star") and e["present"]:
        eb = "elig" if e.get("eligible_buy") else "not elig"
        xb = "elig" if e.get("eligible_sell") else "not elig"
        elig = (f" <span style='color:{GREY}'>· entry {eb} / exit {xb}</span>")
    rows.append(f"<tr><td><b>{e['interval']}</b> entry lane{e_src}{elig}</td>"
                f"<td>{_num(e['strength'], 3) if e['present'] else '—'}</td>"
                f"<td>{rng(e)}</td><td>{rngs(e)}</td>"
                f"{cell(e['buy_ok'], e['present'])}{cell(e['sell_ok'], e['present'])}</tr>")
    for r in chain["rungs"]:
        if r["present"]:
            src = f" · {r['channel']}"
        elif r.get("live"):
            src = f" <span style='color:{AMBER}'>· live: {r['channel'] or 'pulled from candles'}</span>"
        else:
            src = " · not in snapshot"
        judged = r["present"] or r.get("live")
        rows.append(f"<tr><td>&nbsp;&nbsp;↳ rung {r['interval']}{src}</td>"
                    f"<td>{_num(r['strength'], 3) if judged else '—'}</td>"
                    f"<td>{rng(r)}</td><td>{rngs(r)}</td>"
                    f"{cell(r['buy_ok'], judged)}{cell(r['sell_ok'], judged)}</tr>")
    rows.append("</table>")

    g = chain["gauge"]
    if not chain["chain_used"]:
        summary = (f"<b>Climb is OFF</b> — this agent trades its entry lane on its own strength "
                   f"(no rungs listed). Entry range: buy {rng(e)}, sell {rngs(e)}.")
    else:
        buy = ("ALLOW" if chain["buy_all"] else "BLOCKED") if chain["buy_all"] is not None else "unknown"
        sell = ("ALLOW" if chain["sell_all"] else "BLOCKED") if chain["sell_all"] is not None else "unknown"
        bc = GREEN if chain["buy_all"] else (RED if chain["buy_all"] is False else AMBER)
        sc = GREEN if chain["sell_all"] else (RED if chain["sell_all"] is False else AMBER)
        summary = (f"<b>Climb ON</b> — BUY through the rungs is <b style='color:{bc}'>{buy}</b>, "
                   f"SELL is <b style='color:{sc}'>{sell}</b>. Acting gauge = {g['interval']} "
                   f"strength {_num(g['strength'], 3)}.")
        live = [x["interval"] for x in chain["rungs"] if not x["present"] and x.get("live")]
        if live:
            summary += (f" <span style='color:{GREY}'>Rungs {', '.join(live)} have no snapshot lane — "
                        f"shown from the trader's live candle pull last cycle.</span>")
        if chain["absent"]:
            summary += (f" <span style='color:{AMBER}'>Rungs {', '.join(chain['absent'])} are not in the "
                        f"snapshot and have no live read yet — judged fail-closed.</span>")
    if chain.get("star"):
        summary += (f" <span style='color:{GREY}'>Star alignment is ON — the entry lane must also be "
                    f"entryEligible/exitEligible.</span>")
    return "".join(rows) + f"<p style='color:{GREY}'>{summary}</p>"


class AgentsMixin:
    # ---------------------------------------------------------------- build
    def _build_agents(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        head = QLabel("<b style='font-size:15px'>Agents</b> &nbsp;<span style='color:#9ca3af'>"
                      "the bodies — BlackHorse (brain) owns them; intervals are the profiles.</span>")
        v.addWidget(head)
        self.agent_status = QLabel("")
        self.agent_status.setStyleSheet("color:#9ca3af")
        self.agent_status.setWordWrap(True)
        v.addWidget(self.agent_status)

        # create row
        cg = QGroupBox("Create an agent")
        cr = QHBoxLayout(cg)
        cr.addWidget(QLabel("mode"))
        self.agent_kind = QComboBox()
        self.agent_kind.addItems(["PAPER", "LIVE"])
        cr.addWidget(self.agent_kind)
        cr.addWidget(QLabel("interval"))
        self.agent_profile = QComboBox()
        self.agent_profile.setMinimumWidth(260)
        try:
            agents, _ = self._agents_mod()
            for k, p in agents.interval_defaults().items():
                self.agent_profile.addItem(f"{k} — {p['label']}", k)
        except Exception:
            pass
        cr.addWidget(self.agent_profile, 1)
        cr.addWidget(QLabel("capital $"))
        self.agent_capital = QLineEdit("50")
        self.agent_capital.setMaximumWidth(80)
        cr.addWidget(self.agent_capital)
        b_create = QPushButton("＋ Create")
        b_create.setStyleSheet("background:#1a5;color:white;font-weight:bold;padding:4px 12px")
        b_create.clicked.connect(self.create_agent)
        cr.addWidget(b_create)
        v.addWidget(cg)
        self.agent_profile_desc = QLabel("")
        self.agent_profile_desc.setWordWrap(True)
        self.agent_profile_desc.setStyleSheet("color:#9ca3af;font-size:11px")
        v.addWidget(self.agent_profile_desc)
        self.agent_profile.currentIndexChanged.connect(self._show_profile)
        self._show_profile()

        itip = QLabel("<span style='color:#9ca3af'>A new agent is seeded with that interval's "
                      "default band (buy ≤ / sell ≥ / R:R / stop / risk). The climb it starts "
                      "with is that interval's default climb — turn climb rungs on or off for "
                      "any agent in its settings.</span>")
        itip.setWordWrap(True)
        itip.setStyleSheet("color:#9ca3af;font-size:11px")
        v.addWidget(itip)

        # main split: list | detail
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setMinimumHeight(640)
        split.setChildrenCollapsible(False)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.agent_list = QListWidget()
        self.agent_list.setSpacing(2)
        self.agent_list.setMinimumWidth(240)
        self.agent_list.setWordWrap(True)
        self.agent_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.agent_list.currentRowChanged.connect(self._on_agent_selected)
        lv.addWidget(self.agent_list, 1)
        br = QHBoxLayout()
        b_on = QPushButton("Enable")
        b_on.clicked.connect(lambda: self.agent_toggle(True))
        b_off = QPushButton("Disable")
        b_off.clicked.connect(lambda: self.agent_toggle(False))
        b_del = QPushButton("Delete…")
        b_del.setStyleSheet(f"color:{RED}")
        b_del.setToolTip("Remove this agent. Its working orders are cancelled first; open "
                         "positions stay on the exchange and must be dealt with separately.")
        b_del.clicked.connect(self.delete_agent)
        br.addWidget(b_on)
        br.addWidget(b_off)
        br.addWidget(b_del)
        lv.addLayout(br)
        split.addWidget(left)

        self.agent_tabs = QTabWidget()
        self.ag_overview = QTextBrowser()
        self.ag_overview.setOpenExternalLinks(False)
        self.agent_tabs.addTab(self.ag_overview, "Overview")
        self.ag_settings_scroll = QScrollArea()
        self.ag_settings_scroll.setWidgetResizable(True)
        self.agent_tabs.addTab(self.ag_settings_scroll, "Settings")
        self.ag_orders_scroll = QScrollArea()
        self.ag_orders_scroll.setWidgetResizable(True)
        self._build_orders_tab()
        self.agent_tabs.addTab(self.ag_orders_scroll, "Orders")
        self.ag_decisions_scroll = QScrollArea()
        self.ag_decisions_scroll.setWidgetResizable(True)
        self._build_decisions_tab()
        self.agent_tabs.addTab(self.ag_decisions_scroll, "Decisions")
        split.addWidget(self.agent_tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 800])
        v.addWidget(split, 1)

        # house decision
        hg = QGroupBox("House decision (broker across enabled agents)")
        hl = QVBoxLayout(hg)
        hr = QHBoxLayout()
        b_house = QPushButton("🧠 Decide now (house)")
        b_house.clicked.connect(self.house_decide)
        b_ref = QPushButton("⟳ Refresh")
        b_ref.clicked.connect(self.refresh_agents)
        hr.addWidget(b_house)
        hr.addWidget(b_ref)
        hr.addStretch(1)
        hl.addLayout(hr)
        self.house_view = QTextEdit()
        self.house_view.setReadOnly(True)
        self.house_view.setMinimumHeight(120)
        self.house_view.setFont(QFont("Monospace", 9))
        hl.addWidget(self.house_view)
        v.addWidget(hg)

        scroll.setWidget(w)
        self.pages[3] = scroll
        scroll.hide()
        self._agent_rows = []
        self._agent_fields = {}
        self._agent_timer = QTimer(self)
        self._agent_timer.timeout.connect(self._tick_agent_orders)
        self._agent_timer.start(5000)
        QTimer.singleShot(300, self.refresh_agents)
        return scroll

    def _build_orders_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.ord_summary = QLabel("")
        self.ord_summary.setWordWrap(True)
        self.ord_summary.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.ord_summary)
        self.tbl_pending = _table(["Pair", "Limit px", "Volume", "Locked $ (incl. fee)", "Expires in", "Order id"], 90)
        v.addWidget(_box("Working limit orders (waiting to fill)", self.tbl_pending))
        self.tbl_pos = _table(["Pair", "Qty", "Entry", "Bid", "Net @bid", "Breakeven",
                               "Sell ≥", "Target", "State"], 130)
        self.tbl_pos.itemSelectionChanged.connect(self._show_pos_detail)
        v.addWidget(_box("Open positions (click a row for the exit detail)", self.tbl_pos))
        self.pos_detail = QTextBrowser()
        self.pos_detail.setMinimumHeight(190)
        v.addWidget(_box("Exit detail — stored prices captured at entry + profit check", self.pos_detail))
        self.tbl_orders = _table(["When", "Side", "Type", "Price", "Volume", "Filled", "Fee $", "Status", "Order id"], 170)
        v.addWidget(_box("Recent exchange orders (database)", self.tbl_orders))
        self.tbl_closed = _table(["Closed", "Pair", "Entry", "Exit", "Qty", "Net $", "Fees $", "Why"], 140)
        v.addWidget(_box("Closed trades", self.tbl_closed))
        v.addStretch(1)
        self.ag_orders_scroll.setWidget(page)
        self._ord_pos_rows = []

    def _build_decisions_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.dec_summary = QLabel("")
        self.dec_summary.setWordWrap(True)
        self.dec_summary.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.dec_summary)
        self.dec_why = QTextBrowser()
        self.dec_why.setMinimumHeight(180)
        v.addWidget(_box("Why it is (or isn't) entering — the gate checklist the trader produced",
                         self.dec_why))
        self.dec_chain = QTextBrowser()
        self.dec_chain.setMinimumHeight(170)
        v.addWidget(_box("Strength chain — the climb the entry lane must clear, read live now",
                         self.dec_chain))
        self.tbl_decisions = _table(["When", "Action", "Pair", "Channel", "Reason"], 150)
        v.addWidget(_box("Decisions it is making (newest first)", self.tbl_decisions))
        self.dec_market = QTextBrowser()
        self.dec_market.setFont(QFont("Monospace", 9))
        self.dec_market.setMinimumHeight(340)
        v.addWidget(_box("Where the market is — the SignalBot's own read (per interval)",
                         self.dec_market))
        self.dec_scenarios = QTextBrowser()
        self.dec_scenarios.setMinimumHeight(260)
        v.addWidget(_box("Scenarios — the bot's six, and which one each interval sits in now",
                         self.dec_scenarios))
        v.addStretch(1)
        self.ag_decisions_scroll.setWidget(page)

    # ---------------------------------------------------------------- list / selection
    def refresh_agents(self):
        try:
            agents, _ = self._agents_mod()
            rows = agents.list_agents()
            cur = self._selected_agent()
            cur_id = cur["id"] if cur else None
            self._agent_rows = rows
            with keep_scroll(self.agent_list):
                self.agent_list.blockSignals(True)
                self.agent_list.clear()
                for a in rows:
                    on = a.get("enabled")
                    pairs = ",".join(a.get("pairs") or []) or "all pairs"
                    it = QListWidgetItem(f"{'● ' if on else '○ '}{a['name']}  ({a['id']})\n"
                                         f"   {a.get('profile_label') or a.get('entry_interval') or '-'} · ${a['capital']} · {a.get('mode') or '-'}\n"
                                         f"   {pairs.replace(',', ', ')}")
                    it.setForeground(QBrush(QColor(GREEN if on else GREY)))
                    self.agent_list.addItem(it)
                sel = next((i for i, a in enumerate(rows) if a["id"] == cur_id), 0 if rows else -1)
                self.agent_list.setCurrentRow(sel)
                self.agent_list.blockSignals(False)
            self._show_profile()
            self._refresh_audit()
            self._render_agent()
        except Exception as e:
            set_text_keep_scroll(self.house_view, f"agents error: {e}")

    def _refresh_audit(self):
        agents, _ = self._agents_mod()
        aud = agents.audited()
        self._agent_audit = {r["id"]: r for r in aud.get("agents", [])}
        stuck = [r["name"] for r in aud.get("agents", []) if r.get("enabled") and r.get("can_trade") is False]
        head = (f"capital ${aud['capital_total']} · deployed ${aud['deployed']} · free cash ${aud['available']} · "
                f"agents can still spend ${aud['agents_claim']} · equity ${aud['equity']}")
        if aud.get("over_allocated"):
            self.agent_status.setText(f"⚠ OVER-ALLOCATED by ${aud['overshoot']} — {head}")
            self.agent_status.setStyleSheet("color:#ef4444")
        elif stuck:
            self.agent_status.setText(f"✓ allocation adds up — {head}<br>"
                                      f"⚠ can't place an order (own size cap below the exchange minimum): {', '.join(stuck)}")
            self.agent_status.setTextFormat(Qt.TextFormat.RichText)
            self.agent_status.setStyleSheet("color:#d97706")
        else:
            self.agent_status.setText(f"✓ allocation adds up — {head}")
            self.agent_status.setStyleSheet("color:#16a34a")

    def _selected_agent(self):
        i = self.agent_list.currentRow() if hasattr(self, "agent_list") else -1
        rows = getattr(self, "_agent_rows", [])
        return rows[i] if 0 <= i < len(rows) else None

    def _on_agent_selected(self, _row):
        self._render_agent()

    def _select_agent_by_id(self, agent_id):
        for i, a in enumerate(self._agent_rows):
            if a["id"] == agent_id:
                self.agent_list.setCurrentRow(i)
                return

    def agent_toggle(self, on):
        a = self._selected_agent()
        if a:
            self._set_agent_enabled(a["id"], on)

    # ---------------------------------------------------------------- render
    def _render_agent(self):
        a = self._selected_agent()
        if not a:
            with keep_scroll(self.ag_overview):
                self.ag_overview.setHtml("<i>No agents yet — create one above.</i>")
            return
        agents, _ = self._agents_mod()
        try:
            pol = agents.policy_for(a["id"])
        except Exception as e:
            with keep_scroll(self.ag_overview):
                self.ag_overview.setHtml(f"<pre>error: {e}</pre>")
            return
        self._render_overview(a, pol)
        self._render_settings(a, pol)
        self._refresh_orders_tab()
        self._render_decisions(a)

    def _render_overview(self, a, pol):
        on = a.get("enabled")
        d = load_agent_orders(a["id"])["last_decision"]
        last = (f"<b>{d['action']}</b> — {d.get('reason') or ''} <span style='color:{GREY}'>({_ago(d['ts'])})</span>"
                if d else f"<span style='color:{GREY}'>no decision recorded yet</span>")
        ivs = sorted([x for x in (pol.get("strategy_intervals") or []) if x in INTV], key=_ivmin)
        entry = ivs[0] if ivs else "—"
        climb = ", ".join(ivs[1:]) if len(ivs) > 1 else "off — trades on its own strength"
        rows = [("Status", f"<b style='color:{GREEN if on else GREY}'>{'ENABLED' if on else 'disabled'}</b>"),
                ("Entry interval", f"<b>{entry}</b> · climb: {climb}"),
                ("MA/spread gate",
                 "<b>%s</b> · spread %s · MA cross %s" % (
                     pol.get("ma_gate_interval") or "15m",
                     "ON" if pol.get("require_spread_agreement") else "OFF",
                     "ON" if pol.get("require_ma_cross_agreement") else "OFF")),
                ("Mode", f"<b>{a.get('mode') or '-'}</b>"),
                ("Capital (total budget)", f"${a.get('capital')}" + self._budget_html(a["id"])),
                ("Pairs", ", ".join(a.get("pairs") or []) or "all pairs"),
                ("Require edge proof",
                 "<b>" + ("ON — only PROVEN cells" if pol.get("require_proven_edge", False)
                          else "OFF — trades unproven setups") + "</b>"),
                ("Buy range (entry lane)", f"[0 – {pol.get('entry_strength_max')}]" if pol.get("entry_strength_max") is not None else "—"),
                ("Entry order", f"{pol.get('entry_order_type', 'limit')} · "
                                 f"TIF {pol.get('entry_time_in_force', 'GTD')} "
                                 f"(expires {pol.get('entry_expiry_seconds', '-')}s)"),
                ("Sell range (entry lane)", f"[{pol.get('exit_strength_min')} – 1]" if pol.get("exit_strength_min") is not None else "—"),
                ("Star alignment",
                 "<b>" + ("ON — entry lane must be eligible" if pol.get("star_alignment", False)
                          else "OFF — strength zones only") + "</b>"),
                ("Rungs only",
                 "<b>ON — rungs decide ("
                 + ("one rung is enough"
                    if str(pol.get("rung_require_all", "ALL")).strip().upper() in
                    ("ANY", "FALSE", "0", "NO", "OFF")
                    else "every rung must agree")
                 + ")</b>"
                 if pol.get("rungs_only", False)
                 else "<b>OFF — normal gates · ALL rungs</b>"),
                ("Min net profit to sell", f"{pol.get('exit_min_net_pct', '-')}%  · stored sets: {', '.join(pol.get('exit_price_sets') or [])}"),
                ("Stop loss",
                 ("<b style='color:%s'>ON</b> — %s" % (AMBER, pol.get("stop_mode", "price_watch")))
                 + (" · flat %s%%" % pol.get("stop_loss_pct") if pol.get("stop_loss_pct") else " · structural stop")
                 if pol.get("stop_loss_enabled")
                 else "OFF — profit exits only"),
                ("Risk / max position", f"{pol.get('risk_per_trade_pct')}% / {pol.get('max_position_pct')}%"),
                ("Min R:R", pol.get("min_reward_risk")),
                ("Last decision", last)]
        body = "".join(f"<tr><td style='padding:3px 14px 3px 0;color:{GREY};white-space:nowrap'>{k}</td>"
                       f"<td style='padding:3px 0'>{v}</td></tr>" for k, v in rows)
        desc = ("intervals are the profiles — the entry lane trades, the climb rungs each confirm first."
                if a.get("kind") == "trading" else "")
        foot = ("Stop loss is ON — a protective %s stop rests a maker limit near the level." % pol.get("stop_mode", "price_watch")
                if pol.get("stop_loss_enabled")
                else "No stop orders: positions only sell at a net profit after fees (enable Stop loss to change this).")
        with keep_scroll(self.ag_overview):
            self.ag_overview.setHtml(
                f"<h2 style='margin-bottom:2px'>{a.get('name')} <span style='color:{GREY};font-size:13px'>"
                f"{a['id']}</span></h2><p style='color:{GREY}'>{desc}</p><table>{body}</table>"
                f"<p style='color:{GREY};font-size:11px'>{foot}</p>")

    def _budget_html(self, agent_id):
        r = getattr(self, "_agent_audit", {}).get(agent_id)
        if not r:
            return ""
        out = (f" &nbsp;<span style='color:{GREY}'>· deployed ${r['deployed']} · can still size "
               f"<b>${r['spendable']}</b></span>")
        if r.get("min_order") is not None:
            ok = r.get("can_trade")
            out += (f"<br><span style='color:{GREEN if ok else AMBER}'>order size on {r.get('pair')}: up to "
                    f"${r['max_order']} (its max-position cap) vs exchange minimum ${r['min_order']} → "
                    f"{'OK' if ok else 'TOO SMALL — needs about $' + str(r['capital_needed']) + ' capital or a higher max position %'}"
                    f"</span>")
        return out

    # ---------------------------------------------------------------- profile
    def _render_settings(self, a, pol):
        page = QWidget()
        pv = QVBoxLayout(page)
        self._agent_fields = {}
        self._field_labels = {}
        self._rung_fields = {}
        # Entry lane & strength climb — the interval buttons ARE the profiles.
        pv.addWidget(self._build_interval_block(a, pol))
        for title, fields in SETTINGS:
            g = QGroupBox(title)
            grid = QGridLayout(g)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(8)
            row = col = 0
            for key, label, kind, where, tip in fields:
                if key in ('strategy', 'intervals', 'channels'):
                    continue  # intervals are handled by the entry/climb block
                cur = a.get(key) if where == 'agent' else pol.get(key)
                if kind == 'bool':
                    # Checkboxes carry their own label and take the whole row. Laying
                    # them into the 2-column grid made them overlap (and hide) the
                    # field beside them — that is why the MA/spread interval combo
                    # vanished under the "Spread verdict gate" checkbox.
                    if col:
                        row += 1; col = 0
                    w = QCheckBox(label); w.setChecked(bool(cur)); w.setMinimumWidth(0)
                    w.setToolTip(tip)
                    grid.addWidget(w, row, 0, 1, 4)
                    self._agent_fields[key] = (w, kind, where, cur)
                    row += 1
                    continue
                lab = QLabel(label)
                lab.setToolTip(tip)
                lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if kind.startswith('combo:'):
                    w = QComboBox()
                    items = kind.split(':')[1].split(',')
                    w.addItems(items)
                    w.setCurrentText(str(cur or w.itemText(0)))
                else:
                    txt = ','.join(cur) if isinstance(cur, list) else ('' if cur is None else str(cur))
                    w = QLineEdit(txt)
                w.setMinimumWidth(150); w.setToolTip(tip)
                self._field_labels[key] = lab
                grid.addWidget(lab, row, col*3); grid.addWidget(w, row, col*3+1)
                if col == 0:
                    grid.setColumnMinimumWidth(2, 24)
                    col = 1
                else:
                    col = 0; row += 1
                self._agent_fields[key] = (w, kind, where, cur)
            grid.setColumnStretch(1,1); grid.setColumnStretch(4,1)
            pv.addWidget(g)
        ro = self._agent_fields.get('rungs_only')
        if ro:
            ro[0].toggled.connect(self._sync_rungs_only_ui)
        self._sync_rungs_only_ui()
        row = QHBoxLayout()
        b_save = QPushButton("Save to agent policy")
        b_save.setStyleSheet("background:#1d4ed8;color:white;font-weight:bold;padding:6px 16px")
        b_save.clicked.connect(lambda _c=False, aid=a["id"]: self._save_agent_settings(aid))
        b_rev = QPushButton("Revert")
        b_rev.clicked.connect(self._render_agent)
        self.ag_save_msg = QLabel("")
        row.addWidget(b_save)
        row.addWidget(b_rev)
        row.addWidget(self.ag_save_msg, 1)
        pv.addLayout(row)
        note = QLabel("Changes apply on the trader's next cycle (≤5 min). Mode is per agent — PAPER agents never place live orders.")
        note.setStyleSheet(f"color:{GREY};font-size:11px")
        note.setWordWrap(True)
        pv.addWidget(note)
        pv.addStretch(1)
        self.ag_settings_scroll.setWidget(page)

    def _sync_rungs_only_ui(self, *_):
        """The rung agreement type is only relevant when RUNGS ONLY is enabled, so
        hide the agreement row until the user turns that mode on."""
        ro = self._agent_fields.get('rungs_only')
        ag = self._agent_fields.get('rung_require_all')
        if not ro or not ag:
            return
        on = bool(ro[0].isChecked())
        for w in (self._field_labels.get('rung_require_all'), ag[0]):
            if w is not None:
                w.setVisible(on)
                w.setEnabled(on)

    # ---------------------------------------------------------------- interval grid
    def _build_interval_block(self, a, pol):
        self._interval_pol = pol
        g = QGroupBox("Entry lane & strength climb — intervals are the profiles")
        box = QVBoxLayout(g)
        iv = [x for x in (pol.get('strategy_intervals') or [])
              if x in INTV]
        # the decider takes the SMALLEST listed interval as the entry lane — sort so
        # the GUI shows the same driving lane the bot actually trades.
        iv = sorted(iv, key=_ivmin)
        if not iv:
            iv = ['15m', '60m', '240m']
        self._interval_entry = iv[0]
        self._interval_on = [x for x in iv if x != self._interval_entry]
        row = QHBoxLayout()
        self._interval_buttons = {}
        for x in INTV:
            b = QPushButton(x)
            b.setCheckable(True)
            b.setMinimumWidth(72)
            b.clicked.connect(lambda _c=False, iv=x: self._interval_clicked(iv))
            self._interval_buttons[x] = b
            row.addWidget(b)
        box.addLayout(row)
        self._interval_status = QLabel("")
        self._interval_status.setWordWrap(True)
        self._interval_status.setStyleSheet(f"color:{GREY};font-size:11px")
        box.addWidget(self._interval_status)
        self._rungs_holder = QWidget()
        self._rungs_box = QVBoxLayout(self._rungs_holder)
        self._rungs_box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._rungs_holder)
        self._restyle_interval_buttons(apply_defaults=False)
        return g

    def _restyle_interval_buttons(self, apply_defaults=False):
        entry = self._interval_entry
        emin = _ivmin(entry)
        for x in INTV:
            b = self._interval_buttons[x]
            b.blockSignals(True)
            if x == entry:
                b.setText(f"▶ {x} ◀")
                b.setChecked(True)
                b.setEnabled(True)
                b.setToolTip(f"{x} is the entry (driving) lane — the one this agent trades. "
                             f"Click to hand the driving lane to the next rung up.")
                b.setStyleSheet("background:#1d4ed8;color:white;font-weight:bold")
            elif _ivmin(x) < emin:
                # smaller than the entry — NOT disabled: click it to pull the driving
                # lane back down. (it used to grey out and lock the lane forever)
                b.setText(f"◂ {x}")
                b.setChecked(False)
                b.setEnabled(True)
                b.setToolTip(f"{x} is smaller than the current entry lane {entry} — "
                             f"click it to make {x} the driving lane again.")
                b.setStyleSheet("color:#9ca3af;background:#111827;border:1px solid #374151")
            elif x in self._interval_on:
                b.setText(f"✔ {x}")
                b.setChecked(True)
                b.setEnabled(True)
                b.setToolTip(f"{x} is a climb rung — it must clear its own band before "
                             f"{entry} may trade. Click to take it out of the climb.")
                b.setStyleSheet("background:#065f46;color:white")
            else:
                b.setText(f"＋ {x}")
                b.setChecked(False)
                b.setEnabled(True)
                b.setToolTip(f"Add {x} to the climb as a rung above {entry}.")
                b.setStyleSheet("")
            b.blockSignals(False)
        self._rebuild_rung_rows()
        if apply_defaults:
            self._apply_interval_defaults(entry)

    def _interval_clicked(self, x):
        if x == self._interval_entry:
            # the entry button walks the driving lane UP the ladder: the smallest
            # rung on takes over as the driving lane.
            nxt = [y for y in INTV if _ivmin(y) > _ivmin(x) and y in self._interval_on]
            if nxt:
                self._interval_entry = nxt[0]
                self._interval_on = [y for y in self._interval_on if y != self._interval_entry]
            else:
                return  # keep a lone entry; nothing to release onto
        elif _ivmin(x) < _ivmin(self._interval_entry):
            # a SMALLER interval was clicked — pull the driving lane back down to it.
            # The old driving lane drops into the climb as a rung (it is higher than
            # the new entry), the rungs that are still above the new entry stay on,
            # and the new lane's own default climb is merged in.
            old_entry = self._interval_entry
            keep = {y for y in self._interval_on if _ivmin(y) > _ivmin(x)}
            if _ivmin(old_entry) > _ivmin(x):
                keep.add(old_entry)
            for y in self._interval_defaults_climb(x):
                keep.add(y)
            self._interval_entry = x
            self._interval_on = [y for y in INTV if y in keep]
        else:
            if x in self._interval_on:
                self._interval_on.remove(x)
            else:
                self._interval_on.append(x)
            self._restyle_interval_buttons(apply_defaults=False)
            return
        self._restyle_interval_buttons(apply_defaults=True)

    def _interval_defaults_climb(self, iv):
        try:
            agents, _ = self._agents_mod()
            climb = agents.interval_defaults().get(iv, {}).get('climb') or []
            return [y for y in climb if _ivmin(y) > _ivmin(iv)]
        except Exception:
            return []

    def _rebuild_rung_rows(self):
        while self._rungs_box.count():
            it = self._rungs_box.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        active = sorted([x for x in self._interval_on if _ivmin(x) > _ivmin(self._interval_entry)],
                        key=_ivmin)
        entry = self._interval_entry
        self._rung_fields = {}
        smaller = [x for x in INTV if _ivmin(x) < _ivmin(entry)]
        back = (f" Click a smaller interval ({', '.join(smaller)}) to pull the driving lane "
                f"back down to it." if smaller else "")
        if active:
            names = ", ".join(active)
            self._interval_status.setText(
                f"<b>{entry}</b> is the entry lane (the one this agent trades). Climb ON: <b>{names}</b> — "
                f"before {entry} may buy or sell, each rung must sit in its own buy/sell RANGE. "
                f"Click a larger interval to add or remove it from the climb.{back}")
        else:
            self._interval_status.setText(
                f"<b>{entry}</b> is the entry lane and the climb is <b>off</b> — it trades on its own "
                f"strength alone. Click a larger interval above to add a climb rung.{back}")
        for x in active:
            old = (self._interval_pol.get('rung_bands') or {}).get(x) or {}
            em = self._interval_pol.get('entry_strength_max_' + x,
                                        self._interval_pol.get('entry_strength_max', 0.25))
            xm = self._interval_pol.get('exit_strength_min_' + x,
                                        self._interval_pol.get('exit_strength_min', 0.75))
            old_buy = old.get('buy') or [0.0, em]
            old_sell = old.get('sell') or [xm, 1.0]
            rr = QWidget()
            hb = QHBoxLayout(rr)
            hb.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(f"{x} rung")
            lab.setMinimumWidth(70)
            wblo = QLineEdit(str(old_buy[0])); wbhi = QLineEdit(str(old_buy[1]))
            wslo = QLineEdit(str(old_sell[0])); wshi = QLineEdit(str(old_sell[1]))
            for w in (wblo, wbhi, wslo, wshi):
                w.setMinimumWidth(56)
            hb.addWidget(lab)
            hb.addWidget(QLabel("buy ["))
            hb.addWidget(wblo); hb.addWidget(QLabel("–")); hb.addWidget(wbhi)
            hb.addWidget(QLabel("]  sell ["))
            hb.addWidget(wslo); hb.addWidget(QLabel("–")); hb.addWidget(wshi)
            hb.addWidget(QLabel("]"))
            hb.addStretch(1)
            self._rung_fields[x] = (wblo, wbhi, wslo, wshi)
            self._rungs_box.addWidget(rr)

    def _apply_interval_defaults(self, iv):
        try:
            agents, _ = self._agents_mod()
            cfg = agents.interval_defaults().get(iv, {}).get('cfg') or {}
        except Exception:
            cfg = {}
        for k, v in cfg.items():
            w = self._agent_fields.get(k)
            if w and isinstance(w[0], QLineEdit):
                w[0].setText(str(v))

    def _entry_climb_state(self):
        entry = getattr(self, '_interval_entry', None)
        on = list(getattr(self, '_interval_on', []))
        if entry not in INTV:
            return ['15m', '60m', '240m']
        hist = [x for x in INTV if _ivmin(x) > _ivmin(entry) and x in on]
        return [entry] + sorted(hist, key=_ivmin)

    def _save_agent_settings(self, aid):
        agents, _ = self._agents_mod()
        changed, errs = [], []
        # intervals come from the entry button + climbed rung toggles
        iv_list = self._entry_climb_state()
        strat = 'custom'
        # if empty, derive from strategy
        if not iv_list:
            if strat == 'swing':
                iv_list = ['15m','60m','240m']
            elif strat == 'daytrade':
                iv_list = ['5m','15m','60m']
            else:
                iv_list = ['15m','60m','240m']
        # normalize strategy label from intervals if it matches known sets
        ivs = iv_list
        if ivs == ['15m','60m','240m'] or sorted(ivs) == sorted(['15m','60m','240m']):
            strat_lbl = 'swing'
        elif '5m' in ivs or ivs == ['5m','15m','60m'] or sorted(ivs) == sorted(['5m','15m','60m']):
            strat_lbl = 'daytrade'
        else:
            strat_lbl = strat if strat in ('swing','daytrade') else 'custom'
        # write strategy/intervals — intervals are the profiles; no channel names
        try:
            agents.set_override(aid, 'strategy_intervals', ivs)
            changed.append('intervals')
        except Exception as e:
            errs.append(f'intervals:{e}')
        try:
            agents.set_override(aid, 'strategy', strat_lbl)
            changed.append('strategy')
        except Exception as e:
            errs.append(f'strategy:{e}')
        try:                                   # drop any legacy name-based 'channels'
            agents.clear_override(aid, 'channels')
        except Exception as e:
            errs.append(f'channels:{e}')
        # handle other fields
        for key, (w, kind, where, old) in self._agent_fields.items():
            if key in ('strategy','intervals'):
                continue
            try:
                if kind == 'bool':
                    val = bool(w.isChecked()); same = old is not None and bool(old) == val
                else:
                    raw = (w.currentText() if isinstance(w, QComboBox) else w.text()).strip()
                    if kind == 'list':
                        val = [x.strip() for x in raw.split(',') if x.strip()]
                        if key == 'pairs': val = [x.upper() for x in val]
                        same = val == (old or [])
                    elif kind == 'float':
                        if raw == '':
                            if key in ('stop_loss_pct', 'trail_pct'):
                                if old is None:
                                    continue
                                agents.set_override(aid, key, None)   # blank = back to structural/default
                                changed.append(key)
                                continue
                            continue
                        val = float(raw); same = old is not None and float(old) == val
                    elif kind == 'int':
                        if raw == '': continue
                        val = int(float(raw)); same = old is not None and int(float(old)) == val
                    else:
                        val = raw; same = str(old) == val
                if same: continue
                if key == 'pairs':
                    agents.set_pairs(aid, val)
                elif key == 'mode':
                    agents.set_mode(aid, val)
                elif key == 'capital':
                    agents.set_capital(aid, val, allow_over=True)
                else:
                    agents.set_override(aid, key, val)
                changed.append(key)
            except Exception as e:
                errs.append(f'{key}:{e}')
        # strength-climb rung ranges -> one nested rung_bands dict, keyed by interval
        try:
            bands = {}

            def _fv(w):
                raw = w.text().strip()
                if raw == '':
                    return None
                try:
                    return float(raw)
                except ValueError:
                    return None

            for x, (wblo, wbhi, wslo, wshi) in getattr(self, '_rung_fields', {}).items():
                blo, bhi, slo, shi = _fv(wblo), _fv(wbhi), _fv(wslo), _fv(wshi)
                if None in (blo, bhi, slo, shi):
                    errs.append(f'rung {x}: enter all four range values (or none)')
                    continue
                bands[x] = {'buy': [min(blo, bhi), max(blo, bhi)],
                            'sell': [min(slo, shi), max(slo, shi)]}
            if bands != (self._interval_pol.get('rung_bands') or {}):
                agents.set_override(aid, 'rung_bands', bands)
                changed.append('rung_bands')
        except Exception as e:
            errs.append(f'rung_bands:{e}')
        if errs:
            self.ag_save_msg.setText(f"<span style='color:{RED}'>error — {'; '.join(errs)}</span>")
        else:
            self.ag_save_msg.setText(f"<span style='color:{GREEN}'>saved {', '.join(sorted(set(changed))) or 'nothing changed'}</span>")
        if changed:
            self.refresh_agents()
            self.ag_save_msg.setText(f"<span style='color:{GREEN}'>saved {', '.join(sorted(set(changed)))}</span>")

    # ---------------------------------------------------------------- orders tab
    def _tick_agent_orders(self):
        try:
            if not self.pages[3].isVisible():
                return
            cur = self.agent_tabs.currentWidget()
            if cur is self.ag_overview:
                self._refresh_audit()
                a = self._selected_agent()
                if a:
                    pol = self._agents_mod()[0].policy_for(a["id"])
                    self._render_overview(a, pol)
            elif cur is self.ag_orders_scroll:
                # hold the tab's own scroll too: the tables/browsers are rebuilt here
                with keep_scroll(self.ag_orders_scroll):
                    self._refresh_orders_tab()
            elif cur is self.ag_decisions_scroll:
                a = self._selected_agent()
                if a:
                    with keep_scroll(self.ag_decisions_scroll):
                        self._render_decisions(a)
        except Exception:
            pass

    def _refresh_orders_tab(self):
        a = self._selected_agent()
        if not a:
            return
        d = load_agent_orders(a["id"])
        now = time.time()
        wpos = (d["watch"] or {}).get("positions", {})
        net_total = d["net_total"]
        wts = (d["watch"] or {}).get("ts")
        self.ord_summary.setText(
            f"<b>{a['name']}</b> · {len(d['positions'])} open · {len(d['pending'])} working · "
            f"realized net <b style='color:{GREEN if net_total >= 0 else RED}'>{_usd(net_total)}</b> · "
            f"<span style='color:{GREY}'>bid/ask watch updated {_ago(wts) if wts else 'never'}</span>")
        _fill(self.tbl_pending, [[p["pair"], _px(p["price"]), _px(p["volume"], 6), _px(p.get("outlay"), 2),
                                  f"{max(0, int(float(p['expires']) - now))}s", p["txid"]] for p in d["pending"]])
        rows, colors, self._ord_pos_rows = [], {}, []
        for i, p in enumerate(d["positions"]):
            w = wpos.get(p["id"], {})
            cands = [c for c in w.get("candidates", []) if c.get("passes")]
            net = w.get("net_pct_if_sold_at_bid")
            nxt = f"{cands[0]['price']} ({cands[0]['interval']})" if cands else "none pass"
            state = ("SELLING" if p.get("exit_txid") else ("WATCH" if w else "no data"))
            sw = w.get("stop") or {}
            if sw.get("enabled"):
                state += f" · stop:{sw.get('action', '?')}"
            rows.append([p["pair"], _px(p["qty"], 6), _px(p["entry"]), _px(w.get("bid")),
                         "—" if net is None else f"{net:+.2f}%", _px(w.get("breakeven_price")),
                         _px(w.get("min_profit_price")), nxt, state])
            if net is not None:
                colors[(i, 4)] = GREEN if net >= 0 else RED
            colors[(i, 7)] = GREEN if cands else AMBER
            self._ord_pos_rows.append(p["id"])
        keep = self.tbl_pos.currentRow()
        _fill(self.tbl_pos, rows, colors)
        if self._ord_pos_rows:
            self.tbl_pos.selectRow(keep if 0 <= keep < len(rows) else 0)
        else:
            with keep_scroll(self.pos_detail):
                self.pos_detail.setHtml(f"<i style='color:{GREY}'>No open positions for this agent.</i>")
        _fill(self.tbl_orders, [[time.strftime("%m-%d %H:%M", time.localtime(o["created"])), o["side"].upper(),
                                 o["order_type"], _px(o.get("avg_price") or o.get("price")), _px(o["vol"], 6),
                                 _px(o.get("vol_exec"), 6), _px(o.get("fee"), 4), o["status"], o.get("txid")]
                                for o in d["orders"]],
              {(i, 1): (GREEN if o["side"] == "buy" else RED) for i, o in enumerate(d["orders"])})
        crow = [[_ago(c.get("closed")), c["pair"], _px(c["entry"]), _px(c.get("exit")), _px(c.get("qty"), 6),
                 _usd(c.get("net")), _px(c.get("fees"), 4), c.get("reason") or ""] for c in d["closed"]]
        _fill(self.tbl_closed, crow, {(i, 5): (GREEN if float(c.get("net") or 0) >= 0 else RED)
                                      for i, c in enumerate(d["closed"])})

    def _show_pos_detail(self):
        r = self.tbl_pos.currentRow()
        a = self._selected_agent()
        if not a or not (0 <= r < len(self._ord_pos_rows)):
            return
        pid = self._ord_pos_rows[r]
        d = load_agent_orders(a["id"])
        pos = next((p for p in d["positions"] if p["id"] == pid), None)
        w = ((d["watch"] or {}).get("positions") or {}).get(pid, {})
        if not pos:
            return
        snap = pos.get("entry_snapshot") or {}
        late = " <b style='color:%s'>(late snapshot — taken after entry)</b>" % AMBER if snap.get("late_snapshot") else ""
        lines = [f"<b>{pos['pair']}</b> · opened {_ago(pos.get('opened'))} · {_px(pos['qty'], 6)} @ {_px(pos['entry'])} · entry fee ${_px(pos.get('fee_entry'), 4)}"
                 f" · order <code>{(pos.get('note') or '').split(' ')[2] if 'buy' in (pos.get('note') or '') else ''}</code>",
                 f"Bid {_px(w.get('bid'))} / ask {_px(w.get('ask'))} · breakeven <b>{_px(w.get('breakeven_price'))}</b> · "
                 f"min-profit price <b>{_px(w.get('min_profit_price'))}</b> · exit zone: {w.get('exit_zone_why', '—')}",
                 f"Snapshot: {', '.join(snap.get('strategy_intervals') or []) or 'none'}{late}"]
        cand = w.get("candidates") or []
        tbl = (f"<table cellpadding='3'><tr style='color:{GREY}'><td>interval</td><td>channel</td><td>price set</td>"
               "<td>sell price</td><td>net % if sold</td><td>profit check</td></tr>")
        for c in cand:
            ok = c.get("passes")
            tbl += (f"<tr><td>{c['interval']}</td><td>{c['channel']}</td><td>{c['set']}</td><td>{_px(c['price'])}</td>"
                    f"<td style='color:{GREEN if c['net_pct'] >= 0 else RED}'>{c['net_pct']:+.2f}%</td>"
                    f"<td style='color:{GREEN if ok else RED}'><b>{'PASS' if ok else 'fail'}</b></td></tr>")
        tbl += "</table>"
        if not cand:
            tbl = f"<i style='color:{GREY}'>no stored sell prices yet</i>"
        sw = w.get("stop") or {}
        if sw.get("enabled"):
            where = {"ARM": "armed", "armed": "armed", "held": "held", "watching": "watching",
                     "withdrawn": "withdrawn", "no-stop": "no stop level"}.get(sw.get("action"), sw.get("action", "—"))
            trail = (f" · trail high {_px(sw.get('trail_high'))} @ {sw.get('trail_pct')}% of entry"
                     if sw.get("mode") == "trailing" else "")
            tail = (f"<p style='color:{AMBER}'>Stop loss <b>{sw.get('mode')}</b>: level "
                    f"<b>{_px(sw.get('level'))}</b> (stop {_px(sw.get('stop'))} · {sw.get('stop_src')}), "
                    f"arm band {_px(sw.get('arm_level'))} ({sw.get('watch_pct')}%){trail} — "
                    f"<b>{'TRIGGERED' if sw.get('triggered') else ('near' if sw.get('near') else 'watching')}</b> "
                    f"({where}). Rests a maker limit; a profitable strategy exit cancels it and takes the profit.</p>")
        else:
            tail = (f"<p style='color:{GREY}'>Sells when bid ≥ the lowest PASSING stored price, or when a strategy channel is in its "
                    f"sell zone and the bid itself is profitable. No stop armed.</p>")
        with keep_scroll(self.pos_detail):
            self.pos_detail.setHtml("<br>".join(lines) + "<br>" + tbl + tail)

    # ---------------------------------------------------------------- decisions tab
    def _render_decisions(self, a):
        if not hasattr(self, "dec_why") or not a:
            return
        decs = load_agent_decisions(a["id"], limit=25)
        pair = (a.get("pairs") or [None])[0]
        sig = load_signal(pair) or {}
        price = next((c.get("currPrice") for c in (sig.get("channels") or {}).values()
                      if c.get("currPrice") not in (None, "", 0)), None)
        last = decs[0] if decs else None
        if last:
            col = {"BUY": GREEN, "SELL": RED}.get(str(last.get("action")), GREY)
            head = (f"<b>{a.get('name')}</b> · last decision "
                    f"<b style='color:{col}'>{last.get('action')}</b> — {last.get('reason') or ''} "
                    f"<span style='color:{GREY}'>({_ago(last.get('ts'))})</span>")
        else:
            head = f"<b>{a.get('name')}</b> · <span style='color:{GREY}'>no decisions recorded yet</span>"
        if price not in (None, "", 0):
            head += (f" · <span style='color:{GREY}'>market {_px(price)}"
                     + (f" @ {sig.get('pair')}" if sig.get("pair") else "") + "</span>")
        self.dec_summary.setText(head)

        # these four browsers are repainted on the 5s decisions tick — hold scroll
        with keep_scroll(self.dec_why, self.dec_chain, self.dec_market, self.dec_scenarios):
            self.dec_why.setHtml(_why_html(last))
            pol = {}
            try:
                pol = self._agents_mod()[0].policy_for(a["id"])
            except Exception:
                pass
            self.dec_chain.setHtml(_chain_html(strength_chain(pol, sig)))

            rows, colors = [], {}
            for i, d in enumerate(decs):
                act = str(d.get("action") or "")
                rows.append([_ago(d.get("ts")), act, d.get("pair") or "—", d.get("channel") or "—",
                             d.get("reason") or ""])
                colors[(i, 1)] = {"BUY": GREEN, "SELL": RED}.get(act, GREY)
            _fill(self.tbl_decisions, rows, colors)

            set_html_keep_scroll(self.dec_market, _market_html(sig))
            self.dec_scenarios.setHtml(_scenarios_html(sig))
