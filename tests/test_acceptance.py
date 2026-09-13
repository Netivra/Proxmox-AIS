"""Acceptance tests exercise authorization and installation state through HTTP."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import time
import tomllib
from uuid import uuid4

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import pytest

from provisioner.app import create_app
from provisioner.cli import ServiceLock, backup, initialize, restore
from provisioner.config import Settings
from provisioner.db import Database
from provisioner.security import Security


PASSWORD = "Ais-test-admin-only-452!"
ROOT_HASH = "$6$testsalt$" + "A" * 86
HOST_UUID = "d2e59b03-13cf-4ac9-a390-78c55f6a36d3"
HOST_MAC = "02:00:00:00:00:01"
SOURCE = '#!/bin/bash\nset -euo pipefail\ncase "$1" in\ncheck|apply|verify) exit 0;;\n*) exit 64;;\nesac\n'


def login(client, username="admin", password=PASSWORD):
    response = client.post("/auth/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303, response.text
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def post(client, path, data, csrf):
    response = client.post(path, json=data, headers=csrf)
    assert response.status_code in {200, 201}, f"{path}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture
def environment(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", master_key_file=tmp_path / "keys" / "master.key",
        public_url="https://testserver", secure_cookies=False, bootstrap_username="admin",
        bootstrap_password=PASSWORD, testing=True, four_eyes=False,
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        csrf = login(client)
        yield app, client, csrf


@pytest.fixture
def prepared(environment):
    app, client, csrf = environment
    secret = post(client, "/api/v1/secrets", {"name": "test-root", "value": ROOT_HASH}, csrf)
    group = post(client, "/api/v1/groups", {"name": "test-lab", "site": "lab", "valid_hours": 1}, csrf)
    iso = post(client, "/api/v1/iso-records", {
        "name": "Simulated ISO; not a real hardware certification", "build": "9.1-1", "sha256": "1" * 64,
        "assistant_version": "test-only", "fingerprint": "2" * 64, "group_id": group["id"],
        "native_token_support": True, "test_status": "passed", "test_evidence": "Synthetic HTTP acceptance fixture",
    }, csrf)
    module = post(client, "/api/v1/modules", {"name": "test-verification", "source": SOURCE,
        "target_builds": ["9.1-1"], "retry_safe": True}, csrf)
    publication = {"test_evidence": "Synthetic HTTP acceptance fixture", "reason": "Testing publication"}
    post(client, f"/api/v1/modules/{module['id']}/publish", publication, csrf)
    profile_data = json.loads((Path(__file__).parents[1] / "docs" / "sample-profile.json").read_text())
    profile_data["values"]["root_secret_id"] = secret["id"]
    installation = post(client, "/api/v1/profiles", profile_data, csrf)
    post(client, f"/api/v1/profiles/{installation['id']}/publish", publication, csrf)
    postinstall = post(client, "/api/v1/profiles", {"name": "test-postinstall", "kind": "postinstall",
        "target_builds": ["9.1-1"], "steps": [{"id": "verify", "module_id": module["id"], "required": True}]}, csrf)
    post(client, f"/api/v1/profiles/{postinstall['id']}/publish", publication, csrf)
    identities = [{"kind": "uuid", "value": HOST_UUID}, {"kind": "serial", "value": "LAB-HOST-001"},
                  {"kind": "mac", "value": HOST_MAC}]
    host = post(client, "/api/v1/hosts", {"fqdn": "pve01.lab.example.net", "site": "lab",
        "management_ip": "192.0.2.10/24", "identities": identities,
        "installation_profile_id": installation["id"], "postinstall_profile_id": postinstall["id"], "iso_id": iso["id"]}, csrf)
    run = post(client, f"/api/v1/hosts/{host['id']}/approve-install", {
        "expected_version": host["version"], "valid_minutes": 30, "confirmation": host["fqdn"],
        "disks_confirmed": True, "reason": "Dedicated simulated test host"}, csrf)
    payload = {"$schema": {"version": "1.0"}, "product": {"product": "pve"},
        "iso": {"release": "9.1", "build": "1"}, "dmi": {"system": {"uuid": HOST_UUID, "serial": "LAB-HOST-001"}},
        "network-interfaces": [{"mac": HOST_MAC}]}
    return {"app": app, "client": client, "csrf": csrf, "group": group, "iso": iso,
            "module": module, "installation": installation, "profile_data": profile_data,
            "host": host, "run": run, "payload": payload, "identities": identities}


def answer(prepared, payload=None):
    return prepared["client"].post("/installer/v1/answer", json=payload or prepared["payload"],
        headers={"Authorization": f"Bearer {prepared['group']['token']}"})


def enroll(prepared):
    response = answer(prepared)
    assert response.status_code == 200, response.text
    config = tomllib.loads(response.text)
    bootstrap = prepared["client"].get(config["first-boot"]["url"])
    assert bootstrap.status_code == 200
    encoded = re.search(r"config = base64.b64decode\('([^']+)'\)", bootstrap.text).group(1)
    runtime = json.loads(base64.b64decode(encoded))
    key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    registration = {"run_id": prepared["run"]["id"], "enrollment_secret": runtime["enrollment_secret"],
        "public_key": public_key, "identities": prepared["identities"], "boot_id": "boot-test-1"}
    response = prepared["client"].post("/agent/v1/enroll", json=registration)
    assert response.status_code == 200, response.text
    return key, public_key, registration


def signed(prepared, key, method, path, payload=None, headers=None):
    body = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    timestamp, nonce = str(int(time.time())), uuid4().hex
    message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
    request_headers = {"X-Run-ID": prepared["run"]["id"],
        "X-Device-Key": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        "X-Timestamp": timestamp, "X-Nonce": nonce, "X-Signature": base64.b64encode(key.sign(message)).decode(),
        "Content-Type": "application/json"}
    request_headers.update(headers or {})
    return prepared["client"].request(method, path, content=body, headers=request_headers)


def test_authentication_csrf_and_reader_permissions(environment):
    app, client, csrf = environment
    anonymous = TestClient(app, base_url="https://testserver")
    try:
        assert anonymous.get("/api/v1/hosts").status_code == 401
    finally:
        anonymous.close()
    assert client.post("/api/v1/groups", json={"name": "denied", "site": "lab"}).status_code == 403
    post(client, "/api/v1/users", {"username": "reader", "password": PASSWORD, "role": "reader"}, csrf)
    reader_csrf = login(client, "reader")
    assert client.get("/api/v1/hosts").status_code == 200
    assert client.post("/api/v1/groups", json={"name": "denied", "site": "lab"}, headers=reader_csrf).status_code == 403
    assert client.get("/api/v1/secrets").status_code == 403


def test_unknown_conflicting_and_blocked_hosts_never_receive_answer(prepared):
    unknown = deepcopy(prepared["payload"])
    unknown["dmi"]["system"] = {"uuid": str(uuid4()), "serial": "UNKNOWN-HOST"}
    unknown["network-interfaces"] = [{"mac": "02:00:00:00:ff:fe"}]
    assert answer(prepared, unknown).status_code == 403
    contradictory = deepcopy(prepared["payload"])
    contradictory["dmi"]["system"]["uuid"] = str(uuid4())
    assert answer(prepared, contradictory).status_code == 409
    host = prepared["client"].get(f"/api/v1/hosts/{prepared['host']['id']}").json()
    response = prepared["client"].patch(f"/api/v1/hosts/{host['id']}", json={"expected_version": host["version"], "blocked": True}, headers=prepared["csrf"])
    assert response.status_code == 200, response.text
    assert answer(prepared).status_code == 403


def test_expired_approval_and_wrong_group_cannot_install(prepared):
    other = post(prepared["client"], "/api/v1/groups", {"name": "other-group", "site": "lab"}, prepared["csrf"])
    denied = prepared["client"].post("/installer/v1/answer", json=prepared["payload"], headers={"Authorization": f"Bearer {other['token']}"})
    assert denied.status_code == 403
    with prepared["app"].state.db.connection(write=True) as connection:
        connection.execute("UPDATE approvals SET expires_at=?", (time.time() - 1,))
    assert answer(prepared).status_code == 410


def test_author_cannot_publish_own_version_when_four_eyes_enabled(environment):
    app, client, csrf = environment
    app.state.settings.four_eyes = True
    draft = post(client, "/api/v1/profiles", {"name": "self-publish-denied", "kind": "installation"}, csrf)
    response = client.post(f"/api/v1/profiles/{draft['id']}/publish", json={
        "test_evidence": "A laboratory test report", "reason": "Self publication attempt"}, headers=csrf)
    assert response.status_code == 403


def test_concurrent_installer_retries_reserve_one_immutable_answer(prepared):
    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(lambda _: answer(prepared), range(10)))
    assert {response.status_code for response in responses} == {200}, [r.text for r in responses]
    assert len({response.text for response in responses}) == 1
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    native = tomllib.loads(responses[0].text)
    assert native["global"]["fqdn"] == prepared["host"]["fqdn"]
    assert native["disk-setup"]["filter"] == {"ID_SERIAL_SHORT": "LAB_SYSTEM_DISK_001"}
    assert "expected_count" not in native["disk-setup"]
    with prepared["app"].state.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM approvals").fetchone()[0] == "consumed"


def test_new_profile_version_cannot_change_prepared_run(prepared):
    original = prepared["run"]["snapshot"]
    data = deepcopy(prepared["profile_data"])
    data["values"]["network"]["dns"] = "192.0.2.54"
    version2 = post(prepared["client"], "/api/v1/profiles", data, prepared["csrf"])
    assert version2["version"] == prepared["installation"]["version"] + 1
    assert version2["id"] != prepared["installation"]["id"]
    current = prepared["client"].get(f"/api/v1/runs/{prepared['run']['id']}").json()
    assert current["snapshot"] == original
    response = answer(prepared)
    assert response.status_code == 200, response.text
    assert tomllib.loads(response.text)["network"]["dns"] == "192.0.2.53"


def test_enrollment_signature_sequence_and_verified_completion(prepared):
    key, _, registration = enroll(prepared)
    run_id = prepared["run"]["id"]
    assert answer(prepared).status_code in {403, 410}
    # Retrying a lost enrollment response must not generate a second device identity.
    assert prepared["client"].post("/agent/v1/enroll", json=registration).status_code == 200
    lease = signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id})
    assert lease.status_code == 200 and lease.json()["action"] == "run"
    manifest = signed(prepared, key, "GET", f"/agent/v1/runs/{run_id}/manifest")
    assert manifest.status_code == 200, manifest.text
    wrong_key = Ed25519PrivateKey.generate()
    assert signed(prepared, wrong_key, "GET", f"/agent/v1/runs/{run_id}/manifest").status_code in {401, 403}
    premature = signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/complete", {"verification": {"verify": {"passed": True}}})
    assert premature.status_code == 409
    def event(sequence, kind, **extra):
        return {"sequence": sequence, "type": kind, "boot_id": "boot-test-1", "step_id": "verify",
                "occurred_at": "2026-09-13T12:00:00Z", **extra}
    out_of_order = {"events": [event(2, "step.started")]}
    assert signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/events", out_of_order).status_code == 409
    started = {"events": [event(1, "step.started")]}
    assert signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/events", started).status_code == 200
    assert signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/events", started).status_code == 200
    false_success = {"events": [event(2, "step.succeeded", exit_code=0, verification={"passed": False})]}
    assert signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/events", false_success).status_code == 409
    succeeded = {"events": [event(2, "step.succeeded", exit_code=0, verification={"passed": True})]}
    response = signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/events", succeeded)
    assert response.status_code == 200, response.text
    false_completion = signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/complete", {"verification": {"verify": {"passed": False}}})
    assert false_completion.status_code == 409
    completion = signed(prepared, key, "POST", f"/agent/v1/runs/{run_id}/complete", {"verification": {"verify": {"passed": True}}})
    assert completion.status_code == 200, completion.text
    status = prepared["client"].get(f"/api/v1/runs/{run_id}").json()["status"]
    assert status == "succeeded"
    assert answer(prepared).status_code in {403, 410}
    assert signed(prepared, key, "GET", f"/agent/v1/runs/{run_id}/manifest").status_code in {403, 410}


def test_device_signature_replay_and_cross_run_access_are_denied(prepared):
    key, _, _ = enroll(prepared)
    run_id = prepared["run"]["id"]
    assert signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id}).status_code == 200
    original = signed(prepared, key, "GET", f"/agent/v1/runs/{run_id}/manifest")
    assert original.status_code == 200
    repeated = prepared["client"].request(original.request.method, original.request.url, content=original.request.content, headers=original.request.headers)
    assert repeated.status_code == 409
    foreign_path = "/agent/v1/runs/another-run/manifest"
    tampered = prepared["client"].get(foreign_path, headers=original.request.headers)
    assert tampered.status_code == 401
    assert signed(prepared, key, "GET", foreign_path).status_code == 403


def test_tampered_module_is_not_delivered_to_runner(prepared):
    key, _, _ = enroll(prepared)
    run_id = prepared["run"]["id"]
    assert signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id}).status_code == 200
    checksum = prepared["module"]["digest"]
    endpoint = f"/agent/v1/artifacts/{checksum}"
    assert signed(prepared, key, "GET", endpoint).status_code == 200
    artifact = prepared["app"].state.settings.data_dir / "artifacts" / checksum
    artifact.write_bytes(b"#!/bin/bash\nexit 99\n")
    response = signed(prepared, key, "GET", endpoint)
    assert response.status_code == 503
    assert "exit 99" not in response.text


def test_reconciliation_waits_for_issued_lease_and_requires_local_confirmation(prepared):
    key, _, _ = enroll(prepared)
    client, csrf = prepared["client"], prepared["csrf"]
    run_id = prepared["run"]["id"]
    lease = signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id})
    assert lease.status_code == 200 and lease.json()["action"] == "run"
    issued_until = lease.json()["expires_at"]
    current = client.get(f"/api/v1/runs/{run_id}").json()
    cancelled = post(client, f"/api/v1/runs/{run_id}/cancel", {
        "expected_version": current["version"], "reason": "Stop before checking local host state"}, csrf)
    stop = signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id})
    assert stop.status_code == 200 and stop.json()["action"] == "stop"
    with prepared["app"].state.db.connection(write=True) as connection:
        assert connection.execute("SELECT lease_until FROM runs WHERE id=?", (run_id,)).fetchone()[0] == issued_until
        connection.execute("UPDATE runs SET answer_until=? WHERE id=?", (time.time() - 1, run_id))
    request = {"expected_version": cancelled["version"], "reason": "Installer and runner stopped locally and checked",
               "confirmation": prepared["host"]["fqdn"], "execution_stopped": True}
    endpoint = f"/api/v1/runs/{run_id}/reconcile"
    assert client.post(endpoint, json=request, headers=csrf).status_code == 409
    with prepared["app"].state.db.connection(write=True) as connection:
        connection.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, run_id))
    assert client.post(endpoint, json={**request, "confirmation": "wrong.lab.example.net"}, headers=csrf).status_code == 422
    assert client.post(endpoint, json={**request, "execution_stopped": False}, headers=csrf).status_code == 422
    reconciled = post(client, endpoint, request, csrf)
    assert reconciled["status"] == "cancelled"
    with prepared["app"].state.db.connection() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row["device_key"] is None and row["enrollment_hash"] is None
        assert connection.execute("SELECT status FROM approvals WHERE id=?", (row["approval_id"],)).fetchone()[0] == "revoked"
        assert connection.execute("SELECT 1 FROM audit WHERE action='run.reconciled' AND object_id=?", (run_id,)).fetchone()
    assert signed(prepared, key, "POST", "/agent/v1/lease", {"run_id": run_id}).status_code == 401
    host = client.get(f"/api/v1/hosts/{prepared['host']['id']}").json()
    next_run = post(client, f"/api/v1/hosts/{host['id']}/approve-install", {
        "expected_version": host["version"], "valid_minutes": 30, "confirmation": host["fqdn"],
        "disks_confirmed": True, "reason": "Explicit new simulation after verified local stop"}, csrf)
    assert next_run["id"] != run_id and next_run["status"] == "prepared"


def test_sensitive_values_are_encrypted_and_absent_from_management(prepared):
    response = answer(prepared)
    assert response.status_code == 200, response.text
    bootstrap_url = tomllib.loads(response.text)["first-boot"]["url"]
    for endpoint in ("/api/v1/hosts", "/api/v1/profiles", "/api/v1/runs", "/api/v1/audit", "/api/v1/groups"):
        management = prepared["client"].get(endpoint)
        assert management.status_code == 200, management.text
        assert ROOT_HASH not in management.text
        assert prepared["group"]["token"] not in management.text
        assert bootstrap_url not in management.text
    with prepared["app"].state.db.connection() as connection:
        row = connection.execute("SELECT * FROM runs").fetchone()
        assert ROOT_HASH not in row["answer_ciphertext"]
        assert ROOT_HASH in prepared["app"].state.security.decrypt(row["answer_ciphertext"])
        secret = connection.execute("SELECT ciphertext FROM secrets").fetchone()[0]
        assert secret != ROOT_HASH
        assert prepared["app"].state.security.decrypt(secret) == ROOT_HASH


def test_live_backup_offline_restore_revokes_all_active_credentials(prepared, tmp_path):
    enroll(prepared)
    settings = prepared["app"].state.settings
    destination = tmp_path / "snapshot"
    backup(settings, destination)
    key_bytes = settings.master_key_file.read_bytes()
    for path in destination.rglob("*"):
        if path.is_file():
            assert key_bytes not in path.read_bytes()
    restored = replace(settings, data_dir=tmp_path / "restored")
    restore(restored, destination)
    db = Database(restored)
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM groups WHERE revoked=0").fetchone()[0] == 0
        row = connection.execute("SELECT * FROM runs").fetchone()
        assert row["status"] == "needs_review"
        assert all(row[name] is None for name in ("device_key", "bootstrap_hash", "enrollment_hash", "report_hash", "answer_ciphertext"))
        assert Security(restored).decrypt(connection.execute("SELECT ciphertext FROM secrets").fetchone()[0]) == ROOT_HASH
    assert list((restored.data_dir / "artifacts").iterdir())
    with pytest.raises((RuntimeError, ValueError), match="in use|empty"):
        restore(settings, destination)
    restored_app = create_app(restored)
    with TestClient(restored_app, base_url="https://testserver") as restored_client:
        restored_csrf = login(restored_client)
        current = restored_client.get(f"/api/v1/runs/{prepared['run']['id']}").json()
        reconciled = post(restored_client, f"/api/v1/runs/{current['id']}/reconcile", {
            "expected_version": current["version"], "confirmation": prepared["host"]["fqdn"],
            "execution_stopped": True, "reason": "Physical host state checked after restore"}, restored_csrf)
        assert reconciled["status"] == "cancelled"


def test_init_creates_only_password_hash_and_separate_key(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data", master_key_file=tmp_path / "keys" / "master.key")
    monkeypatch.setattr("provisioner.cli.getpass.getpass", lambda _: PASSWORD)
    initialize(settings, "admin")
    with Database(settings).connection() as connection:
        stored = connection.execute("SELECT password_hash FROM users").fetchone()[0]
    assert PASSWORD not in stored
    assert Security.verify_password(PASSWORD, stored)
    assert settings.master_key_file.is_file()
    with ServiceLock(settings.data_dir):
        with pytest.raises(RuntimeError, match="in use"):
            with ServiceLock(settings.data_dir):
                pass


def test_restore_rejects_wrong_key_and_modified_backup(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "original", master_key_file=tmp_path / "keys" / "master.key")
    monkeypatch.setattr("provisioner.cli.getpass.getpass", lambda _: PASSWORD)
    initialize(settings, "admin")
    destination = tmp_path / "backup"
    backup(settings, destination)
    wrong_key = tmp_path / "keys" / "wrong.key"
    wrong_key.write_bytes(Fernet.generate_key())
    wrong_settings = replace(settings, data_dir=tmp_path / "wrong-restore", master_key_file=wrong_key)
    with pytest.raises(ValueError, match="does not match"):
        restore(wrong_settings, destination)
    assert not wrong_settings.data_dir.exists()
    config = destination / "settings.json"
    config.write_text(config.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore(replace(settings, data_dir=tmp_path / "corrupt-restore"), destination)
