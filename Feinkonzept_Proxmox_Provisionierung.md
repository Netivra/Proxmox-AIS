# Feinkonzept für ein Webtool zur Proxmox Provisionierung

**Automatische Installation und gesteuerte Postinstallation**

Version 1.0  \|  13. September 2026  \|  Technische Umsetzungsvorlage

### Ziel und Architekturentscheidung

Das Tool verwaltet Proxmox VE Server, Installationsprofile und versionierte Postinstallationsskripte über eine Weboberfläche. Eine vorbereitete Proxmox ISO kontaktiert das Tool, erhält die für den Server freigegebene Installationskonfiguration und installiert Proxmox. Beim ersten Start lädt ein kleiner Starthelfer die zugewiesene Nachkonfiguration und führt sie kontrolliert aus.

Empfohlen wird ein zentraler Dienst in einem Docker Container mit integrierter Weboberfläche, API und persistenter SQLite Datenbank. Die Server bauen die Verbindungen selbst per HTTPS auf. Ein eigener, zeitlich begrenzt aktiver Runner auf jedem Server übernimmt Skriptausführung, Statusmeldungen und Wiederaufnahme nach Unterbrechungen.

### Leitentscheidungen

| Bereich | Festlegung |
| --- | --- |
| Installationsmedium | Eine gemeinsame ISO je Standort oder Bereitstellungsgruppe; hostbezogene Antworten kommen vom Tool. |
| Freigabe | Nur eindeutig zugeordnete und ausdrücklich für Installation freigegebene Hosts erhalten eine Antwortdatei. |
| Reproduzierbarkeit | Jeder Lauf bindet unveränderliche Versionen von Profil, Skripten und aufgelösten Parametern. |
| Postinstallation | Wiederaufnehmbarer Ablauf mit einzelnen Schritten, Zustandsprüfung und abschließender Funktionsprüfung. |
| Betrieb | Ein Anwendungscontainer; TLS direkt oder über vorhandenen Reverse Proxy. ISO Vorbereitung zunächst als separater Arbeitsschritt. |

### Planungsannahmen und Umfang

Ausgelegt wird zunächst eine interne Umgebung mit bis zu 100 verwalteten Hosts und 10 parallelen Installationen. Diese Werte sind Abnahmeziele, keine gemessenen Leistungsangaben. DHCP, DNS, Zeitsynchronisation und Netzwerkrouting werden bereitgestellt. Unterstützte ISO Builds werden einzeln freigegeben.

Die erste Version umfasst Neuinstallation, Basiskonfiguration, Profile, Skripte, Freigaben, Live Status und Auditprotokoll. Automatisches Einschalten, BMC Steuerung, PXE, Clusterbeitritt, Ceph Aufbau und dauerhafte Konfigurationsverwaltung sind spätere Erweiterungen. Die eigentliche Löschung der Installationsdatenträger bleibt Aufgabe des Proxmox Installers.

## 1 Anbindung an den Proxmox Installer

### Installationskonfiguration und erster Start

Der Proxmox Automated Installer kann seine Antwortdatei per HTTP beziehungsweise HTTPS abrufen. Dabei sendet er einen POST Request mit Systeminformationen. Aktuelle Dokumentation beschreibt zusätzlich --answer-auth-token; daraus entsteht ein Authorization Header im Format Bearer &lt;name&gt;:&lt;secret&gt;. Das Tool liefert im Erfolgsfall TOML als Antwortinhalt. [1]

Der First Boot Mechanismus unterstützt ein ausführbares Skript von der ISO oder einer URL. Bei from-url wird es bereits im Installer heruntergeladen und auf dem Zielsystem hinterlegt; die Ausführung erfolgt beim ersten Boot. Der hier vorgesehene Starthelfer verwendet ordering = "network-online" und prüft danach selbst die Erreichbarkeit des Tools. Der dokumentierte Implementierungsansatz zeigt diese Trennung von Download und Ausführung. [2]

Das optionale Proxmox Postinstallation Webhook meldet Installationsinformationen. Es ersetzt keine lokale Skriptausführung. Im Konzept dient es ausschließlich als zusätzliches Ereignis zwischen Antwortauslieferung und erstem Start. [3]

### Vorbereitung der gemeinsamen ISO

Der folgende Befehl ist ein Konfigurationsmuster. SOURCE.iso, Token und Fingerprint sind vor Verwendung zu ersetzen. Es werden keine produktiven Zugangsdaten vorgegeben. Die unterstützte Kombination aus ISO und Assistant muss die verwendeten Optionen tatsächlich enthalten.

```bash
proxmox-auto-install-assistant prepare-iso SOURCE.iso \
  --fetch-from http \
  --url "https://provision.example.net/installer/v1/answer" \
  --cert-fingerprint "<SHA256-Zertifikatsfingerprint>" \
  --answer-auth-token "<gruppenname>:<zufaelliges-secret>"
```

Das Grundmuster mit URL, Zertifikatsfingerprint und Token ist offiziell dokumentiert. [3] Der eigene API Pfad ist eine Festlegung dieses Konzepts. Das erzeugte Medium wird zusammen mit ISO Prüfsumme, Assistant Paketversion, Gültigkeitsbereich und Prüfergebnis registriert. Skriptänderungen erfordern danach keinen neuen ISO Build.

### Abschnitt der dynamischen Antwortdatei

```toml
[first-boot]
source = "from-url"
ordering = "network-online"
url = "https://provision.example.net/bootstrap/v1/<download-token>"
cert-fingerprint = "<SHA256-Zertifikatsfingerprint>"
```

Dies ist nur der First Boot Abschnitt. Die vollständige Antwort enthält zusätzlich global, network und disk-setup mit hostbezogenen Werten. Vor Auslieferung wird sie für die freigegebene Zielversion validiert. Der Download Token gewährt ausschließlich Zugriff auf den Starthelfer dieses Laufs.

### Kompatibilität als Freigabebedingung

Keine pauschale Unterstützung aller Proxmox Versionen zusagen. Pro ISO werden Antwortschema, Token Header, First Boot Verhalten, Webhook und Bootverfahren getestet. Fehlt native Token Unterstützung, lehnt Version 1 dieses Medium ab. Ein neuer Assistant allein aktualisiert nicht die Komponenten innerhalb einer älteren ISO.

## 2 Komponenten und Bereitstellung

