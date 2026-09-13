"""Runner recovery tests use mocked phases: no provisioning code runs on this host."""
import base64
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from unittest.mock import Mock

import pytest

from provisioner.bootstrap import render_bootstrap
from provisioner.builtin_modules import catalog
from provisioner.runner import API, DeviceKey, Halt, RebootRequested, Runner, TransportError, canonical_json, discover_identities


SOURCE = b"#!/bin/bash\nexit 0\n"
DIGEST = hashlib.sha256(SOURCE).hexdigest()


class FakeAPI:
    def __init__(self, manifest):
        self.manifest = manifest
        self.artifact = SOURCE
        self.events = []
        self.logs = []
        self.secrets = {"password": "secret-value-do-not-log"}
        self.calls = []
        self.action = "run"
        self.version = 1
        self.offline = False
        self.drop_completion = False

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if self.offline:
            raise TransportError("offline")
        if path.endswith("/lease"):
            return {"action": self.action, "expires_at": time.time() + 900, "run_version": self.version}
        if path.endswith("/manifest"):
            return self.manifest
        if "/artifacts/" in path:
            return self.artifact
        if "/secrets/" in path:
            return self.secrets
        if path.endswith("/events"):
            self.events.extend(payload["events"])
            return {"ack_sequence": payload["events"][-1]["sequence"]}
        if path.endswith("/logs"):
            self.logs.extend(payload["chunks"])
            return {"ack_sequence": payload["chunks"][-1]["sequence"]}
        if path.endswith("/complete"):
            if self.drop_completion:
                self.drop_completion = False
                raise TransportError("completion response lost")
            return {"status": "succeeded"}
        raise AssertionError(path)


@pytest.fixture
def setup_runner(tmp_path):
    step = {"id": "example", "name": "Example", "digest": DIGEST, "parameters": {},
            "timeout_seconds": 60, "retry_safe": False, "dependencies": [], "required": True}
    manifest = {"run_id": "run-example", "steps": [step], "reboot_budget": 1}
    config = {"api_url": "https://provision.example.test", "run_id": manifest["run_id"],
              "enrollment_secret": "enrollment-only", "identities": [],
              "manifest_digest": hashlib.sha256(canonical_json(manifest)).hexdigest()}
    api = FakeAPI(manifest)
    runner = Runner(config, tmp_path, api)
    runner.state["enrolled"] = True
    runner.save()
    return runner, api, step


def test_digest_mismatch_never_executes(setup_runner):
    runner, api, step = setup_runner
    api.artifact = b"tampered"
    runner.execute = Mock()
    assert runner.run() == 75
    assert runner.state["status"] == "needs_review"
    assert "digest mismatch" in runner.state["reason"]
    runner.execute.assert_not_called()


def test_cached_artifact_is_revalidated_before_phase(setup_runner):
    runner, api, step = setup_runner
    path = runner.artifact(step)
    path.write_bytes(b"corrupted cached content")
    with pytest.raises(Halt, match="digest mismatch"):
        runner.execute(step, "check", path, runner.directory / "unused-parameters.json")


def test_apply_checkpoint_is_durable_before_mutation(setup_runner):
    runner, api, step = setup_runner
    phases = []
    def execute(step, phase, artifact, parameters):
        phases.append(phase)
        if phase == "apply":
            state = json.loads(runner.state_path.read_text())
            assert state["steps"][step["id"]]["status"] == "applying"
            assert api.events[-1]["type"] == "step.started"
            assert json.loads(parameters.read_text())["secrets"] == api.secrets
        return 1 if phase == "check" else 0
    runner.execute = execute
    assert runner.run() == 0
    assert phases == ["check", "apply", "verify"]
    assert runner.state["status"] == "succeeded"
    assert not (runner.directory / "step-parameters.json").exists()
    assert "secret-value" not in runner.state_path.read_text()


def test_interrupted_non_repeatable_step_requires_review(setup_runner):
    runner, api, step = setup_runner
    runner.state["steps"]["example"] = {"status": "applying", "attempt": 1}
    runner.execute = Mock(side_effect=[1, 1])
    runner.run()
    assert [call.args[1] for call in runner.execute.call_args_list] == ["check", "verify"]
    assert runner.state["status"] == "needs_review"
    assert "cannot be repeated safely" in runner.state["reason"]


