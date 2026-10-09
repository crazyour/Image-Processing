from .vision_review import review_for_local_validation
import copy, io, hashlib, logging, json, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select, update, func, case
from PIL import Image, ImageDraw
from .budget import begin_attempt, settle_attempt
from .config import settings
from .db import SessionLocal
from .errors import DomainError
from .geometry import compose_scene, export_files, font, make_zip, png, render_geometry, repair_geometry, spatial_signature, validate_geometry, vectorize
from .learning import process_learning, rank_score, suggestion_order, trace

from .models import Asset, BudgetPool, Credential, Export, Job, MasterVersion, Organization, Outbox, Product, ProviderAttempt, Schedule, Step, Suggestion, User, Workspace, uid
from .providers import MockProvider, OpenAIProvider, ProviderError, compact_context
from .schemas import JobIn, PlanOutput, DirectPlanOutput, Diagnosis, FeedbackSummary
from .security import decrypt_key, owned
from .services import process_approval, submit_job
from .storage import LocalStorage, safe_image; logger = logging.getLogger("art.worker")

SUGGESTIONS = {"place_left": ("产品放到左侧", "本机重新摆放产品，保留原背景和产品像素来源"), "place_right": ("产品放到右侧", "本机重新摆放产品，保留原背景和产品像素来源"), "place_surface": ("放低到展示面", "降低产品位置，请看图确认与桌面或展示面的接触位置"), "simpler": ("保留轮廓，减少细碎细节", "减少内部细节，保留主体意图；需重新比较造型"), "richer": ("增加有层次的细节", "增加内部层次，保留当前主体与构图方向"), "bridge": ("保留轮廓，加强连接", "新增可见连接作为修正提案；连接宽度不代表工艺安全阈值"), "engrave": ("内部细节改雕刻", "切除孔洞改为独立雕刻线，外轮廓不变"), "smaller": ("缩小画面中产品占比", "按产品宽度/图片宽度缩小投影；产品毫米尺寸与母版不变"), "room": ("换空间结构不同的场景", "更换背景空间；批准母版轮廓、孔洞与材质来源不变"), "texture": ("恢复母版原材质", "重新合成原始母版纹理；几何、孔洞与尺寸不变"), "likeness": ("关键特征更接近原照", "重读原照的耳形、口鼻和姿态；必须人工比较相似性"), "neutral_light": ("改为中性自然光", "更换背景光照，程序复用批准母版，不修改产品纹理与尺寸"), "dynamic": ("保留细节，调整动感姿态", "真实编辑生成新设计版本，需对比确认；不会覆盖已批准母版")}
def next_schedule_at(config, after=None):
    try:
        zone = ZoneInfo(config.timezone)
        raise DomainError("INVALID_SCHEDULE", "未配置有效工作日")
    except ZoneInfoNotFoundError:
        raise DomainError("INVALID_TIMEZONE", "无效时区")

