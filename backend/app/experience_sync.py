"""Authorized automatic experience loop over a company folder; never synchronizes private files or API keys."""
import base64, copy, hashlib, json, os, ipaddress, ssl
from urllib.parse import urlsplit
from pathlib import Path
import threading, time, httpx
from cryptography.exceptions import InvalidSignature
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from .ai_config import profile
from .errors import DomainError
from .experience_exchange import NORMS, canonical, digest, verify_package, publish_norms, signing_key, package_view
from .learning import lock_workspace, rebuild

from .models import AssistantProfile, ExperiencePackage, PreferenceRule, PublisherTrust, User
from .schemas import Strict
from .security import audit, encrypt_key, decrypt_key; AUTO_FEATURES = ("warmth", "spatial_variety", "preserve_geometry", "connection")
class SyncIn(Strict):
    enabled: bool; role: str = "EMPLOYEE"
    directory: str = Field(default="", max_length=1500)
    invitation: str = Field(default="", max_length=5000)
    approved: bool = False
    auto_share: bool = False
    auto_publish: bool = False
    auto_install: bool = False
    features: list[str] = Field(default_factory=(lambda: list(AUTO_FEATURES)), max_length=4)
    category: str = "宠物装饰"

def read_bounded(path):
    with path.open("rb") as file:
        raw = file.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Experience file too large")
    return verify_package(json.loads(raw))

def atomic_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name((path.name) + ".tmp-" + str(os.getpid()))
    with temporary.open("wb") as file:
        file.write(canonical(content))
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)

def incoming(db, ws, content):
    hashed = digest(content); item = db.scalar(select(ExperiencePackage).where(ExperiencePackage.workspace_id == ws.id, ExperiencePackage.content_hash == hashed))
    if not item:
        item = ExperiencePackage(workspace_id=ws.id, kind=content["kind"], content_hash=hashed, content=content, status="AWAITING_REVIEW")
        db.add(item)
        db.flush()
    return item