def test_interrupted_converged_step_is_verified_without_apply(setup_runner):
    runner, api, step = setup_runner
    runner.state["steps"]["example"] = {"status": "applying", "attempt": 1}
    runner.execute = Mock(return_value=0)
    runner.run()
    assert [call.args[1] for call in runner.execute.call_args_list] == ["check", "verify"]
    assert runner.state["status"] == "succeeded"


def test_retry_safe_interrupted_step_can_reapply(setup_runner):
    runner, api, step = setup_runner
    step["retry_safe"] = True
    runner.state["steps"]["example"] = {"status": "applying", "attempt": 1}
    runner.execute = Mock(side_effect=[1, 1, 0, 0])
    runner.run_step(step, 1)
    assert [call.args[1] for call in runner.execute.call_args_list] == ["check", "verify", "apply", "verify"]
    assert runner.state["steps"]["example"]["status"] == "succeeded"
    assert runner.state["steps"]["example"]["attempt"] == 2


def test_success_exit_without_verification_is_not_success(setup_runner):
    runner, api, step = setup_runner
    runner.execute = Mock(side_effect=[1, 0, 2])
    runner.run()
    assert runner.state["status"] == "needs_review"
    assert runner.state["steps"]["example"]["status"] == "failed"
    assert not any(path.endswith("/complete") for _, path, _ in api.calls)


def test_terminal_response_loss_retries_completion_without_module_execution(setup_runner):
    runner, api, step = setup_runner
    runner.execute = Mock(return_value=0)
    api.drop_completion = True
    assert runner.run() == 75
    assert runner.state["status"] == "completion_pending"
    restarted = Runner(runner.config, runner.directory, api)
    restarted.execute = Mock()
    assert restarted.run() == 0
    restarted.execute.assert_not_called()
    assert restarted.state["status"] == "succeeded"
    assert len([path for _, path, _ in api.calls if path.endswith("/complete")]) == 2


def test_event_queue_survives_network_loss_and_acknowledges(setup_runner):
    runner, api, step = setup_runner
    runner.event("step.started", "example")
    api.offline = True
    with pytest.raises(TransportError):
        runner.flush()
    restarted = Runner(runner.config, runner.directory, api)
    assert restarted.state["events"][0]["sequence"] == 1
    api.offline = False
    restarted.flush()
    assert restarted.state["events"] == []
    assert api.events[0]["sequence"] == 1


def test_reboot_checkpoint_waits_for_changed_boot_id(setup_runner):
    runner, api, step = setup_runner
    runner.execute = Mock(side_effect=[1, 194])
    assert runner.run() == 194
    assert runner.state["status"] == "reboot_pending"
    restarted = Runner(runner.config, runner.directory, api)
    restarted.execute = Mock(return_value=0)
    assert restarted.run() == 194
    restarted.execute.assert_not_called()
    restarted.boot_id = "next-boot"
    assert restarted.run() == 0
    assert [call.args[1] for call in restarted.execute.call_args_list] == ["check", "verify"]
    assert any(event["type"] == "run.resumed" for event in api.events)


def test_reboot_budget_cannot_be_exceeded(setup_runner):
    runner, api, step = setup_runner
    runner.execute = Mock(side_effect=[1, 194])
    runner.state["reboot_count"] = 1
    with pytest.raises(Halt, match="budget exhausted"):
        runner.run_step(step, 1)


def test_review_requires_explicit_server_resume_version(setup_runner):
    runner, api, step = setup_runner
    runner.state.update(status="needs_review", halted_version=1, review_started_at=time.time())
    runner.execute = Mock(return_value=0)
    assert runner.run() == 75
    runner.execute.assert_not_called()
    api.version = 2
    assert runner.run() == 0
    assert runner.state["status"] == "succeeded"


def test_secret_redaction_happens_before_durable_log_write(setup_runner):
    runner, api, step = setup_runner
    runner.secret_values = ["super-secret", "secret"]
    runner.log("example", "output super-secret and secret")
    state = runner.state_path.read_text()
    assert "super-secret" not in state
    assert runner.state["logs"][0]["text"] == "output [REDACTED] and [REDACTED]"


