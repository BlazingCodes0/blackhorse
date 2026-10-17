"""BlackHorse desktop app — PyQt6. One window over the trading loop and host defense."""
import sys, os, subprocess, threading, time, json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# the repo root and its importable sub-packages, set up once (this used to be
# scattered through the file in several places).
BH = Path.home() / "blackhorse"
for _sub in ("trade", "memory", "skin", "app"):
    _p = str(BH / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
del _sub, _p

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QPushButton, QLabel, QTextEdit, QListWidget, QListWidgetItem, QFrame,
                               QSystemTrayIcon, QMenu, QDialog, QFormLayout, QComboBox, QDialogButtonBox,
                               QSplitter, QMessageBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QAction, QIcon
from agents_ui import AgentsMixin
import settings as _settings_mod
try:
    import psutil              # optional — used only by the OFF verification
except Exception:
    psutil = None
settings = _settings_mod

LOG = BH / "logs" / "threats.jsonl"

_APP_LOCK_FD = None


def _acquire_app_singleton() -> bool:
    """One GUI, ever. flock is auto-released by the OS when the process dies,
    so a crash never blocks the next start (self-heals). A duplicate launch
    (manual + the login autostart, say) exits 0 instead of running two windows
    that fight over config. Survives the in-app Restart button's os.execv."""
    global _APP_LOCK_FD
    import fcntl
    fd = None
    try:
        p = BH / "logs" / "app.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _APP_LOCK_FD = fd          # keep the fd open for the whole process lifetime
    return True
LOCKDOWN_FLAG = BH / "config" / "LOCKDOWN"

def _n(v, dp=4):
    """compact number print — '?' for missing"""
    try:
        return f"{float(v):.{dp}f}"
    except (TypeError, ValueError):
        return str(v)

def _curr_price(chans):
    """first real currPrice, cheapest interval first (lanes are interval-keyed)."""
    def _iv(c):
        try:
            s = str((c or {}).get("interval") or "")
            return int(s[:-1]) if s.endswith("m") and s[:-1].isdigit() else 10 ** 9
        except Exception:
            return 10 ** 9
    for c in sorted(chans.values(), key=_iv):
        p = (c or {}).get("currPrice")
        if p not in (None, "", 0):
            return p
    return "?"

import calendar as _calendar

_SCEN_WHY = {
    "1": "why not buy: RSI oversold but score gate still failing — wait for buyScore ≥ 0.60 to hold, that's the knife having stopped falling.",
    "2": "why not buy: RSI is oversold but the spread is still weakening — classic knife, the gate is right to refuse. Wait for the spread to stop falling.",
    "3": "why not buy: this is the green-light scenario — spreadMA bullish + trend up. If it drifted off, patient again.",
    "4": "why not buy: the floor broke and the range invalidated — a floor that won't hold isn't a floor. No buy, no edge.",
    "5": "why not buy: 15m strength is asleep at ~0. Zero isn't a buy signal — it's the tape holding its breath. Wait for 15m rising off 0 + RSI≤35 before any fill.",
    "5/6": "why not buy: higher timeframes say the floor is worth a look, but 15m is quiet. We let 15m strength-rising-from-0 *lead*, not chase 240m.",
    "6": "why not buy: wait for the 15m up-tick — the 60m/240m scores are nearly there, but the trigger lane stays 15m.",
}

def _fmt_ts(ts):
    """2026-10-06T20:48:02Z -> Oct 06 8:48 PM (local)"""
    try:
        lt = time.localtime(_calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")))
        return time.strftime("%b %d %-I:%M %p", lt)
    except Exception:
        return str(ts)

def _ago(ts):
    """iso ts -> '45s ago' / '3m ago' / '2h ago'"""
    try:
        ep = _calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
        d = max(0, int(time.time() - ep))
        if d < 60: return f"{d}s ago"
        if d < 3600: return f"{d//60}m ago"
        if d < 86400: return f"{d//3600}h ago"
        return f"{d//86400}d ago"
    except Exception:
        return "?"

class BlackHorse(AgentsMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BlackHorse — multi-agent money system")
        from PyQt6.QtGui import QIcon
        self.setWindowIcon(QIcon(str(Path.home() / "blackhorse" / "logo.png")))
        self.resize(1100, 700)
        self.setMinimumSize(700, 450)

        root = QWidget(); lay = QHBoxLayout(root); self.setCentralWidget(root)

        # ---------- sidebar ----------
        side = QVBoxLayout()
        from PyQt6.QtWidgets import QLabel as _L
        from PyQt6.QtGui import QPixmap
        logo_lbl = _L()
        px = QPixmap(str(Path.home() / "blackhorse" / "logo.png")).scaled(170, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        logo_lbl.setPixmap(px)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(logo_lbl)
        title = QLabel("local security daemon"); title.setFont(QFont("Arial", 8))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:#6b7280")
        side.addWidget(title)
        self.nav = QListWidget(); self.nav.setObjectName("nav")
        for page in ["Dashboard", "Skin (Defense)", "Trade (Exchange)", "Agents", "Heart (Filter)"]:
            self.nav.addItem(QListWidgetItem(page))
        self.nav.setMaximumWidth(180)
        self.nav.currentRowChanged.connect(self.switch_page)
        side.addWidget(self.nav)
        side_w = QWidget(); side_w.setLayout(side); side_w.setFixedWidth(200)
        side_w.setObjectName("sidebar")
        lay.addWidget(side_w)

        # ---------- content ----------
        self.pages = {}
        content = QWidget(); cl = QVBoxLayout(content); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._build_master_bar())
        cl.addWidget(self._build_dashboard())
        cl.addWidget(self._build_skin())
        cl.addWidget(self._build_trade())
        cl.addWidget(self._build_agents())
        cl.addWidget(self._build_heart())
        lay.addWidget(content)
        self.nav.setCurrentRow(1)

        # ---------- tray: closing the window hides, it does not quit ----------
        self._quitting = False
        self.tray = None
        self._setup_tray()

        # live-refresh the threat log
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_log); self.timer.start(10000)
        self.timer3 = QTimer(self); self.timer3.timeout.connect(self.refresh_master); self.timer3.start(10000)
        self.timer5 = QTimer(self); self.timer5.timeout.connect(self.refresh_dashboard); self.timer5.start(10000)
        import os as _os
        if _os.environ.get("BLACKHORSE_PROFILE"):
            import time as _time
            for _name in ("refresh_log", "refresh_master",
                          "refresh_dashboard", "refresh_trade",
                          "refresh_agents", "refresh_heart", "refresh_active_defense",
                          "refresh_skin_status"):
                _fn = getattr(self, _name, None)
                if callable(_fn):
                    def _wrap(fn=_fn, name=_name):
                        t0 = _time.perf_counter()
                        r = fn()
                        dt = (_time.perf_counter() - t0) * 1000
                        if dt > 5:
                            print(f"[profile] {name} took {dt:.1f}ms", flush=True)
                        return r
                    setattr(self, _name, _wrap)
        # investigate everything as soon as the app starts (only if master is ON)
        QTimer.singleShot(1500, self.run_sweep)
        QTimer.singleShot(500, self.refresh_skin_status)
        QTimer.singleShot(300, self.refresh_master)

    # ================= MASTER ON/OFF =================
    def _build_master_bar(self):
        bar = QWidget()
        bar.setStyleSheet("background:#111827; border-bottom:1px solid #1f2937;")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 8, 12, 8)
        self.master_dot = QLabel("●")
        self.master_dot.setStyleSheet("font-size:22px;color:#22c55e")
        self.master_label = QLabel("SYSTEM ARMED")
        self.master_label.setStyleSheet("font-size:15px;font-weight:bold;color:#e5e7eb")
        self.master_detail = QLabel("")
        self.master_detail.setStyleSheet("color:#9ca3af;font-size:11px")
        self.btn_master = QPushButton("ARM")
        self.btn_master.setMinimumHeight(34)
        self.btn_master.setMinimumWidth(150)
        self.btn_master.clicked.connect(self.toggle_master)
        h.addWidget(self.master_dot); h.addWidget(self.master_label)
        h.addWidget(self.master_detail); h.addStretch(1); h.addWidget(self.btn_master)
        self.master_bar = bar
        return bar

    def _master_on(self) -> bool:
        return bool(settings.load().get("master_enabled", True))

    def toggle_master(self):
        """Flip the single true ON/OFF, write it, and verify it took."""
        new = not self._master_on()
        cfg = settings.load(); cfg["master_enabled"] = new; settings.save(cfg)
        self._apply_master_visual()
        # verify the write is what we think it is
        import json
        disk = json.loads((Path.home()/"blackhorse"/"config"/"skin_settings.json").read_text())
        actual = bool(disk.get("master_enabled", True))
        self.master_detail.setText(
            "verified: master_enabled=%s on disk" % actual +
            ("" if actual == new else "  ⚠ WRITE FAILED"))

    def _apply_master_visual(self):
        on = self._master_on()
        if on:
            self.master_dot.setStyleSheet("font-size:22px;color:#22c55e")
            self.master_label.setText("SYSTEM ARMED")
            self.master_label.setStyleSheet("font-size:15px;font-weight:bold;color:#e5e7eb")
            self.btn_master.setText("DISARM (turn everything OFF)")
            self.btn_master.setStyleSheet("background:#7f1d1d;color:white;font-weight:bold")
            self.master_bar.setStyleSheet("background:#111827;border-bottom:1px solid #1f2937;")
        else:
            self.master_dot.setStyleSheet("font-size:22px;color:#ef4444")
            self.master_label.setText("SYSTEM DISARMED — TRULY OFF")
            self.master_label.setStyleSheet("font-size:15px;font-weight:bold;color:#fca5a5")
            self.btn_master.setText("ARM (turn everything ON)")
            self.btn_master.setStyleSheet("background:#14532d;color:white;font-weight:bold")
            self.master_bar.setStyleSheet("background:#3f0d0d;border-bottom:1px solid #7f1d1d;")

    def refresh_master(self):
        self._apply_master_visual()
        on = self._master_on()
        cfg = settings.load()
        parts = [
            "engines " + ("yes" if on else "OFF"),
            "whitecells " + ("yes" if on else "OFF"),
            "local model " + ("yes" if (on and cfg.get("llm_enabled")) else "OFF"),
            "brain " + ("yes" if (on and cfg.get("brain_enabled")) else "OFF"),
        ]
        self.master_detail.setText(" · ".join(parts))
        if self.tray:
            self.tray.setToolTip("BlackHorse — " + ("ARMED" if on else "DISARMED (truly off)"))

    # ================= TRAY =================
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(Path.home() / "blackhorse" / "logo.png"))
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        a_show = QAction("Show window", self); a_show.triggered.connect(self._restore)
        a_master = QAction("Toggle master ON/OFF", self); a_master.triggered.connect(self.toggle_master)
        a_quit = QAction("Quit BlackHorse", self); a_quit.triggered.connect(self._quit_app)
        menu.addAction(a_show); menu.addAction(a_master); menu.addSeparator(); menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._restore() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _restore(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _quit_app(self):
        self._quitting = True
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        # Closing the window must NOT stop the app — only Quit does.
        if self._quitting or not self.tray:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage("BlackHorse still running",
                              "Window hidden. Use the tray icon to show it or Quit. "
                              + ("System is ARMED." if self._master_on() else "System is DISARMED (truly off)."),
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    def _build_dashboard(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)

        # header: title + the one action
        head = QHBoxLayout()
        titles = QVBoxLayout(); titles.setSpacing(0)
        t = QLabel("Dashboard")
        t.setStyleSheet("font-size:20px;font-weight:bold;color:#e5e7eb;")
        sub = QLabel("system overview \u00b7 OFF verification")
        sub.setStyleSheet("color:#9ca3af;font-size:12px;")
        titles.addWidget(t); titles.addWidget(sub)
        head.addLayout(titles); head.addStretch(1)
        self.btn_verify = QPushButton("Verify OFF now")
        self.btn_verify.setMinimumHeight(34)
        self.btn_verify.clicked.connect(self.verify_off)
        head.addWidget(self.btn_verify, 0, Qt.AlignmentFlag.AlignTop)
        btn_restart_app = QPushButton("Restart")
        btn_restart_app.setMinimumHeight(34)
        btn_restart_app.clicked.connect(self.restart_app)
        head.addWidget(btn_restart_app, 0, Qt.AlignmentFlag.AlignTop)
        btn_shutdown_app = QPushButton("Shutdown")
        btn_shutdown_app.setMinimumHeight(34)
        btn_shutdown_app.setStyleSheet("background:#7f1d1d;color:white;")
        btn_shutdown_app.clicked.connect(self.shutdown_app)
        head.addWidget(btn_shutdown_app, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)

        def card(title):
            box = QFrame(); box.setObjectName("card")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(16, 12, 16, 12); lay.setSpacing(6)
            cap = QLabel(title)
            cap.setStyleSheet("color:#9ca3af;font-size:11px;letter-spacing:1px;")
            lay.addWidget(cap)
            return box, lay

        pnl_card, pc = card("TOTAL P&L")
        self.dash_pnl = QLabel("\u2014")
        self.dash_pnl.setFont(QFont("Monospace", 16))
        self.dash_pnl.setWordWrap(True)
        pc.addWidget(self.dash_pnl)
        self.dash_pnl_desc = QLabel("")
        self.dash_pnl_desc.setFont(QFont("Monospace", 9))
        self.dash_pnl_desc.setWordWrap(True)
        self.dash_pnl_desc.setStyleSheet("color:#9aa0a6")
        pc.addWidget(self.dash_pnl_desc)
        self.dash_orders = QLabel("")
        self.dash_orders.setFont(QFont("Monospace", 9))
        self.dash_orders.setWordWrap(True)
        pc.addWidget(self.dash_orders)
        v.addWidget(pnl_card)

        trade_card, tc = card("TRADE STATE")
        self.dash_trade = QLabel("\u2014")
        self.dash_trade.setFont(QFont("Monospace", 9))
        self.dash_trade.setWordWrap(True)
        tc.addWidget(self.dash_trade)
        v.addWidget(trade_card)

        status_card, sc = card("OFF VERIFICATION")
        self.dash_status = QLabel("checking\u2026")
        self.dash_status.setFont(QFont("Monospace", 10))
        self.dash_status.setWordWrap(True)
        sc.addWidget(self.dash_status)
        v.addWidget(status_card)

        v.addStretch(1)
        self.pages[0] = w; w.hide(); return w

    def refresh_dashboard(self):
        try:                                   # total P&L, pretty, attributed per agent
            import portfolio
            pnl = portfolio.pnl_by_agent()
            tot = pnl["total"]
            self.dash_pnl.setText(
                f"{'+' if tot >= 0 else '-'}${abs(tot):.4f}   "
                f"(realized {pnl['realized']:+.4f}  \u00b7  unrealized {pnl['unrealized']:+.4f})")
            self.dash_pnl.setStyleSheet(f"color:{'#22c55e' if tot >= 0 else '#ef4444'}")
            rows = sorted(pnl["agents"].items(), key=lambda kv: kv[1]["total"], reverse=True)
            desc = [f"{a}: {v['total']:+.4f}  (real {v['realized']:+.4f} \u00b7 unreal "
                    f"{v['unrealized']:+.4f} \u00b7 {v['closed']} closed / {v['open']} open)"
                    for a, v in rows] or ["no positions yet \u2014 every agent at 0.0000"]
            self.dash_pnl_desc.setText("each agent's contribution \u2014 " + "\n".join(desc))
            self.dash_pnl.setToolTip("Contribution to total P&L by agent:\n" + "\n".join(desc))
            opens = pnl.get("open_positions", [])
            orow = []
            for o in opens:
                upl = o["unrealized"]
                color = "#22c55e" if upl > 0 else ("#ef4444" if upl < 0 else "#9aa0a6")
                tag = "win" if upl > 0 else ("loss" if upl < 0 else "flat")
                base = o["entry"] * o["qty"]
                pct = (upl / base * 100) if base else 0.0
                txt = (f"{o['id']} {o['agent']} {o['side']} {o['qty']:.4f} "
                       f"@{o['entry']:.4f}\u2192{o['mark']:.4f}  {upl:+.4f} ({pct:+.2f}%) {tag}")
                orow.append(f"<span style='color:{color}'>{txt}</span>")
            if orow:
                self.dash_orders.setText("<span style='color:#9ca3af'>active orders "
                                         "(open \u00b7 marked to last price)</span><br>"
                                         + "<br>".join(orow))
            else:
                self.dash_orders.setText("<span style='color:#9ca3af'>active orders: none</span>")
        except Exception as e:
            self.dash_pnl.setText("\u2014")
            self.dash_pnl_desc.setText(f"P&L unavailable: {e}")
            self.dash_orders.setText("")
        try:
            s = json.loads((BH/"logs"/"loop_state.json").read_text())
            if hasattr(self, 'loop_status'):
                self.loop_status.setText("loop RUNNING (backend night-shift)" if s.get('heartbeat')=='ok' else "loop HALTED (backend no heartbeat)")
            sig_path = BH/"logs"/"signal_latest.json"
            sig = json.loads(sig_path.read_text()) if sig_path.exists() else {}
            ch = sig.get("channels", {})

            def _ivmin_c(c):
                try:
                    s = str((c or {}).get("interval") or "")
                    return int(s[:-1]) if s.endswith("m") and s[:-1].isdigit() else 10 ** 9
                except Exception:
                    return 10 ** 9
            _lanes = sorted(ch.values(), key=_ivmin_c)
            entry = _lanes[0] if _lanes else {}
            htf = _lanes[-1] if _lanes else {}

            def _trend(c):
                return c.get("trendState") or (c.get("marketResult") or {}).get("trendState") or "?"

            def _level(c, side):
                adv = c.get("advisor") or {}
                val = adv.get(side)
                if adv.get(f"{side}_missing") or val in (None, 0):
                    return f"\u2014 (interval has no {side} level)"
                return val

            lines = [
              f"loop: {'ok' if s.get('heartbeat')=='ok' else 'halted'} \u00b7 interval {s.get('ts','?')[11:16]} \u00b7 decision {s.get('decision')} \u2014 {s.get('reason')}",
              f"bot signal: {sig.get('pair','?')} @ {entry.get('currPrice')} \u00b7 {entry.get('interval','?')} str {entry.get('strength')} \u00b7 trend {_trend(entry)} \u00b7 signal {entry.get('rangeSignal')}",
              f"HTF {htf.get('interval','?')}: trend {_trend(htf)} \u00b7 rangeSignal {htf.get('rangeSignal')} \u00b7 buy {_level(htf,'buy')} \u00b7 sell {_level(htf,'sell')}",
              f"marks: {[m.get('closed') if isinstance(m,dict) else m for m in s.get('marks',[])]}",
            ]
            hb = BH/"logs"/"heart_orders.json"
            if hb.exists():
                try:
                    orders = json.loads(hb.read_text())
                    lines.append(f"orders logged: {len(orders) if isinstance(orders,list) else '?' }")
                except Exception:
                    lines.append("orders logged: ?")
            else:
                lines.append("orders logged: 0")
            self.dash_trade.setText("\n".join(lines))
        except Exception as e:
            self.dash_trade.setText(f"unavailable: {e}")

    def restart_app(self):
        """Re-exec the dashboard in place, keeping the same process/args."""
        import os, sys
        self._quitting = True
        if self.tray:
            self.tray.hide()
        python = sys.executable or "python3"
        os.execv(python, [python] + sys.argv)

    def shutdown_app(self):
        """Quit the dashboard cleanly (defense service, if enabled, keeps running)."""
        self._quit_app()

    def verify_off(self):
        """Prove, from the things that actually run, that nothing is active.

        Checks:
          * the on-disk master flag
          * no defense/systemd service cycling
          * the brain budget is untouched
          * no child python defense/whitecell/brain process is alive
        """
        s = settings.load()
        master = bool(s.get("master_enabled", True))
        lines = [f"master_enabled (on disk) : {'ON' if master else 'OFF'}"]
        lines.append(f"engines                  : {'on' if master and any(s['engines'].values()) else 'OFF'}")
        lines.append(f"local fallback model     : {'on' if master and s.get('llm_enabled') else 'OFF'}")
        lines.append(f"whitecell escalation     : {'on' if master and s.get('whitecells_may_escalate') else 'OFF'}")
        # systemd service
        st = self._systemctl("is-active", "blackhorse-defense.service").stdout.strip() or "not set up"
        lines.append(f"defense service          : {st}")
        # stray worker processes
        if psutil is None:
            lines.append("stray defense workers    : (psutil unavailable)")
        else:
            try:
                stray = []
                for p in psutil.process_iter(["pid", "cmdline"]):
                    c = " ".join(p.info.get("cmdline") or [])
                    if ("blackhorse" in c and any(k in c for k in ("defense.py", "whitecells.py", "brain.py", "mastermind.py"))
                            and "main.py" not in c):
                        stray.append(f"pid {p.info['pid']}")
                lines.append(f"stray defense workers    : {', '.join(stray) if stray else 'none'}")
            except Exception as e:
                lines.append(f"stray defense workers    : (psutil unavailable: {e})")
        lines.append(f"stale LOCKDOWN flag      : {'present' if LOCKDOWN_FLAG.exists() else 'absent'}")

        if master:
            verdict = "STATUS: ARMED — defense is active."
            color = "#f59e0b"
        else:
            truly = (st in ("inactive", "not set up", "failed")) and "stray defense workers    : none" in "\n".join(lines)
            verdict = ("STATUS: TRULY OFF — master disarmed and no defense worker is running."
                       if truly else
                       "STATUS: master OFF, but something is still running above — investigate.")
            color = "#22c55e" if truly else "#ef4444"
        self.dash_status.setText(verdict + "\n\n" + "\n".join(lines))
        self.dash_status.setStyleSheet(f"color:{color}")

    def _on_edge_rules_toggle(self, _state):
        cfg = getattr(self, "edge_rules_cfg", {})
        cfg["enabled"] = bool(self.cb_edge_rules.isChecked())
        (Path.home()/"blackhorse"/"config"/"edge_rules.json").write_text(json.dumps(cfg, indent=2))

    def _on_edge_minutes_changed(self, val):
        cfg = getattr(self, "edge_rules_cfg", {})
        cfg["every_minutes"] = int(val)
        (Path.home()/"blackhorse"/"config"/"edge_rules.json").write_text(json.dumps(cfg, indent=2))

    def run_edge_rules_update(self):
        import subprocess as _sp, time as _t, json as _j
        cfg = getattr(self, "edge_rules_cfg", {})
        if not cfg.get("enabled", True):
            return
        now = _t.time()
        minutes = int(cfg.get("every_minutes", 60))
        last = getattr(self, "_last_edge_rules_run", 0)
        if now - last < minutes * 60:
            return
        self._last_edge_rules_run = now
        def _work():
            r = _sp.run([sys.executable, str(Path.home()/"blackhorse"/"trade"/"edge_rules_update.py")],
                        capture_output=True, text=True, timeout=180)
            (Path.home()/"blackhorse"/"logs"/"edge_rules_run.log").write_text((r.stdout or "") + "\n=== STDERR ===\n" + (r.stderr or ""))
        threading.Thread(target=_work, daemon=True).start()

    # ================= TRADE (exchange + autonomous) =================
    def _trade_mod(self):
        import sys as _s
        if str(Path.home()/"blackhorse"/"trade") not in _s.path:
            _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
        import exchanges, engine
        return exchanges, engine

    def _build_trade(self):
        from PyQt6.QtWidgets import QScrollArea, QCheckBox
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("TRADE — exchange connection & autonomous trading"))

        # ---- prominent top actions ----
        top = QHBoxLayout()
        b_refresh_top = QPushButton("↻ Refresh")
        b_refresh_top.clicked.connect(self.refresh_trade)
        top.addWidget(b_refresh_top); top.addStretch(1)
        v.addLayout(top)

        # ---- exchange selector ----
        exc_row = QHBoxLayout(); exc_row.addWidget(QLabel("Exchange:"))
        self.exc_combo = QComboBox()
        self.exc_combo.currentTextChanged.connect(self.on_exchange_selected)
        exc_row.addWidget(self.exc_combo)
        self.btn_use = QPushButton("Set active"); self.btn_use.clicked.connect(self.use_exchange)
        self.btn_connect = QPushButton("Enter credentials…"); self.btn_connect.clicked.connect(self.open_connect_dialog)
        exc_row.addWidget(self.btn_use); exc_row.addWidget(self.btn_connect)
        v.addLayout(exc_row)

        self.exc_status = QLabel("credentials: —")
        v.addWidget(self.exc_status)
        self.exc_detail = QTextEdit(); self.exc_detail.setReadOnly(True); self.exc_detail.setMaximumHeight(150)
        self.exc_detail.setFont(QFont("Monospace", 9))
        v.addWidget(QLabel("Expected connection:")); v.addWidget(self.exc_detail)

        # ---- autonomous toggle ----
        v.addWidget(QLabel("— Autonomy (brain decides; Heart gates; nothing fires while OFF) —"))
        auto_row = QHBoxLayout()
        self.cb_auto = QCheckBox("AUTONOMOUS trading ON")
        self.cb_auto.stateChanged.connect(self.toggle_autonomous)
        auto_row.addWidget(self.cb_auto)
        self.auto_state = QLabel("OFF")
        self.auto_state.setStyleSheet("color:#f59e0b;font-weight:bold")
        auto_row.addWidget(self.auto_state); auto_row.addStretch(1)
        v.addLayout(auto_row)

        # ---- mode: PAPER vs LIVE (explicit, separate from autonomy) ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Money:"))
        self.cb_live = QCheckBox("LIVE (real orders)")
        self.cb_live.stateChanged.connect(self.toggle_live)
        mode_row.addWidget(self.cb_live)
        self.mode_state = QLabel("PAPER")
        self.mode_state.setStyleSheet("color:#22c55e;font-weight:bold")
        mode_row.addWidget(self.mode_state); mode_row.addStretch(1)
        v.addLayout(mode_row)
        self.auto_warning = QLabel(
            "Autonomous stays locked until: real credentials + Heart gate + a fee-honest "
            "backtest + paper period. The switch will refuse to arm until those pass.")
        self.auto_warning.setWordWrap(True); self.auto_warning.setStyleSheet("color:#9ca3af;font-size:11px")
        v.addWidget(self.auto_warning)

        # ---- treasury (the "100") ----
        v.addWidget(QLabel("— Treasury (capital agents may deploy) —"))
        self.treasury_label = QLabel("—")
        self.treasury_label.setFont(QFont("Monospace", 9))
        v.addWidget(self.treasury_label)
        tr = QHBoxLayout()
        tr.addWidget(QLabel("amount"))
        self.treasury_input = QLineEdit(); self.treasury_input.setPlaceholderText("100")
        self.treasury_input.setMaximumWidth(120)
        tr.addWidget(self.treasury_input)
        b_alloc = QPushButton("Allocate"); b_alloc.clicked.connect(lambda: self.treasury_action("allocate"))
        b_res = QPushButton("Set reserve"); b_res.clicked.connect(lambda: self.treasury_action("reserve"))
        tr.addWidget(b_alloc); tr.addWidget(b_res); tr.addStretch(1)
        v.addLayout(tr)

        # ---- memory ----
        self.memory_label = QLabel("memory: —")
        self.memory_label.setStyleSheet("color:#9ca3af")
        v.addWidget(self.memory_label)

        # ---- news ----
        v.addWidget(QLabel("— Market news / macro (feeds the brain) —"))
        self.news_label = QLabel("loading…"); self.news_label.setWordWrap(True)
        v.addWidget(self.news_label)
        self.news_view = QTextEdit(); self.news_view.setReadOnly(True); self.news_view.setMaximumHeight(120)
        self.news_view.setFont(QFont("Monospace", 9))
        v.addWidget(self.news_view)

        # ---- signal ----
        v.addWidget(QLabel("— SignalBot signal (via sidecar; real run, no reimplementation) —"))
        sig_row = QHBoxLayout()
        b_sig = QPushButton("▶ Run SignalBot now")
        b_sig.clicked.connect(self.run_signal_now)
        sig_row.addWidget(b_sig)
        self.signal_status = QLabel("—")
        sig_row.addWidget(self.signal_status); sig_row.addStretch(1)
        v.addLayout(sig_row)
        row_int = QHBoxLayout(); row_int.addWidget(QLabel("intervals"))
        self._signal_setup_path = Path.home()/"blackhorse"/"config"/"signal_setup.json"
        try:
            _sig_cfg = json.loads(self._signal_setup_path.read_text())
        except Exception:
            _sig_cfg = {}
        self.interval_input = QLineEdit(",".join(_sig_cfg.get("channels") or ["60m", "240m", "1440m"])); self.interval_input.setPlaceholderText("e.g. 60m,15m,60m")
        row_int.addWidget(self.interval_input)
        row_int.addWidget(QLabel("pair"))
        self.signal_pair_combo = QComboBox()
        try:
            agents_mod, _ = self._agents_mod()
            pairs = sorted({p for ag in agents_mod.list_agents() for p in (ag.get('pairs') or [])}) or ["DOT/USD"]
        except Exception:
            pairs = ["DOT/USD"]
        self.signal_pair_combo.addItems(pairs)
        try:
            saved_pair = _sig_cfg.get("pair")
            if saved_pair and saved_pair in pairs:
                self.signal_pair_combo.setCurrentText(saved_pair)
        except Exception:
            pass
        row_int.addWidget(self.signal_pair_combo)
        b_save_sig = QPushButton("Save signal setup")
        b_save_sig.clicked.connect(self._save_signal_setup)
        row_int.addWidget(b_save_sig)
        row_int.addStretch(1); v.addLayout(row_int)
        self.signal_view = QTextEdit(); self.signal_view.setReadOnly(True); self.signal_view.setMaximumHeight(150)
        self.signal_view.setFont(QFont("Monospace", 9))
        v.addWidget(self.signal_view)

        btn_refresh = QPushButton("Refresh"); btn_refresh.clicked.connect(self.refresh_trade)
        v.addWidget(btn_refresh)

        # ---- autonomous loop ----
        v.addWidget(QLabel("— Autonomous loop (runs the house on a timer, inside Heart's caps) —"))
        lrow = QHBoxLayout()
        self._loop_proc = None
        b_loop_start = QPushButton("▶ Start loop"); b_loop_start.clicked.connect(self._loop_start)
        b_loop_stop = QPushButton("■ Stop loop"); b_loop_stop.clicked.connect(self._loop_stop)
        from PyQt6.QtWidgets import QSpinBox
        self.loop_interval = QSpinBox(); self.loop_interval.setRange(30, 3600); self.loop_interval.setValue(300)
        lrow.addWidget(b_loop_start); lrow.addWidget(b_loop_stop)
        lrow.addWidget(QLabel("every")); lrow.addWidget(self.loop_interval); lrow.addWidget(QLabel("s"))
        lrow.addStretch(1)
        v.addLayout(lrow)
        self.loop_status = QLabel("loop stopped")
        v.addWidget(self.loop_status)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[2] = scroll; scroll.hide()
        QTimer.singleShot(400, self.refresh_trade)
        self.timer_trade = QTimer(self); self.timer_trade.timeout.connect(self.refresh_trade); self.timer_trade.start(10000)
        return scroll

    def _reload_exchanges(self):
        exchanges, _ = self._trade_mod()
        try:
            data = exchanges.list_exchanges()
        except Exception as e:
            self.exc_combo.clear(); return
        self.exc_combo.blockSignals(True)
        self.exc_combo.clear()
        for e in data["exchanges"]:
            self.exc_combo.addItem(e["name"])
        active = data.get("active")
        if active:
            i = self.exc_combo.findText(active)
            if i >= 0:
                self.exc_combo.setCurrentIndex(i)
        self.exc_combo.blockSignals(False)
        self.on_exchange_selected(self.exc_combo.currentText())

    def on_exchange_selected(self, name):
        if not name:
            return
        exchanges, _ = self._trade_mod()
        try:
            d = exchanges.connection_descriptor(name)
        except Exception as e:
            self.exc_status.setText(f"error: {e}"); return
        creds = d["credentials"]
        self.exc_status.setText(("✅ credentials ready" if creds["ready"] else
                                 "⚠️ missing: " + ", ".join(creds["missing_required"])))
        self.exc_status.setStyleSheet("color:green" if creds["ready"] else "color:orange")
        self.exc_detail.setPlainText(
            f"label: {d['label']}\nccxt: {d['ccxt_id']}\n"
            f"quotes: {', '.join(d['quote_assets'])}\n"
            f"needed perms: {', '.join(d['permissions_needed']) or '-'}\n"
            f"NEVER enable: {', '.join(d['permissions_forbidden']) or '-'}\n"
            f"fields: " + ", ".join(f"{f['field']}({'set' if f['present'] else 'missing'})" for f in creds["fields"]))

    def use_exchange(self):
        exchanges, _ = self._trade_mod()
        name = self.exc_combo.currentText()
        exchanges.set_active(name)
        self.on_exchange_selected(name)
        self.exc_status.setText(f"active = {name} — " + self.exc_status.text())

    def open_connect_dialog(self):
        name = self.exc_combo.currentText()
        exchanges, _ = self._trade_mod()
        try:
            d = exchanges.connection_descriptor(name)
        except Exception as e:
            self.exc_status.setText(f"error: {e}"); return
        dlg = QDialog(self); dlg.setWindowTitle(f"Enter {d['label']} credentials")
        form = QFormLayout(dlg)
        note = QLabel(f"Needed: {', '.join(d['permissions_needed']) or '-'}\n"
                      f"NEVER enable: {', '.join(d['permissions_forbidden']) or '-'}\n"
                      f"Values are hidden and stored encrypted.")
        note.setWordWrap(True); form.addRow(note)
        edits = {}
        for f in d["credentials"]["fields"]:
            le = QLineEdit(); le.setEchoMode(QLineEdit.EchoMode.Password)
            le.setPlaceholderText("already set — leave blank to keep" if f["present"] else "required" if f["required"] else "optional")
            edits[f["field"]] = le
            form.addRow(f["label"], le)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec():
            for field, le in edits.items():
                val = le.text().strip()
                if val:
                    exchanges.store_credential(name, field, val)
            self.on_exchange_selected(name)

    def _trading_cfg(self):
        import json as _j
        p = Path.home()/"blackhorse"/"config"/"trading.json"
        try:
            return _j.loads(p.read_text()), p
        except Exception:
            return {"autonomous": False, "mode": "PAPER"}, p

    def _write_trading_cfg(self, cfg):
        import json as _j
        p = Path.home()/"blackhorse"/"config"/"trading.json"
        p.write_text(_j.dumps(cfg, indent=2))

    def toggle_autonomous(self):
        cfg, _ = self._trading_cfg()
        want = self.cb_auto.isChecked()
        cfg["autonomous"] = bool(want)
        self._write_trading_cfg(cfg)
        self.auto_state.setText("ON" if want else "OFF")
        self.auto_state.setStyleSheet("color:#22c55e;font-weight:bold" if want else "color:#f59e0b;font-weight:bold")

    def toggle_live(self):
        cfg, _ = self._trading_cfg()
        want = self.cb_live.isChecked()
        if want:
            # LIVE requires a real, ready exchange connection; warn clearly.
            try:
                exchanges, _ = self._trade_mod()
                name = exchanges.active_exchange()["name"]
                ready = exchanges.credential_status(name)["ready"]
            except Exception:
                ready = False
            if not ready:
                self.cb_live.blockSignals(True); self.cb_live.setChecked(False); self.cb_live.blockSignals(False)
                self.mode_state.setText("PAPER (no credentials)")
                self.mode_state.setStyleSheet("color:#ef4444;font-weight:bold")
                return
        cfg["mode"] = "LIVE" if want else "PAPER"
        self._write_trading_cfg(cfg)
        self.mode_state.setText("LIVE 💰" if want else "PAPER")
        self.mode_state.setStyleSheet("color:#ef4444;font-weight:bold" if want else "color:#22c55e;font-weight:bold")

    def refresh_trade(self):
        # reloading exchanges decrypts vault keys + rebuilds the combo — only do it
        # when the config actually changes, not every 5s
        import os
        _p = Path.home()/"blackhorse"/"config"/"exchanges.json"
        try:
            mtime = os.path.getmtime(_p)
        except OSError:
            mtime = 0
        if mtime != getattr(self, "_exc_mtime", None):
            self._exc_mtime = mtime
            self._reload_exchanges()
        try:
            _, engine = self._trade_mod()
            snap = engine.snapshot()
        except Exception as e:
            self.news_label.setText(f"(engine error: {e})"); return
        gov = snap["governance"]
        cfg, _ = self._trading_cfg()
        self.cb_auto.blockSignals(True); self.cb_auto.setChecked(bool(cfg.get("autonomous")))
        self.cb_auto.blockSignals(False)
        self.auto_state.setText("ON" if cfg.get("autonomous") else "OFF")
        self.auto_state.setStyleSheet("color:#22c55e;font-weight:bold" if cfg.get("autonomous") else "color:#f59e0b;font-weight:bold")
        self.cb_live.blockSignals(True); self.cb_live.setChecked(cfg.get("mode") == "LIVE")
        self.cb_live.blockSignals(False)
        self.mode_state.setText("LIVE 💰" if cfg.get("mode") == "LIVE" else "PAPER")
        self.mode_state.setStyleSheet("color:#ef4444;font-weight:bold" if cfg.get("mode") == "LIVE" else "color:#22c55e;font-weight:bold")
        self.auto_warning.setText(
            f"mode={gov['trading_mode']} · autonomous={'ON' if gov['autonomous'] else 'OFF'} · "
            "Heart gate + fee-honest backtest + paper period required before arming.")
        n = snap.get("news", {})
        self.news_label.setText(n.get("summary", "—"))
        hl = n.get("headlines", [])
        self.news_view.setPlainText("\n".join(f"• {h}" for h in hl) or "(no headlines)")
        sig = snap.get("signal", {})
        _sig_src = "bot live"
        # the signal block shows the user's manual preview run when one exists —
        # otherwise fall back to what the bot is actually running on
        try:
            _pv = Path.home() / "blackhorse" / "logs" / "signal_preview.json"
            if _pv.exists():
                _snap = json.loads(_pv.read_text())
                if isinstance(_snap, dict) and _snap.get("available"):
                    sig = _snap
                    _sig_src = "manual preview"
        except Exception:
            pass
        if sig.get("available"):
            chans = sig.get("channels", {})
            lines = [f"[{_sig_src}] {sig.get('pair')} @ {sig.get('exchange')} · strategy {sig.get('strategy')} · price {_curr_price(chans)} · ran {_ago(sig.get('ts'))} · {_fmt_ts(sig.get('ts'))} · took {(sig.get('run_ms') or 0)/1000:.1f}s"]
            for name, c in chans.items():
                rr = c.get("marketResult") or {}
                r = c.get("RSI") or {}
                adv = c.get("advisor") or {}
                lines.append(
                    f"\n{name:8s} {c.get('interval','?'):>5}  {c.get('action','?'):5s} conf={c.get('confidence')}  strength={round(c.get('strength') or 0,3)} ({c.get('strengthDirection')})")
                chg = rr.get('spreadChange')
                cnt = rr.get('spreadCount')
                is15m = str(c.get('interval')) == "15m"
                sv = rr.get('spreadVerdict')
                # compact spread numbers; drop the raw "spread=weakening" word (line below carries it)
                gate = c.get("strengthGate60")
                if gate:
                    # small lanes: entry/exit eligibility forced through the 60m
                    # strength gate — say WHY, so 'all green but False' is never a mystery.
                    lines.append(f"    trend={rr.get('trendState')} RSI={r.get('currentRSI')} · entry eligible={c.get('entryEligible')}→60m:{c.get('entryEligible60')} · exit eligible={c.get('exitEligible')}→60m:{c.get('exitEligible60')}")
                    lines.append(f"    60m strength gate (small-tf rule): {gate.get('note')}")
                else:
                    lines.append(f"    trend={rr.get('trendState')} RSI={r.get('currentRSI')} · entry eligible={c.get('entryEligible')} · exit eligible={c.get('exitEligible')}")
                if is15m and sv:
                    lines.append(f"    SPREAD latest={_n(rr.get('latestSpread'))} (was {_n(rr.get('intialSpread'))}) · verdict {sv.get('verdict')} · bias {sv.get('directionBias')} chop {sv.get('chopLevel')} str {sv.get('strength')} ({sv.get('bars')} bars) ⭐")
                elif chg is not None:
                    lines.append(f"    SPREAD latest={_n(rr.get('latestSpread'))} (was {_n(rr.get('intialSpread'))}) · change {chg:+.4f} / {cnt} bars (read 15m for truth)")
                sm = c.get('spreadMA') or {}
                if sm.get('available'):
                    tag = " ⭐" if is15m else " (read 15m for truth)"
                    lines.append(f"    MA10−MA30 diff={sm.get('diff'):+.4f} ({sm.get('diffPct'):+.3f}%) · {sm.get('signal')}{tag}")
                if is15m:
                    sc = c.get('setupScenario')
                    bw = c.get('bottomWatch') or {}
                    lines.append(f"    scenario: {sc}")
                    if bw.get('armed'):
                        lines.append(f"    bottom-watch: ARMED · price {_n(bw.get('price'))} vs low {_n(bw.get('low'))} · strength {_n(bw.get('strength'),2)} · waiting on RSI≤35 / spread improving / buyScore≥0.60 ⭐")
                    else:
                        lines.append(f"    bottom-watch: off (strength {_n(bw.get('strength'),2)} ≥ 0.15 or price off floor)")
                mx = c.get('maCross') or {}
                if is15m and mx.get('verdict'):
                    lines.append(f"    macross MA20={_n(mx.get('ma20'))} MA50={_n(mx.get('ma50'))} · {mx.get('verdict')} · cross {mx.get('cross')} ⭐")
                else:
                    lines.append(f"    macross MA20={_n(mx.get('ma20'))} MA50={_n(mx.get('ma50'))} · {mx.get('bias')} · cross {mx.get('cross')} (read 15m for truth)")
                lines.append(f"    range={c.get('rangeSignal')} bias={c.get('rangeBias')} · stop {_n(rr.get('stopLoss'))} target {_n(rr.get('takeProfit'))}")
                lines.append(f"    advisor buy {adv.get('buy')} sell {adv.get('sell')}")
            self.signal_view.setPlainText("\n".join(lines))
            self.signal_status.setText("available")
            self.signal_status.setStyleSheet("color:#22c55e")
        else:
            self.signal_view.setPlainText(sig.get("error", "not run yet — click 'Run SignalBot now'"))
            self.signal_status.setText("not run")
            self.signal_status.setStyleSheet("color:#9ca3af")
        # treasury — in LIVE, reconcile from the exchange so the page cannot lie
        try:
            import sys as _s
            if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
            import treasury, execute, time as _t
            if execute._mode() == "LIVE":
                # reconciling appends a ledger event; doing it every 5s is spammy
                now = _t.time()
                if now - getattr(self, "_last_recon", 0) > 300:
                    self._last_recon = now
                    treasury.reconcile_from_exchange()
            t = treasury.status()
            hold = t.get("holdings") or {}
            holdstr = (" · holds " + ", ".join(f"{k}{v}" for k, v in hold.items())) if hold else ""
            self.treasury_label.setText(
                f"[{execute._mode()}] allocated {t.get('allocated')} {t.get('currency')} · "
                f"cash {t.get('cash')} · available {t.get('available')} · reserve {t.get('reserve_floor')}"
                f"{holdstr}")
            self.treasury_label.setStyleSheet("color:#e5e7eb")
        except Exception as e:
            self.treasury_label.setText(f"treasury: unavailable ({e})")
        # over-allocation audit — agents must not claim more than exists
        try:
            import agents
            aud = agents.audited()
            try:
                import capital
                pool = f" · pool free ${capital.free()} · deployed ${aud['deployed']}"
            except Exception:
                pool = ""
            if aud["over_allocated"]:
                self.agent_status.setText(
                    f"⚠ AGENTS OVER-ALLOCATED: claim {aud['agents_claim']} vs {aud['available']} real "
                    f"(overshoot {aud['overshoot']}).{pool}")
                self.agent_status.setStyleSheet("color:#ef4444")
            else:
                self.agent_status.setText(
                    f"agents claim {aud['agents_claim']} of {aud['available']} real{pool}")
                self.agent_status.setStyleSheet("color:#9ca3af")
        except Exception:
            pass
        # validation gate warning (honest, not a silent block)
        try:
            import execute as _ex
            warn = _ex.gate_warning()
            if warn:
                self.auto_warning.setText("⚠ " + warn + " — trading continues; this is a quality flag.")
                self.auto_warning.setStyleSheet("color:#f59e0b;font-size:11px")
            else:
                self.auto_warning.setText("validation gate: passed")
                self.auto_warning.setStyleSheet("color:#22c55e;font-size:11px")
        except Exception:
            pass
        # memory
        try:
            import sys as _s
            if str(Path.home()/"blackhorse"/"memory") not in _s.path:
                _s.path.insert(0, str(Path.home()/"blackhorse"/"memory"))
            import index
            st = index.stats()
            self.memory_label.setText(
                f"memory: {st['total']} records · permanent {st['by_zone'].get('permanent',0)} · "
                f"ephemeral {st['by_zone'].get('ephemeral',0)} · {round(st['db_bytes']/1024)} KB on disk")
        except Exception as e:
            self.memory_label.setText(f"memory: unavailable ({e})")

    def treasury_action(self, kind):
        try:
            import sys as _s
            if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
            import treasury
            amt = float(self.treasury_input.text().strip() or 0)
            if amt <= 0:
                self.treasury_label.setText("enter a positive amount first"); return
            if kind == "allocate":
                treasury.allocate(amt, note="gui")
            elif kind == "reserve":
                treasury.set_reserve(amt)
            self.treasury_input.clear()
            self.refresh_trade()
        except Exception as e:
            self.treasury_label.setText(f"treasury error: {e}")

    def _save_signal_setup(self):
        import json as _j
        try:
            cfg = {"pair": (self.signal_pair_combo.currentText() if hasattr(self, 'signal_pair_combo') else 'DOT/USD'),
                   "channels": [x.strip() for x in (self.interval_input.text() if hasattr(self, 'interval_input') else '').split(',') if x.strip()]}
            p = Path.home()/"blackhorse"/"config"/"signal_setup.json"
            p.write_text(_j.dumps(cfg, indent=2))
            self.signal_status.setText(f"saved: {cfg['pair']} {','.join(cfg['channels'])}")
            self.signal_status.setStyleSheet("color:#22c55e")
        except Exception as e:
            self.signal_status.setText(f"save error: {e}")
            self.signal_status.setStyleSheet("color:#ef4444")

    def run_signal_now(self):
        import threading
        self.signal_status.setText("running… (~10-20s)")
        self.signal_status.setStyleSheet("color:#f59e0b")
        class _Res: pass
        self._sig_res = _Res(); self._sig_res.done = threading.Event(); self._sig_res.err = None
        # signal a worker result; actual GUI updates happen on the main thread via QTimer
        def _work():
            try:
                import sys as _s
                if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                    _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
                import signalbot as _sig
                ivs = [x.strip() for x in (self.interval_input.text() if hasattr(self, 'interval_input') else '').split(',') if x.strip()]
                pair = (self.signal_pair_combo.currentText() if hasattr(self, 'signal_pair_combo') else 'DOT/USD')
                _sig.run(pair=pair, strategy="swing", channels=ivs or None,
                         out=str(Path.home() / "blackhorse" / "logs" / "signal_preview.json"))
            except Exception as e:
                self._sig_res.err = str(e)
            finally:
                self._sig_res.done.set()
        threading.Thread(target=_work, daemon=True).start()
        def _wait():
            if self._sig_res.done.is_set():
                if self._sig_res.err:
                    self.signal_status.setText(f"error: {self._sig_res.err}")
                    self.signal_status.setStyleSheet("color:#ef4444")
                else:
                    self.signal_status.setText("ok")
                    self.signal_status.setStyleSheet("color:#22c55e")
                    self.refresh_trade()
                return
            QTimer.singleShot(1000, _wait)
        QTimer.singleShot(1000, _wait)

    # ================= AGENTS (the bodies) =================
    def _agents_mod(self):
        import sys as _s
        if str(Path.home()/"blackhorse"/"trade") not in _s.path:
            _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
        import agents, broker
        return agents, broker

    # ================= HEART (the referee) =================
    def _heart_mod(self):
        import sys as _s
        if str(Path.home()/"blackhorse"/"trade") not in _s.path:
            _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
        import heart
        return heart

    def _build_heart(self):
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("HEART — the referee. Every action must pass ALLOW/BLOCK. Cannot be disabled by the brain."))

        self.heart_status = QLabel("status: —"); self.heart_status.setFont(QFont("Monospace", 9))
        self.heart_status.setWordWrap(True)
        v.addWidget(self.heart_status)

        hrow = QHBoxLayout()
        b_hb = QPushButton("❤ Heartbeat now"); b_hb.clicked.connect(self.heart_beat)
        b_ref = QPushButton("Refresh"); b_ref.clicked.connect(self.refresh_heart)
        hrow.addWidget(b_hb); hrow.addWidget(b_ref); hrow.addStretch(1)
        v.addLayout(hrow)

        v.addWidget(QLabel("— Caps (heart/policies/rules.json) —"))
        from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox, QGridLayout
        self._heart_cap_specs = [
            ("max_open_positions",  "Max open positions",     1, 1000,   0, True),
            ("max_position_pct",    "Max position %",         1.0, 100.0, 1, False),
            ("max_order_quote",     "Max order quote ($)",    1.0, 100000.0, 2, False),
            ("max_orders_per_hour", "Max orders / hour",      1, 1000,   0, True),
            ("max_daily_loss_pct",  "Max daily loss %",       0.1, 100.0, 1, False),
            ("max_drawdown_pct",    "Max drawdown %",         0.1, 100.0, 1, False),
        ]
        cap_form = QGridLayout()
        self._heart_cap_widgets = {}
        for i, (key, label, lo, hi, dec, is_int) in enumerate(self._heart_cap_specs):
            cap_form.addWidget(QLabel(label), i, 0)
            if is_int:
                sb = QSpinBox()
            else:
                sb = QDoubleSpinBox()
                sb.setDecimals(dec)
            sb.setRange(lo, hi)
            self._heart_cap_widgets[key] = sb
            cap_form.addWidget(sb, i, 1)
        v.addLayout(cap_form)
        cap_row = QHBoxLayout()
        b_save_caps = QPushButton("Save caps")
        b_save_caps.clicked.connect(self.save_heart_caps)
        cap_row.addWidget(b_save_caps)
        cap_row.addStretch(1)
        v.addLayout(cap_row)
        self.heart_caps_msg = QLabel("")
        self.heart_caps_msg.setFont(QFont("Monospace", 9))
        v.addWidget(self.heart_caps_msg)
        self.heart_caps = QTextEdit(); self.heart_caps.setReadOnly(True); self.heart_caps.setMaximumHeight(140)
        self.heart_caps.setFont(QFont("Monospace", 9))
        v.addWidget(self.heart_caps)

        v.addWidget(QLabel("— Autonomy unlock (all must pass before the switch can arm) —"))
        self.heart_unlock = QLabel("—"); self.heart_unlock.setFont(QFont("Monospace", 9))
        v.addWidget(self.heart_unlock)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[4] = scroll; scroll.hide()
        QTimer.singleShot(400, self.refresh_heart)
        return scroll

    def heart_beat(self):
        try:
            self._heart_mod().heartbeat()
            self.refresh_heart()
        except Exception as e:
            self.heart_status.setText(f"heartbeat error: {e}")

    def save_heart_caps(self):
        import json as _json
        path = Path.home()/"blackhorse"/"heart"/"policies"/"rules.json"
        try:
            rules = _json.loads(path.read_text())
        except Exception:
            rules = {}
        caps = dict(rules.get("caps") or {})
        for key, sb in self._heart_cap_widgets.items():
            caps[key] = sb.value()
        rules["caps"] = caps
        path.write_text(_json.dumps(rules, indent=2) + "\n")
        self.heart_caps_msg.setText(
            f"saved caps → {path} · max_open_positions is the one that blocks rung-buys")
        self.heart_caps_msg.setStyleSheet("color:#22c55e")
        try:
            import importlib, sys as _s
            if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
            import heart
            importlib.reload(heart)
        except Exception:
            pass
        self.refresh_heart()

    def refresh_heart(self):
        try:
            h = self._heart_mod()
            st = h.status()
            lock = "UNLOCKED (can arm)" if st["unlock"]["can_arm"] else "LOCKED"
            self.heart_status.setText(
                f"heartbeat: {'OK' if st['heartbeat_ok'] else 'STALE/MISSING'} ({st['heartbeat_note']}) · "
                f"orders last hour: {st['orders_last_hour']} · autonomy: {lock}")
            self.heart_status.setStyleSheet("color:green" if st["heartbeat_ok"] else "color:orange")
            self.heart_caps.setPlainText(json.dumps(st["caps"], indent=2))
            for key, sb in getattr(self, "_heart_cap_widgets", {}).items():
                if key in st["caps"]:
                    sb.blockSignals(True)
                    sb.setValue(st["caps"][key])
                    sb.blockSignals(False)
            u = st["unlock"]
            self.heart_unlock.setText(
                "can_arm: %s\n" % u["can_arm"] +
                "\n".join(f"  {k}: {'PASS' if v else 'FAIL'}" for k, v in u["checks"].items()) +
                (f"\n  missing: {', '.join(u['missing'])}" if u["missing"] else ""))
        except Exception as e:
            self.heart_status.setText(f"heart error: {e}")

    # ================= LOOP control (on Trade page) =================
    def _loop_start(self):
        import sys as _s
        if getattr(self, "_loop_proc", None) and self._loop_proc.poll() is None:
            self.loop_status.setText("loop already running"); return
        if self._systemctl("is-active", "blackhorse-trader.service").stdout.strip() == "active":
            self.loop_status.setText("loop already running (systemd: blackhorse-trader.service)")
            return
        args = [sys.executable, str(Path.home()/"blackhorse"/"trade"/"loop.py"),
                "--interval", str(self.loop_interval.value()), "--no-brain"]
        self._loop_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.loop_status.setText(f"loop RUNNING (pid {self._loop_proc.pid}, every {self.loop_interval.value()}s)")

    def _loop_stop(self):
        if getattr(self, "_loop_proc", None) and self._loop_proc.poll() is None:
            self._loop_proc.terminate()
        self.loop_status.setText("loop stopped")

    def _show_profile(self):
        try:
            agents, _ = self._agents_mod()
            key = self.agent_profile.currentData()
            p = agents.interval_defaults().get(key, {})
            desc = p.get("description", "")
            climb = ", ".join(p.get("climb") or []) or "none"
            if desc:
                desc += f"<br>Default climb: <b>{climb}</b> — each listed rung must clear its own band before this lane moves."
            self.agent_profile_desc.setText(desc)
        except Exception:
            pass

    def create_agent(self):
        try:
            agents, _ = self._agents_mod()
            key = self.agent_profile.currentData()
            cap = float(self.agent_capital.text().strip() or 0)
            mode = self.agent_kind.currentText().strip().upper()
            agents.create_agent("trading", key, capital=cap, mode=mode)
            self.refresh_agents()
        except Exception as e:
            self.agent_status.setText(f"create error: {e}")

    def _set_agent_enabled(self, agent_id, on):
        try:
            agents, _ = self._agents_mod()
            agents.set_enabled(agent_id, on)
            self.refresh_agents()
        except Exception as e:
            self.house_view.setPlainText(f"toggle error: {e}")

    def delete_agent(self):
        """Delete the selected agent — with verification. Shows exactly what will
        happen: the working orders that get cancelled and the open positions that
        stay on the exchange (the user has to deal with those)."""
        a = self._selected_agent()
        if not a:
            return
        try:
            agents, _ = self._agents_mod()
            prev = agents.preview_delete(a["id"])
        except Exception as e:
            self.agent_status.setText(f"delete error: {e}")
            return
        orders = prev["entries"] + prev["exits"]
        pos = prev["positions"]
        lines = [f"Remove agent <b>{prev['name']}</b> ({prev['id']}) from the house?", ""]
        if orders:
            lines.append(f"<b>{len(orders)} working order(s) will be CANCELLED on the exchange:</b>")
            lines += [f"&nbsp;&nbsp;• {o['side']} {o['pair']} — {o['txid']}" for o in orders]
        else:
            lines.append("No working orders to cancel.")
        lines.append("")
        if pos:
            lines.append(f"<b style='color:#dc2626'>⚠ {len(pos)} open position(s) will NOT be closed.</b>")
            lines.append("They stay open on the exchange — <b>you must deal with them yourself</b> "
                         "(sell/close them):")
            lines += [f"&nbsp;&nbsp;• {p['pair']} — qty {p['qty']} @ entry {p['entry']} "
                      f"({p.get('mode') or '-'})" for p in pos]
            lines.append("")
        lines.append("This cannot be undone.")
        box = QMessageBox(self)
        box.setWindowTitle("Delete agent")
        box.setIcon(QMessageBox.Icon.Warning if pos else QMessageBox.Icon.Question)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Delete agent")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            res = agents.delete_agent(a["id"], cancel_orders=True)
        except Exception as e:
            self.agent_status.setText(f"delete error: {e}")
            return
        self.refresh_agents()
        msg = f"Deleted {a['name']} ({a['id']})."
        if res.get("cancelled"):
            msg += f" Cancelled {len(res['cancelled'])} order(s)."
        if res.get("cancel_failed"):
            msg += (f" ⚠ {len(res['cancel_failed'])} order(s) could NOT be cancelled — "
                    f"check the venue (reconcile will settle them).")
        if res.get("positions_left"):
            msg += f" ⚠ {len(res['positions_left'])} position(s) left OPEN — close them yourself."
        self.agent_status.setText(msg)
        self.agent_status.setStyleSheet(
            "color:#d97706" if (res.get("cancel_failed") or res.get("positions_left")) else "color:#16a34a")

    def house_decide(self):
        try:
            _, broker = self._agents_mod()
            d = broker.decide()
            lines = [f"HOUSE ACTION: {d['action']}  ({d.get('reason', d.get('profile',''))})"]
            lines += ["  - " + l for l in d.get("explain", [])]
            if d.get("action") in ("BUY", "SELL"):
                lines.append(f"  entry {d.get('entry')} stop {d.get('stop')} target {d.get('target')} "
                             f"size ${d.get('size_quote')} R:R {d.get('rr')}")
            lines.append("  (UNVALIDATED — hypothesis only)")
            self.house_view.setPlainText("\n".join(lines))
        except Exception as e:
            self.house_view.setPlainText(f"broker error: {e}")


    def _build_skin(self):
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("SKIN — connection checks, watchdog, firewall"))

        row = QHBoxLayout()
        b_sweep = QPushButton("Run sweep now"); b_sweep.clicked.connect(self.run_sweep)
        b_lock = QPushButton("LOCKDOWN"); b_lock.setStyleSheet("background:#a33;color:white;font-weight:bold")
        b_lock.clicked.connect(self.toggle_lockdown)
        row.addWidget(b_sweep); row.addWidget(b_lock)
        v.addLayout(row)

        setup_row = QHBoxLayout()
        self.btn_setup_skin = QPushButton("⚡ Set up continuous skin monitoring")
        self.btn_setup_skin.setStyleSheet("background:#1a5;color:white;font-weight:bold;padding:8px")
        self.btn_setup_skin.clicked.connect(self.setup_skin_service)
        self.btn_disable_skin = QPushButton("Disable")
        self.btn_disable_skin.clicked.connect(self.disable_skin_service)
        setup_row.addWidget(self.btn_setup_skin); setup_row.addWidget(self.btn_disable_skin)
        v.addLayout(setup_row)
        self.skin_status = QLabel("Skin service: checking...")
        v.addWidget(self.skin_status)

        self.btn_active = QPushButton("🛡 Enable ACTIVE defense (respond to threats for real — one-time setup)")
        self.btn_active.setStyleSheet("background:#842;color:white;font-weight:bold;padding:8px")
        self.btn_active.clicked.connect(self.toggle_active_defense)
        v.addWidget(self.btn_active)

        self.log_view = QTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Monospace", 9))
        v.addWidget(QLabel("Threat log (live):")); v.addWidget(self.log_view)

        from PyQt6.QtWidgets import QCheckBox, QSpinBox, QComboBox
        v.addWidget(QLabel("— CPU knobs (skin_settings.json) —"))
        s = settings.load()
        self.cb_llm = QCheckBox("Local fallback model ON (qwen via Ollama) — cheap 2nd opinion, NOT the brain")
        self.cb_llm.setChecked(s["llm_enabled"])
        self.cb_llm.stateChanged.connect(self.save_settings)
        v.addWidget(self.cb_llm)
        row = QHBoxLayout(); row.addWidget(QLabel("LLM model"))
        self.cb_model = QComboBox(); self.cb_model.setEditable(True)
        self.cb_model.addItems(["qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:7b", "llama3.2:1b", "llama3.2:3b", "mistral"])
        self.cb_model.setCurrentText(s.get("llm_model", "qwen2.5:0.5b"))
        self.cb_model.currentTextChanged.connect(self.save_settings)
        row.addWidget(self.cb_model); v.addLayout(row)
        for label, key, rng in [("Max local calls/min (0=off)", "max_llm_calls_per_min", (0, 30)),
                                ("LLM timeout (s)", "llm_timeout_seconds", (5, 300)),
                                ("Model keep-alive (s)", "llm_keep_alive_seconds", (0, 600))]:
            row = QHBoxLayout(); row.addWidget(QLabel(label))
            sb = QSpinBox(); sb.setRange(*rng); sb.setValue(s[key])
            sb.valueChanged.connect(self.save_settings)
            setattr(self, f"sb_{key}", sb)
            row.addWidget(sb); v.addLayout(row)
        for eng in ("net_check", "watchdog", "egress", "integrity"):
            cb = QCheckBox(f"engine: {eng}"); cb.setChecked(s["engines"][eng])
            cb.stateChanged.connect(self.save_settings)
            setattr(self, f"cb_eng_{eng}", cb)
            v.addWidget(cb)
        btn_reload = QPushButton("Reload settings now")
        btn_reload.clicked.connect(self.reload_settings_ui)
        v.addWidget(btn_reload)
        btn_restart = QPushButton("Restart dashboard")
        def _restart():
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
        btn_restart.clicked.connect(_restart)
        v.addWidget(btn_restart)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[1] = scroll; scroll.hide(); return scroll

    def switch_page(self, row):
        for i, w in self.pages.items():
            w.setVisible(i == row)

    def refresh_log(self):
        if LOG.exists():
            lines = LOG.read_text(errors="ignore").splitlines()[-300:]
            self.log_view.setPlainText("\n".join(lines))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def run_sweep(self):
        if not self._master_on():
            print("RUN_SWEEP skipped — master OFF", flush=True)
            return
        print("RUN_SWEEP FIRED", flush=True)
        threading.Thread(target=lambda: subprocess.run(
            [sys.executable, str(Path.home()/"blackhorse"/"skin"/"defense.py")],
            cwd=Path.home()/"blackhorse"/"skin", capture_output=True), daemon=True).start()

    SERVICE = Path.home() / ".config" / "systemd" / "user" / "blackhorse-defense.service"
    SERVICE_UNIT = """[Unit]
Description=BlackHorse skin defense monitor
After=default.target

[Service]
ExecStart=/usr/bin/python3 {skin}/defense.py --loop 60
Restart=always
RestartSec=5
Environment=BLACKHORSE_DRY_RUN=false

[Install]
WantedBy=default.target
"""

    def enable_active_defense(self):
        script = Path.home() / "blackhorse" / "skin" / "enable_active_defense.sh"
        script.chmod(0o755)
        subprocess.Popen(["pkexec", "bash", str(script)])
        QTimer.singleShot(15000, self.refresh_active_defense)

    def _active_defense_is_on(self) -> bool:
        """Real state, read from source of truth (not a guess)."""
        # 1) the running service's environment
        r = subprocess.run(["systemctl", "--user", "show", "blackhorse-defense.service",
                            "--property=Environment"], capture_output=True, text=True)
        if "BLACKHORSE_DRY_RUN=false" in (r.stdout or ""):
            return True
        # 2) the on-disk unit file
        try:
            return "BLACKHORSE_DRY_RUN=false" in self.SERVICE.read_text()
        except OSError:
            return False

    def refresh_active_defense(self):
        on = self._active_defense_is_on()
        if on:
            self.btn_active.setText("🛡 ACTIVE defense ON — click to DISABLE")
            self.btn_active.setStyleSheet("background:#14532d;color:white;font-weight:bold;padding:8px")
        else:
            self.btn_active.setText("🛡 Enable ACTIVE defense (respond to threats for real — one-time setup)")
            self.btn_active.setStyleSheet("background:#842;color:white;font-weight:bold;padding:8px")

    def toggle_active_defense(self):
        if self._active_defense_is_on():
            # turn OFF: back to dry-run and restart the service
            try:
                txt = self.SERVICE.read_text().replace("BLACKHORSE_DRY_RUN=false", "BLACKHORSE_DRY_RUN=true")
                self.SERVICE.write_text(txt)
            except OSError:
                pass
            self._systemctl("daemon-reload")
            self._systemctl("restart", "blackhorse-defense.service")
            QTimer.singleShot(15000, self.refresh_active_defense)
        else:
            self.enable_active_defense()

    def _systemctl(self, *args):
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)

    def setup_skin_service(self):
        skin_dir = Path.home() / "blackhorse" / "skin"
        self.SERVICE.parent.mkdir(parents=True, exist_ok=True)
        self.SERVICE.write_text(self.SERVICE_UNIT.format(skin=skin_dir))
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", "blackhorse-defense.service")
        self.refresh_skin_status()

    def disable_skin_service(self):
        self._systemctl("disable", "--now", "blackhorse-defense.service")
        self.refresh_skin_status()

    def save_settings(self):
        import settings as _s
        cfg = _s.load()
        cfg["llm_enabled"] = self.cb_llm.isChecked()
        cfg["llm_model"] = self.cb_model.currentText().strip()
        if cfg["llm_enabled"]:
            from pathlib import Path as _P
            threading.Thread(target=lambda: __import__("subprocess").run(
                ["python3", str(_P.home()/"blackhorse"/"skin"/"llm_ready.py")]), daemon=True).start()
        for key in ("max_llm_calls_per_min", "llm_timeout_seconds", "llm_keep_alive_seconds"):
            cfg[key] = getattr(self, f"sb_{key}").value()
        for eng in ("net_check", "watchdog", "egress", "integrity"):
            cfg["engines"][eng] = getattr(self, f"cb_eng_{eng}").isChecked()
        _s.save(cfg)
        self.skin_status.setText("settings saved — live next cycle")

    def reload_settings_ui(self):
        import settings as _s
        s = _s.load()
        self.cb_llm.setChecked(s["llm_enabled"])
        self.cb_model.setCurrentText(s.get("llm_model", "qwen2.5:0.5b"))
        for key in ("max_llm_calls_per_min", "llm_timeout_seconds", "llm_keep_alive_seconds"):
            getattr(self, f"sb_{key}").setValue(s[key])
        for eng in ("net_check", "watchdog", "egress", "integrity"):
            getattr(self, f"cb_eng_{eng}").setChecked(s["engines"][eng])

    def refresh_skin_status(self):
        r = self._systemctl("is-active", "blackhorse-defense.service")
        state = r.stdout.strip()
        active_def = "🛡 ACTIVE" if self._active_defense_is_on() else "dry-run (log only)"
        if state == "active":
            self.skin_status.setText(f"Skin service: ✅ ACTIVE — monitoring every 60s · defense={active_def}")
            self.skin_status.setStyleSheet("color:green")
        else:
            self.skin_status.setText(f"Skin service: ⚠️ {state or 'not set up'} — hit the button to enable · defense={active_def}")
            self.skin_status.setStyleSheet("color:orange")
        self.refresh_active_defense()

    def toggle_lockdown(self):
        if LOCKDOWN_FLAG.exists():
            LOCKDOWN_FLAG.unlink()
        else:
            LOCKDOWN_FLAG.parent.mkdir(parents=True, exist_ok=True)
            LOCKDOWN_FLAG.write_text("manual lockdown from UI\n")
        self.refresh_log()

if __name__ == "__main__":
    if not _acquire_app_singleton():
        print("blackhorse GUI already running (logs/app.lock is held) — exiting", file=sys.stderr)
        raise SystemExit(0)
    app = QApplication(sys.argv)
    # closing the window must NOT quit — the app lives in the tray until Quit
    app.setQuitOnLastWindowClosed(False)
    try:
        import qdarkstyle
        app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyqt6') + """
            QWidget#sidebar { background:#0f172a; border-right:1px solid #1e293b; }
            QListWidget#nav { background:transparent; border:none; color:#cbd5e1; font-size:13px; }
            QListWidget#nav::item { padding:10px 8px; border-radius:8px; margin:2px 6px; }
            QListWidget#nav::item:selected { background:#065f46; color:white; }
            QFrame#card { background:#111c33; border:1px solid #1e293b; border-radius:12px; }
            QFrame#card QLabel { border:none; background:transparent; }
        """)
    except ImportError:
        pass
    window = BlackHorse()          # keep a reference — temp gets garbage-collected!
    window.show()
    sys.exit(app.exec())
