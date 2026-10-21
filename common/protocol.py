"""Protocole applicatif V2I/V2V """
import json

DEFAULT_PORT = 5050
MAX_FRAME_SIZE = 256 * 1024


def encode(msg: dict) -> bytes:
    """Sérialise un message en une trame JSON terminée par '\n'."""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


class FrameBuffer:
    """Reconstitue les trames TCP et borne la taille d'une trame pour éviter une croissance infinie."""

    def __init__(self, max_size: int = MAX_FRAME_SIZE):
        if max_size < 1:
            raise ValueError("max_size doit être positif")
        self._buf = b""
        self.max_size = max_size

    def feed(self, data: bytes) -> list:
        """Ajoute des octets reçus et renvoie les messages complets décodés."""
        self._buf += data
        messages = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if len(line) > self.max_size:
                raise ValueError("Trame TCP trop volumineuse")
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("Trame JSON invalide") from exc
            messages.append(message)
        if len(self._buf) > self.max_size:
            raise ValueError("Trame TCP incomplète / trop volumineuse")
        return messages