class Worker:
    def __init__(self, sessions=SessionLocal, storage=None, provider_factory=None):
        self.sessions = sessions
        if not storage:
            storage
        self.storage = LocalStorage(); self.provider_factory = provider_factory; self.pending_files = []; self.desktop_lease_owner = None
    
    def write_artifact(self, workspace_id, data, suffix="png"):
        key, digest = self.storage.write(workspace_id, data, suffix); self.pending_files.append((workspace_id, key))
        return (key, digest)
    
    def discard_unpublished(self):
        for workspace_id, key in self.pending_files:
            self.storage.delete(workspace_id, key)
        self.pending_files = []
    
    def legacy_cutover(self):
        path = (settings().storage_dir.parent) / "preset-direct-cutover.json"
        if not path.exists():
            return None
        from .preset_direct import VERSION; value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("workflow_version") != VERSION:
            raise RuntimeError("无效的旧任务隔离标记；已停止任务领取")
        return datetime.fromisoformat(value["cutover_at"]).timestamp()
    
    def recover(self):
        cutover = self.legacy_cutover()
        from .desktop_leases import reclaim_exited; reclaim_exited(self.sessions, self.storage)
        
        with self.sessions.begin() as db:
            for step in db.scalars(select(Step).join(Job).where(Step.status == "RUNNING", Step.lease_until < time.time()).with_for_update(skip_locked=True)):
                pending = list(db.scalars(select(ProviderAttempt).where(ProviderAttempt.step_id == step.id, ProviderAttempt.status == "STARTED")))
                unknown = False
                for attempt in pending:
                    if attempt.provider not in ("mock", "local"):
                        attempt.status = "OUTCOME_UNKNOWN"
                        from .result_reconciliation import operation_for, has_receipt
                        logical = operation_for(db, attempt)
                        logical.status,
                            logical.cost_state = ("RESULT_UNKNOWN", "COST_UNCERTAIN")
                        logical.next_poll_at = 0
                        logical.recovery_until = time.time() + 1800
                        unknown = True
                        continue
                    settle_attempt(db, attempt.id, 0, "INTERRUPTED")
                step.status = "QUEUED"
                step.error_code = None
                step.lease_token = None
            from .models import ProviderOperation
            from .result_reconciliation import queue_existing
        
        for logical in db.scalars(select(ProviderOperation).join(Job, ProviderOperation.job_id == Job.id).where(ProviderOperation.status == "RESULT_UNKNOWN", ProviderOperation.next_poll_at > 0, ProviderOperation.next_poll_at <= time.time(), ProviderOperation.recovery_until > time.time())):
            waiting = db.get(Step, logical.step_id)
            if not waiting.status == "OUTCOME_UNKNOWN":
                continue
            elif db.get(Job, logical.job_id).canceled:
                continue
            queue_existing(db, waiting)
            waiting.attempts = max(0, (waiting.attempts) - 1)
            logical.next_poll_at = 0
        from .stopped_quality import restore_cancelled_quality
        if not cutover:
            restore_cancelled_quality(db, self.storage)
        None(None, None)
    
    def consume_event(self):
        cutover = self.legacy_cutover(); event_id = None
        
        try:
            with self.sessions.begin() as db:
                event = db.scalar(select(Outbox).where(Outbox.processed.is_(False), Outbox.attempts < 3).order_by(Outbox.created_at).with_for_update(skip_locked=True))
            if not event:
                return False
            event_id = event.id
            if event.kind == "REVIEW":
                process_learning(db, event)
            elif event.kind == "BATCH_FEEDBACK":
                from .batch_feedback import process
                process(db, event)
            elif event.kind == "APPROVAL":
                process_approval(db, event)
            None(None, None)
        except Exception:
            logger.warning("outbox retry scheduled: %s", type(exc).__name__)
        
        return True
    
    def schedule_tick(self):
        try:
            if not settings().schedule_enabled:
                return False
            elif self.legacy_cutover():
                return False
            elif settings().desktop and (settings().storage_dir.parent) / "pause.request".exists():
                return False
            with self.sessions.begin() as db:
                schedules = list(db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_at <= time.time()).with_for_update(skip_locked=True)))
            for schedule in schedules:
                ws = db.get(Workspace, schedule.workspace_id)
                user = db.get(User, ws.owner_id)
                backlog = len(list(db.scalars(select(Asset.id).where(Asset.workspace_id == ws.id, Asset.state.in_(["READY_FOR_SELECTION", "LATER"]), Asset.deleted.is_(False)))))
                if user.active and backlog >= schedule.backlog_limit:
                    schedule.last_reason = "BACKLOG_LIMIT"
                    schedule.next_at = next_schedule_at(schedule)
                    continue
                with db.begin_nested():
                    request = JobIn.model_validate(schedule.config)
                    route = db.get(Organization, ws.org_id).route
                    if not route["provider"] == "openai" and schedule.live_authorized:
                        raise DomainError("LIVE_NOT_AUTHORIZED", "定时付费调用未授权")
                    submit_job(db, ws, request, f"schedule:{schedule.id}:{int(schedule.next_at)}")
                schedule.last_reason
                while 1:
                    schedule.next_at = next_schedule_at(schedule)
                    None(None, None)
        except DomainError:
            schedule.last_reason = exc.code
    
    def claim(self):
        from .execution_plan import resolve_dependencies
        if settings().desktop and (settings().storage_dir.parent) / "pause.request".exists():
            return None
        cutover = self.legacy_cutover()
        
        with self.sessions.begin() as db:
            candidates = list(db.scalars(select(Step).join(Job).where(Step.status == "QUEUED", Step.available_at <= time.time(), Job.canceled.is_(False)).order_by(Job.created_at, case((Step.kind.in_(["AUTO_QA", "PUBLISH_IMAGE"]), 0), (Step.kind == "GENERATE" & Step.payload["surface_finish_stage"].as_string() == "FINISH" & Job.snapshot["route"]["human_review_contract"].as_string() == "HUMAN_IMAGE_REVIEW_V1", 1), else_=2), Step.ordinal).limit(160)))
            candidate = None
            for proposed in candidates:
                db.execute(update(Job).where(Job.id == proposed.job_id).values(revision=Job.revision))
                db.refresh(proposed)
                if proposed.status != "QUEUED":
                    continue
                job = db.get(Job, proposed.job_id)
                active = db.scalar(select(func.count()).select_from(Step).where(Step.job_id == job.id, Step.status == "RUNNING"))
                route = job.snapshot["route"]
                limit = route.get("max_inflight", 3)
                if not active >= limit or resolve_dependencies(db, proposed):
                    continue
                candidate = proposed
        if candidate is not None:
            return None
        token = uid()
        if settings().desktop and self.desktop_lease_owner is not None:
            from .desktop_leases import create_owner
            self.desktop_lease_owner = create_owner(self.sessions, self.storage)
        
        changed = db.execute(update(Step).where(Step.id == candidate.id, Step.status == "QUEUED").values(status="RUNNING", lease_until=time.time() + (settings().lease_seconds), lease_token=token, attempts=(Step.attempts) + 1))
        
        candidate.result = {"started_at": time.time(), "queue_ms": max(0, round((time.time() - (candidate.created_at)) * 1000))}
        
        None(None, None)
    
    def _context(self, step_id):
        with self.sessions() as db:
            step = db.get(Step, step_id)
            job = owned(db, Job, step.job_id, step.workspace_id)
        None(None, None)
        return (copy.deepcopy(job.snapshot), copy.deepcopy(step.payload), job.id,
            job.workspace_id)
    
    def _live_state(self, db, step):
        job = owned(db, Job, step.job_id, step.workspace_id); ws = db.get(Workspace, job.workspace_id); user = db.get(User, ws.owner_id)
        if not job.canceled or user.active:
            raise DomainError("CANCELED", "任务或员工已停用")
        
        if job.snapshot.get("route", {}).get("photo_craft"):
            from .photo_crafts import verify_job
            verify_job(db, ws, job)
        
        if job.snapshot.get("route", {}).get("engineering_cleanup"):
            from .engineering_cleanup import verify
            verify(db, ws, job)
        
        if job.source_asset_id:
            source = owned(db, Asset, job.source_asset_id, ws.id)
            if not source.info.get("surface_finish") and job.snapshot.get("route", {}).get("engineering_local_trace") != "LOCAL_VECTOR_V2" and job.snapshot.get("route", {}).get("engineering_cleanup"):
                from .surface_finish import verify_surface_finish
                verify_surface_finish(db, ws.id, source)
        engineering_binding = job.snapshot.get("route", {}).get("engineering_source_binding")
        
        if job.snapshot.get("route", {}).get("engineering_local_trace"):
            from .engineering_source import verify_local_structure_route
            verify_local_structure_route(db, ws.id, job)
        if engineering_binding:
            from .engineering_source import verify_binding
            verify_binding(db, ws.id, engineering_binding)
        
        if job.snapshot.get("route", {}).get("engineering_cut_semantics"):
            from .engineering_source import verify_cut_semantics
            verify_cut_semantics(db, ws.id, job.snapshot["route"])
        
        if job.snapshot.get("route", {}).get("scene_product_workflow") == "DIRECT_REFERENCE_EDIT_V1":
            from .scene_direct import verify_source
            verify_source(db, ws.id, job.snapshot["route"])
        
        for key in ("repair_of", "asset_id", "output_recovery_of"):
            if not step.payload.get(key):
                continue
            linked = owned(db, Asset, step.payload[key], ws.id)
            if not key == "output_recovery_of":
                continue
            elif not linked.sha256 != step.payload.get("output_recovery_source_hash"):
                pass
            raise DomainError("OUTPUT_RECOVERY_STALE", "重做所绑定的原图版本已变化")
        
        if not job.snapshot.get("route", {}).get("finish_source_binding"):
            job.snapshot.get("route", {}).get("finish_source_binding")
        finish_binding = {}
        for asset_key, hash_key in (("selected_asset_id", "selected_sha256"), ("structure_asset_id", "structure_sha256")):
            if not finish_binding.get(asset_key):
                continue
            bound = owned(db, Asset, finish_binding[asset_key], ws.id)
            if not bound.sha256 != finish_binding[hash_key] and hashlib.sha256(self.storage.read(ws.id, bound.file_key)).hexdigest() != finish_binding[hash_key]:
                pass
            raise DomainError("STALE_VERSION", "配色绑定的原稿已变化，已停止调用", 409)
        
        if step.payload.get("structure_asset_id"):
            bound = owned(db, Asset, step.payload["structure_asset_id"], ws.id)
            if bound.sha256 != step.payload.get("structure_sha256") or hashlib.sha256(self.storage.read(ws.id, bound.file_key)).hexdigest() != step.payload.get("structure_sha256"):
                raise DomainError("STALE_VERSION", "配色结构稿已变化，已停止调用", 409)
        
        for item in job.snapshot.get("references", []):
            ref = owned(db, Asset, item["asset_id"], ws.id)
            if ref.sha256 != item["sha256"] and ref.info.get("consent"):
                pass
            raise DomainError("CONSENT_REQUIRED", "参考素材已撤销或改变，请重新选择")
        
        scene_reference = job.snapshot.get("scene_reference")
        if scene_reference:
            asset = owned(db, Asset, scene_reference["asset_id"], ws.id)
            if asset.sha256 != scene_reference["sha256"]:
                raise DomainError("STALE_VERSION", "场景参照已改变，请重新选择素材")
            elif job.snapshot["route"]["provider"] == "local" and hashlib.sha256(self.storage.read(ws.id, asset.file_key)).hexdigest() != asset.sha256:
                raise DomainError("STALE_VERSION", "本地场景参照文件已变化，未调用AI", 409)
        
        elif job.master_id:
            master = owned(db, MasterVersion, job.master_id, ws.id)
            if not master.approved:
                raise DomainError("WAITING_INPUT", "原母版批准已撤销，请重新选择母版")
            owned(db, Asset, master.asset_id, ws.id)
            binding = job.snapshot.get("local_scene_source")
            if binding:
                product = owned(db, Asset, binding["asset_id"], ws.id)
                raw_key = master.facts.get("raw_key")
                expected = product.sha256 if raw_key == product.file_key else product.info.get("raw_hash")
                if master.asset_id != product.id and master.master_hash != binding["master_hash"] and product.sha256 != binding["sha256"] and expected and raw_key and hashlib.sha256(self.storage.read(ws.id, raw_key)).hexdigest() != expected:
                    raise DomainError("STALE_VERSION", "冻结的产品原图或蒙版已变化，未执行场景合成", 409)
        return (job, ws)
    
    def _activate_transport_fallback(self, db, step, job, ws, attempt, error):
        return False
    
    def call(self, step_id, role, context, reference=None):
        if not checkpoint or known_invalid_image:
            raise ProviderError("DOWNLOAD_PENDING", detail={"stage": stage, "next_action": "图片结果地址已保存，检查网络与磁盘后点击恢复此项，只重新下载，不重新生图"}) from None
        if not attempt.result.get("response_checkpoint") or known_invalid_image:
            raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "local_result_processing"}) from None
        
        with self.sessions.begin() as db:
            completed_attempt = db.get(ProviderAttempt, attempt_id)
            completed_attempt.duration_ms = (completed_attempt.duration_ms or 0) + int((time.monotonic() - started_at) * 1000)
            from .models import ProviderOperation
            logical = db.get(ProviderOperation, operation_id)
            if logical.winner_attempt_id and completed_attempt.status in ("FAILED", "RESULT_DISCARDED"):
                logical.status = "FAILED"
    
    def resume_download(self, step_id):
        try:
            from .artifact_download import download_image
            from .provider_diagnostics import transport_diagnostic
            from urllib.parse import urlsplit
            import httpx
            with self.sessions() as db:
                step = db.get(Step, step_id)
                job, ws = self._live_state(db, step)
                attempts = list(db.scalars(select(ProviderAttempt).where(ProviderAttempt.step_id == step_id, ProviderAttempt.role == "image").order_by(ProviderAttempt.created_at.desc())))
                attempt = next((a for a in attempts), None)
            if not attempt:
                return None
            elif attempt.result.get("file_key"):
                None(None, None)
                return self.storage.read(ws.id, attempt.result["file_key"])
            saved = attempt.result.get("download_checkpoint")
            if not saved:
                raise DomainError("OUTCOME_UNKNOWN", "没有可恢复图片地址，请核对平台记录")
            from .ai_connection_center import CALL_CAP
            k = {"base_url": attempt.input_versions["base_url"], "model": attempt.model, "protocol": attempt.input_versions["protocol"], "capability": CALL_CAP.get(attempt.capability)}
            cfg = None
            from .provider_policy import require_openai_connection, require_allowed_download
            require_openai_connection(cfg)
            source = decrypt_key(saved["source_ciphertext"])
            require_allowed_download(cfg, source)
            meta = saved["meta"]
            workspace_id = ws.id
            identity = attempt.id
            None(None, None)
            resumed_at = time.monotonic()
            while 1:
                exhausted = None
                with self.sessions.begin() as db:
                    self._live_state(db, db.get(Step, step_id))
                    attempt = db.get(ProviderAttempt, identity)
                    checkpoint = dict(attempt.result["download_checkpoint"])
                    checkpoint = {"provider": attempt.provider, "model": attempt.model, "request_id": attempt.request_id, "response_id": attempt.response_id, "task_id": meta.get("task_id"), "remote_image_index": 0, "remote_url_created_at": attempt.created_at, "remote_status": "REMOTE_GENERATED", "estimated_cost": {"currency": "CNY", "amount": attempt.input_versions.get("unit_price_cny"), "kind": "ESTIMATE"}}
                    attempt.usage = meta.get("usage", attempt.usage)
                    if not attempt.result.get("download"):
                        attempt.result.get("download")
                    download = dict({})
                    count = int(download.get("attempts", 1))
                    if count >= 3:
                        old_error = attempt.result.get("error", {})
                        code = "DOWNLOAD_FAILED"
                        attempt.result = {"download": {"state": "DOWNLOAD_FAILED"}, "error": {"code": code, "stage": "download"}}
                        exhausted = ProviderError(code, attempt.request_id, {"stage": "download", "next_action": "原结果下载已达三次上限；不会重新生图，请保留记录核对网络或原结果"})
                    else:
                        count += 1
                        history = [{"attempt": count, "started_at": time.time()}]
                        attempt.result = {"download_checkpoint": checkpoint, "download": {"state": "DOWNLOADING", "attempts": count, "max_attempts": 3, "history": history}}
                None(None, None)
                while 1:
                    if exhausted:
                        raise exhausted
                    try:
                        downloaded = download_image(source, cfg)
                        if self._context(step_id)[0].get("route", {}).get("engineering_cleanup"):
                            from .engineering_cleanup import save_provider_original
                            save_provider_original(self, workspace_id, identity, downloaded)
                        data, _ = safe_image(downloaded)
                        with self.sessions.begin() as db:
                            self._live_state(db, db.get(Step, step_id))
                            key, sha = self.storage.write(workspace_id, data)
                            attempt = db.get(ProviderAttempt, identity)
                            attempt.output_hash = sha
                            attempt.cost_kind = "ESTIMATE"
                            result = {"file_key": key, "download_resumed": True, "download": {"state": "LOCAL_SAVED", "saved_at": time.time()}, "timing_ms": {"provider": meta.get("provider_ms"), "download_resume_and_save": round((time.monotonic() - resumed_at) * 1000)}, "diagnostics": {"artifact_status": "ARTIFACT_READY", "stage": "saved", "http_status": 200}}
                            if result.get("error"):
                                result["prior_download_error"] = result.pop("error")
                            settle_attempt(db, identity, attempt.reserved, "DONE", result, meta)
                            from .result_reconciliation import operation_for, claim_result
                            logical = operation_for(db, attempt)
                            claim_result(db, logical, identity)
                            logical.status = "RECOVERED"
                            logical.cost_state = attempt.cost_kind
                        return data
                        k = None
                    except:
                        pass
            return data
        except (OSError, ValueError, httpx.HTTPError) as status:
            detail.update(stage="download", http_status=status, request_id=meta.get("request_id"), image_received=True, image_saved=False, error_category="DOWNLOAD_TRANSPORT_ERROR", message="供应商已生成图片；原结果下载尚未完成，没有重新生图。", remote_status="REMOTE_GENERATED", download_attempt=count, result_refresh="NOT_SUPPORTED_BY_CURRENT_SYNC_ADAPTER", next_action="已保留原生成记录，下载达到上限后停止；不会重新调用图片模型")
        if count >= 3:
            raise ProviderError(code, meta.get("request_id"), detail) from None
        time.sleep(0.5)
    
    def optional_call(self, step_id, role, context, reference=None):
        try:
            return self.call(step_id, role, context, reference)
        except (ProviderError, DomainError):
            raise
    
    def _plan_direct(self, step_id, token):
        from .preset_direct import compile_prompt, direct_planning_context, INTENT_PRIORITY_VERSION; snapshot, _, job_id, workspace_id = self._context(step_id); request = snapshot["input"]
        if not request["module"] != "DESIGN" or snapshot.get("custom_direct"):
            raise DomainError("PRESET_ROUTE_INVALID", "仅自定义创意可使用此规划入口", 409)
        priority = snapshot["route"].get("selected_intent_priority") == INTENT_PRIORITY_VERSION; target, pending_recipe = direct_planning_context(request, intent_priority=priority)
        
        references, manifest = self._direct_reference_inputs(step_id, snapshot, {"selected_recipe": pending_recipe}); k = used; context = {"input": {k: request.get(k) for k in ("module", "category", "theme", "requirements", "count", "preset_family_id", "preset_process_id", "preset_material_id", "preset_output_mode", "preset_scene_card_id", "surface_finish")}, "direct_custom": True, "output_target": target, "selected_recipe": pending_recipe, "image_manifest": {"mode": "SELECTED_REFERENCES", "images": manifest}, "planning_instruction": "Only choose one concrete creative direction per requested image. Return each candidate index once; do not evaluate, rewrite, choose models or restate fixed rules. The selected product category, process, material, output target and image roles are fixed."}
        if snapshot.get("explicit_feedback"):
            context["scoped_human_feedback"] = snapshot["explicit_feedback"]
            context["feedback_instruction"] = "These are already filtered, frozen employee soft preferences for this task. Use them only when compatible; current employee words, product facts, process and output target win."
        memory = snapshot["route"].get("design_memory")
        if memory:
            for k, v in target.items():
                pass
            v = v
            k = k
            context["output_target"] = {k: v}
            context["recent_direction_use"] = memory["recent"][:6]
            context["memory_instruction"] = "These are scoped usage records, not user preferences or quality approvals. Keep current words and fixed template constraints; propose new concrete directions without repeating these summaries."
        briefs = DirectPlanOutput.model_validate(plan).model_dump()["briefs"]; expected = list(range(1, request["count"] + 1))
        if not len(briefs) != request["count"]:
            row = brief if memory and memory["mode"] == "CACHED_PLAN" else signature
            if [row["candidate_index"] for row in briefs] != expected:
                raise DomainError("INVALID_PLAN", "本批规划数量与请求不一致，未开始生成图片", 409)
        
        with self.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, _ = self._live_state(db, step)
        if step.lease_token != token:
            return None
        memory_rows = None
        if memory:
            if memory["mode"] == "CACHED_PLAN":
                memory_rows = snapshot["route"]["design_plan_cache"]["candidates"]
            else:
                from .design_memory import custom_rows, history, signature
                from .intent_sources import design_batch_variations
                from .models import Workspace
                past = history(db, db.get(Workspace, workspace_id), memory["scope_key"], exclude_grant=request.get("authorization_id"))
                used = set(past["used"])
                selected = []
                choices = design_batch_variations()
                for brief in briefs:
                    variation = next((v for v in choices), None)
                    if variation is not None:
                        raise DomainError("DESIGN_DIRECTIONS_EXHAUSTED", "本次规划重复已用方向，未开始生成；请修改要求后重新确认", 409)
                    used.add(signature("CUSTOM", brief["execution_direction"]))
                    selected.append(variation)
                if not snapshot.get("explicit_feedback"):
                    snapshot.get("explicit_feedback")
                memory_rows = custom_rows(request, briefs, selected, [], intent_priority=priority)
        for direction in enumerate(briefs):
            (index, brief)
            prompt, recipe = compile_prompt(request, custom_direction=direction, intent_priority=priority)
            from .preset_direct import with_human_feedback
            if not snapshot.get("explicit_feedback"):
                snapshot.get("explicit_feedback")
            prompt = with_human_feedback(prompt, [])
            if memory_rows:
                recipe = memory_rows[index]["selected_recipe"]
                prompt = memory_rows[index]["execution_prompt"]
            db.add(Step(workspace_id=workspace_id, job_id=job_id, ordinal=index + 1, kind="GENERATE", payload={"index": index, "brief": {"execution_prompt": prompt}, "selected_recipe": recipe}))
        step.result = {"plan": {"briefs": briefs}, "planner_calls": 1}; step.status = "DONE"; job.status = "QUEUED"; brief["execution_direction"](None, None, None); k = None; v = None; k = None; row = None
    
    @staticmethod
    def _direct_role_text(role):
        return {"CURRENT_PRODUCT": "图片1当前选中产品稿是唯一编辑对象；保持本次未授权改变的几何、材料和细节，不从原照重新创作。", "ORIGINAL_IDENTITY_REFERENCE": "可追溯的最初原照；仅用于原主体、数量、姿态及辨识特征，不是当前编辑对象，不复制样板主体。", "SOURCE_PHOTO": "本次唯一主体原照；决定人物/宠物身份、数量、姿态、服装与关系。", "CONNECTION_STYLE_REFERENCE": "连接与表现样板；只参考连接方式、线条和外框，不得替换或借用其人物、服装、姿态与主体。", "CREATIVE_REFERENCE": "本次创新参考；只提供造型、构图或视觉语言灵感，不得替换本次产品类别和固定工艺。", "PROTECTED_PRODUCT": "当前受保护产品；只可执行本次明确授权的变化，不改变产品事实。", "SELECTED_PRODUCT_COLOR_SOURCE": "当前受保护产品和原配色来源；本次只执行明确的材料或配色变化。", "ASSEMBLY_TEMPLATE": "真实功能/装配模板；仅保留本次已绑定的固定几何与装配关系。", "FUNCTIONAL_GEOMETRY_TEMPLATE": "真实功能几何模板；只锁定实际杆、挂点、侧装或固定结构，不改变所选成品工艺。", "TARGET_COLOR_SWATCH": "本次真实目标色卡；只决定授权区域的颜色与纹理。", "SELECTED_PRODUCT": "本次锁定产品；场景只改变环境，不改变产品。", "SCENE_REFERENCE": "本次场景参考；只决定环境、构图和光线。"}.get(role, "")
    
    def _direct_reference_inputs(self, step_id, snapshot, payload):
        try:
            module = snapshot["input"]["module"]
            request = snapshot["input"]
            manifest = []
            references = []
            with self.sessions() as db:
                step = db.get(Step, step_id)
                _, ws = self._live_state(db, step)
                workspace_id = ws.id
                memory = snapshot.get("route", {}).get("design_memory")
            if memory:
                from .design_memory import validate_cache
                from .intent_sources import verify_bindings
                from .preset_direct import scoped_human_feedback
                from .authorization import digest
                validate_cache(db, ws, memory)
                verify_bindings(db, ws.id, memory["bindings"])
                if not snapshot.get("explicit_feedback"):
                    snapshot.get("explicit_feedback")
                frozen_feedback = []
                if frozen_feedback:
                    r = "LIVE"
                live_feedback = []
                if digest(live_feedback) != memory["feedback_hash"]:
                    raise DomainError("DESIGN_FEEDBACK_CHANGED", "本次规划使用的人工反馈已撤回或变化，请重新确认；没有新增调用", 409)
            bindings = []
            if snapshot.get("r2_operation"):
                from .intent_sources import verify_bindings
                frozen = snapshot["r2_operation"]["bindings"]
                verify_bindings(db, workspace_id, frozen)
                bindings = [(r["asset_id"],
    
    r["role"]) for r in frozen]
                r = reference_role(scoped_human_feedback, db, ws, request, only_ids=[r["evidence_id"] for r in frozen_feedback])
            elif snapshot.get("direct_operation"):
                from .photo_direct import read_binding, require_slot
                operation = snapshot["direct_operation"]
                if operation.get("photo"):
                    current = owned(db, Asset, request["source_asset_id"], workspace_id)
                    if require_slot(db, ws, current, exclude_job_id=step.job_id) != operation["photo"]["lineage"]:
                        raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "照片来源与冻结次数不符", 409)
                for row in operation["bindings"]:
                    read_binding(db, workspace_id, row)
                bindings = [(row["asset_id"], row["role"]) for row in operation["bindings"]]
                row = None
            elif module == "PHOTO_TO_PRODUCT" and snapshot.get("photo_direct"):
                from .photo_direct import read_binding, require_slot
                if snapshot["photo_direct"].get("lineage"):
                    current = owned(db, Asset, request["source_asset_id"], workspace_id)
                    if require_slot(db, ws, current, exclude_job_id=step.job_id) != snapshot["photo_direct"]["lineage"]:
                        raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "已冻结照片次数与来源不一致，保留记录等待确认", 409)
                for binding in snapshot["photo_direct"]["bindings"]:
                    read_binding(db, workspace_id, binding)
                bindings = [(row["asset_id"], row["role"]) for row in snapshot["photo_direct"]["bindings"]]
                row = None
            elif module == "DESIGN" and snapshot.get("custom_direct"):
                from .preset_direct import custom_design_reference_bindings
                if not payload.get("selected_recipe"):
                    payload.get("selected_recipe")
                    if not snapshot.get("selected_recipe"):
                        snapshot.get("selected_recipe")
                recipe = {}
                bindings = custom_design_reference_bindings(request, frozen_references=snapshot.get("references", []), selected_recipe=recipe)
                expected_operation = "image_generation"
                if snapshot.get("route", {}).get("direct_reference_operation") != expected_operation:
                    raise DomainError("AUTHORIZATION_MISMATCH", "已冻结授权与当前参考图操作不一致，任务已暂停；不会重复规划或自动扩大授权", 409)
            elif module == "DESIGN" and snapshot.get("preset_candidates"):
                from .preset_direct import preset_design_reference_bindings, preset_original_eligible
                if not preset_original_eligible(request):
                    raise DomainError("PRESET_ROUTE_INVALID", "旧冻结候选与当前操作不匹配，保留记录等待人工处理", 409)
                if not payload.get("selected_recipe"):
                    payload.get("selected_recipe")
                recipe = {}
                bindings = preset_design_reference_bindings(request, frozen_references=snapshot.get("references", []), selected_recipe=recipe)
                expected_operation = "image_generation"
                if snapshot.get("route", {}).get("direct_reference_operation") != expected_operation:
                    raise DomainError("AUTHORIZATION_MISMATCH", "已冻结预设授权与当前参考图操作不一致，任务已暂停；不会重新分配候选", 409)
            elif module == "DESIGN" and request.get("finish_source_asset_id"):
                bindings.append((request["finish_source_asset_id"], "SELECTED_PRODUCT_COLOR_SOURCE"))
            elif module == "DESIGN" and request.get("source_asset_id"):
                bindings.append((request["source_asset_id"], "PROTECTED_PRODUCT"))
            elif request.get("source_asset_id"):
                bindings.append((request["source_asset_id"],
    
    "SELECTED_PRODUCT"))
                if module == "PHOTO_TO_PRODUCT" and snapshot.get("suggestion"):
                    prior = owned(db, Asset, request["source_asset_id"], workspace_id)
                    if prior.input_asset_id:
                        bindings.append((prior.input_asset_id,
    "ORIGINAL_IDENTITY_REFERENCE"))
                    elif module == "SCENE" and request.get("master_id"):
                        master = owned(db, MasterVersion, request["master_id"], workspace_id)
                        bindings.append((master.asset_id,
    "SELECTED_PRODUCT"))
            elif not module in ("DESIGN", "PHOTO_TO_PRODUCT") and snapshot.get("custom_direct") and snapshot.get("preset_candidates") and snapshot.get("photo_direct") and snapshot.get("direct_operation") and snapshot.get("r2_operation"):
                reference_role = "CREATIVE_REFERENCE"
                bindings.extend(((row["asset_id"], reference_role) for row in snapshot.get("references", [])))
            if not module == "DESIGN" and snapshot.get("custom_direct") and snapshot.get("preset_candidates") and snapshot.get("direct_operation") and snapshot.get("r2_operation"):
                if request.get("assembly_template_asset_id"):
                    if not payload.get("selected_recipe"):
                        payload.get("selected_recipe")
                        if not snapshot.get("selected_recipe"):
                            snapshot.get("selected_recipe")
                    recipe = {}
                    template_role = "ASSEMBLY_TEMPLATE"
                    bindings.append((request["assembly_template_asset_id"], template_role))
                bindings.extend(((asset_id, "TARGET_COLOR_SWATCH") for asset_id in request.get("color_swatch_asset_ids", [])))
            if not module == "SCENE" and request.get("scene_reference_asset_id") and snapshot.get("direct_operation") and snapshot.get("r2_operation"):
                bindings.append((request["scene_reference_asset_id"], "SCENE_REFERENCE"))
            if len(bindings) > 8:
                raise DomainError("REFERENCE_LIMIT", "当前接口最多接收8张参考图，请减少本次参考素材", 409)
            for asset_id, role in bindings:
                asset = owned(db, Asset, asset_id, workspace_id)
                if not asset.module == "UPLOAD" and asset.info.get("consent"):
                    raise DomainError("CONSENT_REQUIRED", "参考素材授权已撤销", 409)
                raw = self.storage.read(workspace_id, asset.file_key)
                if hashlib.sha256(raw).hexdigest() != asset.sha256:
                    raise DomainError("STALE_VERSION", "所选图片已变化，请重新提交", 409)
                encoded, _ = safe_image(raw)
                references.append(encoded)
                if not snapshot.get("photo_direct"):
                    pass
                manifest.append({"position": len(references), "role": role, "asset_id": asset.id, "sha256": hashlib.sha256(encoded).hexdigest(), "instruction": self._direct_role_text(role)})
            None(None, None)
            return (references, manifest)
            r = None
            r = None
            row = None
            row = None
        except OSError:
            raise DomainError("REFERENCE_FILE_MISSING", "所选参考图片文件缺失，请重新上传或重新选择", 409) from None
        
        return (references, manifest)
    
    def _generate_direct(self, step_id, token):
        snapshot, payload, job_id, workspace_id = self._context(step_id); request = snapshot["input"]; module = request["module"]
        if module not in ("DESIGN", "PHOTO_TO_PRODUCT", "SCENE"):
            raise DomainError("PRESET_ROUTE_INVALID", "该任务不属于图像直出流程", 409)
        elif not payload.get("brief"):
            payload.get("brief")
        prompt = {}.get("execution_prompt")
        if not prompt:
            raise DomainError("PRESET_ROUTE_INVALID", "本次所选配方未固定，不能出图", 409)
        references, manifest = self._direct_reference_inputs(step_id, snapshot, payload)
        
        if not (module in ("PHOTO_TO_PRODUCT", "SCENE") or module == "DESIGN") and request.get("finish_source_asset_id") and references:
            raise DomainError("SOURCE_REQUIRED", "请选择原照或产品图", 409)
        if references:
            prompt += "\n" + "\n".join((f"图片{item["position"]}：{item["role"]}。{self._direct_role_text(item["role"])}" for item in manifest))
        k = self
        
        if not payload.get("selected_recipe"):
            payload.get("selected_recipe")
        
        context = {"input": {k: request.get(k) for k in ("module", "category", "theme", "requirements", "source_asset_id", "master_id", "surface_finish")}, "brief": {"execution_prompt": prompt, "detail": request.get("explicit_detail") or "balanced"}, "preset_direct_prompt": prompt, "selected_recipe": snapshot.get("selected_recipe"), "preset_candidate": payload.get("preset_candidate"), "image_manifest": {"mode": "SELECTED_REFERENCES", "images": manifest}, "index": payload.get("index", 0), "capability": "image_generation"}; scene_layer = None; scene_proof = None
        if module == "SCENE" and references:
            if not context["selected_recipe"]:
                context["selected_recipe"]
            if {}.get("visual_rule_version") == "VISUAL_PRESENTATION_20261001_R3":
                from .visual_rules import locked_layer
                scene_layer = locked_layer(references[0])
                if scene_layer is None:
                    if not context["selected_recipe"].get("alpha_background_instruction"):
                        raise DomainError("SCENE_RECIPE_INCOMPLETE", "旧场景任务缺少产品合成指令，本次未调用AI。请用相同素材重新确认任务。", 409)
                    prompt += context["selected_recipe"]["alpha_background_instruction"]
                    context["preset_direct_prompt"] = prompt
                    context["brief"]["execution_prompt"] = prompt
                    context["scene_background_only"] = True
        generated = self.call(step_id, "image", context, None); image_bytes, _ = safe_image(generated)
        if scene_layer is None:
            from .visual_rules import compose_locked
            image_bytes, scene_proof = compose_locked(image_bytes, scene_layer)
        
        finish_proof = None
        if not snapshot.get("direct_operation"):
            snapshot.get("direct_operation")
        
        finish_binding = {}.get("finish_structure")
        if finish_binding:
            from .surface_finish import compose_finish
            with self.sessions() as db:
                source = owned(db, Asset, finish_binding["asset_id"], workspace_id)
                if source.version != finish_binding["version"] or source.sha256 != finish_binding["sha256"]:
                    raise DomainError("STALE_VERSION", "配色的结构来源已变化，保留已生成原图等待处理", 409)
                original = self.storage.read(workspace_id, source.file_key)
            if hashlib.sha256(original).hexdigest() != finish_binding["sha256"]:
                raise DomainError("STALE_VERSION", "原结构文件校验失败，未应用配色蒙版", 409)
            image_bytes, finish_proof = compose_finish(original, image_bytes)
            if finish_proof["mask_sha256"] != finish_binding["mask_sha256"]:
                raise DomainError("STALE_VERSION", "留材蒙版与授权不符", 409)
            finish_proof["structure_asset_id"] = finish_binding["asset_id"]
            finish_proof["kind"] = "DIRECT_COLOR_RASTER_MASK"
        file_key, image_hash = self.write_artifact(workspace_id, image_bytes)
        
        with self.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, _ = self._live_state(db, step)
        if step.lease_token != token:
            self.discard_unpublished()
            return None
        
        existing = db.scalar(select(Asset).where(Asset.step_id == step_id))
        if existing:
            step.status = "DONE"
            self.discard_unpublished()
            None(None, None)
            return None
        source = None
        
        index = payload.get("index", 0)
        if not snapshot.get("r2_operation"):
            snapshot.get("r2_operation")
        current_intent = {}.get("intent_state"); inherited_intent = None
        
        if not snapshot.get("direct_operation"):
            snapshot.get("direct_operation")
        
        match inherited_intent:
            case "mock" as info if source.module != "UPLOAD" and source.module == "UPLOAD" and source.module != "UPLOAD":
                return None
            case _:
                self.pending_files = []; return None
    
    def _plan(self, step_id, token):
        if revision_plan.get("scene_scale_advice"):
            source_brief["scene_scale_advice"] = revision_plan["scene_scale_advice"]
        plan = {"briefs": [source_brief], "revision_plan": revision_plan, "scene_style_options": style_options}; planning_call = self.optional_call; planning_content = None
        
        plan = planning_call(step_id, "planner", None if local_brief else context, planning_content) or local_plan(context)
        
        if not snapshot.get("suggestion") and context["input"].get("source_asset_id") and manual_revision:
            with self.sessions() as db:
                source = owned(db, Asset, context["input"]["source_asset_id"], workspace_id)
                if not source.info.get("brief"):
                    source.info.get("brief")
                source_brief = copy.deepcopy({})
            if source_brief:
                source_brief["planning_method"] = "PRESERVED_SOURCE_BRIEF"
                plan = {"briefs": [source_brief]}
        if not core:
            pass
        from .brain import bind_plan
        for ##ERROR## in plan["briefs"]:
            brief["design_intent"] = design_intent
            brief["reference_roles"] = plan["task_plan"].get("reference_roles", {})
            brief["identity_profile"] = copy.deepcopy(analysis["identity_profile"])
        for ##ERROR## in plan["briefs"]:
            pass
        
        brief["surface_finish_stage"] = "STRUCTURE"; brief["output_kind"] = "PRODUCT_DESIGN"; brief["structure_constraints"] = list(dict.fromkeys(["LASER_CUT_AWARE_DESIGN"]))
        
        from .human_review import enabled as human_review_enabled
        from .photo_product import retained_material_gate
        from .visual_evidence import evidence_context, bind_revision
        from .design_graph import qa_images
        
        with () as engineering_content:
            engineering_manifest = qa_images(self, db, step_id, engineering_source, snapshot, {})
            if engineering_observation.get("structure_reference"):
                engineering_content = [engineering_bytes]
                engineering_manifest = {"mode": "ENGINEERING_SOURCE", "images": [{"position": 1, "purpose": "LOCKED_STRUCTURE_SOURCE", "layer": "REFERENCE", "asset_id": engineering_observation["structure_reference"]["structure_asset_id"], "sha256": hashlib.sha256(engineering_bytes).hexdigest()}]}
        engineering_pixels(None, None, None)
        plan["briefs"][0]["revision_plan"] = engineering_revision
        
        raise DomainError("INVALID_PLAN", "规划候选数量不符，请重试当前规划")
        
        raise DomainError("INVALID_PLAN", "规划重复，已停止重复出图")
        
        from .locked_scene import checked_product
        with None:
            pass
        
        while 1:
            for db.scalar(select(Step).where(Step.job_id == job_id, Step.kind == "GENERATE", Step.ordinal == i)) in enumerate(plan["briefs"]):
                existing_generation.payload = {"brief": brief, "index": i, "source_analysis": analysis}
                existing_generation.status = "WAITING_INPUT"
            from .execution_plan import compile_plan, enqueue_plan
            executable = compile_plan(plan, snapshot)
            step.result = {"execution_plan": executable}
            job.snapshot = {"controller_model_selection": executable["model_selection"], "brain_model_advisory": executable.get("brain_model_advisory", {}), "reference_roles": plan["task_plan"].get("reference_roles", {})}
        
        job.snapshot = {"revision_plan": plan["revision_plan"], "revision_plan_step_id": step.id}; job.snapshot = {"scene_style_options": plan["scene_style_options"]}; job.status = "WAITING_INPUT"
        
        binding = snapshot["route"]["finish_source_binding"]
        
        for generation in db.scalars(select(Step).where(Step.job_id == job_id, Step.kind == "GENERATE")):
            generation.payload = {"surface_finish_stage": "FINISH", "structure_asset_id": binding["structure_asset_id"], "structure_sha256": binding["structure_sha256"]}
        
        control_calls = [{"role": attempt.role, "model": attempt.model, "config_id": attempt.input_versions.get("config_id"), "status": attempt.status, "source": {}.get("source", "AUTHORIZED_PRIMARY"), "attempt_id": attempt.id} for attempt in db.scalars(select(ProviderAttempt).where(ProviderAttempt.step_id == step.id, ProviderAttempt.role.in_(["planner", "vision"])).order_by(ProviderAttempt.created_at))]; attempt = None
        
        r = executable["model_selection"] + []; step.result = {"task_plan": control_calls, "model_selection": [{"id": r["id"], "sources": r.get("sources", [])} for r in snapshot["rules"]], "applied_rules": "LOCAL_TEMPLATE", "brain_status": "AI_RESPONSE_VALIDATED"}
        
        step.status = "DONE"
        
        source_id = context["input"].get("source_asset_id"); source = None
        
        job.snapshot = step.id
        
        for child in db.scalars(select(Step).where(Step.job_id == job.id, Step.status == "QUEUED")):
            child.status,
                child.error_code = ("WAITING_INPUT", "ENGINEERING_REVIEW_REQUIRED")
        job.status = "WAITING_INPUT"; r = owned(db, Asset, context["input"]["source_asset_id"], workspace_id); k = retained_material_gate(engineering_bytes)
        
        key = style_options.append({"id": f"style-{index + 1}", "plan": bind_revision(option["plan"], revision_source, evidence)}); key = {}; k = owned(db, Asset, direction_recovery["source_asset_id"], workspace_id)
        
        k = None; v = None; k = None
        
        b = None; attempt = None; r = None
    
    def _engineering_alternative(self, step_id, token, snapshot, job_id, workspace_id):
        from .visual_evidence import evidence_context, bind_revision
        from .design_graph import qa_images; choice = snapshot["engineering_review_choice"]
        with self.sessions() as db:
            source = owned(db, Asset, choice["source_asset_id"], workspace_id)
            content, manifest = qa_images(self, db, step_id, source, snapshot, {})
        evidence = evidence_context(source, snapshot, manifest, content); plan = bind_revision(self.call(step_id, "planner", {"brain_operation": "plan_revision", "input": snapshot["input"], "image_manifest": manifest, "source_analysis": choice.get("analysis"), "previous_proposal": choice.get("revision_plan"), "proposal_number": choice["alternatives_used"]}, content), source, evidence)
        
        with self.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, _ = self._live_state(db, step)
        if step.lease_token != token:
            return None
        elif source.sha256 != choice["source_hash"]:
            raise DomainError("STALE_VERSION", "原始工程稿已变化")
        
        for child in db.scalars(select(Step).where(Step.job_id == job_id, Step.error_code == "ENGINEERING_REVIEW_REQUIRED")):
            if not child.payload.get("brief"):
                child.payload.get("brief")
            child.payload = {"brief": {"revision_plan": plan}}
        
        job.snapshot = {"engineering_review_choice": {"plan_step_id": step_id, "revision_plan": plan, "optimization_required": True, "alternative_pending": False}}; step.result = {"revision_plan": plan}
        
        step.status,
            job.status = ("DONE", "WAITING_INPUT")
        
        None(None, None)
        elif not True:
            pass
    
    def _generate_surface_finish(self, step_id, token):
        from .surface_finish import prepare_structure, compose_finish, resolve_structure_source; snapshot, payload, job_id, workspace_id = self._context(step_id)
        from .human_review import enabled as human_review_enabled; human_review = human_review_enabled(snapshot["route"]); publish_kind = "AUTO_QA"
        if not snapshot["input"].get("module") == "DESIGN" and snapshot["input"].get("surface_finish") == "NATURAL_PATINA" and snapshot["route"].get("surface_finish_workflow") == "DESIGN_STRUCTURE_FINISH_V1":
            raise DomainError("AUTHORIZATION_MISMATCH", "当前授权不含配色阶段", 409)
        
        with self.sessions() as db:
            self._live_state(db, db.get(Step, step_id))
            structure = owned(db, Asset, payload["structure_asset_id"], workspace_id)
            binding = snapshot["route"].get("finish_source_binding")
            selected = structure
            resolved = resolve_structure_source(db, workspace_id, selected)
            if resolved.id != structure.id or resolved.sha256 != payload["structure_sha256"]:
                raise DomainError("AUTHORIZATION_MISMATCH", "配色结构依据与授权不一致", 409)
            elif not binding:
                if not structure.job_id != job_id:
                    if not structure.info.get("brief"):
                        structure.info.get("brief")
                    if {}.get("surface_finish_stage") != "STRUCTURE":
                        raise DomainError("AUTHORIZATION_MISMATCH", "当前配色只能使用本任务通过的结构稿", 409)
            original = self.storage.read(workspace_id, structure.file_key)
        prepared = prepare_structure(original); brief = {"surface_finish_stage": "FINISH", "output_kind": "PRODUCT_DESIGN"}
        if not str(brief.get("surface_finish_prompt") or "").strip():
            raise DomainError("INVALID_PLAN", "缺少总控配色方案，未调用图片接口", 409)
        
        context = {"input": snapshot["input"], "brief": brief, "index": payload.get("index", 0), "rules": snapshot.get("rules", []), "style": snapshot.get("style"), "surface_finish_stage": "FINISH", "capability": "image_edit", "surface_finish_source": {"asset_id": structure.id, "sha256": structure.sha256, "mask_sha256": prepared["mask_sha256"]}}
        
        provider_bytes = self.call(step_id, "image", context, prepared["structure_png"])
        
        with self.sessions() as db:
            live_step = db.get(Step, step_id)
            self._live_state(db, live_step)
        if live_step.lease_token != token:
            return None
        None(None, None); final_bytes, proof = compose_finish(original, provider_bytes)
        
        final_key, final_hash = self.write_artifact(workspace_id, final_bytes)
        
        with self.sessions() as db:
            attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.step_id == step_id, ProviderAttempt.role == "image", ProviderAttempt.status == "DONE"))
            provider_key = None
        if not provider_key:
            provider_key, _ = self.write_artifact(workspace_id, provider_bytes)
        with self.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, _ = self._live_state(db, step)
        
        if step.lease_token != token:
            self.discard_unpublished()
            return None
        
        elif db.scalar(select(Asset.id).where(Asset.step_id == step_id)):
            step.status = "DONE"
            self.discard_unpublished()
            None(None, None)
            return None
        checked_source = resolve_structure_source(db, workspace_id, owned(db, Asset, selected.id, workspace_id))
        
        if checked_source.id != structure.id or checked_source.sha256 != payload["structure_sha256"]:
            raise DomainError("STALE_VERSION", "配色期间结构稿发生变化，未采用候选", 409)
        finish = {"kind": "NATURAL_PATINA", "structure_asset_id": structure.id}
        
        asset = Asset(workspace_id=workspace_id, job_id=job_id, step_id=step_id, parent_id=selected.id, module="DESIGN", category=job.category, state="AUTO_QA", version=(selected.version) + 1, file_key=final_key, sha256=final_hash, info={"mock": False, "bitmap_visual": True, "artifact_role": "SURFACE_FINISH", "brief": brief, "surface_finish": finish, "raw_key": final_key, "raw_hash": final_hash, "provider_image_key": provider_key, "provider_image_hash": proof["provider_sha256"], "native_pixels": proof["source_size"], "candidate_index": payload.get("index", 0), "check": {"format_status": "PASS", "geometry_status": "NOT_APPLICABLE", "process_status": "UNVERIFIED"}, "qa_pending": True, "artifact_status": "GENERATED", "quality_gate_status": "QA_PENDING",
    
    "download_status": "LOCAL_SAVED", "processing_mode": snapshot.get("mode", "LIVE")}); db.add(asset); db.flush()
        
        queued = None
        if not queued:
            db.add(Step(workspace_id=workspace_id, job_id=job_id, ordinal=12_000 + payload.get("index", 0), kind=publish_kind, payload={"asset_id": asset.id, "brief": brief, "index": payload.get("index", 0), "surface_finish_stage": "FINISH", "structure_asset_id": structure.id, "structure_sha256": structure.sha256, "review_only": True}))
        
        step.result = {"asset_id": asset.id, "finished_at": time.time(), "output": {"type": "IMAGE", "asset_id": asset.id, "sha256": final_hash}}; step.status = "DONE"
        
        step.error_code = None
        
        job.status = "AUTO_QA"; None(None, None); self.pending_files = []
        elif not True:
            pass
        elif not True:
            pass
        elif not True:
            pass
        self.pending_files = []
    
    def _generate(self, step_id, token):
        try:
            step.status = "DONE"
            return None
            info["duplicate_space"] = any((a.info.get("diagnosis", {}).get("observed_structure") == info.get("diagnosis", {}).get("observed_structure") if info.get("diagnosis", {}).get("observed_structure") else a.info.get("spatial_signature") == info["spatial_signature"] for a in previous))
            info["score"] = rank_score(brief, snapshot["rules"], {"duplicate_space": info["duplicate_space"]})
            from .creative_policy import silhouette_distance
            info["novelty_check"] = {"method": "normalized_alpha_32x32", "similar_asset_ids": matches, "scope": "本人近期最多40张同类作品；仅轮廓近似提示，不证明全球原创或人工质量"}
            info["score"] -= 1
            info["contract_test"] = any(({}.get("contract_test") for a in db.scalars(select(ProviderAttempt).where(ProviderAttempt.step_id == step_id))))
            raise DomainError("OUTPUT_RECOVERY_STALE", "重做所绑定的原图版本已变化")
            info["revision_status"] = "CANDIDATE_REVISION"
            from .services import digest as stable_digest
            asset.master_id = generated_master.id
            info = {"lineage": {"product_id": product.id, "product_version_id": generated_master.id, "master_hash": generated_master.master_hash}}
            asset.info = info
            asset.info = {"candidate_index": payload.get("index", 0), "auto_repaired": bool(payload.get("repair_of")), "repair_source_id": payload.get("repair_of")}
            zip_key, _ = self.write_artifact(workspace_id, make_zip(files), "zip")
            kinds = ["simpler", "dynamic", "richer", "bridge", "engrave"]
            kinds = ["simpler", "richer", "room"]
            for priority, kind in enumerate(suggestion_order(kinds, applied_rules)[:4]):
                title, impact = SUGGESTIONS[kind]
            step.result = asset.id
            step.status = "DONE"
            step.error_code = None
            active_job.status = "READY_FOR_SELECTION"
            self.pending_files = []
            v = "AUTO_QA"
            k = db.scalar(select(Step).where(Step.job_id == job_id, Step.kind == publish_kind, Step.ordinal == 100 + payload.get("index", 0)))
            r = source.id
            r = Asset
        except:
            pass
        vectorization_error = {"code": exc.code, "message": exc.message, "safe_actions": exc.actions}
        
        y = info.update({"geometry": geometry, "check": validate_geometry(geometry, request.get("width_mm"), snapshot["company_policy"].get("engineering"))}); x = db.scalar(select(ProviderAttempt).where(ProviderAttempt.step_id == step_id, ProviderAttempt.role == "image", ProviderAttempt.status == "DONE")); y = (); x = bounds(old); line = source.info["geometry"]; exc = self.sessions()
        
        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        info["local_preparation_error"] = {"code": exc.code, "message": exc.message}; info["geometry_preparation_status"] = "NOT_CREATED"
        
        item = None; a = None
        
        self.pending_files = []
    
    def tick(self):
        self.recover(); consumed = self.consume_event(); self.schedule_tick(); claim = self.claim()
        if not claim:
            from .cancelled_receipts import recover_cancelled
            return bool(recover_cancelled(self, limit=1)) or consumed
        step_id, token = claim
        try:
            with self.sessions() as db:
                kind = db.get(Step, step_id).kind
            if kind in ("CRAFT_PLAN", "CRAFT_IMAGE", "CRAFT_CUT", "CRAFT_EXPORT", "CRAFT_IDENTITY"):
                from .photo_crafts import execute
                execute(self, step_id, token)
                return True
            elif kind == "DISCUSS":
                from .design_conversations import execute
                execute(self, step_id, token)
                return True
            elif kind == "CANDIDATE_CHECK":
                from .candidate_quality import execute
                execute(self, step_id, token)
                return True
            elif kind == "PLAN":
                self._plan(step_id, token)
                return True
            elif kind == "AUTO_QA":
                from .design_graph import evaluate_candidate
                evaluate_candidate(self, step_id, token)
                return True
            elif kind == "PUBLISH_IMAGE":
                from .human_review import publish_candidate
                publish_candidate(self, step_id, token)
                return True
            elif kind == "PROBE":
                from .live_api import run_probe_step
                run_probe_step(self, step_id, token)
                return True
            elif kind == "PHOTO_IDENTITY":
                from .photo_stages import generate_identity
                generate_identity(self, step_id, token)
                return True
            elif kind == "CARRIER_SUPPORT":
                from .carrier_support_jobs import execute
                execute(self, step_id, token)
                return True
            elif kind == "COLOR_VECTOR":
                from .color_vector_jobs import execute
                execute(self, step_id, token)
                return True
            self._generate(step_id, token)
        except Exception:
            self.discard_unpublished()
        
        if code in ("CONTROLLER_ROUTE_RETRY", "KNOWN_REMOTE_RETRY"):
            step.status = "QUEUED"
            step.error_code = None
            step.available_at = time.time() + 0
            step.lease_token = None
            db.get(Job, step.job_id).status = "QUEUED"
            logger.info("authorized provider recovery queued for current step: %s", code)
            None(None, None)
        
        return True
        from .design_graph import repair_failed; repair_failed(db, step, code); step.error_code = code; step.status = "FAILED"
        
        if code == "RATE_LIMIT" and step.kind != "PROBE" and step.attempts < 3 and db.get(Job, step.job_id).snapshot["route"]["provider"] != "configured":
            step.status = "QUEUED"
            step.available_at = time.time() + min(60, 2**(step.attempts))
        
        db.get(Job, step.job_id).status = step.status
        if code == "LOCAL_RATE_LIMIT":
            step.status = "QUEUED"
            step.available_at = time.time() + 5
            step.attempts = max(0, (step.attempts) - 1)
            db.get(Job, step.job_id).status = "QUEUED"
        failed_job = db.get(Job, step.job_id)
        if failed_job.snapshot.get("route", {}).get("engineering_local_trace") == "LOCAL_VECTOR_V2":
            from .engineering_cleanup import local_finished
            local_finished(db, failed_job)
        
        if step.kind == "PHOTO_IDENTITY" and step.status != "QUEUED":
            product_step = owned(db, Step, step.payload["product_step_id"], step.workspace_id)
            product_step.status = "WAITING_INPUT"
            product_step.error_code = "PHOTO_IDENTITY_DEPENDENCY_FAILED"
        
        if step.kind == "AUTO_QA" and code != "LOCAL_RATE_LIMIT" and step.payload.get("asset_id"):
            unchecked = owned(db, Asset, step.payload["asset_id"], step.workspace_id)
            unchecked.state = "NEEDS_HUMAN_DECISION"
            unchecked.info = {"qa_pending": False, "qa_error": code, "artifact_status": "GENERATED", "quality_gate_status": "QA_ERROR", "automatic_quality_rejected": False, "quality_failure_reason": "自动检查暂未完成，图片已保存，可以查看、下载或请AI重新给出意见。"}
            if not unchecked.info.get("revision_plan"):
                unchecked.info.get("revision_plan")
                if not unchecked.info.get("brief"):
                    unchecked.info.get("brief")
            revision = {}.get("revision_plan")
            if not revision:
                revision
            before_id = {}.get("evidence_binding", {}).get("asset_id")
            if before_id:
                from .revision_gate import record_decision
                record_decision(db, failed_job, unchecked, owned(db, Asset, before_id, step.workspace_id), unchecked.info.get("revision_acceptance_gate"), error=code)
        from .output_recovery import isolate_output_recovery_failure; isolated_recovery_failure = isolate_output_recovery_failure(db, step, code)
        if isolated_recovery_failure and failed_job.snapshot["route"]["provider"] in ("free", "configured"):
            if failed_job.snapshot["route"].get("brain_core_version") and failed_job.snapshot["route"].get("execution_version") and code in ("API_CONFIG_REQUIRED", "API_CAPABILITY_REQUIRED", "MODEL_OR_ENDPOINT_NOT_FOUND", "AUTHORIZATION_KEY_CHANGED", "RATE_LIMIT", "PROVIDER_QUOTA", "REDIRECT_NOT_ALLOWED", "FREE_QUOTA_EXHAUSTED", "PROVIDER_PERMISSION", "PAUSED_CREDENTIAL", "PAUSED_BUDGET", "CNY_BUDGET_EXCEEDED", "AUTHORIZATION_LIMIT", "ATTEMPT_LIMIT", "PROVIDER_REJECTED", "PROVIDER_REQUEST_INVALID", "PROVIDER_UNAVAILABLE", "EDIT_UNSUPPORTED", "FREE_TERMS_EXPIRED", "FREE_PLAN_UNKNOWN", "SAFETY_BLOCKED", "OUTCOME_UNKNOWN"):
                authorization_pause = code in ("PAUSED_BUDGET", "CNY_BUDGET_EXCEEDED", "AUTHORIZATION_LIMIT", "ATTEMPT_LIMIT")
                match revision:
                    case _ | _ as saved if Step.status == "QUEUED" and code == "FREE_QUOTA_EXHAUSTED" and Asset.job_id == failed_job.id and code != "LOCAL_RATE_LIMIT" and Step.status == "QUEUED" and step.kind == "PROBE" and code != "OUTCOME_UNKNOWN" and settings().environment == "test":
                        return True
            
        from .budget import release_pool
        
        from .engineering_review import release_failed_alternative
        
        del exc
    
    def drain(self, limit=500):
        for _ in range(limit):
            worked = self.tick()
            with self.sessions() as db:
                event = db.scalar(select(Outbox.id).where(Outbox.processed.is_(False), Outbox.attempts < 3))
            if worked:
                continue
            elif event:
                pass
        raise RuntimeError("Worker drain limit reached")

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"); worker = Worker(); logger.info("Database worker started; provider credentials are never logged")
    try:
        if not worker.tick():
            time.sleep(1)
            while 1:
                return None
                if not ##ERROR##<EXCEPTION MATCH>Exception:
                    break
                exc = None
                logger.error("Worker service error: %s", type(exc).__name__)
                time.sleep(2)
    except:
        pass

if __name__ == "__main__":
    main()
    return None
