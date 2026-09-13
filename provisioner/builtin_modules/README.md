# Mitgelieferte Modulentwürfe

Alle acht Module werden als **Entwurf ohne Hardware-Testnachweis und ohne freigegebene Zielbuilds** angeboten. Vor Veröffentlichung müssen Quelltext, konkrete Parameter und Verhalten auf einem passenden Testhost geprüft werden. Die Syntaxprüfung ersetzt diesen Nachweis nicht.

| Modul | Parameter und Umfang |
| --- | --- |
| Voraussetzungen | `allowed_versions`: exakte Versionsstrings aus `pveversion`; `dns_names`: aufzulösende Namen; `minimum_free_mb`: freier Platz auf `/`. Eine leere Versionsliste lässt die zusätzliche Versionsprüfung aus; die Buildfreigabe im Dienst bleibt erforderlich. |
| Paketquellen | `url`, `suite`, `components`, `keyring` sind zwingend. Verwaltet genau `/etc/apt/sources.list.d/pve-provisioner.sources` mit HTTPS und vorhandenem APT-Schlüsselbund. Andere Quellen, insbesondere Subscription-Konfigurationen, werden nicht automatisch entfernt. |
| Basispakete | `packages`: Debian-Paketnamen ohne Shell-Ausdrücke oder APT-Optionen. Installiert fehlende Pakete, wartet auf die Paketmanagersperre und prüft anschließend den Installationsstatus. Führt kein allgemeines Systemupgrade aus. |
| SSH-Zugang | `users`: Liste aus `name` und `authorized_keys`. Benutzer müssen bereits existieren und eine Login-Shell sowie ein sicher berechtigtes Home-Verzeichnis haben. OpenSSH validiert vollständige Schlüssel vor jeder Änderung. Schlüssel werden ergänzt und Dateirechte, effektive lokale SSH-Schlüsselrichtlinie, `sshd -t` sowie der aktive Dienst geprüft. Verbindung und gegebenenfalls abweichende `Match`-Regeln aus dem realen Managementnetz sind separat zu testen. |
| Zeitsynchronisation | `servers`: explizite NTP-Hostnamen oder IP-Adressen. `chrony` muss zuvor installiert sein und `sourcedir /etc/chrony/sources.d` verwenden. Verwaltet eine eigene Quelldatei und wartet begrenzt auf Synchronisation. |
| Monitoring | `enabled`: standardmäßig `false`. Bei Aktivierung muss `prometheus-node-exporter` bereits installiert sein. Aktiviert den Dienst und prüft dessen lokalen Metrics-Endpunkt. Netzwerkzugriff auf den Exporter muss im Standortnetz passend geregelt sein. |
| Zusätzlicher Storage | `id`, `path`, `content`: registriert ein bereits existierendes Verzeichnis unter `/mnt/` oder `/srv/` als PVE-Verzeichnisstorage. Kein Formatieren, kein Mounten, keine Änderung widersprüchlicher vorhandener Storage-Konfiguration. |
| Abschlussprüfung | `allowed_versions`, `dns_names`, `storage_ids`, `require_time_sync`: prüft PVE-Dienste, Version, DNS, Zeit und angegebenen aktiven Storage. |

Modulabhängigkeiten beziehen sich im Verwaltungsmodell auf **Modulnamen**. Beim Freigeben werden sie auf die konkreten Schritt-IDs des unveränderlichen Laufmanifests aufgelöst. Profilparameter werden gegen das jeweilige JSON-Schema geprüft. Beispielsweise muss das Paketprofil `chrony` enthalten, wenn der Zeitschritt auf einem Host ohne Chrony eingeplant wird.

## Modulvertrag

Der Runner startet `bash modul.sh check|apply|verify parameter.json`. `check` liefert `0`, wenn der Sollzustand erreicht ist, `1`, wenn eine Änderung nötig ist, und einen anderen Rückgabecode für einen Prüffehler. Ein Schritt gilt erst nach erfolgreichem `verify` als erfolgreich. `apply` darf mit `194` einen geplanten Neustart anfordern; der Runner schreibt zuerst seinen Checkpoint und kontrolliert das Neustartbudget. Ein Modul darf den Neustart nicht selbst auslösen.

Die Parameterdatei enthält die freigegebenen Parameter sowie ein Objekt `secrets` mit ausschließlich den Geheimnissen des aktuellen Schritts. Sie wird mit Modus `0600` angelegt und nach dem Schritt entfernt. Module sollen keine Geheimnisse ausgeben; zusätzlich redigiert der Runner bekannte Geheimniswerte vor der dauerhaften Logablage. Logausgabe ist pro Phase und in der lokalen Warteschlange begrenzt.

Das Schritt-Timeout gilt gemeinsam für `check`, `apply` und `verify`. Nach Unterbrechungen werden `check` und `verify` erneut ausgeführt; ein nicht bestätigter Zustand darf nur bei `retry_safe=true` erneut angewendet werden. Ein permanenter Fehler wartet auf eine explizite Wiederaufnahme im Webtool. Die standardmäßige Wartefrist beträgt 24 Stunden, die maximale automatische Wiederherstellung bei Netzausfall 30 Minuten.

Chrony-Kommandos orientieren sich an der offiziellen Dokumentation zu [chronyc](https://chrony-project.org/doc/4.7/chronyc.html) und [sourcedir](https://chrony-project.org/doc/4.7/chrony.conf.html). Die tatsächliche Paketversion und Distribution bleiben Bestandteil des Zielhost-Tests.