Die Anwendung ist ein modularer Monolith. Fachliche Komponenten bleiben im Code getrennt, werden jedoch gemeinsam ausgeliefert. Dadurch erfüllt der Regelbetrieb die Vorgabe eines Docker Containers ohne zusätzliche Datenbank oder Nachrichtenwarteschlange.

| Komponente | Aufgabe | Vorschlag |
| --- | --- | --- |
| Weboberfläche | Inventar, Profile, Skripte, Freigaben, Laufdetails | Serverseitiges HTML mit kleinen JavaScript Komponenten |
| API | Installer Adapter, Runner API, Verwaltungsfunktionen | Python mit FastAPI und typisierten Datenmodellen |
| Konfigurationsdienst | Werte zusammenführen, validieren, Versionen fixieren | TOML und JSON Serializer; restriktive Vorlagen |
| Laufsteuerung | Freigaben, Status, Timeout, Sperren, Ereignisse | Persistente Zustände; keine Aufgaben nur im Arbeitsspeicher |
| Datenhaltung | Inventar, Versionen, Läufe, Ereignisse, Audit | SQLite im lokalen Volume; Schema Migrationen |
| Artefaktablage | Unveränderliche Skriptpakete und Run Manifeste | Dateien nach Inhaltsdigest; Referenzen in Datenbank |
| Starthelfer und Runner | Start, Abruf, Ausführung und Rückmeldungen | Kleiner Bash Starthelfer; Python Runner und Bash Module |

FastAPI unterstützt OpenAPI und JSON Schema; daraus wird die technische API Dokumentation erzeugt. [4] Die Oberfläche verwendet dieselben fachlichen Dienste, aber eigene Benutzerrechte. Geheimnisse und freigegebene Skripte dürfen nicht über allgemeine Dateipfade ausgeliefert werden.

### Verbindungen

Administratoren greifen aus dem Verwaltungsnetz per HTTPS auf die Oberfläche zu. Installer und installierte Hosts erreichen nur die vorgesehenen Maschinenendpunkte. Es gibt im Grundumfang keine eingehende SSH Verbindung vom Tool zu den Hosts. Paketquellen werden direkt oder über einen internen Paketproxy erreicht.

TLS endet entweder im vorhandenen Reverse Proxy oder direkt am Anwendungsserver auf einem unprivilegierten Port. Im Proxybetrieb ist der Backend Port ausschließlich für den Proxy erreichbar. Vertrauenswürdige Proxy Header werden auf konkrete Proxy Adressen beschränkt.

### ISO Erstellung als eigener Vorgang

Version 1 stellt den vorbereiteten Befehl, die erforderlichen Parameter und eine Registrierung der erzeugten ISO bereit. Der Administrator erstellt das Medium auf einer kontrollierten Build Maschine mit dem offiziellen Assistant. Das Webtool selbst benötigt dafür weder privilegierte Rechte noch einen Docker Socket.

Ein späterer ISO Builder kann denselben Auftrag in einem kurzlebigen, separat gestarteten Build Container ausführen. Dabei gelten feste Eingabepfade, Ressourcenlimits und Prüfsummen. Das Ausführen eines vom Benutzer eingegebenen Shell Befehls im Anwendungscontainer ist nicht Teil der Architektur.

## 3 Ablauf einer Installation

| Schritt | Aktion und Ergebnis |
| --- | --- |
| 1 Vorbereiten | Host anhand Seriennummer, System UUID und MAC Adressen erfassen; FQDN, Netzwerk, Datenträgerprofil und Skriptprofil zuweisen. |
| 2 Prüfen | Aufgelöste Konfiguration anzeigen, Pflichtfelder validieren, Kollisionen bei IP und FQDN prüfen; Skriptversionen und Modulreihenfolge festschreiben. |
| 3 Freigeben | Operator erteilt eine zeitlich begrenzte Installationsfreigabe. Neuinstallationen enthalten eine gesonderte Bestätigung der zu überschreibenden Zielgeräte. |
| 4 Booten | Administrator startet den Server mit der vorbereiteten ISO über USB oder vorhandene virtuelle Medien. |
| 5 Zuordnen | Installer sendet Systemdaten. API prüft Gruppentoken, Hostzuordnung, Freigabefenster, Versionsprofil und laufende Vorgänge. |
| 6 Antworten | API reserviert atomar den Installationslauf und liefert dieselbe validierte TOML Antwort bei erlaubten Wiederholungen. |
| 7 Installieren | Installer lädt den runbezogenen Starthelfer und installiert Proxmox. Ein konfiguriertes Webhook kann das Ende der Basisinstallation melden. |
| 8 Registrieren | Nach dem Reboot legt der Starthelfer lokale Zustandsdateien und einen systemd Dienst an. Der Runner registriert sich für genau diesen Lauf. |
| 9 Konfigurieren | Runner lädt Manifest und Skriptpaket, prüft Integrität, führt Schritte aus und meldet Ereignisse, Protokolle und Neustartbedarf. |
| 10 Abschließen | Pflichtprüfungen sind erfolgreich. Der Lauf wird abgeschlossen, Ausführungsrechte erlöschen und der Runner wird deaktiviert. |

### Unbekannte und widersprüchliche Hosts

Ein unbekannter Host kann mit gültigem Gruppentoken als entdeckt gespeichert werden, erhält jedoch keine ausführbare Installationskonfiguration. Mehrdeutige Identitäten oder widersprüchliche UUID und Seriennummern führen ebenfalls zur Sperre. Die Oberfläche zeigt den Grund und die nötige Zuordnung.

Das Konzept setzt kein beliebig langes Warten des Proxmox Installers voraus. Nach einer Ablehnung wird die Zuordnung im Tool korrigiert und der Installationsversuch neu gestartet. Für unbeaufsichtigten Betrieb sind Hosts und Freigaben deshalb vorher anzulegen.

### Grenze der Installationssperre

Ein HTTP Abruf und eine tatsächliche Installation sind nicht gleichzusetzen. Wiederholungsabrufe werden nur in einem kurzen, getesteten Auslieferungsfenster zugelassen. Ab gemeldetem Installationsende, Runner Registrierung oder Ablauf dieses Fensters ist eine neue Antwort gesperrt. Das verhindert weitere Freigaben, kann eine bereits ausgelieferte und lokal gespeicherte Antwort aber nicht zurückziehen. Die Bootreihenfolge muss nach Installation auf das lokale System wechseln.

## 4 Bedienoberfläche und Berechtigungen

### Zentrale Ansichten

