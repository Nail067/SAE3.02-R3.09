"""Moteur de simulation du carrefour connecté (logique pure, sans dépendance Qt).

Circulation à droite, feux tricolores / STOP / priorité à droite, véhicules d'urgence
et prise en charge des demandes de priorité V2I/V2V.
"""
import math
import random
from collections import deque

CX = CY = 400                 # centre de la scène (800 x 800)
EDGE, HALF, LANE = 400, 60, 30
S_STOP = EDGE - 82            # abscisse curviligne de la ligne d'arrêt
ACC, DECEL = 90.0, 200.0      # accélération / décélération (px/s²)
SIDES = ("Nord", "Sud", "Est", "Ouest")
IN_DIR = {"Nord": (0, 1), "Sud": (0, -1), "Est": (-1, 0), "Ouest": (1, 0)}    # sens en entrant depuis ce côté
OUT_DIR = {"Nord": (0, -1), "Sud": (0, 1), "Est": (1, 0), "Ouest": (-1, 0)}   # sens en sortant vers ce côté
OPP = {"Nord": "Sud", "Sud": "Nord", "Est": "Ouest", "Ouest": "Est"}
RIGHT_OF = {"Nord": "Ouest", "Ouest": "Sud", "Sud": "Est", "Est": "Nord"}     # côté prioritaire (à droite)

JUNCTIONS = {
    "Feux tricolores – 4 branches": dict(branches=SIDES, mode="lights", phases=[{"Nord", "Sud"}, {"Est", "Ouest"}]),
    "Feux tricolores – 3 branches (T)": dict(branches=("Nord", "Sud", "Ouest"), mode="lights",
                                             phases=[{"Nord", "Sud"}, {"Ouest"}]),
    "STOP – 4 branches": dict(branches=SIDES, mode="stop", phases=[]),
    "Priorité à droite – 4 branches": dict(branches=SIDES, mode="prio", phases=[]),
}
DB_MAP = {  # (type_carrefour, regulation) tels que stockés en base
    "Feux tricolores – 4 branches": ("4 branches", "feux"), "Feux tricolores – 3 branches (T)": ("T", "feux"),
    "STOP – 4 branches": ("4 branches", "panneaux"), "Priorité à droite – 4 branches": ("4 branches", "panneaux")}
KIND_FR = {"car": "voiture", "truck": "camion", "bus": "bus"}
EMERGENCY_TYPES = {"Ambulance": "#f4f6f8", "Pompiers": "#d62828", "Police": "#2b4c9b"}
CAR_COLORS = ["#d93a2f", "#2f6fd9", "#e8e8e8", "#202225", "#f0b429", "#2fa36b", "#e0782c", "#7a8590", "#8a4fd0"]


