-- Schéma MariaDB du carrefour connecté (créé aussi automatiquement par server/database.py).
CREATE DATABASE IF NOT EXISTS carrefour CHARACTER SET utf8mb4;
USE carrefour;

CREATE TABLE IF NOT EXISTS events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts VARCHAR(25),
    kind VARCHAR(40),
    message VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS interventions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts VARCHAR(25),
    vehicle VARCHAR(30),
    approach VARCHAR(10),
    destination VARCHAR(10),
    junction VARCHAR(40),
    duration_s FLOAT
);

CREATE TABLE IF NOT EXISTS metrics (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts VARCHAR(25),
    vehicles INT,
    avg_wait FLOAT,
    interventions INT
);
