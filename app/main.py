"""BlackHorse desktop app — PySide6. Skin defense is the first working sidebar page."""
import sys, os, subprocess, threading, time, json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
sys.path.insert(0, str(Path.home() / "blackhorse" / "skin"))

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QPushButton, QLabel, QTextEdit, QListWidget, QListWidgetItem,
                               QSystemTrayIcon, QMenu, QDialog, QFormLayout, QComboBox, QDialogButtonBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QAction, QIcon
sys.path.insert(0, str(Path.home() / "blackhorse" / "skin"))
import settings as _settings_mod
settings = _settings_mod

LOG = Path.home() / "blackhorse" / "logs" / "threats.jsonl"
LOCKDOWN_FLAG = Path.home() / "blackhorse" / "config" / "LOCKDOWN"

class BlackHorse(QMainWindow):
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
        for page in ["Dashboard", "Skin (Defense)", "Brain (Decide)", "Trade (Exchange)", "Agents", "Heart (Filter)", "Console", "Stories", "Motar (Agents)"]:
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
        cl.addWidget(self._build_brain())
        cl.addWidget(self._build_trade())
        cl.addWidget(self._build_agents())
        cl.addWidget(self._build_heart())
        cl.addWidget(self._build_console())
        cl.addWidget(self._build_stories())
        cl.addWidget(self._build_placeholder("Motar (Agents)", "Agent workers — coming soon."))
        lay.addWidget(content)
        self.nav.setCurrentRow(1)

        # ---------- tray: closing the window hides, it does not quit ----------
        self._quitting = False
        self.tray = None
        self._setup_tray()

        # live-refresh the threat log
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_log); self.timer.start(10000)
        self.timer2 = QTimer(self); self.timer2.timeout.connect(self.refresh_brain); self.timer2.start(10000)
        self.timer3 = QTimer(self); self.timer3.timeout.connect(self.refresh_master); self.timer3.start(10000)
        self.timer4 = QTimer(self); self.timer4.timeout.connect(self.refresh_console); self.timer4.start(10000)
        self.timer5 = QTimer(self); self.timer5.timeout.connect(self.refresh_dashboard); self.timer5.start(10000)
        import os as _os
        if _os.environ.get("BLACKHORSE_PROFILE"):
            import time as _time
            for _name in ("refresh_log", "refresh_brain", "refresh_master",
                          "refresh_console", "refresh_dashboard", "refresh_trade",
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
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Dashboard — system overview / OFF verification"))
        self.dash_trade = QLabel("trade state: —")
        self.dash_trade.setFont(QFont("Monospace", 9))
        self.dash_trade.setWordWrap(True)
        v.addWidget(self.dash_trade)
        self.dash_status = QLabel("checking…")
        self.dash_status.setFont(QFont("Monospace", 10))
        self.dash_status.setWordWrap(True)
        v.addWidget(self.dash_status)
        row = QHBoxLayout()
        b_verify = QPushButton("Verify OFF now"); b_verify.clicked.connect(self.verify_off)
        row.addWidget(b_verify)
        v.addLayout(row)
        v.addStretch(1)
        self.pages[0] = w; w.hide(); return w

    def refresh_dashboard(self):
        import json, pathlib
        bh = pathlib.Path.home()/"blackhorse"
        try:
            s = json.loads((bh/"logs"/"loop_state.json").read_text())
            if hasattr(self, 'loop_status'):
                self.loop_status.setText("loop RUNNING (backend night-shift)" if s.get('heartbeat')=='ok' else "loop HALTED (backend no heartbeat)")
            sig_path = bh/"logs"/"signal_latest.json"
            sig = json.loads(sig_path.read_text()) if sig_path.exists() else {}
            ch = sig.get("channels", {})
            ripple = ch.get("ripple", {})
            tide = ch.get("tide", {})
            lines = [
              f"loop: {'ok' if s.get('heartbeat')=='ok' else 'halted'} · interval {s.get('ts','?')[11:16]} · decision {s.get('decision')} — {s.get('reason')}",
              f"signal: {sig.get('pair','?')} @ {ripple.get('currPrice')} · 60m str {ripple.get('strength')} · trend {ripple.get('trendState')} · signal {ripple.get('rangeSignal')}",
              f"tide(1440m): trend {tide.get('trendState')} · rangeSignal {tide.get('rangeSignal')} · buy ticking at {tide.get('advisor',{}).get('buy')}",
              f"marks: {[m.get('closed') if isinstance(m,dict) else m for m in s.get('marks',[])]}",
            ]
            hb = (bh/"logs"/"heart_orders.json")
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
            self.dash_trade.setText(f"trade state: {e}")

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
        lines.append(f"brain (opencode)         : {'on' if master and s.get('brain_enabled') else 'OFF'}")
        lines.append(f"whitecell escalation     : {'on' if master and s.get('whitecells_may_escalate') else 'OFF'}")
        # systemd service
        st = self._systemctl("is-active", "blackhorse-defense.service").stdout.strip() or "not set up"
        lines.append(f"defense service          : {st}")
        # stray worker processes
        try:
            import psutil
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

    def _build_brain(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("BRAIN — opencode guardian (the brain). Skin is its war room."))
        self.brain_state = QLabel("daemon: checking…")
        v.addWidget(self.brain_state)
        self.brain_budget = QLabel("brain budget: —")
        v.addWidget(self.brain_budget)

        # ---- brain controls ----
        from PyQt6.QtWidgets import QCheckBox, QSpinBox
        s = settings.load()
        self.cb_brain = QCheckBox("Brain ON (uncheck = never call opencode)")
        self.cb_brain.setChecked(s.get("brain_enabled", True))
        self.cb_brain.stateChanged.connect(self.save_brain_settings)
        v.addWidget(self.cb_brain)
        self.cb_escalate = QCheckBox("Whitecells may ask the brain (escalate unresolved threats)")
        self.cb_escalate.setChecked(s.get("whitecells_may_escalate", True))
        self.cb_escalate.stateChanged.connect(self.save_brain_settings)
        v.addWidget(self.cb_escalate)

        for label, key, rng in [("Min gap between brain calls (s)", "brain_min_seconds_between_calls", (60, 3600)),
                                ("Brain calls per hour (max)", "brain_max_calls_per_hour", (1, 60)),
                                ("Brain sweep interval (s)", "brain_sweep_seconds", (60, 3600))]:
            row = QHBoxLayout(); row.addWidget(QLabel(label))
            sb = QSpinBox(); sb.setRange(*rng); sb.setValue(int(s.get(key, rng[0])))
            sb.valueChanged.connect(self.save_brain_settings)
            setattr(self, f"sb_{key}", sb)
            row.addWidget(sb); v.addLayout(row)

        brow = QHBoxLayout()
        b_sweep = QPushButton("Sweep brain now"); b_sweep.clicked.connect(self.brain_sweep_now)
        b_silence = QPushButton("Reset brain budget (clear cooldown)")
        b_silence.clicked.connect(self.brain_reset_budget)
        brow.addWidget(b_sweep); brow.addWidget(b_silence)
        v.addLayout(brow)

        # periodic edge_rules study toggle
        from PyQt6.QtWidgets import QCheckBox, QSpinBox
        self.cb_edge_rules = QCheckBox("Run edge_rules study periodically")
        self.edge_rules_cfg = json.loads((Path.home()/"blackhorse"/"config"/"edge_rules.json").read_text()) if (Path.home()/"blackhorse"/"config"/"edge_rules.json").exists() else {"enabled": True, "every_minutes": 60}
        self.cb_edge_rules.setChecked(self.edge_rules_cfg.get("enabled", True))
        self.cb_edge_rules.stateChanged.connect(self._on_edge_rules_toggle)
        v.addWidget(self.cb_edge_rules)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("every (min)"))
        self.sb_edge_minutes = QSpinBox(); self.sb_edge_minutes.setRange(5, 1440); self.sb_edge_minutes.setValue(int(self.edge_rules_cfg.get("every_minutes", 60)))
        self.sb_edge_minutes.valueChanged.connect(self._on_edge_minutes_changed)
        row2.addWidget(self.sb_edge_minutes); row2.addStretch(1); v.addLayout(row2)
        self.edge_rules_timer = QTimer(self); self.edge_rules_timer.timeout.connect(self.run_edge_rules_update); self.edge_rules_timer.start(5000)  # tick 1s, each tick check last_ts
        self._last_edge_rules_run = 0

        self.brain_view = QTextEdit(); self.brain_view.setReadOnly(True)
        self.brain_view.setFont(QFont("Monospace", 9))
        self.brain_view.setUndoRedoEnabled(False)
        self.brain_view.document().setMaximumBlockCount(500)
        v.addWidget(self.brain_view)
        b_row = QHBoxLayout()
        b_clear = QPushButton("Clear view"); b_clear.clicked.connect(lambda: self.brain_view.clear())
        b_wipe = QPushButton("Archive history file")
        def _wipe():
            import shutil, time
            f = Path.home()/"blackhorse"/"logs"/"mastermind.jsonl"
            if f.exists():
                shutil.move(str(f), str(f) + "." + time.strftime("%Y%m%d%H%M%S"))
            self.brain_view.clear()
        b_wipe.clicked.connect(_wipe)
        b_row.addWidget(b_clear); b_row.addWidget(b_wipe); b_row.addStretch(1)
        v.addLayout(b_row)
        row = QHBoxLayout()
        self.brain_input = QLineEdit(); self.brain_input.setPlaceholderText("Inject a message into the mastermind session…")
        self.brain_input.returnPressed.connect(self.brain_send)
        btn = QPushButton("Send to mastermind"); btn.clicked.connect(self.brain_send)
        row.addWidget(self.brain_input); row.addWidget(btn)
        v.addLayout(row)
        self.pages[2] = w; w.hide(); return w

    def _brain_cfg(self):
        cfg = settings.load()
        cfg["brain_enabled"] = self.cb_brain.isChecked()
        cfg["whitecells_may_escalate"] = self.cb_escalate.isChecked()
        for key in ("brain_min_seconds_between_calls", "brain_max_calls_per_hour", "brain_sweep_seconds"):
            cfg[key] = getattr(self, f"sb_{key}").value()
        return cfg

    def save_brain_settings(self):
        settings.save(self._brain_cfg())
        self.brain_state.setText("brain settings saved — live next cycle")

    def brain_sweep_now(self):
        threading.Thread(target=lambda: subprocess.run(
            [sys.executable, str(Path.home()/"blackhorse"/"skin"/"brain.py"), "sweep"],
            capture_output=True, cwd=Path.home()/"blackhorse"), daemon=True).start()
        QTimer.singleShot(2500, self.refresh_brain)

    def brain_reset_budget(self):
        try:
            import brain_budget
            brain_budget.reset()
        except Exception as e:
            self.brain_state.setText(f"budget reset failed: {e}")
        self.refresh_brain()

    def brain_send(self):
        q = self.brain_input.text().strip()
        if not q: return
        self.brain_input.clear()
        self._brain_pending = q  # shown by refresh_brain until the reply lands in the log
        self._brain_reply = None
        def _run():
            r = subprocess.run(
                [sys.executable, str(Path.home()/"blackhorse"/"skin"/"mastermind.py"), "ask", q],
                capture_output=True, cwd=Path.home()/"blackhorse", text=True)
            self._brain_reply = (r.stdout.strip() or r.stderr.strip()[:200] or "(no reply)")
        threading.Thread(target=_run, daemon=True).start()
        QTimer.singleShot(400, self._brain_poll_reply)


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

    def _brain_poll_reply(self):
        reply = getattr(self, "_brain_reply", None)
        if reply is None:
            QTimer.singleShot(400, self._brain_poll_reply)
            return
        _sb = self.brain_view.verticalScrollBar()
        _stick = _sb.value() >= _sb.maximum() - 40
        self.brain_view.append(f"brain: {reply}")
        if _stick: _sb.setValue(_sb.maximum())
        self._brain_reply = None
        self._brain_pending = None

    def refresh_brain(self):
        import json
        state = Path.home()/"blackhorse"/"logs"/"mastermind_state.json"
        self.brain_state.setText(f"state file: {state.read_text() if state.exists() else 'not started — click Wake mastermind in Skin page'}")
        try:
            import brain_budget
            b = brain_budget.status()
            self.brain_budget.setText(
                f"brain budget: {b['calls_last_hour']}/{b['max_calls_per_hour']} calls this hour · "
                f"gap {b['min_seconds_between_calls']}s · "
                + (f"ready" if b['allowed'] else f"wait {b['seconds_until_next']}s"))
        except Exception as e:
            self.brain_budget.setText(f"brain budget: unavailable ({e})")
        out = Path.home()/"blackhorse"/"logs"/"mastermind.jsonl"
        try:
            import os as _os
            _mtime = _os.path.getmtime(out) if out.exists() else 0
        except OSError:
            _mtime = 0
        _pending_now = getattr(self, "_brain_pending", None)
        if (_mtime, _pending_now) != getattr(self, "_brain_key", None):
            self._brain_key = (_mtime, _pending_now)
            lines = []
            if out.exists():
                # read only the tail so an ever-growing file stays cheap
                with open(out, "rb") as fh:
                    fh.seek(0, 2); size = fh.tell()
                    fh.seek(max(0, size - 256*1024))
                    lines = fh.read().decode(errors="ignore").splitlines()[-60:]
            rows = []
            for l in lines:
                try:
                    e = json.loads(l)
                except Exception:
                    continue
                if e.get("kind") == "trade":
                    continue  # trade prompts belong on the Trade page, not here
                if "BlackHorse trading brain" in str(e.get("q", "")):
                    continue  # legacy un-tagged trade prompts
                rows.append(f"YOU: {e.get('q','')}\nGUARDIAN: {str(e.get('a',''))[:400]}\n")
            base = "\n".join(rows)
            target = base + (f"\nYOU: {_pending_now}\nGUARDIAN: … thinking\n" if _pending_now else "")
            if getattr(self, "_brain_last_view", None) != target:
                # preserve the user's scroll position unless they were at the bottom
                sb = self.brain_view.verticalScrollBar()
                was_at_bottom = sb.value() >= sb.maximum() - 40
                prev = sb.value()
                self.brain_view.setPlainText(target)
                sb.setValue(sb.maximum() if was_at_bottom else prev)
                self._brain_last_view = target
            else:
                # view content unchanged — leave selection/scroll intact
                pass

    def _build_placeholder(self, name, msg):
        w = QWidget(); v = QVBoxLayout(w); v.addWidget(QLabel(msg))
        self.pages[len(self.pages)] = w; w.hide(); return w

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
        b_ask_top = QPushButton("🧠 Ask brain now")
        b_ask_top.setStyleSheet("background:#3730a3;color:white;font-weight:bold;padding:8px")
        b_ask_top.clicked.connect(self.ask_brain_now)
        b_refresh_top = QPushButton("↻ Refresh")
        b_refresh_top.clicked.connect(self.refresh_trade)
        top.addWidget(b_ask_top); top.addWidget(b_refresh_top); top.addStretch(1)
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

        # ---- brain budget countdown ----
        self.budget_label = QLabel("brain budget: —")
        self.budget_label.setStyleSheet("color:#9ca3af")
        v.addWidget(self.budget_label)

        # ---- treasury (the "100") ----
        v.addWidget(QLabel("— Treasury (capital the brain may use) —"))
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

        # ---- brain verdict ----
        v.addWidget(QLabel("— Brain verdict (opencode; budgeted, minutes-spaced) —"))
        self.verdict_view = QTextEdit(); self.verdict_view.setReadOnly(True); self.verdict_view.setMaximumHeight(140)
        self.verdict_view.setFont(QFont("Monospace", 9))
        v.addWidget(self.verdict_view)

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
        self.loop_use_brain = QCheckBox("ask brain each cycle"); self.loop_use_brain.setChecked(False)
        lrow.addWidget(b_loop_start); lrow.addWidget(b_loop_stop)
        lrow.addWidget(QLabel("every")); lrow.addWidget(self.loop_interval); lrow.addWidget(QLabel("s"))
        lrow.addWidget(self.loop_use_brain); lrow.addStretch(1)
        v.addLayout(lrow)
        self.loop_status = QLabel("loop stopped")
        v.addWidget(self.loop_status)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[3] = scroll; scroll.hide()
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
        bv = snap.get("brain_verdict")
        self.verdict_view.setPlainText(
            json.dumps(bv, indent=2) if bv else "no verdict yet — click 'Ask brain now'"
        )
        sig = snap.get("signal", {})
        if sig.get("available"):
            chans = sig.get("channels", {})
            lines = [f"{sig.get('pair')} @ {sig.get('exchange')} · strategy {sig.get('strategy')} · {sig.get('run_ms')}ms · {sig.get('ts')}"]
            for name, c in chans.items():
                rr = c.get("marketResult") or {}
                r = c.get("RSI") or {}
                adv = c.get("advisor") or {}
                lines.append(
                    f"\n{name:8s} {c.get('interval','?'):>5}  {c.get('action','?'):5s} conf={c.get('confidence')}  strength={round(c.get('strength') or 0,3)} ({c.get('strengthDirection')})")
                lines.append(f"    trend={rr.get('trendState')} spread={rr.get('spreadDirection')} RSI={r.get('currentRSI')} entryEligible={c.get('entryEligible')} exitEligible={c.get('exitEligible')}")
                # the ACTUAL spread numbers (the word alone hides magnitude)
                chg = rr.get('spreadChange')
                cnt = rr.get('spreadCount')
                if chg is not None:
                    arrow = "↑improving" if chg > 0 else ("↓weakening" if chg < 0 else "→flat")
                    lines.append(f"    SPREAD change={chg:+.4f} over {cnt} bars {arrow} · latest={rr.get('latestSpread')} (was {rr.get('intialSpread')})")
                mx = c.get('maCross') or {}
                lines.append(f"    MA20={mx.get('ma20')} MA50={mx.get('ma50')} gap={mx.get('gap_pct')}% bias={mx.get('bias')} cross={mx.get('cross')}")
                lines.append(f"    rangeSignal={c.get('rangeSignal')} rangeBias={c.get('rangeBias')} curve stop={rr.get('stopLoss')} target={rr.get('takeProfit')}")
                lines.append(f"    advisor buy={adv.get('buy')} sell={adv.get('sell')}")
            self.signal_view.setPlainText("\n".join(lines))
            self.signal_status.setText("available")
            self.signal_status.setStyleSheet("color:#22c55e")
        else:
            self.signal_view.setPlainText(sig.get("error", "not run yet — click 'Run SignalBot now'"))
            self.signal_status.setText("not run")
            self.signal_status.setStyleSheet("color:#9ca3af")
        # budget countdown
        try:
            import sys as _s
            if str(Path.home()/"blackhorse"/"skin") not in _s.path:
                _s.path.insert(0, str(Path.home()/"blackhorse"/"skin"))
            import brain_budget
            b = brain_budget.status()
            if b["allowed"]:
                self.budget_label.setText(
                    f"brain budget: ready · {b['calls_last_hour']}/{b['max_calls_per_hour']} this hour · "
                    f"gap {b['min_seconds_between_calls']}s")
                self.budget_label.setStyleSheet("color:#22c55e")
            else:
                m, s = divmod(b["seconds_until_next"], 60)
                self.budget_label.setText(
                    f"brain budget: cooling down · next call in {m}:{s:02d} · "
                    f"{b['calls_last_hour']}/{b['max_calls_per_hour']} this hour")
                self.budget_label.setStyleSheet("color:#f59e0b")
        except Exception as e:
            self.budget_label.setText(f"brain budget: unavailable ({e})")
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
                pool = f" · pool free ${capital.free()} (open ${capital.status()['in_open_positions']})"
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
                _sig.run(pair=pair, strategy="swing", channels=ivs or None)
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

    def ask_brain_now(self):
        import threading
        self.verdict_view.setPlainText("asking brain… (budgeted; may defer if cooling down)")
        def _work():
            try:
                _, engine = self._trade_mod()
                import sys as _s
                _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
                import coach
                v = coach.ask_brain(coach.build_packet())
                self.verdict_view.setPlainText(json.dumps(v, indent=2))
            except Exception as e:
                self.verdict_view.setPlainText(f"(brain error: {e})")
        threading.Thread(target=_work, daemon=True).start()

    # ================= AGENTS (the bodies) =================
    def _agents_mod(self):
        import sys as _s
        if str(Path.home()/"blackhorse"/"trade") not in _s.path:
            _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
        import agents, broker
        return agents, broker

    def _build_agents(self):
        from PyQt6.QtWidgets import QScrollArea, QComboBox, QSpinBox
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("AGENTS — the bodies. BlackHorse (brain) owns them; each trades by its own profile."))

        # create row
        v.addWidget(QLabel("— Create an agent —"))
        row = QHBoxLayout()
        row.addWidget(QLabel("kind"))
        self.agent_kind = QComboBox(); self.agent_kind.addItems(["trading", "youtube"])
        row.addWidget(self.agent_kind)
        row.addWidget(QLabel("profile"))
        self.agent_profile = QComboBox()
        try:
            agents, _ = self._agents_mod()
            for k, p in agents.profiles().items():
                self.agent_profile.addItem(f"{k} — {p['label']}", k)
        except Exception:
            pass
        row.addWidget(self.agent_profile)
        row.addWidget(QLabel("capital"))
        self.agent_capital = QLineEdit("50"); self.agent_capital.setMaximumWidth(80)
        row.addWidget(self.agent_capital)
        b_create = QPushButton("＋ Create agent")
        b_create.setStyleSheet("background:#1a5;color:white;font-weight:bold")
        b_create.clicked.connect(self.create_agent)
        row.addWidget(b_create)
        row.addStretch(1)
        v.addLayout(row)
        self.agent_profile_desc = QLabel("")
        self.agent_profile_desc.setWordWrap(True); self.agent_profile_desc.setStyleSheet("color:#9ca3af;font-size:11px")
        v.addWidget(self.agent_profile_desc)
        self.agent_profile.currentIndexChanged.connect(self._show_profile)

        # list
        v.addWidget(QLabel("— Your agents —"))
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        self.agent_list = QListWidget()
        self.agent_list.currentRowChanged.connect(self._on_agent_selected)
        v.addWidget(self.agent_list, 1)
        arow = QHBoxLayout()
        b_on = QPushButton("Enable"); b_on.clicked.connect(lambda: self.agent_toggle(True))
        b_off = QPushButton("Disable"); b_off.clicked.connect(lambda: self.agent_toggle(False))
        arow.addWidget(b_on); arow.addWidget(b_off)
        arow.addStretch(1)
        v.addLayout(arow)
        from PyQt6.QtWidgets import QTabWidget
        self.agent_tabs = QTabWidget()
        v.addWidget(self.agent_tabs, 1)
        self._agent_pages = []  # (agent_id, tab_index) built on refresh

        # house decision
        v.addWidget(QLabel("— House decision (broker across enabled agents) —"))
        hrow = QHBoxLayout()
        b_house = QPushButton("🧠 Decide now (house)")
        b_house.clicked.connect(self.house_decide)
        hrow.addWidget(b_house); hrow.addStretch(1)
        v.addLayout(hrow)
        self.house_view = QTextEdit(); self.house_view.setReadOnly(True); self.house_view.setMaximumHeight(160)
        self.house_view.setFont(QFont("Monospace", 9))
        v.addWidget(self.house_view)

        btn_refresh = QPushButton("Refresh"); btn_refresh.clicked.connect(self.refresh_agents)
        v.addWidget(btn_refresh)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[4] = scroll; scroll.hide()
        QTimer.singleShot(300, self.refresh_agents)
        return scroll

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
        self.heart_caps = QTextEdit(); self.heart_caps.setReadOnly(True); self.heart_caps.setMaximumHeight(180)
        self.heart_caps.setFont(QFont("Monospace", 9))
        v.addWidget(self.heart_caps)

        v.addWidget(QLabel("— Autonomy unlock (all must pass before the switch can arm) —"))
        self.heart_unlock = QLabel("—"); self.heart_unlock.setFont(QFont("Monospace", 9))
        v.addWidget(self.heart_unlock)
        v.addStretch(1)

        scroll.setWidget(w)
        self.pages[5] = scroll; scroll.hide()
        QTimer.singleShot(400, self.refresh_heart)
        return scroll

    def heart_beat(self):
        try:
            self._heart_mod().heartbeat()
            self.refresh_heart()
        except Exception as e:
            self.heart_status.setText(f"heartbeat error: {e}")

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
            u = st["unlock"]
            self.heart_unlock.setText(
                "can_arm: %s\n" % u["can_arm"] +
                "\n".join(f"  {k}: {'PASS' if v else 'FAIL'}" for k, v in u["checks"].items()) +
                (f"\n  missing: {', '.join(u['missing'])}" if u["missing"] else ""))
        except Exception as e:
            self.heart_status.setText(f"heart error: {e}")

    # ================= CONSOLE (stateful brain chat) =================
    def _console_mod(self):
        import sys as _s
        if str(Path.home()/"blackhorse"/"trade") not in _s.path:
            _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
        import console
        return console

    def _build_console(self):
        from PyQt6.QtWidgets import QTabWidget
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("CONSOLE — talk to the brain. It knows your capital, positions, P&L, and memory."))
        # status strip: loop + last signal, read fresh each paint
        self.console_status = QLabel("loop: — · signal: —")
        self.console_status.setStyleSheet("color:#9ca3af;font-size:11px")
        v.addWidget(self.console_status)
        tabs = QTabWidget(); v.addWidget(tabs, 1)
        # ---- tab: PAPER (default, always live-safe) ----
        paper = QWidget(); pv = QVBoxLayout(paper)
        self.console_view = QTextEdit(); self.console_view.setReadOnly(True)
        self.console_view.setFont(QFont("Monospace", 9))
        self.console_view.setUndoRedoEnabled(False)
        self.console_view.document().setMaximumBlockCount(400)  # cap scrollback
        pv.addWidget(self.console_view, 1)
        b_clear = QPushButton("Clear chat"); b_clear.clicked.connect(lambda: self.console_view.clear())
        pv.addWidget(b_clear)
        row = QHBoxLayout()
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText("e.g.  I gave it 100, what's up?   |   allocate 50   |   reserve 20")
        self.console_input.returnPressed.connect(self.console_send)
        b_send = QPushButton("Send to brain"); b_send.clicked.connect(self.console_send)
        row.addWidget(self.console_input); row.addWidget(b_send)
        pv.addLayout(row)
        tabs.addTab(paper, "PAPER")
        # ---- tab: LIVE (locked until flag + execute flip) ----
        live = QWidget(); lv = QVBoxLayout(live)
        self.live_view = QTextEdit(); self.live_view.setReadOnly(True)
        self.live_view.setFont(QFont("Monospace", 9))
        lv.addWidget(self.live_view, 1)
        live_note = QLabel("LIVE is locked: execute.py refuses LIVE and logs/paper_period.flag is not set.\n"
                           "When those flip, this tab mirrors the live positions, P&L and fills.")
        live_note.setWordWrap(True); live_note.setStyleSheet("color:#9ca3af;font-size:11px")
        lv.addWidget(live_note)
        tabs.addTab(live, "LIVE 🔒")
        self.pages[6] = w; w.hide()
        return w

    def refresh_console(self):
        # thread-safe handoff: if a brain reply is ready, append it (replacing the thinking line)
        wait = getattr(self, "_console_pending", None)
        if wait:
            self._console_pending = None
            reply = wait[0]
            from PyQt6.QtGui import QTextCursor
            cur = self.console_view.textCursor()
            cur.movePosition(QTextCursor.End)
            cur.movePosition(QTextCursor.StartOfBlock, QTextCursor.MoveAnchor)
            cur.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
            if "thinking" in cur.selectedText():
                cur.removeSelectedText()
            self.console_view.moveCursor(QTextCursor.End)
            _sb = self.console_view.verticalScrollBar()
            _stick = _sb.value() >= _sb.maximum() - 40
            self.console_view.append(f"<b>Spice:</b> {reply}")
            if _stick: _sb.setValue(_sb.maximum())
        import json, pathlib
        bh = pathlib.Path.home()/"blackhorse"
        loop_state = (bh/"logs"/"loop_state.json")
        sig_path = (bh/"logs"/"signal_latest.json")
        loop = "loop: unknown"
        try:
            s = json.loads(loop_state.read_text())
            loop = f"loop: {'ok' if s.get('heartbeat')=='ok' else 'halted'} · last decision {s.get('decision')}"
        except Exception:
            pass
        sig = "signal: not run"
        try:
            d = json.loads(sig_path.read_text())
            ch = d.get("channels", {})
            ripple = ch.get("ripple", {})
            sig = f"signal: {d.get('pair')} @ {ripple.get('currPrice')} · 60m strength {ripple.get('strength')} · tide {ch.get('tide',{}).get('trendState')}"
        except Exception:
            pass
        self.console_status.setText(f"{loop}  |  {sig}")
        try:
            import heart
            txt = "unlock checks:\n" + "\n".join(f"  {k}: {v}" for k, v in heart.unlock().get("checks", {}).items())
            if txt != getattr(self, "_last_live_view", None):
                sb = self.live_view.verticalScrollBar()
                was_at_bottom = sb.value() >= sb.maximum() - 40
                prev = sb.value()
                self.live_view.setPlainText(txt)
                sb.setValue(sb.maximum() if was_at_bottom else prev)
                self._last_live_view = txt
        except Exception:
            pass

    def console_send(self):
        text = self.console_input.text().strip()
        if not text:
            return
        self.console_input.clear()
        self.console_view.append(f"<b>you:</b> {text}")
        _sb = self.console_view.verticalScrollBar()
        _stick = _sb.value() >= _sb.maximum() - 40
        if _stick: _sb.setValue(_sb.maximum())
        self.console_view.append("<i>Spice: thinking… (opencode can take 10–30s on this box)</i>")
        if _stick: _sb.setValue(_sb.maximum())
        import threading
        self._console_pending = getattr(self, "_console_pending", None)
        def _work(text, wait):
            try:
                import sys as _s
                if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                    _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
                import spice
                reply = spice.handle(text)
            except Exception as e:
                reply = f"error: {e}"
            wait.append(reply)  # thread-safe handoff
        wait = []
        self._console_pending = wait
        threading.Thread(target=_work, args=(text, wait), daemon=True).start()


    # ================= LOOP control (on Trade page) =================
    def _loop_start(self):
        import sys as _s
        if getattr(self, "_loop_proc", None) and self._loop_proc.poll() is None:
            self.loop_status.setText("loop already running"); return
        args = [sys.executable, str(Path.home()/"blackhorse"/"trade"/"loop.py"),
                "--interval", str(self.loop_interval.value())]
        if not self.loop_use_brain.isChecked():
            args.append("--no-brain")
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
            p = agents.profiles().get(key, {})
            self.agent_profile_desc.setText(p.get("description", ""))
        except Exception:
            pass

    def create_agent(self):
        try:
            agents, _ = self._agents_mod()
            key = self.agent_profile.currentData()
            cap = float(self.agent_capital.text().strip() or 0)
            agents.create_agent(self.agent_kind.currentText(), key, capital=cap)
            self.refresh_agents()
        except Exception as e:
            self.agent_status.setText(f"create error: {e}")

    def refresh_agents(self):
        try:
            agents, _ = self._agents_mod()
            self._agent_rows = agents.list_agents()
            self.agent_list.blockSignals(True); self.agent_list.clear()
            for a in self._agent_rows:
                self.agent_list.addItem(QListWidgetItem(
                    f"{'✅' if a['enabled'] else '⭘'} {a['id']}  {a['name']}  "
                    f"[{a['kind']}/{a.get('profile') or '-'}]  ${a['capital']}"))
            self.agent_list.blockSignals(False)
            self._show_profile()
            self._rebuild_agent_tabs()
        except Exception as e:
            self.house_view.setPlainText(f"agents error: {e}")

    def _selected_agent(self):
        i = self.agent_list.currentRow()
        return self._agent_rows[i] if 0 <= i < len(self._agent_rows) else None

    def _on_agent_selected(self, _row):
        a = self._selected_agent()
        if not a:
            return
        # open/find the per-agent tab
        for agent_id, idx in getattr(self, "_agent_pages", []):
            if agent_id == a["id"]:
                self.agent_tabs.setCurrentIndex(idx)
                return

    def _rebuild_agent_tabs(self):
        if not getattr(self, '_agent_rows', None):
            return
        try:
            del self._agent_pages
            self._agent_pages = []
        except AttributeError:
            pass
        self.agent_tabs.clear()
        own = QListWidget(); own.currentRowChanged.connect(lambda i: self.agent_list.setCurrentRow(i))
        try:
            agents, _ = self._agents_mod()
            self._agent_rows = agents.list_agents()
            for a in self._agent_rows:
                item = f"{'✅' if a['enabled'] else '⭘'} {a['id']} {a['name']} [{a['kind']}/{a.get('profile','-')}] ${a['capital']}"
                own.addItem(item)
        except Exception:
            pass
        self.agent_tabs.addTab(own, "List")
        from PyQt6.QtWidgets import QScrollArea
        for a in self._agent_rows:
            page = QWidget(); pv = QVBoxLayout(page)
            agents, _ = self._agents_mod()
            pol = agents.policy_for(a["id"])
            prof = agents.profiles().get(a.get("profile"), {})
            big = QTextEdit(); big.setReadOnly(True); big.setFont(QFont("Monospace", 9))
            lines = [
                f"name: {a.get('name')} · id: {a.get('id')}",
                f"kind: {a.get('kind')} · profile: {a.get('profile')} · enabled: {a.get('enabled')} · capital: ${a.get('capital')}",
                f"role: {prof.get('label','')}",
                "description:",
                prof.get('description',''),
                "",
                "— static profile cfg —",
            ]
            for k, v in sorted((prof.get('cfg') or {}).items()):
                lines.append(f"  {k}: {v}")
            lines.append("")
            lines.append("— policy_for(agent_id) —")
            for k, v in sorted(pol.items()):
                lines.append(f"  {k}: {v}")
            big.setPlainText("\n".join(lines))
            pv.addWidget(QLabel(f"{a['name']} ({a['id']})"))
            pv.addWidget(big, 1)
            row = QHBoxLayout()
            b_prof = QPushButton("Show profile")
            b_prof.clicked.connect(lambda _ch, aid=a['id']: self._select_agent_by_id(aid))
            b_toggle = QPushButton("Enable" if not a.get('enabled') else "Disable")
            b_toggle.clicked.connect(lambda _c, aid=a['id'], on=not a.get('enabled'): self._set_agent_enabled(aid, on))
            row.addWidget(b_prof); row.addWidget(b_toggle); row.addStretch(1)
            pv.addLayout(row)
            # per-agent settings editor (feeds agents.set_override -> policy_for)
            grid = QHBoxLayout()
            def _edit(label_text, default):
                w = QLineEdit(str(default)); w.setMaximumWidth(80)
                grid.addWidget(QLabel(label_text)); grid.addWidget(w)
                return w
            le_channels = _edit('channels', ",".join(pol.get('channels', [])))
            le_entry = _edit('entry<=', pol.get('entry_strength_max',''))
            le_exit = _edit('exit>=', pol.get('exit_strength_min',''))
            le_rr = _edit('R:R>=', pol.get('min_reward_risk',''))
            le_stop = _edit('stop%<=', pol.get('stop_max_pct',''))
            le_risk = _edit('risk%=', pol.get('risk_per_trade_pct',''))
            le_maxpos = _edit('maxpos%=', pol.get('max_position_pct',''))
            le_pairs = _edit('pairs', ','.join(a.get('pairs') or []))
            le_capital = _edit('capital', str(a.get('capital', 0)))
            pv.addLayout(grid)
            def _save_overrides(_checked=False, aid=a['id'], le_channels=le_channels, le_entry=le_entry, le_exit=le_exit, le_rr=le_rr, le_stop=le_stop, le_risk=le_risk, le_maxpos=le_maxpos, le_pairs=le_pairs, le_capital=le_capital):
                try:
                    agents, _ = self._agents_mod()
                    if le_channels.text().strip():
                        agents.set_override(aid, 'channels', [x.strip() for x in le_channels.text().split(',') if x.strip()])
                    if le_entry.text().strip():
                        agents.set_override(aid, 'entry_strength_max', float(le_entry.text()))
                    if le_exit.text().strip():
                        agents.set_override(aid, 'exit_strength_min', float(le_exit.text()))
                    if le_rr.text().strip():
                        agents.set_override(aid, 'min_reward_risk', float(le_rr.text()))
                    if le_stop.text().strip():
                        agents.set_override(aid, 'stop_max_pct', float(le_stop.text()))
                    if le_risk.text().strip():
                        agents.set_override(aid, 'risk_per_trade_pct', float(le_risk.text()))
                    if le_maxpos.text().strip():
                        agents.set_override(aid, 'max_position_pct', float(le_maxpos.text()))
                    if le_pairs.text().strip():
                        agents.set_pairs(aid, [x.strip().upper() for x in le_pairs.text().split(',') if x.strip()])
                    if le_capital.text().strip():
                        agents.set_capital(aid, float(le_capital.text()), allow_over=True)
                    self.refresh_agents()
                except Exception as e:
                    import traceback as _tb
                    self.house_view.setPlainText(f"save error: {e}\n\n{_tb.format_exc()}")
            b_save = QPushButton("Save to agent policy"); b_save.clicked.connect(_save_overrides)
            pv.addWidget(b_save)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
            self.agent_tabs.addTab(scroll, a['id'])
            self._agent_pages.append((a['id'], self.agent_tabs.count()-1))

    def _set_agent_enabled(self, agent_id, on):
        try:
            agents, _ = self._agents_mod()
            agents.set_enabled(agent_id, on)
            self.refresh_agents()
        except Exception as e:
            self.house_view.setPlainText(f"toggle error: {e}")

    def _select_agent_by_id(self, agent_id):
        try:
            self.agent_list.setCurrentRow(next(i for i,a in enumerate(self._agent_rows) if a['id']==agent_id))
            for aid, idx in self._agent_pages:
                if aid == agent_id:
                    self.agent_tabs.setCurrentIndex(idx)
                    return
        except Exception:
            pass

    def agent_toggle(self, on):
        a = self._selected_agent()
        if not a:
            return
        try:
            agents, _ = self._agents_mod()
            agents.set_enabled(a["id"], on)
            self.refresh_agents()
        except Exception as e:
            self.agent_status.setText(f"toggle error: {e}")

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


    def _build_stories(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("Stories — book/script workspace, one visible folder plus a prompt."))
        row = QHBoxLayout()
        b_refresh = QPushButton("🔄 Reload story folders")
        b_refresh.clicked.connect(self._stories_refresh)
        row.addWidget(b_refresh); row.addStretch(1); v.addLayout(row)
        self.stories_list = QListWidget(); self.stories_list.currentRowChanged.connect(self._stories_show)
        v.addWidget(self.stories_list, 1)
        self.stories_sections = QListWidget(); self.stories_sections.setMaximumHeight(90); self.stories_sections.currentRowChanged.connect(self._stories_show_section)
        v.addWidget(self.stories_sections)
        rowf = QHBoxLayout(); rowf.addWidget(QLabel("files/sections in dir:"))
        self.stories_files = QListWidget(); self.stories_files.setMaximumHeight(140); self.stories_files.currentRowChanged.connect(self._stories_show_file)
        rowf.addWidget(self.stories_files,1); v.addLayout(rowf)
        rowx = QHBoxLayout()
        b_del = QPushButton("🗑 Delete selected"); b_del.clicked.connect(self._stories_delete_selected)
        rowx.addWidget(b_del); rowx.addStretch(1); v.addLayout(rowx)
        rownav = QHBoxLayout()
        b_prev = QPushButton("◀ prev"); b_prev.clicked.connect(self._stories_prev)
        b_next = QPushButton("next ▶"); b_next.clicked.connect(self._stories_next)
        rownav.addWidget(b_prev); rownav.addWidget(b_next); rownav.addStretch(1); v.addLayout(rownav)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.stories_view = QPlainTextEdit(); self.stories_view.setReadOnly(True); self.stories_view.setFont(QFont("Georgia", 13))
        v.addWidget(QLabel("— project details —")); v.addWidget(self.stories_view, 2)
        self.stories_chat = QPlainTextEdit(); self.stories_chat.setReadOnly(True); self.stories_chat.setFont(QFont("Menlo", 12))
        v.addWidget(QLabel("— project conversation (remembered) —")); v.addWidget(self.stories_chat, 2)
        row2 = QHBoxLayout()
        self.stories_prompt = QLineEdit(); self.stories_prompt.returnPressed.connect(self._stories_send)
        self.stories_prompt.setPlaceholderText("Ask for a scene, add a chapter, or open a project like the book...")
        b_ask = QPushButton("Send to Spice"); b_ask.clicked.connect(self._stories_send)
        row2.addWidget(self.stories_prompt,1); row2.addWidget(b_ask); v.addLayout(row2)
        self.pages[7] = w; w.hide()
        try: self._stories_refresh()
        except Exception: pass
        return w

    def _stories_refresh(self):
        import pathlib, json
        root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
        root.mkdir(parents=True, exist_ok=True)
        self.stories_list.clear()
        for f in sorted(root.glob('*.json')):
            try:
                meta=json.loads(f.read_text())
                self.stories_list.addItem(meta.get("title", f.stem))
            except Exception:
                self.stories_list.addItem(f.stem)

    def _stories_show(self):
        import pathlib, json
        root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
        items = [x.text() for x in [self.stories_list.currentItem()] if x]
        if not items: return
        title=items[0]
        for f in root.glob('*.json'):
            try:
                meta=json.loads(f.read_text())
                if meta.get("title")==title:
                    # middle pane is for reading the story, not JSON
                    lines=[]
                    for ch in meta.get("chapters", []):
                        ch_text = (ch.get("text") or "")[:3000]
                        lines.append(f"CHAPTER — {ch.get('title','untitled')}\n{ch_text}\n")
                    if not lines:
                        lines.append("(no chapters yet — add one with the prompt, or it will appear here as chapters.json grows)")
                    for ch in meta.get("characters", {}).items():
                        vals=ch[1] if isinstance(ch[1],list) else []
                        lines.append(f"CHARACTER {ch[0]}:")
                        for v in vals[:6]:
                            lines.append(f" - {v.get('detail','')}")
                    # fill section list (ideas/scenes/etc.) and remember selected view
                    self.stories_sections.clear()
                    for name in ("chapters", "characters", "ideas", "scenes", "notes"):
                        self.stories_sections.addItem(name)
                    self._stories_meta = meta
                    self.stories_view.setPlainText("\n\n".join(lines))
                    thread = meta.get("thread") or []
                    self.stories_chat.setPlainText("\n\n".join(f"YOU: {t.get('q') or ''}\nSPICE: {t.get('a') or ''}" for t in thread) or "(no remembered conversation yet)")
                    return
            except Exception: pass

    def _stories_show_section(self, _row=None):
        import pathlib, json
        items = [x.text() for x in [self.stories_sections.currentItem()] if x]
        if not items: return
        section = items[0]
        meta = getattr(self, "_stories_meta", None)
        if meta is None:
            root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
            title_item = self.stories_list.currentItem()
            if not title_item: return
            for f in root.glob('*.json'):
                try:
                    m=json.loads(f.read_text())
                    if m.get('title')==title_item.text(): meta=m; break
                except Exception: pass
        if meta is None: return
        self.stories_files.clear()
        if section == "chapters":
            self.stories_files.addItems([ch.get("title", "untitled") for ch in meta.get("chapters", [])] or ["(no chapters yet)"])
            self.stories_view.setPlainText("(select a chapter in the files list above)")
        elif section == "characters":
            self.stories_files.addItems([name for name in meta.get("characters", {}).keys()] or ["(no characters yet)"])
            self.stories_view.setPlainText("(select a character name above)")
        else:
            import hashlib
            slug = hashlib.md5((meta.get('title','default').strip().encode())).hexdigest()[:8]
            path = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"/f"{slug}_project/{section}"
            if path.is_dir():
                files = sorted([f.name for f in path.iterdir() if f.is_file()])
            else:
                files = []
            self.stories_files.addItems(files or ["(empty)"])
            self.stories_view.setPlainText(f"{section} — click a file name above to read it")


    def _stories_show_file(self, _row=None):
        import pathlib, json
        texts = [self.stories_files.item(i).text() for i in range(self.stories_files.count())]
        item = self.stories_files.currentItem()
        if not item: return
        name = item.text()
        section = (self.stories_sections.currentItem().text() if self.stories_sections.currentItem() else None) or ""
        root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
        meta = getattr(self, '_stories_meta', None)
        if meta is None: return
        if section == "chapters":
            for ch in meta.get("chapters", []):
                if ch.get("title", "untitled") == name:
                    self.stories_view.setPlainText(ch.get("text","") or "(empty chapter)")
                    return
            self.stories_view.setPlainText("(chapter not found)")
        elif section == "characters":
            vals = meta.get("characters", {}).get(name, [])
            lines=[]
            for v in (vals or []):
                lines.append(f"- {v.get('detail','')}")
            self.stories_view.setPlainText("\n".join(lines) or "(no detail)")
        else:
            import hashlib
            slug = hashlib.md5((meta.get('title','default').strip().encode())).hexdigest()[:8]
            path = root.joinpath(f"{slug}_project/{section}")
            fpath = path / name
            if fpath.exists():
                self.stories_view.setPlainText(fpath.read_text(errors='ignore'))
            else:
                self.stories_view.setPlainText("(file not found)")

    def _stories_prev(self):
        row = self.stories_files.currentRow()
        if row > 0:
            self.stories_files.setCurrentRow(row - 1)

    def _stories_next(self):
        row = self.stories_files.currentRow()
        if row < self.stories_files.count() - 1:
            self.stories_files.setCurrentRow(row + 1)


    def _stories_delete_selected(self):
        import pathlib, json, re, hashlib
        from PyQt6.QtWidgets import QMessageBox
        fn_item = self.stories_files.currentItem()
        if not fn_item:
            return
        title_item = self.stories_list.currentItem()
        if not title_item:
            QMessageBox.information(self, "No project", "Select a project first.")
            return
        project_title = title_item.text()
        section_item = self.stories_sections.currentItem() if hasattr(self, 'stories_sections') else None
        section = section_item.text() if section_item else 'notes'
        root = pathlib.Path.home()/"blackhorse"/"logs"/"storybook"
        # find target JSON file and optional physical file
        target_json = None
        for f in root.glob('*.json'):
            try:
                meta=json.loads(f.read_text())
                if meta.get('title')==project_title:
                    target_json=f; break
            except Exception: pass
        slug=hashlib.md5(project_title.strip().encode()).hexdigest()[:8]
        folder = root/f"{slug}_project/{section}"
        match=None
        if folder.is_dir():
            for f in folder.iterdir():
                if fn_item.text() in f.name or f.name.startswith(fn_item.text()):
                    match=f; break
        # tell the user exactly what is about to be removed
        msg_lines = []
        if section == 'chapters' and target_json:
            try:
                meta=json.loads(target_json.read_text())
                matches=[c for c in meta.get('chapters',[]) if c.get('title','').startswith(fn_item.text()) or c.get('text','').startswith(fn_item.text())]
                msg_lines.append(f"Removing {len(matches)} chapter record(s)<icide JSON: {target_json}")
                for m in matches:
                    msg_lines.append(f"  - '{m.get('title','')}'")
            except Exception:
                msg_lines.append(f"Could not parse JSON chapter records from {target_json}; proceed with caution.")
            if match:
                msg_lines.append(f"Will also remove this file from the chapters folder: {match}")
            else:
                msg_lines.append("No corresponding physical chapter file found on disk.")
        else:
            msg_lines.append(f"Project: {project_title}")
            msg_lines.append(f"Section: {section}")
            if match:
                msg_lines.append(f"Will remove this physical file:\n  {match}")
            else:
                msg_lines.append("No physical file will be removed from this section.")
            msg_lines.append("This only removes the selected item; it does not remove the whole project, the notes folder, or other chapters.")
        msg = "\n".join(msg_lines) + "\n\nProceed ONLY if this is what you want removed."
        ret = QMessageBox.question(self, "Confirm delete", msg,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        # perform delete
        if target_json and section == 'chapters':
            try:
                meta=json.loads(target_json.read_text())
                meta['chapters']=[c for c in meta.get('chapters',[]) if not (c.get('title','').startswith(fn_item.text()) or c.get('text','').startswith(fn_item.text()))]
                target_json.write_text(json.dumps(meta,indent=2))
            except Exception: pass
        if match and match.exists():
            match.unlink()
        try: self._stories_show_section()
        except Exception: pass
        try:
            self.stories_files.takeItem(self.stories_files.currentRow())
        except Exception: pass
        if self.stories_files.count()==0:
            self.stories_view.setPlainText(f"(empty {section})")

    def _stories_send(self):
        text = self.stories_prompt.text().strip()
        if not text: return
        self.stories_prompt.clear()
        def _work():
            try:
                import sys as _s
                if str(Path.home()/"blackhorse"/"trade") not in _s.path:
                    _s.path.insert(0, str(Path.home()/"blackhorse"/"trade"))
                import spice
                return f"<b>Spice:</b> {spice.handle(text)}"
            except Exception as e:
                return f"<b>error:</b> {e}"
        # if a section is selected, the prompt also writes a note into that folder
        import pathlib as _pb, json as _json, time as _t, hashlib as _hx, re as _re
        try:
            project_title = (self.stories_list.currentItem().text() if self.stories_list.currentItem() else None)
            section_item = self.stories_sections.currentItem() if hasattr(self, 'stories_sections') else None
            if project_title and section_item and section_item.text() in ('chapters','ideas','scenes','notes','characters'):
                slug = _hx.md5(project_title.strip().encode()).hexdigest()[:8]
                folder = _pb.Path.home()/"blackhorse"/"logs"/"storybook"/f"{slug}_project"/section_item.text()
                try: folder.mkdir(parents=True, exist_ok=True)
                except Exception: pass
                base = text.splitlines()[0][:40] if text else ''
                # turn the first line into a readable, filesystem-safe file name
                safe = _re.sub(r'[^a-zA-Z0-9]+', '_', base).strip('_').lower()
                safe = (safe or _t.strftime('%Y-%m-%d', _t.gmtime()))[:37]
                # chapters keep a chapter number as well as a name
                if section_item.text() == 'chapters':
                    try:
                        _meta = _json.loads(f.read_text())
                        ch_count = len(_meta.get('chapters', [])) + 1
                    except Exception:
                        ch_count = 1
                    fname = f"chapter_{ch_count:02d}_{safe}.md"
                else:
                    fname = f"{safe}.md"
                folder.joinpath(fname).write_text(text)
                # also push into project JSON chapters/notes if applicable
                for f in (_pb.Path.home()/"blackhorse"/"logs"/"storybook").glob('*.json'):
                    try:
                        meta2=_json.loads(f.read_text())
                        if meta2.get('title')==project_title:
                            if section_item.text() == 'chapters':
                                meta2.setdefault('chapters',[]).append({"title": text.splitlines()[0][:40] if text else 'chapter note', "text": text})
                                f.write_text(_json.dumps(meta2, indent=2))
                            break
                    except Exception: pass
        except Exception: pass
        # save into this project's thread for later
        try:
            items=[x.text() for x in [self.stories_list.currentItem()] if x]
            if items:
                root=_pb.Path.home()/"blackhorse"/"logs"/"storybook"
                for f in root.glob('*.json'):
                    try:
                        meta=_json.loads(f.read_text())
                        if meta.get('title')==items[0]:
                            meta.setdefault('thread',[]).append({"q": text, "a": "", "ts": _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())})
                            f.write_text(_json.dumps(meta, indent=2)); break
                    except Exception: pass
            # don't block the GUI: call Spice on a worker thread and reveal when done
            reply_holder=[]
            self.stories_chat.appendPlainText(f"you: {text}\nSpice: thinking…")
            self.stories_chat.verticalScrollBar().setValue(self.stories_chat.verticalScrollBar().maximum())
            def _run():
                reply_holder.append(_work())
            import threading
            threading.Thread(target=_run, daemon=True).start()
            def _reap():
                if reply_holder:
                    try: self.stories_chat.verticalScrollBar().setValue(self.stories_chat.verticalScrollBar().maximum())
                    except Exception: pass
                    reply = reply_holder[0]
                    current=[x.text() for x in [self.stories_list.currentItem()] if x]
                    if current:
                        root = _pb.Path.home()/"blackhorse"/"logs"/"storybook"
                        for f in root.glob('*.json'):
                            try:
                                meta=_json.loads(f.read_text())
                                if meta.get('title')==current[0]:
                                    if meta.get('thread'): meta['thread'][-1]['a']=reply
                                    f.write_text(_json.dumps(meta, indent=2)); break
                            except Exception: pass
                    # always tell the owner, even if no project row is selected
                    self.stories_chat.appendPlainText(reply)
                    self.stories_chat.verticalScrollBar().setValue(self.stories_chat.verticalScrollBar().maximum())
                    self._stories_show()
                    return
                QTimer.singleShot(1500, _reap)
            _reap()
        except Exception as e:
            self.stories_chat.appendPlainText(f"error: {e}")

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
        btn_wake = QPushButton("Wake brain (opencode sweep daemon)")
        self._mm_proc = None
        def _wake():
            from pathlib import Path as _P
            if self._mm_proc and self._mm_proc.poll() is None:
                self._mm_proc.terminate(); self._mm_proc = None
                btn_wake.setText("Wake brain (opencode sweep daemon)")
                return
            self._mm_proc = subprocess.Popen([sys.executable, str(_P.home()/"blackhorse"/"skin"/"brain.py"), "daemon"])
            btn_wake.setText("Brain AWAKE — click to sleep")
        btn_wake.clicked.connect(_wake)
        v.addWidget(btn_wake)

        # ---- TAP: talk to skin ----
        v.addWidget(QLabel("— TAP: ask the brain a direct question (one-shot, bypasses budget) —"))
        self.tap_log = QTextEdit(); self.tap_log.setReadOnly(True); self.tap_log.setMaximumHeight(140)
        v.addWidget(self.tap_log)
        tap_row = QHBoxLayout()
        self.tap_input = QLineEdit(); self.tap_input.setPlaceholderText("Ask skin anything: 'what happened last night?', 'why was pid X killed?'")
        self.tap_input.returnPressed.connect(self.tap_send)
        btn_tap = QPushButton("Send"); btn_tap.clicked.connect(self.tap_send)
        tap_row.addWidget(self.tap_input); tap_row.addWidget(btn_tap)
        v.addLayout(tap_row)
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

    def tap_send(self):
        q = self.tap_input.text().strip()
        if not q: return
        self.tap_input.clear()
        self.tap_log.append(f"<b>you:</b> {q}")
        threading.Thread(target=self._tap_answer, args=(q,), daemon=True).start()

    def _tap_answer(self, q):
        try:
            from pathlib import Path as P
            proc = subprocess.run([sys.executable, str(P.home()/"blackhorse"/"skin"/"mastermind.py"), "ask", q],
                                  capture_output=True, text=True, timeout=240)
            out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else (proc.stderr.strip()[:200] or "(no answer)")
        except Exception as e:
            out = f"(skin offline or busy: {e})"
        self.tap_log.append(f"<b>skin:</b> {out}")

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
        """)
    except ImportError:
        pass
    window = BlackHorse()          # keep a reference — temp gets garbage-collected!
    window.show()
    sys.exit(app.exec())