def test_expired_lease_prevents_any_module_execution(setup_runner):
    runner, api, step = setup_runner
    api.action = "wait"
    runner.execute = Mock()
    assert runner.run() == 75
    runner.execute.assert_not_called()


def test_tls_cannot_be_disabled(setup_runner):
    runner, api, step = setup_runner
    with pytest.raises(Halt, match="HTTPS"):
        API({**runner.config, "api_url": "http://example.test"}, Mock())
    with pytest.raises(ValueError, match="HTTPS"):
        render_bootstrap({**runner.config, "api_url": "http://example.test"})


def test_bootstrap_is_self_contained_persistent_and_bounded(setup_runner):
    runner, api, step = setup_runner
    result = render_bootstrap({**runner.config, "ca_pem": "TEST CA"})
    assert len(result.encode()) < 1024 * 1024
    assert result.index("persist(etc / 'config.json'") < result.index("systemctl enable --now")
    embedded_python = result.split("<<'PVE_BOOTSTRAP_PY'\n", 1)[1].split("\nPVE_BOOTSTRAP_PY", 1)[0]
    compile(embedded_python, "bootstrap-embedded", "exec")
    assert "Restart=on-failure" in result
    assert "trusted-ca.pem" in result


def test_module_drafts_have_compilable_embedded_python_and_no_release_claims():
    modules = catalog()
    assert len(modules) == 8
    for module in modules:
        assert module["status"] == "draft"
        assert not module["test_evidence"] and not module["target_builds"]
        python = module["source"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(python, module["id"], "exec")
        assert 'case "${1:-}" in check|apply|verify)' in module["source"]


def test_identity_binding_uses_observed_target_data(tmp_path):
    dmi = tmp_path / "class/dmi/id"
    dmi.mkdir(parents=True)
    (dmi / "product_serial").write_text("SERIAL-123\n")
    observed = discover_identities([{"kind": "serial", "value": "Serial-123"}], tmp_path)
    assert observed == [{"kind": "serial", "value": "serial-123"}]
    with pytest.raises(Halt, match="do not match"):
        discover_identities([{"kind": "serial", "value": "different-host"}], tmp_path)


def test_logs_remain_bounded_with_contiguous_sequence(setup_runner):
    runner, api, step = setup_runner
    for index in range(12):
        runner.log("example", "x" * 131072)
    chunks = runner.state["logs"]
    assert sum(len(chunk["text"].encode()) for chunk in chunks) <= 1024 * 1024
    assert all(len(chunk["text"]) <= 16384 for chunk in chunks)
    assert [chunk["sequence"] for chunk in chunks] == list(range(1, len(chunks) + 1))
    runner.flush()
    assert not runner.state["logs"]


def test_openssl_device_key_signatures_and_key_reuse(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    git_bin = Path("C:/Program Files/Git/usr/bin")
    if not shutil.which("openssl") and (git_bin / "openssl.exe").exists():
        monkeypatch.setenv("PATH", str(git_bin) + os.pathsep + os.environ["PATH"])
    if not shutil.which("openssl"):
        pytest.skip("OpenSSL is unavailable on this test workstation")
    key = DeviceKey(tmp_path)
    key.ensure()
    public_key = key.public_key
    body = canonical_json({"run_id": "run-test"})
    message = f"POST\n/agent/v1/lease\n1234567890\nonce-123456789\n{hashlib.sha256(body).hexdigest()}".encode()
    signature = base64.b64decode(key.sign(message))
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(signature, message)
    key.ensure()
    assert key.public_key == public_key
    if os.name == "posix":
        assert key.path.stat().st_mode & 0o777 == 0o600


def test_bash_syntax_without_executing_provisioning_modules(setup_runner):
    runner, api, step = setup_runner
    git_bash = Path("C:/Program Files/Git/usr/bin/bash.exe")
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("Bash parser unavailable")
    sources = [module["source"] for module in catalog()] + [render_bootstrap(runner.config)]
    for source in sources:
        result = subprocess.run([bash, "-n"], input=source, text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr


def module_helper(module_id, function_name, namespace=None):
    """Load one pure/helper function without executing the module's host actions."""
    source = next(module["source"] for module in catalog() if module["id"] == module_id)
    python = source.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    function = next(node for node in ast.parse(python).body if isinstance(node, ast.FunctionDef) and node.name == function_name)
    scope = {} if namespace is None else namespace
    exec(compile(ast.Module(body=[function], type_ignores=[]), module_id, "exec"), scope)
    return scope[function_name]


def test_ssh_rejects_truncated_public_key_before_writing_accounts(monkeypatch):
    git_bin = Path("C:/Program Files/Git/usr/bin")
    if not shutil.which("ssh-keygen") and (git_bin / "ssh-keygen.exe").exists():
        monkeypatch.setenv("PATH", str(git_bin) + os.pathsep + os.environ["PATH"])
    if not shutil.which("ssh-keygen"):
        pytest.skip("OpenSSH public-key validator unavailable")
    validate = module_helper("ssh", "validate_public_key", {"base64": base64, "os": os,
        "subprocess": subprocess, "tempfile": tempfile})
    malformed = base64.b64encode((11).to_bytes(4, "big") + b"ssh-ed25519" + b"x").decode()
    with pytest.raises(SystemExit, match="OpenSSH rejected"):
        validate("ssh-ed25519 " + malformed)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    valid = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
    assert validate(valid) is None


def test_repository_verification_rejects_stale_other_suite_indexes():
    verify = module_helper("repositories", "has_repository_indexes")
    policy = " 500 https://repo.example.test/debian bookworm/main amd64 Packages\n"
    assert verify(policy, "https://repo.example.test/debian", "bookworm", ["main"])
    assert not verify(policy, "https://repo.example.test/debian", "trixie", ["main"])
    assert not verify(policy, "https://repo.example.test/debian", "bookworm", ["main", "contrib"])
    assert not verify(policy, "https://repo.example.test/deb", "bookworm", ["main"])


def test_pending_reboot_waits_for_permission(setup_runner):
    runner, api, step = setup_runner
    runner.state.update(status="reboot_pending", reboot_boot_id=runner.boot_id)
    api.action = "wait"
    assert runner.run() == 75
    assert runner.state["status"] == "reboot_pending"


def test_review_deadline_stops_even_when_authorization_is_unavailable(setup_runner):
    runner, api, step = setup_runner
    runner.state.update(status="needs_review", review_started_at=time.time() - 86401)
    api.offline = True
    assert runner.run() == 0
    assert api.calls == []


@pytest.mark.skipif(os.name != "posix", reason="Requires native Linux subprocess supervision")
def test_native_phase_obeys_shared_timeout(setup_runner):
    runner, api, step = setup_runner
    api.artifact = b"#!/bin/bash\nprintf 'timeout-probe\\n'\nsleep 30\n"
    step["digest"] = hashlib.sha256(api.artifact).hexdigest()
    runner.heartbeat = Mock()
    artifact = runner.artifact(step)
    runner.step_deadline = time.monotonic() + 1
    before = time.monotonic()
    result = runner.execute(step, "apply", artifact, runner.directory / "unused.json")
    assert result == 124
    assert time.monotonic() - before < 5
    assert "timeout-probe" in runner.state["logs"][0]["text"]


@pytest.mark.skipif(os.name != "posix", reason="Requires native Linux subprocess supervision")
def test_native_output_redacts_secret_crossing_capture_boundary(setup_runner):
    runner, api, step = setup_runner
    secret = "sensitive-value-across-output-boundary"
    payload = "x" * (131072 - 5) + secret
    api.artifact = ("#!/bin/bash\nprintf '%s' '" + payload + "'\n").encode()
    step["digest"] = hashlib.sha256(api.artifact).hexdigest()
    runner.secret_values = [secret]
    runner.heartbeat = Mock()
    result = runner.execute(step, "check", runner.artifact(step), runner.directory / "unused.json")
    assert result == 0
    assert "sensi" not in "".join(chunk["text"] for chunk in runner.state["logs"])
