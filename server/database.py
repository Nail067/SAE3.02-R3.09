"""Couche de persistance du serveur : MariaDB (production, variables DB_*) avec repli SQLite (tests locaux).

Tables : sessions_simulation, vehicules, evenements_priorite, evenements_trafic, journal_feux (voir base/schema.sql).
"""
import logging
import os
import sqlite3
from datetime import datetime

log = logging.getLogger("carrefour.db")

TABLES = ("sessions_simulation", "vehicules", "evenements_priorite", "evenements_trafic", "journal_feux")
TYPES_CARREFOUR = ("4 branches", "T", "Y", "rond-point")
REGULATIONS = ("feux", "panneaux", "giratoire")
TYPES_VEHICULE = ("voiture", "camion", "bus", "ambulance", "pompiers", "police")
ETATS_VEHICULE = ("en circulation", "arrêté", "a franchi le carrefour")
RESULTATS = ("accordée", "refusée", "terminée", "mode dégradé")
TYPES_EVENEMENT = ("véhicule arrêté", "embouteillage détecté", "passage terminé")
ETATS_FEU = ("vert", "orange", "rouge")
CAUSES_FEU = ("cycle normal", "priorité urgence", "retour au cycle normal")

SQLITE_DDL = [
    "CREATE TABLE IF NOT EXISTS sessions_simulation (id_session INTEGER PRIMARY KEY AUTOINCREMENT, date_debut TEXT NOT NULL,"
    " date_fin TEXT, type_carrefour TEXT NOT NULL, regulation TEXT NOT NULL, densite_trafic INTEGER NOT NULL, client TEXT)",
    "CREATE TABLE IF NOT EXISTS vehicules (id_vehicule INTEGER PRIMARY KEY AUTOINCREMENT, id_session INTEGER NOT NULL,"
    " type_vehicule TEXT NOT NULL, direction TEXT NOT NULL, destination TEXT, etat TEXT NOT NULL DEFAULT 'en circulation')",
    "CREATE TABLE IF NOT EXISTS evenements_priorite (id_priorite INTEGER PRIMARY KEY AUTOINCREMENT, id_session INTEGER NOT NULL,"
    " id_vehicule INTEGER, heure_demande TEXT NOT NULL, direction TEXT NOT NULL, duree_s REAL, resultat TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS evenements_trafic (id_evenement INTEGER PRIMARY KEY AUTOINCREMENT, id_session INTEGER NOT NULL,"
    " id_vehicule INTEGER, horodatage TEXT NOT NULL, type_evenement TEXT NOT NULL, details TEXT)",
    "CREATE TABLE IF NOT EXISTS journal_feux (id_journal INTEGER PRIMARY KEY AUTOINCREMENT, id_session INTEGER NOT NULL,"
    " horodatage TEXT NOT NULL, feu TEXT NOT NULL, etat TEXT NOT NULL, cause TEXT NOT NULL)",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _plain(value):
    """Convertit une valeur SQL en valeur JSON-sérialisable."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None or isinstance(value, (int, float, str)):
        return value
    return str(value)


class Database:
    """Accès aux 5 tables ; reconnexion automatique à MariaDB en cas de coupure."""

    def __init__(self):
        self.backend = "sqlite"
        self.conn = None
        self._mariadb = None
        if os.environ.get("DB_HOST"):
            try:
                import mariadb  # connecteur officiel MariaDB
                self._mariadb = mariadb
                self._connect_mariadb()
            except Exception as exc:  # noqa: BLE001
                log.error("MariaDB indisponible (%s) -> repli SQLite", exc)
                self.conn = None
        if self.conn is None:
            self.conn = sqlite3.connect(os.environ.get("SQLITE_PATH", "carrefour.db"), check_same_thread=False)
            for stmt in SQLITE_DDL:
                self.conn.execute(stmt)
            self.conn.commit()
            self.backend = "sqlite"
        log.info("Base de données : %s", self.backend)

    # ------------------------------------------------------------------ connexion
    def _connect_mariadb(self):
        self.conn = self._mariadb.connect(
            host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", 3306)),
            user=os.environ.get("DB_USER", "carrefour"), password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "carrefour"), connect_timeout=5, autocommit=True,
            init_command="SET NAMES utf8mb4")  # indispensable : valeurs ENUM accentuées (accordée, priorité urgence…)
        self.backend = "mariadb"

    def _run(self, sql, params=(), fetch=False):
        """Exécute une requête ; une reconnexion est tentée une fois si la liaison MariaDB est perdue."""
        for attempt in (1, 2):
            try:
                cur = self.conn.cursor()
                cur.execute(sql, params)
                if fetch:
                    cols = [d[0] for d in cur.description]
                    return cols, [[_plain(v) for v in row] for row in cur.fetchall()]
                if self.backend == "sqlite":
                    self.conn.commit()
                return cur.lastrowid
            except Exception as exc:  # noqa: BLE001
                if self.backend == "mariadb" and attempt == 1:
                    log.warning("Requête échouée (%s) -> reconnexion", exc)
                    try:
                        self._connect_mariadb()
                        continue
                    except Exception as exc2:  # noqa: BLE001
                        log.error("Reconnexion impossible : %s", exc2)
                raise

    # ------------------------------------------------------------------ écritures
    def start_session(self, type_carrefour, regulation, densite, client=None) -> int:
        """Ouvre une session de simulation et renvoie son identifiant."""
        return self._run("INSERT INTO sessions_simulation (date_debut, type_carrefour, regulation, densite_trafic, client)"
                         " VALUES (?, ?, ?, ?, ?)", (_now(), type_carrefour, regulation, int(densite), client))

    def end_session(self, id_session):
        """Renseigne la date de fin d'une session."""
        self._run("UPDATE sessions_simulation SET date_fin = ? WHERE id_session = ? AND date_fin IS NULL", (_now(), id_session))

    def add_vehicle(self, id_session, type_vehicule, direction, destination=None, etat="en circulation") -> int:
        """Enregistre un véhicule."""
        return self._run("INSERT INTO vehicules (id_session, type_vehicule, direction, destination, etat) VALUES (?, ?, ?, ?, ?)",
                         (id_session, type_vehicule, direction, destination, etat))

    def set_vehicle_state(self, id_vehicule, etat):
        """Met à jour l'état d'un véhicule."""
        self._run("UPDATE vehicules SET etat = ? WHERE id_vehicule = ?", (etat, id_vehicule))

    def add_priority(self, id_session, id_vehicule, direction, resultat="accordée") -> int:
        """Enregistre une demande de priorité (heure de la demande = maintenant)."""
        return self._run("INSERT INTO evenements_priorite (id_session, id_vehicule, heure_demande, direction, resultat)"
                         " VALUES (?, ?, ?, ?, ?)", (id_session, id_vehicule, _now(), direction, resultat))

    def finish_priority(self, id_priorite, duree_s, resultat="terminée"):
        """Clôture une demande de priorité (durée et résultat)."""
        self._run("UPDATE evenements_priorite SET duree_s = ?, resultat = ? WHERE id_priorite = ?", (float(duree_s), resultat, id_priorite))

    def add_traffic_event(self, id_session, id_vehicule, type_evenement, details=None):
        """Enregistre un événement de trafic."""
        self._run("INSERT INTO evenements_trafic (id_session, id_vehicule, horodatage, type_evenement, details) VALUES (?, ?, ?, ?, ?)",
                  (id_session, id_vehicule, _now(), type_evenement, (details or "")[:255]))

    def add_light_log(self, id_session, feu, etat, cause):
        """Enregistre un changement d'état de feu."""
        self._run("INSERT INTO journal_feux (id_session, horodatage, feu, etat, cause) VALUES (?, ?, ?, ?, ?)",
                  (id_session, _now(), feu, etat, cause))

    # ------------------------------------------------------------------ lectures
    def history(self, table: str, limit: int = 100):
        """Renvoie (colonnes, lignes) d'une table (liste blanche), du plus récent au plus ancien."""
        if table not in TABLES:
            raise ValueError("table inconnue")
        pk = {"sessions_simulation": "id_session", "vehicules": "id_vehicule", "evenements_priorite": "id_priorite",
              "evenements_trafic": "id_evenement", "journal_feux": "id_journal"}[table]
        return self._run(f"SELECT * FROM {table} ORDER BY {pk} DESC LIMIT ?", (max(1, min(int(limit), 500)),), fetch=True)

    def chronology(self, id_session=None):
        """Chronologie de la dernière intervention : demande -> modification des feux -> passage terminé."""
        _, last = self._run("SELECT id_priorite, id_session, heure_demande, direction, resultat, duree_s FROM evenements_priorite"
                            + (" WHERE id_session = ?" if id_session else "") + " ORDER BY id_priorite DESC LIMIT 1",
                            (id_session,) if id_session else (), fetch=True)
        cols = ["heure", "evenement"]
        if not last:
            return cols, []
        sess, h0, direction, resultat, duree = last[0][1:6]
        rows = [[h0, f"Demande de priorité (direction {direction}) -> {resultat}"]]
        t0 = datetime.strptime(h0, "%Y-%m-%d %H:%M:%S")
        _, feux = self._run("SELECT horodatage, feu, etat, cause FROM journal_feux WHERE id_session = ? AND cause <> 'cycle normal'"
                            " ORDER BY id_journal", (sess,), fetch=True)
        _, trafic = self._run("SELECT horodatage, details FROM evenements_trafic WHERE id_session = ? AND type_evenement = 'passage terminé'"
                              " ORDER BY id_evenement", (sess,), fetch=True)
        fin = t0.timestamp() + (duree if duree is not None else 3600) + 10
        for h, feu, etat, cause in feux:
            if t0.timestamp() - 2 <= datetime.strptime(h, "%Y-%m-%d %H:%M:%S").timestamp() <= fin:
                rows.append([h, f"Feu {feu} : {etat} ({cause})"])
        for h, det in trafic:
            if t0.timestamp() <= datetime.strptime(h, "%Y-%m-%d %H:%M:%S").timestamp() <= fin:
                rows.append([h, f"Passage terminé - {det}"])
        rows.sort(key=lambda r: r[0])
        return cols, rows
