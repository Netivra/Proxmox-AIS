# Betrieb und Wiederherstellung

Für die Installation aus der GitHub Container Registry (GHCR) siehe
[Deployment-Anleitung](deployment.md). Alle `docker compose`-Befehle unten
werden im Deployment-Verzeichnis mit `compose.yaml` und `.env` ausgeführt.
`PROVISIONER_IMAGE` verweist auf das veröffentlichte Image, vorzugsweise per Digest.
Die CLI ist im Container enthalten; auf dem Zielserver ist keine eigene
Python-Installation erforderlich.

## Konfiguration

`proxmox-ais` liest die optionale TOML-Datei aus `APP_CONFIG`, anschließend
gleichnamige großgeschriebene Umgebungsvariablen. Relative Pfade beziehen sich
auf das Arbeitsverzeichnis.

Die mitgelieferte Compose-Datei übernimmt `PROVISIONER_IMAGE`, `PUBLIC_URL`
und `MAINTENANCE` aus `.env`. Weitere Anwendungseinstellungen bei Bedarf unter
`services.provisioner.environment` in `compose.yaml` ergänzen.

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| `DATA_DIR` | `./data` | SQLite, Artefakte und Dienstsperre auf lokalem Speicher |
| `MASTER_KEY_FILE` | `./secrets/master.key` | Separat abgelegter Fernet-Schlüssel |
| `PUBLIC_URL` | `https://localhost:8080` | Extern erreichbare Basis-URL ohne Pfad |
| `SECURE_COOKIES` | `true` | Sessioncookies nur über HTTPS |
| `FOUR_EYES` | `true` | Autor darf eigene Versionen nicht veröffentlichen |
| `MAINTENANCE` | `false` | Neue Installationsfreigaben aussetzen |
| `ANSWER_WINDOW_SECONDS` | `300` | Wiederholungsfenster des Antwortabrufs |
| `ENROLLMENT_HOURS` | `4` | Maximale Registrierungsfrist |
| `LEASE_SECONDS` | `900` | Gültigkeit der Runner-Ausführungsberechtigung |
| `RUNNER_CA_FILE` | nicht gesetzt | Vertrauensanker für Runner bei interner CA |

`TESTING` und Bootstrap-Passwörter nicht im Regelbetrieb setzen. Benutzer werden
interaktiv mit `init` und danach über die Administrationsoberfläche angelegt.
Das Datenverzeichnis darf den Master-Key nicht enthalten. Unter Windows schützt
zusätzlich eine auf das Dienstkonto beschränkte NTFS-ACL das Schlüsselverzeichnis.

## TLS und Reverse-Proxy

Der Compose-Port ist nur an Loopback gebunden. Ein bestehender Proxy terminiert
TLS. Bei externem Proxy das private Container-/Verwaltungsnetz gezielt freigeben.
Der Dienst wertet Proxy-Header standardmäßig nicht aus; alle absoluten URLs
entstehen aus `PUBLIC_URL`. Bei einer nativen Installation kann der Dienst
TLS auch direkt terminieren:

```bash
proxmox-ais serve --host 0.0.0.0 --port 8080 \
  --tls-cert /etc/proxmox-ais/fullchain.pem \
  --tls-key /etc/proxmox-ais/server.key
```

Zertifikatskette und DNS müssen im Installer und auf installierten Hosts gültig
sein. Ein ISO-Fingerprint ersetzt nicht automatisch den CA-Vertrauensanker des
Runners. Bei interner CA `RUNNER_CA_FILE` konfigurieren und die gesamte Kette vom
Installer bis zum Runner prüfen. Keine TLS-Prüfung abschalten. Zertifikatsrotation
erfordert die Prüfung vorhandener Medien.

Capability-Tokens stehen zwangsläufig in Bootstrap-URLs. Der integrierte Server
schreibt daher keine Access-Logs. Im vorgeschalteten Proxy Bootstrap- und
Installer-Report-Pfade redigieren oder deren Access-Logging deaktivieren. Keine
Authorization-Header protokollieren. Skriptausgaben werden zusätzlich redigiert;
Skripte sollten Geheimnisse grundsätzlich nie ausgeben.

## Betrieb

Der Dienst verwendet genau einen Anwendungsprozess. Mehrere Uvicorn-Worker oder
gleichzeitige Dienste für dasselbe Datenverzeichnis verhindert die Dienstsperre.
SQLite benötigt lokalen Speicher mit zuverlässigen Dateisperren; kein NFS/SMB-Volume
einsetzen. Als Startwert sind 2 vCPU, 2 GB RAM und 20 GB Speicher ohne ISO-Archiv
vorgesehen; Kapazität muss unter realer Last geprüft werden.

