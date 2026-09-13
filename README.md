# Proxmox AIS

Webtool für kontrollierte Proxmox-Neuinstallationen und wiederaufnehmbare
Postinstallation auf Basis des [Feinkonzepts](Feinkonzept_Proxmox_Provisionierung.md).
Ein FastAPI-Dienst bündelt Inventar, versionierte Profile und Bash-Module,
Installationsfreigaben, ISO-Registrierung, Laufprotokolle und Auditdaten.
SQLite und unveränderliche Artefakte liegen in einem persistenten Datenverzeichnis;
der Schlüssel für verschlüsselte Geheimnisse liegt separat.

**Unabhängigkeitshinweis:** Proxmox AIS ist ein unabhängiges Projekt und steht in keiner Verbindung zur Proxmox Server Solutions GmbH oder den Entwicklern von Proxmox Virtual Environment.

**Status: erste Implementierung für die Laborabnahme.**
Es ist noch kein echter Proxmox-ISO-Build auf Hardware oder in einer VM abgenommen.
Die [Kompatibilitätsmatrix](docs/compatibility.md) trennt Softwaretests von noch
offenen Installationstests. Freigaben können Datenträger überschreiben lassen;
für die erste Abnahme ausschließlich dedizierte Testsysteme verwenden.

## Lokal starten

