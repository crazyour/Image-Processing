import time, uuid
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase

def uid():
    return str(uuid.uuid4())

class Base(DeclarativeBase):
    pass

class Identity:
    id = Column(String(36), primary_key=True, default=uid); created_at = Column(Float, nullable=False, default=time.time)

class Private:
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)

class Organization(Identity, Base):
    __tablename__ = "organizations"; name = Column(String(120), nullable=False); policy = Column(JSON, nullable=False, default=(lambda: {"version": 1, "max_attempts": 40, "max_revisions": 2, "engineering": {}})); route = Column(JSON, nullable=False, default=(lambda: {"version": 1, "provider": "mock", "roles": {}, "verified": False}))

class User(Identity, Base):
    __tablename__ = "users"; org_id = Column(String(36), ForeignKey("organizations.id"), nullable=False); username = Column(String(100), unique=True, nullable=False); password_hash = Column(Text, nullable=False); name = Column(String(100), nullable=False); role = Column(String(20), nullable=False, default="employee"); active = Column(Boolean, nullable=False, default=True)

class Workspace(Identity, Base):
    __tablename__ = "workspaces"; org_id = Column(String(36), ForeignKey("organizations.id"), nullable=False); owner_id = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False); name = Column(String(120), nullable=False); learning_enabled = Column(Boolean, nullable=False, default=False); personalization_enabled = Column(Boolean, nullable=False, default=True); review_position = Column(Integer, nullable=False, default=0); learned_position = Column(Integer, nullable=False, default=0); active_snapshot_id = Column(String(36))

class AssistantProfile(Identity, Private, Base):
    __tablename__ = "assistant_profiles"; name = Column(String(100), nullable=False, default="我的设计伙伴"); preferences = Column(JSON, nullable=False, default=dict)

class StyleProfile(Identity, Private, Base):
    __tablename__ = "style_profiles"; name = Column(String(100), nullable=False); module = Column(String(30), nullable=False); category = Column(String(100), nullable=False); series = Column(String(100), nullable=False, default=""); config = Column(JSON, nullable=False, default=dict)

class LoginSession(Identity, Base):
    __tablename__ = "login_sessions"; user_id = Column(String(36), ForeignKey("users.id"), nullable=False); token_hash = Column(String(64), unique=True, nullable=False); expires_at = Column(Float, nullable=False)

class Credential(Identity, Private, Base):
    __tablename__ = "credentials"; __table_args__ = (UniqueConstraint("workspace_id")); ciphertext = Column(Text, nullable=False); active = Column(Boolean, nullable=False, default=True); version = Column(Integer, nullable=False, default=1)

class ProviderCredential(Identity, Private, Base):
    __tablename__ = "provider_credentials"; __table_args__ = (UniqueConstraint("workspace_id", "provider")); provider = Column(String(30), nullable=False); ciphertext = Column(Text, nullable=False); active = Column(Boolean, nullable=False, default=True); version = Column(Integer, nullable=False, default=1); config = Column(JSON, nullable=False, default=dict)

class FreeUsage(Identity, Base):
    __tablename__ = "free_usage"; __table_args__ = (UniqueConstraint("account_scope", "day")); account_scope = Column(String(100), nullable=False); day = Column(String(10), nullable=False); reserved_units = Column(Float, nullable=False, default=0)

class BudgetAccount(Identity, Base):
    __tablename__ = "budget_accounts"; owner_key = Column(String(50), unique=True, nullable=False); limit_micros = Column(Integer, nullable=False, default=0); held_micros = Column(Integer, nullable=False, default=0); spent_micros = Column(Integer, nullable=False, default=0)

class BudgetPool(Identity, Private, Base):
    __tablename__ = "budget_pools"; org_id = Column(String(36), ForeignKey("organizations.id"), nullable=False); total = Column(Integer, nullable=False); spent = Column(Integer, nullable=False, default=0); pending = Column(Integer, nullable=False, default=0); released = Column(Boolean, nullable=False, default=False); simulated = Column(Boolean, nullable=False)