| Ansicht | Inhalte und Aktionen |
| --- | --- |
| Übersicht | Anzahl bereiter, laufender, gestörter und abgeschlossener Hosts; ausstehende Freigaben; letzte Ereignisse; Filter nach Standort und Profil. |
| Serverinventar | Identitäten, FQDN, IP, Standort, Tags, Profil und letzter Kontakt. Anlegen, Datenimport, Zuordnen und Sperren. |
| Serverdetail | Sollkonfiguration, erkannte Daten, Historie und aktueller Lauf. Ansichten für Schritte, redigierte Logs, Prüfungen und Fehlerursache. |
| Installationsprofile | Basisparameter und Hardwareprofil getrennt pflegen. Vorschau der vollständigen Antwort mit markierten Geheimnisreferenzen. |
| Postinstallationsprofile | Module auswählen und ordnen, Parameter setzen, Abhängigkeiten prüfen, Rebootstrategie festlegen. |
| Skriptverwaltung | Quelltext, Versionsvergleich, Syntaxprüfung, Freigabestatus, Testnachweis, unterstützte Proxmox Builds und Änderungsgrund. |
| Installationsmedien | URL, Gültigkeitsbereich, Tokenstatus, Zertifikat, Assistant Version, ISO Prüfsumme und vorbereiteter Build Befehl. |
| Audit und Einstellungen | Änderungen, Freigaben, Tokenzugriffe, Aufbewahrung, Benutzer, Zertifikate und Sicherungsstatus. |

### Benutzerrollen

| Rolle | Berechtigung |
| --- | --- |
| Leser | Inventar und redigierte Laufdaten ansehen; keine Änderungen oder Geheimnisse. |
| Operator | Hosts zuweisen, freigegebene Profile nutzen, Installationen freigeben und sichere Wiederaufnahme anfordern. |
| Skriptautor | Skripte und Profile als Entwurf bearbeiten; keine eigene Veröffentlichung im Vieraugenmodus. |
| Administrator | Benutzer, Geheimnisse, Vertrauensanker und Skriptveröffentlichungen verwalten; privilegierte Vorgänge auditieren. |
| Entwickler | Hat alle Berechtigungen. |

### Bedienregeln

Die Aktion Installation freigeben zeigt Host, Zielversion, ausgewählte Datenträger, aufgelöste Netzwerkeinstellungen und Skriptversionen zusammen an. Profiländerungen wirken nur auf neue Läufe. Ein laufender Vorgang wird niemals durch Bearbeiten eines Profils stillschweigend verändert.

Wiederaufnehmen und Neu installieren sind getrennte Aktionen. Abbrechen stoppt einen Runner am nächsten sicheren Übergang. Ein bereits laufender Paketmanager oder eine bereits gestartete Datenträgeroperation wird nicht als sicher rückgängig darstellbar behandelt. Unbekannt bezeichnet fehlende Rückmeldung, nicht nachgewiesenen Erfolg oder Fehlschlag.

## 5 Profile und Postinstallationsmodule

### Auflösung der Konfiguration

Werte werden in fester Reihenfolge zusammengeführt: globale Vorgaben, Standort, Profilversion, Hostwerte und freigegebene Laufparameter. Spezifischere Werte überschreiben allgemeinere. Sicherheitsregeln, freigegebene Proxmox Builds und gesperrte Variablen dürfen durch Hostwerte nicht gelockert werden. Ergebnis und Herkunft jedes Werts sind in der Vorschau sichtbar.

Installationsprofil und Postinstallationsprofil bleiben getrennte Objekte. Das erste enthält Sprache, Zeitzone, Root Zugang, FQDN, Managementnetz und Systemdatenträger. Das zweite enthält Module und deren Parameter. Ein Lauf speichert die vollständig aufgelöste Kombination mit Versionsnummern und Digest.

### Empfohlene Module für die erste Version

| Modul | Verhalten | Erfolgskriterium |
| --- | --- | --- |
| Voraussetzungen | Zielversion, Zeit, DNS, Konnektivität und freien Speicher prüfen | Alle Pflichtbedingungen erfüllt |
| Paketquellen | Freigegebenes Repositoryprofil anwenden; Subscription berücksichtigen | Erwartete Quellen aktiv und erreichbar |
| Basispakete | Definierte Paketliste installieren; Paketmanager Sperre beachten | Erwartete Pakete vorhanden |
| Zugang und SSH | Freigegebene Schlüssel und Benutzer konfigurieren | Konfiguration validiert; definierter Zugang vorhanden |
| Zeit und Monitoring | Zeitsynchronisation und gewählten Monitoring Agent konfigurieren | Dienste aktiv; lokale Funktionsprüfung erfolgreich |
| Zusätzlicher Storage | Freigegebene, nicht destruktive Storage Anbindung setzen | Sollressource vorhanden und lokal nutzbar |
| Abschlussprüfung | Netzwerk, PVE Dienste, Storage und Versionsstand prüfen | Alle als Pflicht markierten Prüfungen erfolgreich |

### Schnittstelle eines Moduls

Jedes Modul hat eine unveränderliche ID und Version, eine unterstützte Zielmatrix, ein Parameterschema, Abhängigkeiten, Timeout, Wiederholungsregeln und eine Prüffunktion. check ermittelt den Istzustand; apply ändert nur erforderliche Werte; verify prüft das Ergebnis. Ein Custom Bash Skript muss dieselbe Schnittstelle einhalten.

Parameter werden in einer JSON Datei übergeben. Es gibt kein eval und keine ungeprüfte Textersetzung in Shell Befehlen. Eine festgelegte Funktion liest erlaubte Parameter typisiert aus. Geheimnisse werden nur für den aktuellen Schritt als Datei mit Modus 0600 bereitgestellt, anschließend entfernt und niemals in die Profilvorschau oder Logs geschrieben.

Eine syntaktische Prüfung, beispielsweise bash -n und ein Linter, bewertet keine fachliche Sicherheit. Die Veröffentlichung verlangt zusätzlich einen Test auf einem passenden Testhost. Kernelupdates, Netzwerkumbau und destruktive Storage Schritte sind eigenständige Erweiterungen mit zusätzlicher Wiederherstellungsplanung.

## 6 Starthelfer und wiederaufnehmbare Ausführung

### Verantwortung des Starthelfers

Der Starthelfer enthält den kleinen, fest versionierten Runner, run_id, eine begrenzte Enrollment Berechtigung, die API Adresse und den erwarteten Hostbezug. Er enthält keine allgemeinen Administratorzugänge und kein vollständiges Postinstallationsskript. Das Paket bleibt unter dem Limit des freigegebenen Installers.

