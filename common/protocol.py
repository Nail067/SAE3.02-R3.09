"""Protocole applicatif V2I/V2V : messages JSON délimités par un saut de ligne (TCP brut)."""
import json

DEFAULT_PORT = 5050


def encode(msg: dict) -> bytes:
    """Sérialise un message en une trame JSON terminée par '\\n'."""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


class FrameBuffer:
    """Reconstitue les trames complètes à partir d'un flux TCP (fragmentation possible)."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes) -> list:
        """Ajoute des octets reçus et renvoie la liste des messages complets décodés."""
        self._buf += data
        messages = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                try:
                    messages.append(json.loads(line.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    pass
        return messages