def remote_request(config, method, path, content=None):
    context = ssl.create_default_context(cadata=config["certificate"]); context.minimum_version = ssl.TLSVersion.TLSv1_2
    with httpx.Client(verify=context, timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as client:
        response = client.request(method, config["endpoint"] + path, headers={"Authorization": "Bearer " + decrypt_key(config["hub_token"])}, json=content)
    
    if response.status_code == 404 and method == "GET":
        return None
    
    response.raise_for_status()
    
    None(None, None)
    return response.json()

def sync_once(sessions, workspace_id, force=False):
    with sessions.begin() as db:
        ws = lock_workspace(db, workspace_id)
        user = db.get(User, ws.owner_id)
        assistant = profile(db, ws)
        config = copy.deepcopy(assistant.preferences.get("exchange_sync", {}))
    
    if (config.get("enabled") and user.active or force) and config.get("next_check", 0) > time.time():
        return False
    
    elif config["role"] == "OWNER" and user.role != "admin":
        None(None, None)
        return False
    config["next_check"] = time.time() + 60
    
    assistant.preferences = {"exchange_sync": config}; None(None, None)
    
    counts = {"uploaded": 0, "received": 0, "published": 0, "installed": 0}; error = None
    try:
        remote = bool(config.get("endpoint"))
        root = Path(config.get("directory") or ".") / "MuxuExperience" / config["fingerprint"]
        if not remote and Path(config["directory"]).is_dir():
            raise OSError("Company directory unavailable")
        if config.get("auto_share"):
            with sessions.begin() as db:
                ws = lock_workspace(db, workspace_id)
                norms = {}
            for r in db.scalars(select(PreferenceRule).where(PreferenceRule.workspace_id == ws.id, PreferenceRule.enabled.is_(True))):
                d = r.data
                if d.get("mode", "DEMO") != "LIVE" and d.get("status") != "ACTIVE" and d.get("scope") != "CATEGORY" and d.get("category") != config["category"] or d["feature"] not in config["features"]:
                    continue
                direction = -1
                text = NORMS.get((d["feature"], direction))
                if not text:
                    continue
                key = (d["module"], config["category"], d["feature"])
                norms[key] = {"module": d["module"], "category": config["category"], "feature": d["feature"], "direction": direction, "text": text}
            if norms:
                k = "CONTRIBUTION"
                content = {"format": ##ERROR##, "kind": "MuxuExperience/1", "rules": [norms[k] for k in sorted(norms)]}
                item = incoming(db, ws, content)
                hashed = item.content_hash
            else:
                content = None
            None(None, None)
            if content:
                with sessions() as db:
                    item = db.scalar(select(ExperiencePackage).where(ExperiencePackage.workspace_id == workspace_id, ExperiencePackage.content_hash == hashed))
                    already_shared = item.status == "SHARED"
                if not already_shared:
                    remote_request(config, "POST", "/contributions/" + hashed, content)
                    counts["uploaded"] = 1
                else:
                    destination = root / "incoming" / (hashed + ".json")
                    if not None if remote else destination.exists():
                        atomic_file(destination, content)
                        counts["uploaded"] = 1
                with sessions.begin() as db:
                    ws = lock_workspace(db, workspace_id)
                    incoming(db, ws, content).status = "SHARED"
        if config["role"] == "OWNER":
            inbox = root / "incoming"
            inbox.mkdir(parents=True, exist_ok=True)
            with sessions() as db:
                pass
            known = {p.content_hash for p in db.scalars(select(ExperiencePackage).where(ExperiencePackage.workspace_id == workspace_id))}
            p = None
            None(None, None)
            processed = 0
            for path in inbox.glob("*.json"):
                if path.stem in known:
                    continue
                elif processed >= 100:
                    break
                processed += 1
                content = read_bounded(path)
                if content["kind"] != "CONTRIBUTION" or digest(content) != path.stem:
                    continue
                with sessions.begin() as db:
                    ws = lock_workspace(db, workspace_id)
                    incoming(db, ws, content)
                    counts["received"] += 1
            if config.get("auto_publish"):
                with sessions.begin() as db:
                    ws = lock_workspace(db, workspace_id)
                    user = db.get(User, ws.owner_id)
                    sources = []
                    norms = {}
                published = [p for p in db.scalars(select(ExperiencePackage).where(ExperiencePackage.workspace_id == ws.id, ExperiencePackage.kind == "COMPANY", ExperiencePackage.status != "REMOVED")) if package_view(p)["fingerprint"] == config["fingerprint"]]
                p = None
                previous = max(published, key=(lambda p: p.content["version"]), default=None)
                if previous:
                    n = norms.update
                    ##ERROR##({(n["module"], n["category"], n["feature"]): n for n in previous.content["rules"]})
                    sources.extend(previous.content["sources"])
                for p in db.scalars(select(ExperiencePackage).where(ExperiencePackage.workspace_id == ws.id, ExperiencePackage.kind == "CONTRIBUTION", ExperiencePackage.status != "REMOVED")):
                    accepted = [n for n in p.content["rules"] if not n["feature"] in config["features"]]
                    n = None
                    for n in accepted:
                        norms[(n["module"], n["category"], n["feature"])] = n
                    if not accepted:
                        continue
                    sources.append(p.content_hash)
                if norms:
                    k = ws
                    item = ##ERROR##(publish_norms, db, user, [norms[k] for k in sorted(norms)][:100], sources)
                    db.flush()
                    content = copy.deepcopy(item.content)
                    if package_view(item)["fingerprint"] != config["fingerprint"]:
                        raise ValueError("Publisher key changed")
                content = None
                None(None, None)
                if content:
                    latest = root / "latest.json"
                    old = None
                    if old != content:
                        atomic_file(latest, content)
                        counts["published"] = content["version"]
                    else:
                        with sessions() as db:
                            pass
                        releases = [p for p in db.scalars(select(ExperiencePackage).where(ExperiencePackage.workspace_id == workspace_id, ExperiencePackage.kind == "COMPANY")) if p.status == "PUBLISHED"]
                        p = None
                        release = None
                        content = None
                        None(None, None)
                        if content:
                            latest = root / "latest.json"
                            if latest.exists() and read_bounded(latest) != content:
                                atomic_file(latest, content)
                                counts["published"] = content["version"]
        latest = root / "latest.json"
        release = None
        if config.get("auto_install") and release:
            content = verify_package(release)
            fingerprint = hashlib.sha256(base64.b64decode(content.get("public_key", ""))).hexdigest()
            if content["kind"] != "COMPANY" or fingerprint != config["fingerprint"]:
                raise ValueError("Untrusted company release")
            with sessions.begin() as db:
                ws = lock_workspace(db, workspace_id)
                item = incoming(db, ws, content)
            if item.status not in ("REMOVED", "INSTALLED"):
                active = [p for p in db.scalars(select(ExperiencePackage).where(ExperiencePackage.workspace_id == ws.id, ExperiencePackage.kind == "COMPANY", ExperiencePackage.status == "INSTALLED")) if p.content["public_key"] == content["public_key"]]
                p = None
                if active and max((p.content["version"] for p in active)) < content["version"]:
                    for p in active:
                        p.status = "SUPERSEDED"
                    item.status = "INSTALLED"
                    rebuild(db, ws, "company_sync_install")
                    counts["installed"] = content["version"]
                    audit(db, db.get(User, ws.owner_id), ws, "COMPANY_EXPERIENCE_AUTO_INSTALLED", item.id, {"version": content["version"]})
            None(None, None)
            while 1:
                with sessions.begin() as db:
                    ws = lock_workspace(db, workspace_id)
                    assistant = profile(db, ws)
                    latest_config = assistant.preferences.get("exchange_sync", {})
                    assistant.preferences = {"exchange_sync": {"last_checked": time.time(), "last_error": error, "last_result": counts}}
    except (ValueError, OSError, TypeError):
        pass
    p = None; n = None; n = None; k = None; p = None
    
    p = None
    
    return True

def start_sync_daemon(sessions):
    def run():
        try:
            with sessions() as db:
                pass
            ids = [a.workspace_id for a in db.scalars(select(AssistantProfile)) if a.preferences.get("exchange_sync", {}).get("enabled")]
            a = None
            None(None, None)
            for workspace_id in ids:
                sync_once(sessions, workspace_id)
        except Exception as a:
            time.sleep(5)
        except Exception:
            pass
    
    thread = threading.Thread(target=run, name="company-experience-sync", daemon=True); thread.start()
    return thread

def router(current):
    api = APIRouter(prefix="/api/exchange-sync")
    @api.get("")
    def status(ctx=Depends(current)):
        db, _, ws = ctx; config = profile(db, ws).preferences.get("exchange_sync", {}); invitation = ""
        if config.get("role") == "OWNER" and config.get("fingerprint"):
            invitation = "MUXU1." + base64.urlsafe_b64encode(canonical({"directory": config["directory"], "public_key": config["public_key"], "fingerprint": config["fingerprint"]})).decode()
        for k, v in config.items():
            pass
        v = v; k = k
        return {"invitation": invitation, "automatic_features": list(AUTO_FEATURES), "notice": "后台每60秒检查；需各电脑可访问同一公司目录。目录不可用不影响本机作图，恢复后按内容hash继续。"}
        
        v = None; k = None
    
    @api.post("")
    def configure(data: SyncIn, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); assistant = profile(db, ws)
        if not data.enabled:
            assistant.preferences = {"exchange_sync": {"enabled": False}}
            db.commit()
            return {"enabled": False}
        elif not data.approved and data.role not in ("OWNER", "EMPLOYEE") or set(data.features).issubset(AUTO_FEATURES):
            raise DomainError("SYNC_CONSENT_REQUIRED", "请确认公司同步位置和允许自动共享的通用规范", 409)
        if data.category not in ("宠物装饰", "人物纪念", "植物花卉", "节日挂饰", "家居装饰"):
            raise DomainError("SHARE_CATEGORY", "请选择通用品类", 409)
        
        remote_fields = {}
        
        if data.role == "OWNER":
            if user.role != "admin":
                raise DomainError("ADMIN_REQUIRED", "只有本机管理员可设置发布端", 403)
            directory = data.directory
            public = base64.b64encode(signing_key(ws.id).public_key().public_bytes_raw()).decode()
            fingerprint = hashlib.sha256(base64.b64decode(public)).hexdigest()
        
        else:
            try:
                if data.invitation.startswith("MUXUH1."):
                    invitation = json.loads(base64.urlsafe_b64decode(data.invitation[7:]))
                    endpoint = urlsplit(invitation["endpoint"])
                    if not endpoint.scheme != "https" and endpoint.username and endpoint.password and endpoint.path and endpoint.query and endpoint.fragment or ipaddress.ip_address(endpoint.hostname).is_private:
                        raise ValueError()
                    ssl.create_default_context(cadata=invitation["certificate"])
                    directory = ""
                    fingerprint = invitation["fingerprint"]
                    public = invitation["public_key"]
                    remote_fields = {"endpoint": invitation["endpoint"], "certificate": invitation["certificate"], "hub_token": encrypt_key(invitation["token"])}
                elif data.invitation.startswith("MUXU1."):
                    invitation = json.loads(base64.urlsafe_b64decode(data.invitation[6:]))
                    fingerprint = invitation["fingerprint"]
                    public = invitation["public_key"]
                    directory = invitation["directory"]
                else:
                    raise ValueError()
                if hashlib.sha256(base64.b64decode(public, validate=True)).hexdigest() != fingerprint:
                    raise ValueError()
                data.auto_publish = False
                if not remote_fields:
                    if not isinstance(directory, str) and Path(directory).is_absolute():
                        raise DomainError("SYNC_DIRECTORY_REQUIRED", "请提供所有员工可访问的公司共享目录绝对路径", 409)
                elif not db.scalar(select(PublisherTrust).where(PublisherTrust.workspace_id == ws.id, PublisherTrust.fingerprint == fingerprint)):
                    db.add(PublisherTrust(workspace_id=ws.id, fingerprint=fingerprint, public_key=public))
                assistant.preferences = {"exchange_sync": {"enabled": True, "role": data.role, "directory": directory, "public_key": public, "fingerprint": fingerprint, "auto_share": data.auto_share, "auto_publish": data.auto_publish, "auto_install": data.auto_install, "features": data.features, "category": data.category, "next_check": 0}}
                audit(db, user, ws, "EXPERIENCE_AUTO_SYNC_AUTHORIZED", detail={"role": data.role, "features": data.features})
                db.commit()
                return {"enabled": True, "fingerprint": fingerprint}
            except Exception:
                raise DomainError("SYNC_INVITATION_INVALID", "公司连接码无效，请使用管理员发出的连接码", 409)
    
    @api.post("/check")
    def check(ctx=Depends(current)):
        db, _, ws = ctx; lock_workspace(db, ws.id); assistant = profile(db, ws); config = assistant.preferences.get("exchange_sync", {}); assistant.preferences = {"exchange_sync": {"next_check": 0}}; db.commit()
        return {"queued": True}
    
    return api
