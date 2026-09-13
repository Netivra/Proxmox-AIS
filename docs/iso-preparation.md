# ISO auf einer Build-Maschine vorbereiten

Die ISO wird außerhalb des Webcontainers mit dem offiziellen
`proxmox-auto-install-assistant` erstellt. Der Container benötigt weder
privilegierte Rechte noch Zugriff auf einen Docker-Socket.

1. Original-ISO aus vertrauenswürdiger Proxmox-Quelle beziehen und ihre SHA-256
   gegen den veröffentlichten Wert prüfen. Build und Assistant-Version festhalten.
2. In der Anwendung einen standortbezogenen Gruppentoken erzeugen. Den einmal
   ausgegebenen vollständigen Wert `<gruppenname>:<secret>` sicher übernehmen.
3. Zertifikatsfingerprint des verwendeten TLS-Endpunkts über einen
   vertrauenswürdigen Weg ermitteln. Bei einem Reverse-Proxy dessen Zertifikat verwenden.
4. Verfügbare Optionen prüfen und die ISO erstellen:

```bash
proxmox-auto-install-assistant prepare-iso --help
proxmox-auto-install-assistant prepare-iso SOURCE.iso \
  --fetch-from http \
  --url 'https://provision.example.net/installer/v1/answer' \
  --cert-fingerprint '<SHA256-Zertifikatsfingerprint>' \
  --answer-auth-token '<gruppenname>:<secret>'
```

URL-, Fingerprint- und Token-Optionen entsprechen der
[offiziellen Proxmox-Dokumentation](https://pdm.proxmox.com/docs/automated-installations.html#preparing-an-iso).
Die URL zeigt hier auf den eigenen Antwortdienst. Den Befehl mit echtem Token
nicht in öffentliche Logs oder gemeinsam genutzte Shell-History schreiben.

5. Ergebnis-ISO, Ausgangs-ISO, Assistant-Version, Build, Fingerprint und Gruppe
   in der Medienverwaltung registrieren. Zunächst `draft` verwenden.
6. Den Build anhand der [Abnahmematrix](compatibility.md) im isolierten Labor
   prüfen. Ein neuer Assistant aktualisiert die Komponenten einer alten ISO
   nicht automatisch. Erst nach Prüfung `passed` setzen und Testnachweis referenzieren.

Der Installer sendet Hardwaremerkmale per POST und erhält bei eindeutiger,
freigegebener Zuordnung eine TOML-Antwort. Ein kurzes Wiederholungsfenster liefert
dieselbe Antwort für denselben Lauf. Nach dessen Ende oder Runner-Anmeldung
ist der Abruf gesperrt. Bei abgelehnter Zuordnung die Ursache korrigieren und
den Installationsversuch erneut starten.

Der dynamische First-Boot-Abschnitt verwendet `source = "from-url"` und
`ordering = "network-online"`. Der Installer lädt den Starthelfer, das installierte
System führt ihn beim ersten Boot aus. Diese Trennung beschreibt der
[offizielle First-Boot-Implementierungsentwurf](https://lists.proxmox.com/pipermail/pve-devel/2024-November/066649.html).
Die Gesamtfunktion muss mit der konkreten ISO praktisch geprüft werden.

Ein gemeinsamer ISO-Token ist vom Medium auslesbar, Hardwaremerkmale sind kopierbar.
Zugriff auf Medium und Provisionierungsnetz beschränken. Nach erfolgreicher
Installation die Bootreihenfolge auf das lokale System umstellen; eine bereits
ausgelieferte Antwort kann der Server nicht nachträglich ungültig machen.
