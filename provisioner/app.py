from contextlib import asynccontextmanager
import asyncio
import base64
from collections import defaultdict, deque
import hashlib
import hmac
import json
from pathlib import Path
import re
import shlex
import sqlite3
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import jsonschema
from pydantic import ValidationError

from .config import Settings
from .db import Database
from .models import (Approval, Completion, Enroll, EventBatch, GroupCreate, HostCreate, HostUpdate, IsoCreate, LeaseRequest, LogBatch, ModuleCreate, ProfileCreate, Publish, RunAction, RunReconcile, SecretCreate, UserCreate, normalize_identity)
from .security import Security, atomic_artifact, canonical, digest, token
from .service import Service, TERMINAL, audit, get_host, new_id, now_iso, public_run, require, unpack

ASSETS = Path(__file__).parent


class RequestGuards:
    """Bound bodies before FastAPI parses them; no capability URLs in access logs."""
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        consumed = 0
        messages = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            consumed += len(message.get("body", b""))
            if consumed > self.max_bytes:
                return await JSONResponse({"detail":"Anfrage überschreitet das Größenlimit."},413)(scope,receive,send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()
        async def guarded_send(message):
            if message["type"] == "http.response.start":
                referrer_policy = b"no-referrer" if scope["path"].startswith(("/bootstrap/","/installer/","/agent/")) else b"same-origin"
                message.setdefault("headers", []).extend([(b"cache-control", b"no-store"),(b"x-content-type-options",b"nosniff"),(b"x-frame-options",b"DENY"),(b"referrer-policy",referrer_policy),(b"content-security-policy",b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")])
            await send(message)
        await self.app(scope,replay,guarded_send)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    db = Database(settings)
    security = Security(settings)
    service = Service(settings, db, security)
    rate_buckets = defaultdict(deque)

    def rate_limit(key, limit, seconds=60):
        timestamp = time.monotonic()
        queue = rate_buckets[key]
        while queue and queue[0] < timestamp - seconds:
            queue.popleft()
        require(len(queue) < limit, 429, "Zu viele Anfragen. Bitte später erneut versuchen.")
        queue.append(timestamp)
        if len(rate_buckets) > 10000:
            for item in list(rate_buckets):
                if not rate_buckets[item] or rate_buckets[item][-1] < timestamp - 3600:
                    rate_buckets.pop(item, None)

    async def maintenance_loop():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(service.maintain)

    @asynccontextmanager
    async def lifespan(app):
        from .cli import ServiceLock
        require(not (settings.data_dir / "RESTORE_FAILED").exists(), 503, "Unvollständige Wiederherstellung muss zuerst behoben werden.")
        with ServiceLock(settings.data_dir):
            db.initialize()
            if settings.bootstrap_username and settings.bootstrap_password:
                with db.connection(write=True) as connection:
                    if not connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                        connection.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)", (new_id("user"),settings.bootstrap_username,security.hash_password(settings.bootstrap_password),"admin",now_iso()))
            service.maintain()
            task = asyncio.create_task(maintenance_loop())
            try:
                yield
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Proxmox AIS", version="0.1.0", description="Kontrollierte Proxmox-Installation und wiederaufnehmbare Nachkonfiguration.", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.settings, app.state.security, app.state.service = db, settings, security, service
    app.add_middleware(RequestGuards, max_bytes=settings.max_request_bytes)
    (ASSETS / "static").mkdir(exist_ok=True)
    (ASSETS / "templates").mkdir(exist_ok=True)
    app.mount("/static",StaticFiles(directory=ASSETS / "static"),name="static")
    templates = Jinja2Templates(directory=ASSETS / "templates")

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, error):
        return JSONResponse({"detail":"Konflikt: Identität, IP, FQDN, Name oder aktiver Lauf ist bereits vergeben."},409)

    @app.exception_handler(sqlite3.OperationalError)
    async def db_unavailable(request,error):
        return JSONResponse({"detail":"Datenbank vorübergehend nicht verfügbar."},503)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_failed(request,error):
        errors = [{"loc":list(e["loc"]),"msg":e["msg"],"type":e["type"]} for e in error.errors()]
        return JSONResponse({"detail":"Ungültige Eingaben.","errors":errors},422)

    @app.exception_handler(ValueError)
    async def bad_value(request,error):
        return JSONResponse({"detail":"Ungültiger Wert oder nicht lesbare Konfiguration."},422)

    def current_user(request: Request):
        session_token = request.cookies.get("ais_session", "")
        require(bool(session_token),401,"Anmeldung erforderlich.")
        with db.connection() as connection:
            row = connection.execute("SELECT u.id,u.username,u.role,s.csrf_token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",(digest(session_token),time.time())).fetchone()
        require(row is not None,401,"Sitzung ist ungültig oder abgelaufen.")
        user = dict(row)
        if request.method not in {"GET","HEAD","OPTIONS"}:
            csrf = request.headers.get("x-csrf-token", "")
            require(hmac.compare_digest(csrf,user["csrf_token"]),403,"CSRF-Prüfung fehlgeschlagen. Seite neu laden.")
        return user

    def roles(*allowed):
        def check(user=Depends(current_user)):
            require(user["role"] in {*allowed,"admin","developer"},403,"Für diese Aktion fehlt die Berechtigung.")
            return user
        return check

    @app.get("/health/live")
    def health_live():
        return {"status":"ok"}

    @app.get("/health/ready")
    def health_ready():
        with db.connection() as connection:
            connection.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        require(settings.master_key_file.is_file(),503,"Notwendige Konfiguration fehlt.")
        return {"status":"ready"}

    @app.get("/login",response_class=HTMLResponse,include_in_schema=False)
    def login_page(request:Request):
        return templates.TemplateResponse(request=request,name="login.html",context={"error":None})

    @app.post("/auth/login",include_in_schema=False)
    def login(request:Request,username:str=Form(...),password:str=Form(...)):
        rate_limit("login:" + (request.client.host if request.client else "unknown"),10,300)
        origin = request.headers.get("origin")
        require(not origin or origin == settings.public_url or (settings.testing and origin == str(request.base_url).rstrip("/")),403,"Anmeldeanfrage stammt von einer fremden Website.")
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM users WHERE username=? AND disabled=0",(username,)).fetchone()
            if row is None or not security.verify_password(password,row["password_hash"]):
                audit(connection,"anonymous","login.failed","",data={"username":username[:64]})
                return templates.TemplateResponse(request=request,name="login.html",context={"error":"Benutzername oder Passwort ist falsch."},status_code=401)
            session_token, csrf = token(),token()
            connection.execute("INSERT INTO sessions VALUES(?,?,?,?)",(digest(session_token),row["id"],csrf,time.time()+settings.session_hours*3600))
            audit(connection,row["id"],"login.succeeded",row["id"])
        response = RedirectResponse("/",status_code=303)
        response.set_cookie("ais_session",session_token,httponly=True,secure=settings.secure_cookies,samesite="strict",max_age=settings.session_hours*3600,path="/")
        return response

    @app.post("/auth/logout")
    def logout(request:Request,user=Depends(current_user)):
        with db.connection(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?",(digest(request.cookies.get("ais_session","")),))
            audit(connection,user["id"],"logout",user["id"])
        response = JSONResponse({"status":"ok"})
        response.delete_cookie("ais_session",path="/")
        return response

    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def index(request:Request):
        try:
            user = current_user(request)
        except HTTPException:
            return RedirectResponse("/login",303)
        return templates.TemplateResponse(request=request,name="index.html",context={"user":user})

    @app.get("/api/v1/me")
    def me(user=Depends(current_user)):
        return user

    @app.get("/openapi.json",include_in_schema=False)
    def openapi(user=Depends(current_user)):
        return app.openapi()

    @app.get("/api/v1/dashboard")
    def dashboard(user=Depends(current_user)):
        service.maintain()
        with db.connection() as connection:
            hosts = [get_host(connection,r[0]) for r in connection.execute("SELECT id FROM hosts ORDER BY created_at DESC")]
            runs = [decorate_run(r,connection) for r in connection.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 20")]
            counts = {"hosts":len(hosts),"ready":sum(h["status"] == "prepared" and not h["blocked"] for h in hosts),"active":sum(h["status"] in {"answer_served","installed_reported","runner_ready","running","reboot_pending","waiting_retry"} for h in hosts),"succeeded":sum(h["status"]=="succeeded" for h in hosts),"needs_review":sum(h["status"] in {"needs_review","failed"} for h in hosts),"discovered":connection.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]}
            events = [unpack(r) for r in connection.execute("SELECT * FROM audit ORDER BY created_at DESC LIMIT 12")]
            return {"counts":counts,"recent_events":events,"hosts":hosts,"runs":runs,"maintenance":settings.maintenance}

    @app.get("/api/v1/hosts")
    def hosts(user=Depends(current_user)):
        with db.connection() as connection:
            return [get_host(connection,r[0]) for r in connection.execute("SELECT id FROM hosts ORDER BY fqdn")]

    @app.post("/api/v1/hosts",status_code=201)
    def create_host(payload:HostCreate,user=Depends(roles("operator"))):
        with db.connection(write=True) as connection:
            return service.create_host(connection,payload,user["id"])

    @app.post("/api/v1/hosts/import",status_code=201)
    def import_hosts(payload:list[HostCreate],user=Depends(roles("operator"))):
        require(1 <= len(payload) <= 100,422,"Ein Import darf 1 bis 100 Hosts enthalten.")
        with db.connection(write=True) as connection:
            return [service.create_host(connection,item,user["id"]) for item in payload]

    @app.get("/api/v1/discoveries")
    def discoveries(user=Depends(current_user)):
        with db.connection() as connection:
            return [unpack(r) for r in connection.execute("SELECT * FROM discoveries ORDER BY last_seen DESC")]

    @app.get("/api/v1/hosts/{host_id}")
    def host_detail(host_id:str,user=Depends(current_user)):
        with db.connection() as connection:
            host = get_host(connection,host_id)
            host["runs"] = [decorate_run(r,connection) for r in connection.execute("SELECT * FROM runs WHERE host_id=? ORDER BY created_at DESC",(host_id,))]
            return host

    @app.patch("/api/v1/hosts/{host_id}")
    def update_host(host_id:str,payload:HostUpdate,user=Depends(roles("operator"))):
        with db.connection(write=True) as connection:
            return service.update_host(connection,host_id,payload,user["id"])

    @app.get("/api/v1/hosts/{host_id}/preview")
    def preview(host_id:str,user=Depends(roles("operator","author"))):
        with db.connection() as connection:
            return service.resolve(connection,host_id)[0]

    @app.post("/api/v1/hosts/{host_id}/approve-install",status_code=201)
    def approve(host_id:str,payload:Approval,user=Depends(roles("operator"))):
        service.maintain()
        with db.connection(write=True) as connection:
            return service.approve(connection,host_id,payload,user["id"])

    @app.get("/api/v1/profiles")
    def profiles(user=Depends(current_user)):
        with db.connection() as connection:
            return [unpack(r) for r in connection.execute("SELECT * FROM profiles ORDER BY name,version DESC")]

    @app.post("/api/v1/profiles",status_code=201)
    def create_profile(payload:ProfileCreate,user=Depends(roles("author"))):
        with db.connection(write=True) as connection:
            return service.create_profile(connection,payload,user["id"])

    @app.get("/api/v1/modules")
    def modules(user=Depends(current_user)):
        with db.connection() as connection:
            result = [unpack(r) for r in connection.execute("SELECT * FROM modules ORDER BY name,version DESC")]
        if user["role"] not in {"author","admin","developer"}:
            for module in result:
                module.pop("source",None)
        return result

    @app.post("/api/v1/modules",status_code=201)
    def create_module(payload:ModuleCreate,user=Depends(roles("author"))):
        try:
            jsonschema.Draft202012Validator.check_schema(payload.parameters_schema)
        except jsonschema.SchemaError:
            raise HTTPException(422,"Parameterschema ist ungültig.")
        require("$ref" not in canonical(payload.parameters_schema),422,"Externe und rekursive Schema-Referenzen werden nicht unterstützt.")
        payload.source = payload.source.replace("\r\n","\n")
        checksum = atomic_artifact(service.artifact_dir,payload.source)
        with db.connection(write=True) as connection:
            version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM modules WHERE name=?",(payload.name,)).fetchone()[0]
            module_id = new_id("module")
            connection.execute("INSERT INTO modules VALUES(?,?,?,?,?,?,?,?)",(module_id,payload.name,version,"draft",checksum,canonical(payload.model_dump()),user["id"],now_iso()))
            audit(connection,user["id"],"module.created",module_id,payload.reason,{"digest":checksum})
            return unpack(connection.execute("SELECT * FROM modules WHERE id=?",(module_id,)).fetchone())

    @app.get("/api/v1/modules/builtin")
    def builtin_modules(user=Depends(roles("author"))):
        from .builtin_modules import catalog
        return catalog()

    @app.get("/api/v1/modules/{object_id}")
    def module_detail(object_id:str,user=Depends(current_user)):
        with db.connection() as connection:
            result = unpack(connection.execute("SELECT * FROM modules WHERE id=?",(object_id,)).fetchone())
        if user["role"] not in {"admin","developer","author"}:
            result.pop("source",None)
        return result

    @app.get("/api/v1/profiles/{object_id}")
    def profile_detail(object_id:str,user=Depends(current_user)):
        with db.connection() as connection:
            return unpack(connection.execute("SELECT * FROM profiles WHERE id=?",(object_id,)).fetchone())

    @app.post("/api/v1/modules/{object_id}/publish")
    def publish_module(object_id:str,payload:Publish,user=Depends(roles())):
        return publish_object("modules",object_id,payload,user)

    @app.post("/api/v1/profiles/{object_id}/publish")
    def publish_profile(object_id:str,payload:Publish,user=Depends(roles())):
        return publish_object("profiles",object_id,payload,user)

    def publish_object(table,object_id,payload,user):
        with db.connection() as connection:
            item = unpack(connection.execute(f"SELECT * FROM {table} WHERE id=?",(object_id,)).fetchone())
        require(item["status"] == "draft",409,"Veröffentlichte Versionen sind unveränderlich. Neue Version erstellen.")
        require(not settings.four_eyes or item["created_by"] != user["id"],403,"Vieraugenprinzip: Eine andere Person muss diese Version veröffentlichen.")
        require(item["target_builds"],422,"Mindestens ein getesteter Zielbuild ist erforderlich.")
        if table == "modules":
            service.module_syntax(item["source"])
        else:
            require(item["kind"] != "postinstall" or item["steps"],422,"Postinstallationsprofil benötigt Schritte.")
        with db.connection(write=True) as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id=?",(object_id,)).fetchone()
            require(row["status"] == "draft",409,"Version wurde bereits veröffentlicht.")
            data = json.loads(row["data"])
            data.update({"test_evidence":payload.test_evidence,"published_by":user["id"],"published_at":now_iso()})
            if table == "profiles":
                data["digest"] = digest(canonical({k:v for k,v in data.items() if k != "digest"}))
            connection.execute(f"UPDATE {table} SET status='published',data=? WHERE id=?",(canonical(data),object_id))
            audit(connection,user["id"],table[:-1]+".published",object_id,payload.reason,{"test_evidence":payload.test_evidence})
            return unpack(connection.execute(f"SELECT * FROM {table} WHERE id=?",(object_id,)).fetchone())

    @app.get("/api/v1/groups")
    def groups(user=Depends(roles("operator","author"))):
        with db.connection() as connection:
            return [dict(r) for r in connection.execute("SELECT id,name,site,expires_at,revoked,created_at FROM groups ORDER BY name")]

    @app.post("/api/v1/groups",status_code=201)
    def create_group(payload:GroupCreate,user=Depends(roles())):
        secret = token()
        with db.connection(write=True) as connection:
            group_id = new_id("group")
            connection.execute("INSERT INTO groups VALUES(?,?,?,?,?,?,?)",(group_id,payload.name,payload.site,digest(secret),time.time()+payload.valid_hours*3600,0,now_iso()))
            audit(connection,user["id"],"group.created",group_id)
            result = dict(connection.execute("SELECT id,name,site,expires_at,revoked,created_at FROM groups WHERE id=?",(group_id,)).fetchone())
            return {**result,"token":payload.name + ":" + secret}

    @app.post("/api/v1/groups/{group_id}/revoke")
    def revoke_group(group_id:str,user=Depends(roles())):
        with db.connection(write=True) as connection:
            changed = connection.execute("UPDATE groups SET revoked=1 WHERE id=?",(group_id,)).rowcount
            require(changed,404,"Gruppe nicht gefunden.")
            audit(connection,user["id"],"group.revoked",group_id)
        return {"status":"revoked"}

    @app.get("/api/v1/iso-records")
    def iso_records(user=Depends(current_user)):
        with db.connection() as connection:
            return [iso_command(unpack(r),connection) for r in connection.execute("SELECT * FROM iso_records ORDER BY created_at DESC")]

    def iso_command(iso,connection):
        group = connection.execute("SELECT name FROM groups WHERE id=?",(iso["group_id"],)).fetchone()
        args = ["proxmox-auto-install-assistant","prepare-iso","SOURCE.iso","--fetch-from","http","--url",settings.public_url + "/installer/v1/answer","--cert-fingerprint",iso["fingerprint"],"--answer-auth-token",(group[0] if group else "gruppe") + ":<SECRET>"]
        iso["command"] = shlex.join(args)
        return iso

    @app.post("/api/v1/iso-records",status_code=201)
    def create_iso(payload:IsoCreate,user=Depends(roles())):
        require(payload.test_status != "passed" or (payload.native_token_support and len(payload.test_evidence)>=5),422,"Freigegebenes Medium benötigt nativen Token-Support und dokumentierten Testnachweis.")
        with db.connection(write=True) as connection:
            require(connection.execute("SELECT 1 FROM groups WHERE id=?",(payload.group_id,)).fetchone(),422,"Gruppe nicht gefunden.")
            iso_id = new_id("iso")
            connection.execute("INSERT INTO iso_records VALUES(?,?,?,?)",(iso_id,payload.name,canonical(payload.model_dump()),now_iso()))
            audit(connection,user["id"],"iso.registered",iso_id,data={"build":payload.build,"test_status":payload.test_status})
            return iso_command(unpack(connection.execute("SELECT * FROM iso_records WHERE id=?",(iso_id,)).fetchone()),connection)

    @app.get("/api/v1/secrets")
    def secrets_list(user=Depends(roles())):
        with db.connection() as connection:
            return [dict(r) for r in connection.execute("SELECT id,name,created_at FROM secrets ORDER BY name")]

    @app.post("/api/v1/secrets",status_code=201)
    def create_secret(payload:SecretCreate,user=Depends(roles())):
        with db.connection(write=True) as connection:
            secret_id = new_id("secret")
            connection.execute("INSERT INTO secrets VALUES(?,?,?,?)",(secret_id,payload.name,security.encrypt(payload.value),now_iso()))
            audit(connection,user["id"],"secret.created",secret_id)
            return {"id":secret_id,"name":payload.name}

    @app.get("/api/v1/users")
    def users(user=Depends(roles())):
        with db.connection() as connection:
            return [dict(r) for r in connection.execute("SELECT id,username,role,disabled,created_at FROM users ORDER BY username")]

    @app.post("/api/v1/users",status_code=201)
    def create_user(payload:UserCreate,user=Depends(roles())):
        with db.connection(write=True) as connection:
            user_id = new_id("user")
            connection.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",(user_id,payload.username,security.hash_password(payload.password),payload.role,now_iso()))
            audit(connection,user["id"],"user.created",user_id,data={"role":payload.role})
            return {"id":user_id,"username":payload.username,"role":payload.role}

    @app.post("/api/v1/users/{user_id}/disable")
    def disable_user(user_id:str,user=Depends(roles())):
        require(user_id != user["id"],409,"Eigenes Konto kann nicht deaktiviert werden.")
        with db.connection(write=True) as connection:
            require(connection.execute("UPDATE users SET disabled=1 WHERE id=?",(user_id,)).rowcount,404,"Benutzer nicht gefunden.")
            connection.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
            audit(connection,user["id"],"user.disabled",user_id)
        return {"status":"disabled"}

    @app.get("/api/v1/audit")
    def audit_list(user=Depends(current_user)):
        with db.connection() as connection:
            return [unpack(r) for r in connection.execute("SELECT * FROM audit ORDER BY created_at DESC LIMIT 500")]

    @app.get("/api/v1/runs")
    def runs(user=Depends(current_user)):
        with db.connection() as connection:
            return [decorate_run(r,connection) for r in connection.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 500")]

    def decorate_run(row,connection):
        result = public_run(row)
        result["contact_status"] = "unknown" if row["last_seen"] and row["status"] not in TERMINAL and time.time()-row["last_seen"]>settings.heartbeat_unknown_seconds else "current" if row["last_seen"] else "pending"
        specs = {s["id"]:s for s in result["manifest"]["steps"]}
        result["steps"] = [{**specs[r["step_id"]],**dict(r),"verification":json.loads(r["verification"])} for r in connection.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY position",(row["id"],))]
        return result

    @app.get("/api/v1/runs/{run_id}")
    def run_detail(run_id:str,user=Depends(current_user)):
        with db.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
            require(row is not None,404,"Lauf nicht gefunden.")
            result = decorate_run(row,connection)
            result["events"] = [unpack(r) for r in connection.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence DESC LIMIT 200",(run_id,))][::-1]
            result["logs"] = [unpack(r) for r in connection.execute("SELECT * FROM logs WHERE run_id=? ORDER BY sequence DESC LIMIT 100",(run_id,))][::-1]
            return result

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id:str,payload:RunAction,user=Depends(roles("operator"))):
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
            run = unpack(row)
            require(run["version"] == payload.expected_version and run["status"] not in TERMINAL,409,"Laufzustand hat sich geändert oder ist bereits abgeschlossen.")
            data = json.loads(row["data"])
            data["cancel_requested"] = True
            status = "cancelled" if run["status"] == "prepared" else run["status"]
            connection.execute("UPDATE runs SET data=?,status=?,version=version+1 WHERE id=?",(canonical(data),status,run_id))
            if status == "cancelled":
                connection.execute("UPDATE approvals SET status='revoked' WHERE id=?",(run["approval_id"],))
                connection.execute("UPDATE hosts SET status='cancelled' WHERE id=?",(run["host_id"],))
            audit(connection,user["id"],"run.cancel_requested",run_id,payload.reason)
            return public_run(connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())

    @app.post("/api/v1/runs/{run_id}/resume")
    def resume_run(run_id:str,payload:RunAction,user=Depends(roles("operator"))):
        with db.connection(write=True) as connection:
            run = unpack(connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())
            require(run["version"] == payload.expected_version and run["status"] in {"needs_review","waiting_retry"},409,"Nur ein wartender, unverändert angezeigter Lauf kann fortgesetzt werden.")
            require(run["device_key"] and not run.get("cancel_requested"),409,"Keine aktive Gerätebindung für sichere Wiederaufnahme vorhanden.")
            require(not get_host(connection,run["host_id"])["blocked"],403,"Host ist gesperrt.")
            connection.execute("UPDATE runs SET status='runner_ready',version=version+1 WHERE id=?",(run_id,))
            connection.execute("UPDATE hosts SET status='runner_ready' WHERE id=?",(run["host_id"],))
            audit(connection,user["id"],"run.resumed",run_id,payload.reason)
            return public_run(connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())

    @app.post("/api/v1/runs/{run_id}/reconcile")
    def reconcile_run(run_id:str,payload:RunReconcile,user=Depends(roles("operator"))):
        """Close an externally checked abandoned run; never grants installation."""
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
            run = unpack(row)
            host = get_host(connection,run["host_id"])
            require(run["version"] == payload.expected_version and run["status"] not in TERMINAL,409,"Lauf wurde geändert oder ist bereits beendet.")
            require(payload.execution_stopped and payload.confirmation == host["fqdn"],422,"Vor dem Abschließen muss lokal geprüft sein, dass Installer und Runner gestoppt sind; Host-FQDN bestätigen.")
            require(not row["lease_until"] or row["lease_until"]<=time.time(),409,"Aktuelle Laufberechtigung muss vor dem Abgleich ablaufen. Zuerst Abbruch anfordern.")
            require(not row["answer_until"] or row["answer_until"]<=time.time(),409,"Auslieferungsfenster ist noch aktiv. Abgleich erst nach dessen Ablauf möglich.")
            data = json.loads(row["data"])
            data.update({"cancel_requested":True,"reconciled_by":user["id"],"reconciliation_reason":payload.reason})
            connection.execute("UPDATE runs SET status='cancelled',version=version+1,data=?,completed_at=?,device_key=NULL,enrollment_hash=NULL,bootstrap_hash=NULL,report_hash=NULL,answer_ciphertext=NULL,bootstrap_ciphertext=NULL,lease_until=NULL WHERE id=?",(canonical(data),time.time(),run_id))
            connection.execute("UPDATE approvals SET status='revoked' WHERE id=?",(run["approval_id"],))
            connection.execute("UPDATE hosts SET status='cancelled',version=version+1 WHERE id=?",(host["id"],))
            audit(connection,user["id"],"run.reconciled",run_id,payload.reason,{"execution_stopped_confirmed":True,"fqdn":host["fqdn"]})
            return public_run(connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone())

    def installer_group(request):
        authorization = request.headers.get("authorization", "")
        require(authorization.startswith("Bearer ") and ":" in authorization,401,"Gültiger Installer-Gruppentoken erforderlich.")
        name,secret = authorization[7:].split(":",1)
        with db.connection() as connection:
            row = connection.execute("SELECT * FROM groups WHERE name=?",(name,)).fetchone()
        require(row is not None and hmac.compare_digest(digest(secret),row["token_hash"]),401,"Ungültiger Installer-Gruppentoken.")
        require(not row["revoked"],403,"Installer-Gruppentoken ist gesperrt.")
        require(row["expires_at"] > time.time(),410,"Installer-Gruppentoken ist abgelaufen.")
        rate_limit("installer:" + row["id"],120)
        return row

    @app.post("/installer/v1/answer",response_class=Response)
    async def installer_answer(request:Request):
        rate_limit("answer-ip:" + (request.client.host if request.client else "unknown"),240)
        group = installer_group(request)
        try:
            payload = await request.json()
        except (ValueError,UnicodeDecodeError):
            raise HTTPException(422,"Ungültige Installer-Systemdaten.")
        service.maintain()
        try:
            with db.connection(write=True) as connection:
                answer = service.serve_answer(connection,group,payload)
        except HTTPException as error:
            with db.connection(write=True) as connection:
                audit(connection,"installer:"+group["name"],"installation.denied","",str(error.detail))
            raise
        require(answer is not None,403,"Unbekannter Host wurde als entdeckt gespeichert. Vor erneutem Start zuordnen und freigeben.")
        return Response(answer,media_type="application/toml")

    @app.get("/bootstrap/v1/{download_token}",response_class=Response)
    def bootstrap(download_token:str):
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM runs WHERE bootstrap_hash=?",(digest(download_token),)).fetchone()
            require(row is not None,401,"Ungültige Download-Berechtigung.")
            require(row["status"] in {"answer_served","installed_reported"} and time.time()<row["answer_until"],410,"Starthelfer ist nicht mehr zum Download freigegeben.")
            require(not get_host(connection,row["host_id"])["blocked"] and not json.loads(row["data"]).get("cancel_requested"),403,"Lauf ist gesperrt.")
            rate_limit("bootstrap:"+row["id"],12,300)
            audit(connection,"installer","bootstrap.downloaded",row["id"])
            return Response(security.decrypt(row["bootstrap_ciphertext"]),media_type="text/x-shellscript")

    @app.post("/installer/v1/report/{report_token}")
    async def installer_report(report_token:str,request:Request):
        try:
            await request.json()
        except ValueError:
            raise HTTPException(422,"Ungültiger Installationsbericht.")
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM runs WHERE report_hash=?",(digest(report_token),)).fetchone()
            require(row is not None,401,"Ungültige Report-Berechtigung.")
            require(row["enroll_until"] and row["enroll_until"]>time.time(),410,"Report-Berechtigung ist abgelaufen.")
            require(row["status"] in {"answer_served","installed_reported","runner_ready","running"},409,"Installationsbericht passt nicht zum Laufzustand.")
            if row["status"] == "answer_served":
                connection.execute("UPDATE runs SET status='installed_reported',version=version+1,last_seen=? WHERE id=?",(time.time(),row["id"]))
                connection.execute("UPDATE hosts SET status='installed_reported' WHERE id=?",(row["host_id"],))
                audit(connection,"installer","installation.reported",row["id"])
            return {"status":"accepted"}

    @app.post("/agent/v1/enroll")
    def enroll(payload:Enroll,request:Request):
        rate_limit("enroll:" + (request.client.host if request.client else "unknown"),60)
        try:
            key = base64.b64decode(payload.public_key,validate=True)
            Ed25519PublicKey.from_public_bytes(key)
        except (ValueError,TypeError):
            raise HTTPException(422,"Ungültiger Ed25519-Geräteschlüssel.")
        with db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?",(payload.run_id,)).fetchone()
            require(row is not None and row["enrollment_hash"] and hmac.compare_digest(row["enrollment_hash"],digest(payload.enrollment_secret)),401,"Ungültige Enrollment-Berechtigung.")
            require(row["enroll_until"] > time.time(),410,"Enrollment-Berechtigung ist abgelaufen.")
            require(row["status"] in {"answer_served","installed_reported","runner_ready","running","reboot_pending","needs_review","waiting_retry"},403,"Lauf erlaubt keine Registrierung.")
            run_data = json.loads(row["data"])
            require(not run_data.get("cancel_requested"),403,"Lauf ist zum Abbruch markiert.")
            identities = [{"kind":i.kind,"value":normalize_identity(i.kind,i.value)} for i in payload.identities]
            host = service.match_host(connection,identities)
            require(host and host["id"] == row["host_id"] and not host["blocked"],403,"Geräteidentität passt nicht zum freigegebenen Host.")
            require(not row["device_key"] or row["device_key"] == payload.public_key,409,"Enrollment ist bereits an einen anderen Geräteschlüssel gebunden.")
            if not row["device_key"]:
                run_data["boot_id"] = payload.boot_id
                connection.execute("UPDATE runs SET device_key=?,data=?,status='runner_ready',version=version+1,last_seen=? WHERE id=?",(payload.public_key,canonical(run_data),time.time(),row["id"]))
                connection.execute("UPDATE hosts SET status='runner_ready' WHERE id=?",(row["host_id"],))
                audit(connection,"device:"+row["id"],"runner.enrolled",row["id"])
            return {"run_id":row["id"],"status":"enrolled","manifest_digest":run_data["manifest_digest"]}

    async def device(request:Request):
        run_id = request.headers.get("x-run-id", "")
        key = request.headers.get("x-device-key", "")
        timestamp = request.headers.get("x-timestamp", "")
        nonce = request.headers.get("x-nonce", "")
        try:
            require(abs(time.time()-int(timestamp)) <= 300,401,"Signaturzeit liegt außerhalb des zulässigen Fensters.")
            require(bool(re.fullmatch(r"[A-Za-z0-9_-]{16,128}",nonce)),401,"Ungültige Request-Nonce.")
            body = await request.body()
            message = f"{request.method}\n{request.url.path}\n{timestamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
            signature = base64.b64decode(request.headers.get("x-signature", ""),validate=True)
            with db.connection(write=True) as connection:
                row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
                require(row is not None and row["device_key"] and hmac.compare_digest(row["device_key"],key),401,"Gerätebindung ungültig.")
                Ed25519PublicKey.from_public_bytes(base64.b64decode(key,validate=True)).verify(signature,message)
                require(not connection.execute("SELECT 1 FROM nonces WHERE run_id=? AND nonce=?",(run_id,nonce)).fetchone(),409,"Request-Nonce wurde bereits verwendet.")
                if row["status"] in TERMINAL:
                    complete_retry = request.url.path == f"/agent/v1/runs/{run_id}/complete" and row["status"] == "succeeded" and row["completed_at"] and time.time()-row["completed_at"] < 3600
                    cancel_retry = request.url.path == f"/agent/v1/runs/{run_id}/events" and row["status"] == "cancelled" and row["completed_at"] and time.time()-row["completed_at"] < 3600
                    require(complete_retry or cancel_retry,403,"Laufberechtigung ist beendet.")
                connection.execute("INSERT INTO nonces VALUES(?,?,?)",(run_id,nonce,time.time()+600))
            rate_limit("device:"+run_id,600)
            return run_id
        except (ValueError,TypeError,InvalidSignature):
            raise HTTPException(401,"Ungültige Gerätesignatur.")

    def authorized_run(connection,run_id,device_id,lease=False):
        require(run_id == device_id,403,"Kein Zugriff auf einen fremden Lauf.")
        row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
        require(row is not None,404,"Lauf nicht gefunden.")
        if lease:
            require(row["status"] in {"runner_ready","running"} and row["lease_until"] and row["lease_until"]>time.time(),403,"Aktuelle Ausführungsberechtigung erforderlich.")
            require(not json.loads(row["data"]).get("cancel_requested") and not get_host(connection,row["host_id"])["blocked"],403,"Lauf ist gesperrt.")
        return row

    @app.post("/agent/v1/lease")
    def lease(payload:LeaseRequest,device_id=Depends(device)):
        with db.connection(write=True) as connection:
            row = authorized_run(connection,payload.run_id,device_id)
            data = json.loads(row["data"])
            host = get_host(connection,row["host_id"])
            action = "stop" if data.get("cancel_requested") else "wait" if host["blocked"] or row["status"] in {"needs_review","waiting_retry"} else "run" if row["status"] in {"runner_ready","running","reboot_pending"} else "revoked"
            expiry = time.time()+settings.lease_seconds if action == "run" else time.time()
            # A lease already held by an offline device cannot be recalled.
            # Keep its horizon for the operator reconciliation gate.
            remembered_expiry = expiry if action == "run" else row["lease_until"]
            connection.execute("UPDATE runs SET lease_until=?,last_seen=? WHERE id=?",(remembered_expiry,time.time(),row["id"]))
            return {"action":action,"expires_at":expiry,"run_version":row["version"]}

    @app.get("/agent/v1/runs/{run_id}/manifest")
    def manifest(run_id:str,device_id=Depends(device)):
        with db.connection() as connection:
            row = authorized_run(connection,run_id,device_id,lease=True)
            data = json.loads(row["data"])
            return {**data["manifest"],"digest":data["manifest_digest"]}

    @app.get("/agent/v1/artifacts/{checksum}",response_class=Response)
    def artifact(checksum:str,device_id=Depends(device)):
        require(bool(re.fullmatch(r"[a-f0-9]{64}",checksum)),404,"Artefakt nicht gefunden.")
        with db.connection() as connection:
            row = authorized_run(connection,device_id,device_id,lease=True)
            require(checksum in {s["digest"] for s in json.loads(row["data"])["manifest"]["steps"]},403,"Artefakt gehört nicht zum Lauf.")
        path = service.artifact_dir / checksum
        require(path.is_file(),404,"Artefakt fehlt.")
        content = path.read_bytes()
        require(digest(content)==checksum,503,"Artefaktintegrität konnte nicht bestätigt werden.")
        return Response(content,media_type="application/octet-stream")

    @app.get("/agent/v1/runs/{run_id}/secrets/{step_id}")
    def step_secrets(run_id:str,step_id:str,device_id=Depends(device)):
        with db.connection(write=True) as connection:
            row = authorized_run(connection,run_id,device_id,lease=True)
            step = connection.execute("SELECT * FROM run_steps WHERE run_id=? AND step_id=?",(run_id,step_id)).fetchone()
            require(step is not None and step["status"] != "succeeded",403,"Kein Geheimniszugriff für diesen Schritt.")
            specs = {s["id"]:s for s in json.loads(row["data"])["manifest"]["steps"]}
            remaining = [s for s in connection.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY position",(run_id,)) if s["status"] != "succeeded" and not (s["status"] == "failed" and not specs[s["step_id"]]["required"])]
            require(remaining and remaining[0]["step_id"] == step_id,403,"Geheimnisse sind nur für den aktuellen Schritt verfügbar.")
            audit(connection,"device:"+run_id,"step.secrets_read",run_id,data={"step_id":step_id})
            return json.loads(security.decrypt(row["secrets_ciphertext"]))["steps"].get(step_id,{})

    @app.post("/agent/v1/runs/{run_id}/events")
    def events(run_id:str,payload:EventBatch,device_id=Depends(device)):
        with db.connection(write=True) as connection:
            row = authorized_run(connection,run_id,device_id)
            ack = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",(run_id,)).fetchone()[0]
            for event in payload.events:
                redacted = service.redact_payload(row,event.model_dump())
                if event.sequence <= ack:
                    stored = connection.execute("SELECT data FROM events WHERE run_id=? AND sequence=?",(run_id,event.sequence)).fetchone()
                    require(stored and stored[0] == redacted,409,"Sequenznummer wurde mit anderem Ereignisinhalt wiederholt.")
                    continue
                require(event.sequence == ack+1,409,"Ereignisse müssen lückenlos und aufsteigend eintreffen.")
                row = connection.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
                apply_event(connection,row,event)
                connection.execute("INSERT INTO events VALUES(?,?,?,?)",(run_id,event.sequence,redacted,now_iso()))
                ack = event.sequence
            connection.execute("UPDATE runs SET last_seen=? WHERE id=?",(time.time(),run_id))
            return {"ack_sequence":ack}

    def apply_event(connection,row,event):
        data = json.loads(row["data"])
        status = row["status"]
        require(status not in TERMINAL,409,"Terminaler Lauf nimmt keine neuen Ereignisse an.")
        if event.type == "heartbeat":
            return
        if event.type.startswith("step."):
            require(status in {"runner_ready","running"},409,"Schrittereignis ist in diesem Laufzustand nicht zulässig.")
            step = connection.execute("SELECT * FROM run_steps WHERE run_id=? AND step_id=?",(row["id"],event.step_id)).fetchone()
            require(step is not None,422,"Schritt gehört nicht zum fixierten Manifest.")
            specs = {s["id"]:s for s in data["manifest"]["steps"]}
            previous = connection.execute("SELECT * FROM run_steps WHERE run_id=? AND position<? AND status!='succeeded'",(row["id"],step["position"])).fetchall()
            require(all(s["status"] == "failed" and not specs[s["step_id"]]["required"] for s in previous),409,"Vorheriger Pflichtschritt ist nicht erfolgreich abgeschlossen.")
            for dependency in specs[event.step_id]["dependencies"]:
                dep = connection.execute("SELECT status FROM run_steps WHERE run_id=? AND step_id=?",(row["id"],dependency)).fetchone()
                require(dep and dep[0] == "succeeded",409,"Schrittabhängigkeit ist nicht erfolgreich.")
            if event.type == "step.started":
                require(row["lease_until"] and row["lease_until"]>time.time() and not data.get("cancel_requested") and not get_host(connection,row["host_id"])["blocked"],403,"Keine aktuelle Erlaubnis für einen neuen Schritt.")
                require(step["status"] in {"pending","applying","failed"},409,"Abgeschlossener Schritt darf nicht erneut gestartet werden.")
                connection.execute("UPDATE run_steps SET status='applying',attempt=attempt+1 WHERE run_id=? AND step_id=?",(row["id"],event.step_id))
                status = "running"
            elif event.type == "step.succeeded":
                require(step["status"] == "applying" and event.exit_code == 0 and bool(event.verification),409,"Erfolg benötigt einen laufenden Schritt und erfolgreiche Verifikation.")
                require(event.verification.get("passed") is True or ("passed" not in event.verification and all(v is True for v in event.verification.values())),409,"Verifikation bestätigt keinen Erfolg.")
                connection.execute("UPDATE run_steps SET status='succeeded',verification=? WHERE run_id=? AND step_id=?",(service.redact_payload(row,event.verification),row["id"],event.step_id))
            else:
                require(step["status"] == "applying",409,"Nur ein laufender Schritt kann fehlschlagen.")
                connection.execute("UPDATE run_steps SET status='failed',verification=? WHERE run_id=? AND step_id=?",(service.redact_payload(row,event.verification),row["id"],event.step_id))
                status = "needs_review" if specs[event.step_id]["required"] else "running"
        elif event.type == "run.needs_review":
            status = "needs_review"
        elif event.type == "run.reboot_pending":
            require(status in {"running","runner_ready"} and data.get("reboots",0)<data["manifest"]["reboot_budget"],409,"Neustart ist nicht erlaubt oder Budget erschöpft.")
            data["reboots"] = data.get("reboots",0)+1
            data["boot_id"] = event.boot_id
            status = "reboot_pending"
        elif event.type == "run.resumed":
            require((status == "reboot_pending" and event.boot_id != data.get("boot_id")) or status == "runner_ready",409,"Wiederaufnahme benötigt einen neuen Boot oder eine Operatorfreigabe.")
            data["boot_id"] = event.boot_id
            status = "runner_ready"
        elif event.type == "run.cancelled":
            require(data.get("cancel_requested"),409,"Kein Abbruch angefordert.")
            status = "cancelled"
        connection.execute("UPDATE runs SET status=?,data=?,version=version+1 WHERE id=?",(status,canonical(data),row["id"]))
        if status == "cancelled":
            connection.execute("UPDATE runs SET completed_at=?,lease_until=NULL,enrollment_hash=NULL,bootstrap_hash=NULL,report_hash=NULL WHERE id=?",(time.time(),row["id"]))
        connection.execute("UPDATE hosts SET status=? WHERE id=?",(status,row["host_id"]))

    @app.post("/agent/v1/runs/{run_id}/logs")
    def logs(run_id:str,payload:LogBatch,device_id=Depends(device)):
        with db.connection(write=True) as connection:
            row = authorized_run(connection,run_id,device_id)
            ack = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM logs WHERE run_id=?",(run_id,)).fetchone()[0]
            for chunk in payload.chunks:
                data = service.redact_payload(row,chunk.model_dump())
                if chunk.sequence <= ack:
                    existing = connection.execute("SELECT data FROM logs WHERE run_id=? AND sequence=?",(run_id,chunk.sequence)).fetchone()
                    require(existing and existing[0]==data,409,"Logsequenz wurde mit anderem Inhalt wiederholt.")
                    continue
                require(chunk.sequence==ack+1,409,"Logsequenz ist nicht lückenlos.")
                connection.execute("INSERT INTO logs VALUES(?,?,?,?)",(run_id,chunk.sequence,data,now_iso()))
                ack = chunk.sequence
            return {"ack_sequence":ack}

    @app.post("/agent/v1/runs/{run_id}/complete")
    def complete(run_id:str,payload:Completion,device_id=Depends(device)):
        with db.connection(write=True) as connection:
            row = authorized_run(connection,run_id,device_id)
            if row["status"] == "succeeded":
                return {"status":"succeeded"}
            data = json.loads(row["data"])
            require(row["status"] in {"running","runner_ready"} and not data.get("cancel_requested"),409,"Lauf ist nicht abschließbar.")
            steps = connection.execute("SELECT * FROM run_steps WHERE run_id=?",(run_id,)).fetchall()
            specs = {s["id"]:s for s in data["manifest"]["steps"]}
            require(steps and all(s["status"]=="succeeded" or (s["status"]=="failed" and not specs[s["step_id"]]["required"]) for s in steps) and bool(payload.verification),409,"Alle Pflichtschritte müssen verifiziert erfolgreich sein.")
            require(all(isinstance(payload.verification.get(s["id"]),dict) and payload.verification[s["id"]].get("passed") is True for s in specs.values() if s["required"]),409,"Abschlussprüfung muss jeden Pflichtschritt ausdrücklich als erfolgreich bestätigen.")
            data["verification"] = json.loads(service.redact_payload(row,payload.verification))
            connection.execute("UPDATE runs SET status='succeeded',version=version+1,data=?,completed_at=?,lease_until=NULL,enrollment_hash=NULL,bootstrap_hash=NULL,report_hash=NULL,answer_ciphertext=NULL,bootstrap_ciphertext=NULL WHERE id=?",(canonical(data),time.time(),run_id))
            connection.execute("UPDATE hosts SET status='succeeded',version=version+1 WHERE id=?",(row["host_id"],))
            audit(connection,"device:"+run_id,"run.succeeded",run_id)
            return {"status":"succeeded"}

    return app
