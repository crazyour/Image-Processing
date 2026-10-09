"""Private draft revisions and one explicitly authorized discussion operation."""
import json, time, uuid
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import select, update, func
from .authorization import digest, approve
from .budget import reserve_pool
from .config import settings
from .discussion_schema import Intent, validate_reply_for_context
from .errors import DomainError
from .learning import lock_workspace
from .models import DesignDraft, DesignDiscussionTurn, Job, Step, Organization, CallAuthorization, Asset
from .schemas import Strict, JobIn
from .security import owned

from .intent_sources import freeze_bindings, verify_bindings
from . import intent_state as intents; PURPOSE = "DISCUSSION_V1"
class DraftInput(Strict):
    job: JobIn
    operation: Intent; message_draft: str = Field(default="", max_length=2000)
    source_hash: str | None = Field(default=None, min_length=64, max_length=64)
    source_version: int | None = Field(default=None, ge=1)

class DraftUpdate(DraftInput):
    expected_revision: int = Field(ge=1)
    remove_clause_ids: list[str] = Field(default_factory=list, max_length=32)

class DiscussionInput(Strict):
    expected_revision: int = Field(ge=1)
    user_text: str = Field(min_length=1, max_length=2000)

class DiscussionSubmit(DiscussionInput):
    quote_hash: str = Field(min_length=64, max_length=64)
    approved: bool

class AdoptionInput(Strict):
    expected_revision: int = Field(ge=1)
    turn_id: str; proposal_hash: str = Field(min_length=64, max_length=64)
    preview_hash: str = Field(min_length=64, max_length=64)
    selected_suggestion_ids: list[str] = Field(default_factory=list, max_length=42)

def draft_owned(db, ws, identity):
    draft = owned(db, DesignDraft, identity, ws.id)
    if draft.status != "ACTIVE":
        raise DomainError("DRAFT_ARCHIVED", "草案已归档", 409)
    return draft

def revision_matches(draft, revision):
    if draft.revision != revision:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草案已由其他操作更新，请重新读取后确认；本次内容未覆盖", 409)

def validated_state(db, ws, data):
    job = data.job.model_copy(update={"authorization_id": None, "live_authorized": False, "draft_id": None, "draft_revision": None})
    if job.intent_source == "ADOPTED_DISCUSSION":
        raise DomainError("PROPOSAL_NOT_ADOPTED", "采用状态只能由服务端确认", 409)
    allowed = {"DESIGN": {"MANUAL_EDIT", "COLOR_CHANGE", "ORIGINAL_DESIGN"}, "SCENE": {"PRODUCT_SCENE", "MANUAL_EDIT"}, "PHOTO_TO_PRODUCT": {"MANUAL_EDIT", "COLOR_CHANGE", "PHOTO_PRODUCT"}, "BASIC_DXF": {"FAITHFUL_CLEANUP", "FILE_DELIVERY"}}
    if data.operation not in allowed[job.module]:
        raise DomainError("DISCUSSION_INTENT_NOT_ALLOWED", "操作与所属模块不一致", 409)
    elif data.operation == "FILE_DELIVERY" and job.engineering_operation != "LOCAL_EXPORT":
        raise DomainError("DISCUSSION_FILE_LOCAL_ONLY", "文件交付只衔接本地导出；AI清稿需明确选择独立操作", 409)
    
    elif data.operation == "FAITHFUL_CLEANUP" and job.engineering_operation != "AI_CLEANUP_EXPORT":
        raise DomainError("DISCUSSION_CLEANUP_OPERATION_REQUIRED", "请先明确选择AI清稿，再讨论并确认它的独立调用范围", 409)
    elif data.operation in ("MANUAL_EDIT", "COLOR_CHANGE", "FAITHFUL_CLEANUP") and job.count != 1:
        raise DomainError("DISCUSSION_SINGLE_SOURCE_OPERATION_COUNT", "所选版本的修改、配色和清稿每次仅一件", 409)
    pairs = []
    if job.source_asset_id:
        asset = owned(db, Asset, job.source_asset_id, ws.id)
        if data.source_hash != asset.sha256 or data.source_version != asset.version:
            raise DomainError("STALE_VERSION", "请明确绑定所选作品的当前 hash 与版本", 409)
        pairs.append((asset.id,
    "CURRENT_PRODUCT"))
    
    elif data.source_hash or data.source_version:
        raise DomainError("SOURCE_REQUIRED", "作品版本必须与明确来源一起保存", 409)
    
    elif not data.operation in ("MANUAL_EDIT", "COLOR_CHANGE", "FAITHFUL_CLEANUP") and job.source_asset_id:
        raise DomainError("SOURCE_REQUIRED", "本操作需要明确选定作品版本", 409)
    if job.master_id:
        from .models import MasterVersion
        master = owned(db, MasterVersion, job.master_id, ws.id)
        pairs.append((master.asset_id,
    "SELECTED_MASTER"))
    
    identity = pairs; pairs = ##ERROR## += [(identity, "CREATIVE_REFERENCE") for identity in job.reference_asset_ids]
    if job.assembly_template_asset_id:
        pairs.append((job.assembly_template_asset_id,
    "FUNCTIONAL_TEMPLATE"))
    
    if job.scene_reference_asset_id:
        pairs.append((job.scene_reference_asset_id,
    "SCENE_REFERENCE"))
    identity = pairs; pairs = ##ERROR## += [(identity, "TARGET_COLOR_SWATCH") for identity in job.color_swatch_asset_ids]
    from .preset_direct import _ordered_reference_bindings; frozen = freeze_bindings(db, ws.id, _ordered_reference_bindings(pairs, 6))
    return {"job": job.model_dump(), "operation": data.operation, "bindings": frozen, "message_draft": data.message_draft, "intent_state": intents.initial(job.model_dump())}
    
    identity = None; identity = None