def _right(d):
    return (-d[1], d[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


class Path:
    """Trajectoire polygonale paramétrée par l'abscisse curviligne s."""

    def __init__(self, pts):
        self.pts, self.cum = pts, [0.0]
        for a, b in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.length = self.cum[-1]
        self.s_exit = self.cum[-2]  # fin de la zone de croisement
        self._box = None

    def box_points(self):
        """Points échantillonnés de la trajectoire dans la zone de croisement."""
        if self._box is None:
            self._box = [self.pose(s)[:2] for s in range(EDGE - HALF, int(self.s_exit) + 1, 10)]
        return self._box

    def pose(self, s):
        """Renvoie (x, y, angle_radians) à l'abscisse s."""
        s = min(max(s, 0.0), self.length - 1e-6)
        i = 1
        while self.cum[i] < s:
            i += 1
        a, b = self.pts[i - 1], self.pts[i]
        seg = self.cum[i] - self.cum[i - 1]
        t = (s - self.cum[i - 1]) / seg if seg else 0.0
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, math.atan2(b[1] - a[1], b[0] - a[0])


def build_path(origin, dest, lane_off=LANE):
    """Construit la trajectoire (tout droit, droite ou gauche) d'un côté d'entrée vers un côté de sortie."""
    di, do = IN_DIR[origin], OUT_DIR[dest]
    ri, ro = _right(di), _right(do)
    p0 = (CX - di[0] * EDGE + ri[0] * lane_off, CY - di[1] * EDGE + ri[1] * lane_off)
    e = (CX - di[0] * HALF + ri[0] * lane_off, CY - di[1] * HALF + ri[1] * lane_off)
    x = (CX + do[0] * HALF + ro[0] * LANE, CY + do[1] * HALF + ro[1] * LANE)
    q = (CX + do[0] * EDGE + ro[0] * LANE, CY + do[1] * EDGE + ro[1] * LANE)
    pts = [p0, e]
    if di != do:  # virage : courbe de Bézier quadratique
        k = _dot((x[0] - e[0], x[1] - e[1]), di)
        c = (e[0] + di[0] * k, e[1] + di[1] * k)
        for i in range(1, 12):
            t = i / 12
            pts.append(((1 - t) ** 2 * e[0] + 2 * (1 - t) * t * c[0] + t * t * x[0],
                        (1 - t) ** 2 * e[1] + 2 * (1 - t) * t * c[1] + t * t * x[1]))
    return Path(pts + [x, q])


class LightController:
    """Cycle de feux (vert 8 s, orange 2 s, tout-rouge 1 s) avec préemption pour véhicule d'urgence."""

    def __init__(self, phases):
        self.phases, self.idx, self.t, self.stage = [set(ph) for ph in phases], 0, 0.0, "green"
        self.hold, self.target, self.base = False, 0, len(self.phases)

    def request(self, approach):
        """Ajoute une phase réservée à la seule voie `approach` et la force jusqu'à `release()`."""
        self.phases.append({approach})
        self.target, self.hold = len(self.phases) - 1, True

    def release(self):
        """Supprime la phase d'urgence et reprend le cycle normal."""
        self.hold, self.t = False, 5.0
        if len(self.phases) > self.base:
            tmp = self.phases.pop()
            if self.idx >= len(self.phases):
                self.idx = next(i for i, ph in enumerate(self.phases) if tmp <= ph)
            self.target = min(self.target, len(self.phases) - 1)

    def update(self, dt):
        """Fait avancer le cycle de feux."""
        self.t += dt
        if self.stage == "green":
            if self.hold and self.idx == self.target:
                self.t = 0.0
            elif self.t >= (0.0 if self.hold else 8.0):
                self.stage, self.t = "orange", 0.0
        elif self.stage == "orange" and self.t >= 2.0:
            self.stage, self.t = "allred", 0.0
        elif self.stage == "allred" and self.t >= 1.0:
            self.idx = self.target if self.hold else (self.idx + 1) % len(self.phases)
            self.stage, self.t = "green", 0.0

    def state(self, approach):
        """État du feu ('green', 'orange' ou 'red') pour une branche."""
        if approach in self.phases[self.idx] and self.stage in ("green", "orange"):
            return self.stage
        return "red"


_CONFLICTS = {}


def paths_conflict(a, b):
    """Vrai si les trajectoires de deux véhicules se croisent ou fusionnent dans le carrefour."""
    same = a.origin == b.origin
    if same and not (a.emergency or b.emergency):
        return False  # même branche : géré par le suivi de véhicule
    k = (a.key, b.key)
    if k not in _CONFLICTS:  # seuil plus serré pour l'urgence qui double dans sa propre file
        _CONFLICTS[k] = any(math.dist(p, q) < (19 if same else 26)
                            for p in a.path.box_points() for q in b.path.box_points())
    return _CONFLICTS[k]


class Vehicle:
    """Véhicule suivant une trajectoire, avec modèle de suivi simple."""
    _next_id = 0
    SPECS = {"car": (30, 16, 85), "truck": (46, 18, 70), "bus": (52, 18, 70), "emergency": (34, 16, 140)}

    def __init__(self, kind, origin, dest, color, lane_off=LANE):
        Vehicle._next_id += 1
        self.id, self.kind, self.origin, self.dest, self.color = Vehicle._next_id, kind, origin, dest, color
        self.length, self.width, self.vmax = self.SPECS[kind][0], self.SPECS[kind][1], self.SPECS[kind][2]
        self.emergency = kind == "emergency"
        self.path = build_path(origin, dest, lane_off)
        self.s, self.v = 0.0, 40.0
        self.wait = self.stop_timer = self.blocked = 0.0
        self.arrival = None
        self.entered = self.done = self.crossed = self.claimed = False
        self.etat = "en circulation"  # état tel que stocké en base
        self.key = (origin, dest, lane_off)
        self.x = self.y = self.angle = self.hx = self.hy = 0.0
        self.refresh_pose()

    def refresh_pose(self):
        """Met à jour position et cap depuis l'abscisse curviligne."""
        self.x, self.y, a = self.path.pose(self.s)
        self.hx, self.hy, self.angle = math.cos(a), math.sin(a), math.degrees(a)


class Simulation:
    """Simulation complète : trafic, régulation, urgence."""

    def __init__(self):
        self.on_event = lambda kind, text: None
        self.on_request = lambda payload: None
        self.on_crossed = lambda payload: None
        self.on_telemetry = lambda msg: None   # messages destinés au serveur (session, véhicules, feux, événements)
        self.telemetry, self._rid = False, 0
        self.density, self.speed, self.auto_request = 6, 1.0, False
        self.running = False
        self.set_junction(next(iter(JUNCTIONS)))

    # ---------------------------------------------------------------- configuration
    def set_junction(self, name):
        """Change de type de carrefour et remet la simulation à zéro."""
        self.junction, self.cfg = name, JUNCTIONS[name]
        self.ctrl = LightController(self.cfg["phases"]) if self.cfg["mode"] == "lights" else None
        self.vehicles, self.em, self.t = [], None, 0.0
        self.em_requested = self.granted = False
        self.interventions, self.waits = 0, deque(maxlen=50)
        self.flow_entered = dict.fromkeys(SIDES, 0)
        self.last_light, self.jam, self.last_stop, self.last_jam, self.return_until = {}, {}, {}, 0.0, 0.0

    def start(self):
        """Démarre la simulation avec un préchauffage pour obtenir directement du trafic dense."""
        self.set_junction(self.junction)
        self.running = True
        for _ in range(120):
            self.update(0.1, warmup=True)
        typ, reg = DB_MAP[self.junction]
        self.telemetry = True
        self.on_telemetry({"type": "session_start", "type_carrefour": typ, "regulation": reg, "densite": self.density})
        self.on_event("info", "Simulation démarrée")

    def stop(self):
        """Arrête la simulation."""
        if self.telemetry:
            self.on_telemetry({"type": "session_end"})
        self.running, self.telemetry = False, False

    # ---------------------------------------------------------------- urgence
    @property
    def emergency_active(self):
        """Vrai tant qu'un véhicule d'urgence bénéficie de la priorité."""
        return self.em is not None and self.granted and not self.em.crossed

    def spawn_emergency(self, label, dest):
        """Fait apparaître un véhicule d'urgence vers `dest`, depuis une branche au hasard."""
        if self.em is not None and not self.em.crossed:
            return False
        origins = [s for s in self.cfg["branches"] if s != dest]
        if dest not in self.cfg["branches"] or not origins:
            return False
        origin = random.choice(origins)
        self.em = Vehicle("emergency", origin, dest, EMERGENCY_TYPES.get(label, "#fff"), lane_off=0)
        self.em.label = label
        self.em_requested = self.granted = False
        self.vehicles.append(self.em)
        self._tele_vehicle(self.em)
        self.on_event("approach", f"Véhicule d'urgence en approche ({origin})")
        return True

    def request_priority(self):
        """Émet la demande V2I (via le callback `on_request`)."""
        if self.em is None or self.em_requested or self.em.crossed:
            return False
        self.em_requested, self.t_request = True, self.t
        self._rid += 1
        self.em.rid = self._rid
        self.on_event("priority", f"Demande de priorité émise ({self.em.label})")
        self.on_request({"type": "priority_request", "vehicle": self.em.label, "approach": self.em.origin,
                         "destination": self.em.dest, "junction": self.junction, "vid": self.em.id, "rid": self._rid})
        return True

    def grant_priority(self):
        """Applique l'ordre de priorité (reçu du serveur ou décidé localement en mode dégradé)."""
        if self.em is None or self.granted or self.em.crossed or not self.em_requested:
            return
        self.granted = True
        if self.ctrl:
            self.ctrl.request(self.em.origin)
            self.on_event("priority", f"Feu vert uniquement pour la voie {self.em.origin}")
        else:
            self.on_event("priority", "Priorité accordée : les autres branches marquent l'arrêt")
        self.on_event("v2v", f"V2V : file {self.em.origin} avertie, couloir central dégagé")

    # ---------------------------------------------------------------- accesseurs
    def light_state(self, side):
        """État du feu d'une branche (None hors régulation par feux)."""
        return self.ctrl.state(side) if self.ctrl else None

    def counts_by_origin(self):
        """Nombre de véhicules présents par branche d'entrée."""
        counts = dict.fromkeys(SIDES, 0)
        for v in self.vehicles:
            counts[v.origin] += 1
        return counts

    def avg_wait(self):
        """Temps d'attente moyen (s) des derniers véhicules sortis."""
        return sum(self.waits) / len(self.waits) if self.waits else 0.0

    # ---------------------------------------------------------------- télémétrie vers le serveur
    def _tele_vehicle(self, v):
        if self.telemetry:
            kind = v.label.lower() if v.emergency else KIND_FR[v.kind]
            self.on_telemetry({"type": "vehicle", "vid": v.id, "kind": kind, "direction": v.origin, "destination": v.dest})

    def _tele_state(self, v):
        """Signale les changements d'état d'un véhicule (arrêté, a franchi le carrefour)."""
        if v.etat == "a franchi le carrefour":
            return
        if v.s - v.length / 2 >= v.path.s_exit:
            etat = "a franchi le carrefour"
        elif v.wait > 6 and v.v < 3:
            etat = "arrêté"
        else:
            return
        if etat == v.etat:
            return
        v.etat = etat
        self.on_telemetry({"type": "vehicle_state", "vid": v.id, "etat": etat})
        if etat == "arrêté" and self.t - self.last_stop.get(v.origin, -99) > 10:
            self.last_stop[v.origin] = self.t
            name = v.label.lower() if v.emergency else KIND_FR[v.kind]
            self.on_telemetry({"type": "traffic_event", "vid": v.id, "event": "véhicule arrêté",
                               "details": f"{name} immobilisé, branche {v.origin}"})

    def _tele_lights(self):
        """Journal des feux : un message à chaque changement d'état, avec sa cause."""
        for side in self.cfg["branches"]:
            state = self.ctrl.state(side)
            if self.last_light.get(side) != state:
                self.last_light[side] = state
                cause = ("priorité urgence" if self.granted else
                         "retour au cycle normal" if self.t < self.return_until else "cycle normal")
                self.on_telemetry({"type": "light_change", "feu": side, "etat": {"green": "vert", "orange": "orange", "red": "rouge"}[state],
                                   "cause": cause})

    def _tele_jam(self):
        """Détection d'embouteillage : au moins 6 véhicules à l'arrêt sur une branche."""
        if self.t - self.last_jam < 2.0:
            return
        self.last_jam = self.t
        for side in self.cfg["branches"]:
            n = sum(1 for v in self.vehicles if v.origin == side and not v.entered and v.v < 3)
            if n >= 6 and not self.jam.get(side):
                self.jam[side] = True
                self.on_telemetry({"type": "traffic_event", "event": "embouteillage détecté",
                                   "details": f"{n} véhicules à l'arrêt, branche {side}"})
            elif n < 3:
                self.jam[side] = False

    # ---------------------------------------------------------------- boucle
    def update(self, dt, warmup=False):
        """Avance la simulation de `dt` secondes."""
        self.t += dt
        if self.ctrl:
            self.ctrl.update(dt)
        if self.telemetry:
            self._tele_lights()
            self._tele_jam()
        self._spawn(dt)
        if (self.auto_request and self.em and not self.em_requested and not warmup
                and S_STOP - (self.em.s + self.em.length / 2) < 220):
            self.request_priority()
        for v in self.vehicles:
            self._step(v, dt)
        if self.em and not self.em.crossed and self.em.s - self.em.length / 2 >= self.em.path.s_exit:
            self._emergency_crossed()
        self.vehicles = [v for v in self.vehicles if not v.done]
        if self.em and self.em.done:
            self.em = None

    def _emergency_crossed(self):
        em = self.em
        em.crossed = True
        was_granted, self.granted = self.granted, False
        self.return_until = self.t + 6.0
        if self.ctrl:
            self.ctrl.release()
        if was_granted:
            self.interventions += 1
            self.on_event("crossed", f"{em.label} a franchi le carrefour")
            self.on_event("normal", "Retour au cycle normal du carrefour")
            self.on_crossed({"type": "crossing_done", "vehicle": em.label, "approach": em.origin,
                             "destination": em.dest, "junction": self.junction,
                             "duration": round(self.t - self.t_request, 1), "vid": em.id, "rid": getattr(em, "rid", None)})
        else:
            self.on_event("crossed", f"{em.label} a franchi le carrefour sans priorité")

    def _spawn(self, dt):
        rate = 0.12 + 0.09 * self.density  # véhicules / s / branche
        for side in self.cfg["branches"]:
            if random.random() > rate * dt:
                continue
            if any(o.origin == side and not o.emergency and o.s < 85 for o in self.vehicles):
                continue
            r = random.random()
            dest = OPP[side] if r < 0.7 else (RIGHT_OF[side] if r < 0.92 else OPP[RIGHT_OF[side]])
            if dest not in self.cfg["branches"]:
                dest = OPP[side] if OPP[side] in self.cfg["branches"] else RIGHT_OF[side]
            k = random.random()
            kind, color = (("bus", "#f0b429") if k < 0.06 else ("truck", random.choice(["#e8e8e8", "#8a949e"]))
                           if k < 0.14 else ("car", random.choice(CAR_COLORS)))
            veh = Vehicle(kind, side, dest, color)
            self.vehicles.append(veh)
            self._tele_vehicle(veh)

    def _leader_gap(self, v):
        if v.blocked > 3:  # anti-interblocage
            return None
        best = None
        for o in self.vehicles:
            if o is v or o.done:
                continue
            dx, dy = o.x - v.x, o.y - v.y
            f = dx * v.hx + dy * v.hy
            if f <= 0 or f > 140 or abs(dx * v.hy - dy * v.hx) > 16:
                continue
            gap = f - (v.length + o.length) / 2
            best = gap if best is None else min(best, gap)
        return best

    @staticmethod
    def _in_box(o, margin=0):
        return abs(o.x - CX) < HALF + margin and abs(o.y - CY) < HALF + margin

    def _free_to_claim(self, v):
        """Réservation du carrefour : aucun véhicule engagé ne doit croiser mon trajet et la sortie doit être libre."""
        ex, ey, _ = v.path.pose(v.path.s_exit + v.length + 10)
        for o in self.vehicles:
            if o is v or o.done:
                continue
            if (self.emergency_active and not v.emergency and o is self.em
                    and self.em.s + self.em.length / 2 > S_STOP - 260 and paths_conflict(v, self.em)):
                return False  # le carrefour est gardé libre pour le véhicule d'urgence
            if o.claimed and paths_conflict(v, o):
                return False
            if math.hypot(o.x - ex, o.y - ey) < 28 and o.v < 8:
                return False
        return True

    def _box_clear(self, v):
        return not any(o is not v and not o.done and o.origin != v.origin and self._in_box(o, 6)
                       for o in self.vehicles)

    def _may_enter(self, v, d):
        """Décide si `v` peut franchir la ligne d'arrêt (d = distance restante)."""
        mode = self.cfg["mode"]
        if mode == "lights":
            st = self.ctrl.state(v.origin)
            return st == "green" or (st == "orange" and v.v ** 2 / (2 * DECEL) > d)
        if v.emergency and self.granted:
            return True
        if self.emergency_active and v.origin != self.em.origin:
            return False
        if mode == "stop":
            return (v.stop_timer >= 1.0 and self._box_clear(v) and
                    not any(o is not v and o.arrival is not None and not o.entered and o.arrival < v.arrival
                            for o in self.vehicles))
        right = RIGHT_OF[v.origin]  # priorité à droite
        right_busy = any(o.origin == right and not o.entered and o.s + o.length / 2 > S_STOP - 120
                         for o in self.vehicles)
        return self._box_clear(v) and (not right_busy or v.stop_timer > 3.0)

    def _step(self, v, dt):
        limit = v.vmax * self.speed
        gap = self._leader_gap(v)
        if gap is not None:
            limit = min(limit, math.sqrt(2 * DECEL * max(gap - 10, 0)))
        front = v.s + v.length / 2
        if not v.entered:
            if front > S_STOP:
                v.entered = True
                self.flow_entered[v.origin] += 1
            else:
                d = S_STOP - front
                zone = 10 if self.cfg["mode"] == "stop" else 120
                if self.cfg["mode"] != "lights" and d < zone and v.v < 3:
                    v.stop_timer += dt
                    if v.arrival is None:
                        v.arrival = self.t
                if not (v.claimed or (self._may_enter(v, d) and self._free_to_claim(v))):
                    limit = min(limit, math.sqrt(2 * DECEL * max(d - 2, 0)))
                elif d < 30 + v.v ** 2 / (2 * DECEL):
                    v.claimed = True  # engagement : le véhicule réserve le carrefour
        v.v = min(limit, v.v + ACC * dt) if limit > v.v else max(limit, v.v - DECEL * 1.6 * dt)
        if v.v < 3:
            v.wait += dt
        v.blocked = v.blocked + dt if (self._in_box(v) and v.v < 2) else 0.0
        v.s += v.v * dt
        if self.telemetry:
            self._tele_state(v)
        if v.claimed and v.s - v.length / 2 >= v.path.s_exit:
            v.claimed = False  # carrefour libéré
        if v.s >= v.path.length - 1:
            v.done = True
            if not v.emergency:
                self.waits.append(v.wait)
        else:
            v.refresh_pose()