Vor dem ersten Netzwerkabruf schreibt er seine Konfiguration atomar nach /etc/pve-provisioner, installiert den mitgelieferten Runner und aktiviert pve-provisioner.service nach network-online.target. Bash, Python und TLS Unterstützung werden je Zielimage geprüft. Der Runner führt Enrollment und Skriptabruf aus; fehlende Voraussetzungen erzeugen einen sichtbaren Fehler.

### Lokaler Zustand und Ausführungsregeln

Der Runner verwendet /var/lib/pve-provisioner für run_id, Manifestdigest, Gerätebindung, Ereigniszähler und Schrittzustände. Er hält eine lokale flock Sperre. Der Server erlaubt gleichzeitig höchstens einen aktiven Ausführungslauf pro Host. Pakete werden erst vollständig heruntergeladen, validiert und atomar in das Laufverzeichnis übernommen.

Vor jedem Schritt prüft der Runner die Laufberechtigung und den lokalen Checkpoint. Er schreibt applying dauerhaft vor der Änderung, führt apply aus und schreibt succeeded erst nach erfolgreichem verify. Unvollständige Schritte werden nach einem Absturz erneut geprüft. Ein bloßer Rückgabecode 0 ersetzt keine Zustandsprüfung.

| Situation | Festgelegtes Verhalten |
| --- | --- |
| Netzausfall | Abrufe mit exponentiellem Abstand und Zufallsanteil wiederholen; nach konfigurierter Gesamtdauer auf manuelle Prüfung wechseln. |
| API nicht erreichbar | Bereits laufenden sicheren Schritt beenden, Ereignisse lokal puffern; keine weiteren Änderungen nach Ablauf der Laufberechtigung. |
| Geplanter Reboot | Checkpoint und Rebootabsicht speichern; Nachricht zustellen oder puffern; Neustartbudget prüfen; nach Boot über denselben Lauf fortsetzen. |
| Unklarer Schrittzustand | check und verify ausführen. Nur ausdrücklich wiederholbare Schritte erneut anwenden; sonst manuelle Prüfung. |
| Doppelte Rückmeldung | Ereignis anhand run_id und Sequenznummer deduplizieren; Quittierung wiederholt zurückgeben. |
| Erfolgreicher Abschluss | Terminalen Checkpoint speichern, finalen Status quittieren lassen, Ausführungsrechte widerrufen und Dienst deaktivieren. |

### Systemd und Fehlergrenzen

Der eigene Dienst verwendet Type=simple und Restart=on-failure mit begrenzter Startfrequenz. Er benötigt keine Proxmox Hook Wiederholung. Permanente Modulfehler führen zu einem persistenten Haltzustand; ein Neustart des Dienstes darf sie nicht automatisch erneut ausführen. Zulässige Wiederholungen sind Teil der Moduldefinition, nicht einer pauschalen Shell Schleife.

Beliebige Shell Aktionen haben keine Exactly once Garantie. Zustandsprüfungen und idempotente Schritte ermöglichen sichere Wiederaufnahme; unklare destruktive Aktionen werden nicht automatisch wiederholt.

## 7 Zustände und Ereignisse

Hostzustand, Installationsfreigabe und Ausführungslauf sind getrennte Zustandsautomaten. Ein Host kann bereits installiert sein, während ein neuer Postinstallationslauf wartet. Eine solche Nachkonfiguration erteilt niemals automatisch eine neue Erlaubnis zum Löschen und Installieren.

| Laufzustand | Bedeutung und erlaubter Übergang |
| --- | --- |
| prepared | Konfiguration fixiert; für Installation zeitlich begrenzt freigegeben. Übergang zu answer_served oder expired. |
| answer_served | Antwort ausgeliefert. Installation läuft möglicherweise; ihr Beginn ist ohne weitere Meldung nicht bewiesen. |
| installed_reported | Optionales Installer Webhook empfangen. Runner Registrierung wird erwartet; dieser Zustand darf übersprungen werden. |
| runner_ready | Enrollment abgeschlossen, Hostbindung geprüft. Manifest und Ausführungsberechtigung können ausgegeben werden. |
| running | Ein bestimmter Schritt läuft; Heartbeats und geordnete Ereignisse werden angenommen. |
| reboot_pending | Checkpoint vorhanden. Gleicher Lauf wartet auf Rückkehr mit geänderter boot_id. |
| waiting_retry | Temporärer Fehler bei einem ausdrücklich wiederholbaren Vorgang; nächster Versuch ist terminiert. |
| needs_review | Unklarer oder nicht automatisch behebbarer Zustand. Weiterarbeit erfordert eine dokumentierte Operatorentscheidung. |
| succeeded | Alle Pflichtschritte und Abschlussprüfungen erfolgreich, Abschlussmeldung gespeichert. |
| failed oder cancelled | Permanenter Fehler oder Abbruch bestätigt. Weitere Änderungen sind gesperrt. |
| expired | Freigabe oder Startfrist abgelaufen, ohne rechtzeitige Aufnahme des Laufs. |

### Ereignisformat

```json
{
  "run_id": "run-2026-001",
  "sequence": 42,
  "boot_id": "<lokale-boot-id>",
  "step_id": "base-packages",
  "type": "step.succeeded",
  "occurred_at": "2026-09-13T10:15:00Z",
  "exit_code": 0,
  "verification": {"packages_present": true}
}
```

Der Server ergänzt Empfangszeit und authentifizierte Geräteidentität. Ereignisse dürfen nur zulässige Übergänge auslösen. Veraltete Meldungen können einen terminalen Erfolg nicht auf running zurücksetzen. Der Server bestätigt die höchste lückenlos gespeicherte Sequenznummer; der Runner behält nicht quittierte Ereignisse.

Ein fehlender Heartbeat setzt den Kontaktstatus auf unbekannt und erzeugt nach einer konfigurierten Frist eine Betriebswarnung. Er beweist weder einen Modulfehler noch einen erfolgreichen Abschluss. Die Übersicht verwendet deshalb keine künstliche Prozentanzeige für nicht beobachtbare Installerphasen.

## 8 Schnittstellen des Tools

### Maschinenendpunkte

