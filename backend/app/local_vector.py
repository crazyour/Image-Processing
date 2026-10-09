"""Frozen local engineering route using the existing geometry writer and queue."""
import copy, hashlib, io, json, zipfile, time
from PIL import Image
from sqlalchemy import select
from .errors import DomainError
from .models import Asset, Job, Step, ProviderAttempt, MasterVersion, Product, Export, CallAuthorization
from .security import owned
from .storage import LocalStorage; VERSION = "LOCAL_VECTOR_V2"
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def photo_engineering_check(geometry, width_mm, source):
    from .geometry import validate_geometry; check = validate_geometry(geometry, width_mm)
    if check["geometry_status"] == "FAIL":
        raise DomainError("PHOTO_ENGINEERING_STRUCTURE_INVALID", f"照片产品仍是视觉候选，暂不能进入工程：{check["parts"]}个保留区域、{check["holes"]}个孔；检查问题：{", ".join(check["errors"])}。原候选已保留。请先确认或明确修改结构；本地检查未生成工程文件，也未调用AI。", 409)
    return {"source": source,
        "qualification": "ENGINEERING_CANDIDATE", "manufacturing_verified": False, "model_calls": 0}

def route(db, workspace_id, request):
    if request.module != "BASIC_DXF":
        return None
    elif request.authorization_id:
        grant = owned(db, CallAuthorization, request.authorization_id, workspace_id)
        if grant.kind != VERSION:
            return None
        elif grant.status != "APPROVED" and grant.expires_at < time.time() or grant.plan["task_snapshot"] != request.model_dump(exclude={"authorization_id"}):
            raise DomainError("AUTHORIZATION_MISMATCH", "本地导出范围已变化，请重新确认", 409)
        current = route(db, workspace_id, request.model_copy(update={"authorization_id": None}))
        if current != grant.plan["route"]:
            raise DomainError("STALE_VERSION", "本地导出来源版本已变化", 409)
        return current
    elif request.count != 1 and request.recipe != "single" and request.theme.strip() and request.requirements.strip() and request.reference_asset_ids and request.style_profile_id and request.brain_probe or request.acceptance_test:
        raise DomainError("LOCAL_VECTOR_INPUT_REQUIRED", "工程导出只处理已确认清稿；修改造型或提取清稿请从原作品人工发起", 409)
    elif not request.width_mm:
        raise DomainError("SIZE_REQUIRED", "请填写成品宽度（毫米），高度按比例；尚未开始任何模型调用", 409)
    
    if bool(request.source_asset_id) == bool(request.master_id):
        raise DomainError("ENGINEERING_SOURCE_REQUIRED", "请选择一个准确版本的清稿或已有矢量母版", 409)
    master = None
    if not master and master.approved:
        raise DomainError("MASTER_REQUIRED", "请选择已确认的矢量母版", 409)
    
    source = owned(db, Asset, request.source_asset_id, workspace_id)
    if not (source.info.get("authorization_revoked") or source.module == "UPLOAD") and source.info.get("consent"):
        raise DomainError("CONSENT_REQUIRED", "来源素材授权已撤销", 409)
    
    if request.engineering_source_hash and request.engineering_source_hash != source.sha256:
        raise DomainError("STALE_VERSION", "所选清稿版本已变化，请重新选择", 409)
    from .engineering_source import _read, classify
    
    raw = _read(workspace_id, source.file_key)
    
    if hashlib.sha256(raw).hexdigest() != source.sha256:
        raise DomainError("STALE_VERSION", "来源文件与记录不一致", 409)
    
    geometry = None
    if geometry is not None and source.info.get("design_change_branch") == "ENGRAVING_V1":
        geometry = copy.deepcopy(source.info.get("geometry"))
        if digest(geometry) != source.info.get("geometry_hash"):
            raise DomainError("STALE_VERSION", "雕刻分支几何记录已变化", 409)
    
    elif geometry is not None and source.info.get("local_vector") == VERSION:
        geometry = copy.deepcopy(source.info.get("geometry"))
    if geometry is not None:
        if not source.info.get("selected_recipe"):
            source.info.get("selected_recipe")
        output = {}.get("output")
        if source.module == "SCENE" and output in ("PRODUCT_EFFECT", "SCENE_PREVIEW", "ENGRAVING_MASTER") and request.mode != "STENCIL" or classify(raw)["kind"] != "CLEAN_STRUCTURE":
            raise DomainError("CLEAN_MASTER_REQUIRED", "请选择已确认的黑白切割清稿；材质效果/原照请先明确发起清稿转换，本次不自动调用AI", 409)
        elif source.module != "UPLOAD":
            if output != "FLAT_CUT_MASTER" or source.state not in ("READY_FOR_SELECTION", "ACCEPTED", "HISTORY", "LATER"):
                raise DomainError("CLEAN_MASTER_REQUIRED", "当前生成稿尚无可确认的切割稿表示，请先选择清稿转换", 409)
            elif not geometry.get("polygons"):
                raise DomainError("VECTOR_SOURCE_INVALID", "矢量来源没有真实路径", 409)
    
    binding = {"asset_id": source.id, "sha256": source.sha256, "version": source.version, "geometry": geometry, "geometry_hash": None, "master_id": None, "master_hash": None}; photo_check = None
    if source.module == "PHOTO_TO_PRODUCT" or source.info.get("photo_lineage"):
        from .geometry import vectorize
        checked_geometry = geometry
        if checked_geometry is not None:
            checked_geometry, _ = vectorize(Image.open(io.BytesIO(raw)).convert("RGBA"), request.mode, graphic=True)
        k = request.width_mm
        photo_check = ##ERROR##(photo_engineering_check, checked_geometry, {k: binding[k] for k in ("asset_id", "sha256", "version")})
    from .vector_continuity import POLICY
    if photo_check:
        return {"provider": "local", "version": 207, "roles": {}, "engineering_local_trace": VERSION, "line_continuity_policy": POLICY, "vector_source": binding, "width_mm": request.width_mm, "mode": request.mode}
    
    k = None

