"""Point d'entrée de l'IHM :  python -m client.main [--host 192.168.56.101] [--port 5050]"""
import argparse
import os
import sys

from PyQt6.QtWidgets import QApplication

from client.main_window import MainWindow
from common.protocol import DEFAULT_PORT


def main():
    """Analyse les arguments puis lance l'application Qt."""
    ap = argparse.ArgumentParser(description="SAÉ 3.02 – IHM du carrefour connecté")
    ap.add_argument("--host", default="127.0.0.1", help="adresse IP du serveur (VM Serveur)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=os.environ.get("CARREFOUR_TOKEN", ""), help="jeton partagé avec le serveur (si activé)")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    win = MainWindow(args.host, args.port, args.token)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
