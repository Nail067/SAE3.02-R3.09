"""Serveur de carrefour connecté : sockets TCP bruts + select, persistance en base.

Lancement :  python -m server.server [--host 0.0.0.0] [--port 5050]
"""
import argparse
import select
import socket

from common.protocol import DEFAULT_PORT, FrameBuffer, encode
from server.database import Database

AXIS = {"Nord": "NS", "Sud": "NS", "Est": "EW", "Ouest": "EW"}


class IntersectionServer:
    """Reçoit les demandes V2I des véhicules, les journalise et répond par un ordre de priorité."""

    def __init__(self, host: str, port: int):
        self.db = Database()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(8)
        self.buffers = {}  # socket -> FrameBuffer
        print(f"[SERVEUR] écoute sur {host}:{port} (base : {self.db.backend})")

    def run(self):
        """Boucle d'E/S multiplexée (select)."""
        try:
            while True:
                readable, _, _ = select.select([self.srv, *self.buffers], [], [], 1.0)
                for sock in readable:
                    if sock is self.srv:
                        client, addr = self.srv.accept()
                        self.buffers[client] = FrameBuffer()
                        print(f"[SERVEUR] connexion de {addr}")
                    else:
                        self._read(sock)
        except KeyboardInterrupt:
            print("\n[SERVEUR] arrêt")

    def _read(self, sock):
        try:
            data = sock.recv(4096)
        except OSError:
            data = b""
        if not data:
            self.buffers.pop(sock, None)
            sock.close()
            return
        for msg in self.buffers[sock].feed(data):
            self.handle(msg, sock)

    @staticmethod
    def _send(sock, msg: dict):
        try:
            sock.sendall(encode(msg))
        except OSError:
            pass

    def handle(self, msg: dict, sock):
        """Traite un message client et répond."""
        kind = msg.get("type")
        if kind == "hello":
            self.db.log_event("connexion", f"Client {msg.get('client', '?')} connecté")
            self._send(sock, {"type": "welcome", "db": self.db.backend})
        elif kind == "priority_request":
            veh, app = msg.get("vehicle", "?"), msg.get("approach", "?")
            self.db.log_event("priorité", f"Demande de priorité {veh} depuis {app}")
            self._send(sock, {"type": "priority_grant", "approach": app, "axis": AXIS.get(app, "?")})
            for other in self.buffers:
                if other is not sock:
                    self._send(other, {"type": "v2v_alert", "vehicle": veh, "approach": app})
        elif kind == "crossing_done":
            self.db.log_intervention(
                msg.get("vehicle"),
                msg.get("approach"),
                msg.get("destination"),
                msg.get("junction"),
                float(msg.get("duration", 0)),
            )
            self.db.log_event("passage", f"{msg.get('vehicle')} a franchi le carrefour")
            self._send(sock, {"type": "ack", "ref": "crossing_done"})
        elif kind == "metrics":
            self.db.log_metrics(
                int(msg.get("vehicles", 0)),
                float(msg.get("avg_wait", 0)),
                int(msg.get("interventions", 0)),
            )
        elif kind == "history":
            try:
                cols, rows = self.db.history(msg.get("table", "events"), msg.get("limit", 100))
                self._send(sock, {"type": "history_result", "table": msg.get("table"), "columns": cols, "rows": rows})
            except ValueError:
                self._send(sock, {"type": "error", "message": "table inconnue"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Serveur du carrefour connecté (SAÉ 3.02)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    IntersectionServer(args.host, args.port).run()