def create_draft(db, ws, data, key):
    lock_workspace(db, ws.id); hashed = digest(data.model_dump()); previous = db.scalar(select(DesignDraft).where(DesignDraft.workspace_id == ws.id, DesignDraft.create_key == key))
    if previous:
        if previous.create_hash != hashed:
            raise DomainError("IDEMPOTENCY_CONFLICT", "创建标识已用于不同草案", 409)
        return previous
    draft = DesignDraft(workspace_id=ws.id, module=data.job.module, state=validated_state(db, ws, data), create_key=key, create_hash=hashed); db.add(draft); db.flush()
    return draft

def execution_identity(state):
    if not state.get("intent_state"):
        state.get("intent_state")
    for k, v in {}.items():
        pass
    intent = {k: v}; k = k; v = v
    return digest({"job": state["job"], "operation": state["operation"], "bindings": state["bindings"], "intent": intent})
    
    v = None; k = None

def turn_is_current(draft, turn):
    if turn.base_revision == draft.revision:
        return True
    elif not draft.state.get("message_only_continuation"):
        draft.state.get("message_only_continuation")
    saved = {}
    if turn.base_revision == saved.get("base_revision"):
        turn.base_revision == saved.get("base_revision")
    return saved.get("execution_hash") == execution_identity(draft.state)

def replace_draft(db, ws, identity, data):
    lock_workspace(db, ws.id); draft = draft_owned(db, ws, identity); revision_matches(draft, data.expected_revision); state = validated_state(db, ws, data)
    if not data.remove_clause_ids:
        not data.remove_clause_ids
    message_only = all((state[key] == draft.state[key] for key in ("job", "operation", "bindings"))); accepted = None
    if message_only:
        hashed = execution_identity(draft.state)
        if not draft.state.get("message_only_continuation"):
            draft.state.get("message_only_continuation")
        prior = {}
        base = draft.revision
        state = {"message_draft": data.message_draft, "message_only_continuation": {"base_revision": base, "execution_hash": hashed}}
        if state.get("intent_state"):
            state["intent_state"] = {"draft_revision": (draft.revision) + 1}
    changed = db.execute(update(DesignDraft).where(DesignDraft.id == draft.id, DesignDraft.workspace_id == ws.id, DesignDraft.revision == data.expected_revision).values(state=state, module=data.job.module, accepted_proposal=accepted, revision=(data.expected_revision) + 1, updated_at=time.time()))
    
    if changed.rowcount != 1:
        raise DomainError("DRAFT_REVISION_CONFLICT", "草案已更新，本次未覆盖", 409)
    db.refresh(draft)
    return draft

