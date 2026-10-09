"""Independent append-only template/design branches; reuse existing private Assets."""
from sqlalchemy import Column, String, Integer, JSON, ForeignKey, UniqueConstraint
from .models import Base, Identity, Private

class CraftTemplate(Identity, Private, Base):
    __tablename__ = "craft_templates"; parent_id = Column(String(36), ForeignKey("craft_templates.id")); version = Column(Integer, nullable=False, default=1); spec = Column(JSON, nullable=False); paths = Column(JSON, nullable=False); content_hash = Column(String(64), nullable=False); assets = Column(JSON, nullable=False, default=dict); confirmation = Column(JSON, nullable=False, default=dict)

class CraftDesign(Identity, Private, Base):
    __tablename__ = "craft_designs"; __table_args__ = (UniqueConstraint("workspace_id", "create_key")); create_key = Column(String(120), nullable=False); request_hash = Column(String(64), nullable=False); template_id = Column(String(36), ForeignKey("craft_templates.id"), nullable=False); parent_id = Column(String(36), ForeignKey("craft_designs.id")); source = Column(JSON, nullable=False); template_hash = Column(String(64), nullable=False); requirements = Column(String(2000), nullable=False, default=""); revision = Column(Integer, nullable=False, default=1); status = Column(String(30), nullable=False, default="draft")
    
    ai_result = Column(JSON, nullable=False, default=dict); assets = Column(JSON, nullable=False, default=dict)
    
    approval = Column(JSON, nullable=False, default=dict); production_analysis = Column(JSON, nullable=False, default=dict); job_id = Column(String(36), ForeignKey("jobs.id"))
    
    export_job_id = Column(String(36), ForeignKey("jobs.id"))
