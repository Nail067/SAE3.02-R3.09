"""Couche de persistance : MariaDB (variables d'environnement DB_*) avec repli SQLite."""
import os
import sqlite3
from datetime import datetime

TABLES = ("events", "interventions", "metrics")


class Database:
    """Stocke événements, interventions d'urgence et métriques de trafic."""

    def __init__(self):
        self.backend = "sqlite"
        self.conn = None
        if os.environ.get("DB_HOST"):
            try:
                import mariadb
                self.conn = mariadb.connect(
                    host=os.environ["DB_HOST"],
                    port=int(os.environ.get("DB_PORT", 3306)),
                    user=os.environ.get("DB_USER", "carrefour"),
                    password=os.environ.get("DB_PASSWORD", ""),
                    database=os.environ.get("DB_NAME", "carrefour"),
                )
                self.backend = "mariadb"
            except Exception as exc:  # noqa: BLE001
                print(f"[DB] MariaDB indisponible ({exc}) -> repli SQLite")
        if self.conn is None:
            self.conn = sqlite3.connect("carrefour.db", check_same_thread=False)
        self._init_schema()

    def _sql(self, sql: str) -> str:
        """MariaDB attend %s ; SQLite attend ?."""
        if self.backend == "mariadb":
            return sql.replace("?", "%s")
        return sql

    def _init_schema(self):
        """Crée les tables si elles n'existent pas."""
        pk = ("INT AUTO_INCREMENT PRIMARY KEY" if self.backend == "mariadb"
              else "INTEGER PRIMARY KEY AUTOINCREMENT")
        ddl = [
            f"CREATE TABLE IF NOT EXISTS events (id {pk}, ts VARCHAR(25), kind VARCHAR(40), message VARCHAR(255))",
            f"CREATE TABLE IF NOT EXISTS interventions (id {pk}, ts VARCHAR(25), vehicle VARCHAR(30), "
            "approach VARCHAR(10), destination VARCHAR(10), junction VARCHAR(40), duration_s FLOAT)",
            f"CREATE TABLE IF NOT EXISTS metrics (id {pk}, ts VARCHAR(25), vehicles INT, avg_wait FLOAT, interventions INT)",
        ]
        cur = self.conn.cursor()
        for stmt in ddl:
            cur.execute(stmt)
        self.conn.commit()
        cur.close()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _insert(self, sql: str, params: tuple):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        self.conn.commit()
        cur.close()

    def log_event(self, kind: str, message: str):
        """Enregistre un événement du journal."""
        self._insert("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (self._now(), kind, message))

    def log_intervention(self, vehicle, approach, destination, junction, duration):
        """Enregistre une intervention terminée."""
        self._insert(
            "INSERT INTO interventions (ts, vehicle, approach, destination, junction, duration_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self._now(), vehicle, approach, destination, junction, duration),
        )

    def log_metrics(self, vehicles, avg_wait, interventions):
        """Enregistre un instantané des métriques de trafic."""
        self._insert(
            "INSERT INTO metrics (ts, vehicles, avg_wait, interventions) VALUES (?, ?, ?, ?)",
            (self._now(), vehicles, avg_wait, interventions),
        )

    def history(self, table: str, limit: int = 100):
        """Renvoie (colonnes, lignes) d'une table (liste blanche) triées du plus récent au plus ancien."""
        if table not in TABLES:
            raise ValueError("table inconnue")
        cur = self.conn.cursor()
        cur.execute(self._sql(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?"), (int(limit),))
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        cur.close()
        return cols, rows
