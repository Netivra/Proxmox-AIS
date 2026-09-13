# GitHub Actions und Container Registry

Der [Workflow](../.github/workflows/ci.yml) führt die Python-Tests aus, prüft die
JavaScript-Syntax und baut und testet das Anwendungsimage. Freigegebene Builds
werden in die GitHub Container Registry (GHCR) veröffentlicht. Der Workflow
endet beim Image-Push und aktualisiert keinen Zielserver. Die anschließenden
Betriebsschritte stehen in der [Deployment-Anleitung](deployment.md).

## Auslöser und Image-Tags

Der Workflow läuft bei Pushes auf alle Branches und Tags, bei Pull Requests
und beim manuellen Start mit `workflow_dispatch`.

| Ref und Ereignis | Container-Job | Veröffentlichung |
| --- | --- | --- |
| Pull Request, auch aus einem Fork | `container-verify` | Keine |
| Feature-Branch, ungeschützter Ref oder sonstiger Tag | `container-verify` | Keine |
| Geschützter Standardbranch bei Push oder manuellem Start | `container-publish` | `sha-<vollständiger Commit-SHA>` und `edge` |
| Geschützter Tag `vX.Y.Z` bei Push oder manuellem Start | `container-publish` | `sha-<vollständiger Commit-SHA>` und `X.Y.Z` |

Release-Tags müssen dem Muster `v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)`
vollständig entsprechen. Führende Nullen, Vorabversionen wie `v1.0.0-rc.1` und
Build-Zusätze veröffentlichen daher kein Image. Bei einem gültigen geschützten
Release-Tag muss dessen Version außerdem exakt `[project].version` in
[pyproject.toml](../pyproject.toml) entsprechen; eine Abweichung bricht den Lauf ab.

Das Image liegt für dieses Repository unter `ghcr.io/netivra/proxmox-ais`.
Der Workflow leitet den Pfad aus dem kleingeschriebenen GitHub-Repositorynamen
ab; bei einem Fork oder einer Umbenennung ändert sich deshalb der Imagepfad.
`edge` bezeichnet den zuletzt veröffentlichten Standardbranch-Build. Ein
erneuter Lauf eines älteren Commits kann `edge` zurücksetzen. SHA-Tags enthalten
alle 40 Zeichen des Commits; erneute Builds desselben Quellstands können durch
externe Build-Abhängigkeiten einen anderen Digest ergeben. Es gibt keinen
`latest`-Tag. Im Betrieb den Digest aus `deploy.env` verwenden und die
zugehörigen Registry-Versionen für Updates und Rollback aufbewahren.

## Jobs und Artefakte

`python-tests` und `javascript-check` prüfen den Quellstand. Das Skript
[ci/python-tests.sh](../ci/python-tests.sh) installiert `.[dev]` in einer
eigenen temporären virtuellen Umgebung, führt Pytest aus und entfernt die
Umgebung anschließend. Die JavaScript-Prüfung verwendet
`node --check provisioner/static/app.js`; sie ist kein Browsertest.
`container-policy` bestimmt anhand des GitHub-Ereignisses, des Ref-Schutzes
und des Branch- beziehungsweise Tag-Namens, welcher Container-Job laufen darf.

Nach erfolgreichen Python- und JavaScript-Prüfungen läuft genau einer der
Jobs `container-verify` und `container-publish`. Beide bauen über
[ci/container.sh](../ci/container.sh) ein `linux/amd64`-Image und prüfen es
mit dem [Container-Smoke-Test](../tests/container_smoke.py). Der Publish-Job
lädt genau das bereits getestete Image hoch, ohne einen zweiten Build.
Die Veröffentlichung wird im Skript erneut auf Ereignis und Ref geprüft;
dort erfolgt auch der Abgleich mit der Projektversion.