| Methode und Pfad | Vertrag |
| --- | --- |
| POST /installer/v1/answer | Native Proxmox Systemdaten und Gruppentoken; 200 mit roher TOML Antwort bei passender Freigabe. |
| GET /bootstrap/v1/{token} | Runbezogener Starthelfer; ausschließlich über zeitlich begrenzte Download Capability. |
| POST /installer/v1/report/{token} | Optionales Proxmox Webhook; eigener Token, nur Ereignisannahme für diesen Lauf. |
| POST /agent/v1/enroll | Enrollment Secret und lokal erzeugter öffentlicher Geräteschlüssel; registriert den Runner für einen Lauf. |
| POST /agent/v1/lease | Authentifiziertes Gerät erhält begrenzte Laufberechtigung oder Status wait, stop, revoked. |
| GET /agent/v1/runs/{id}/manifest | Fixierte Schrittfolge, Parameterreferenzen, Digests, Fristen und freigegebene Artefakte. |
| GET /agent/v1/artifacts/{digest} | Nur für den eigenen Lauf autorisierte, unveränderliche Skriptpakete. |
| POST /agent/v1/runs/{id}/events | Batch mit Sequenznummern; transaktionale Speicherung und Quittierung. |
| POST /agent/v1/runs/{id}/logs | Begrenzte, redigierte Logblöcke mit Sequenz und Größenlimit. |
| POST /agent/v1/runs/{id}/complete | Abschluss mit Prüfergebnissen; serverseitige Prüfung aller Pflichtschritte. |

### Verwaltungsschnittstellen

Unter /api/v1 liegen CRUD Ressourcen für hosts, profiles, modules, releases und iso-records sowie Aktionen approve-install, resume, cancel und publish. Änderungen benötigen Benutzeranmeldung, rollenbasierte Prüfung und bei Browser Sessions CSRF Schutz. Versionierte Objekte werden mit ETag beziehungsweise Versionsnummer gegen gleichzeitiges Überschreiben geschützt.

### Fehlersemantik und Validierung

401 bedeutet ungültige Authentifizierung, 403 fehlende Freigabe, 409 mehrdeutige Zuordnung oder Zustandskonflikt, 410 abgelaufene Berechtigung, 422 ungültige Konfiguration und 503 temporäre Nichtverfügbarkeit. Für unbekannte Hosts wird niemals eine Standardantwort mit Datenträgerauswahl zurückgegeben. Fehlertexte enthalten keine Geheimnisse.

Nur der Installer Adapter akzeptiert das native Proxmox Payload. Er speichert dessen Schemahinweis und normalisiert verfügbare Identitätsfelder; nicht vorhandene Felder werden nicht erfunden. Die eigene Runner API verwendet ein separat dokumentiertes Schema. Die Anwendung garantiert keine zusätzlichen HTTP Header oder eigenen Felder seitens des unveränderten Proxmox Installers.

Antworten und Starthelfer verwenden Cache-Control: no-store. Keine Weiterleitung auf fremde Domains. GET /agent/v1/runs/{id}/secrets/{step} liefert nur Geheimnisse des autorisierten aktuellen Schritts. Größen und Ratenlimits gelten je Endpunkt; nach dem Starthelfer stehen Zugangsdaten nur in authentifizierten Requests.

## 9 Vertrauensmodell und Geheimnisse

### Hostzuordnung ist keine Geräteauthentifizierung

UUID, Seriennummer und MAC Adressen helfen bei der Zuordnung, sind aber vom anfragenden System behauptete und grundsätzlich kopierbare Werte. Auch ein in einer gemeinsamen ISO gespeicherter Token ist auslesbar. Das Standardmodell setzt daher ein kontrolliertes Provisionierungsnetz und Schutz der Installationsmedien voraus.

Bei höherem Schutzbedarf erhält jeder Host ein individuelles Installationsmedium mit eigenem Token oder ein vorab unabhängig bereitgestelltes Gerätegeheimnis. Optional ist später hardwaregestützte Attestierung möglich. Ein gemeinsamer ISO Token plus MAC Abgleich darf in der Oberfläche nicht als starker Identitätsnachweis bezeichnet werden.

| Berechtigung | Begrenzung und Lebenszyklus |
| --- | --- |
| ISO Gruppentoken | Nur Antwortabruf für einen Standort und freigegebene Hosts; Ablaufdatum, Sperrung und Ratenlimit. Keine Verwaltungsrechte. |
| Bootstrap Download Token | Nur ein Starthelfer eines Laufs. Kurzes Abruffenster mit begrenzten Wiederholungen; danach gesperrt. |
| Enrollment Secret | Nur erstmalige Bindung eines Geräteschlüssels an den bereits reservierten Lauf; Ablauf nach geplantem Installationsfenster. |
| Gerätebindung | Privater Schlüssel lokal mit Modus 0600. Requests signiert mit Nonce und Request Digest; kein Gerätezugriff auf andere Hosts. |
| Laufberechtigung | Kurzlebig und erneuerbar, etwa 15 Minuten; nur zugewiesene Module, Artefakte und Ereignisse. Terminale Läufe entziehen Änderungsrechte. |
| Betriebsgeheimnisse | Verschlüsselt gespeichert; Schlüssel außerhalb des Datenvolumes. Ausgabe nur an berechtigten Schritt und niemals in allgemeine Logs. |

### Enrollment mit sicherer Wiederholung

Der erste gültige Enrollment Request bindet das Secret atomar an einen lokal generierten Geräteschlüssel. Geht die Antwort verloren, darf derselbe Schlüssel den Vorgang erneut anfragen; ein anderer wird abgewiesen. Spätere Authentifizierung verlangt Schlüsselbesitz. Eine Umbindung ist ein Auditvorgang. Signaturen und Replay Schutz werden mit einem etablierten Verfahren umgesetzt. Nach Abschluss bleibt kurzzeitig nur die idempotente Abschlussquittierung möglich.

### TLS und Integrität

Der ISO Fingerprint schützt die Verbindung zum Antwortdienst. Starthelfer und Runner verwenden passende Vertrauensanker; Zertifikatsrotation wird mit neuen Medien und Übergangsfristen geplant. Zertifikatsprüfungen werden nicht deaktiviert. Skriptpakete werden anhand eines authentifiziert bezogenen Manifests und Inhaltsdigests geprüft.

Der Bootstrap URL Token kann in Installerprotokollen erscheinen. Deshalb ist er eine begrenzte Capability und wird in Proxy und Anwendungslogs maskiert. Reproduzierbare Starthelfer mit enthaltenem Enrollment Secret werden verschlüsselt aufbewahrt. Token Verifizierer werden gehasht gespeichert. Root Passwörter sind individuell; bevorzugt wird ein für die Zielversion zulässiger Passwort Hash übertragen.

