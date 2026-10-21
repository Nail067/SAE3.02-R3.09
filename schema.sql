-- Usage (VM Database) :  sudo mariadb < base/schema.sql
-- =====================================================================
SET NAMES utf8mb4;   -- indispensable : les valeurs ENUM contiennent des accents
CREATE DATABASE IF NOT EXISTS carrefour CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE carrefour;

-- Une ligne par simulation lancée
CREATE TABLE IF NOT EXISTS sessions_simulation (
  id_session      INT AUTO_INCREMENT PRIMARY KEY,
  date_debut      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  date_fin        DATETIME NULL,
  type_carrefour  ENUM('4 branches','T','Y','rond-point') NOT NULL,
  regulation      ENUM('feux','panneaux','giratoire') NOT NULL DEFAULT 'feux',
  densite_trafic  TINYINT NOT NULL,                       -- de 1 à 10
  client          VARCHAR(60) NULL                        -- identifiant du poste IHM
) ENGINE=InnoDB;

-- Les véhicules d'une session (voiture, camion, ambulance…)
CREATE TABLE IF NOT EXISTS vehicules (
  id_vehicule     INT AUTO_INCREMENT PRIMARY KEY,
  id_session      INT NOT NULL,
  type_vehicule   ENUM('voiture','camion','bus','ambulance','pompiers','police') NOT NULL,
  direction       VARCHAR(20) NOT NULL,                   -- branche d'arrivée : Nord, Sud, Est, Ouest, Sud-Est…
  destination     VARCHAR(20) NULL,
  etat            ENUM('en circulation','arrêté','a franchi le carrefour') NOT NULL DEFAULT 'en circulation',
  FOREIGN KEY (id_session) REFERENCES sessions_simulation(id_session) ON DELETE CASCADE,
  INDEX idx_veh_session (id_session)
) ENGINE=InnoDB;

-- Demandes de priorité V2I / V2V des véhicules d'urgence
CREATE TABLE IF NOT EXISTS evenements_priorite (
  id_priorite     INT AUTO_INCREMENT PRIMARY KEY,
  id_session      INT NOT NULL,
  id_vehicule     INT NULL,
  heure_demande   DATETIME NOT NULL,
  direction       VARCHAR(20) NOT NULL,
  duree_s         FLOAT NULL,                             -- renseignée à la fin du passage
  resultat        ENUM('accordée','refusée','terminée','mode dégradé') NOT NULL,
  FOREIGN KEY (id_session)  REFERENCES sessions_simulation(id_session) ON DELETE CASCADE,
  FOREIGN KEY (id_vehicule) REFERENCES vehicules(id_vehicule) ON DELETE SET NULL,
  INDEX idx_prio_session (id_session)
) ENGINE=InnoDB;

-- Événements de trafic
CREATE TABLE IF NOT EXISTS evenements_trafic (
  id_evenement    INT AUTO_INCREMENT PRIMARY KEY,
  id_session      INT NOT NULL,
  id_vehicule     INT NULL,
  horodatage      DATETIME NOT NULL,
  type_evenement  ENUM('véhicule arrêté','embouteillage détecté','passage terminé') NOT NULL,
  details         VARCHAR(255) NULL,
  FOREIGN KEY (id_session)  REFERENCES sessions_simulation(id_session) ON DELETE CASCADE,
  FOREIGN KEY (id_vehicule) REFERENCES vehicules(id_vehicule) ON DELETE SET NULL,
  INDEX idx_trafic_session (id_session)
) ENGINE=InnoDB;

-- Journal des changements d'état des feux
CREATE TABLE IF NOT EXISTS journal_feux (
  id_journal      INT AUTO_INCREMENT PRIMARY KEY,
  id_session      INT NOT NULL,
  horodatage      DATETIME NOT NULL,
  feu             VARCHAR(20) NOT NULL,                   -- Nord, Sud, Est, Ouest, Sud-Est…
  etat            ENUM('vert','orange','rouge') NOT NULL,
  cause           ENUM('cycle normal','priorité urgence','retour au cycle normal') NOT NULL,
  FOREIGN KEY (id_session) REFERENCES sessions_simulation(id_session) ON DELETE CASCADE,
  INDEX idx_feux_session (id_session)
) ENGINE=InnoDB;

-- Vue : chronologie d'une intervention (demande -> feux -> passage terminé)
CREATE OR REPLACE VIEW v_chronologie AS
  SELECT id_session, heure_demande AS heure,
         CONCAT('Demande de priorité (direction ', direction, ') -> ', resultat) AS evenement
    FROM evenements_priorite
  UNION ALL
  SELECT id_session, horodatage, CONCAT('Feu ', feu, ' : ', etat, ' (', cause, ')')
    FROM journal_feux WHERE cause <> 'cycle normal'
  UNION ALL
  SELECT id_session, horodatage, CONCAT('Passage terminé - ', COALESCE(details, ''))
    FROM evenements_trafic WHERE type_evenement = 'passage terminé';
