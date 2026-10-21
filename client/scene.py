"""Rendu 2D du carrefour avec QGraphicsScene / QGraphicsView (vue de dessus)."""
import random

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import (QGraphicsEllipseItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView)

from client.simulation import CX, CY, HALF, IN_DIR, SIDES, _right

NOPEN = QPen(Qt.PenStyle.NoPen)
LIGHT_COLORS = {"red": "#ff3b30", "orange": "#ffb020", "green": "#2ee36b"}


class IntersectionScene(QGraphicsScene):
    """Scène 800x800 : décor statique + synchronisation des véhicules et des feux."""

    def __init__(self):
        super().__init__(0, 0, 800, 800)
        self.v_items, self.lights = {}, {}

    def build(self, cfg):
        """Redessine le décor pour le type de carrefour `cfg`."""
        self.clear()
        self.v_items, self.lights = {}, {}
        rnd = random.Random(7)
        self.setBackgroundBrush(QColor("#8c9196"))
        for x, y in [(0, 0), (460, 0), (0, 460), (460, 460)]:
            self.addRect(x, y, 340, 340, NOPEN, QBrush(QColor("#9aa0a5")))
            self.addRect(x + 22, y + 22, 296, 296, NOPEN, QBrush(QColor("#3f6b44")))
            self.addRect(x + 70, y + 70, 200, 200, QPen(QColor("#1c2733"), 2), QBrush(QColor("#4b5766")))
            self.addRect(x + 90, y + 90, 160, 160, NOPEN, QBrush(QColor("#6b7889")))
            for _ in range(5):
                cx, cy, r = x + rnd.choice([40, 300]), y + rnd.randint(40, 300), rnd.randint(9, 15)
                self.addEllipse(cx - r, cy - r, 2 * r, 2 * r, NOPEN, QBrush(QColor("#2b5a31")))
        asphalt, strips = QBrush(QColor("#3b3f45")), {
            "Nord": (340, 0, 120, 340), "Sud": (340, 460, 120, 340), "Est": (460, 340, 340, 120), "Ouest": (0, 340, 340, 120)}
        for side, rect in strips.items():
            if side in cfg["branches"]:
                self.addRect(*rect, NOPEN, asphalt)
            else:  # branche absente : îlot bâti
                self.addRect(*rect, NOPEN, QBrush(QColor("#9aa0a5")))
                self.addRect(rect[0] + 20, rect[1] + 20, rect[2] - 40, rect[3] - 40, NOPEN, QBrush(QColor("#5a6573")))
        self.addRect(340, 340, 120, 120, NOPEN, asphalt)
        dash = QPen(QColor("#e8c44a"), 2, Qt.PenStyle.CustomDashLine)
        dash.setDashPattern([6, 6])
        zebra = QPen(QColor("#f2f2f2"), 16)
        zebra.setDashPattern([0.6, 0.6])
        zebra.setCapStyle(Qt.PenCapStyle.FlatCap)
        for side in cfg["branches"]:
            di = IN_DIR[side]
            ri = _right(di)
            far = (CX - di[0] * 400, CY - di[1] * 400)
            near = (CX - di[0] * HALF, CY - di[1] * HALF)
            self.addLine(far[0], far[1], near[0], near[1], dash)
            cw = (CX - di[0] * 68, CY - di[1] * 68)
            self.addLine(cw[0] - ri[0] * 60, cw[1] - ri[1] * 60, cw[0] + ri[0] * 60, cw[1] + ri[1] * 60, zebra)
            sl = (CX - di[0] * 82, CY - di[1] * 82)
            self.addLine(sl[0], sl[1], sl[0] + ri[0] * 60, sl[1] + ri[1] * 60, QPen(QColor("white"), 4))
            pole = (sl[0] - di[0] * 4 + ri[0] * 70, sl[1] - di[1] * 4 + ri[1] * 70)
            if cfg["mode"] == "lights":
                self._add_light(side, pole)
            else:
                self._add_sign(cfg["mode"], pole)

    def _add_light(self, side, pos):
        box = QGraphicsRectItem(-7, -19, 14, 38)
        box.setBrush(QColor("#10151b"))
        box.setPen(QPen(QColor("#000"), 1))
        box.setPos(*pos)
        box.setZValue(20)
        self.addItem(box)
        self.lights[side] = []
        for i, y in enumerate((-11, 0, 11)):
            dot = QGraphicsEllipseItem(-4, -4, 8, 8, box)
            dot.setPos(0, y)
            dot.setPen(NOPEN)
            self.lights[side].append(dot)

    def _add_sign(self, mode, pos):
        if mode == "stop":
            sign = QGraphicsRectItem(-13, -8, 26, 16)
            sign.setBrush(QColor("#c8102e"))
            sign.setPen(QPen(QColor("white"), 1.5))
            txt = QGraphicsSimpleTextItem("STOP", sign)
            txt.setBrush(QColor("white"))
            txt.setScale(0.55)
            txt.setPos(-11, -7)
        else:  # panneau « priorité à droite » (losange jaune)
            sign = QGraphicsPolygonItem(QPolygonF([QPointF(0, -13), QPointF(13, 0), QPointF(0, 13), QPointF(-13, 0)]))
            sign.setBrush(QColor("#f0b429"))
            sign.setPen(QPen(QColor("white"), 2))
        sign.setPos(*pos)
        sign.setZValue(20)
        self.addItem(sign)

    def _make_vehicle(self, v):
        length, width = v.length, v.width
        body = QGraphicsRectItem(-length / 2, -width / 2, length, width)
        body.setBrush(QColor(v.color))
        body.setPen(QPen(QColor(15, 15, 15, 210), 1))
        body.setZValue(10 + (5 if v.emergency else 0))
        glass = QGraphicsRectItem(length * 0.12, -width / 2 + 2, length * 0.22, width - 4, body)
        glass.setBrush(QColor(25, 35, 48, 230))
        glass.setPen(NOPEN)
        siren = None
        if v.emergency:
            siren = QGraphicsEllipseItem(-9, -9, 18, 18, body)
            siren.setPen(NOPEN)
            stripe = QGraphicsRectItem(-length / 2 + 2, -2, length - 4, 4, body)
            stripe.setBrush(QColor("#d62828") if v.color != "#d62828" else QColor("#f5f5f5"))
            stripe.setPen(NOPEN)
        self.addItem(body)
        return body, siren

    def sync(self, sim):
        """Aligne les éléments graphiques sur l'état de la simulation."""
        live = set()
        for v in sim.vehicles:
            live.add(v.id)
            if v.id not in self.v_items:
                self.v_items[v.id] = self._make_vehicle(v)
            body, siren = self.v_items[v.id]
            body.setPos(v.x, v.y)
            body.setRotation(v.angle)
            if siren is not None:
                siren.setBrush(QColor(255, 40, 40, 150) if int(sim.t * 4) % 2 else QColor(40, 130, 255, 150))
        for vid in [k for k in self.v_items if k not in live]:
            self.removeItem(self.v_items.pop(vid)[0])
        for side, dots in self.lights.items():
            state = sim.light_state(side)
            for dot, name in zip(dots, ("red", "orange", "green")):
                dot.setBrush(QColor(LIGHT_COLORS[name]) if state == name else QColor("#2a2f36"))


class SimulationView(QGraphicsView):
    """Vue qui conserve la scène entièrement visible."""

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