`/health/live` prüft den Prozess, `/health/ready` Datenbank und Konfiguration.
Readiness, freien Speicher, Backupalter und ausbleibende Runner-Meldungen überwachen.
DHCP, DNS, NTP und Paketquellen sind externe Voraussetzungen. Der Server baut keine
eingehenden SSH-Verbindungen zu Hosts auf.

## Backup

Beim Registry-Deployment die Sicherung mit der aktuell eingesetzten Image-Version
erstellen, bevor `PROVISIONER_IMAGE` für ein Update geändert wird. Das
Hostverzeichnis `/srv/ais-backups` muss vorhanden und für UID/GID 10001
beschreibbar sein. Beispielsweise einmalig vom Administrator anlegen:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/ais-backups
docker compose run --rm --no-deps --volume /srv/ais-backups:/backups \
  provisioner backup /backups/2026-09-13
```

Für jede Sicherung einen neuen Zielnamen wählen. Bei einer nativen
Python-Installation lautet der entsprechende Befehl
`proxmox-ais backup /srv/ais-backups/2026-09-13`.

Das Ziel muss neu sein und außerhalb von `DATA_DIR` liegen. Die Anwendung kann
weiterlaufen: Die SQLite-Backup-API erzeugt einen konsistenten Snapshot, anschließend
werden unveränderliche Artefakte kopiert. Artefakte währenddessen nicht manuell
löschen. Die Sicherung enthält `database.sqlite3`, `artifacts/`, `settings.json`
und `manifest.json`. Der Master-Key wird ausgeschlossen; sein Fingerprint verhindert
die Wiederherstellung mit einem versehentlich falschen Schlüssel.

`settings.json` enthält die wirksamen Anwendungseinstellungen einschließlich
Standortvorgaben, Fristen und Freigaberegeln. Lokale Daten-/Schlüsselpfade und
Bootstrap-Zugangsdaten sind ausgeschlossen. Individuelle TOML-Datei,
Umgebungsvariablen, Proxykonfiguration, CA-/TLS-Dateien, ursprüngliche
ISOs und Prüfprotokolle separat sichern. Registrierte ISO-Metadaten liegen in der
Datenbank. Backup-Prüfsummen erkennen beschädigte Dateien; sie sind keine Signatur
gegen einen Angreifer mit Schreibzugriff. Backupverzeichnis deshalb schützen.

Den Master-Key aus `proxmox-ais-keys` über einen getrennten verschlüsselten
Sicherungsweg verwahren.
Eine lokale Kopie in ein bereits vorhandenes Zielverzeichnis ist möglich:

```bash
docker compose cp provisioner:/run/secrets/master.key /secure/offline/master.key
```

Die Schlüsselkopie ist genauso vertraulich wie alle gespeicherten Geheimnisse.

## Restore

1. Wartungsmodus aktivieren, neue Freigaben stoppen und Anwendungsdienst
   herunterfahren. Die Host-Runner separat berücksichtigen: ein bereits
   gestarteter Befehl kann noch laufen.
2. Bisheriges Datenverzeichnis erhalten. Ein neues leeres Datenverzeichnis
   wählen und den zum Backup gehörenden Master-Key separat bereitstellen.
3. Die gleiche Anwendungsversion wie beim Backup verwenden. Beim
   Registry-Deployment deren Digest in `.env` als `PROVISIONER_IMAGE` setzen
   und herunterladen. Das Schlüsselvolume muss bereits den passenden
   Master-Key als `master.key`, lesbar für UID/GID 10001, enthalten. In ein
   neues, leeres Datenvolume wiederherstellen:

```bash
docker compose stop provisioner
docker compose pull provisioner
docker volume create proxmox-ais-data-restored
docker compose run --rm --no-deps \
  --volume /srv/ais-backups:/backups:ro \
  --volume proxmox-ais-data-restored:/var/lib/proxmox-ais \
  provisioner restore /backups/2026-09-13
