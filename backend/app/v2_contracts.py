"""Phase 0-A read contracts. These are projections, never persistence models."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class ReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    view_schema_version: Literal["V2_READ_V1"] = "V2_READ_V1"

class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal[("ASSET", "MASTER_VERSION", "PRODUCT")]
    id: str; version: int | None = None
    sha256: str | None = None

class Integrity(BaseModel):
    state: Literal[("NOT_CHECKED", "VERIFIED", "BLOCKED")] = "NOT_CHECKED"
    code: str | None = None

class Relation(BaseModel):
    role: str
    target: SourceRef
    recorded_in: str

class RecordedEngineering(BaseModel):
    record_state: Literal[("MISSING", "VERIFIED", "BLOCKED")] = "MISSING"
    record: SourceRef | None = None
    source_svg: SourceRef | None = None
    optimized_svg: SourceRef | None = None
    schema_version: str | None = None
    recorded_status: Literal[("PASS", "WARNING", "REVIEW_REQUIRED", "SIZE_CONFIRMED", "SIZE_REQUIRED")] | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    unit: str | None = None
    size_source: Literal[("USER_INPUT", "PRODUCT_SPEC", "UNKNOWN")] | None = None
    specification: SourceRef | None = None
    code: str | None = None

class AssetView(ReadContract):
    source_ref: SourceRef
    module: str
    category: str
    state: str
    data_zone: str
    needs_confirmation: bool; asset_schema_version: str | None = None
    contract_version: str | None = None
    integrity: Integrity = Field(default_factory=Integrity)
    relations: list[Relation] = Field(default_factory=list)
    manufacturing: RecordedEngineering = Field(default_factory=RecordedEngineering)
    physical_size: RecordedEngineering = Field(default_factory=RecordedEngineering)

class ProductSummary(ReadContract):
    source_ref: SourceRef
    name: str

class ProductVersionView(BaseModel):
    source_ref: SourceRef
    previous_version: SourceRef | None
    current: bool
    approved: bool
    asset: AssetView

class ProductView(ProductSummary):
    current_version: SourceRef | None
    versions: list[ProductVersionView]; next_cursor: str | None = None

class ProductPage(ReadContract):
    items: list[ProductSummary]; next_cursor: str | None = None

class AssetPage(ReadContract):
    items: list[AssetView]; next_cursor: str | None = None
