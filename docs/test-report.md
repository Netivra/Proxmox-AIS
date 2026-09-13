# Softwaretestbericht

Stand: 13. September 2026. Die hier dokumentierten Softwaretests führen keine
Proxmox-Installation aus und verändern keine Hostdatenträger, Paketquellen oder
Produktivdienste. Die noch offenen praktischen Prüfungen stehen in der
[Kompatibilitätsmatrix](compatibility.md).

## Automatisierte Prüfungen

Die 49 Anwendungstests wurden unter Windows mit Python 3.14 ausgeführt:
47 Tests bestanden; zwei Linux-spezifische Prozesstests wurden dort übersprungen.
Die 68 zusätzlichen CI-Skripttests bestanden unter Windows mit Git Bash.
Insgesamt enthält die Testsuite jetzt 117 Testfälle; der aktuelle Gesamtlauf
unter Windows/Python 3.14.7 ergab 115 bestandene und zwei übersprungene Tests.
Unter Linux/Python 3.13.15 bestanden alle 117 Tests über das migrierte
CI-Einstiegsskript `ci/python-tests.sh`, einschließlich der OpenSSH-Prüfung.

| Testgruppe | Umfang | Geprüftes Verhalten |
| --- | --- | --- |
| `tests/test_acceptance.py` | 14 bestanden | Authentifizierung, CSRF, Rollen und Vieraugenregel; Ablehnung unbekannter, gesperrter und widersprüchlicher Hosts; Gruppenzuordnung und Freigabeablauf; zehn parallele Antwortabrufe mit genau einem Lauf; unveränderliche Konfiguration; Enrollment, Signaturen, Nonce-Replay, fremde Läufe, Ereignisreihenfolge und Pflichtverifikation; Artefaktmanipulation; Verschlüsselung; Backup/Restore und Schlüsselprüfung; Abgleich verlassener Läufe erst nach Lease-Ablauf und ausdrücklicher lokaler Bestätigung |
| `tests/test_protocol.py` | 8 bestanden | Tatsächlicher Runner gegen die HTTP-API über einen Testtransport mit gültigen Ed25519-Signaturen; Check-/Apply-Abläufe, verlorene Abschlussantwort, simulierter Neustart, fehlgeschlagene Verifikation und Operatorfortsetzung, wiederholte Abbruchbestätigung, optionale Fehler mit verpflichtender Abschlussprüfung sowie redigierte Logs |
| `tests/test_runner.py` | Windows: 25 bestanden, 2 übersprungen; Linux-CI: 27 bestanden | Dauerhafte Checkpoints, Wiederaufnahme nach Unterbrechung, Ablauf von Leases, Rebootbudget, Queue- und Logbegrenzung, Secrets-Redigierung, Hashprüfung, TLS-Zwang, eigenständiger Starthelfer, lokale Hardwaremerkmale, echte OpenSSL-Signaturen, SSH-Schlüsselprüfung, Repositoryprüfung, Bash-Syntaxprüfung sowie Prozess-Timeout und Ausgaberedigierung |
| `tests/test_ci.py` | 68 bestanden | Veröffentlichung nur von geschützten Standardbranch-/Versions-Refs und erlaubten GitHub-Ereignissen; Release-Versionsabgleich; GHCR-Pfade; kein Push nach Build-/Smoke-Fehlern; Digest-Artefakte; temporäre Docker-Zugangsdaten und Image-Tags; Isolation nach Run-ID, Wiederholung und Job-ID; eigene Python-Testumgebung mit Aufräumen bei Erfolg und Fehlern; fehlende Host-Werkzeuge verhindern übersprungene Pflichtprüfungen |

Die API- und Protokolltests verwenden eine temporäre SQLite-Datenbank sowie
temporäre Schlüssel. Die Protokolltests führen die Runner-Steuerung und
Signaturprüfung aus; die eigentlichen Modulphasen werden simuliert. Runner-Tests
prüfen Bash-Syntax und eingebetteten Python-Code ohne Provisionierungsänderungen.
Das ist kein Nachweis für funktionierende Betriebssysteminstallation oder
erfolgreiche Konfiguration echter Proxmox-Dienste.

