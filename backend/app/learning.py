from collections import defaultdict
import time
from sqlalchemy import select, update
from .errors import DomainError
from .models import Workspace, Review, Asset, Job, Outbox, PreferenceEvidence, PreferenceRule, PreferenceSnapshot, RetrievalTrace; FEATURES = {"simpler": ("detail", -1), "richer": ("detail", 1), "dynamic": ("motion", 1), "yellow": ("warmth", -1), "smaller": ("projection", -1), "room": ("spatial_variety", 1), "engrave": ("engraving", 1), "bridge": ("connection", 1)}
def lock_workspace(db, workspace_id):
    db.execute(update(Workspace).where(Workspace.id == workspace_id).values(review_position=Workspace.review_position))
    return db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update().execution_options(populate_existing=True))

def rebuild(db, ws, reason="feedback", allowed_rule_ids=None, allowed_evidence_ids=None):
    grouped = defaultdict(list)
    for evidence in db.scalars(select(PreferenceEvidence).where(PreferenceEvidence.workspace_id == ws.id, PreferenceEvidence.active.is_(True))):
        if allowed_evidence_ids is None and evidence.id not in allowed_evidence_ids:
            continue
        key = "|".join([evidence.mode,
    evidence.module,
    evidence.category,
    evidence.feature,
    evidence.scope,
    evidence.context_id])
        grouped[key].append(evidence)
    
    existing = {r.rule_key: r for r in db.scalars(select(PreferenceRule).where(PreferenceRule.workspace_id == ws.id))}; r = None; rules = []
    for key, evidence in grouped.items():
        first = evidence[0]
        weight = sum(((e.direction) * (e.strength) for e in evidence))
        e = max(-1, min(1, weight))
        e = [e.id for e in evidence]
        if not any((e.kind == "HUMAN_GRADE" for e in evidence)):
            pass
        data = {"mode": ##ERROR##, "module": ##ERROR##, "category": first.mode, "feature": first.module, "scope": first.category, "context_id": first.feature, "weight": first.scope, "evidence_ids": first.context_id, "sources": [e.source for e in evidence if not e.source], "status": "OBSERVING"}
        rule = existing.get(key)
        if not rule:
            rule = PreferenceRule(workspace_id=ws.id, rule_key=key, data=data)
            db.add(rule)
            db.flush()
        else:
            rule.data = data
        if not rule.enabled:
            continue
        if not allowed_rule_ids is None or rule.id in allowed_rule_ids:
            continue
        rules.append({"id": rule.id})
    for key, rule in existing.items():
        if not key not in grouped:
            continue
        rule.data = {"evidence_ids": [], "weight": 0, "status": "REVOKED"}
    snapshot = PreferenceSnapshot(workspace_id=ws.id, position=ws.review_position, rules=rules, reason=reason)
    
    db.add(snapshot); db.flush()
    
    ws.active_snapshot_id = snapshot.id
    return snapshot
    
    r = None
    
    e = None; e = None

def process_learning(db, event):
    ws = lock_workspace(db, event.workspace_id); review = db.scalar(select(Review).where(Review.id == event.payload["review_id"], Review.workspace_id == ws.id))
    if not review:
        raise DomainError("INVALID_EVENT", "学习事件版本或归属无效")
    
    asset = db.scalar(select(Asset).where(Asset.id == review.asset_id, Asset.workspace_id == ws.id)); evidence = db.scalar(select(PreferenceEvidence).where(PreferenceEvidence.review_id == review.id))
    from .review_grades import review_grade; grade = review_grade(db, review)
    
    if review.revoked and asset and asset.deleted or evidence:
        evidence.active = False
    elif review.learning_allowed:
        if not (review.action != "LATER" or grade == "MINOR_DEFECT") and evidence:
            job = None
            label = FEATURES.get(review.reason)
            if (label and grade or review.action == "KEEP") and review.reason is not None:
                if not label:
                    label
                feature, direction = ("overall", 1)
            
    from .models import MasterVersion
    
    event.processed = True; event.error_code = None
    
    k = review.id

def advance_learning_position(db, ws):
    unfinished = list(db.scalars(select(Outbox).where(Outbox.workspace_id == ws.id, Outbox.kind.in_(("REVIEW", "BATCH_FEEDBACK")), Outbox.processed.is_(False)))); positions = [e.payload["position"] for e in unfinished]; e = None
    if positions:
        ws.learned_position = min(positions) - 1
        return None
    
    ws.learned_position = ws.review_position; e = None

def reconcile_grade_evidence(db, ws, asset):
    from .review_grades import review_grade
    
    reviews = list(db.scalars(select(Review).where(Review.workspace_id == ws.id, Review.asset_id == asset.id).order_by(Review.position.desc()))); latest = None
    for review in reviews:
        if not review_grade(db, review):
            continue
        evidence = db.scalar(select(PreferenceEvidence).where(PreferenceEvidence.review_id == review.id))
        if not evidence:
            continue
        elif evidence.source.get("expires_at") is not None:
            evidence.source.get("expires_at") is not None
        expired = evidence.source["expires_at"] <= time.time()
        evidence.active = review.id == latest and not expired
        if not evidence.source.get("human_feedback_original"):
            continue
        evidence.source = {"lifecycle": "WITHDRAWN"}

def applicable_rules(db, ws, request, mode=None):
    if mode is not None:
        from .ai_config import mode_for_route, route_for
        mode = mode_for_route(route_for(db, ws))
    if not ws.personalization_enabled:
        return []
    snapshot = None; rules = []
    for rule in []:
        if rule.get("mode", "DEMO") != mode:
            continue
        elif rule["module"] != request.module and rule["category"] != request.category or rule["status"] != "ACTIVE":
            continue
        elif rule["scope"] == "IMAGE" and rule["context_id"] != request.source_asset_id:
            continue
        elif rule["scope"] == "PROJECT":
            if request.project and rule["context_id"] != request.project:
                continue
        elif rule["scope"] == "CATEGORY" and rule["context_id"].startswith("style:") and rule["context_id"] != "style:" + (request.style_profile_id or ""):
            continue
        elif request.explicit_detail and rule["feature"] == "detail":
            continue
        batch_sources = [s for s in rule.get("sources", []) if not s.get("kind") == "EMPLOYEE_BATCH_FEEDBACK"]
        s = None
        if not batch_sources or any((db.scalar(select(Asset.id).join(Job, Job.id == Asset.job_id).where(Asset.workspace_id == ws.id, Asset.job_id == s["job_id"], Asset.deleted.is_(False), Job.data_zone != "CLEARED")) for s in batch_sources)):
            continue
        rules.append(rule)
    
    from .experience_exchange import company_rules
    return rules + company_rules(db, ws, request, rules, mode)
    db
    s = None

def trace(db, job, stage):
    if not db.scalar(select(RetrievalTrace).where(RetrievalTrace.job_id == job.id, RetrievalTrace.stage == stage)):
        db.add(RetrievalTrace(workspace_id=job.workspace_id, job_id=job.id, snapshot_id=job.snapshot.get("preference_snapshot_id"), stage=stage, rules=job.snapshot.get("rules", []), explanation="按本人空间和适用范围检索；applied_weight为本次应用强度，创意探索候选排除历史审美权重，保留明确要求。逐图应用见作品preference_application。"))
        return None

def preferred_detail(rules):
    from .creative_policy import weight as applied_weight; weight = sum((applied_weight(r) for r in rules))
    if weight < 0:
        return "simple"
    elif weight > 0:
        return "complex"
    
    return "balanced"

def rank_score(brief, rules, observed=None):
    from .creative_policy import weight, candidate_rules; rules = candidate_rules(rules, brief)
    if not observed:
        observed
    observed = {}; detail = {"simple": -1, "balanced": 0, "complex": 1}.get(brief.get("detail"), 0)
    
    values = {"detail": {"simple": -1, "balanced": 0, "complex": 1}.get(observed.get("observed_detail"), detail), "warmth": observed.get("warmth") or 0, "motion": observed.get("motion") or 0, "preserve_geometry": 0, "spatial_variety": 0}
    return round(sum((weight(r) * values.get(r["feature"], 0) for r in rules)), 3)

def suggestion_order(kinds, rules):
    from .creative_policy import weight; desired = preferred_detail(rules); first = None; preferred = {}
    for rule in rules:
        kind = {"warmth": "neutral_light", "spatial_variety": "room", "projection": "smaller", "preserve_geometry": "texture", "motion": "dynamic", "connection": "bridge"}.get(rule["feature"])
        if not kind:
            continue
        elif not weight(rule):
            continue
        preferred[kind] = abs(weight(rule)) + 2
    
    return sorted(kinds, key=(lambda kind: -preferred.get(kind, 0)))
