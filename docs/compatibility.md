# Kompatibilität und Abnahme

Es gibt zum Auslieferungszeitpunkt **keinen auf Hardware oder in einer VM
freigegebenen Proxmox-ISO-Build**. Die Registrierung `test_status=passed` ist eine
vom Administrator dokumentierte Freigabe; sie ersetzt keinen tatsächlichen Test.

| Kombination | Status | Nachweis |
| --- | --- | --- |
| API/SQLite/Sicherheitslogik, Python 3.14 unter Windows | Automatisierte lokale Softwaretests | `python -m pytest` |
| Python 3.13 im bereitgestellten Dockerfile | Image gebaut; Tests, TLS-Start und Dienstneustart bestanden | Ergebnisse im [Testbericht](test-report.md) |
| Python 3.12+ | Deklarierte Python-Untergrenze; keine vollständige CI-Matrix | Auf gewählter Version testen |
| Proxmox VE ISO mit nativem Answer-Token und First-Boot-URL | Pro konkretem ISO-Build zu testen | Noch offen |
| UEFI, BIOS und Secure Boot | Je verwendetem Bootmodus zu testen | Noch offen |
| Netzunterbrechung/Neustart auf echtem Proxmox-Host | Runner-Softwaretests plus erforderlicher Integrationstest | Hardware-/VM-Test offen |
| 100 Hosts, zehn gleichzeitige Installationen | Planungsziel | Lasttest offen |
| RPO 24 Stunden / RTO zwei Stunden | Planungsziel | Betrieblicher Restore-Test offen |

Ein ISO ohne native Unterstützung für `--answer-auth-token` wird nicht freigegeben.
Die Assistant-Version allein belegt nicht die Funktionen der Komponenten in einer
älteren ISO. Alle Beispieldaten sind Platzhalter und stellen keine Freigabe dar.

## Freigabe eines Builds

Für jeden Build ein eigenes Prüfprotokoll außerhalb der Anwendung archivieren:

- Original-ISO und SHA-256, Assistant-Paketversion, Build-Befehl, Ergebnisdatei
  und Prüfsumme; Tokens im Protokoll redigieren.
- Installer-Request als redigiertes JSON; UUID-, Seriennummer- und MAC-Zuordnung
  sowie Ablehnung unbekannter und widersprüchlicher Identitäten.
- Antwortdatei ohne Geheimnisse, Prüfung mit dem zugehörigen Assistant, korrekte
  Netzwerkwerte und Auswahl ausschließlich der vorgesehenen Testdatenträger.
- Bearer-Header, TLS-Verifikation, First-Boot-Download, Zeitpunkt des ersten
  Starts und Vertrauen in die Server-CA.
- Modulabläufe `check`, `apply`, `verify`, Verlust einer Serverantwort,
  Netzausfall, erneuter Start und kontrollierter Reboot. Unterbrochene nicht sichere
  Änderungen müssen einen prüfbedürftigen Zustand ergeben.
- Abschluss nur nach erfolgreichen Pflichtprüfungen; danach keine erneute
  Installationsantwort oder weitere Skriptausführung mit alten Berechtigungen.
- Hardware-/VM-Konfiguration und Bootverfahren. Secure Boot nur nennen,
  wenn genau dieser Modus tatsächlich geprüft wurde.

Die Akzeptanzszenarien A01–A16 des Feinkonzepts sind die vollständige fachliche
Abnahmeliste. Repositorytests decken die simulierbaren Server- und Runner-Teile ab,
nicht die Betriebssysteminstallation oder reale Ausfälle.
