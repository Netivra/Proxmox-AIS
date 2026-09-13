"""Actual Runner/API protocol with Ed25519; only target-side phases are mocked."""
import base64
from copy import deepcopy
import io
import json
import re
import urllib.error
import urllib.parse
from unittest.mock import Mock
import tomllib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from provisioner.runner import API, Runner, TransportError
from test_acceptance import environment, prepared, answer, post, enroll, signed


class Key:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.public_key = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()

    def sign(self, message):
        return base64.b64encode(self.key.sign(message)).decode()


class TestOpener:
    __test__ = False

    def __init__(self, client):
        self.client = client
        self.drop_complete = False
        self.drop_cancel = False

    def open(self, request, timeout):
        path = urllib.parse.urlsplit(request.full_url).path
        result = self.client.request(request.method, path, content=request.data, headers=dict(request.header_items()))
        if result.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url,result.status_code,result.text,result.headers,io.BytesIO(result.content))
        if self.drop_complete and path.endswith("/complete"):
            self.drop_complete = False
            raise TransportError("Simulated lost completion acknowledgement")
        if self.drop_cancel and path.endswith("/events") and b"run.cancelled" in (request.data or b""):
            self.drop_cancel = False
            raise TransportError("Simulated lost cancellation acknowledgement")
        return io.BytesIO(result.content)


def make_runner(prepared,tmp_path,monkeypatch):
    response = answer(prepared)
    assert response.status_code == 200,response.text
    bootstrap = prepared["client"].get(tomllib.loads(response.text)["first-boot"]["url"]).text
    encoded = re.search(r"config = base64.b64decode\('([^']+)'\)",bootstrap).group(1)
    config = json.loads(base64.b64decode(encoded))
    key = Key()
    api = API(config,key)
    api.opener = TestOpener(prepared["client"])
    runner = Runner(config,tmp_path / "target",api)
    runner.key = key
    monkeypatch.setattr("provisioner.runner.discover_identities",lambda *args:prepared["identities"])
    return runner,api