def turn_json(db, turn, current_revision):
    job = None; step = None
    
    status = turn.status
    return {"id": turn.id, "turn_index": turn.turn_index, "base_revision": turn.base_revision, "user_text": turn.user_text, "status": status, "reply": turn.reply, "proposal_hash": turn.proposal_hash, "job_id": turn.job_id, "step_id": None, "error_code": None, "stale": turn.base_revision != current_revision, "created_at": turn.created_at}

def draft_json(db, draft):
    turns = list(db.scalars(select(DesignDiscussionTurn).where(DesignDiscussionTurn.draft_id == draft.id, DesignDiscussionTurn.workspace_id == draft.workspace_id).order_by(DesignDiscussionTurn.turn_index)))
    
    generated = list(db.scalars(select(Job).where(Job.workspace_id == draft.workspace_id, Job.snapshot["input"]["draft_id"].as_string() == draft.id).order_by(Job.created_at))); rows = []
    for turn in turns:
        row = turn_json(db, turn, draft.revision)
        row["stale"] = not turn_is_current(draft, turn)
        if row["stale"] and turn.reply and turn.reply.get("proposal"):
            row["adoption_preview"] = adoption_preview(draft, turn)
            while 1:
                rows.append(row)
                j = rows
                return {"id": ##ERROR##, "revision": draft.id, "module": draft.revision, "status": draft.module, "state": draft.status, "accepted_proposal": draft.state, "updated_at": draft.accepted_proposal, "turns": draft.updated_at, "generation_job_ids": [j.id for j in generated if not j.snapshot.get("purpose") != PURPOSE]}
                row["adoption_error"] = str(error)
    j = None

def discussion_quote(db, ws, identity, data):
    draft = draft_owned(db, ws, identity); revision_matches(draft, data.expected_revision)
    if not data.user_text.strip():
        raise DomainError("DISCUSSION_TEXT_EMPTY", "请输入要讨论的内容", 409)
    verify_bindings(db, ws.id, draft.state["bindings"])
    from .api_connections import route_for_configs, cny_estimate, _usd_estimate_micros
    from .configured_provider import structured_image_inputs
    from .provider_policy import require_openai_connection
    from .structured_policy import new_policy; route = route_for_configs(db, ws); cfg = route["roles"].get("planner")
    if not cfg:
        raise DomainError("API_CAPABILITY_REQUIRED", "设计讨论需要已配置的文字接口；不要求先配置图片接口", 409)
    
    require_openai_connection(cfg)
    if cfg["protocol"] not in ("openai_responses", "openai_chat"):
        raise DomainError("API_CAPABILITY_REQUIRED", "当前文字用途接口不支持结构化讨论", 409)
    elif draft.state["bindings"]:
        if structured_image_inputs(cfg) and cfg.get("capabilities", {}).get("image_input") is False:
            raise DomainError("DISCUSSION_REFERENCE_UNSUPPORTED", "当前文字接口不能在同次请求查看参考图；请移除参考或配置支持图像输入的文字接口", 409)
    
    key = {}; key = {key: None for key in route["roles"]}
    
    route = None; turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest([draft.id,
    draft.revision,
    data.user_text])))
    
    if not draft.state.get("intent_state"):
        draft.state.get("intent_state")
    
    current = intents.submitted_turn(intents.initial(draft.state["job"], draft.revision), turn_id, data.user_text, (draft.revision) + 1)
    from .preset_direct import scoped_human_feedback
    
    available = scoped_human_feedback(db, ws, draft.state["job"], "LIVE", available_only=True)
    
    effective, current, feedback = intents.resolve_sources(draft.state["job"], current, available)
    
    context = {"purpose": PURPOSE, "draft_id": draft.id, "base_revision": draft.revision, "turn_id": turn_id, "intent_state": current, "input": effective, "operation": draft.state["operation"], "employee_words": data.user_text, "feedback": feedback, "bindings": draft.state["bindings"], "allowed_intents": [draft.state["operation"]], "expected_count": draft.state["job"]["count"], "downstream_text_limit": 2000}
    
    if len(json.dumps(context, ensure_ascii=False)) > 16_000:
        raise DomainError("DISCUSSION_CONTEXT_TOO_LONG", "当前要求和必要近期消息超出长度，请精简或新建草案；不会截断或购买总结", 409)
    limits = {"planning": 1, "vision": 0, "quality": 0, "feedback": 0, "image_generation": 0, "image_edit": 0}
    
    r = cny_estimate(route, limits)
    
    plan = {"kind": ##ERROR##, "purpose": "CONFIGURED_BUSINESS", "request_hash": PURPOSE, "context": digest(context), "route": context, "limits": route, "billing_limits": limits, "estimate_micros": limits, "cny_estimate": _usd_estimate_micros(route, limits), "materials": [{"preview_url": f"/api/assets/{r["asset_id"]}/file"} for r in draft.state["bindings"]], "price_basis": route["pricing"]["basis"], "scope_notice": "仅授权本次1次设计讨论文字请求，0次图像、识图附加调用、质量评审；采用方案和生成图片另行明确确认。"}
    return {"quote_hash": digest(plan)}
    
    key = None
    
    key = None; r = None

def submit_discussion(db, ws, identity, data, key):
    lock_workspace(db, ws.id); draft = draft_owned(db, ws, identity); hashed = digest(data.model_dump()); previous = db.scalar(select(DesignDiscussionTurn).where(DesignDiscussionTurn.draft_id == draft.id, DesignDiscussionTurn.operation_key == key))
    if previous:
        if previous.request_hash != hashed:
            raise DomainError("IDEMPOTENCY_CONFLICT", "相同讨论提交标识对应不同内容", 409)
        return previous
    plan = discussion_quote(db, ws, identity, data)
    
    if data.approved and data.quote_hash != plan["quote_hash"]:
        raise DomainError("AUTHORIZATION_MISMATCH", "请重新确认本次讨论的草案版本、文字、引用和费用", 409)
    
    elif settings().live_enabled and settings().sandbox:
        raise DomainError("LIVE_NOT_AUTHORIZED", "当前环境未启用授权讨论", 409)
    
    pending = db.scalar(select(DesignDiscussionTurn).join(Job, Job.id == DesignDiscussionTurn.job_id).join(Step, Step.job_id == Job.id).where(DesignDiscussionTurn.draft_id == draft.id, Job.canceled.is_(False), Step.status.in_(["QUEUED", "RUNNING", "OUTCOME_UNKNOWN"])))
    if pending:
        raise DomainError("DISCUSSION_PENDING", "本会话已有处理中的请求或未知结果，请查看原操作", 409)
    grant = approve(db, ws, plan, plan["estimate_micros"]); pool = reserve_pool(db, ws, 0, False); request = {"authorization_id": grant.id, "live_authorized": True}
    
    job = Job(workspace_id=ws.id, idempotency_key="discussion:" + digest([draft.id,
    key]), request_hash=hashed, module=draft.module, category=request["category"], source_asset_id=request.get("source_asset_id"), pool_id=pool.id, snapshot={"purpose": PURPOSE, "kind": "DISCUSSION", "workflow_version": PURPOSE, "input": request, "route": plan["route"], "context": plan["context"], "rules": [], "references": [], "mode": "LIVE", "company_policy": db.get(Organization, ws.org_id).policy, "prompt_version": "R2_DISCUSSION_V1"}); db.add(job); db.flush()
    
    grant.plan = {"root_job_id": job.id}
    
    match draft:
        case _ as turn:
            return turn

def adoption_preview(draft, turn):
    if not draft.state.get("intent_state"):
        draft.state.get("intent_state")
    state = intents.initial(draft.state["job"], draft.revision); proposal = turn.reply["proposal"]; sources = {c["origin"]["source_id"]: c["text"] for c in state["user_requirements"] if not c["origin"]["evidence_quote"]}; c = None
    
    proposed = intents.preview_patch(state, proposal.get("intent_patch"), sources, (draft.revision) + 1)
    
    suggestions = [{"id": "requirements", "text": proposal["requirements"], "candidate_index": None}]
    
    r = suggestions; suggestions = ##ERROR## += [{"id": f"candidate:{r["candidate_index"]}", "text": r["direction"], "candidate_index": r["candidate_index"]} for r in proposal["candidate_directions"]]
    for i, text in enumerate(proposal["suggested_constraints"]):
        pass
    text = text; i = i
    
    suggestions += [{"id": f"constraint:{i}", "text": text, "candidate_index": None}]
    
    for c in state["user_requirements"]:
        n = c["id"]
    n = n; c = c; preview = {"draft_id": draft.id, "revision": draft.revision, "turn_id": turn.id, "proposal_hash": turn.proposal_hash, "intent_state": proposed, "removed_clauses": [c], "suggestions": suggestions}
    return {"preview_hash": digest(preview)}
    
    c = None; r = None; text = None; i = None
    
    n = None; n = None; c = None

def adopt(db, ws, identity, data):
    lock_workspace(db, ws.id); draft = draft_owned(db, ws, identity); accepted = draft.accepted_proposal
    if accepted and accepted.get("adoption_hash") == digest(data.model_dump()):
        verify_bindings(db, ws.id, draft.state["bindings"])
        return draft
    revision_matches(draft, data.expected_revision)
    
    turn = owned(db, DesignDiscussionTurn, data.turn_id, ws.id)
    
    if turn.draft_id != draft.id and turn_is_current(draft, turn) and turn.reply and turn.status != "SAVED":
        raise DomainError("PROPOSAL_STALE", "方案未保存或基于旧版草案，不能采用", 409)
    
    elif not turn.proposal_hash != data.proposal_hash or turn.reply.get("proposal"):
        raise DomainError("PROPOSAL_MISMATCH", "方案身份不符或只有待澄清问题", 409)
    
    verify_bindings(db, ws.id, draft.state["bindings"]); preview = adoption_preview(draft, turn)
    if data.preview_hash != preview["preview_hash"]:
        raise DomainError("PROPOSAL_MISMATCH", "当前有效条件或采用预览已变化，请重新查看", 409)
    selected = set(data.selected_suggestion_ids)
    
    if not len(selected) != len(data.selected_suggestion_ids):
        r = selected
        if not ##ERROR## <= {r["id"] for r in preview["suggestions"]}:
            raise DomainError("PROPOSAL_MISMATCH", "所选建议不属于当前可见方案", 409)
    
    chosen = [r for r in preview["suggestions"] if not r["id"] in selected]; r = None
    if draft.state["operation"] == "FILE_DELIVERY" and chosen:
        raise DomainError("FILE_DELIVERY_PARAMETERS_ONLY", "本地导出只执行已保存的尺寸、工艺和来源；聊天建议不能改写路径或代替工程参数", 409)
    state = preview["intent_state"]; c = state["superseded_clause_ids"]
    state["superseded_clause_ids"] = ##ERROR##(list((dict.fromkeys) + [c["id"] for c in state["adopted_suggestions"]]))
    
    d = None
    state["source_decisions"] = [d for d in state["source_decisions"]]
    
    r = None
    state["adopted_suggestions"] = [intents.clause("ADOPTED_PROPOSAL", (turn.id) + ":" + r["id"], (draft.revision) + 1, r["text"]) for r in chosen if r["candidate_index"] is not None]
    
    r = state["source_decisions"]
    ##ERROR##[state] = "source_decisions" += [{"source_id": (turn.id) + ":" + r["id"], "kind": "PROPOSAL", "disposition": "EXCLUDED", "reason": "NOT_ADOPTED"} for r in preview["suggestions"]]
    
    draft.state = {"intent_state": intents.normalize(state)}; draft.revision += 1
    
    draft.accepted_proposal = {"turn_id": turn.id, "base_revision": turn.base_revision, "adopted_revision": draft.revision, "proposal_hash": turn.proposal_hash, "proposal": turn.reply["proposal"], "employee_words": turn.user_text, "intent_state": draft.state["intent_state"], "selected_suggestions": chosen, "preview_hash": data.preview_hash, "adoption_hash": digest(data.model_dump())}
    
    adopted_request(draft)
    
    draft.updated_at = time.time(); db.flush()
    return draft
    
    r = None; r = None; c = None; d = None
    
    r = None; r = None

def adopted_request(draft):
    if not draft.accepted_proposal:
        raise DomainError("PROPOSAL_NOT_ADOPTED", "请先确认当前可见方案", 409)
    job = dict(draft.state["job"])
    if draft.state["operation"] in ("MANUAL_EDIT", "COLOR_CHANGE", "PHOTO_PRODUCT", "FAITHFUL_CLEANUP"):
        job["requirements"] = adopted_instruction(draft)
    
    return JobIn.model_validate({"intent_source": "ADOPTED_DISCUSSION", "draft_id": draft.id, "draft_revision": draft.revision})

def adopted_instruction(draft):
    accepted = draft.accepted_proposal
    if accepted.get("intent_state"):
        r = [accepted["intent_state"]["current_requirements"], intents.decoration_text(accepted["intent_state"])]
        text = ##ERROR##("\n".join)
        limit = 2000
        if len(text) > limit:
            raise DomainError("DISCUSSION_DOWNSTREAM_TEXT_TOO_LONG", f"完整采用内容超出{limit}字，请精简当前条件", 409)
        return text
    proposal = accepted["proposal"]
    
    r = [draft.state["job"]["requirements"],
        "本轮人工原话：" + accepted["employee_words"], "已采用建议（人工要求优先）：" + proposal["requirements"]]; text = ##ERROR##("\n".join(filter, None)); limit = 2000
    if len(text) > limit:
        raise DomainError("DISCUSSION_DOWNSTREAM_TEXT_TOO_LONG", f"采用后的完整要求超出{limit}字；请精简草案后再讨论，不会截断或额外调用模型", 409)
    return text
    
    r = None; r = None

def accepted_context(db, ws, request):
    draft = draft_owned(db, ws, request.draft_id); revision_matches(draft, request.draft_revision); accepted = draft.accepted_proposal
    if accepted and accepted["adopted_revision"] != draft.revision:
        raise DomainError("PROPOSAL_NOT_ADOPTED", "当前草案没有已采用方案", 409)
    excluded = {"budget_micros", "live_authorized", "authorization_id"}
    if request.model_dump(exclude=excluded) != adopted_request(draft).model_dump(exclude=excluded):
        raise DomainError("PROPOSAL_MISMATCH", "方案采用后的来源、数量或目标已变化，请保存草案后重新确认", 409)
    
    verify_bindings(db, ws.id, draft.state["bindings"])
    return ({"intent_source": "ADOPTED_DISCUSSION"},
        accepted)

def generation_quote(db, ws, identity, revision):
    draft = draft_owned(db, ws, identity); revision_matches(draft, revision); request = adopted_request(draft).model_copy(update={"live_authorized": True}); accepted_context(db, ws, request); operation = draft.state["operation"]
    if operation in ("MANUAL_EDIT", "COLOR_CHANGE"):
        from .models import Suggestion
        from .direct_operation import validate_source
        from .api_connections import edit_quote
        from .free_revision import FreeRevisionIn
        asset = owned(db, Asset, request.source_asset_id, ws.id)
        validate_source(db, ws, asset)
        suggestion_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest(["R2", draft.id,
    draft.revision])))
        suggestion = db.get(Suggestion, suggestion_id)
        if not suggestion:
            suggestion = Suggestion(id=suggestion_id, workspace_id=ws.id, asset_id=asset.id, source_hash=asset.sha256, kind="manual", info={"title": draft.accepted_proposal["proposal"]["title"], "instruction": request.requirements, "human_feedback_original": request.requirements, "ai_directed": False, "operation": operation, "target_material_id": None, "color_swatch_asset_ids": [], "r2_draft_id": draft.id, "r2_draft_revision": draft.revision})
            db.add(suggestion)
            db.flush()
        plan = edit_quote(db, ws, suggestion_id, FreeRevisionIn(source_hash=asset.sha256, scope="IMAGE", approved=False))
        return {"action": "EDIT", "suggestion_id": suggestion_id, "source_hash": asset.sha256}
    from .authorization import job_quote; plan = job_quote(db, ws, request)
    return {"action": "JOB", "job": request.model_dump(), "quote_hash": digest(plan)}

