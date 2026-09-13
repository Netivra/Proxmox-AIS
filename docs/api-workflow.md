# API-Ablauf im Labor

Die API verwendet Sessioncookies und für alle Verwaltungsänderungen
`X-CSRF-Token`. Die Beispiele benötigen Bash, curl und jq. Für PowerShell steht
die gleiche API über `Invoke-RestMethod` zur Verfügung. Das vollständige Schema
ist nach Anmeldung unter `/openapi.json` und als generierter Repositorystand in
[openapi.json](openapi.json) verfügbar.

## Anmelden und Daten lesen

```bash
export AIS_URL=https://provision.example.net
umask 077
read -rsp 'Administrator-Passwort: ' AIS_PASSWORD
printf '\n'
printf '%s' "$AIS_PASSWORD" | curl --fail --silent --show-error \
  --cookie-jar session.cookies --data-urlencode username=admin \
  --data-urlencode password@- "$AIS_URL/auth/login" --output /dev/null
unset AIS_PASSWORD
AIS_CSRF=$(curl --fail --silent --show-error --cookie session.cookies \
  "$AIS_URL/api/v1/me" | jq -r .csrf_token)
curl --fail --silent --show-error --cookie session.cookies "$AIS_URL/api/v1/hosts"
```

Cookies wie Zugangsdaten behandeln und nach Ende der Sitzung löschen.
Der Server liefert bei nicht angemeldeten API-Aufrufen HTTP 401 und bei
unzulässigen Rollen oder fehlendem CSRF-Token HTTP 403.

## Profilversion anlegen

Zuerst einen Root-Passwort-Hash als Geheimnis in der Weboberfläche speichern.
Unter Linux kann ein SHA-512-crypt-Hash etwa mit `openssl passwd -6` interaktiv
erzeugt werden. Den Hash nicht als Klartextfeld in ein Profil aufnehmen.

[sample-profile.json](sample-profile.json) kopieren und alle Platzhalter anpassen:
Secret-ID, konkret geprüfter Build, Management-Interface, Datenträgerseriennummer
und Inventarisierungsnachweis. `9.1-1` dient lediglich als Formatbeispiel und ist
keine Kompatibilitätsfreigabe. FQDN und Management-IP kommen aus dem Hostinventar.

```bash
curl --fail --silent --show-error --cookie session.cookies \
  --header "X-CSRF-Token: $AIS_CSRF" \
  --json @my-installation-profile.json "$AIS_URL/api/v1/profiles"
```

Die Antwort enthält `id`, `version`, `digest` und `status=draft`. Ein weiterer
POST mit demselben Namen und Typ erstellt eine neue unveränderliche Version.
Bereits vorbereitete Läufe behalten die zuvor aufgelösten Werte und Versionen.

Veröffentlichung erfolgt in einer Sitzung einer anderen berechtigten Person:

```bash
curl --fail --silent --show-error --cookie reviewer.cookies \
  --header "X-CSRF-Token: $REVIEWER_CSRF" \
  --json '{"test_evidence":"lab-report-2026-09-13","reason":"Labortest erfolgreich geprüft"}' \
  "$AIS_URL/api/v1/profiles/$PROFILE_ID/publish"
```

Module werden analog über `POST /api/v1/modules` und
`POST /api/v1/modules/{id}/publish` versioniert und veröffentlicht. Ausgangsvorlagen
stehen unter `GET /api/v1/modules/builtin`. Sie enthalten keinen ausgefüllten
Testnachweis und benötigen Anpassung sowie Prüfung auf dem Zielhost.

Ein Postinstallationsprofil verweist auf unveränderliche Modul-IDs:

```json
{
  "name": "lab-postinstall",
  "kind": "postinstall",
  "target_builds": ["9.1-1"],
  "steps": [
    {
      "id": "final-check",
      "module_id": "REPLACE_WITH_PUBLISHED_MODULE_ID",
      "parameters": {},
      "required": true
    }
  ],
  "reboot_budget": 1
}
```

Modulabhängigkeiten müssen bereits in vorherigen Schritten enthalten sein.
Mindestens eine Pflichtprüfung wird verlangt. Modulparameter werden gegen das
JSON-Schema der gewählten Modulversion validiert.

## Host prüfen und Installation freigeben