class Recipe(Identity, Private, Base):
    __tablename__ = "recipes"; name = Column(String(100), nullable=False); version = Column(Integer, nullable=False, default=1); config = Column(JSON, nullable=False)

class Schedule(Identity, Private, Base):
    __tablename__ = "schedules"; name = Column(String(100), nullable=False); config = Column(JSON, nullable=False); timezone = Column(String(60), nullable=False); weekdays = Column(JSON, nullable=False); hour = Column(Integer, nullable=False); minute = Column(Integer, nullable=False); enabled = Column(Boolean, nullable=False, default=False); live_authorized = Column(Boolean, nullable=False, default=False); backlog_limit = Column(Integer, nullable=False, default=60); next_at = Column(Float, nullable=False); last_reason = Column(String(100))

class Job(Identity, Private, Base):
    __tablename__ = "jobs"; data_zone = Column(String(20), nullable=False, default="PRODUCTION"); needs_confirmation = Column(Boolean, nullable=False, default=False); __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key")); idempotency_key = Column(String(120), nullable=False); request_hash = Column(String(64), nullable=False); module = Column(String(30), nullable=False); category = Column(String(100), nullable=False); status = Column(String(40), nullable=False, default="QUEUED"); snapshot = Column(JSON, nullable=False); pool_id = Column(String(36), ForeignKey("budget_pools.id"), nullable=False); source_asset_id = Column(String(36))
    
    master_id = Column(String(36))
    
    parent_id = Column(String(36), ForeignKey("jobs.id")); revision = Column(Integer, nullable=False, default=0)
    
    canceled = Column(Boolean, nullable=False, default=False)

class Step(Identity, Private, Base):
    __tablename__ = "steps"; __table_args__ = (UniqueConstraint("job_id", "ordinal"), Index("ix_steps_claim", "status", "available_at")); job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); ordinal = Column(Integer, nullable=False); kind = Column(String(20), nullable=False); status = Column(String(40), nullable=False, default="QUEUED"); payload = Column(JSON, nullable=False, default=dict); result = Column(JSON, nullable=False, default=dict); attempts = Column(Integer, nullable=False, default=0); lease_until = Column(Float, nullable=False, default=0); lease_token = Column(String(36)); available_at = Column(Float, nullable=False, default=0)
    
    error_code = Column(String(80))

class DesignDraft(Identity, Private, Base):
    __tablename__ = "design_drafts"; __table_args__ = (UniqueConstraint("workspace_id", "create_key")); updated_at = Column(Float, nullable=False, default=time.time); revision = Column(Integer, nullable=False, default=1); status = Column(String(20), nullable=False, default="ACTIVE"); module = Column(String(30), nullable=False); state = Column(JSON, nullable=False); accepted_proposal = Column(JSON); create_key = Column(String(120), nullable=False); create_hash = Column(String(64), nullable=False)

class DesignDiscussionTurn(Identity, Private, Base):
    __tablename__ = "design_discussion_turns"; __table_args__ = (UniqueConstraint("draft_id", "operation_key"), UniqueConstraint("draft_id", "turn_index")); draft_id = Column(String(36), ForeignKey("design_drafts.id"), nullable=False); turn_index = Column(Integer, nullable=False); operation_key = Column(String(120), nullable=False); request_hash = Column(String(64), nullable=False); base_revision = Column(Integer, nullable=False); user_text = Column(Text, nullable=False); input_snapshot = Column(JSON, nullable=False); job_id = Column(String(36), ForeignKey("jobs.id")); status = Column(String(30), nullable=False, default="QUEUED"); reply = Column(JSON)
    
    proposal_hash = Column(String(64)); error_summary = Column(JSON)