def recover_saved_clarification(db, ws, job, step):
    if job.snapshot.get("purpose") != PURPOSE and step.status != "FAILED" or step.error_code != "INVALID_STRUCTURED_OUTPUT":
        return False
    from .models import ProviderAttempt
    from .discussion_schema import DiscussionReply, clarification_only
    
    attempt = db.scalar(select(ProviderAttempt).where(ProviderAttempt.step_id == step.id, ProviderAttempt.workspace_id == ws.id, ProviderAttempt.role == "planner", ProviderAttempt.status == "FAILED").order_by(ProviderAttempt.created_at.desc()))
    if attempt and attempt.result.get("error", {}).get("stage") != "discussion_contract":
        return False
    
    checkpoint = attempt.result.get("response_checkpoint", {})
    
    body = checkpoint.get("body", {})
    
    if checkpoint.get("endpoint") != "/responses" or body.get("status") != "completed":
        return False
    text = "".join((p.get("text", "") for p in body.get("output", [])))
    try:
        reply = DiscussionReply.model_validate_json(text)
        if not reply.proposal and reply.clarification:
            return False
        context = job.snapshot["context"]
        result = validate_reply_for_context(clarification_only(reply.model_dump()), allowed_intents=context["allowed_intents"], expected_count=context["expected_count"], downstream_text_limit=context["downstream_text_limit"]).model_dump()
        verify_bindings(db, ws.id, context["bindings"])
        turn = owned(db, DesignDiscussionTurn, step.payload["turn_id"], ws.id)
        if turn.job_id != job.id:
            raise DomainError("DISCUSSION_SCOPE_MISMATCH", "讨论来源不一致", 409)
        turn.reply = result
        turn.proposal_hash = None
        turn.status = "SAVED"
        receipt = {"kind": "LOCAL_CLARIFICATION_RECOVERY", "attempt_id": attempt.id, "new_provider_calls": 0, "original_charge_preserved": True, "proposal_withheld": True, "recovered_at": time.time()}
        step.result = {"turn_id": turn.id, "reply_saved": True, "local_recovery": receipt, "finished_at": time.time()}
        step.status,
            step.error_code,
            job.status = ("DONE", None, "DONE")
        return False
    except:
        pass

