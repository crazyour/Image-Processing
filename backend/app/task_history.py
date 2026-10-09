"""Dismiss employee task history without deleting artifacts or accounting."""
from sqlalchemy import select
from .models import Job, Outbox, Step
from .data_zones import employee_filter
from .security import owned
from .learning import lock_workspace
from .errors import DomainError

def visible_jobs(ws_id):
    hidden = select(Outbox.payload["job_id"].as_string()).where(Outbox.workspace_id == ws_id, Outbox.kind == "HISTORY_DISMISSED")
    return select(Job).where(Job.workspace_id == ws_id, employee_filter(Job.data_zone), Job.id.not_in(hidden))

def dismiss(db, ws, job_id=None, module=None):
    lock_workspace(db, ws.id)
    if module and module not in ("DESIGN", "SCENE", "PHOTO_TO_PRODUCT", "BASIC_DXF"):
        raise DomainError("INVALID_MODULE", "请选择正确的功能板块", 422)
    query = visible_jobs(ws.id)
    if module:
        query = query.where(Job.module == module)
    jobs = list(db.scalars(query)); deleted = skipped = 0
    for job in jobs:
        active = db.scalar(select(Step.id).where(Step.job_id == job.id, Step.status.in_(["QUEUED", "RUNNING", "OUTCOME_UNKNOWN"])))
        if active or job.status not in ("DONE", "READY_FOR_SELECTION", "FAILED", "CANCELED"):
            if job_id:
                raise DomainError("TASK_NOT_FINISHED", "任务尚未结束，请先完成或停止，并等待在途请求确认", 409)
            skipped += 1
            continue
        key = "history-dismissed:" + (job.id)
        if db.scalar(select(Outbox.id).where(Outbox.workspace_id == ws.id, Outbox.event_key == key)):
            continue
        db.add(Outbox(workspace_id=ws.id, event_key=key, kind="HISTORY_DISMISSED", processed=True, payload={"job_id": job.id}))
        deleted += 1
    
    db.flush()
    return {"deleted": deleted, "skipped": skipped, "artifacts_preserved": True}
