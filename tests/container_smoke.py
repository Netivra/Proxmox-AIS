"""Standalone container smoke: real TLS, login, persistence and restart.

Run inside the application image. All data lives in a temporary directory;
no installer, runner or provisioning module is executed.
"""
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from provisioner import cli
from provisioner.config import Settings


def main():
    assert os.getuid() == 10001, "Container must use its unprivileged service account"
    with tempfile.TemporaryDirectory(prefix="ais-smoke-") as directory:
        root = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port = sock.getsockname()[1]
        base = f"https://localhost:{port}"
        settings = Settings(data_dir=root / "data",master_key_file=root / "keys" / "master.key",public_url=base)
        password = secrets.token_urlsafe(24)
        cli.getpass.getpass = lambda _:password
        cli.initialize(settings,"smoke-admin")
        certificate,private = root / "tls.crt",root / "tls.key"
        subprocess.run(["openssl","req","-x509","-newkey","ed25519","-nodes","-keyout",str(private),"-out",str(certificate),"-days","1","-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost"],check=True,capture_output=True)
        environment = {**os.environ,"DATA_DIR":str(settings.data_dir),"MASTER_KEY_FILE":str(settings.master_key_file),"PUBLIC_URL":base,"SECURE_COOKIES":"true","TESTING":"false"}
        context = ssl.create_default_context(cafile=str(certificate))
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),urllib.request.HTTPCookieProcessor(CookieJar()))
        process = None
        def start():
            child = subprocess.Popen([sys.executable,"-m","provisioner.cli","serve","--host","127.0.0.1","--port",str(port),"--tls-cert",str(certificate),"--tls-key",str(private)],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(60):
                    if child.poll() is not None:
                        raise AssertionError("Service exited during startup")
                    try:
                        with opener.open(base+"/health/ready",timeout=2) as response:
                            assert json.load(response)["status"] == "ready"
                        return child
                    except urllib.error.URLError:
                        time.sleep(0.1)
                raise AssertionError("Service did not become ready")
            except BaseException:
                child.terminate()
                child.wait(timeout=15)
                raise
        def request(path,payload=None,csrf=None):
            headers = {}
            body = None
            if payload is not None:
                body = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            if csrf:
                headers["X-CSRF-Token"] = csrf
            return opener.open(urllib.request.Request(base+path,data=body,headers=headers),timeout=5)
        try:
            process = start()
            try:
                urllib.request.urlopen(base+"/health/ready",timeout=3)
            except urllib.error.URLError as error:
                assert isinstance(error.reason,ssl.SSLCertVerificationError),repr(error)
            else:
                raise AssertionError("An untrusted TLS certificate was accepted")
            form = urllib.parse.urlencode({"username":"smoke-admin","password":password}).encode()
            with opener.open(urllib.request.Request(base+"/auth/login",data=form,headers={"Origin":base}),timeout=5) as response:
                assert response.status == 200
                assert b"/static/app.js" in response.read()
            with request("/api/v1/me") as response:
                csrf = json.load(response)["csrf_token"]
            with request("/api/v1/hosts",{"fqdn":"smoke.example.net","site":"isolated-test","management_ip":"192.0.2.11/24","identities":[{"kind":"serial","value":"SMOKE-ONLY"}]},csrf) as response:
                host_id = json.load(response)["id"]
            with opener.open(base+"/static/app.js",timeout=5) as response:
                assert response.status == 200 and len(response.read())>1000
            process.terminate()
            process.wait(timeout=15)
            process = start()
            with request("/api/v1/hosts") as response:
                assert any(host["id"] == host_id for host in json.load(response))
            print(json.dumps({"uid":os.getuid(),"checks":["TLS trusted certificate","untrusted certificate rejected","browser login and CSRF","static assets packaged","host persists across service restart","session persists across service restart"],"status":"passed"}))
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