class ProviderAttempt(Identity, Private, Base):
    __tablename__ = "provider_attempts"; step_id = Column(String(36), ForeignKey("steps.id"), nullable=False); pool_id = Column(String(36), ForeignKey("budget_pools.id"), nullable=False); role = Column(String(30), nullable=False); provider = Column(String(20), nullable=False); status = Column(String(30), nullable=False); reserved = Column(Integer, nullable=False); charged = Column(Integer, nullable=False, default=0); cost_kind = Column(String(20), nullable=False, default="ESTIMATE"); request_id = Column(String(150)); response_id = Column(String(150))
    
    usage = Column(JSON, nullable=False, default=dict); result = Column(JSON, nullable=False, default=dict)
    
    model = Column(String(100)); capability = Column(String(40)); duration_ms = Column(Integer)
    
    input_hash = Column(String(64))
    
    output_hash = Column(String(64)); input_versions = Column(JSON, nullable=False, default=dict)

class ProviderOperation(Identity, Private, Base):
    __tablename__ = "provider_operations"; __table_args__ = (UniqueConstraint("step_id", "role", "request_hash", name="uq_provider_operation_input")); job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); step_id = Column(String(36), ForeignKey("steps.id"), nullable=False); role = Column(String(30), nullable=False); provider = Column(String(40), nullable=False); model = Column(String(100)); request_hash = Column(String(64), nullable=False); request_id = Column(String(150)); response_id = Column(String(150)); started_at = Column(Float, nullable=False)
    
    last_observed_at = Column(Float, nullable=False); status = Column(String(30), nullable=False, default="PENDING")
    
    cost_state = Column(String(30), nullable=False, default="PENDING"); current_attempt_id = Column(String(36), ForeignKey("provider_attempts.id"))
    
    winner_attempt_id = Column(String(36), ForeignKey("provider_attempts.id")); retry_count = Column(Integer, nullable=False, default=0); recovery_authorized = Column(Boolean, nullable=False, default=False); next_poll_at = Column(Float, nullable=False, default=0); recovery_until = Column(Float, nullable=False, default=0)

class Asset(Identity, Private, Base):
    __tablename__ = "assets"; data_zone = Column(String(20), nullable=False, default="PRODUCTION"); needs_confirmation = Column(Boolean, nullable=False, default=False); job_id = Column(String(36), ForeignKey("jobs.id")); step_id = Column(String(36), ForeignKey("steps.id"), unique=True); parent_id = Column(String(36), ForeignKey("assets.id")); input_asset_id = Column(String(36)); master_id = Column(String(36)); module = Column(String(30), nullable=False); category = Column(String(100), nullable=False)
    
    state = Column(String(30), nullable=False, default="READY_FOR_SELECTION"); version = Column(Integer, nullable=False, default=1)
    
    file_key = Column(String(220), nullable=False); sha256 = Column(String(64), nullable=False)
    
    info = Column(JSON, nullable=False, default=dict)
    
    deleted = Column(Boolean, nullable=False, default=False)

class Product(Identity, Private, Base):
    __tablename__ = "products"; name = Column(String(150), nullable=False); current_master_id = Column(String(36))

class MasterVersion(Identity, Private, Base):
    __tablename__ = "master_versions"; product_id = Column(String(36), ForeignKey("products.id"), nullable=False); asset_id = Column(String(36), ForeignKey("assets.id"), unique=True, nullable=False); previous_id = Column(String(36)); master_hash = Column(String(64), nullable=False); width_mm = Column(Float); geometry = Column(JSON, nullable=False); facts = Column(JSON, nullable=False); approved = Column(Boolean, nullable=False, default=True)

class Suggestion(Identity, Private, Base):
    __tablename__ = "suggestions"; asset_id = Column(String(36), ForeignKey("assets.id"), nullable=False); source_hash = Column(String(64), nullable=False); kind = Column(String(50), nullable=False); info = Column(JSON, nullable=False); used_job_id = Column(String(36))

