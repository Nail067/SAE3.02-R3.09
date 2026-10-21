"""Serveur du carrefour connecté (VM Serveur) : sockets TCP bruts + select, journalisation en base.

Lancement :  python -m server.server [--host 0.0.0.0] [--port 5050]
Variables d'environnement : DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME (MariaDB), CARREFOUR_TOKEN (jeton optionnel).

Protocole (JSON, une trame par ligne) :
  client -> serveur : hello, session_start, session_end, vehicle, vehicle_state, priority_request, crossing_done,
                      traffic_event, light_change, batch, history, chronology, ping
  serveur -> client : welcome, session_started, priority_grant, v2v_alert, ack, history_result, pong, error
"""
import argparse
import logging
import os
import select
import signal
import socket

from common.protocol import DEFAULT_PORT, FrameBuffer, encode
from server import database as dbm
from server.database import Database

log = logging.getLogger("carrefour.server")
MAX_BUFFER = 256 * 1024          # octets non décodés tolérés par client
AXIS = {"Nord": "NS", "Sud": "NS", "Est": "EW", "Ouest": "EW"}


class ClientContext:
    """État d'un poste IHM connecté : tampon de lecture, session ouverte, correspondances d'identifiants."""

    def __init__(self, sock, addr):
        self.sock, self.addr, self.buf = sock, addr, FrameBuffer()
        self.name, self.authenticated = "?", False
        self.id_session = None
        self.vehicles = {}      # identifiant local du client -> id_vehicule en base
        self.priorities = {}    # identifiant local de la demande -> id_priorite en base
        self.bytes_pending = 0


