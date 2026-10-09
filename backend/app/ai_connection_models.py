"""V2 connection documents. No dependency on legacy credential/configuration rows."""
import time
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from .models import Base, Identity, Private

class AISecret(Identity, Private, Base):
    __tablename__ = "ai_connection_secrets"; ciphertext = Column(Text, nullable=False)

class AIConnectionProfile(Identity, Private, Base):
    __tablename__ = "ai_connection_profiles"; __table_args__ = (UniqueConstraint("workspace_id", "creation_key")); name = Column(String(100), nullable=False); provider = Column(String(40), nullable=False); endpoint = Column(String(500), nullable=False); api_key_reference = Column(String(36), ForeignKey("ai_connection_secrets.id"), nullable=False); model_config = Column(JSON, nullable=False); capabilities = Column(JSON, nullable=False); status = Column(String(40), nullable=False, default="NOT_TESTED"); version = Column(Integer, nullable=False, default=1); enabled = Column(Boolean, nullable=False, default=True); deleted = Column(Boolean, nullable=False, default=False); creation_key = Column(String(120), nullable=False)
    
    creation_digest = Column(String(64), nullable=False)
    
    updated_at = Column(Float, nullable=False, default=time.time)

class AIConnectionTest(Identity, Private, Base):
    __tablename__ = "ai_connection_tests"; __table_args__ = (UniqueConstraint("workspace_id", "request_key")); profile_id = Column(String(36), ForeignKey("ai_connection_profiles.id"), nullable=False); profile_version = Column(Integer, nullable=False); request_key = Column(String(120), nullable=False); status = Column(String(40), nullable=False, default="TESTING"); result = Column(JSON, nullable=False, default=dict); finished_at = Column(Float)

class AIUsageRecord(Identity, Private, Base):
    __tablename__ = "ai_usage_records"; attempt_id = Column(String(36), ForeignKey("provider_attempts.id"), nullable=False, unique=True); profile_id = Column(String(36), ForeignKey("ai_connection_profiles.id"), nullable=False); profile_version = Column(Integer, nullable=False); product_id = Column(String(36)); exploration_id = Column(String(36)); task_id = Column(String(36), ForeignKey("jobs.id"), nullable=False); capability = Column(String(40), nullable=False); model = Column(String(150), nullable=False); reason = Column(String(200), nullable=False)
    
    status = Column(String(40), nullable=False); input_tokens = Column(Integer)
    
    output_tokens = Column(Integer); cost = Column(Float); currency = Column(String(10))
    
    cost_source = Column(String(40), nullable=False, default="NOT_REPORTED")
    
    evidence = Column(String(40), nullable=False, default="PROVIDER_RESPONSE"); updated_at = Column(Float, nullable=False, default=time.time)