class Review(Identity, Private, Base):
    __tablename__ = "reviews"; __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key")); asset_id = Column(String(36), ForeignKey("assets.id"), nullable=False); idempotency_key = Column(String(120), nullable=False); position = Column(Integer, nullable=False); action = Column(String(20), nullable=False); reason = Column(String(50)); scope = Column(String(20), nullable=False); previous_state = Column(String(30), nullable=False); learning_allowed = Column(Boolean, nullable=False); revoked = Column(Boolean, nullable=False, default=False)

class Outbox(Identity, Private, Base):
    __tablename__ = "outbox"; __table_args__ = (UniqueConstraint("event_key")); event_key = Column(String(150), nullable=False); kind = Column(String(30), nullable=False); payload = Column(JSON, nullable=False); processed = Column(Boolean, nullable=False, default=False); attempts = Column(Integer, nullable=False, default=0); error_code = Column(String(100))

class PreferenceEvidence(Identity, Private, Base):
    __tablename__ = "preference_evidence"; review_id = Column(String(36), ForeignKey("reviews.id"), unique=True); module = Column(String(30), nullable=False); category = Column(String(100), nullable=False); feature = Column(String(80), nullable=False); direction = Column(Float, nullable=False); strength = Column(Float, nullable=False); scope = Column(String(20), nullable=False); context_id = Column(String(100), nullable=False); kind = Column(String(30), nullable=False); active = Column(Boolean, nullable=False, default=True); mode = Column(String(12), nullable=False, default="DEMO")
    
    history_card_id = Column(String(36), ForeignKey("history_cards.id"), unique=True); source = Column(JSON, nullable=False, default=dict)

class HistoryBatch(Identity, Private, Base):
    __tablename__ = "history_batches"; __table_args__ = (UniqueConstraint("workspace_id", "source_hash")); source_hash = Column(String(64), nullable=False); filename = Column(String(150), nullable=False); format = Column(String(30), nullable=False); status = Column(String(30), nullable=False, default="PARSED"); summary = Column(JSON, nullable=False, default=dict)

class HistoryConversation(Identity, Private, Base):
    __tablename__ = "history_conversations"; batch_id = Column(String(36), ForeignKey("history_batches.id"), nullable=False); source_id = Column(String(150), nullable=False); title = Column(String(300), nullable=False); is_design = Column(Boolean, nullable=False); messages = Column(JSON, nullable=False); image_refs = Column(JSON, nullable=False, default=list); selected = Column(Boolean, nullable=False, default=False)

class HistoryCard(Identity, Private, Base):
    __tablename__ = "history_cards"; __table_args__ = (UniqueConstraint("workspace_id", "fingerprint")); batch_id = Column(String(36), ForeignKey("history_batches.id"), nullable=False); conversation_id = Column(String(36), ForeignKey("history_conversations.id"), nullable=False); fingerprint = Column(String(64), nullable=False); message_id = Column(String(150), nullable=False); kind = Column(String(40), nullable=False); text = Column(String(1500), nullable=False); feature = Column(String(80)); direction = Column(Integer, nullable=False, default=0); module = Column(String(30), nullable=False, default="DESIGN")
    
    category = Column(String(100), nullable=False, default="未指定产品")
    
    scope = Column(String(20), nullable=False, default="CATEGORY"); context_id = Column(String(100), nullable=False, default="")
    
    status = Column(String(30), nullable=False, default="PENDING")
    
    source_role = Column(String(30), nullable=False); image_refs = Column(JSON, nullable=False, default=list)

class CallAuthorization(Identity, Private, Base):
    __tablename__ = "call_authorizations"; kind = Column(String(30), nullable=False); status = Column(String(20), nullable=False, default="APPROVED"); plan = Column(JSON, nullable=False); used = Column(JSON, nullable=False, default=dict); expires_at = Column(Float, nullable=False); credential_version = Column(Integer, nullable=False)