```

   `proxmox-ais-data-restored` muss neu sein; existiert es schon, einen anderen
   unbenutzten Namen wählen. Nach erfolgreichem Restore in `compose.yaml` unter
   `volumes.ais-data.name` diesen neuen Namen eintragen. Das bisherige Datenvolume
   bleibt erhalten. Bei einer nativen Python-Installation alternativ:

```bash
export DATA_DIR=/srv/proxmox-ais-restored
export MASTER_KEY_FILE=/secure/offline/master.key
proxmox-ais restore /srv/ais-backups/2026-09-13
```

4. Individuelle Konfiguration und Zertifikate wiederherstellen. Im Wartungsmodus
   starten, anmelden und Inventar sowie aktuelle Hostzustände abgleichen.
   Beim Registry-Deployment dafür `MAINTENANCE=true` in `.env` setzen und
   `docker compose up -d --no-build --wait` ausführen.
5. Aktive Läufe stehen auf `needs_review`. Alte Sessions, Gerätezugänge,
   Bootstrap-/Enrollment-/Report-Capabilities und Gruppentokens gelten nicht mehr;
   offene Installationsfreigaben sind gesperrt. Gruppentokens und davon abhängige
   Medien neu ausgeben. Zuvor ausgelieferte Installer nicht ungeprüft neu booten.
6. Installer und Runner auf jedem betroffenen Host lokal stoppen und den
   tatsächlichen Zustand prüfen. Danach den verlassenen Lauf über
   `POST /api/v1/runs/{id}/reconcile` abschließen: aktuelle `expected_version`,
   aussagekräftige `reason`, `confirmation` mit dem Host-FQDN und
   `execution_stopped: true` übermitteln. Die Aktion beendet den Lauf als
   `cancelled`, sperrt seine Berechtigungen und schreibt einen Auditeintrag.
   Erst danach ist eine neue ausdrückliche Installationsfreigabe möglich.
   Der automatische Wiederanschluss alter Geräteidentitäten nach Restore
   ist nicht Teil dieser Version.

Außerhalb eines Restore zuerst `/cancel` anfordern. Ein offline befindlicher
Runner kann seine bereits erhaltene Lease bis zum Ablauf behalten; eine
Abbruchanforderung zieht diese nicht zurück. `/reconcile` weist den Abgleich
deshalb zurück, solange eine zuvor ausgestellte Lease oder das
Antwortauslieferungsfenster noch gültig ist. Auch nach deren Ablauf ist die
ausdrückliche Bestätigung erforderlich, dass Installer und Runner lokal gestoppt
und kontrolliert wurden. Eine nicht mehr erreichbare Maschine ist kein Nachweis
dafür. Das Verfahren erteilt selbst keine Neuinstallationsfreigabe.

Restore überschreibt keine bestehende Datenbank. Die Dienstsperre verhindert
Restore während eines laufenden Dienstes im Zielverzeichnis. Ein fehlgeschlagener
Restore hinterlässt `RESTORE_FAILED`; der CLI-Start verweigert dann den Betrieb.
Nach Ursachenklärung erneut in ein neues leeres Ziel restaurieren.

Bereits heruntergeladene Antwortdateien kann der Server nicht zurückziehen.
Ebenso beendet eine Credential-Sperrung einen lokal laufenden Skriptprozess nicht
unmittelbar. Restore erfordert deshalb den physischen Zustandsabgleich der Hosts.

RPO 24 Stunden und RTO zwei Stunden sind Ziele aus dem Konzept. Erst wiederholte
Sicherung und zeitlich gemessene Wiederherstellung unter realen Bedingungen
weisen diese Ziele nach.

## Updates

Der vollständige Ablauf für Registry-Images steht unter
[Deployment aktualisieren](deployment.md#updates).
Vor dem ersten Neustart `PROVISIONER_IMAGE` auf den Digest des tatsächlich
laufenden Images festlegen, wie dort beschrieben. Anschließend
`MAINTENANCE=true` in `.env` setzen und die laufende Version mit
`docker compose up -d --no-build --wait` neu erstellen. Der Wartungsmodus
sperrt neue Freigaben; bereits gestartete Installations- und Modulabläufe werden
dadurch nicht beendet. Diese vor dem Update kontrolliert abschließen lassen.

Mit der bisherigen Version Daten und Schlüssel sichern und den alten
Image-Digest festhalten. Erst danach `PROVISIONER_IMAGE` ändern, das neue Image
mit `docker compose pull provisioner` laden, den Dienst mit
`docker compose stop provisioner` stoppen und mit
`docker compose up -d --no-build --wait` neu starten. Nach Prüfung von Anmeldung,
Inventar und Readiness `MAINTENANCE=false` setzen und erneut `up` ausführen.
Bei Updates wird `init` nicht wiederholt.

Die Datenbank
führt eine Migrationstabelle und `PRAGMA user_version`. Neuere unbekannte Schemas
werden abgewiesen. Downgrades erfolgen über die zur Sicherung passende Version
und den beschriebenen Restore; keine alte Anwendung auf eine bereits migrierte
Produktivdatenbank starten.