Python 3.12 oder neuer wird benötigt. Die Anwendung selbst läuft unter Windows
und Linux; der Host-Runner benötigt Proxmox/Linux mit systemd, Python und Bash.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:PUBLIC_URL = "http://127.0.0.1:8080"
$env:SECURE_COOKIES = "false"
.\.venv\Scripts\proxmox-ais.exe init --username admin
.\.venv\Scripts\proxmox-ais.exe serve
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
export PUBLIC_URL=http://127.0.0.1:8080
export SECURE_COOKIES=false
.venv/bin/proxmox-ais init --username admin
.venv/bin/proxmox-ais serve
```

`init` fragt das Passwort zweimal verdeckt ab, speichert einen scrypt-Hash und
legt `secrets/master.key` mit restriktiven Rechten an. Anschließend
<http://127.0.0.1:8080> öffnen. Es gibt keine mitgelieferten Zugangsdaten.
Die HTTP-Einstellungen sind ausschließlich für die lokale Entwicklung gedacht.

Optional `config/app.example.toml` kopieren und dessen Pfad über `APP_CONFIG`
setzen. Umgebungsvariablen überschreiben TOML-Werte. Weitere Einstellungen
und TLS-Konfiguration stehen in der [Betriebsanleitung](docs/operations.md).

## Registry-Container deployen

Auf dem Zielserver werden Docker Engine, das Compose-Plugin und ein
HTTPS-Reverse-Proxy benötigt. Die Anwendung wird als fertiges Image aus der
GitHub Container Registry (GHCR) heruntergeladen. Quellcode und Build-Werkzeuge werden
für das Deployment nicht benötigt.

[compose.yaml](compose.yaml) und die [Umgebungsvorlage](config/deployment.env.example)
in dasselbe Verzeichnis auf dem Zielserver kopieren; die Vorlage dort als `.env`
speichern. `PUBLIC_URL` auf die erreichbare HTTPS-URL der Anwendung setzen.
Die vollständige `PROVISIONER_IMAGE=…@sha256:…`-Zeile aus `deploy.env` im Artefakt
`container-deploy` eines erfolgreichen `container-publish`-Jobs in `.env` übernehmen. Alternativ
einen bereits veröffentlichten Versionstag aus der Projektregistry verwenden.
Compose benötigt ausdrücklich `PROVISIONER_IMAGE`; ein lokaler Build ist nicht
Teil dieser Deployment-Konfiguration.

Im Deployment-Verzeichnis ausführen. Für ein privates GHCR-Paket beim Login den
eigenen GitHub-Benutzernamen und einen Personal Access Token (classic) mit
`read:packages` als Passwort verwenden. Bei einem öffentlichen Paket entfällt
der Login. Details stehen unter [Registry-Anmeldung](docs/deployment.md#an-der-registry-anmelden-und-image-laden).

```bash
# Nur für ein privates Paket; GITHUB_USERNAME ersetzen:
docker login ghcr.io --username GITHUB_USERNAME
docker compose config --quiet
docker compose pull provisioner
# Nur bei der ersten Inbetriebnahme:
docker compose run --rm --no-deps --volume proxmox-ais-keys:/run/secrets:rw provisioner init --username admin
docker compose up -d --no-build --wait
docker compose ps
```

Der HTTPS-Reverse-Proxy leitet auf `127.0.0.1:8080` weiter. Nur `init` bindet das
Schlüsselvolume schreibbar ein; im Regelbetrieb läuft das Image als UID/GID 10001
mit schreibgeschütztem Dateisystem. Die Daten liegen in `proxmox-ais-data`, der
Master-Key getrennt in `proxmox-ais-keys`.

Die [Deployment-Anleitung](docs/deployment.md) beschreibt Image-Auswahl,
Erstinitialisierung, Updates, Rollback und die Prüfung des laufenden Containers.

## GitHub Actions CI/CD

Der [GitHub-Actions-Workflow](.github/workflows/ci.yml) prüft Python und JavaScript, baut das Image
und testet HTTPS, Anmeldung und Datenerhalt beim Neustart im gehärteten Container.
Geschützte Standardbranch-Pushes veröffentlichen `sha-<Commit>` und `edge` in
`ghcr.io/netivra/proxmox-ais`; geschützte Release-Tags wie `v0.1.0` zusätzlich
die passende Versionsnummer. Feature-Branches und Pull Requests werden ohne Push geprüft.

Die Jobs verwenden GitHub-gehostete Runner mit `ubuntu-24.04`, Python 3.13,
Node.js 24 und Docker/Buildx. Ein eigener CI-Runner ist nicht erforderlich.
Für die Veröffentlichung müssen der Standardbranch und die Release-Tags durch
aktive GitHub-Regeln geschützt sein. GHCR erhält das automatisch bereitgestellte
`GITHUB_TOKEN`; ein eigenes CI-Secret wird nicht benötigt. Einrichtung,
Schutzregeln und Artefakte stehen in der [GitHub-Anleitung](docs/github-actions.md).

Die Pipeline liefert das getestete Image und dessen Digest in `deploy.env`.
Die Installation auf dem Zielserver folgt der [Deployment-Anleitung](docs/deployment.md).

## Erster Installationslauf

1. Als Administrator weitere Benutzer anlegen. Rollen sind `reader`, `operator`,
   `author`, `admin` und `developer`.
2. Standortbezogenen Installer-Gruppentoken erzeugen und den einmal angezeigten
   Token sicher speichern. Er ist Bestandteil des Installationsmediums.
3. ISO-Build, SHA-256, Assistant-Version und Zertifikatsfingerprint erfassen.
   Das Medium erst nach bestandenem Labortest freigeben.
4. Root-Zugang als Geheimnis speichern, Installationsprofil und
   Postinstallationsprofil anlegen. Module enthalten `check`, `apply` und
   `verify`; Veröffentlichungen benötigen einen Testnachweis. Standardmäßig
   muss eine andere Person als der Autor veröffentlichen.
5. Host mit UUID, Seriennummer und MAC-Adressen, FQDN, Standort, Profilversionen
   und Medium erfassen. Vorschau und geprüfte Zielgeräte kontrollieren.
6. Eine zeitlich begrenzte Freigabe mit FQDN-Bestätigung und ausdrücklicher
   Datenträgerbestätigung erteilen. Erst anschließend vom vorbereiteten Medium booten.
7. Laufstatus und redigierte Logs verfolgen. Der Lauf gilt erst nach erfolgreichen
   Pflichtprüfungen als abgeschlossen.

Die [ISO-Anleitung](docs/iso-preparation.md) beschreibt den offiziellen Assistant;
der [API-Ablauf](docs/api-workflow.md) zeigt konkrete Requests. Die vollständigen
Schemas stehen nach Anmeldung am laufenden Dienst unter `/openapi.json`; ein
generierter Stand liegt in [docs/openapi.json](docs/openapi.json).

## Prüfen und sichern

```bash
.venv/bin/python -m pytest
.venv/bin/proxmox-ais backup ./backups/first-snapshot
```

Unter PowerShell entsprechend `.\.venv\Scripts\python.exe -m pytest` und
`.\.venv\Scripts\proxmox-ais.exe backup .\backups\first-snapshot` verwenden.
Der [Testbericht](docs/test-report.md) beschreibt die ausgeführten Prüfungen.

Die Sicherung verwendet die SQLite-Backup-API und enthält Artefakte, verschlüsselte
Geheimnisse, ausgewählte Betriebseinstellungen und SHA-256-Prüfsummen. Den
Entschlüsselungsschlüssel separat sichern. Restore funktioniert ausschließlich
offline in ein leeres Datenverzeichnis und sperrt alte Maschinenberechtigungen.
Details stehen in [Betrieb und Wiederherstellung](docs/operations.md).

## Umfang und Grenzen

ISO-Erstellung erfolgt auf einer getrennten Build-Maschine. BMC-Steuerung, PXE,
Clusterbeitritt, Ceph, dauerhafte Konfigurationsverwaltung und zusätzliche
destruktive Storage-Module gehören nicht zu dieser Version. Die Kapazitäten
von 100 Hosts und zehn parallelen Installationen sind Planungsziele,
keine bereits gemessenen Leistungswerte. Gemeinsame ISO-Tokens plus gemeldete
Hardwaremerkmale setzen ein kontrolliertes Provisionierungsnetz voraus.