## 10 Datenmodell und Konsistenz

| Entität | Wesentliche Felder und Beziehungen |
| --- | --- |
| Host | id, Standort, FQDN, Soll IP, Status, Profilzuordnung, gesperrt, letzter Kontakt. |
| HostIdentity | host_id, Typ, Wert, Herkunft, Prüfdatum; mehrere Identitäten je Host. |
| ProfileVersion | id, profile_id, Version, Werte, Module, Schema, Zielmatrix, Status, Digest. |
| ModuleVersion | id, module_id, Version, Artefaktdigest, check/apply/verify Vertrag, Timeout, Wiederholungsregel, Freigabenachweis. |
| InstallApproval | host_id, freigegebene ProfileVersion, Gültigkeitsfenster, Genehmiger, Neuinstallationsgrund, Verbrauchsstatus. |
| ProvisioningRun | id, host_id, approval_id, Zustand, Antwortdigest, Manifestdigest, fixierte Parameter, Beginn, Ende, Versionszähler. |
| RunStep | run_id, step_id, Position, Modulversion, Versuch, Zustand, Checkpoint, Exitcode, Prüfergebnis. |
| Credential | id, Typ, Scope, Verifizierer oder Public Key, Fristen, Sperre, Lauf oder Gruppe. |
| Artifact und Secret | Digest, Größe, Ablagepfad, Typ; Geheimnisse separat verschlüsselt mit Schlüsselreferenz. Keine Secrets im Artefaktnamen. |
| Event und LogChunk | run_id, sequence, Zeit, Gerät, Nutzdaten beziehungsweise begrenzter Logblock; zusammengesetzter eindeutiger Schlüssel. |
| AuditEvent | Akteur, Aktion, Objekt, Zeitpunkt, Änderungsgrund und redigierte Vorher Nachher Werte. |
| IsoRecord | ISO Digests, Assistant Version, Zielbuild, URL, Fingerprint, Token, Teststatus. |

### Transaktionsregeln

Freigabeprüfung, Reservierung eines Laufs und Verbrauch der Freigabe erfolgen in einer Transaktion. Ein eindeutiger Index verhindert mehrere aktive Installationsläufe je Host. Wiederholte POST Requests liefern innerhalb des zugelassenen Fensters dieselbe Antwort und vergeben weder neue IP Adressen noch neue Hostnamen.

Zustandsübergänge prüfen die gespeicherte Versionsnummer. Ereignisse werden dedupliziert gespeichert, bevor die API sie quittiert. Ein Lauf referenziert nur bereits vollständig geschriebene Artefakte. Die Veröffentlichung erfolgt durch atomare Dateiumbenennung und anschließende Datenbankreferenz; verwaiste Dateien dürfen später bereinigt werden.

### SQLite im Eincontainerbetrieb

SQLite wird auf einem lokalen Docker Volume im WAL Modus betrieben. WAL erlaubt parallele Leser und einen schreibenden Vorgang; die Datenbank gehört nicht auf ein Netzwerkdateisystem. [5] Transaktionen bleiben kurz, Logdaten werden gebündelt und Schreibkonflikte kontrolliert wiederholt. Zunächst läuft ein API Prozess mit begrenzter Hintergrundarbeit.

Für mehrere aktive Anwendungsinstanzen oder höhere Schreiblast ist eine Migration auf PostgreSQL vorgesehen. Das ist eine Änderung des Betriebsmodells und erfordert zusätzliche Infrastruktur. Eine zweite Instanz darf nicht einfach dasselbe SQLite Volume parallel verwenden.

## 11 Docker Betrieb und Wiederherstellung

### Containervertrag

Das Image enthält Weboberfläche, API, Migrationswerkzeug und feste Runner Versionen. Es läuft als nicht privilegierter Benutzer ohne Hostnetz, ohne Docker Socket und ohne Zugriff auf Hostgeräte. Das Root Dateisystem ist schreibgeschützt; nur Datenvolume und temporäre Verzeichnisse sind beschreibbar. Docker Volumes entkoppeln persistente Daten vom Containerlebenszyklus. [6]

```yaml
# Zielkonfiguration; Image und Dateien entstehen in der Umsetzung
services:
  provisioner:
    image: ${PROVISIONER_IMAGE}
    user: "10001:10001"
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    ports: ["127.0.0.1:8080:8080"]
    volumes:
      - provisioner-data:/data
      - ./config:/config:ro
      - ./secrets:/run/secrets:ro
    tmpfs: ["/tmp:rw,noexec,nosuid,size=128m"]
    environment:
      APP_CONFIG: /config/app.toml
      DATA_DIR: /data
      MASTER_KEY_FILE: /run/secrets/master.key
volumes:
  provisioner-data:
```

Dieses Beispiel setzt einen TLS Proxy auf demselben Docker Host voraus. Bei direkter TLS Terminierung werden Zertifikat und Schlüssel eingebunden und der TLS Port passend veröffentlicht. Image Digest, Dateirechte und Eigentümer des Volumes werden im Installationspaket festgelegt. Das Beispiel ist keine bereits verfügbare Software.

### Betriebsziele und Beobachtung

Startdimensionierung: 2 vCPU, 2 GB RAM und 20 GB Datenkapazität ohne ISO Archiv; unter Last zu überprüfen. /health/live prüft den Prozess, /health/ready Datenbank und notwendige Konfiguration. Gemessen werden API Fehler, Antwortzeiten, aktive Läufe, Alter des letzten Heartbeats, Speicherplatz und Backupalter. Secrets und Logtexte werden nicht zu Metriklabels.

Als Startwerte gelten 30 Sekunden Heartbeat, 3 Minuten bis Kontakt unbekannt, 30 Tage technische Logs und 180 Tage Auditdaten. Fristen sind konfigurierbar. Ein fertiger Host darf nicht allein wegen Ablaufs einer Logaufbewahrung erneut freigegeben werden.

### Sicherung und Updates

Tägliche konsistente Sicherung über die SQLite Backup API oder einen kontrollierten Anwendungsstopp; keine isolierte Kopie nur der laufenden DB Datei. Artefakte, Konfiguration und verschlüsselte Geheimnisse gehören zur Sicherung. Der Entschlüsselungsschlüssel wird separat geschützt gesichert. Planungsziel: RPO 24 Stunden, RTO 2 Stunden.