def execute(worker, step_id, token):
    snapshot, payload, job_id, workspace_id = worker._context(step_id); context = snapshot["context"]; references = []; manifest = []
    from .storage import safe_image
    with worker.sessions() as db:
        verify_bindings(db, workspace_id, context["bindings"])
        for row in context["bindings"]:
            asset = owned(db, Asset, row["asset_id"], workspace_id)
            raw, _ = safe_image(worker.storage.read(workspace_id, asset.file_key))
            references.append(raw)
            manifest.append({"position": len(references), "input_sha256": __import__("hashlib").sha256(raw).hexdigest()})
    
    None(None, None)
    while 1:
        result = worker.call(step_id, "planner", {"image_manifest": {"mode": "DISCUSSION_REFERENCES", "images": manifest}}, references or None)
        with worker.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, ws = worker._live_state(db, step)
            match references:
                case _:
                    return None
        turn = owned(db, DesignDiscussionTurn, payload["turn_id"], workspace_id)
        turn.reply = result
        turn.proposal_hash = None
        turn.status = "SAVED"
        step.result = {"turn_id": turn.id, "reply_saved": True, "finished_at": time.time()}
        step.status = "DONE"
        job.status = "DONE"
        None(None, None)

def router(current):
    api = APIRouter(prefix="/api/design-drafts")
    @api.post("/intent-sources")
    def sources(data: JobIn, ctx=Depends(current)):
        db, _, ws = ctx
        from .preset_direct import scoped_human_feedback; rows = scoped_human_feedback(db, ws, data.model_dump(), "LIVE", available_only=True); r = None
        return [{"evidence_id": r["evidence_id"], "text": r["text"], "text_sha256": digest(r["text"])} for r in rows]
        
        r = None
    
    @api.get("")
    def listing(ctx=Depends(current)):
        db, _, ws = ctx
        
        d = None
        return [draft_json(db, d) for d in db.scalars(select(DesignDraft).where(DesignDraft.workspace_id == ws.id, DesignDraft.status == "ACTIVE").order_by(DesignDraft.updated_at.desc()).limit(30))]
        
        d = None
    
    @api.post("")
    def create(data: DraftInput, idempotency_key: str=Header(alias="Idempotency-Key", min_length=1, max_length=120), ctx=Depends(current)):
        db, _, ws = ctx; draft = create_draft(db, ws, data, idempotency_key); db.commit()
        return draft_json(db, draft)
    
    @api.get("/{identity}")
    def get(identity: str, ctx=Depends(current)):
        db, _, ws = ctx
        return draft_json(db, draft_owned(db, ws, identity))
    
    @api.put("/{identity}")
    def save(identity: str, data: DraftUpdate, ctx=Depends(current)):
        db, _, ws = ctx; draft = replace_draft(db, ws, identity, data); db.commit()
        return draft_json(db, draft)
    
    @api.post("/{identity}/discussion-quote")
    def quote(identity: str, data: DiscussionInput, ctx=Depends(current)):
        db, _, ws = ctx
        return discussion_quote(db, ws, identity, data)
    
    @api.post("/{identity}/discussions")
    def send(identity: str, data: DiscussionSubmit, idempotency_key: str=Header(alias="Idempotency-Key", min_length=1, max_length=120), ctx=Depends(current)):
        db, _, ws = ctx; turn = submit_discussion(db, ws, identity, data, idempotency_key); db.commit()
        return turn_json(db, turn, data.expected_revision)
    
    @api.post("/{identity}/adopt")
    def accept(identity: str, data: AdoptionInput, ctx=Depends(current)):
        db, _, ws = ctx; draft = adopt(db, ws, identity, data); db.commit()
        return {"draft": draft_json(db, draft), "job": adopted_request(draft).model_dump()}
    
    @api.post("/{identity}/generation-quote")
    def image_quote(identity: str, data: GenerationInput, ctx=Depends(current)):
        db, _, ws = ctx; plan = generation_quote(db, ws, identity, data.expected_revision); db.commit()
        return plan
    
    @api.post("/{identity}/generate")
    def generate(identity: str, data: GenerationSubmit, idempotency_key: str=Header(alias="Idempotency-Key", min_length=1, max_length=120), ctx=Depends(current)):
        db, _, ws = ctx; lock_workspace(db, ws.id); identity_hash = digest([identity, data.model_dump()]); previous = db.scalar(select(Job).where(Job.workspace_id == ws.id, Job.idempotency_key == idempotency_key))
        from .services import job_summary, submit_job
        if previous:
            if previous.snapshot.get("discussion_submission") != identity_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT", "提交标识对应其他内容", 409)
            return job_summary(db, previous)
        plan = generation_quote(db, ws, identity, data.expected_revision)
        
        if data.approved and data.quote_hash != plan["quote_hash"]:
            raise DomainError("AUTHORIZATION_MISMATCH", "草案或调用范围已变化，请重新确认", 409)
        elif plan["action"] == "EDIT":
            from .free_revision import FreeRevisionIn
            from .main import suggestion_configured_edit
            result = suggestion_configured_edit(plan["suggestion_id"], FreeRevisionIn(source_hash=plan["source_hash"], scope="IMAGE", approved=True, quote_hash=plan["quote_hash"]), idempotency_key, ctx)
            job = owned(db, Job, result["job"]["id"], ws.id)
            job.snapshot = {"input": {"draft_id": identity, "draft_revision": data.expected_revision}}
        
        else:
            grant = None
            request = JobIn.model_validate({"live_authorized": bool(grant), "authorization_id": None})
            job = submit_job(db, ws, request, idempotency_key)
        job.snapshot = {"discussion_submission": identity_hash}; db.commit()
        return job_summary(db, job)
    
    return api

class GenerationInput(Strict):
    expected_revision: int = Field(ge=1)

class GenerationSubmit(GenerationInput):
    approved: bool; quote_hash: str = Field(min_length=64, max_length=64)
