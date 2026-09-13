"""Transactional domain rules shared by browser and machine APIs."""
from copy import deepcopy
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from ipaddress import ip_address, ip_interface
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
import jsonschema
import tomli_w

from .models import HostCreate, normalize_identity
from .security import atomic_artifact, canonical, digest, redact, token

TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex


def require(condition, status, detail):
    if not condition:
        raise HTTPException(status, detail)


def unpack(row):
    if row is None:
        raise HTTPException(404, "Objekt nicht gefunden.")
    result = dict(row)
    data = json.loads(result.pop("data", "{}"))
    return {**data, **result}


def audit(connection, actor, action, object_id, reason="", data=None):
    connection.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?)", (new_id("audit"), actor, action, object_id, reason, canonical(data or {}), now_iso()))


def get_host(connection, host_id):
    host = unpack(connection.execute("SELECT * FROM hosts WHERE id=?", (host_id,)).fetchone())
    host["identities"] = [dict(r) for r in connection.execute("SELECT kind,value FROM host_identities WHERE host_id=? ORDER BY kind,value", (host_id,))]
    host["blocked"] = bool(host["blocked"])
    stored = json.loads(connection.execute("SELECT data FROM hosts WHERE id=?", (host_id,)).fetchone()[0])
    host["management_ip"] = stored.get("management_ip")
    latest = connection.execute("SELECT last_seen FROM runs WHERE host_id=? ORDER BY created_at DESC LIMIT 1",(host_id,)).fetchone()
    host["last_seen"] = latest[0] if latest else None
    for field,table,label in (("installation_profile_id","profiles","installation_profile_name"),("postinstall_profile_id","profiles","postinstall_profile_name"),("iso_id","iso_records","iso_name")):
        name = connection.execute(f"SELECT name FROM {table} WHERE id=?",(host.get(field),)).fetchone()
        host[label] = name[0] if name else None
    return host


def public_run(row):
    result = unpack(row)
    for field in ("answer_ciphertext", "bootstrap_ciphertext", "secrets_ciphertext", "bootstrap_hash", "enrollment_hash", "report_hash", "device_key"):
        result.pop(field, None)
    return result


def deep_merge(base, patch, provenance=None, source="", prefix=""):
    for key, value in patch.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if not isinstance(base.get(key), dict):
                base[key] = {}
            deep_merge(base[key], value, provenance, source, path)
        else:
            base[key] = deepcopy(value)
            if provenance is not None:
                provenance[path] = source
    return base


def leaf_paths(value, prefix=""):
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            yield from leaf_paths(item, path)
        else:
            yield path