Updates stoppen neue Freigaben, sichern den Datenstand und führen versionierte Migrationen aus. Wiederherstellung und Kompatibilität mit bereits gestarteten Runnern werden vor Produktionsfreigabe geprüft. Nach Restore werden unsichere aktive Berechtigungen gesperrt und Läufe abgeglichen, damit zurückgesetzte Zustände keine Aktion erneut auslösen.

## 12 Netzwerk und Datenträgersicherheit

### Erreichbarkeit in zwei Umgebungen

Der Installer benötigt sein eigenes erreichbares Startnetz, bevor er die Antwortdatei abrufen kann. Die darin enthaltene spätere Management IP löst dieses Problem nicht. Standard ist ein dediziertes Provisionierungs VLAN mit DHCP, DNS und korrekter Route zum Tool. Nach dem Neustart muss das Zielnetz ebenfalls HTTPS zum Tool und zu den benötigten Paketquellen erlauben.

| Verbindung | Zweck und Freigabe |
| --- | --- |
| Browser zum Tool | HTTPS aus dem Verwaltungsnetz; rollenbasierte Anmeldung. |
| Installer zum Tool | HTTPS für Antwort, Starthelfer und optionales Report Webhook; maschinelle Authentifizierung. |
| Installierter Host zum Tool | HTTPS für Enrollment, Laufberechtigung, Artefakte und Status. |
| Installer und Host zur Infrastruktur | DNS, DHCP im Startnetz, Zeitdienst und freigegebene Paketquellen gemäß Standortkonzept. |
| Tool zu externen Diensten | Nur bewusst eingerichtete Ziele, etwa Sicherungsziel oder spätere Identitätsanbieter. |

Ein erfolgreicher network-online.target garantiert nicht die Erreichbarkeit eines bestimmten Dienstes. Der Runner testet Namensauflösung, TLS Verbindung und API Antwort ausdrücklich. Wechsel von IP, Bridge, Bond oder VLAN wird in der ersten Version möglichst vollständig in der Installationskonfiguration abgebildet.

### Auswahl der Systemdatenträger

Datenträger werden anhand eines geprüften Hardwareprofils und stabiler, vom freigegebenen Installer unterstützter Eigenschaften ausgewählt. Pauschale Auswahl des ersten Geräts oder eine feste Annahme über /dev/sda ist unzulässig. Anzahl und erwartete Eigenschaften der Systemdatenträger müssen zum freigegebenen Profil passen.

Die Systeminformationen des Antwortabrufs werden nicht pauschal als vollständiges Storageinventar behandelt. Fehlen Datenträgermerkmale im nativen Payload, ist vor der Freigabe eine separate Inventarisierung beziehungsweise ein geprüfter Hardwaretyp erforderlich. Ein Postinstallationsskript kann einen bereits überschriebenen falschen Datenträger nicht nachträglich schützen.

### Risikoreiche Erweiterungen

Netzwerkänderungen nach dem ersten Start benötigen einen separaten Rückfallmechanismus mit lokaler Konfigurationssicherung, Zeitfenster und Out of Band Zugang. Vollständige Root Storage Änderungen sind Neuinstallationsvorgänge. Zusätzliche destruktive Storage Module dürfen nur ausdrücklich ausgewählte Geräte verwenden und verlangen eine eigene Freigabe.

Clusterbeitritt und Ceph werden später als koordinierte Gruppenabläufe entwickelt. Dazu gehören versionsbezogene Kompatibilitätsprüfungen, Quorum, serielle Operationen und eigene Geheimnisverwaltung. Sie werden nicht als beliebige zusätzliche Bash Zeile in ein allgemeines Basisskript aufgenommen.

## 13 Abnahmekriterien und Teststrategie

Diese Prüfaufträge definieren die Fertigstellung; Installationen wurden noch nicht ausgeführt. Unit Tests prüfen Regeln und Zustände, Vertragstests echte Installer Payloads und Integrationstests Persistenz und Fehlerfälle. Vollständige Installationen werden zunächst virtuell, dann auf einem physischen Gerät je Hardwareprofil geprüft. Ein Bash Syntaxcheck allein genügt nicht.

| Nr | Testfall | Erwartetes Ergebnis |
| --- | --- | --- |
| A01 | Bekannter und freigegebener Host | Basisinstallation und alle Pflichtmodule laufen mit fixierten Versionen bis succeeded durch. |
| A02 | Unbekannter oder gesperrter Host | Keine verwendbare Antwortdatei; keine Freigabe destruktiver Installation durch das Tool. |
| A03 | Doppelte oder widersprüchliche Identität | Zuordnung abgelehnt, Konflikt sichtbar, keine automatische Auswahl eines Hosts. |
| A04 | Parallele und wiederholte Antwortabrufe | Ein Lauf und dieselbe Konfiguration; keine doppelte Vergabe von IP oder Hostname. |
| A05 | Falsches oder abgelaufenes Zertifikat | Abruf scheitert geschlossen; kein Ausweichen auf unverschlüsselten oder ungeprüften Zugriff. |
| A06 | Falscher, gesperrter oder fremder Token | 401 beziehungsweise 403; kein Zugriff auf fremde Antwort, Artefakte oder Logs. |
| A07 | Manipuliertes Skriptpaket | Digestprüfung schlägt fehl; keine Ausführung und nachvollziehbarer Fehler. |
| A08 | Verlust der Enrollment Antwort | Wiederholung mit demselben Schlüssel funktioniert; anderer Schlüssel bleibt gesperrt. |
| A09 | Netzausfall und Containerneustart | Runner puffert Ereignisse; Zustand bleibt erhalten; keine unkontrollierte Modulwiederholung. |
| A10 | Stromausfall während eines Schritts | Istzustand wird geprüft; sichere Wiederaufnahme oder needs_review, keine falsche Erfolgsmeldung. |
| A11 | Geplanter Neustart | Rückkehr in denselben Lauf; abgeschlossene Schritte werden nicht erneut angewendet. |
| A12 | Erneutes Booten der Installations ISO | Nach Sperre beziehungsweise Abschluss keine neue Antwort ohne neue ausdrückliche Freigabe. |
| A13 | Gleichzeitige Profiländerung | Aktiver Lauf behält Manifest und Versionen; neuer Lauf nutzt erst die neue Veröffentlichung. |
| A14 | Zehn parallele Läufe | Keine Zuordnungsfehler oder verlorenen Ereignisse; p95 Antwortzeit der Steuer API unter 2 s im definierten Testnetz. |
| A15 | Backup und Restore | Vollständige Wiederherstellung einschließlich Schlüssel; RPO und RTO nachgewiesen; aktive Läufe sicher abgeglichen. |
| A16 | Jeder freigegebene ISO Build | Antwortschema, Token, First Boot, Startnetz, UEFI und gegebenenfalls Secure Boot praktisch geprüft. |