Unter Windows benötigt die Veröffentlichung von Bash-Modulen Git Bash.
Der vorhandene WSL-Bash-Starter ohne Linux-Distribution genügt nicht. Die Anwendung
erkennt die native Git-Bash-Installation für die Syntaxprüfung.

Alle Tests erneut ausführen:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Beim Testlauf können Upstream-DeprecationWarnings von Starlette/httpx und AnyIO
erscheinen. Sie sind von fehlgeschlagenen Tests zu unterscheiden.

## Containerprüfung

`proxmox-ais-server:0.1.0` wurde erfolgreich mit Python 3.13 und Debian slim
gebaut. Das Dockerfile fixiert die geprüfte Basis über den Digest
`sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285`.

Ein kurzlebiger Container wurde mit schreibgeschütztem Dateisystem, deaktiviertem
Netzwerk und entfernten Linux-Capabilities gestartet. Die Prüfung ergab:

| Merkmal | Beobachtung |
| --- | --- |
| Benutzer | UID 10001 |
| Bash | `/usr/bin/bash`, GNU Bash 5.2.37 |
| OpenSSL | `/usr/bin/openssl`, OpenSSL 3.5.7 |
| CLI-Einstiegspunkt | `proxmox-ais`, Unterbefehle `init`, `serve`, `backup`, `restore` |

Auch Wheel-Erstellung und Installation wurden erfolgreich geprüft. In einem
früheren Linux-Testlauf im Anwendungsimage bestanden 48 Tests; ein Test wurde übersprungen, weil
`ssh-keygen` im Anwendungsimage nicht installiert ist. Die SSH-Schlüsselprüfung
wurde unter Windows ausgeführt. Der Linux-Lauf umfasste die POSIX-spezifischen
Prüfungen zur Prozessbeendigung nach Timeout und zur Redigierung von Ausgaben.
Der Testcontainer lief ohne Rootrechte, mit schreibgeschütztem Dateisystem,
entfernten Capabilities und `no-new-privileges`.

`tests/container_smoke.py` prüfte zusätzlich den installierten Dienst als echten
Prozess im Container: Anmeldung über TLS mit eigener vertrauenswürdiger Test-CA,
Ablehnung eines nicht vertrauten Zertifikats, CSRF, ausgelieferte Webdateien sowie
Erhalt von Hosts und Sitzungen nach einem Dienstneustart. Der Prozess lief als
UID 10001 mit schreibgeschütztem Root-Dateisystem, ohne Capabilities und mit
`no-new-privileges`; sämtliche Testdaten lagen in einem temporären Verzeichnis.

Die folgende Abfrage verändert weder Anwendungsdaten noch Konfiguration. Reproduzierbarer
Befehl für Bash unter Linux oder Git Bash:

```bash
docker run --rm --read-only --network none --cap-drop ALL \
  --entrypoint python proxmox-ais-server:0.1.0 \
  -c 'import os, shutil, subprocess; print(os.getuid()); print(shutil.which("bash")); print(shutil.which("openssl")); subprocess.run(["bash", "--version"], check=True); subprocess.run(["openssl", "version"], check=True)'
```

TLS-Reverse-Proxy, reale Provisionierungsnetze, Medienboot, Initialisierung und
Wiederherstellung in der späteren Betriebsumgebung müssen zusätzlich geprüft werden.
Ein erfolgreicher Image-Build allein ist keine Betriebsfreigabe.

## GitHub Actions und GHCR

Der [Workflow](../.github/workflows/ci.yml) verwendet GitHub-gehostete
Ubuntu-24.04-Runner, Python 3.13 und Node.js 24. Python- und JavaScript-Prüfung
müssen erfolgreich sein, bevor das Containerimage gebaut und getestet wird.
Die [Einrichtungsanleitung](github-actions.md) beschreibt Schutzregeln,
Paketberechtigungen und die erste Veröffentlichung.

Bei der Migration wurden die folgenden Prüfungen lokal ausgeführt:

- Die vollständige Windows-Testsuite bestand mit 115 erfolgreichen Tests;
  zwei Linux-Prozesstests wurden plattformbedingt übersprungen.
