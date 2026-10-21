"""Fenêtre principale de l'IHM « Carrefour connecté » (SAÉ 3.02 – groupe A113)."""
import json
import math
import time
from collections import deque
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit,
                             QPushButton, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                             QVBoxLayout, QWidget)

from client.network import NetworkClient
from client.scene import IntersectionScene, SimulationView
from client.simulation import EMERGENCY_TYPES, JUNCTIONS, SIDES, Simulation

SERIES = {"Nord": "#1e88ff", "Sud": "#ff9f1c", "Est": "#2ee36b", "Ouest": "#ff3b30"}
EVENT_COLORS = {"info": "#8fb3d9", "approach": "#4aa3ff", "priority": "#2ee36b", "v2v": "#c58bff",
                "crossed": "#4aa3ff", "normal": "#8fb3d9", "net": "#ffb020"}
STYLE = """
QMainWindow, QWidget#root { background:#08182b; }
QWidget { color:#dce8f5; font-size:13px; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:transparent; padding:12px 18px; color:#9db4cc; font-size:14px; border-bottom:3px solid transparent; }
QTabBar::tab:selected { color:#2e9bff; border-bottom:3px solid #2e9bff; background:#10294a; }
QFrame#panel { background:#0d2240; border:1px solid #1d3b61; border-radius:6px; }
QLabel#title { font-weight:bold; font-size:15px; padding:4px 0 6px 0; }
QLabel#muted { color:#9db4cc; }
QLabel#big { font-size:20px; font-weight:bold; }
QComboBox, QLineEdit, QSpinBox { background:#0a1a30; border:1px solid #27466f; border-radius:4px; padding:6px; }
QComboBox QAbstractItemView { background:#0a1a30; selection-background-color:#1e5fa8; }
QPushButton { background:#1e7bf0; color:white; border:0; border-radius:4px; padding:10px; font-weight:bold; }
QPushButton:hover { background:#3a90ff; } QPushButton:disabled { background:#27384f; color:#7d8da3; }
QPushButton#danger { background:#17a870; } QPushButton#danger:hover { background:#1fc684; }
QPushButton#ghost { background:#16335a; }
QSlider::groove:horizontal { height:6px; background:#27466f; border-radius:3px; }
QSlider::handle:horizontal { background:#4aa3ff; width:16px; margin:-5px 0; border-radius:8px; }
QListWidget, QPlainTextEdit, QTableWidget { background:#0a1a30; border:1px solid #1d3b61; gridline-color:#1d3b61; }
QHeaderView::section { background:#12315a; border:0; padding:5px; }
"""


