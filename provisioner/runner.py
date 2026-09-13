#!/usr/bin/env python3
"""Short-lived Linux provisioning runner. Target dependencies: Python 3, Bash, OpenSSL.

Module contract: check=0 means converged, check=1 means apply is needed; all other
check exits fail. Every successful apply is followed by verify. Exit 194 from
apply requests a checkpointed reboot. Module scripts must not reboot themselves.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import selectors
import signal
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


MAX_ARTIFACT = 2 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
MAX_PHASE_OUTPUT = 128 * 1024
MAX_EVENTS = 4096
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
STEP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode) if hasattr(os, "fchmod") else None
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            parent_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verified_digest(content, expected):
    if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
        raise Halt("Invalid artifact digest")
    if hashlib.sha256(content).hexdigest() != expected:
        raise Halt("Artifact digest mismatch; execution refused")
    return content


def discover_identities(expected, sys_root=Path("/sys")):
    """Check bootstrap host binding against identities actually observed on target."""
    def normalize(kind, value):
        value = value.strip().lower()
        if kind == "uuid":
            parsed = uuid.UUID(value)
            if parsed.int in (0, 2 ** 128 - 1):
                raise ValueError("Empty UUID")
            return str(parsed)
        if kind == "mac":
            value = value.replace("-", ":")
            if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value) or value in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                raise ValueError("Invalid MAC")
        if not value or value in ("unknown", "none", "not specified", "default string", "to be filled by o.e.m."):
            raise ValueError("Placeholder identity")
        return value
    observed = set()
    candidates = [("uuid", sys_root / "class/dmi/id/product_uuid"),
                  ("serial", sys_root / "class/dmi/id/product_serial")]
    candidates.extend(("mac", path) for path in (sys_root / "class/net").glob("*/address"))
    for kind, path in candidates:
        try:
            observed.add((kind, normalize(kind, path.read_text())))
        except (OSError, ValueError):
            continue
    try:
        required = {(item["kind"], normalize(item["kind"], item["value"])) for item in expected}
    except (KeyError, ValueError) as exc:
        raise Halt("Invalid expected host identity") from exc
    if not required or not required.issubset(observed):
        raise Halt("Observed target identities do not match the bootstrap host binding")
    return [{"kind": kind, "value": value} for kind, value in sorted(required)]


class TransportError(Exception):
    pass


class Rejected(Exception):
    pass


class Halt(Exception):
    pass


class Deferred(Exception):
    pass


class RebootRequested(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("API redirects are not permitted")


class DeviceKey:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "device-key.pem"

    def ensure(self):
        if not self.path.exists():
            result = subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519"],
                                    capture_output=True, check=True, timeout=15)
            atomic_write(self.path, result.stdout)
        os.chmod(self.path, 0o600)

    @property
    def public_key(self):
        result = subprocess.run(["openssl", "pkey", "-in", str(self.path), "-pubout",
                                 "-outform", "DER"], capture_output=True, check=True, timeout=15)
        # RFC 8410 Ed25519 SubjectPublicKeyInfo: fixed 12-byte prefix + raw key.
        if not result.stdout.startswith(bytes.fromhex("302a300506032b6570032100")) or len(result.stdout) != 44:
            raise Halt("Device key is not Ed25519")
        return base64.b64encode(result.stdout[12:]).decode()

    def sign(self, content):
        fd, path = tempfile.mkstemp(prefix=".signature-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            result = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey",
                                     str(self.path), "-in", path], capture_output=True,
                                    check=True, timeout=15)
            return base64.b64encode(result.stdout).decode()
        finally:
            os.unlink(path)


class API:
    def __init__(self, config, key):
        self.config, self.key = config, key
        self.base = config["api_url"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Halt("api_url must be an HTTPS origin with normal certificate validation")
        context = ssl.create_default_context(cafile=config.get("ca_file"))
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())

    def request(self, method, path, payload=None, *, signed=True, raw=False, attempts=5):
        body = b"" if payload is None else canonical_json(payload)
        if len(body) > MAX_RESPONSE:
            raise Halt("Outgoing request exceeds size limit")
        url = self.base + path
        for attempt in range(attempts):
            headers = {"Accept": "application/octet-stream" if raw else "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            if signed:
                timestamp, nonce = str(int(time.time())), os.urandom(24).hex()
                request_path = urllib.parse.urlsplit(url).path
                message = f"{method}\n{request_path}\n{timestamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
                headers.update({"X-Run-ID": self.config["run_id"], "X-Device-Key": self.key.public_key,
                                "X-Timestamp": timestamp, "X-Nonce": nonce,
                                "X-Signature": self.key.sign(message)})
            request = urllib.request.Request(url, data=body if payload is not None else None,
                                             method=method, headers=headers)
            try:
                with self.opener.open(request, timeout=10) as response:
                    result = response.read((MAX_ARTIFACT if raw else MAX_RESPONSE) + 1)
                if len(result) > (MAX_ARTIFACT if raw else MAX_RESPONSE):
                    raise Halt("Response exceeds size limit")
                if raw:
                    return result
                try:
                    return json.loads(result)
                except (ValueError, UnicodeError) as exc:
                    raise Halt("Invalid API response") from exc
            except urllib.error.HTTPError as exc:
                if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                    raise Rejected(f"API rejected {method} {path}: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            if attempt + 1 < attempts:
                time.sleep(min(20, 2 ** attempt) + random.random())
        raise TransportError("API unavailable after bounded retries")


class Runner:
    def __init__(self, config, directory="/var/lib/pve-provisioner", api=None):
        self.config = config
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.directory / "state.json"
        self.key = DeviceKey(self.directory)
        self.api = api
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip() if os.name == "posix" else "test-boot"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "format": 1, "run_id": config["run_id"], "status": "pending", "steps": {},
            "events": [], "logs": [], "event_sequence": 0, "log_sequence": 0,
            "reboot_count": 0, "boot_id": self.boot_id,
        }
        if self.state["run_id"] != config["run_id"]:
            raise Halt("Existing local state belongs to another run")
        self.lease = {"action": "wait", "expires_at": 0, "run_version": 0}
        self.last_heartbeat = 0
        self.secret_values = []
        self.stop_requested = False
        self.step_deadline = None
        self.save()

    def save(self):
        atomic_write(self.state_path, canonical_json(self.state))

    def event(self, event_type, step_id=None, **fields):
        if len(self.state["events"]) >= MAX_EVENTS:
            # Reserve a durable halt locally; never evict unacknowledged events.
            self.state.update(status="needs_review", reason="Event queue limit reached")
            self.save()
            raise Halt("Event queue limit reached")
        self.state["event_sequence"] += 1
        self.state["events"].append({"sequence": self.state["event_sequence"], "boot_id": self.boot_id,
                                     "step_id": step_id, "type": event_type,
                                     "occurred_at": datetime.now(timezone.utc).isoformat(), **fields})
        self.save()

    def log(self, step_id, content):
        for value in sorted(self.secret_values, key=len, reverse=True):
            if value:
                content = content.replace(value, "[REDACTED]")
        content = content[:MAX_PHASE_OUTPUT]
        remaining = MAX_LOG_BYTES - sum(len(chunk["text"].encode()) for chunk in self.state["logs"])
        encoded = content.encode()
        if len(encoded) > remaining:
            content = encoded[:max(0, remaining)].decode(errors="ignore")
            self.state["logs_truncated"] = True
        # Drop newly arriving overflow, never evict a sequenced/unacknowledged
        # chunk: server acknowledgements require a contiguous sequence.
        for position in range(0, len(content), 16000):
            self.state["log_sequence"] += 1
            self.state["logs"].append({"sequence": self.state["log_sequence"], "step_id": step_id,
                                       "text": content[position:position + 16000]})
        self.save()

    def flush(self):
        base = f"/agent/v1/runs/{self.config['run_id']}"
        for queue, endpoint, key in (("logs", "logs", "chunks"), ("events", "events", "events")):
            while self.state[queue]:
                batch = self.state[queue][:100 if queue == "events" else 8]
                response = self.api.request("POST", f"{base}/{endpoint}", {key: batch}, attempts=1)
                ack = response.get("ack_sequence")
                if not isinstance(ack, int) or ack < batch[0]["sequence"] or ack > self.state["event_sequence" if queue == "events" else "log_sequence"]:
                    raise Halt("Invalid event/log acknowledgement")
                self.state[queue] = [event for event in self.state[queue] if event["sequence"] > ack]
                self.save()

    def network_failure(self):
        self.state.setdefault("network_failed_since", time.time())
        self.save()
        if time.time() - self.state["network_failed_since"] > int(self.config.get("network_deadline_seconds", 1800)):
            raise Halt("Network recovery deadline exceeded")

    def renew_lease(self):
        self.lease = self.api.request("POST", "/agent/v1/lease", {"run_id": self.config["run_id"]}, attempts=1)
        if self.lease.get("action") not in ("run", "wait", "stop", "revoked"):
            raise Halt("Invalid lease action")
        self.state["run_version"] = self.lease.get("run_version", 0)
        self.state.pop("network_failed_since", None)
        self.save()

    def authorize(self):
        if self.stop_requested:
            raise Deferred("Runner service is stopping")
        self.renew_lease()
        if self.lease["action"] == "stop":
            self.cancel()
            raise Deferred("Run cancelled")
        if self.lease["action"] == "revoked":
            raise Halt("Execution permission revoked or stopped")
        if self.lease["action"] != "run" or self.lease.get("expires_at", 0) <= time.time():
            raise Deferred("Waiting for execution permission")

    def heartbeat(self, step_id):
        if time.monotonic() - self.last_heartbeat < 30:
            return
        self.last_heartbeat = time.monotonic()
        try:
            self.renew_lease()
            self.event("heartbeat", step_id)
            self.flush()
        except TransportError:
            self.state.setdefault("network_failed_since", time.time())
            self.save()
        except Rejected:
            # Finish the running safe phase; authorize() prevents another apply.
            self.lease = {"action": "revoked", "expires_at": 0}

    def manifest(self):
        path = self.directory / "manifest.json"
        if path.exists():
            manifest = json.loads(path.read_text())
        else:
            manifest = self.api.request("GET", f"/agent/v1/runs/{self.config['run_id']}/manifest")
        claimed = manifest.get("digest")
        unsigned = {key: value for key, value in manifest.items() if key != "digest"}
        digest = hashlib.sha256(canonical_json(unsigned)).hexdigest()
        expected = self.config.get("manifest_digest") or self.state.get("manifest_digest") or claimed
        if not expected or digest != expected or (claimed and claimed != digest):
            raise Halt("Manifest digest mismatch")
        if manifest.get("run_id") != self.config["run_id"]:
            raise Halt("Manifest belongs to another run")
        steps = manifest.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise Halt("Manifest must contain 1 to 100 steps")
        seen = set()
        for step in steps:
            if not STEP_ID.fullmatch(step.get("id", "")) or step["id"] in seen:
                raise Halt("Invalid or duplicate step identifier")
            if not DIGEST.fullmatch(step.get("digest", "")) or not isinstance(step.get("parameters", {}), dict):
                raise Halt("Invalid module digest or parameters")
            if not isinstance(step.get("timeout_seconds", 600), int) or not 1 <= step.get("timeout_seconds", 600) <= 14400:
                raise Halt("Invalid module timeout")
            if not set(step.get("dependencies", [])).issubset(seen):
                raise Halt("Module dependencies must precede their dependants")
            seen.add(step["id"])
        self.state["manifest_digest"] = digest
        atomic_write(path, canonical_json(manifest))
        self.save()
        return manifest

    def artifact(self, step):
        digest = step["digest"]
        if not DIGEST.fullmatch(digest):
            raise Halt("Invalid artifact digest")
        path = self.directory / "artifacts" / digest
        content = path.read_bytes() if path.exists() else self.api.request("GET", f"/agent/v1/artifacts/{digest}", raw=True)
        if len(content) > MAX_ARTIFACT:
            raise Halt("Artifact exceeds size limit")
        verified_digest(content, digest)
        if not path.exists():
            atomic_write(path, content)
        return path

    def execute(self, step, phase, artifact, parameters):
        # Verify again immediately before *every* execution, including check/verify.
        verified_digest(Path(artifact).read_bytes(), step["digest"])
        deadline = self.step_deadline or (time.monotonic() + step.get("timeout_seconds", 600))
        if time.monotonic() >= deadline:
            self.log(step["id"], f"[{phase}] Step timeout reached before phase start")
            return 124
        process = subprocess.Popen(["/bin/bash", str(artifact), phase, str(parameters)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True, cwd=self.directory,
                                   env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
                                        "DEBIAN_FRONTEND": "noninteractive", "PVE_RUN_ID": self.config["run_id"]})
        output = bytearray()
        capture_limit = MAX_PHASE_OUTPUT + max((len(value.encode()) for value in self.secret_values), default=0)
        timed_out = False
        poller = selectors.DefaultSelector()
        poller.register(process.stdout, selectors.EVENT_READ)
        try:
            while process.poll() is None or poller.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    break
                for key, _ in poller.select(timeout=0.25):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        poller.unregister(key.fileobj)
                    elif len(output) < capture_limit:
                        output.extend(chunk[:capture_limit - len(output)])
                # Continued lease renewal never interrupts a package operation.
                self.heartbeat(step["id"])
            process.wait(timeout=5)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise
        finally:
            poller.close()
            process.stdout.close()
        text = output.decode("utf-8", errors="replace")
        if len(output) >= MAX_PHASE_OUTPUT:
            text += "\n[output truncated]"
        self.log(step["id"], f"[{phase}]\n{text}")
        return 124 if timed_out else process.returncode

    def run_step(self, step, reboot_budget):
        step_id = step["id"]
        checkpoint = self.state["steps"].get(step_id, {})
        if checkpoint.get("status") == "succeeded":
            return
        if checkpoint.get("status") == "failed":
            if step.get("required", True):
                raise Halt(f"Step {step_id} previously failed; explicit review is required")
            return
        self.authorize()
        self.flush()
        artifact = self.artifact(step)
        secrets = self.api.request("GET", f"/agent/v1/runs/{self.config['run_id']}/secrets/{step_id}")
        if not isinstance(secrets, dict):
            raise Halt("Invalid step secret response")
        def secret_strings(value):
            if isinstance(value, dict):
                return [item for child in value.values() for item in secret_strings(child)]
            if isinstance(value, list):
                return [item for child in value for item in secret_strings(child)]
            return [str(value)] if value is not None else []
        self.secret_values = secret_strings(secrets)
        if any(len(value.encode()) > 16384 for value in self.secret_values):
            raise Halt("Step secret exceeds the supported redaction limit")
        parameters = self.directory / "step-parameters.json"
        atomic_write(parameters, canonical_json({**step.get("parameters", {}), "secrets": secrets}))
        self.step_deadline = time.monotonic() + step.get("timeout_seconds", 600)
        try:
            self.event("step.started", step_id)
            self.flush()  # Server knows the attempt before any module phase.
            check = self.execute(step, "check", artifact, parameters)
            recovering = checkpoint.get("status") in ("applying", "reboot_pending")
            if recovering:
                verified = self.execute(step, "verify", artifact, parameters)
                if check == 0 and verified == 0:
                    self.succeed(step_id, recovered=True)
                    return
                if not step.get("retry_safe", False):
                    raise Halt(f"Interrupted step {step_id} cannot be repeated safely")
                if check == 0 and verified != 0:
                    check = 1
            if check not in (0, 1):
                self.fail(step, check, "check failed")
                return
            if check == 0:
                verified = self.execute(step, "verify", artifact, parameters)
                if verified == 0:
                    self.succeed(step_id, unchanged=True)
                    return
                # A converged check with a failing verify is an inconsistent module.
                self.fail(step, verified, "verification failed")
                return
            self.authorize()
            self.state["steps"][step_id] = {"status": "applying", "attempt": checkpoint.get("attempt", 0) + 1}
            self.state["status"] = "running"
            self.save()  # Durable checkpoint before any mutation.
            code = self.execute(step, "apply", artifact, parameters)
            if code == 194:
                if self.state["reboot_count"] >= reboot_budget:
                    raise Halt("Reboot budget exhausted")
                self.state["reboot_count"] += 1
                self.state["status"] = "reboot_pending"
                self.state["reboot_boot_id"] = self.boot_id
                self.state["steps"][step_id]["status"] = "reboot_pending"
                self.save()
                self.event("run.reboot_pending", step_id)
                try:
                    self.flush()
                except (TransportError, Rejected):
                    pass
                self.authorize()
                raise RebootRequested()
            if code != 0:
                self.fail(step, code, "apply failed")
                return
            verified = self.execute(step, "verify", artifact, parameters)
            if verified != 0:
                self.fail(step, verified, "verification failed")
                return
            self.succeed(step_id)
        finally:
            parameters.unlink(missing_ok=True)
            self.secret_values = []
            self.step_deadline = None

    def succeed(self, step_id, **verification):
        self.state["steps"].setdefault(step_id, {})["status"] = "succeeded"
        self.state["steps"][step_id]["verification"] = {"passed": True, **verification}
        self.event("step.succeeded", step_id, exit_code=0, verification={"passed": True, **verification})

    def fail(self, step, code, reason):
        self.state["steps"].setdefault(step["id"], {}).update(status="failed", exit_code=code)
        self.event("step.failed", step["id"], exit_code=code, verification={"passed": False, "reason": reason})
        if step.get("required", True):
            raise Halt(f"Required step {step['id']}: {reason} (exit {code})")

    def halt(self, reason):
        self.state.update(status="needs_review", reason=reason, halted_version=self.state.get("run_version", 0))
        self.state.setdefault("review_started_at", time.time())
        self.save()
        if len(self.state["events"]) < MAX_EVENTS:
            self.event("run.needs_review", verification={"reason": reason})
        if self.api is None:
            return
        try:
            self.flush()
        except (TransportError, Rejected, Halt):
            pass

    def cancel(self):
        self.state["status"] = "cancellation_pending"
        self.event("run.cancelled", verification={"reason": "Operator cancelled at a safe transition"})
        self.flush()
        self.state["status"] = "cancelled"
        self.save()

    def review(self):
        if time.time() - self.state.get("review_started_at", time.time()) >= self.config.get("review_deadline_seconds", 86400):
            return False
        self.flush()
        self.renew_lease()
        if self.lease["action"] == "stop":
            self.cancel()
            return False
        if self.lease["action"] == "run" and self.lease.get("run_version", 0) > self.state.get("halted_version", 0):
            # Only an explicit server-side resume permits recovery. Failed steps
            # become interrupted steps and still pass check/verify + retry_safe.
            for checkpoint in self.state["steps"].values():
                if checkpoint.get("status") == "failed":
                    checkpoint["status"] = "applying"
            self.state.update(status="running")
            self.state.pop("review_started_at", None)
            self.state.pop("reason", None)
            self.event("run.resumed")
            return True
        raise Deferred("Awaiting explicit operator resume")

    def finish(self):
        self.flush()
        verification = {key: value.get("verification", {"passed": False}) for key, value in self.state["steps"].items()}
        response = self.api.request("POST", f"/agent/v1/runs/{self.config['run_id']}/complete", {"verification": verification})
        if response.get("status") != "succeeded":
            raise Halt("Completion was not acknowledged")
        self.state["status"] = "succeeded"
        self.save()

    def run(self):
        try:
            if self.state["status"] in ("succeeded", "cancelled"):
                return 0
            if self.api is None:
                self.key.ensure()
                self.api = API(self.config, self.key)
            if self.state["status"] == "cancellation_pending":
                self.flush()
                self.state["status"] = "cancelled"
                self.save()
                return 0
            if self.state["status"] == "needs_review":
                if not self.review():
                    return 0
            if self.state["status"] == "completion_pending":
                self.finish()
                return 0
            if not self.state.get("enrolled"):
                self.api.request("POST", "/agent/v1/enroll", {
                    "run_id": self.config["run_id"], "enrollment_secret": self.config["enrollment_secret"],
                    "public_key": self.key.public_key, "identities": discover_identities(self.config["identities"]),
                    "boot_id": self.boot_id}, signed=False)
                self.state["enrolled"] = True
                self.save()
            if self.state["status"] == "reboot_pending":
                if self.state.get("reboot_boot_id") == self.boot_id:
                    self.authorize()
                    raise RebootRequested()
                self.state.update(status="running", boot_id=self.boot_id)
                self.save()
                self.event("run.resumed")
                self.flush()
            self.authorize()
            manifest = self.manifest()
            self.flush()
            for step in manifest["steps"]:
                for dependency in step.get("dependencies", []):
                    if self.state["steps"].get(dependency, {}).get("status") != "succeeded":
                        raise Halt(f"Dependency {dependency} has not succeeded")
                self.run_step(step, int(manifest.get("reboot_budget", 1)))
                try:
                    self.flush()
                except TransportError:
                    self.network_failure()
            self.state["status"] = "completion_pending"
            self.save()
            self.finish()
            return 0
        except RebootRequested:
            return 194
        except (Halt, Rejected) as exc:
            self.halt(str(exc))
            return 75
        except Deferred:
            return 0 if self.state["status"] == "cancelled" else 75
        except TransportError:
            try:
                self.network_failure()
            except Halt as exc:
                self.halt(str(exc))
                return 75
            return 75


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/pve-provisioner/config.json")
    parser.add_argument("--state-dir", default="/var/lib/pve-provisioner")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("The runner requires a Linux target and root privileges")
    import fcntl
    os.umask(0o077)
    directory = Path(args.state_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        # Secrets from a power failure are removed before any recovery operation.
        (directory / "step-parameters.json").unlink(missing_ok=True)
        config = json.loads(Path(args.config).read_text())
        runner = Runner(config, directory)
        def request_stop(signum, frame):
            runner.stop_requested = True
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        result = runner.run()
        if runner.state.get("enrolled") and "enrollment_secret" in config:
            config.pop("enrollment_secret")
            atomic_write(args.config, canonical_json(config))
        if result == 194:
            subprocess.run(["systemctl", "reboot"], check=True, timeout=15)
            return 0
        if runner.state["status"] in ("succeeded", "cancelled"):
            subprocess.run(["systemctl", "disable", "pve-provisioner.service"], check=True, timeout=15)
        return result


if __name__ == "__main__":
    raise SystemExit(main())
