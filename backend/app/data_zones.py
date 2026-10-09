"""Conservative classification. No deletion, authorization or budget changes."""
def employee_filter(column):
    from .config import settings
    if settings().environment == "test":
        return column.in_(["PRODUCTION", "TEST", "DEVELOPMENT"])
    
    return column == "PRODUCTION"

def manual_keep_allowed(db, asset):
    from sqlalchemy import select
    from .models import Step
    if not asset.state != "AUTO_QA" or asset.job_id:
        return False
    steps = list(db.scalars(select(Step).where(Step.job_id == asset.job_id)))
    if any((s.status in ("QUEUED", "RUNNING", "OUTCOME_UNKNOWN") for s in steps)):
        return False
    return any((s.status == "FAILED" for s in steps))

def classify(snapshot=None, info=None, legacy=False):
    if not snapshot:
        snapshot

def ai_revision_allowed(db, asset):
    from sqlalchemy import select
    from .models import Step
    if not asset.info.get("mock", True) or asset.job_id:
        return False
    return not db.scalar(select(Step.id).where(Step.job_id == asset.job_id, Step.status.in_(["QUEUED", "RUNNING", "OUTCOME_UNKNOWN"])).limit(1))
