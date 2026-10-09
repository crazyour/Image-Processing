"""Opt-in HTTPS experience receiver on the manager PC. It exposes no workbench/private-data routes."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib, ipaddress, json
from pathlib import Path
import secrets, socket, ssl, threading, time
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import select
from .ai_config import profile
from .config import settings

from .errors import DomainError
from .experience_exchange import canonical, digest, verify_package, signing_key
from .learning import lock_workspace
from .models import AssistantProfile, HubPeer, User, Workspace
from .schemas import Strict, EnabledIn
from .security import encrypt_key, decrypt_key, hash_token, audit, owned

def local_addresses():
    values = {"127.0.0.1"}
    try:
        values.update((item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)))
        while 1:
            return sorted((v for v in values))
    except:
        pass

def certificate(workspace_id, host):
    root = (settings().storage_dir.parent) / "secrets"; root.mkdir(parents=True, exist_ok=True); key_path = root / ("hub-" + workspace_id + ".key"); pem_path = root / ("hub-" + workspace_id + ".crt")
    if pem_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
        if ipaddress.ip_address(host) in sans and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=7):
            return (pem_path, key_path)
    key = ec.generate_private_key(ec.SECP256R1()); name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Muxu company experience receiver")])
    
    v = x509.SubjectAlternativeName
    
    cert = ##ERROR##(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5)).not_valid_after(datetime.now(timezone.utc) + timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).add_extension([x509.IPAddress(ipaddress.ip_address(v)) for v in sorted(set([host, "127.0.0.1"]))]), critical=False).sign(key, hashes.SHA256())
    
    pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    
    key_path.write_text(encrypt_key(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()), encoding="utf-8")
    return (pem_path, key_path)
    
    v = None

def hub_app(sessions, workspace_id):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    @app.middleware("http")
    async def auth(request, call_next):
        try:
            if request.headers.get("origin"):
                return JSONResponse({"code": "BROWSER_ORIGIN_BLOCKED"}, status_code=403)
            token = request.headers.get("authorization", "")
            if not token.startswith("Bearer "):
                return JSONResponse({"code": "PAIRING_REQUIRED"}, status_code=401)
            with sessions.begin() as db:
                owner = db.get(Workspace, workspace_id)
                config = profile(db, owner).preferences.get("experience_hub", {})
                peer = db.scalar(select(HubPeer).where(HubPeer.workspace_id == workspace_id, HubPeer.token_hash == hash_token(token[7:]), HubPeer.active.is_(True)))
            if not config.get("enabled") and db.get(User, owner.owner_id).active and peer:
                None(None, None)
                return JSONResponse({"code": "PAIRING_REVOKED"}, status_code=401)
            peer.last_seen = time.time()
            request.state.root = Path(config["directory"]) / "MuxuExperience" / config["fingerprint"]
            None(None, None)
            while 1:
                while 1:
                    return await call_next(request)
        except:
            pass
    
    @app.post("/contributions/{content_hash}")
    async def receive(content_hash: str, request: Request):
        try:
            from .experience_sync import atomic_file
            data = bytearray()
            async for chunk in request.stream():
                while 1:
                    while 1:
                        data.extend(chunk)
                        if not len(data) > 1_000_000:
                            continue
                        return JSONResponse({"code": "TOO_LARGE"}, status_code=413)
            content = verify_package(json.loads(data))
            if content["kind"] != "CONTRIBUTION" or digest(content) != content_hash:
                raise ValueError()
        except Exception:
            return
        destination = (request.state.root) / "incoming" / (content_hash + ".json")
        if not destination.exists():
            atomic_file(destination, content)
        return {"received": True, "hash": content_hash}
    
    @app.get("/latest")
    def latest(request: Request):
        from .experience_sync import read_bounded; path = (request.state.root) / "latest.json"
        if not path.exists():
            return JSONResponse({"code": "NO_RELEASE_YET"}, status_code=404)
        
        return read_bounded(path)
    
    return app

def server(sessions, workspace_id, config):
    import uvicorn; pem, encrypted = certificate(workspace_id, config["host"]); context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.minimum_version = ssl.TLSVersion.TLSv1_2; temporary = encrypted.with_suffix(".load-" + secrets.token_hex(8))
    
    try:
        with temporary.open("x", encoding="utf-8") as file:
            file.write(decrypt_key(encrypted.read_text(encoding="utf-8")))
        context.load_cert_chain(str(pem), str(temporary))
        temporary.unlink(missing_ok=True)
        configuration = uvicorn.Config(hub_app(sessions, workspace_id), host=config["host"], port=config["port"], access_log=False, log_config=None, limit_concurrency=12)
        configuration.load()
        configuration.ssl = context
        return uvicorn.Server(configuration)
    except:
        temporary.unlink(missing_ok=True)

def start_hub_daemon(sessions):
    def run():
        running, thread, identity = (None, None, None)
        try:
            with sessions() as db:
                pass
            found = [(a.workspace_id, a.preferences["experience_hub"]) for a in db.scalars(select(AssistantProfile)) if a.preferences.get("experience_hub", {}).get("enabled")]
            a = None
            None(None, None)
            current = None
            if current != identity:
                if running:
                    running.should_exit = True
                    if thread:
                        thread.join(timeout=5)
                identity = current
                thread = None
                running = None
                if found:
                    running = server(sessions, found[0][0], found[0][1])
                    thread = threading.Thread(target=running.run, daemon=True, name="experience-https")
                    thread.start()
            if found:
                state = "START_FAILED"
                with sessions.begin() as db:
                    ws = lock_workspace(db, found[0][0])
                    item = profile(db, ws)
                    config = item.preferences.get("experience_hub", {})
                    if config.get("runtime_status") != state:
                        item.preferences = {"experience_hub": {"runtime_status": state}}
                None(None, None)
                while 1:
                    time.sleep(3)
                    a = None
        except Exception as identity:
            pass
    
    thread = threading.Thread(target=run, daemon=True, name="experience-hub-controller"); thread.start()
    return thread

class HubIn(Strict):
    enabled: bool; host: str = "127.0.0.1"
    port: int = Field(default=8899, ge=1024, le=65_535)
    approved: bool = False
    auto_publish: bool = False

class PeerIn(Strict):
    label: str = Field(min_length=1, max_length=100)

def router(current, admin):
    api = APIRouter(prefix="/api/experience-hub")
    @api.get("")
    def status(ctx=Depends(admin)):
        db, _, ws = ctx
        
        p = local_addresses()
        
        p = None
    
    @api.post("")
    def configure(data: HubIn, ctx=Depends(admin)):
        from .experience_sync import AUTO_FEATURES; db, user, ws = ctx; lock_workspace(db, ws.id); assistant = profile(db, ws)
        if not data.enabled:
            assistant.preferences = {"experience_hub": {"enabled": False}, "exchange_sync": {"enabled": False}}
            db.commit()
            return {"enabled": False}
        elif data.approved and data.host not in local_addresses():
            raise DomainError("HUB_APPROVAL_REQUIRED", "请选择本机地址并确认仅开启公司经验接收", 409)
        
        for other in db.scalars(select(AssistantProfile).where(AssistantProfile.workspace_id != ws.id)):
            if not other.preferences.get("experience_hub", {}).get("enabled"):
                pass
            raise DomainError("HUB_ALREADY_CONFIGURED", "此电脑已配置另一个发布端，请先停用原发布端", 409)
        
        pem, _ = certificate(ws.id, data.host)
        
        public = base64.b64encode(signing_key(ws.id).public_key().public_bytes_raw()).decode()
        
        fingerprint = hashlib.sha256(base64.b64decode(public)).hexdigest(); directory = (settings().storage_dir.parent) / "company-experience"; directory.mkdir(parents=True, exist_ok=True)
        
        config = {"enabled": True, "host": data.host, "port": data.port, "directory": str(directory), "public_key": public, "fingerprint": fingerprint, "certificate": pem.read_text(encoding="utf-8")}
        
        assistant.preferences = {"experience_hub": config, "exchange_sync": {"enabled": True, "role": "OWNER", "directory": str(directory), "public_key": public, "fingerprint": fingerprint, "auto_share": False, "auto_publish": data.auto_publish, "auto_install": True, "features": list(AUTO_FEATURES), "category": "宠物装饰", "next_check": 0}}
        
        audit(db, user, ws, "LOCAL_EXPERIENCE_HUB_ENABLED", detail={"host": data.host, "port": data.port}); db.commit()
        return {"enabled": True, "fingerprint": fingerprint}
    
    @api.post("/peers")
    def invite(data: PeerIn, ctx=Depends(admin)):
        db, user, ws = ctx; config = profile(db, ws).preferences.get("experience_hub", {})
        if not config.get("enabled"):
            raise DomainError("HUB_DISABLED", "请先开启本机经验接收端", 409)
        token = secrets.token_urlsafe(40); peer = HubPeer(workspace_id=ws.id, label=data.label, token_hash=hash_token(token)); db.add(peer); db.flush()
        
        invitation = "MUXUH1." + base64.urlsafe_b64encode(canonical({"endpoint": "https://" + config["host"] + ":" + str(config["port"]), "certificate": config["certificate"], "public_key": config["public_key"], "fingerprint": config["fingerprint"], "token": token})).decode(); audit(db, user, ws, "EXPERIENCE_PEER_INVITED", peer.id); db.commit()
        return {"id": peer.id, "invitation": invitation, "notice": "连接码只显示这一次，通过公司渠道交给对应员工；不要发布到公开页面。"}
    
    @api.patch("/peers/{peer_id}")
    def toggle_peer(peer_id: str, data: EnabledIn, ctx=Depends(admin)):
        db, user, ws = ctx; peer = owned(db, HubPeer, peer_id, ws.id); peer.active = data.enabled; audit(db, user, ws, "EXPERIENCE_PEER_STATUS_CHANGED", peer.id); db.commit()
        return {"active": peer.active}
    
    return api