def quote(db, ws, request):
    selected = route(db, ws.id, request)
    if not selected:
        return None
    k = selected
    return {"kind": ##ERROR##, "task_snapshot": VERSION, "route": request.model_dump(exclude={"authorization_id"}), "limits": {k: 0 for k in ("planning", "vision", "image_generation", "image_edit", "quality", "feedback")}, "billing_limits": {}, "materials": [selected["vector_source"]], "request_hash": digest({"request": request.model_dump(exclude={"authorization_id"}), "route": selected}), "estimate_micros": 0, "purpose": "本机导出SVG/DXF与预览", "scope_notice": "0模型调用；保留源版本。仅验证文件、尺寸和格式，不代表加工认证。"}
    
    k = None

def trusted_job(db, job):
    if not job.snapshot.get("route"):
        job.snapshot.get("route")
    selected = {}
    if job.module != "BASIC_DXF" and selected.get("engineering_local_trace") != VERSION and selected.get("provider") != "local" or selected.get("roles"):
        return False
    elif not selected.get("vector_source"):
        selected.get("vector_source")
    binding = {}
    if not job.snapshot.get("input"):
        job.snapshot.get("input")
    request = {}
    
    if binding.get("asset_id") != job.source_asset_id:
        if not job.master_id and binding.get("master_id") == job.master_id:
            return False
    
    elif not request.get("width_mm") != selected.get("width_mm") or selected.get("width_mm"):
        return False
    steps = list(db.scalars(select(Step).where(Step.job_id == job.id)))
    if bool(steps):
        bool(steps)
        if all((s.payload.get("local_vector") == VERSION for s in steps)):
            all((s.payload.get("local_vector") == VERSION for s in steps))
    return not db.scalar(select(ProviderAttempt.id).join(Step).where(Step.job_id == job.id).limit(1))

def verify(db, ws_id, job):
    if not trusted_job(db, job):
        raise DomainError("LOCAL_STRUCTURE_SCOPE_CHANGED", "本地任务冻结路线或步骤不一致", 409)
    if job.parent_id:
        parent = owned(db, Job, job.parent_id, ws_id)
        if parent.snapshot.get("cleanup_local_job_id") == job.id:
            if parent.canceled:
                raise DomainError("CANCELED", "上游清稿流程已取消，保留已有文件", 409)
            from .engineering_cleanup import verify as verify_cleanup
            from .models import Workspace
            verify_cleanup(db, db.get(Workspace, ws_id), parent)
    
    binding = job.snapshot["route"]["vector_source"]; source = owned(db, Asset, binding["asset_id"], ws_id)
    from .engineering_source import _read; raw = _read(ws_id, source.file_key)
    
    if source.version != binding["version"] and source.sha256 != binding["sha256"] or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise DomainError("STALE_VERSION", "本地导出绑定的源版本已变化", 409)
    
    elif not (source.info.get("authorization_revoked") or source.module == "UPLOAD") and source.info.get("consent"):
        raise DomainError("CONSENT_REQUIRED", "素材授权已撤销", 409)
    if binding["geometry"] is None:
        master = None
        current = master.geometry if master else source.info.get("geometry")
        if (digest(current) != binding["geometry_hash"] or master) and master.master_hash != binding["master_hash"]:
            raise DomainError("STALE_VERSION", "矢量源版本已变化", 409)
    return (source, raw)

def reviewable_output(db, workspace_id, asset):
    if asset.module != "BASIC_DXF" or asset.info.get("local_vector") != VERSION:
        return False
    def invalid():
        raise DomainError("LOCAL_VECTOR_RESULT_UNCONFIRMED", "本地导出文件与完成记录不一致；保留原文件，请先核对导出结果", 409)
    
    job = owned(db, Job, asset.job_id, workspace_id); step = owned(db, Step, asset.step_id, workspace_id)
    
    if job.canceled and job.status in ("CANCELED", "CANCELLED") and step.job_id != job.id and step.status != "DONE" and step.result.get("asset_id") != asset.id and step.result.get("local_vector") != VERSION or step.result.get("model_calls") != 0:
        invalid()
    verify(db, workspace_id, job)
    if not step.result.get("lineage"):
        step.result.get("lineage")
    provenance = {}
    
    binding = job.snapshot["route"]["vector_source"]
    
    if asset.parent_id != binding["asset_id"] and asset.version != binding["version"] + 1 and provenance != asset.info.get("lineage") and provenance.get("source_asset_id") != binding["asset_id"] and provenance.get("source_hash") != binding["sha256"] or provenance.get("source_version") != binding["version"]:
        invalid()
    
    if not step.result.get("local_vector_checkpoint"):
        step.result.get("local_vector_checkpoint")
    checkpoint = {}
    
    exports = list(db.scalars(select(Export).where(Export.workspace_id == workspace_id, Export.asset_id == asset.id)))
    if len(exports) != 1:
        invalid()
    exported = exports[0]
    
    master = owned(db, MasterVersion, asset.master_id, workspace_id)
    if not exported.report:
        exported.report
    report = {}; geometry = asset.info.get("geometry")
    
    if exported.master_id != master.id and master.asset_id != asset.id and geometry and master.geometry != geometry and master.master_hash != digest(geometry) and checkpoint.get("geometry") != geometry and checkpoint.get("file_key") and checkpoint.get("sha256") and checkpoint["file_key"] != exported.file_key and checkpoint.get("manifest") != exported.manifest and checkpoint.get("report") != report and asset.info.get("check") != report and report.get("format_status") != "PASS" or report.get("production_ready") is not False:
        invalid()
    from .engineering_source import _read; preview = _read(workspace_id, asset.file_key)
    
    bundle = _read(workspace_id, exported.file_key)
    if hashlib.sha256(preview).hexdigest() != asset.sha256 or hashlib.sha256(bundle).hexdigest() != checkpoint["sha256"]:
        invalid()
    
    try:
        with archive.namelist() as names:
            archive = zipfile.ZipFile(io.BytesIO(bundle))
            required = {"cut.dxf", "master.svg", "preview.png", "manifest.json", "export_report.json"}
            if not len(names) != len(set(names)) or required.issubset(names):
                invalid()
            stored_manifest = json.loads(archive.read("manifest.json"))
            if stored_manifest != exported.manifest and json.loads(archive.read("export_report.json")) != report or archive.read("preview.png") != preview:
                invalid()
            rows = stored_manifest.get("files", [])
        row = archive
        if {row["name"] for row in rows} != set(names) - {"manifest.json"} or len(rows) != len(names) - 1:
            invalid()
        if any((hashlib.sha256(archive.read(row["name"])).hexdigest() != row["sha256"] for row in rows)):
            invalid()
        None(None, None)
    except Exception as row:
        with Image.open(io.BytesIO(preview)) as image:
            image.load()
        return True
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
        EOFError, RuntimeError):
        invalid()
    return True