- Unter Linux/Python 3.13.15 führte `ci/python-tests.sh` alle 117 Tests
  erfolgreich als UID 10001 aus. Das Skript installierte die Abhängigkeiten
  in einer eigenen virtuellen Umgebung, schrieb den JUnit-Bericht und
  entfernte die Umgebung anschließend.
- Die 68 CI-Skripttests prüfen mit Docker- und Python-Testdoubles
  GitHub-Ereignisse, geschützte Refs, Versionsabgleich, GHCR-Pfade,
  Publish-Reihenfolge, Digest-Artefakte und Fehlerpfade. Run-ID, Wiederholung
  und Job-ID isolieren temporäre Ressourcen. Ein Build- oder Smoke-Fehler
  verhindert Registry-Anmeldung und Push.
- Die im Workflow eingebettete Veröffentlichungsentscheidung wurde mit
  17 Fällen ausgeführt: geschützte Branches und Release-Tags, manueller Start,
  andere Standardbranchnamen, Pull Requests, ungeschützte Refs, ungültige Tags
  und Shell-Sonderzeichen im Ref-Namen.
- `actionlint` 1.7.12 prüfte den Workflow mit genau einer ausgenommenen
  Schema-Meldung: Die aktuelle Version kennt `concurrency.queue` noch nicht.
  `queue: max` wurde gegen die offizielle
  [GitHub-Syntax](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
  geprüft; die fehlende Linter-Unterstützung ist in
  [actionlint #657](https://github.com/rhysd/actionlint/issues/657) dokumentiert.
  Weitere Linter-Befunde gab es nicht.
- `node --check provisioner/static/app.js` und `docker compose config --quiet`
  mit gesetzten Beispielwerten für Image und öffentliche URL waren erfolgreich.
- `ci/container.sh verify` lief mit GitHub-Umgebungsvariablen gegen den lokalen
  Linux-Docker-Dienst 29.7.2. amd64-Build und Container-Smoke-Test bestanden:
  vertrauenswürdiges TLS, Ablehnung eines fremden Zertifikats, Anmeldung,
  CSRF, Webdateien sowie Erhalt von Hosts und Sitzungen beim Dienstneustart.
  Das Skript erzeugte `build.env` und entfernte den temporären Image-Tag.

Ein tatsächlicher GitHub-Actions-Lauf einschließlich GHCR-Anmeldung,
Registry-Push und Artefakt-Upload wurde bei dieser lokalen Prüfung nicht
ausgeführt. Die Push-Fehlerpfade und Digest-Auswertung sind durch die
CI-Skripttests abgedeckt; die Paketberechtigungen und aktiven Schutzregeln
müssen im GitHub-Repository eingerichtet und beim ersten Lauf bestätigt werden.

## Browserprüfung

In Chromium wurden Anmeldung, alle neun Verwaltungsansichten, Hostanlage und
Hostbearbeitung, Geheimnisreferenzen, Gruppentoken, ISO-Registrierung und Buildbefehl,
Modulvorlagen, Skript- und Profilveröffentlichung, Installationsvorschau,
Freigabe, Abbruch, Laufabgleich, Benutzeranlage und Leserrechte durchgespielt.
Die mobile Ansicht bei 390 Pixeln wurde ebenfalls geprüft. Es traten keine
JavaScript- oder CSP-Fehler auf. Die separate Testkonfiguration verwendete
temporäre Daten und deaktivierte die Vieraugenregel für den Formulartest mit
einem Administrator; die Vieraugenregel wurde in den API-Tests geprüft.

## API-Artefakt

[openapi.json](openapi.json) wird über `create_app(Settings(...)).openapi()` aus
dem aktuellen Anwendungscode erzeugt. Der Export verwendet ausschließlich
temporäre Testverzeichnisse, keine produktiven Benutzer oder Schlüssel. Er
enthält Schemas und Endpunktbeschreibungen, keine Datenbankinhalte, Sessions oder
Geheimniswerte. Bei Änderungen an den API-Modellen muss der Export erneuert werden.