class Service:
    def __init__(self, settings, db, security):
        self.settings, self.db, self.security = settings, db, security
        self.artifact_dir = settings.data_dir / "artifacts"

    def create_host(self, connection, payload, actor):
        host_id = new_id("host")
        data = payload.model_dump()
        normalized = [(item.kind, normalize_identity(item.kind, item.value)) for item in payload.identities]
        require(len(set(normalized)) == len(normalized), 422, "Identitäten sind doppelt angegeben.")
        address = str(ip_interface(payload.management_ip).ip) if payload.management_ip else None
        connection.execute("INSERT INTO hosts(id,fqdn,management_ip,site,status,data,blocked,created_at) VALUES(?,?,?,?,?,?,?,?)", (host_id, payload.fqdn, address, payload.site, "ready", canonical(data), payload.blocked, now_iso()))
        connection.executemany("INSERT INTO host_identities VALUES(?,?,?)", [(host_id, *item) for item in normalized])
        audit(connection, actor, "host.created", host_id)
        return get_host(connection, host_id)

    def update_host(self, connection, host_id, patch, actor):
        previous = get_host(connection, host_id)
        require(previous["version"] == patch.expected_version, 409, "Host wurde zwischenzeitlich geändert. Ansicht neu laden.")
        values = json.loads(connection.execute("SELECT data FROM hosts WHERE id=?", (host_id,)).fetchone()[0])
        values.update(patch.model_dump(exclude_unset=True, exclude={"expected_version"}))
        payload = HostCreate.model_validate(values)
        identities = [(i.kind, normalize_identity(i.kind, i.value)) for i in payload.identities]
        require(len(set(identities)) == len(identities), 422, "Identitäten sind doppelt angegeben.")
        active = connection.execute("SELECT id FROM runs WHERE host_id=? AND status NOT IN ('succeeded','failed','cancelled','expired')", (host_id,)).fetchone()
        changed_keys = set(patch.model_fields_set) - {"expected_version", "blocked", "tags"}
        require(not active or not changed_keys, 409, "Während eines aktiven Laufs sind nur Sperre und Tags änderbar.")
        address = str(ip_interface(payload.management_ip).ip) if payload.management_ip else None
        connection.execute("UPDATE hosts SET fqdn=?,management_ip=?,site=?,blocked=?,data=?,version=version+1 WHERE id=?", (payload.fqdn,address,payload.site,payload.blocked,canonical(payload.model_dump()),host_id))
        connection.execute("DELETE FROM host_identities WHERE host_id=?", (host_id,))
        connection.executemany("INSERT INTO host_identities VALUES(?,?,?)", [(host_id, *item) for item in identities])
        audit(connection, actor, "host.updated", host_id, data={"fields": sorted(patch.model_fields_set)})
        return get_host(connection, host_id)

    def create_profile(self, connection, payload, actor):
        data = payload.model_dump()
        require(payload.kind == "postinstall" or not payload.steps, 422, "Installationsprofile enthalten keine Skriptschritte.")
        self.reject_inline_secrets(data["values"])
        for step in data["steps"]:
            self.reject_inline_secrets(step["parameters"])
        version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM profiles WHERE name=? AND kind=?", (payload.name,payload.kind)).fetchone()[0]
        profile_id = new_id("profile")
        data["digest"] = digest(canonical(data))
        connection.execute("INSERT INTO profiles VALUES(?,?,?,?,?,?,?,?)", (profile_id,payload.name,payload.kind,version,"draft",canonical(data),actor,now_iso()))
        audit(connection, actor, "profile.created", profile_id, payload.reason)
        return unpack(connection.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone())

    @staticmethod
    def reject_inline_secrets(value):
        for path in leaf_paths(value):
            name = path.rsplit(".", 1)[-1].lower().replace("-", "_")
            require(name not in {"password", "root_password", "root_password_hashed", "secret", "token", "private_key"}, 422, "Geheimnisse müssen über Secret-Referenzen eingebunden werden.")

    def resolve(self, connection, host_id):
        host = get_host(connection, host_id)
        require(not host["blocked"], 403, "Host ist gesperrt.")
        install = unpack(connection.execute("SELECT * FROM profiles WHERE id=?", (host.get("installation_profile_id"),)).fetchone())
        post = unpack(connection.execute("SELECT * FROM profiles WHERE id=?", (host.get("postinstall_profile_id"),)).fetchone())
        require(install["kind"] == "installation" and post["kind"] == "postinstall", 422, "Profiltypen passen nicht zur Zuordnung.")
        require(install["status"] == post["status"] == "published", 403, "Beide Profile müssen veröffentlicht sein.")
        iso = unpack(connection.execute("SELECT * FROM iso_records WHERE id=?", (host.get("iso_id"),)).fetchone())
        require(iso["test_status"] == "passed" and iso["native_token_support"] and len(iso["test_evidence"]) >= 5, 403, "ISO benötigt Testnachweis und native Token-Unterstützung.")
        group = connection.execute("SELECT * FROM groups WHERE id=?", (iso["group_id"],)).fetchone()
        require(group and not group["revoked"] and group["expires_at"] > time.time() and group["site"] == host["site"], 403, "ISO-Gruppe ist ungültig oder gehört zu einem anderen Standort.")
        require(iso["build"] in install["target_builds"] and iso["build"] in post["target_builds"], 422, "Zielbuild ist nicht in beiden Profilen freigegeben.")
        resolved, provenance = {}, {}
        deep_merge(resolved, self.settings.defaults, provenance, "Globale Vorgaben")
        deep_merge(resolved, self.settings.sites.get(host["site"], {}), provenance, f"Standort {host['site']}")
        deep_merge(resolved, install["values"], provenance, f"Profil {install['name']} v{install['version']}")
        overrides = host.get("overrides", {})
        paths = list(leaf_paths(overrides))
        for locked in install.get("locked_fields", []):
            require(not any(path == locked or path.startswith(locked + ".") for path in paths), 422, f"Host darf gesperrtes Feld {locked} nicht überschreiben.")
        require(set(overrides) <= {"global", "network", "root_secret_id", "disk_setup"}, 422, "Unzulässige Hostparameter.")
        deep_merge(resolved, overrides, provenance, "Host")
        stored_host = json.loads(connection.execute("SELECT data FROM hosts WHERE id=?", (host_id,)).fetchone()[0])
        deep_merge(resolved, {"global": {"fqdn": host["fqdn"]}, "network": {"cidr": stored_host.get("management_ip")}}, provenance, "Host")
        self.validate_installation(resolved)
        secret_row = connection.execute("SELECT * FROM secrets WHERE id=?", (resolved["root_secret_id"],)).fetchone()
        require(secret_row is not None, 422, "Root-Passwort-Hash als Secret fehlt.")
        root_hash = self.security.decrypt(secret_row["ciphertext"])
        require(root_hash.startswith(("$6$", "$5$", "$y$")) and len(root_hash) > 30, 422, "Root-Secret muss ein unterstützter crypt-Passwort-Hash sein.")
        steps, seen, seen_names = [], set(), {}
        secrets_snapshot = {"root": root_hash, "steps": {}}
        for step in post["steps"]:
            require(step["id"] not in seen, 422, "Schritt-IDs müssen eindeutig sein.")
            module = unpack(connection.execute("SELECT * FROM modules WHERE id=?", (step["module_id"],)).fetchone())
            require(module["status"] == "published" and iso["build"] in module["target_builds"], 403, f"Modul {module['name']} ist für den Build nicht freigegeben.")
            require(set(module["dependencies"]) <= set(seen_names), 422, f"Abhängigkeiten von {module['name']} sind nicht vorher eingeplant.")
            try:
                jsonschema.Draft202012Validator(module["parameters_schema"]).validate(step["parameters"])
            except jsonschema.ValidationError:
                raise HTTPException(422, f"Parameter von {module['name']} passen nicht zum Schema.")
            checksum = module["digest"]
            path = self.artifact_dir / checksum
            require(path.is_file() and digest(path.read_bytes()) == checksum, 422, "Modulartefakt fehlt oder ist beschädigt.")
            secrets_snapshot["steps"][step["id"]] = {}
            for name, secret_id in step.get("secret_refs", {}).items():
                require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)), 422, "Ungültiger Secret-Parametername.")
                secret = connection.execute("SELECT ciphertext FROM secrets WHERE id=?", (secret_id,)).fetchone()
                require(secret is not None, 422, "Ein Schritt-Secret fehlt.")
                secrets_snapshot["steps"][step["id"]][name] = self.security.decrypt(secret[0])
            steps.append({**step, "name": module["name"], "module_version": module["version"], "digest": checksum, "timeout_seconds": module["timeout_seconds"], "retry_safe": module["retry_safe"], "dependencies": [seen_names[name] for name in module["dependencies"]]})
            seen.add(step["id"])
            seen_names[module["name"]] = step["id"]
        require(steps and any(step["required"] for step in steps), 422, "Mindestens eine verpflichtende Abschlussprüfung ist erforderlich.")
        snapshot = {"resolved": resolved, "provenance": provenance, "profiles": [{"id": p["id"], "name": p["name"], "version": p["version"], "digest": p["digest"]} for p in (install,post)], "steps": steps, "disks": resolved["disk_setup"], "iso": iso, "identities": host["identities"], "reboot_budget": post.get("reboot_budget",1), "warnings": ["Die Hardwarekennung dient der Zuordnung im kontrollierten Provisionierungsnetz."]}
        snapshot["digest"] = digest(canonical(snapshot))
        return snapshot, secrets_snapshot

    @staticmethod
    def validate_installation(values):
        require(isinstance(values,dict),422,"Installationsparameter müssen ein Objekt sein.")
        require(set(values) <= {"global", "network", "disk_setup", "root_secret_id"}, 422, "Unbekannte Installationsparameter.")
        glob = values.get("global", {})
        require(isinstance(glob,dict) and all(isinstance(glob.get(k),str) for k in ("keyboard","country","timezone","mailto","fqdn")),422,"Globale Pflichtfelder müssen Zeichenketten sein.")
        require(set(glob) <= {"keyboard", "country", "timezone", "mailto", "fqdn", "root-ssh-keys", "reboot-on-error"}, 422, "Nicht freigegebene globale Antwortoption.")
        require(all(glob.get(k) for k in ("keyboard", "country", "timezone", "mailto", "fqdn")), 422, "Globale Pflichtfelder fehlen.")
        require(bool(re.fullmatch(r"[a-z]{2}", glob["country"])) and "@" in glob["mailto"], 422, "Land oder E-Mail ungültig.")
        require(glob["keyboard"] in {"de","de-ch","dk","en-gb","en-us","es","fi","fr","fr-be","fr-ca","fr-ch","hu","is","it","jp","lt","mk","nl","no","pl","pt","pt-br","se","si","tr"},422,"Tastaturlayout wird vom Installer nicht unterstützt.")
        require(isinstance(glob.get("reboot-on-error",False),bool),422,"reboot-on-error muss ein Wahrheitswert sein.")
        require(isinstance(glob.get("root-ssh-keys",[]),list) and all(isinstance(k,str) and k.startswith(("ssh-ed25519 ","ssh-rsa ","ecdsa-sha2-")) for k in glob.get("root-ssh-keys",[])),422,"Root-SSH-Schlüssel müssen als Liste öffentlicher Schlüssel angegeben werden.")
        try:
            ZoneInfo(glob["timezone"])
        except (ZoneInfoNotFoundError, TypeError):
            raise HTTPException(422, "Ungültige Zeitzone.")
        network = values.get("network", {})
        require(isinstance(network,dict),422,"Netzwerkparameter müssen ein Objekt sein.")
        require(set(network) <= {"source", "cidr", "gateway", "dns", "filter"}, 422, "Nicht freigegebene Netzwerkoption.")
        require(network.get("source") == "from-answer" and network.get("filter"), 422, "Explizites Managementnetz und Interface-Filter erforderlich.")
        try:
            address = ip_interface(network["cidr"])
            gateway, dns = ip_address(network["gateway"]), ip_address(network["dns"])
            require(gateway.version == address.version and gateway in address.network, 422, "Gateway liegt außerhalb des Managementnetzes.")
            require(address.ip != gateway, 422, "Hostadresse darf nicht der Gatewayadresse entsprechen.")
            if address.version == 4:
                require(address.ip not in {address.network.network_address,address.network.broadcast_address}, 422, "Hostadresse ist Netz- oder Broadcastadresse.")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Management-IP mit CIDR, Gateway und DNS müssen gültig sein.")
        require(isinstance(network["filter"],dict) and all(isinstance(k,str) and isinstance(v,str) and v and v != "*" for k,v in network["filter"].items()), 422, "Expliziter Interface-Filter erforderlich.")
        disks = values.get("disk_setup", {})
        require(isinstance(disks,dict),422,"Datenträgerparameter müssen ein Objekt sein.")
        require(set(disks) <= {"filesystem", "filter", "filter_match", "expected_count", "expected_serials", "inventory_evidence", "zfs", "lvm"}, 422, "Nicht freigegebene Datenträgeroption.")
        require(isinstance(disks.get("filesystem"),str) and disks["filesystem"] in {"ext4", "xfs", "zfs"}, 422, "Unterstützte Dateisysteme: ext4, xfs, zfs.")
        filters = disks.get("filter", {})
        require(isinstance(filters,dict) and bool(filters) and set(filters) <= {"ID_SERIAL", "ID_SERIAL_SHORT", "ID_WWN"}, 422, "Datenträger benötigen stabile Seriennummer- oder WWN-Filter.")
        serials = disks.get("expected_serials", [])
        require(isinstance(serials,list) and all(isinstance(s,str) for s in serials) and 1 <= len(serials) <= 16 and len(set(serials)) == len(serials) and type(disks.get("expected_count")) is int and disks["expected_count"] == len(serials), 422, "Erwartete Datenträger und Anzahl müssen explizit übereinstimmen.")
        require(all(isinstance(s,str) and s and not any(c in s for c in "*?[]") for s in serials), 422, "Erwartete Seriennummern müssen konkret sein.")
        require(all(isinstance(v,str) and v and v not in {"*", "?"} for v in filters.values()), 422, "Pauschale Datenträgerfilter sind unzulässig.")
        require(len(filters) == 1 and all(fnmatchcase(s, next(iter(filters.values()))) for s in serials), 422, "Ein stabiler Filter muss alle bestätigten Systemdatenträger auswählen.")
        require(isinstance(disks.get("inventory_evidence"),str) and len(disks["inventory_evidence"]) >= 5, 422, "Datenträger benötigen einen Inventarisierungsnachweis.")
        require(isinstance(disks.get("filter_match","all"),str) and disks.get("filter_match","all") in {"all","any"},422,"Ungültiger Datenträger-Filtermodus.")
        if disks["filesystem"] in {"ext4", "xfs"}:
            require(len(serials) == 1, 422, "LVM-Dateisysteme benötigen genau einen Systemdatenträger.")
            require("zfs" not in disks,422,"ZFS-Optionen sind mit einem LVM-Dateisystem nicht kombinierbar.")
            lvm = disks.get("lvm",{})
            require(isinstance(lvm,dict) and set(lvm) <= {"hdsize","swapsize","maxroot","maxvz","minfree"},422,"Nicht unterstützte LVM-Option.")
            require(all(isinstance(v,(int,float)) and not isinstance(v,bool) and v >= (2 if k in {"hdsize","maxroot"} else 0) and v < 1000000 for k,v in lvm.items()),422,"Ungültige LVM-Größenangabe.")
        else:
            require("lvm" not in disks,422,"LVM-Optionen sind mit ZFS nicht kombinierbar.")
            zfs = disks.get("zfs",{})
            require(isinstance(zfs,dict) and set(zfs) <= {"raid","ashift","arc-max","checksum","compress","copies","hdsize"},422,"Nicht unterstützte ZFS-Option.")
            raid = zfs.get("raid")
            minimum = {"raid0":1,"raid1":2,"raid10":4,"raidz-1":3,"raidz-2":4,"raidz-3":5}
            require(isinstance(raid,str) and raid in minimum, 422, "ZFS benötigt einen expliziten RAID-Modus.")
            require(len(serials)>=minimum[raid] and (raid!="raid10" or len(serials)%2==0),422,"Datenträgeranzahl passt nicht zum ZFS-RAID-Modus.")
            for key,lower,upper in (("ashift",9,16),("arc-max",64,1048576),("copies",1,3),("hdsize",2,1000000)):
                if key in zfs:
                    require(isinstance(zfs[key],(int,float)) and not isinstance(zfs[key],bool) and lower<=zfs[key]<=upper and (key=="hdsize" or isinstance(zfs[key],int)),422,f"Ungültige ZFS-Option {key}.")
            require(isinstance(zfs.get("checksum","on"),str) and isinstance(zfs.get("compress","on"),str) and zfs.get("checksum","on") in {"on","fletcher4","sha256"} and zfs.get("compress","on") in {"on","off","lzjb","lz4","zle","gzip","zstd"},422,"Nicht unterstützte ZFS-Kompression oder Prüfsumme.")
        require(isinstance(values.get("root_secret_id"),str) and bool(values["root_secret_id"]), 422, "Root-Secret-Referenz fehlt.")

    def approve(self, connection, host_id, payload, actor):
        require(not self.settings.maintenance, 503, "Wartungsmodus: Neue Freigaben sind gesperrt.")
        host = get_host(connection, host_id)
        require(host["version"] == payload.expected_version, 409, "Host wurde geändert. Vorschau erneut prüfen.")
        require(payload.confirmation == host["fqdn"] and payload.disks_confirmed, 422, "FQDN und Überschreiben der aufgeführten Systemdatenträger müssen bestätigt werden.")
        active = connection.execute("SELECT id FROM runs WHERE host_id=? AND status NOT IN ('succeeded','failed','cancelled','expired')", (host_id,)).fetchone()
        require(not active, 409, "Für diesen Host existiert bereits ein aktiver Lauf.")
        snapshot, secret_values = self.resolve(connection, host_id)
        run_id, approval_id = new_id("run"), new_id("approval")
        manifest = {"run_id": run_id, "steps": snapshot["steps"], "reboot_budget": snapshot["reboot_budget"]}
        manifest_digest = digest(canonical(manifest))
        run_data = {"snapshot": snapshot, "manifest": manifest, "manifest_digest": manifest_digest, "fqdn": host["fqdn"], "site": host["site"], "cancel_requested": False, "reboots": 0}
        expiry = time.time() + payload.valid_minutes * 60
        connection.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)", (approval_id,host_id,"approved",expiry,canonical({"actor":actor,"reason":payload.reason,"snapshot_digest":snapshot["digest"]}),now_iso()))
        connection.execute("INSERT INTO runs(id,host_id,approval_id,status,data,secrets_ciphertext,created_at) VALUES(?,?,?,?,?,?,?)", (run_id,host_id,approval_id,"prepared",canonical(run_data),self.security.encrypt(canonical(secret_values)),now_iso()))
        connection.executemany("INSERT INTO run_steps(run_id,step_id,position) VALUES(?,?,?)", [(run_id,s["id"],i) for i,s in enumerate(snapshot["steps"])])
        connection.execute("UPDATE hosts SET status='prepared',version=version+1 WHERE id=?", (host_id,))
        audit(connection, actor, "installation.approved", host_id, payload.reason, {"run_id":run_id,"disks":snapshot["disks"],"expires_at":expiry})
        return public_run(connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    @staticmethod
    def installer_identity(payload):
        require(isinstance(payload,dict), 422, "Installer-Payload muss ein Objekt sein.")
        meta = payload.get("$schema", payload.get("$fetchinfo", {}))
        dmi = payload.get("dmi",{})
        require(isinstance(meta,dict) and isinstance(dmi,dict),422,"Native Metadaten oder DMI-Daten sind ungültig.")
        schema = meta.get("version", "legacy")
        require(isinstance(schema,str) and schema in {"1.0","legacy"},422,"Nicht freigegebenes Installer-Payload-Schema.")
        system = dmi.get("system", {})
        interfaces = payload.get("network-interfaces",payload.get("network_interfaces",[]))
        require(isinstance(system,dict) and isinstance(interfaces,list) and len(interfaces)<=64,422,"Native Hardwarekennungen sind ungültig.")
        raw = []
        for field,kind in (("uuid","uuid"),("serial","serial")):
            if system.get(field):
                raw.append((kind,system[field]))
        for item in interfaces:
            if isinstance(item,dict) and item.get("mac"):
                raw.append(("mac",item["mac"]))
        identities = []
        for kind,value in raw:
            if not isinstance(value,str) or value.lower() in {"unknown","none","not specified","default string","to be filled by o.e.m."}:
                continue
            try:
                identities.append({"kind":kind,"value":normalize_identity(kind,value)})
            except ValueError:
                continue
        require(identities, 422, "Installer übermittelt keine verwendbare Hardwarekennung.")
        product = payload.get("product", {})
        iso = payload.get("iso", {})
        require(isinstance(product,dict) and isinstance(iso,dict),422,"Native Produkt- und ISO-Angaben fehlen.")
        require(product.get("product") == "pve", 422, "Nur Proxmox VE Installer werden unterstützt.")
        release, build = iso.get("release"), iso.get("build")
        require(release and build, 422, "Native ISO Release- und Build-Informationen fehlen.")
        return identities, f"{release}-{build}", schema

    @staticmethod
    def match_host(connection, identities, site=None):
        candidates = set()
        for identity in identities:
            for row in connection.execute("SELECT host_id FROM host_identities WHERE kind=? AND value=?", (identity["kind"],identity["value"])):
                candidates.add(row[0])
        require(len(candidates) <= 1, 409, "Widersprüchliche Hardwarekennungen gehören zu mehreren Hosts.")
        if not candidates:
            return None
        host = get_host(connection, candidates.pop())
        if site is not None:
            require(host["site"] == site, 403, "Host gehört nicht zum Standort des Gruppentokens.")
        for kind in ("uuid", "serial"):
            expected = {x["value"] for x in host["identities"] if x["kind"] == kind}
            supplied = {x["value"] for x in identities if x["kind"] == kind}
            require(not expected or not supplied or supplied <= expected, 409, "UUID und Seriennummer widersprechen der gespeicherten Hostidentität.")
        return host

    def serve_answer(self, connection, group, payload):
        identities, build, schema = self.installer_identity(payload)
        host = self.match_host(connection, identities, group["site"])
        if host is None:
            fingerprint = digest(canonical(sorted(identities,key=lambda i:(i["kind"],i["value"]))))
            connection.execute("INSERT INTO discoveries VALUES(?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET last_seen=excluded.last_seen", (new_id("discovery"),fingerprint,group["site"],canonical({"identities":identities,"build":build,"schema":schema}),"Host ist nicht zugeordnet.",time.time()))
            # The caller commits discovery before returning the denial.
            return None
        require(not host["blocked"], 403, "Host ist gesperrt.")
        row = connection.execute("SELECT * FROM runs WHERE host_id=? ORDER BY created_at DESC LIMIT 1", (host["id"],)).fetchone()
        require(row is not None, 403, "Keine ausdrückliche Installationsfreigabe vorhanden.")
        run = unpack(row)
        iso = run["snapshot"]["iso"]
        require(iso["group_id"] == group["id"] and iso["build"] == build, 403, "Installergruppe oder Zielbuild stimmt nicht mit der Freigabe überein.")
        require(not run.get("cancel_requested"), 403, "Lauf ist zum Abbruch markiert.")
        require(run["status"] != "expired", 410, "Installationsfreigabe ist abgelaufen.")
        if run["status"] == "answer_served":
            require(time.time() < run["answer_until"], 410, "Auslieferungsfenster ist abgelaufen.")
            audit(connection, "installer:" + group["name"], "answer.repeated", run["id"])
            return self.security.decrypt(run["answer_ciphertext"])
        require(run["status"] == "prepared", 403, "Dieser Lauf erlaubt keine weitere Installation.")
        approval = connection.execute("SELECT * FROM approvals WHERE id=?", (run["approval_id"],)).fetchone()
        require(approval["status"] == "approved" and approval["expires_at"] > time.time(), 410, "Installationsfreigabe ist abgelaufen.")
        require(self.settings.testing or self.settings.public_url.startswith("https://"), 503, "Maschinenendpunkte benötigen eine konfigurierte HTTPS-Adresse.")
        bootstrap_token, enrollment_secret, report_token = token(), token(), token()
        from .bootstrap import render_bootstrap
        bootstrap_config = {"api_url":self.settings.public_url,"run_id":run["id"],"enrollment_secret":enrollment_secret,"identities":run["snapshot"]["identities"],"manifest_digest":run["manifest_digest"]}
        if self.settings.runner_ca_file:
            from pathlib import Path
            bootstrap_config["ca_pem"] = Path(self.settings.runner_ca_file).read_text()
        bootstrap = render_bootstrap(bootstrap_config)
        require(len(bootstrap.encode()) <= 1024 * 1024, 422, "Starthelfer überschreitet das Größenlimit.")
        resolved = deepcopy(run["snapshot"]["resolved"])
        resolved["global"]["root-password-hashed"] = json.loads(self.security.decrypt(run["secrets_ciphertext"]))["root"]
        disks = resolved["disk_setup"]
        native_disks = {k:v for k,v in disks.items() if k not in {"expected_count","expected_serials","inventory_evidence","filter_match"}}
        native_disks["filter-match"] = disks.get("filter_match","all")
        answer_data = {"global":resolved["global"],"network":resolved["network"],"disk-setup":native_disks,"first-boot":{"source":"from-url","ordering":"network-online","url":self.settings.public_url + "/bootstrap/v1/" + bootstrap_token,"cert-fingerprint":iso["fingerprint"]},"post-installation-webhook":{"url":self.settings.public_url + "/installer/v1/report/" + report_token,"cert-fingerprint":iso["fingerprint"]}}
        answer = tomli_w.dumps(answer_data)
        run_data = json.loads(row["data"])
        run_data.update({"answer_digest":digest(answer),"installer_schema":schema})
        connection.execute("UPDATE runs SET status='answer_served',version=version+1,data=?,answer_ciphertext=?,bootstrap_ciphertext=?,bootstrap_hash=?,enrollment_hash=?,report_hash=?,answer_until=?,enroll_until=?,last_seen=? WHERE id=?", (canonical(run_data),self.security.encrypt(answer),self.security.encrypt(bootstrap),digest(bootstrap_token),digest(enrollment_secret),digest(report_token),time.time()+self.settings.answer_window_seconds,time.time()+self.settings.enrollment_hours*3600,time.time(),run["id"]))
        connection.execute("UPDATE approvals SET status='consumed' WHERE id=?", (run["approval_id"],))
        connection.execute("UPDATE hosts SET status='answer_served' WHERE id=?", (host["id"],))
        audit(connection, "installer:" + group["name"], "answer.served", run["id"])
        return answer

    def redact_run(self, row, text):
        values = []
        if row["secrets_ciphertext"]:
            secret_values = json.loads(self.security.decrypt(row["secrets_ciphertext"]))
            values.append(secret_values.get("root", ""))
            for step in secret_values.get("steps", {}).values():
                values.extend(step.values())
        return redact(text, values)

    def redact_payload(self, row, value):
        def walk(item):
            if isinstance(item, str):
                return self.redact_run(row, item)
            if isinstance(item, dict):
                return {key:walk(child) for key,child in item.items()}
            if isinstance(item, list):
                return [walk(child) for child in item]
            return item
        return canonical(walk(value))

    def maintain(self):
        with self.db.connection(write=True) as connection:
            timestamp = time.time()
            connection.execute("DELETE FROM sessions WHERE expires_at<?", (timestamp,))
            connection.execute("DELETE FROM nonces WHERE expires_at<?", (timestamp,))
            expired = connection.execute("SELECT r.id,r.host_id,r.approval_id FROM runs r JOIN approvals a ON a.id=r.approval_id WHERE r.status='prepared' AND a.expires_at<?", (timestamp,)).fetchall()
            for run in expired:
                connection.execute("UPDATE runs SET status='expired',version=version+1,completed_at=? WHERE id=?", (timestamp,run["id"]))
                connection.execute("UPDATE approvals SET status='expired' WHERE id=?", (run["approval_id"],))
                connection.execute("UPDATE hosts SET status='expired' WHERE id=?", (run["host_id"],))
                audit(connection,"system","approval.expired",run["id"])
            for table,days in (("logs",self.settings.log_retention_days),("audit",self.settings.audit_retention_days)):
                cutoff = datetime.fromtimestamp(timestamp-days*86400,timezone.utc).isoformat()
                connection.execute(f"DELETE FROM {table} WHERE created_at<?", (cutoff,))

    def module_syntax(self, source):
        executable = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
            if candidate and candidate.is_file():
                executable = str(candidate)
        require(executable is not None, 503, "Bash-Syntaxprüfung nicht verfügbar. Modul im Linux-Container veröffentlichen.")
        result = subprocess.run([executable,"-n"],input=source.replace("\r\n","\n").encode(),capture_output=True,timeout=15)
        require(result.returncode == 0, 422, "Bash-Syntaxprüfung fehlgeschlagen.")
        require(all(re.search(r"\b" + phase + r"\b",source) for phase in ("check","apply","verify")), 422, "Modul muss check/apply/verify implementieren.")