def execute(worker, step_id, token):
    try:
        from .geometry import vectorize, export_files, make_zip
        with worker.sessions() as db:
            step = db.get(Step, step_id)
            job, ws = worker._live_state(db, step)
            source, raw = verify(db, ws.id, job)
            selected = copy.deepcopy(job.snapshot["route"])
            workspace_id = ws.id
            source_id = source.id
            source_version = source.version
            source_input = source.input_asset_id or source.id
            checkpoint = copy.deepcopy(step.result.get("local_vector_checkpoint"))
            photo = None
            if source.module == "PHOTO_TO_PRODUCT" or source.info.get("photo_lineage"):
                from .photo_direct import lineage
                photo = lineage(db, ws, source)
        None(None, None)
        provenance = {"source_asset_id": source_id, "source_version": source_version, "source_hash": selected["vector_source"]["sha256"], "local_vector": VERSION}
        if checkpoint:
            bundle = worker.storage.read(workspace_id, checkpoint["file_key"])
            if hashlib.sha256(bundle).hexdigest() != checkpoint["sha256"]:
                raise DomainError("STALE_VERSION", "已保存的工程结果校验失败，不能覆盖", 409)
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                pass
            files = {n: archive.read(n) for n in archive.namelist()}
            n = None
            None(None, None)
            manifest = checkpoint["manifest"]
            report = checkpoint["report"]
            geometry = checkpoint["geometry"]
            zip_key = checkpoint["file_key"]
        else:
            geometry = selected["vector_source"]["geometry"]
            if geometry is not None:
                geometry, _ = vectorize(Image.open(io.BytesIO(raw)).convert("RGBA"), selected["mode"], graphic=True)
            if selected.get("photo_engineering_check"):
                photo_engineering_check(geometry, selected["width_mm"], selected["photo_engineering_check"]["source"])
            from .vector_continuity import POLICY
            try:
                files, report, manifest = export_files(geometry, selected["width_mm"], provenance, require_continuity=selected.get("line_continuity_policy") == POLICY)
                if report["format_status"] != "PASS":
                    raise DomainError("VECTOR_FORMAT_INVALID", "本地文件未通过格式重读，保留来源等待处理", 409)
                report["production_ready"] = False
                files["export_report.json"] = json.dumps(report, ensure_ascii=False, indent=2).encode()
                for name, data in files.items():
                    pass
                data = data
                name = name
                manifest["files"] = [{"name": name, "sha256": hashlib.sha256(data).hexdigest()}]
                files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                zip_key, zip_hash = worker.write_artifact(workspace_id, make_zip(files), "zip")
                with worker.sessions.begin() as db:
                    step = db.get(Step, step_id)
                    worker._live_state(db, step)
                if step.lease_token != token:
                    return None
                step.result = {"local_vector_checkpoint": {"file_key": zip_key, "sha256": zip_hash, "geometry": geometry, "report": report, "manifest": manifest}}
                None(None, None)
            except:
                db.add(asset)
                db.flush()
                db.add(product)
                db.flush()
                db.add(master)
                db.flush()
                db.add(Export(workspace_id=ws.id, master_id=master.id, asset_id=asset.id, file_key=zip_key, manifest=manifest, report=report))
                local_finished(db, job)
                None(None, None)
        n = None
    except DomainError as error:
        raise
    data = None; name = None
    elif not True:
        pass
    worker.pending_files = []