class IntersectionServer:
    """Reçoit les messages V2I/V2V, répond aux demandes de priorité et historise tout en base."""

    def __init__(self, host: str, port: int):
        self.db = Database()
        self.token = os.environ.get("CARREFOUR_TOKEN", "")
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(16)
        self.clients = {}       # socket -> ClientContext
        self.running = True
        log.info("Écoute sur %s:%d (base : %s, jeton : %s)", host, port, self.db.backend, "oui" if self.token else "non")

    # ------------------------------------------------------------------ boucle principale
    def run(self):
        """Boucle d'E/S multiplexée (select)."""
        while self.running:
            readable, _, _ = select.select([self.srv, *self.clients], [], [], 1.0)
            for sock in readable:
                if sock is self.srv:
                    client, addr = self.srv.accept()
                    self.clients[client] = ClientContext(client, addr)
                    log.info("Connexion de %s:%d", *addr)
                else:
                    self._read(sock)
        for sock in list(self.clients):
            self._drop(sock)
        self.srv.close()

    def stop(self, *_):
        """Demande l'arrêt propre (SIGTERM / Ctrl+C)."""
        self.running = False

    def _read(self, sock):
        ctx = self.clients[sock]
        try:
            data = sock.recv(4096)
        except OSError:
            data = b""
        if not data:
            self._drop(sock)
            return
        try:
            messages = ctx.buf.feed(data)
        except ValueError as exc:
            log.warning("Trame invalide depuis %s : %s", ctx.addr[0], exc)
            self._drop(sock)
            return
        for msg in messages:
            try:
                self.dispatch(ctx, msg)
            except Exception:  # noqa: BLE001 — une erreur de base ne doit jamais arrêter le serveur
                log.exception("Erreur de traitement (%s)", msg.get("type") if isinstance(msg, dict) else msg)
                # Ne pas exposer au client les détails internes (SQL, chemins, configuration).
                self._send(ctx, {"type": "error", "message": "traitement impossible"})
            if sock not in self.clients:
                return

    def _drop(self, sock):
        ctx = self.clients.pop(sock, None)
        if ctx is None:
            return
        if ctx.id_session:
            try:
                self.db.end_session(ctx.id_session)
            except Exception:  # noqa: BLE001
                log.warning("Fermeture de session %s impossible", ctx.id_session)
        log.info("Déconnexion de %s (%s)", ctx.addr[0], ctx.name)
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def _send(ctx, msg: dict):
        try:
            ctx.sock.sendall(encode(msg))
        except OSError:
            pass

    # ------------------------------------------------------------------ traitement des messages
    def dispatch(self, ctx, msg):
        """Aiguille un message vers son traitement (batch = liste de messages)."""
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "hello":
            return self._hello(ctx, msg)
        if not ctx.authenticated:
            self._send(ctx, {"type": "error", "message": "authentification requise"})
            return self._drop(ctx.sock)
        if kind == "batch":
            for sub in msg.get("messages", [])[:200]:
                self.dispatch(ctx, sub)
            return
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            return self._send(ctx, {"type": "error", "message": f"type de message inconnu : {kind}"})
        handler(ctx, msg)

    def _hello(self, ctx, msg):
        if self.token and msg.get("token") != self.token:
            log.warning("Jeton invalide depuis %s", ctx.addr[0])
            self._send(ctx, {"type": "error", "message": "jeton invalide"})
            return self._drop(ctx.sock)
        ctx.name, ctx.authenticated = str(msg.get("client", "?"))[:60], True
        self._send(ctx, {"type": "welcome", "db": self.db.backend, "tables": list(dbm.TABLES)})

    def _on_ping(self, ctx, msg):
        self._send(ctx, {"type": "pong"})

    def _on_session_start(self, ctx, msg):
        if ctx.id_session:
            self.db.end_session(ctx.id_session)
        typ, reg = msg.get("type_carrefour"), msg.get("regulation")
        if typ not in dbm.TYPES_CARREFOUR or reg not in dbm.REGULATIONS:
            return self._send(ctx, {"type": "error", "message": "type_carrefour ou regulation invalide"})
        try:
            densite = int(msg.get("densite", 5))
        except (TypeError, ValueError):
            return self._send(ctx, {"type": "error", "message": "densite doit être un entier entre 1 et 10"})
        if isinstance(msg.get("densite", 5), bool) or not 1 <= densite <= 10:
            return self._send(ctx, {"type": "error", "message": "densite doit être un entier entre 1 et 10"})
        ctx.vehicles.clear()
        ctx.priorities.clear()
        ctx.id_session = self.db.start_session(typ, reg, densite, ctx.name)
        self._send(ctx, {"type": "session_started", "id_session": ctx.id_session})

    def _on_session_end(self, ctx, msg):
        if ctx.id_session:
            self.db.end_session(ctx.id_session)
            ctx.id_session = None

    def _on_vehicle(self, ctx, msg):
        if not ctx.id_session or msg.get("kind") not in dbm.TYPES_VEHICULE:
            return
        ctx.vehicles[msg.get("vid")] = self.db.add_vehicle(ctx.id_session, msg["kind"], str(msg.get("direction", "?"))[:20],
                                                           str(msg.get("destination", ""))[:20] or None)

    def _on_vehicle_state(self, ctx, msg):
        id_v = ctx.vehicles.get(msg.get("vid"))
        if id_v and msg.get("etat") in dbm.ETATS_VEHICULE:
            self.db.set_vehicle_state(id_v, msg["etat"])

    def _on_priority_request(self, ctx, msg):
        approach, vehicle = str(msg.get("approach", "?"))[:20], str(msg.get("vehicle", "?"))
        # La décision de priorité ne dépend PAS de la base : on répond d'abord, on journalise ensuite.
        grant = {"type": "priority_grant", "approach": approach, "axis": AXIS.get(approach, "?"), "rid": msg.get("rid")}
        logged = False
        try:
            if ctx.id_session:
                id_p = self.db.add_priority(ctx.id_session, ctx.vehicles.get(msg.get("vid")), approach, "accordée")
                ctx.priorities[msg.get("rid")] = id_p
                logged = True
        except Exception:  # noqa: BLE001
            log.exception("Journalisation de la demande de priorité impossible")
        grant["logged"] = logged
        self._send(ctx, grant)
        log.info("Priorité accordée à %s depuis %s", vehicle, approach)
        for other in self.clients.values():  # diffusion V2V aux autres postes
            if other is not ctx and other.authenticated:
                self._send(other, {"type": "v2v_alert", "vehicle": vehicle, "approach": approach})

    def _on_crossing_done(self, ctx, msg):
        id_p = ctx.priorities.pop(msg.get("rid"), None)
        if id_p:
            self.db.finish_priority(id_p, float(msg.get("duration", 0)), "terminée")
        if ctx.id_session:
            self.db.add_traffic_event(ctx.id_session, ctx.vehicles.get(msg.get("vid")), "passage terminé",
                                      f"{msg.get('vehicle', 'Véhicule')} a franchi le carrefour")
        self._send(ctx, {"type": "ack", "ref": "crossing_done"})

    def _on_traffic_event(self, ctx, msg):
        if ctx.id_session and msg.get("event") in dbm.TYPES_EVENEMENT:
            self.db.add_traffic_event(ctx.id_session, ctx.vehicles.get(msg.get("vid")), msg["event"], msg.get("details"))

    def _on_light_change(self, ctx, msg):
        if ctx.id_session and msg.get("etat") in dbm.ETATS_FEU and msg.get("cause") in dbm.CAUSES_FEU:
            self.db.add_light_log(ctx.id_session, str(msg.get("feu", "?"))[:20], msg["etat"], msg["cause"])

    def _on_history(self, ctx, msg):
        try:
            cols, rows = self.db.history(msg.get("table", ""), msg.get("limit", 100))
        except ValueError:
            return self._send(ctx, {"type": "error", "message": "table inconnue"})
        self._send(ctx, {"type": "history_result", "table": msg.get("table"), "columns": cols, "rows": rows})

    def _on_chronology(self, ctx, msg):
        cols, rows = self.db.chronology(ctx.id_session)
        self._send(ctx, {"type": "history_result", "table": "chronologie", "columns": cols, "rows": rows})


def main():
    """Point d'entrée en ligne de commande."""
    ap = argparse.ArgumentParser(description="Serveur du carrefour connecté (SAÉ 3.02)")
    ap.add_argument("--host", default=os.environ.get("SERVER_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SERVER_PORT", DEFAULT_PORT)))
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    server = IntersectionServer(args.host, args.port)
    signal.signal(signal.SIGTERM, server.stop)
    signal.signal(signal.SIGINT, server.stop)
    server.run()


if __name__ == "__main__":
    main()
