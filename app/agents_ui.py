"""Agents page for the BlackHorse desktop app (mixin for BlackHorse).

Left: agent list. Right: tabs per agent —
  Overview  pretty summary + last decision
  Settings  labelled inputs grouped (where it trades / entry / exit), two columns
  Orders    working limit orders, open positions with live bid/net + exit detail,
            recent exchange orders (DB), closed trades
Everything on the right scrolls instead of squishing, and the split is draggable.
"""
import json, time, sqlite3
from pathlib import Path

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
                             QPushButton, QListWidget, QListWidgetItem, QTabWidget, QTableWidget,
                             QTableWidgetItem, QHeaderView, QSplitter, QGroupBox, QScrollArea,
                             QComboBox, QTextBrowser, QTextEdit, QAbstractItemView, QFrame,
                             QCheckBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

BH = Path.home() / "blackhorse"
GREEN, RED, GREY, AMBER = "#16a34a", "#dc2626", "#9ca3af", "#d97706"


def _intervals_to_slots(intervals):
    iv = [str(x).strip() for x in (intervals or [])]
    has5 = any(x == '5m' for x in iv)
    slots = []
    for x in iv:
        if x == '5m':
            slots.append('ripple5')
        elif x == '15m':
            slots.append('wave15' if has5 else 'ripple')
        elif x == '60m':
            slots.append('tide60' if has5 else 'wave')
        elif x == '240m' or x == '1440m':
            slots.append('tide')
        else:
            slots.append(x)
    seen = set(); out = []
    for s in slots:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def _slots_to_intervals(slots):
    m = {'ripple':'15m','wave':'60m','tide':'240m','ripple5':'5m','wave15':'15m','tide60':'60m'}
    iv = []
    for s in (slots or []):
        iv.append(m.get(s, s))
    seen = set(); out = []
    for x in iv:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


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
    ("Where it trades", [
        ("pairs", "Pairs", "list", "agent", "Comma list, e.g. DOT/USD,ETH/USD. Empty = every pair."),
        ("strategy", "Strategy", "combo:swing,daytrade,custom", "policy", "Interval combo (editable). swing/daytrade are examples."),
        ("intervals", "Intervals", "list", "policy", "SMALLEST = the entry (trading) lane. "
         "Every OTHER interval listed becomes a strength-chain rung (60m -> 240m -> 1440m) "
         "that must clear its own band before this agent may buy/sell. Listing an interval "
         "turns its rung ON; removing it turns it OFF. e.g. 15m,60m,240m — or just 15m for "
         "the default 60m gate."),
        ("capital", "Capital $", "float", "agent", "Budget this agent may size from (bounded by real free cash)."),
    ]),
    ("Entry (buy)", [
        ("entry_strength_max", "Buy when strength ≤", "float", "policy", "0 = floor, 1 = roof."),
        ("entry_order_type", "Order type", "combo:limit,market", "policy", "limit = rests at the entry price (maker fee)."),
        ("entry_expiry_seconds", "Limit expires (s)", "int", "policy", "Unfilled limit orders are cancelled after this."),
        ("min_reward_risk", "Min R:R", "float", "policy", "Target distance vs invalidation distance."),
        ("risk_per_trade_pct", "Risk % of capital", "float", "policy", "Used for sizing (fees included)."),
        ("max_position_pct", "Max position %", "float", "policy", "Largest single position as % of capital."),
        ("stop_max_pct", "Sizing stop % ≤", "float", "policy",
         "Caps how far the structural stop sits from entry. Used only if you enable stop loss."),
    ]),
    ("Exit (sell) — profit exit", [
        ("exit_strength_min", "Sell zone strength ≥", "float", "policy", "Strategy 'sell time' band."),
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
]


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
        cr.addWidget(QLabel("kind"))
        self.agent_kind = QComboBox()
        self.agent_kind.addItems(["trading", "youtube"])
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
        br.addWidget(b_on)
        br.addWidget(b_off)
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

    # ---------------------------------------------------------------- list / selection
    def refresh_agents(self):
        try:
            agents, _ = self._agents_mod()
            rows = agents.list_agents()
            cur = self._selected_agent()
            cur_id = cur["id"] if cur else None
            self._agent_rows = rows
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
            self._render_agent()
            try:
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
            except Exception:
                pass
        except Exception as e:
            self.house_view.setPlainText(f"agents error: {e}")

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
            self.ag_overview.setHtml("<i>No agents yet — create one above.</i>")
            return
        agents, _ = self._agents_mod()
        try:
            pol = agents.policy_for(a["id"])
        except Exception as e:
            self.ag_overview.setHtml(f"<pre>error: {e}</pre>")
            return
        self._render_overview(a, pol)
        self._render_settings(a, pol)
        self._refresh_orders_tab()

    def _render_overview(self, a, pol):
        on = a.get("enabled")
        d = load_agent_orders(a["id"])["last_decision"]
        last = (f"<b>{d['action']}</b> — {d.get('reason') or ''} <span style='color:{GREY}'>({_ago(d['ts'])})</span>"
                if d else f"<span style='color:{GREY}'>no decision recorded yet</span>")
        ivs = pol.get("strategy_intervals") or []
        entry = ivs[0] if ivs else pol.get("channels", [""])[0]
        climb = ", ".join(ivs[1:]) if len(ivs) > 1 else "off — trades on its own strength"
        rows = [("Status", f"<b style='color:{GREEN if on else GREY}'>{'ENABLED' if on else 'disabled'}</b>"),
                ("Entry interval", f"<b>{entry}</b> · climb: {climb}"),
                ("Mode", f"<b>{a.get('mode') or '-'}</b>"),
                ("Capital (total budget)", f"${a.get('capital')}" + self._budget_html(a["id"])),
                ("Pairs", ", ".join(a.get("pairs") or []) or "all pairs"),
                ("Channels", ", ".join(pol.get("channels") or []) or "—"),
                ("Require edge proof",
                 "<b>" + ("ON — only PROVEN cells" if pol.get("require_proven_edge", False)
                          else "OFF — trades unproven setups") + "</b>"),
                ("Buy when strength ≤", pol.get("entry_strength_max")),
                ("Entry order", f"{pol.get('entry_order_type', 'limit')} (expires {pol.get('entry_expiry_seconds', '-')}s)"),
                ("Sell zone strength ≥", pol.get("exit_strength_min")),
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
        # Entry lane & strength climb — the interval buttons ARE the profiles.
        pv.addWidget(self._build_interval_block(a, pol))
        for title, fields in SETTINGS:
            # skip Where it trades block (already handled)
            if title == 'Where it trades':
                continue
            g = QGroupBox(title)
            grid = QGridLayout(g)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(8)
            for i, (key, label, kind, where, tip) in enumerate(fields):
                if key in ('strategy', 'intervals', 'channels'):
                    continue  # intervals are handled by the entry/climb block
                r, c = divmod(i, 2)
                cur = a.get(key) if where == 'agent' else pol.get(key)
                lab = QLabel(label)
                lab.setToolTip(tip)
                lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if kind.startswith('combo:'):
                    w = QComboBox()
                    items = kind.split(':')[1].split(',')
                    w.addItems(items)
                    w.setCurrentText(str(cur or w.itemText(0)))
                elif kind == 'bool':
                    w = QCheckBox(label); w.setChecked(bool(cur)); w.setMinimumWidth(0)
                else:
                    txt = ','.join(cur) if isinstance(cur, list) else ('' if cur is None else str(cur))
                    w = QLineEdit(txt)
                w.setMinimumWidth(150); w.setToolTip(tip)
                if kind == 'bool':
                    grid.addWidget(w, r, 0, 1, 4)
                else:
                    grid.addWidget(lab, r, c*3); grid.addWidget(w, r, c*3+1)
                if c == 0: grid.setColumnMinimumWidth(2,24)
                self._agent_fields[key] = (w, kind, where, cur)
            grid.setColumnStretch(1,1); grid.setColumnStretch(4,1)
            pv.addWidget(g)
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
        note = QLabel("Changes apply on the trader's next cycle (≤5 min). Mode (PAPER/LIVE) is set in config/agents.json.")
        note.setStyleSheet(f"color:{GREY};font-size:11px")
        note.setWordWrap(True)
        pv.addWidget(note)
        pv.addStretch(1)
        self.ag_settings_scroll.setWidget(page)

    # ---------------------------------------------------------------- interval grid
    def _build_interval_block(self, a, pol):
        self._interval_pol = pol
        g = QGroupBox("Entry lane & strength climb — intervals are the profiles")
        box = QVBoxLayout(g)
        iv = [x for x in (pol.get('strategy_intervals') or _slots_to_intervals(pol.get('channels') or []))
              if x in INTV]
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
                b.setStyleSheet("background:#1d4ed8;color:white;font-weight:bold")
            elif _ivmin(x) < emin:
                b.setText(x)
                b.setChecked(False)
                b.setEnabled(False)
                b.setStyleSheet("color:#6b7280;background:#111827")
            elif x in self._interval_on:
                b.setText(f"✔ {x}")
                b.setChecked(True)
                b.setEnabled(True)
                b.setStyleSheet("background:#065f46;color:white")
            else:
                b.setText(f"＋ {x}")
                b.setChecked(False)
                b.setEnabled(True)
                b.setStyleSheet("")
            b.blockSignals(False)
        self._rebuild_rung_rows()
        if apply_defaults:
            self._apply_interval_defaults(entry)

    def _interval_clicked(self, x):
        agents, _ = self._agents_mod()
        def defaults_for(k):
            return [y for y in (agents.interval_defaults().get(k, {}).get('climb') or [])
                    if _ivmin(y) > _ivmin(k)]
        if x == self._interval_entry:
            nxt = [y for y in INTV if _ivmin(y) > _ivmin(x) and y in self._interval_on]
            if nxt:
                self._interval_entry = nxt[0]
                self._interval_on = [y for y in self._interval_on if y != self._interval_entry]
            else:
                return  # keep a lone entry; nothing to release onto
        elif _ivmin(x) < _ivmin(self._interval_entry):
            self._interval_entry = x
            self._interval_on = list(self._interval_defaults_climb(x))
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
        if active:
            names = ", ".join(active)
            self._interval_status.setText(
                f"<b>{entry}</b> is the entry lane (the one this agent trades). Climb ON: <b>{names}</b> — "
                f"before {entry} may buy or sell, each rung must sit in its own band (buy ≤ / sell ≥). "
                f"Click a larger interval to add or remove it from the climb.")
        else:
            self._interval_status.setText(
                f"<b>{entry}</b> is the entry lane and the climb is <b>off</b> — it trades on its own "
                f"strength alone. Click a larger interval above to add a climb rung.")
        for x in active:
            rr = QWidget()
            hb = QHBoxLayout(rr)
            hb.setContentsMargins(0, 0, 0, 0)
            old_b = self._interval_pol.get('entry_strength_max_' + x)
            old_s = self._interval_pol.get('exit_strength_min_' + x)
            lab = QLabel(f"{x} rung")
            lab.setMinimumWidth(70)
            wb = QLineEdit('' if old_b is None else str(old_b))
            ws = QLineEdit('' if old_s is None else str(old_s))
            wb.setMinimumWidth(80)
            ws.setMinimumWidth(80)
            hb.addWidget(lab)
            hb.addWidget(QLabel("buy ≤"))
            hb.addWidget(wb)
            hb.addWidget(QLabel("sell ≥"))
            hb.addWidget(ws)
            hb.addStretch(1)
            self._agent_fields['entry_strength_max_' + x] = (wb, 'float', 'policy', old_b)
            self._agent_fields['exit_strength_min_' + x] = (ws, 'float', 'policy', old_s)
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
                iv_list = _slots_to_intervals([]) or ['15m','60m','240m']
        # normalize strategy label from intervals if it matches known sets
        ivs = iv_list
        if ivs == ['15m','60m','240m'] or sorted(ivs) == sorted(['15m','60m','240m']):
            strat_lbl = 'swing'
        elif '5m' in ivs or ivs == ['5m','15m','60m'] or sorted(ivs) == sorted(['5m','15m','60m']):
            strat_lbl = 'daytrade'
        else:
            strat_lbl = strat if strat in ('swing','daytrade') else 'custom'
        # translate to slots
        slots = _intervals_to_slots(ivs)
        # write strategy/intervals/channels
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
        try:
            agents.set_override(aid, 'channels', slots)
            changed.append('channels')
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
                elif key == 'capital':
                    agents.set_capital(aid, val, allow_over=True)
                else:
                    agents.set_override(aid, key, val)
                changed.append(key)
            except Exception as e:
                errs.append(f'{key}:{e}')
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
            if self.pages[3].isVisible() and self.agent_tabs.currentWidget() is self.ag_orders_scroll:
                self._refresh_orders_tab()
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
        self.pos_detail.setHtml("<br>".join(lines) + "<br>" + tbl + tail)