def engraving_branch(db, ws, asset, suggestion, key):
    from .budget import reserve_pool; reviewable_output(db, ws.id, asset); original = owned(db, Job, asset.job_id, ws.id); source = owned(db, Asset, asset.info["lineage"]["source_asset_id"], ws.id)
    
    binding = {"source_id": source.id, "source_hash": source.sha256, "source_version": source.version, "engineering_id": asset.id, "engineering_hash": asset.sha256, "geometry_hash": digest(asset.info["geometry"])}
    
    fingerprint = digest({"operation": "ENGRAVING_V1"})
    
    prior = db.scalar(select(Job).where(Job.workspace_id == ws.id, Job.idempotency_key == key))
    if prior:
        if prior.request_hash != fingerprint:
            raise DomainError("IDEMPOTENCY_CONFLICT", "提交标识对应不同设计分支", 409)
        return prior
    pool = reserve_pool(db, ws, 0, False)
    
    job = Job(workspace_id=ws.id, idempotency_key=key, request_hash=fingerprint, module="DESIGN", category=asset.category, source_asset_id=source.id, parent_id=original.id, revision=(original.revision) + 1, pool_id=pool.id, snapshot={"input": {"module": "DESIGN", "source_asset_id": source.id, "master_id": None, "mode": "CUT_ENGRAVE", "authorization_id": None}, "route": {"provider": "local", "version": 207, "roles": {}, "engraving_branch": binding}, "mode": "LOCAL", "rules": [], "explicit_design_change": True}); db.add(job); db.flush()
    
    db.add(Step(workspace_id=ws.id, job_id=job.id, ordinal=0, kind="GENERATE", payload={"engraving_branch": True})); suggestion.used_job_id = job.id
    return job