Gruppentoken, geprüftes Medium und beide veröffentlichten Profile müssen bereits
existieren. Der Host verweist über `installation_profile_id`,
`postinstall_profile_id` und `iso_id` auf diese Objekte.

```bash
curl --fail --silent --show-error --cookie session.cookies \
  "$AIS_URL/api/v1/hosts/$HOST_ID/preview"
curl --fail --silent --show-error --cookie session.cookies \
  --header "X-CSRF-Token: $AIS_CSRF" \
  --json '{"expected_version":1,"valid_minutes":30,"confirmation":"pve01.lab.example.net","disks_confirmed":true,"reason":"Freigegebener dedizierter Labortest"}' \
  "$AIS_URL/api/v1/hosts/$HOST_ID/approve-install"
```

Vor dem zweiten Aufruf tatsächlichen FQDN, aktuelle Hostversion und dargestellte
Zielgeräte prüfen. Die Aktion erzeugt einen vorbereiteten Lauf und bindet die
Konfiguration. Ein zweiter gleichzeitiger aktiver Lauf für denselben Host ist gesperrt.

Der Proxmox-Installer sendet seinen nativen Request selbst. Für einen isolierten
API-Test zeigt dieses Beispiel die verwendete Payload-Struktur:

```json
{
  "$schema": {"version": "1.0"},
  "product": {"product": "pve"},
  "iso": {"release": "9.1", "build": "1"},
  "dmi": {
    "system": {
      "uuid": "d2e59b03-13cf-4ac9-a390-78c55f6a36d3",
      "serial": "LAB-HOST-001"
    }
  },
  "network-interfaces": [{"mac": "02:00:00:00:00:01"}]
}
```

Der echte Installer darf weitere Hardwarefelder senden. Registrierung und
Antwortschema stammen aus den
[offiziellen Installer-Typen](https://github.com/proxmox/proxmox-rs/blob/master/proxmox-installer-types/src/answer.rs).
Ein unbekannter Host erhält keine TOML-Antwort. Der Gruppentoken muss zum Standort
und zum freigegebenen Medium des Hosts passen.

```bash
curl --fail --silent --show-error \
  --header "Authorization: Bearer $INSTALLER_GROUP_TOKEN" \
  --json @installer-info.json "$AIS_URL/installer/v1/answer" \
  --output answer.toml
```

Dieser Abruf verbraucht eine Freigabe. `answer.toml` enthält den Root-Hash und
runbezogene Downloadberechtigungen; wie Zugangsdaten schützen. Innerhalb des
kurzen Auslieferungsfensters liefern Wiederholungen dieselbe Antwort.

## Runner und Status

Der Starthelfer richtet den Runner automatisch ein. Das Gerät erzeugt einen
eigenen Ed25519-Schlüssel, registriert dessen öffentlichen Anteil und signiert
weitere Requests einschließlich Methode, Pfad, Zeitstempel, Nonce und Body-Digest.
Ein gewöhnlicher curl-Aufruf mit Installer-Gruppentoken berechtigt nicht zum
Manifest-, Artefakt- oder Secret-Abruf.

```bash
curl --fail --silent --show-error --cookie session.cookies \
  "$AIS_URL/api/v1/runs/$RUN_ID"
```

Die Laufansicht enthält Schrittzustände, redigierte Logs und die fixierte
Konfiguration. Fortsetzung und Abbruch benötigen `expected_version` und einen
Begründungstext über `/api/v1/runs/{id}/resume` bzw. `/cancel`. Eine Fortsetzung
erst nach Prüfung des lokalen Zustands anfordern. Ein bereits laufender
Paketmanager wird beim Abbruch am nächsten sicheren Übergang angehalten.

Für einen dauerhaft verlassenen Lauf steht `/api/v1/runs/{id}/reconcile` zur
Verfügung. Zuerst den Abbruch anfordern, ausgestellte Leases und das
Antwortauslieferungsfenster ablaufen lassen sowie Installer und Runner lokal
stoppen und prüfen. Danach `expected_version`, `reason`, `confirmation` mit dem
Host-FQDN und `execution_stopped: true` senden. Diese gesonderte Bestätigung beendet
den Lauf als `cancelled`; eine neue Installation benötigt anschließend eine
eigene Freigabe. Der Ablauf ist auch für `needs_review` nach Restore vorgesehen.
