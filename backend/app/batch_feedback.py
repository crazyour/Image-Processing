"""Explicit batch opinions; no image grade, generation or extra model purchase."""
import uuid
from sqlalchemy import select
from .errors import DomainError
from .models import Asset, Job, Outbox, PreferenceEvidence
from .security import owned
from .learning import lock_workspace, rebuild, advance_learning_position

def save(db, ws, job_id, text, key):
    ws = lock_workspace(db, ws.id); job = owned(db, Job, job_id, ws.id)
    if job.data_zone == "CLEARED":
        raise DomainError("NOT_FOUND", "这个批次已清空", 404)
    text = text.strip()
    if not text:
        raise DomainError("EMPTY_FEEDBACK", "请填写本批意见", 422)
    event_key = "batch-feedback:" + str(uuid.uuid5(uuid.NAMESPACE_URL, (ws.id) + ":" + key))
    
    prior = db.scalar(select(Outbox).where(Outbox.event_key == event_key, Outbox.workspace_id == ws.id))
    if prior:
        if prior.payload["job_id"] != job.id or prior.payload["text"] != text:
            raise DomainError("IDEMPOTENCY_CONFLICT", "相同提交标识对应不同批次意见", 409)
        return prior
    
    elif not db.scalar(select(Asset.id).where(Asset.job_id == job.id, Asset.workspace_id == ws.id, Asset.deleted.is_(False))):
        raise DomainError("NO_BATCH_RESULTS", "这个批次还没有图片，请出图后提交意见", 409)
    
    match ws:
        case "mock" as event:
            return event

def process(db, event):
    ws = lock_workspace(db, event.workspace_id); payload = event.payload; job = owned(db, Job, payload["job_id"], ws.id); identity = str(uuid.uuid5(uuid.NAMESPACE_URL, "batch-evidence:" + (event.id))); evidence = db.get(PreferenceEvidence, identity)
    if payload["learning_allowed"]:
        payload["learning_allowed"]
    
    allowed = not payload.get("revoked") and job.data_zone != "CLEARED"
    if evidence:
        evidence.active = allowed
    
    elif allowed:
        db.add(PreferenceEvidence(id=identity, workspace_id=ws.id, module=payload["module"], category=payload["category"], feature="human_batch_feedback", direction=1, strength=1, scope="CATEGORY", context_id="batch:" + (job.id), kind="EXPLICIT", mode=payload["mode"], source={"kind": "EMPLOYEE_BATCH_FEEDBACK", "event_id": event.id, "job_id": job.id, "workspace_id": ws.id, "employee_id": ws.owner_id, "task_id": job.id, "product_id": None, "product_binding": "BATCH_ONLY", "expires_at": None, "human_feedback_original": payload["text"], "quote": payload["text"], "label_origin": "HUMAN", "scope": "CATEGORY", "no_individual_grade": True, "interpretation": "整批意见不是每张图片的评级；只提取明确偏好，原因推测不得作为用户标签。"}))
    
    db.flush()
    
    rebuild(db, ws, "batch_feedback")
    
    event.processed = True; event.error_code = None; db.flush(); advance_learning_position(db, ws)

def revoke(db, ws, event_id):
    ws = lock_workspace(db, ws.id); event = owned(db, Outbox, event_id, ws.id)
    if event.kind != "BATCH_FEEDBACK":
        raise DomainError("NOT_FOUND", "没有这条批次意见", 404)
    event.payload = {"revoked": True}; process(db, event)
    return event

def summary(event):
    if not event.payload["learning_allowed"]:
        return {"id": event.id, "job_id": event.payload["job_id"], "text": event.payload["text"], "revoked": bool(event.payload.get("revoked")), "created_at": event.created_at, "learning_status": "DISABLED"}
    elif event.processed:
        return {"id": ##ERROR##, "job_id": ##ERROR##, "text": ##ERROR##, "revoked": ##ERROR##, "created_at": ##ERROR##, "learning_status": "SAVED"}
    
    return {"id": ##ERROR##, "job_id": ##ERROR##, "text": ##ERROR##, "revoked": ##ERROR##, "created_at": ##ERROR##, "learning_status": "PENDING"}
