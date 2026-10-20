# SAÉ 3.02 – Serveur carrefour connecté (VM Linux)

**Groupe A113** – BENCHEKCHOU Nail-Omar & LEMASSON Ewan

Ce dépôt contient **uniquement** ce dont la VM Linux serveur a besoin :
écoute TCP 5050 (JSON, sockets bruts) et persistance MariaDB / SQLite.

L’IHM n’est plus une page HTML : elle tourne en **PyQt6 sur le PC client**
(hors de ce dépôt). Elle se connecte à ce serveur.

```
PC client (PyQt6)  --TCP 5050-->  VM Serveur (server.py)  --3306-->  VM Base (MariaDB)
```

## Contenu

| Chemin | Rôle |
|--------|------|
| `server/server.py` | Socket TCP + `select`, logique V2I/V2V |
| `server/database.py` | MariaDB (`DB_*`) avec repli SQLite |
| `common/protocol.py` | Trames JSON délimitées par `\n` |
| `schema.sql` | Tables `events`, `interventions`, `metrics` |

## Protocole

| Sens | Messages |
|------|----------|
| Client → Serveur | `hello`, `priority_request`, `crossing_done`, `metrics`, `history` |
| Serveur → Client | `welcome`, `priority_grant`, `v2v_alert`, `ack`, `history_result`, `error` |

## VM Base (Debian/Ubuntu, ex. `192.168.56.102`)

```bash
sudo apt update && sudo apt install -y mariadb-server
sudo sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf
sudo systemctl restart mariadb
sudo mariadb <<'SQL'
CREATE DATABASE carrefour CHARACTER SET utf8mb4;
CREATE USER 'carrefour'@'192.168.56.101' IDENTIFIED BY 'MotDePasseFort!';
GRANT ALL ON carrefour.* TO 'carrefour'@'192.168.56.101';
FLUSH PRIVILEGES;
SQL
sudo ufw allow from 192.168.56.101 to any port 3306
```

Les tables sont créées au premier lancement du serveur (ou via `schema.sql`).

## VM Serveur (ex. `192.168.56.101`)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libmariadb-dev gcc git
git clone https://github.com/Nail067/SAE3.02-R3.09.git
cd SAE3.02-R3.09
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DB_HOST=192.168.56.102
export DB_USER=carrefour
export DB_PASSWORD='MotDePasseFort!'
export DB_NAME=carrefour
python -m server.server --host 0.0.0.0 --port 5050
```

Sans `DB_HOST`, le serveur utilise SQLite (`carrefour.db`) pour un test local.

Le client PyQt6 (PC) se lance à part avec `--host 192.168.56.101 --port 5050`.