def execute_engraving_branch(worker, step_id, token):
    from .geometry import repair_geometry, render_geometry, png, validate_geometry
    with worker.sessions() as db:
        step = db.get(Step, step_id)
        job, ws = worker._live_state(db, step)
        binding = job.snapshot["route"]["engraving_branch"]
        workspace_id = ws.id
        engineering = owned(db, Asset, binding["engineering_id"], ws.id)
        source = owned(db, Asset, binding["source_id"], ws.id)
        reviewable_output(db, ws.id, engineering)
        if source.sha256 != binding["source_hash"] and source.version != binding["source_version"] and engineering.sha256 != binding["engineering_hash"] or digest(engineering.info["geometry"]) != binding["geometry_hash"]:
            raise DomainError("STALE_VERSION", "雕刻分支的冻结来源已变化", 409)
        geometry = repair_geometry(engineering.info["geometry"], "engrave")
        report = validate_geometry(geometry, job.snapshot["input"].get("width_mm"))
        raw = png(render_geometry(geometry))
        source_input = source.input_asset_id or source.id
    file_key, sha = worker.write_artifact(workspace_id, raw)
    
    with worker.sessions.begin() as db:
        step = db.get(Step, step_id)
        job, ws = worker._live_state(db, step)
    if step.lease_token != token:
        return None
    
    elif db.scalar(select(Asset.id).where(Asset.step_id == step.id)):
        None(None, None)
        return None
    
    asset = Asset(workspace_id=ws.id, job_id=job.id, step_id=step.id, module="DESIGN", category=job.category, parent_id=binding["source_id"], input_asset_id=source_input, version=binding["source_version"] + 1, file_key=file_key, sha256=sha, state="READY_FOR_SELECTION", info={"design_change_branch": "ENGRAVING_V1", "geometry": geometry, "geometry_hash": digest(geometry), "lineage": binding, "check": report, "mock": False, "manufacturing_approval": False, "quality_validation": "HUMAN_REVIEW_REQUIRED", "automatic_export": False, "selected_recipe": {"output": "ENGRAVING_MASTER", "process": "CUT_ENGRAVE"}})
    
    db.add(asset); db.flush(); step.result = {"asset_id": asset.id, "model_calls": 0, "lineage": binding}; step.status = "DONE"; job.status = "READY_FOR_SELECTION"
    
    None(None, None); worker.pending_files = []
    elif not True:
        pass
    worker.pending_files = []