@pytest.mark.parametrize("phases",[[0,0],[1,0,0]])
def test_actual_protocol_completes_verified_check_or_apply(prepared,tmp_path,monkeypatch,phases):
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    runner.execute = Mock(side_effect=phases)
    result = runner.run()
    assert result == 0,runner.state
    assert runner.state["status"] == "succeeded"
    detail = prepared["client"].get(f"/api/v1/runs/{runner.config['run_id']}").json()
    assert detail["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "succeeded"
    assert [item["type"] for item in detail["events"]] == ["step.started","step.succeeded"]


def test_runner_retries_lost_completion_without_running_scripts(prepared,tmp_path,monkeypatch):
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    runner.execute = Mock(return_value=0)
    api.opener.drop_complete = True
    assert runner.run() == 75,runner.state
    assert runner.state["status"] == "completion_pending"
    resumed = Runner(runner.config,runner.directory,api)
    resumed.execute = Mock()
    assert resumed.run() == 0,resumed.state
    resumed.execute.assert_not_called()


def test_actual_reboot_resumes_same_verified_step(prepared,tmp_path,monkeypatch):
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    runner.execute = Mock(side_effect=[1,194])
    assert runner.run() == 194,runner.state
    detail = prepared["client"].get(f"/api/v1/runs/{runner.config['run_id']}").json()
    assert detail["status"] == "reboot_pending"
    resumed = Runner(runner.config,runner.directory,api)
    resumed.boot_id = "new-boot-id"
    resumed.execute = Mock(return_value=0)
    assert resumed.run() == 0,resumed.state
    assert [call.args[1] for call in resumed.execute.call_args_list] == ["check","verify"]


def test_failed_verification_requires_explicit_operator_resume(prepared,tmp_path,monkeypatch):
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    runner.execute = Mock(side_effect=[1,0,2])
    assert runner.run() == 75,runner.state
    detail = prepared["client"].get(f"/api/v1/runs/{runner.config['run_id']}").json()
    assert detail["status"] == "needs_review"
    waiting = Runner(runner.config,runner.directory,api)
    waiting.execute = Mock()
    assert waiting.run() == 75,waiting.state
    waiting.execute.assert_not_called()
    detail = prepared["client"].get(f"/api/v1/runs/{runner.config['run_id']}").json()
    post(prepared["client"],f"/api/v1/runs/{runner.config['run_id']}/resume",{"expected_version":detail["version"],"reason":"Examined interrupted test step"},prepared["csrf"])
    resumed = Runner(runner.config,runner.directory,api)
    resumed.execute = Mock(return_value=0)
    assert resumed.run() == 0,resumed.state


def test_lost_cancellation_acknowledgement_is_idempotent(prepared,tmp_path,monkeypatch):
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    # Enrollment before cancellation, without executing any module.
    api.request("POST","/agent/v1/enroll",{"run_id":runner.config["run_id"],"enrollment_secret":runner.config["enrollment_secret"],"public_key":runner.key.public_key,"identities":prepared["identities"],"boot_id":runner.boot_id},signed=False)
    runner.state["enrolled"] = True
    runner.save()
    detail = prepared["client"].get(f"/api/v1/runs/{runner.config['run_id']}").json()
    post(prepared["client"],f"/api/v1/runs/{runner.config['run_id']}/cancel",{"expected_version":detail["version"],"reason":"Cancel test at safe boundary"},prepared["csrf"])
    api.opener.drop_cancel = True
    runner.execute = Mock()
    assert runner.run() == 75,runner.state
    resumed = Runner(runner.config,runner.directory,api)
    resumed.execute = Mock()
    assert resumed.run() == 0,resumed.state
    resumed.execute.assert_not_called()
    assert resumed.state["status"] == "cancelled"


def test_step_logs_redact_secrets_without_corrupting_json(prepared):
    key,_,_ = enroll(prepared)
    run_id = prepared["run"]["id"]
    from test_acceptance import ROOT_HASH
    result = signed(prepared,key,"POST",f"/agent/v1/runs/{run_id}/logs",{"chunks":[{"sequence":1,"step_id":"verify","text":f'password=abc\nquoted secret: "{ROOT_HASH}" token=xyz'}]})
    assert result.status_code == 200,result.text
    logs = prepared["client"].get(f"/api/v1/runs/{run_id}").json()["logs"]
    assert ROOT_HASH not in json.dumps(logs)
    assert "[REDACTED]" in logs[0]["text"]


def test_optional_failure_does_not_bypass_required_final_verification(prepared,tmp_path,monkeypatch):
    client,csrf = prepared["client"],prepared["csrf"]
    old = client.get(f"/api/v1/runs/{prepared['run']['id']}").json()
    post(client,f"/api/v1/runs/{old['id']}/cancel",{"expected_version":old["version"],"reason":"Replace prepared test profile"},csrf)
    profile = post(client,"/api/v1/profiles",{"name":"optional-then-required","kind":"postinstall","target_builds":["9.1-1"],"steps":[{"id":"optional","module_id":prepared["module"]["id"],"required":False},{"id":"final","module_id":prepared["module"]["id"],"required":True}]},csrf)
    post(client,f"/api/v1/profiles/{profile['id']}/publish",{"test_evidence":"Synthetic optional failure protocol test","reason":"Test full runner protocol"},csrf)
    host = client.get(f"/api/v1/hosts/{prepared['host']['id']}").json()
    updated = client.patch(f"/api/v1/hosts/{host['id']}",json={"expected_version":host["version"],"postinstall_profile_id":profile["id"]},headers=csrf)
    assert updated.status_code == 200,updated.text
    host = updated.json()
    prepared["run"] = post(client,f"/api/v1/hosts/{host['id']}/approve-install",{"expected_version":host["version"],"confirmation":host["fqdn"],"disks_confirmed":True,"reason":"Approve simulated optional failure run"},csrf)
    runner,api = make_runner(prepared,tmp_path,monkeypatch)
    runner.execute = Mock(side_effect=[2,0,0])
    assert runner.run() == 0,runner.state
    detail = client.get(f"/api/v1/runs/{runner.config['run_id']}").json()
    assert [(s["step_id"],s["status"]) for s in detail["steps"]] == [("optional","failed"),("final","succeeded")]
    assert detail["status"] == "succeeded"