Publish-Jobs teilen eine Warteschlange mit `queue: max` und laufen einzeln.
Bis zu 100 Jobs können warten; ein laufender Publish-Job wird durch neue
Commits nicht abgebrochen. Die Reihenfolge richtet sich nach dem Eintritt in
die Warteschlange, nicht zwingend nach dem Alter der Commits. Bei Pull Requests
und ungeschützten Refs ersetzt ein neuer Lauf den vorherigen Lauf desselben Refs.
[GitHub-Parallelität und Warteschlangen](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

Der Smoke-Test prüft TLS, Anmeldung, CSRF, ausgelieferte Webdateien und
Datenerhalt über einen Dienstneustart. Er läuft als UID 10001 mit
schreibgeschütztem Dateisystem, entfernten Linux-Capabilities und temporären
Testdaten. Eine Proxmox-Installation ist kein Bestandteil dieses Tests.

Der Build verwendet `--provenance=false --sbom=false` und veröffentlicht
keine Provenance- oder SBOM-Attestierungen. Die OCI-Labels für Quellrepository,
Commit und Anwendungsversion bleiben erhalten.

| Artefakt in GitHub Actions | Inhalt | Aufbewahrung |
| --- | --- | --- |
| `python-test-results` | `reports/pytest.xml` als JUnit-Datei, auch nach fehlgeschlagenen Tests, sofern erzeugt | 7 Tage |
| `container-build` | `build.env` nach erfolgreichem Verify-Job | 7 Tage |
| `container-deploy` | `build.env` und `deploy.env` nach erfolgreichem Publish-Job | 30 Tage |

Die Aufbewahrung wird im Workflow mit `retention-days` gesetzt und ist durch
die übergeordneten GitHub-Einstellungen begrenzt. Die JUnit-Datei wird als
Download bereitgestellt; der Workflow installiert keinen zusätzlichen
Testberichtsdienst. Auf der Übersicht des jeweiligen Laufs unter **Artifacts**
das gewünschte Archiv herunterladen und entpacken.
[Workflow-Artefakte](https://docs.github.com/en/actions/tutorials/store-and-share-data),
[Artefakte herunterladen](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

`build.env` enthält den temporären lokalen Image-Tag, Commit und
Anwendungsversion. Dieser `ci-…`-Tag wird nicht veröffentlicht und beim
Jobende lokal entfernt. `deploy.env` enthält ausschließlich die
Deploymentreferenz `PROVISIONER_IMAGE=ghcr.io/netivra/proxmox-ais@sha256:…`.
Die Dateien enthalten keine Zugangsdaten. `deploy.env` wird auf dem Zielserver
nicht automatisch eingelesen; seine Image-Zeile in die dortige `.env` übernehmen.

## GitHub einrichten

Ein eigener CI-Runner ist nicht erforderlich. Alle Jobs verwenden
GitHub-gehostete Linux-amd64-Runner mit `ubuntu-24.04`. Der Workflow richtet
Python 3.13 über `actions/setup-python` und Node.js 24 über `actions/setup-node`
ein. Docker und Buildx werden vom Runner bereitgestellt und vor dem Build
geprüft. Bash, OpenSSL und der OpenSSH-Client werden dort ebenfalls verwendet.
Diese Build-Umgebung ist
unabhängig vom AIS-Host-Runner für die Postinstallation auf Proxmox-Hosts.
[GitHub-gehostete Runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Im Repository unter **Settings → Actions → General** GitHub Actions aktivieren
und die im Workflow verwendeten Actions von `actions/*` zulassen. Diese sind
im Workflow auf vollständige Commit-SHAs festgelegt. Es werden
keine selbst angelegten Repository-Secrets oder CI-Variablen benötigt.
Der Workflow verwendet standardmäßig `contents: read`; nur
`container-publish` erhält zusätzlich `packages: write` und verwendet das
automatisch bereitgestellte `GITHUB_TOKEN` für die Anmeldung an `ghcr.io`.
[Actions-Einstellungen](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).

GHCR-Pakete sind bei der ersten Veröffentlichung standardmäßig privat. Ein
bereits existierendes Paket muss mit diesem Repository verbunden sein und
dessen Actions Schreibzugriff haben. In den Paketeinstellungen unter
**Manage Actions access** gegebenenfalls `Netivra/Proxmox-AIS` mit Schreibrecht
hinzufügen. Dort auch die gewünschte Paketsichtbarkeit festlegen.
[GHCR und GITHUB_TOKEN](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-in-a-github-actions-workflow),
[Paketzugriff und Sichtbarkeit](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

## Standardbranch und Release-Tags schützen

**Vor der ersten Veröffentlichung müssen aktive Schutzregeln für die
veröffentlichten Refs eingerichtet sein.** Der Workflow und das Skript
verlangen `github.ref_protected == true`. GitHub setzt dieses Merkmal, wenn
Branchschutz oder ein passendes Ruleset für den auslösenden Ref konfiguriert
ist. Ohne Schutz wird nur geprüft.
[GitHub-Ref-Kontext](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context).

Unter den Repository-Einstellungen für Rulesets zwei Regeln anlegen und
jeweils **Enforcement status: Active** wählen:

| Ruleset | Ziel | Vorgesehene Regeln |
| --- | --- | --- |
| Standardbranch | **Include default branch**, etwa `main` | Pull Request und Review verlangen; Löschen und Force-Push verhindern; erfolgreiche Statuschecks verlangen |
| Releases | Tags mit dem Muster `v*` | Erstellung, Aktualisierung und Löschung beschränken; nur Release-Verantwortliche in die Bypass-Liste aufnehmen |

Für Pull Requests die Statuschecks `python-tests`, `javascript-check`,
`container-policy` und `container-verify` nach ihrem ersten Lauf als
verpflichtend auswählen. `container-publish` ist bei Pull Requests absichtlich
übersprungen und wird deshalb nicht als Pflichtprüfung eingerichtet.
Das Tag-Ruleset schützt alle `v*`-Tags; die engere Versionsprüfung übernimmt
zusätzlich das CI-Skript. Release-Verantwortliche setzen Tags auf einen
geprüften Commit mit der passenden Projektversion. Ein Tag auf einem Commit
des geschützten Standardbranches ist selbst noch kein geschützter Tag.
[Rulesets anlegen](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository),
[Verfügbare Schutzregeln](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

Die Regeln und Paketberechtigungen im GitHub-Projekt sind Teil der Einrichtung;
sie werden durch den Commit der Workflow-Datei nicht automatisch angelegt.
Welche Rulesets verfügbar sind, hängt von Repository-Sichtbarkeit und
GitHub-Tarif ab. Die CI-Abfragen ersetzen keine restriktiven Schreibrechte
auf Repository und Paket; Änderungen an Workflow und CI-Skripten im Review prüfen.
[Ruleset-Verfügbarkeit](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository).

## Erster Lauf und Release

Nach dem Push der Workflow-Datei unter **Actions** den Lauf öffnen.
Python- und JavaScript-Prüfung sowie der passende Container-Job müssen
erfolgreich sein. Für einen geschützten Standardbranch zusätzlich prüfen,
dass das GHCR-Paket `sha-<Commit>` und `edge` enthält und das Artefakt
`container-deploy` mit `deploy.env` bereitsteht. Ein lokaler Test ersetzt
diesen ersten GitHub-Lauf samt GHCR-Anmeldung und Push nicht.

Für ein Release zuerst `[project].version` auf die gewünschte Version setzen
und den geprüften Commit übernehmen. Anschließend durch ein für das
Release-Ruleset berechtigtes Konto den passenden Tag erstellen und pushen.
Im vorhandenen Checkout heißt der GitHub-Remote `GH-AIS`; in anderen Checkouts
den Namen mit `git remote -v` prüfen und im Befehl entsprechend ersetzen.
Für Projektversion `0.1.0`, sofern der Tag noch nicht existiert:

```bash
git tag -a v0.1.0 -m "Release 0.1.0"
git push GH-AIS v0.1.0
```

Der Release-Lauf muss zusätzlich den Image-Tag `0.1.0` erzeugen. Für einen
manuellen Branch-Lauf unter **Actions** den Workflow und **Run workflow**
wählen. Dafür muss die Workflow-Datei bereits auf dem Standardbranch liegen.
Ein manueller Lauf veröffentlicht nur, wenn dieselben Ref-Schutz- und
Versionsbedingungen erfüllt sind.
[Workflow manuell starten](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Branch-Push und Pull Request können für denselben Quellstand getrennte Läufe
erzeugen. Pull Requests werden im Merge-Kontext geprüft; ihre Tests führen
keinen Registry-Push aus. Die Jobs laden keine produktiven Deployment-Zugänge.

## Fehlersuche und Deployment

Bei übersprungenem Publish-Job zuerst Ereignis, Standardbranch, Tagformat und
aktive Schutzregeln prüfen. Bei einer Versionsabweichung müssen Git-Tag und
`pyproject.toml` aufeinander abgestimmt werden. Bei `denied` oder `unauthorized`
im Publish-Job die Organisationsrichtlinien, `packages: write` und den
Actions-Zugriff auf das GHCR-Paket kontrollieren. Fehlt `container-deploy`,
die Logs des Build-, Smoke- und Push-Schritts prüfen; vor erfolgreicher
Veröffentlichung steht kein Deployment-Digest bereit.

Zum Deployment [compose.yaml](../compose.yaml) und
[config/deployment.env.example](../config/deployment.env.example) auf den
Zielserver kopieren und die Image-Zeile aus `deploy.env` verwenden.
Die [Deployment-Anleitung](deployment.md) beschreibt GHCR-Anmeldung,
Erstinitialisierung, HTTPS-Prüfung, Updates und Rollback. Für private Pakete
benötigt das Deploymentkonto einen Personal Access Token (classic) mit
`read:packages`; öffentliche Pakete lassen sich anonym laden.