class ExperiencePackage(Identity, Private, Base):
    __tablename__ = "experience_packages"; __table_args__ = (UniqueConstraint("workspace_id", "content_hash")); kind = Column(String(30), nullable=False); content_hash = Column(String(64), nullable=False); content = Column(JSON, nullable=False); status = Column(String(30), nullable=False, default="AWAITING_REVIEW")

class PublisherTrust(Identity, Private, Base):
    __tablename__ = "publisher_trust"; __table_args__ = (UniqueConstraint("workspace_id", "fingerprint")); fingerprint = Column(String(64), nullable=False); public_key = Column(String(100), nullable=False)

class HubPeer(Identity, Private, Base):
    __tablename__ = "hub_peers"; label = Column(String(100), nullable=False); token_hash = Column(String(64), nullable=False, unique=True); active = Column(Boolean, nullable=False, default=True); last_seen = Column(Float)

class CapabilityCheck(Identity, Private, Base):
    __tablename__ = "capability_checks"; grant_id = Column(String(36), ForeignKey("call_authorizations.id"), nullable=False); attempt_id = Column(String(36), ForeignKey("provider_attempts.id"), nullable=False); capability = Column(String(40), nullable=False); status = Column(String(30), nullable=False); model = Column(String(100), nullable=False); credential_version = Column(Integer, nullable=False)

class BlindComparison(Identity, Private, Base):
    __tablename__ = "blind_comparisons"; category = Column(String(100), nullable=False); left_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); right_job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); personal_side = Column(String(5), nullable=False); choice = Column(String(10)); voted_at = Column(Float); mode = Column(String(12), nullable=False)

class PreferenceRule(Identity, Private, Base):
    __tablename__ = "preference_rules"; __table_args__ = (UniqueConstraint("workspace_id", "rule_key")); rule_key = Column(String(300), nullable=False); enabled = Column(Boolean, nullable=False, default=True); data = Column(JSON, nullable=False)

class PreferenceSnapshot(Identity, Private, Base):
    __tablename__ = "preference_snapshots"; position = Column(Integer, nullable=False); rules = Column(JSON, nullable=False); reason = Column(String(80), nullable=False)

class RetrievalTrace(Identity, Private, Base):
    __tablename__ = "retrieval_traces"; job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); snapshot_id = Column(String(36)); stage = Column(String(30), nullable=False); rules = Column(JSON, nullable=False); explanation = Column(String(300), nullable=False)

class Export(Identity, Private, Base):
    __tablename__ = "exports"; master_id = Column(String(36), ForeignKey("master_versions.id"), nullable=False); asset_id = Column(String(36), ForeignKey("assets.id"), nullable=False); file_key = Column(String(220), nullable=False); manifest = Column(JSON, nullable=False); report = Column(JSON, nullable=False)

class Audit(Identity, Base):
    __tablename__ = "audit_events"; org_id = Column(String(36), nullable=False); actor_id = Column(String(36), nullable=False); workspace_id = Column(String(36), nullable=False); action = Column(String(100), nullable=False); resource_id = Column(String(100)); detail = Column(JSON, nullable=False, default=dict)

from sqlalchemy import event

@event.listens_for(Job, "before_insert")
def classify_job(mapper, connection, target):
    from .data_zones import classify
    target.data_zone,
        target.needs_confirmation = classify(snapshot=target.snapshot)

@event.listens_for(Asset, "before_insert")
def classify_asset(mapper, connection, target):
    from .data_zones import classify
    from sqlalchemy import select
    target.data_zone,
        target.needs_confirmation = classify(info=target.info)
    if target.job_id:
        if target.data_zone != "TEST":
            row = connection.execute(select(Job.data_zone, Job.needs_confirmation).where(Job.id == target.job_id)).first()
            if row:
                target.data_zone,
                    target.needs_confirmation = row
                return None
            return None
        return None

from . import ai_connection_models
from . import craft_models