## 14 Umsetzung und offene Festlegungen

### Umsetzung in fünf Arbeitspaketen

| Paket | Ergebnis | Abschlussbedingung |
| --- | --- | --- |
| 1 Technischer Nachweis | Ein Ziel ISO Build, Antwortendpoint, sicherer Starthelfer und ein Testmodul | Installation bis zu einer verifizierten Rückmeldung demonstriert |
| 2 Fachlicher Kern | Inventar, Profile, Modulversionen, Freigaben und Datenmodell | Zuordnung und Konfigurationsauflösung mit Fehlerfällen geprüft |
| 3 Robuste Ausführung | Runner, Enrollment, Checkpoints, Reboots, Ereignisse und Statusanzeige | Unterbrechungs und Wiederaufnahmetests bestehen |
| 4 Betriebsreife | Rollen, Audit, Geheimnisse, Containerpaket, Backup und Upgradepfad | Sicherheits und Wiederherstellungskriterien erfüllt |
| 5 Pilotbetrieb | Test auf realer Zielhardware, Betriebsanleitung und Supportmatrix | Alle zutreffenden Abnahmekriterien erfüllt und Betreiberfreigabe erteilt |

Liefergegenstände der Umsetzung sind Quellcode, versioniertes Containerimage, Compose Beispiel, Datenbankschema mit Migrationen, OpenAPI Spezifikation, Starthelfer und Runner, Basismodule, ISO Vorbereitungsanleitung, getestete Kompatibilitätsmatrix sowie Betriebs und Wiederherstellungsanleitung. Eine belastbare Aufwandsschätzung folgt nach dem technischen Nachweis und der Festlegung der Module.

### Vor Entwicklungsbeginn festzulegen

| Entscheidung | Vorgesehener Ausgangspunkt |
| --- | --- |
| Proxmox Zielversionen | Ein konkret getesteter aktueller ISO Build zum Start; weitere Builds nur mit Nachweis. |
| Start und Zielnetz | Dediziertes DHCP Provisionierungsnetz, erreichbare feste HTTPS Adresse des Tools. |
| Hardware und Storage | Freigegebene Servertypen; je Typ explizite Systemdatenträger und Dateisystemkonfiguration. |
| Identitätsniveau | Gruppen ISO im kontrollierten Netz; für höhere Anforderungen individuelles Medium oder unabhängiges Gerätegeheimnis. |
| Authentifizierung der Benutzer | Lokale Konten und Rollen zum Start; OIDC als optionale Erweiterung. |
| Konkrete Skriptmodule | Repositories, Pakete, SSH, Zeit, Monitoring und Abschlussprüfung; genaue Produkte und Werte festlegen. |
| TLS und Aufbewahrung | Vorhandenen Proxy nutzen, sofern verfügbar; Fristen und Backupziele an Betriebsvorgaben anpassen. |

### Abgleich mit vorhandenen Proxmox Funktionen

Proxmox Datacenter Manager dokumentiert inzwischen vorbereitete Antworten, Zielfilter, Installationsübersicht und eigene Installationstokens. [3] Falls diese Lösung bereits vorhanden ist, sollte vor Eigenentwicklung geprüft werden, welche Funktionen sie abdeckt. Der in diesem Konzept ausgearbeitete Eigenbau erfüllt die Docker Vorgabe und legt zusätzlich die hostbezogene Postinstallation mit Checkpoints und Laufsteuerung fest.

## 15 Quellen und technische Nachweise

Die Quellen belegen die verwendeten Produktmechanismen. Datenmodell, eigene API Pfade, Rollen, Fristen, Betriebsziele und Wiederaufnahmelogik sind Festlegungen dieses Feinkonzepts. Vor einer Implementierungsfreigabe werden die Mechanismen mit den tatsächlich eingesetzten ISO und Paketversionen geprüft.

[\[1\] Proxmox VE Wiki Automated Installation](https://pve.proxmox.com/wiki/Automated_Installation)

Dokumentierter Antwortabruf, Authentifizierung und First Boot Optionen. Stand des recherchierten Wiki Eintrags: 14. Juli 2026. Referenz für den versionsbezogenen Adapter und die vollständige Antwortdatei.

[\[2\] Proxmox Entwicklerarchiv First Boot Hook Implementierung](https://lists.proxmox.com/pipermail/pve-devel/2024-November/066649.html)

Primärer Implementierungsnachweis vom 18. November 2024: Abruf des First Boot Executables im Installer, Quellenwahl und Ausführungsreihenfolge. Der Beitrag ist ein historischer Patch; die Produktfreigabe stützt sich zusätzlich auf den praktischen Test des gewählten Builds.

[\[3\] Proxmox Datacenter Manager Automated Installations](https://pdm.proxmox.com/docs/automated-installations.html)

Offizielle Dokumentation zu vorbereiteten Antworten, Installationsinformationen, Tokenverwaltung und ISO Vorbereitung. Recherchierte Dokumentationsversion: 1.1.7.

[\[4\] FastAPI Features](https://fastapi.tiangolo.com/features/)

Offizielle Funktionsbeschreibung zu OpenAPI, JSON Schema und API Dokumentation.

[\[5\] SQLite Write Ahead Logging](https://www.sqlite.org/wal.html)

Offizielle Beschreibung des WAL Modus und seiner Einschränkungen, insbesondere bei gemeinsam genutzten Netzwerkdateisystemen und Schreibzugriffen.

[\[6\] Docker Volumes](https://docs.docker.com/engine/storage/volumes/)

Offizielle Beschreibung persistenter Volumes und ihrer Unabhängigkeit vom Containerlebenszyklus.

### Wichtige Nachweise aus dem Pilotbetrieb

Für jede unterstützte Kombination werden ISO Digest, Assistant Paketversion, Installer Payload Muster, validierte Antwortdatei ohne Geheimnisse, First Boot Protokoll, Runner Version und Ergebnis der Abnahmetests archiviert. Diese Nachweise bilden die Supportmatrix und verhindern, dass eine ungetestete Versionsänderung automatisch für produktive Installationen verwendet wird.
