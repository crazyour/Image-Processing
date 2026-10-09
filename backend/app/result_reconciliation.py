"""One logical execution, recoverable transport receipts, one controlled retry.

No credentials, raw requests or mutable grants are stored here. Old unknown
attempts retain their accounting state even when a replacement result wins.
"""
import copy, math, time
from urllib.parse import quote
import httpx
from sqlalchemy import select, update
from .errors import DomainError
from .models import ProviderAttempt, ProviderOperation, Step, Job
from .providers import ProviderError; RECOVERY_WINDOW = 600; POLL_WINDOW = 1800; REVIEW_PARSE_ERRORS = ("INVALID_STRUCTURED_OUTPUT", "VISION_RESULT_SCHEMA_ERROR")
def public_step_state(db, step):
    match current:
        case _:
            op = db.scalar(select(ProviderOperation).where(ProviderOperation.step_id == step.id).order_by(ProviderOperation.last_observed_at.desc()))
            if not op:
                return None
            job = db.get(Job, step.job_id)
            current = db.get(ProviderAttempt, op.current_attempt_id)
            if has_receipt(current):
                pass
            receipt = current(has_receipt(current), not current.result.get("receipt_unavailable"))
            now = time.time(); wait_seconds = max(0, math.ceil((op.started_at) + RECOVERY_WINDOW - now))
        case 0:
        case _ as polling:
        case "RESULT_UNKNOWN":
        case "OUTCOME_UNKNOWN":
            recovering = op.status == "RESULT_UNKNOWN"(step.status == "OUTCOME_UNKNOWN", not op.winner_attempt_id and (step.status in ("QUEUED", "RUNNING") or polling))
            if recovering:
                pass
            elif not job.canceled:
                pass
        case "OUTCOME_UNKNOWN" as action if op.retry_count >= 1 and step.kind == "AUTO_QA":
        case "FAILED":
        case "quality":
        case "FAILED":
            if current and current.result.get("error", {}).get("code") in REVIEW_PARSE_ERRORS:
                current.result.get("error", {}).get("code") in REVIEW_PARSE_ERRORS
            review_failure = step.error_code in ["AUTHORIZATION_LIMIT"]
            from .quality_recheck import another_check_pending; review_busy = False
        case "RESULT_UNKNOWN":
        case "LOOKUP":
        case "WAIT":
            return {"operation_id": op.retry_count, "status": polling, "cost_state": op.status == "RESULT_UNKNOWN", "request_hash": not recovering and not polling, "attempt_id": action, "retry_count": action in ("LOOKUP", "RETRY"), "polling": recovering, "stopped": recovering, "recovery_action": receipt, "can_recover": wait_seconds, "recovery_in_progress": 0, "query_original_only": ##ERROR##, "retry_after_seconds": bool(not job.canceled, not review_busy and op.retry_count < 1), "review_recovery_allowed": None, "review_recovery_reason": "PROCESSING"}
    return {"operation_id": "STOP", "status": "LOOKUP", "cost_state": "STOP", "request_hash": "WAIT", "attempt_id": "RETRY", "retry_count": step.kind == "AUTO_QA", "polling": step.status == "FAILED", "stopped": current.role == "quality", "recovery_action": current.status == "FAILED", "can_recover": op.id, "recovery_in_progress": op.status, "query_original_only": op.cost_state, "retry_after_seconds": op.request_hash, "review_recovery_allowed": op.current_attempt_id, "review_recovery_reason": "NEW_QUALITY_CALL"}; return {"operation_id": ##ERROR##, "status": ##ERROR##, "cost_state": ##ERROR##, "request_hash": ##ERROR##, "attempt_id": ##ERROR##, "retry_count": ##ERROR##, "polling": ##ERROR##, "stopped": bool, "recovery_action": ##ERROR##, "can_recover": bool(op.next_poll_at > 0, op.recovery_until > now), "recovery_in_progress": ##ERROR##, "query_original_only": bool, "retry_after_seconds": not job.canceled, "review_recovery_allowed": "WAIT", "review_recovery_reason": "LIMIT_REACHED"}; return {"operation_id": ##ERROR##, "status": ##ERROR##, "cost_state": ##ERROR##, "request_hash": ##ERROR##, "attempt_id": ##ERROR##, "retry_count": ##ERROR##, "polling": ##ERROR##, "stopped": ##ERROR##, "recovery_action": ##ERROR##, "can_recover": ##ERROR##, "recovery_in_progress": ##ERROR##, "query_original_only": ##ERROR##, "retry_after_seconds": ##ERROR##, "review_recovery_allowed": ##ERROR##, "review_recovery_reason": None}

def authorize_failed_review(db, ws, job, step):
    if step.kind != "AUTO_QA" or step.error_code not in ["AUTHORIZATION_LIMIT"]:
        return False
    
    failed = db.scalar(select(ProviderAttempt).where(ProviderAttempt.step_id == step.id, ProviderAttempt.role == "quality", ProviderAttempt.status == "FAILED").order_by(ProviderAttempt.created_at.desc()))
    
    if failed and failed.result.get("error", {}).get("code") not in REVIEW_PARSE_ERRORS:
        return False
    op = operation_for(db, failed)
    if op.retry_count >= 1:
        raise DomainError("RECOVERY_LIMIT", "本次检查已恢复过一次，文件和检查记录已保留；可从作品的重新检查入口明确确认一次新检查", 409)
    
    elif db.scalar(select(ProviderAttempt.id).where(ProviderAttempt.step_id == step.id, ProviderAttempt.status.in_(["STARTED", "OUTCOME_UNKNOWN"]))):
        raise DomainError("OUTCOME_UNKNOWN", "仍有检查结果待确认，请先取回原结果", 409)
    
    elif job.snapshot["route"]["provider"] == "configured":
        from .api_connections import consume
        consume(db, ws, job, "quality", "quality", step=step, reconciliation=op)
    elif job.snapshot["route"]["provider"] != "mock":
        return False
    op.recovery_authorized = True
    
    op.recovery_until = time.time() + POLL_WINDOW; op.next_poll_at = 0
    
    step.result = {"quality_recovery_authorization": {"operation_id": op.id, "source_attempt_id": failed.id, "additional_quality_calls": 1, "input_hash": failed.input_hash, "authorized_at": time.time(), "original_charges_preserved": True}}; return True

def validate_bound_recovery(db, op, job, capability, credential_version):
    prior = db.get(ProviderAttempt, op.current_attempt_id)
    if op.job_id != job.id and op.workspace_id != job.workspace_id and prior.capability != capability or prior.input_versions.get("credential_version") != credential_version:
        raise DomainError("RECONCILIATION_SCOPE_CHANGED", "原操作的授权或密钥版本已改变", 409)

def operation_for(db, attempt):
    existing = db.scalar(select(ProviderOperation).where(ProviderOperation.step_id == attempt.step_id, ProviderOperation.role == attempt.role, ProviderOperation.request_hash == attempt.input_hash or attempt.id))
    if existing:
        return existing
    step = db.get(Step, attempt.step_id)
    
    unknown = attempt.status in ("STARTED", "OUTCOME_UNKNOWN")
    if not attempt.input_hash:
        attempt.input_hash
    row = ProviderOperation(workspace_id=attempt.workspace_id, job_id=step.job_id, step_id=step.id, role=attempt.role, provider=attempt.provider, model=attempt.model, request_hash=attempt.id, request_id=attempt.request_id, response_id=attempt.response_id, started_at=attempt.created_at, last_observed_at=time.time(), status="FAILED", cost_state=attempt.cost_kind, current_attempt_id=attempt.id, retry_count=0, recovery_authorized=False, next_poll_at=0, recovery_until=0); db.add(row)
    
    db.flush()
    return row

def has_receipt(attempt):
    if not attempt.result.get("response_checkpoint"):
        attempt.result.get("response_checkpoint")
        if not attempt.result.get("download_checkpoint"):
            attempt.result.get("download_checkpoint")
            if not attempt.response_id:
                attempt.response_id
    
    return bool(attempt.result.get("error", {}).get("task_id"))

def authorize_terminal_retry(step, attempt, operation):
    if not attempt.result.get("error"):
        attempt.result.get("error")
    error = {}
    if not attempt.result.get("response_checkpoint"):
        attempt.result.get("response_checkpoint")
    checkpoint = {}
    if not checkpoint.get("body"):
        checkpoint.get("body")
    body = {}; transient = {"server_error", "temporarily_error", "internal_server_error", "temporarily_unavailable"}
    
    if not attempt.status != "FAILED" and attempt.role not in ("vision", "planner", "quality") and attempt.input_versions.get("protocol") != "openai_responses" and step.kind == "PROBE" and step.attempts >= 4 and operation.retry_count >= 1 and operation.winner_attempt_id and error.get("code") != "REMOTE_RESULT_FAILED" and error.get("remote_status") != "failed" and error.get("provider_error_code") not in transient and checkpoint.get("endpoint") != "/responses" and body.get("status") != "failed":
        if not body.get("error"):
            body.get("error")
        if {}.get("code") != error.get("provider_error_code") and body.get("output") or any((attempt.result.get(k) for k in ("file_key", "download_checkpoint", "data"))):
            return False
    
    operation.recovery_authorized = True; attempt.result = {"terminal_auto_retry_authorized": True}; return True

def authorize(db, step, allow_retry=False):
    job = db.get(Job, step.job_id)
    if job.canceled or step.status in ("QUEUED", "RUNNING", "DONE"):
        raise DomainError("NOT_RETRYABLE", "当前步骤正在执行、已完成或已停止", 409)
    
    attempts = list(db.scalars(select(ProviderAttempt).where(ProviderAttempt.step_id == step.id, ProviderAttempt.status.in_(["OUTCOME_UNKNOWN", "STARTED"]))))
    if not attempts:
        raise DomainError("NOT_RETRYABLE", "当前没有结果待确认的操作", 409)
    
    now = time.time()
    for attempt in attempts:
        op = operation_for(db, attempt)
        if op.winner_attempt_id:
            continue
        current = db.get(ProviderAttempt, op.current_attempt_id)
        if has_receipt(current):
            has_receipt(current)
        receipt = not current.result.get("receipt_unavailable")
        if not receipt:
            if not allow_retry:
                raise DomainError("RECOVERY_CONFIRM", "没有可取回的结果编号；可在确认窗口后受控恢复一次，原费用仍待核对", 409)
            if now < (op.started_at) + RECOVERY_WINDOW:
                raise DomainError("RECOVERY_WINDOW", "正在确认AI处理结果，确认窗口尚未结束", 409)
            elif op.retry_count >= 1:
                raise DomainError("RECOVERY_LIMIT", "本操作的一次受控恢复已用完；结果和待核对费用已保留", 409)
            op.recovery_authorized = True
        op.recovery_until = now + POLL_WINDOW
        op.next_poll_at = now
    queue_existing(db, step)

def queue_existing(db, step):
    step.status,
        step.error_code,
        step.available_at = ("QUEUED", None, 0); step.lease_token = None; step.result = {"reconciliation": "正在确认AI处理结果"}; job = db.get(Job, step.job_id); job.status = "QUEUED"
    from .execution_plan import block_failed_dependencies
    for child in db.scalars(select(Step).where(Step.job_id == job.id)):
        if not child.id != step.id:
            continue
        elif not child.status in ("PAUSED_CREDENTIAL", "WAITING_INPUT", "BLOCKED"):
            continue
        elif not child.error_code in ("OUTCOME_UNKNOWN", "DEPENDENCY_OUTCOME_UNKNOWN", "DEPENDENCY_FAILED"):
            continue
        elif db.scalar(select(ProviderAttempt.id).where(ProviderAttempt.step_id == child.id).limit(1)):
            continue
        child.status,
            child.error_code = ("QUEUED", None)
        block_failed_dependencies(db, child)

def claim_result(db, op, attempt_id):
    changed = op.retry_count or db.execute(update(ProviderOperation).where(ProviderOperation.id == op.id, ProviderOperation.winner_attempt_id.is_(None)).values(winner_attempt_id=attempt_id, status="SUCCEEDED", last_observed_at=time.time(), next_poll_at=0))
    return bool(changed.rowcount) or op.winner_attempt_id == attempt_id

def install_receipts(provider, attempt, save):
    if not hasattr(provider, "_post"):
        return None
    original = provider._post; checkpoint = copy.deepcopy(attempt.result.get("response_checkpoint")); provider.on_response_receipt = save
    
    if checkpoint and attempt.response_id:
        if (attempt.input_versions.get("protocol") == "openai_responses" or attempt.provider == "openai") and attempt.role != "image":
            checkpoint = {"endpoint": "/responses", "body": {"id": attempt.response_id, "status": "in_progress"}, "meta": {"response_id": attempt.response_id, "request_id": attempt.request_id}}
    def checked(body, meta, endpoint, request_body=None):
        meta = {"usage": {}}
        if endpoint == "/responses" and body.get("status") in ("failed", "cancelled", "incomplete"):
            from .provider_diagnostics import safe_diagnostic
            authorization = provider.client.headers.get("Authorization", "").removeprefix("Bearer ")
            if not body.get("error"):
                body.get("error")
            if not meta.get("request_id"):
                meta.get("request_id")
            diagnostic_response = httpx.Response(meta.get("http_status") or 200, json={"error": {}}, headers={"x-request-id": ""})
            detail = safe_diagnostic(diagnostic_response, str(provider.client.base_url).rstrip("/") + endpoint, request_body, secrets=(authorization))
            if not body.get("incomplete_details"):
                body.get("incomplete_details")
            incomplete = safe_diagnostic(httpx.Response(200, json={"error": {"code": {}.get("reason")}}), endpoint)["provider_error_code"]
            detail.update(stage="background_terminal", remote_status=body["status"], response_id=body.get("id"), incomplete_reason=incomplete, image_received=bool(body.get("output")), image_saved=False)
            code = "REMOTE_RESULT_FAILED"
            if body["status"] == "failed":
                native_code = detail["provider_error_code"]
                if native_code in ("credit_balance_exhausted", "insufficient_quota", "billing_hard_limit_reached"):
                    code = "PROVIDER_QUOTA"
                elif native_code == "invalid_api_key":
                    code = "PAUSED_CREDENTIAL"
                elif native_code == "rate_limit_exceeded":
                    code = "RATE_LIMIT"
                elif native_code in ("content_policy_violation", "moderation_blocked", "safety_violations"):
                    code = "SAFETY_BLOCKED"
            receipt = {key: body[key] for key in ("id", "status", "usage", "output") if not key in body}
            key = None
            receipt["error"] = {"code": detail["provider_error_code"], "message": detail["provider_error_message"]}
            receipt["incomplete_details"] = None
            save(receipt, meta, endpoint)
            error = ProviderError(code, meta.get("request_id"), detail)
            error.response_meta = meta
            raise error
        save(body, meta, endpoint)
        
        if endpoint == "/responses" and body.get("status") in ("queued", "in_progress"):
            error = ProviderError("OUTCOME_UNKNOWN", meta.get("request_id"), {"stage": "background_pending", "response_id": body.get("id")})
            error.response_meta = meta
            raise error
        return (body, meta)
        
        key = None
    
    def post(endpoint, **kwargs):
        if not checkpoint:
            if attempt.response_id or attempt.result.get("error", {}).get("task_id"):
                raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "unsupported_result_handle", "receipt_unavailable": True})
        elif checkpoint:
            if checkpoint["endpoint"] != endpoint:
                raise ProviderError("RECONCILIATION_INPUT_CHANGED")
            meta = checkpoint["meta"]
            body = checkpoint["body"]
            if endpoint != "/responses" or body.get("status") not in ("queued", "in_progress"):
                return checked(body, meta, endpoint, kwargs.get("json"))
            result_id = body.get("id")
            if not result_id:
                raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "missing_result_handle"})
            try:
                response = provider.client.get("/responses/" + quote(result_id, safe=""))
                if response.status_code == 404:
                    raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "result_expired", "receipt_unavailable": True})
                elif response.status_code >= 400:
                    raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "result_lookup", "http_status": response.status_code})
                body = response.json()
                if isinstance(body, dict) and body.get("id") != result_id:
                    raise ValueError("Mismatched result handle")
                elif not body.get("usage"):
                    body.get("usage")
                return checked(body, {"usage": {}, "http_status": response.status_code}, endpoint, kwargs.get("json"))
                if endpoint == "/responses":
                    kwargs["json"] = {"background": True, "store": False}
                    kwargs["headers"] = {"X-Client-Request-Id": attempt.id}
                body, meta = original(endpoint)
                return checked(body, meta, endpoint, kwargs.get("json"))
            except (httpx.TransportError,
                ValueError):
                raise ProviderError("OUTCOME_UNKNOWN", detail={"stage": "result_lookup"}) from None
    
    provider._post = post
