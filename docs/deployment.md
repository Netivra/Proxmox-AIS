# Deployment mit einem Image aus der Container Registry

Proxmox AIS wird auf dem Zielserver aus einem bereits veröffentlichten
Containerimage gestartet. Der [GitHub-Actions-Workflow](github-actions.md) erstellt dieses Image; auf dem
Zielserver werden nur Docker Compose und die Betriebskonfiguration benötigt.
Diese Anleitung setzt einen erfolgreichen `container-publish`-Job und ein
weiterhin abrufbares Image voraus.

## Voraussetzungen und Dateien

Der Zielserver benötigt Linux auf amd64, Docker Engine, das Docker-Compose-Plugin
und einen HTTPS-Reverse-Proxy. Die Docker-Befehle unter einem Deploymentkonto
mit Docker-Zugriff ausführen. Dasselbe Konto für `docker login` und
`docker compose` verwenden, damit die Registry-Anmeldung verfügbar ist.
Die [Docker-Installationsanleitung](https://docs.docker.com/engine/install/debian/)
beschreibt beispielsweise die Installation auf Debian.

Python, Node.js, ein CI-Runner und Build-Werkzeuge gehören zur Build-Umgebung;
die Anwendung samt Laufzeit ist im veröffentlichten Image enthalten. Ein
Checkout des Anwendungsquellcodes ist auf dem Zielserver nicht erforderlich.

Ein beschreibbares Deploymentverzeichnis bereitstellen, beispielsweise
`/opt/proxmox-ais`, und diese beiden Dateien aus dem gewünschten Projektstand
herunterladen beziehungsweise dorthin kopieren:

| Datei aus dem Repository | Dateiname auf dem Zielserver |
| --- | --- |
| [compose.yaml](../compose.yaml) | `/opt/proxmox-ais/compose.yaml` |
| [config/deployment.env.example](../config/deployment.env.example) | `/opt/proxmox-ais/.env` |

Im weiteren Verlauf alle Compose-Befehle in diesem Verzeichnis ausführen:

```bash
cd /opt/proxmox-ais
chmod 600 .env
docker version
docker compose version
```

Die Compose-Datei verwendet das Image aus `PROVISIONER_IMAGE`. Ein fehlender
Wert führt bereits bei der Konfigurationsprüfung zu einem Fehler.

## Image und öffentliche Adresse festlegen

In GitHub unter **Actions** den erfolgreichen Workflow-Lauf mit
`container-publish` öffnen, unter **Artifacts** das Archiv `container-deploy`
herunterladen und entpacken. Es enthält `build.env` und `deploy.env`.
Die vollständige Zeile `PROVISIONER_IMAGE=…@sha256:…` aus `deploy.env` in die
`.env` auf dem Zielserver übernehmen. Der Digest legt genau das veröffentlichte
Image fest. Compose liest `deploy.env` nicht automatisch ein.
[GitHub-Artefakte herunterladen](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

Alternativ auf GitHub das zugehörige **Packages**-Paket öffnen und den vollständigen
Imagepfad mit einem tatsächlich vorhandenen Versions-Tag kopieren. Der aktuelle
Imagepfad der Beispieldatei sieht so aus:

```dotenv
PROVISIONER_IMAGE=ghcr.io/netivra/proxmox-ais:0.1.0
PUBLIC_URL=https://provision.example.net
MAINTENANCE=false
```

`PROVISIONER_IMAGE` und `PUBLIC_URL` für die Installation festlegen. Der
Imagepfad entspricht dem aktuellen Wert der Vorlage; die Verfügbarkeit des
Tags `0.1.0` wird damit nicht vorausgesetzt. Maßgeblich sind das
Pipeline-Artefakt oder die Registry-Anzeige deines Projekts.
Der Registry-Endpunkt ist `ghcr.io`. Für reproduzierbare Deployments den Digest
bevorzugen und die bisher verwendeten Digests für spätere Updates aufbewahren.

`PUBLIC_URL` ist die vollständige HTTPS-Basisadresse ohne zusätzlichen Pfad,
unter der Browser, Installer und installierte Hosts den Dienst erreichen.
DNS und Zertifikatskette müssen für diese Systeme gültig sein. Der
Reverse-Proxy auf dem Zielserver leitet diese Adresse an
`http://127.0.0.1:8080` weiter. Der Compose-Port ist nur auf Loopback gebunden.
Bei einem Proxy auf einem anderen Host oder in einem anderen Container dessen
Verbindung gezielt nach der [Betriebsanleitung](operations.md#tls-und-reverse-proxy)
einrichten; `127.0.0.1` bezeichnet dort jeweils das eigene System.

## An der Registry anmelden und Image laden

Öffentliche GHCR-Pakete lassen sich ohne Anmeldung herunterladen. Für ein
privates Paket einen GitHub Personal Access Token **(classic)** mit dem Scope
`read:packages` verwenden. Das zugehörige GitHub-Konto benötigt Lesezugriff auf
das Paket; bei Organisations-SSO den Token dafür autorisieren. Der Token wird
bei der Anmeldung als Passwort verdeckt eingegeben und gehört nicht in `.env`.
Das kurzlebige `GITHUB_TOKEN` des CI-Jobs ist kein Deployment-Zugang.
[GHCR-Authentifizierung](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic).

Nur für private Pakete anmelden und `GITHUB_USERNAME` durch den GitHub-Namen
des Token-Inhabers ersetzen:

```bash
docker login ghcr.io --username GITHUB_USERNAME
```

Konfiguration prüfen und das gewählte Image herunterladen:

```bash
docker compose config --quiet
docker compose pull provisioner
```

Erst nach erfolgreichem Pull fortfahren. `unauthorized` oder `denied` weist
auf Anmeldung beziehungsweise Zugriffsrechte hin; bei `manifest unknown` den
Imagepfad und den verfügbaren Tag/Digest prüfen. Bei internen Zertifikaten
den CA-Vertrauensanker im Docker-Dienst des Zielservers einrichten.

## Einmalige Initialisierung und Start

Nur bei der ersten Inbetriebnahme mit neuen Daten- und Schlüsselvolumes den
Administrator und den Master-Key anlegen:

```bash
docker compose run --rm --no-deps \
  --volume proxmox-ais-keys:/run/secrets:rw provisioner init --username admin
```

Das Passwort wird zweimal interaktiv abgefragt. Es gibt kein Standardpasswort.
Das Schlüsselvolume ist für diesen einen Aufruf beschreibbar. Im Regelbetrieb
wird es schreibgeschützt eingebunden. Bei einer bestehenden Installation oder
einem Update diesen Initialisierungsbefehl nicht erneut ausführen.

Anschließend den Dienst starten:

```bash
docker compose up -d --no-build --wait
docker compose ps
docker compose logs --tail=100 provisioner
```

`--wait` wartet auf einen laufenden, gesunden Dienst gemäß dem Healthcheck im
Image. Der Proxyzugriff wird separat geprüft.
[Compose-Startoptionen](https://docs.docker.com/reference/cli/docker/compose/up/).
Mit `curl`, sofern auf dem Zielserver verfügbar, den lokalen Dienst und die
öffentliche HTTPS-Adresse prüfen; die Beispieladresse ersetzen:

```bash
curl --fail http://127.0.0.1:8080/health/ready
curl --fail https://provision.example.net/health/ready
```

Beide Aufrufe sollen `"status":"ready"` liefern. Danach die öffentliche
Adresse im Browser öffnen und mit dem angelegten Administrator anmelden.

## Daten und Schlüssel

Die Anwendung läuft im Container als UID/GID `10001:10001`. Die Compose-Datei
verwendet diese dauerhaften, von Docker verwalteten Volumes:

| Volume | Inhalt | Containerpfad |
| --- | --- | --- |
| `proxmox-ais-data` | SQLite-Datenbank und Artefakte | `/var/lib/proxmox-ais` |
| `proxmox-ais-keys` | Master-Key für verschlüsselte Geheimnisse | `/run/secrets` |

Für neue Volumes übernimmt Docker die vorbereiteten Verzeichnisse aus dem
Image; sie sind für den Dienstbenutzer angelegt. Beim Wiederverwenden oder
Wiederherstellen bestehender Volumes müssen Eigentümer und Rechte weiterhin
zu UID/GID 10001 passen. Lokale Bind-Mount-Verzeichnisse benötigen dieselben
passenden Rechte, falls die Compose-Datei später entsprechend angepasst wird.
[Docker-Volumes](https://docs.docker.com/engine/storage/volumes/).

Containerneustarts und Updates erhalten diese Volumes.
`docker compose down --volumes` würde sie löschen. Für mehrere getrennte Instanzen auf demselben
Host die Volume-Namen in Compose und beim Initialisierungsaufruf je Instanz
anpassen. Daten und Master-Key nach der
[Backup-Anleitung](operations.md#backup) getrennt sichern.

## Updates

Ein Wartungsfenster vorsehen. Vor dem ersten Neustart den Digest des
tatsächlich laufenden Images ermitteln, insbesondere bei Verwendung eines
beweglichen Tags:

```bash
AIS_RUNNING_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$(docker compose ps -q provisioner)")
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$AIS_RUNNING_IMAGE_ID"
```

Den zum Projekt passenden Registry-Digest zusammen mit dem Backup aufbewahren
und in `.env` als `PROVISIONER_IMAGE` eintragen. Damit verwendet auch der
Wartungsneustart genau die bisherige Version. Falls kein Registry-Digest
angezeigt wird, die bisherige Version vor dem Update eindeutig zuordnen.

Jetzt in `.env` `MAINTENANCE=true` setzen und die Einstellung mit der
bisherigen Version übernehmen:

```bash
docker compose up -d --no-build --wait
```

Der Wartungsmodus sperrt neue Installationsfreigaben. Bereits gestartete
Installer und Host-Runner werden dadurch nicht beendet. Ihre laufenden
Phasen vor dem eigentlichen Versionswechsel prüfen und einen geeigneten
Zeitpunkt für den Dienststopp abwarten.

Jetzt die Daten mit der bisher eingesetzten Anwendungsversion sichern und
den Master-Key separat verwahren. Die [Backup-Anleitung](operations.md#backup)
enthält dafür die Containerbefehle. Zusätzlich die aktuelle `.env`, den
bisherigen Image-Digest und die Proxykonfiguration sichern.

Danach die neue `PROVISIONER_IMAGE`-Zeile aus dem erfolgreichen
`container-publish`-Job in `.env` übernehmen. `PUBLIC_URL` und die
Volume-Namen beibehalten, `MAINTENANCE=true` gesetzt lassen. Folgender Ablauf
prüft und lädt das neue Image, bevor er den bisherigen Dienst stoppt:

```bash
(
set -eu
docker compose config --quiet
docker compose pull provisioner
docker compose stop provisioner
docker compose up -d --no-build --wait
docker compose ps
)
```

Bei einem fehlgeschlagenen Pull läuft der bisherige Dienst weiter. Nach dem
Update Readiness über HTTPS, Anmeldung und Inventar prüfen. Erst nach
erfolgreicher Prüfung in `.env` `MAINTENANCE=false` setzen und übernehmen:

```bash
docker compose up -d --no-build --wait
```

Der Initialisierungsbefehl gehört ausschließlich zur ersten Inbetriebnahme.

## Rückkehr zum vorherigen Stand

Falls der neue Dienst nicht gesund startet, zunächst
`docker compose logs --tail=100 provisioner` prüfen. Das vorherige Image darf
nur auf eine damit kompatible Datenbank zugreifen. Bei bestätigter
Kompatibilität die gesicherte `.env` beziehungsweise den bisherigen exakten
Image-Digest wiederherstellen und den Updateablauf erneut ausführen; den
Wartungsmodus erst nach erfolgreicher Prüfung beenden.

Hat die neue Version das Schema bereits verändert, den Dienst stoppen und
mit der zum Backup passenden Anwendungsversion nach der
[Restore-Anleitung](operations.md#restore) in ein neues leeres Datenvolume
wiederherstellen. Die vorhandenen Daten erhalten, bis die Wiederherstellung
geprüft ist. Der Restore setzt Maschinenberechtigungen zurück und erfordert
den dort beschriebenen Abgleich laufender Installationen.
