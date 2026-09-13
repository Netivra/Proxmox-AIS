"""Conservative module drafts. Publication always requires target-host evidence."""
from pathlib import Path


def schema(properties, required=()):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}


STRING_LIST = {"type": "array", "items": {"type": "string"}, "maxItems": 100}
DEFINITIONS = [
    ("prerequisites", "Voraussetzungen", "PVE-Version, DNS, Uhrzeit und freien Speicher prüfen.",
     schema({"allowed_versions": STRING_LIST, "dns_names": STRING_LIST,
             "minimum_free_mb": {"type": "integer", "minimum": 512, "maximum": 1048576}}),
     {"allowed_versions": [], "dns_names": [], "minimum_free_mb": 2048}, [], 120, True),
    ("repositories", "Paketquellen", "Eine signierte, explizit freigegebene HTTPS-Paketquelle verwalten.",
     schema({"url": {"type": "string"}, "suite": {"type": "string"}, "components": STRING_LIST,
             "keyring": {"type": "string"}}, ("url", "suite", "components", "keyring")),
     {}, ["prerequisites"], 600, True),
    ("packages", "Basispakete", "Explizit genannte Pakete installieren; keine globale Aktualisierung.",
     schema({"packages": STRING_LIST}), {"packages": []}, ["prerequisites"], 1800, True),
    ("ssh", "SSH-Zugang", "Freigegebene Schlüssel vorhandenen Benutzern hinzufügen; sshd validieren.",
     schema({"users": {"type": "array", "maxItems": 50, "items": schema({
         "name": {"type": "string", "pattern": "^[a-z_][a-z0-9_-]{0,31}$"},
         "authorized_keys": STRING_LIST}, ("name", "authorized_keys"))}}),
     {"users": []}, ["prerequisites"], 120, True),
    ("time", "Zeitsynchronisation", "Chrony mit expliziten Zeitservern konfigurieren und Synchronisation prüfen.",
     schema({"servers": STRING_LIST}, ("servers",)), {"servers": []}, ["packages"], 600, True),
    ("monitoring", "Monitoring", "Optional den Debian prometheus-node-exporter aktivieren.",
     schema({"enabled": {"type": "boolean"}}), {"enabled": False}, ["packages"], 600, False),
    ("storage", "Zusätzlicher Storage", "Vorhandenes Verzeichnis ohne Formatierung als PVE-Storage anbinden.",
     schema({"id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,31}$"},
             "path": {"type": "string"}, "content": STRING_LIST}, ("id", "path", "content")),
     {}, ["prerequisites"], 120, True),
    ("final-verification", "Abschlussprüfung", "PVE-Dienste, Versionsstand, DNS, Zeit und aktiven Storage prüfen.",
     schema({"allowed_versions": STRING_LIST, "dns_names": STRING_LIST,
             "storage_ids": STRING_LIST, "require_time_sync": {"type": "boolean"}}),
     {"allowed_versions": [], "dns_names": [], "storage_ids": [], "require_time_sync": True},
     ["prerequisites"], 180, True),
]


def catalog():
    result = []
    root = Path(__file__).parent
    names = {definition[0]: definition[1] for definition in DEFINITIONS}
    for module_id, name, description, parameters_schema, defaults, dependencies, timeout, required in DEFINITIONS:
        result.append({"id": module_id, "name": name, "description": description, "version": "1.0.0",
                       "source": (root / f"{module_id}.sh").read_text(encoding="utf-8"),
                       "parameters_schema": parameters_schema, "default_parameters": defaults,
                       "dependencies": [names[dependency] for dependency in dependencies], "timeout_seconds": timeout,
                       "retry_safe": True, "required": required, "status": "draft",
                       "test_evidence": "", "target_builds": []})
    return result