def panel(title=None):
    """Crée un cadre « carte » avec titre optionnel ; renvoie (cadre, layout)."""
    frame = QFrame()
    frame.setObjectName("panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 10, 14, 12)
    if title:
        t = QLabel(title)
        t.setObjectName("title")
        lay.addWidget(t)
    return frame, lay


def muted(text):
    """Petit libellé grisé."""
    lab = QLabel(text)
    lab.setObjectName("muted")
    return lab


class StatusRow(QWidget):
    """Ligne « pastille – libellé – valeur » de l'état du système."""

    def __init__(self, name):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        self.dot, self.value = QLabel("●"), QLabel("")
        lay.addWidget(self.dot)
        lay.addWidget(QLabel(name), 1)
        lay.addWidget(self.value)

    def set(self, ok, text):
        """Met à jour couleur et texte."""
        color = "#2ee36b" if ok else "#ff5a4f"
        self.dot.setStyleSheet(f"color:{color}; font-size:16px;")
        self.value.setText(text)


class FlowChart(QWidget):
    """Courbes du nombre de véhicules par branche, dessinées avec QPainter."""
    HIST = 60

    def __init__(self):
        super().__init__()
        self.data = deque(maxlen=self.HIST)
        self.setMinimumHeight(150)

    def add(self, counts):
        """Ajoute un échantillon {branche: nombre}."""
        self.data.append(dict(counts))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(32, 8, -8, -30)
        ymax = max(10, math.ceil(max((max(d.values()) for d in self.data), default=10) / 5) * 5)
        p.setFont(self.font())
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.setPen(QPen(QColor("#22416a"), 1))
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
            p.setPen(QColor("#9db4cc"))
            p.drawText(0, int(y) - 8, 28, 16, Qt.AlignmentFlag.AlignRight, str(int(ymax * i / 4)))
        step = r.width() / (self.HIST - 1)
        for side, color in SERIES.items():
            pts = [QPointF(r.right() - (len(self.data) - 1 - k) * step, r.bottom() - r.height() * d[side] / ymax)
                   for k, d in enumerate(self.data)]
            p.setPen(QPen(QColor(color), 2))
            if len(pts) > 1:
                p.drawPolyline(QPolygonF(pts))
        x = r.left() + 20
        for side, color in SERIES.items():
            p.fillRect(int(x), self.height() - 18, 22, 6, QColor(color))
            p.setPen(QColor("#9db4cc"))
            p.drawText(int(x) + 28, self.height() - 22, 50, 16, Qt.AlignmentFlag.AlignLeft, side)
            x += 80


class MainWindow(QMainWindow):
    """Assemble simulation, réseau (QThread), base de données distante et statistiques."""

    def __init__(self, host, port, token=""):
        super().__init__()
        self.setWindowTitle("SAÉ 3.02 - Carrefour connecté")
        self.resize(1440, 900)
        self.setStyleSheet(STYLE)
        self.db_name = "—"
        self.sim = Simulation()
        self.sim.on_event, self.sim.on_request, self.sim.on_crossed = self.log_event, self.send_request, self.send_crossed
        self.scene = IntersectionScene()
        self.scene.build(self.sim.cfg)
        self.net = NetworkClient(host, port, token)
        self.tele = []   # messages de télémétrie en attente d'envoi (envoi groupé toutes les 0,4 s)
        self.sim.on_telemetry = self.queue_telemetry
        self.net.status.connect(self.on_net_status)
        self.net.received.connect(self.on_net_message)
        self.net.frame.connect(self.on_frame)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        outer.addWidget(self._build_footer())
        self.tabs.addTab(self._build_sim_tab(), "▦  Simulation")
        self.tabs.addTab(self._build_network_tab(), "⌘  Réseau")
        self.tabs.addTab(self._build_db_tab(), "🗄  Base de données")
        self.tabs.addTab(self._build_stats_tab(), "▮  Statistiques")
        self.tabs.addTab(self._build_settings_tab(host, port, token), "⚙  Paramètres")
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.tabs.setCornerWidget(self._build_corner(), Qt.Corner.TopRightCorner)

        self.refresh_destinations()
        self.last, self.last_flow, self.last_metrics, self.last_stats = time.perf_counter(), 0.0, 0.0, 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        clock = QTimer(self)
        clock.timeout.connect(lambda: self.clock.setText(datetime.now().strftime("%H:%M:%S\n%d/%m/%Y")))
        clock.start(1000)
        self._clock_timer = clock
        self.clock.setText(datetime.now().strftime("%H:%M:%S\n%d/%m/%Y"))
        flush = QTimer(self)
        flush.timeout.connect(self.flush_telemetry)
        flush.start(400)
        self._flush_timer = flush
        self.net.start()
        self.update_status()

    # ------------------------------------------------------------------ construction
    def _build_corner(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        self.srv_label = QLabel("Serveur déconnecté")
        self.clock = QLabel("")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.clock.setMinimumHeight(44)
        lay.addWidget(self.srv_label)
        lay.addSpacing(24)
        lay.addWidget(self.clock)
        return w

    def _build_footer(self):
        f = QFrame()
        f.setStyleSheet("background:#061224; border-top:1px solid #1d3b61;")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(20, 6, 20, 6)
        lay.addWidget(QLabel("SAÉ 3.02 - Carrefour connecté   |   Groupe A113 - BENCHEKCHOU Nail-Omar & LEMASSON Ewan"), 1)
        self.foot_v2i, self.foot_db = QLabel("● V2I"), QLabel("● MariaDB")
        for lab in (self.foot_v2i, self.foot_db):
            lay.addWidget(lab)
        lay.addWidget(QLabel("v1.0.0"))
        return f

    def _build_sim_tab(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        # ---- colonne gauche
        left = QVBoxLayout()
        cfg, cl = panel("Configuration de la simulation")
        self.cb_junction, self.cb_vehicle, self.cb_dest = QComboBox(), QComboBox(), QComboBox()
        self.cb_junction.addItems(list(JUNCTIONS))
        self.cb_vehicle.addItems(list(EMERGENCY_TYPES))
        self.cb_junction.currentTextChanged.connect(self.on_junction_changed)
        for text, widget in (("Type de carrefour", self.cb_junction), ("Véhicule d'urgence", self.cb_vehicle),
                             ("Destination du véhicule d'urgence", self.cb_dest)):
            cl.addWidget(muted(text))
            cl.addWidget(widget)
        self.sl_density, self.lb_density = self._slider(1, 10, 6, "Densité du trafic", cl)
        self.sl_speed, self.lb_speed = self._slider(5, 20, 10, "Vitesse des véhicules", cl)
        self.sl_density.valueChanged.connect(self.on_sliders)
        self.sl_speed.valueChanged.connect(self.on_sliders)
        self.btn_start = QPushButton("Lancer la simulation")
        self.btn_start.clicked.connect(self.toggle_simulation)
        self.btn_send = QPushButton("Envoyer le véhicule d'urgence")
        self.btn_send.setObjectName("ghost")
        self.btn_send.clicked.connect(self.send_emergency_vehicle)
        cl.addWidget(self.btn_start)
        cl.addWidget(self.btn_send)
        left.addWidget(cfg)
        cmd, ml = panel("⚠  Commande d'urgence")
        ml.addWidget(QLabel("Demande de priorité V2I/V2V"))
        self.btn_priority = QPushButton("Appuyer  (Véhicule d'urgence)")
        self.btn_priority.setObjectName("danger")
        self.btn_priority.clicked.connect(self.sim.request_priority)
        ml.addWidget(self.btn_priority)
        left.addWidget(cmd)
        st, sl = panel("État du système")
        self.row_lights, self.row_net = StatusRow("Régulation du carrefour"), StatusRow("Réseau V2I/V2V")
        self.row_db, self.row_sim = StatusRow("Base de données"), StatusRow("Simulation")
        for row in (self.row_lights, self.row_net, self.row_db, self.row_sim):
            sl.addWidget(row)
        left.addWidget(st)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(320)
        left.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(lw)
        # ---- centre
        self.view = SimulationView(self.scene)
        lay.addWidget(self.view, 1)
        # ---- colonne droite
        right = QVBoxLayout()
        jr, jl = panel("Journal des événements")
        self.journal = QListWidget()
        self.journal.setMinimumHeight(230)
        self.journal.setWordWrap(True)
        self.journal.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        jl.addWidget(self.journal)
        right.addWidget(jr, 2)
        sr, sgl = panel("Statistiques")
        grid = QGridLayout()
        self.stat_labels = {}
        for i, (key, cap) in enumerate((("veh", "Véhicules en circulation"), ("em", "Véhicules d'urgence"),
                                        ("wait", "Temps d'attente moyen"), ("int", "Nombre d'interventions"))):
            box = QVBoxLayout()
            box.addWidget(muted(cap))
            val = QLabel("0")
            val.setObjectName("big")
            box.addWidget(val)
            self.stat_labels[key] = val
            grid.addLayout(box, i // 2, i % 2)
        sgl.addLayout(grid)
        right.addWidget(sr)
        fr, fl = panel("Flux de trafic (dernières minutes)")
        self.chart = FlowChart()
        fl.addWidget(self.chart)
        right.addWidget(fr, 1)
        rw = QWidget()
        rw.setLayout(right)
        right.setContentsMargins(0, 0, 0, 0)
        rw.setFixedWidth(360)
        lay.addWidget(rw)
        return page

    def _slider(self, lo, hi, val, caption, layout):
        row = QHBoxLayout()
        row.addWidget(muted(caption), 1)
        label = QLabel("")
        row.addWidget(label)
        layout.addLayout(row)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        layout.addWidget(sl)
        return sl, label

    def _build_network_tab(self):
        page, lay = panel("Trames V2I / V2V (TCP – JSON)")
        self.net_log = QPlainTextEdit()
        self.net_log.setReadOnly(True)
        self.net_log.setMaximumBlockCount(500)
        lay.addWidget(self.net_log)
        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(page)
        return wrap

    def _build_db_tab(self):
        page, lay = panel("Historique stocké en base (via le serveur)")
        top = QHBoxLayout()
        self.cb_table = QComboBox()
        for label, name in (("★ Chronologie d'une intervention", "chronologie"), ("sessions_simulation", "sessions_simulation"), ("vehicules", "vehicules"),
                            ("evenements_priorite", "evenements_priorite"), ("evenements_trafic", "evenements_trafic"), ("journal_feux", "journal_feux")):
            self.cb_table.addItem(label, name)
        btn = QPushButton("Actualiser")
        btn.clicked.connect(self.request_history)
        top.addWidget(self.cb_table, 1)
        top.addWidget(btn)
        lay.addLayout(top)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table)
        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(page)
        return wrap

    def _build_stats_tab(self):
        page, lay = panel("Statistiques de la session")
        self.stats_text = QLabel("")
        self.stats_text.setStyleSheet("font-size:15px;")
        lay.addWidget(self.stats_text)
        self.chart_big = FlowChart()
        self.chart_big.setMinimumHeight(360)
        lay.addWidget(self.chart_big, 1)
        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(page)
        return wrap

    def _build_settings_tab(self, host, port, token=""):
        page, lay = panel("Paramètres de connexion et de simulation")
        self.ed_host, self.sp_port = QLineEdit(host), QSpinBox()
        self.ed_token = QLineEdit(token)
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(port)
        self.chk_auto = QCheckBox("Demande de priorité automatique à l'approche du carrefour (V2I auto)")
        self.chk_auto.toggled.connect(lambda on: setattr(self.sim, "auto_request", on))
        btn = QPushButton("Appliquer et reconnecter")
        btn.clicked.connect(lambda: self.net.configure(self.ed_host.text().strip(), self.sp_port.value(), self.ed_token.text()))
        for w in (muted("Adresse du serveur (VM Serveur)"), self.ed_host, muted("Port TCP"), self.sp_port, muted("Jeton d'authentification (facultatif)"), self.ed_token, self.chk_auto, btn):
            lay.addWidget(w)
        lay.addStretch(1)
        wrap = QWidget()
        QVBoxLayout(wrap).addWidget(page)
        return wrap

    # ------------------------------------------------------------------ simulation
    def refresh_destinations(self):
        """Recharge la liste des destinations selon les branches du carrefour."""
        current = self.cb_dest.currentText()
        self.cb_dest.clear()
        self.cb_dest.addItems(list(self.sim.cfg["branches"]))
        if current in self.sim.cfg["branches"]:
            self.cb_dest.setCurrentText(current)
        self.on_sliders()

    def on_junction_changed(self, name):
        """Change de carrefour (réinitialise la scène)."""
        was_running = self.sim.running
        self.sim.set_junction(name)
        self.scene.build(self.sim.cfg)
        self.refresh_destinations()
        if was_running:
            self.sim.start()
        self.update_status()

    def on_sliders(self):
        """Applique densité et vitesse."""
        self.sim.density = self.sl_density.value()
        self.sim.speed = self.sl_speed.value() / 10
        self.lb_density.setText(("Faible", "Moyenne", "Élevée")[min(2, (self.sim.density - 1) // 3)])
        self.lb_speed.setText(f"×{self.sim.speed:.1f}")

    def toggle_simulation(self):
        """Démarre ou arrête la simulation."""
        if self.sim.running:
            self.sim.stop()
            self.btn_start.setText("Lancer la simulation")
        else:
            self.sim.start()
            self.btn_start.setText("Arrêter la simulation")
        self.update_status()

    def send_emergency_vehicle(self):
        """Fait arriver le véhicule d'urgence choisi."""
        if not self.sim.running:
            self.toggle_simulation()
        self.sim.spawn_emergency(self.cb_vehicle.currentText(), self.cb_dest.currentText())

    def update_status(self):
        """Rafraîchit le panneau « État du système » et les boutons."""
        mode = self.sim.cfg["mode"]
        if self.sim.emergency_active:
            self.row_lights.set(True, "Priorité urgence")
        else:
            self.row_lights.set(True, {"lights": "Normal", "stop": "Régime STOP", "prio": "Priorité à droite"}[mode])
        up = self.net.connected
        self.row_net.set(up, "Connecté" if up else "Déconnecté")
        self.row_db.set(up, f"Connectée ({self.db_name})" if up else "Indisponible")
        self.row_sim.set(self.sim.running, "En cours" if self.sim.running else "Arrêtée")
        em = self.sim.em
        self.btn_priority.setEnabled(bool(em and not self.sim.em_requested and not em.crossed))
        self.btn_send.setEnabled(not (em and not em.crossed))
        for lab, ok in ((self.foot_v2i, up), (self.foot_db, up)):
            lab.setStyleSheet(f"color:{'#2ee36b' if ok else '#ff5a4f'};")

    def tick(self):
        """Boucle d'animation (~60 Hz) : simulation, rendu, statistiques."""
        now = time.perf_counter()
        dt, self.last = min(now - self.last, 0.05), now
        if self.sim.running:
            self.sim.update(dt)
            if now - self.last_flow > 2.0:
                self.last_flow = now
                counts = self.sim.counts_by_origin()
                self.chart.add(counts)
                self.chart_big.add(counts)
        self.scene.sync(self.sim)
        if now - self.last_stats > 0.25:
            self.last_stats = now
            s = self.sim
            self.stat_labels["veh"].setText(str(len(s.vehicles)))
            self.stat_labels["em"].setText("1" if s.em and not s.em.crossed else "0")
            self.stat_labels["wait"].setText(f"{s.avg_wait():.0f} s")
            self.stat_labels["int"].setText(str(s.interventions))
            self.stats_text.setText(f"Véhicules : {len(s.vehicles)}    Attente moyenne : {s.avg_wait():.1f} s    "
                                    f"Interventions : {s.interventions}    Véhicules entrés : {sum(s.flow_entered.values())}")
            self.update_status()

    # ------------------------------------------------------------------ journal
    def log_event(self, kind, text):
        """Ajoute une ligne au journal local et la renvoie au serveur pour archivage."""
        item = QListWidgetItem(f"●  {datetime.now():%H:%M:%S}   {text}")
        item.setForeground(QColor(EVENT_COLORS.get(kind, "#dce8f5")))
        self.journal.insertItem(0, item)
        while self.journal.count() > 200:
            self.journal.takeItem(self.journal.count() - 1)

    # ------------------------------------------------------------------ réseau
    def send_request(self, payload):
        """Envoie la demande V2I ; sans serveur, bascule en mode dégradé local."""
        if self.net.connected:
            self.net.send(payload)
            self.log_event("net", "Demande V2I transmise au serveur, en attente de la réponse…")
        else:
            self.log_event("net", "Serveur injoignable : mode dégradé, décision locale")
            self.sim.grant_priority()

    def queue_telemetry(self, msg):
        """Met un message de télémétrie en file ; les messages de session partent immédiatement."""
        self.tele.append(msg)
        if msg["type"] in ("session_start", "session_end"):
            self.flush_telemetry()

    def flush_telemetry(self):
        """Envoie au serveur les messages en attente (un seul message « batch »)."""
        if self.tele and self.net.connected:
            self.net.send({"type": "batch", "messages": self.tele[:150]})
            del self.tele[:150]
        elif not self.net.connected:
            self.tele.clear()

    def send_crossed(self, payload):
        """Notifie le serveur que le véhicule a franchi le carrefour."""
        if self.net.connected:
            self.net.send(payload)

    def on_net_status(self, up, target):
        """Réaction aux changements d'état de la connexion (signal Qt)."""
        self.srv_label.setText(f"{'Serveur connecté' if up else 'Serveur déconnecté'}\n({target})")
        self.srv_label.setStyleSheet(f"color:{'#2ee36b' if up else '#ff5a4f'};")
        self.log_event("net", f"Réseau : {'connecté à' if up else 'déconnecté de'} {target}")
        self.update_status()

    def on_net_message(self, msg):
        """Traite un message reçu du serveur (signal Qt, thread GUI)."""
        kind = msg.get("type")
        if kind == "welcome":
            self.db_name = msg.get("db", "?")
            self.foot_db.setText(f"● {self.db_name}")
            self.update_status()
        elif kind == "priority_grant":
            self.sim.grant_priority()
        elif kind == "v2v_alert":
            self.log_event("v2v", f"V2V : alerte reçue ({msg.get('vehicle')} depuis {msg.get('approach')})")
        elif kind == "history_result":
            self.fill_table(msg["columns"], msg["rows"])

    def on_frame(self, direction, msg):
        """Affiche une trame dans l'onglet Réseau."""
        arrow = "→ TX" if direction == "TX" else "← RX"
        self.net_log.appendPlainText(f"{datetime.now():%H:%M:%S}  {arrow}  {json.dumps(msg, ensure_ascii=False)}")

    def on_tab_changed(self, index):
        """Charge l'historique en entrant dans l'onglet Base de données."""
        if index == 2:
            self.request_history()

    def request_history(self):
        """Demande au serveur le contenu d'une table."""
        if self.net.connected:
            if self.cb_table.currentData() == "chronologie":
                self.net.send({"type": "chronology"})
            else:
                self.net.send({"type": "history", "table": self.cb_table.currentData(), "limit": 100})

    def fill_table(self, columns, rows):
        """Remplit le tableau de l'onglet Base de données."""
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))

    def closeEvent(self, event):
        """Arrête proprement le thread réseau."""
        self.net.stop()
        self.net.wait(2000)
        super().closeEvent(event)
