from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .intent_state import IntentControls; Module = Literal[("DESIGN", "SCENE", "PHOTO_TO_PRODUCT", "BASIC_DXF")]
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr

class UserDesignDirection(Strict):
    id: str = Field(pattern="^USER_[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=800)
    
    @model_validator(mode="after")
    def nonempty(self):
        if not self.label.strip() and self.prompt.strip():
            raise ValueError("方向名称和设计内容不能为空")
        return self

class JobIn(Strict):
    module: Module = "DESIGN"
    intent_controls: IntentControls | None = Field(default=None, exclude_if=(lambda value: value is None))
    intent_source: Literal[("FREETEXT", "PRESET", "ADOPTED_DISCUSSION")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    draft_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    draft_revision: int | None = Field(default=None, ge=1, exclude_if=(lambda value: value is None))
    scene_variation_axes: list[Literal[("SPATIAL_LAYOUT", "ELEMENT_GROUPING")]] | None = Field(default=None, max_length=2, exclude_if=(lambda value: value is None))
    workflow_mode: Literal[("PRESET", "CUSTOM")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    creative_entry_mode: Literal[("PRESET", "PRESET_WITH_TEXT", "AI_DIFFERENTIATION")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_family_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_direction_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_direction_ids: list[str] | None = Field(default=None, min_length=1, max_length=12, exclude_if=(lambda value: value is None))
    custom_design_directions: list[UserDesignDirection] | None = Field(default=None, max_length=12, exclude_if=(lambda value: value is None))
    preset_set_mode_id: Literal[("SINGLE", "TRIPTYCH", "THREE_PIECE_SET")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_process_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_material_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_scene_card_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    preset_output_mode: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    assembly_template_asset_id: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    color_swatch_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    category: str = Field(default="未指定产品", min_length=1, max_length=100)
    theme: str = Field(default="", max_length=500)
    requirements: str = Field(default="", max_length=2000)
    count: int = Field(default=4, ge=1, le=30)
    budget_micros: int = Field(default=0, ge=0, le=1_000_000_000)
    source_asset_id: str | None = None
    engineering_source_hash: str | None = Field(default=None, exclude_if=(lambda value: value is None))
    engineering_operation: Literal[("LOCAL_EXPORT", "AI_CLEANUP_EXPORT")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    surface_finish: Literal[("NONE", "NATURAL_PATINA")] = "NONE"
    finish_source_asset_id: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    high_quality_scene: bool = False
    master_id: str | None = None
    style_profile_id: str | None = None
    project: str = Field(default="", max_length=80)
    width_mm: float | None = Field(default=None, gt=0, le=5000)
    thickness_mm: float | None = Field(default=None, gt=0, le=500)
    mode: Literal[("STENCIL", "CUT_ENGRAVE")] = "CUT_ENGRAVE"
    recipe: Literal[("single", "delivery")] = "single"
    scene_count: int = Field(default=2, ge=1, le=5)
    latest_feedback: bool = False
    allow_old_snapshot: bool = False
    live_authorized: bool = False
    explicit_detail: Literal[("simple", "balanced", "complex")] | None = None
    projection_width: float = Field(default=0.35, ge=0.15, le=0.6)
    camera: Literal[("front", "slight")] = "front"
    scene_mode: Literal[("GENERATE", "REFERENCE", "ORIGINAL", "AI_COMPOSE")] = "GENERATE"
    photo_style: Literal["PORTRAIT_BLACK_WHITE"] | None = None
    photo_construction: Literal[("SUBJECT_SILHOUETTE", "SHAPED_CUTOUT")] | None = Field(default=None, exclude_if=(lambda value: value is None))
    scene_reference_asset_id: str | None = None
    scene_placement: Literal[("wall", "surface", "left", "right")] = "wall"
    authorization_id: str | None = None
    policy_variant: Literal[("personal", "generic")] = "personal"
    creative_direction: Literal[("EXPLORE", "BALANCED", "FOLLOW")] = "EXPLORE"
    economy_mode: bool = False
    cost_mode: Literal[("ECONOMY", "BALANCED", "QUALITY")] = "BALANCED"
    material: str = Field(default="未指定", max_length=100)
    installation: str = Field(default="未指定", max_length=100)
    auto_repair: bool = False
    repair_policy: Literal[("OFF", "REPAIRABLE", "QUALITY")] | None = None
    brain_probe: Literal[("MINIMAL", "JSON_OBJECT", "JSON_SCHEMA", "TEXT_JSON")] | None = None
    acceptance_test: bool = False
    prior_test_cost_cny: float | None = Field(default=None, ge=0, le=5)
    cost_strategy: Literal[("EXISTING", "FREE_ONLY", "FREE_PLUS")] = "EXISTING"
    
    @model_validator(mode="before")
    @classmethod
    def task_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for public, internal in (("product_type", "category"), ("requested_count", "count")):
            if not public in value:
                continue
            elif internal in value and value[internal] != value[public]:
                raise ValueError("任务参数别名冲突：" + public)
            value[internal] = value.pop(public)
        if value.get("module") == "CREATIVE_DESIGN":
            value["module"] = "DESIGN"
        if value.get("auto_repair") == "OFF":
            value.update(auto_repair=False, repair_policy="OFF")
        return value
    
    @model_validator(mode="after")
    def normalize_repair_policy(self):
        if self.creative_entry_mode:
            if self.module != "DESIGN" or self.finish_source_asset_id:
                raise ValueError("创意入口只用于新设计")
            self.intent_source = "PRESET"
            self.workflow_mode = "PRESET"
        if self.preset_direction_id and self.preset_direction_ids:
            raise ValueError("设计方向单选与多选不能同时提交")
        
        elif self.preset_direction_ids and len(set(self.preset_direction_ids)) != len(self.preset_direction_ids):
            raise ValueError("设计方向不能重复选择")
        elif self.custom_design_directions:
            if self.module != "DESIGN" and self.intent_source != "PRESET" or self.finish_source_asset_id:
                raise ValueError("个人方向只用于新建创意设计")
            elif not self.preset_direction_ids:
                self.preset_direction_ids
            selected = []
            ids = [row.id for row in self.custom_design_directions]
            row = selected
            if len(ids) != len(set(ids)) or any((key not in selected for key in ids)):
                raise ValueError("仅提交本次已选的个人方向，不能重复或夹带未选择方向")
        elif self.surface_finish != "NONE" and self.module != "DESIGN":
            raise ValueError("表面配色仅用于设计作品")
        
        elif self.finish_source_asset_id:
            if not self.module == "DESIGN" and self.count == 1 and self.finish_source_asset_id == self.source_asset_id:
                raise ValueError("已有作品配色须绑定同一张设计稿，每次一张")
        elif self.repair_policy is None:
            self.auto_repair = self.repair_policy != "OFF"
        return self
        
        row = None

class BatchFeedbackIn(Strict):
    text: str = Field(min_length=1, max_length=1200)

class ReviewIn(Strict):
    asset_id: str
    source_hash: str; feedback_text: str | None = Field(default=None, max_length=1200)
    feedback_expires_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    engineering_visual_confirmed: bool = False
    action: Literal[("KEEP", "REJECT", "LATER")]; reason: Literal[("simpler", "richer", "dynamic", "yellow", "duplicate", "customer", "smaller", "room", "engrave", "bridge")] | None = None
    scope: Literal[("IMAGE", "PROJECT", "CATEGORY")] = "IMAGE"
    grade: Literal[("USABLE", "MINOR_DEFECT", "UNUSABLE")] | None = None
    
    @model_validator(mode="after")
    def grade_matches_action(self):
        if self.grade and {"USABLE": "KEEP", "MINOR_DEFECT": "LATER", "UNUSABLE": "REJECT"}[self.grade] != self.action:
            raise ValueError("人工评级与处理动作不一致")
        return self

class SuggestionApply(Strict):
    source_hash: str; scope: Literal[("IMAGE", "PROJECT", "CATEGORY")] = "IMAGE"

class ManualSuggestionIn(Strict):
    source_hash: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    instruction: str = Field(default="", max_length=1200)
    target_output_mode: Literal[("PRODUCT_EFFECT", "FLAT_CUT_MASTER", "ENGRAVING_MASTER")] | None = None
    ai_directed: bool = False
    operation: Literal[("MANUAL_EDIT", "COLOR_CHANGE")] = "MANUAL_EDIT"
    target_material_id: str | None = Field(default=None, max_length=80)
    color_swatch_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    scene_style_request: bool = False
    change_direction: bool = False
    scope: Literal[("IMAGE", "PROJECT", "CATEGORY")] = "IMAGE"
    
    @model_validator(mode="after")
    def instruction_or_ai(self):
        if self.operation == "COLOR_CHANGE":
            if self.ai_directed and self.scene_style_request or self.change_direction:
                raise ValueError("配色需要明确人工意见，不能同时购买建议或改变方向")
        elif self.operation != "COLOR_CHANGE":
            if self.target_material_id or self.color_swatch_asset_ids:
                raise ValueError("目标材料与色卡仅用于配色")
        elif self.change_direction:
            if self.ai_directed or self.scene_style_request:
                raise ValueError("换方向与先提供修改意见不能同时提交")
        
        elif not self.scene_style_request and self.ai_directed:
            raise ValueError("换风格须先由AI提供场景方案")
        
        if self.ai_directed and len(self.instruction.strip()) < 3:
            raise ValueError("请填写修改要求，或选择由AI提供意见")
        elif self.ai_directed and self.scope != "IMAGE":
            raise ValueError("AI修改意见仅针对选中的当前图片")
        return self

class RevisionDecisionIn(Strict):
    source_hash: str
    plan_step_id: str
    action: Literal[("ACCEPT", "ALTERNATIVE", "KEEP_ORIGINAL")]; option_id: str | None = Field(default=None, max_length=40)

class AssistantIn(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    learning_enabled: bool | None = None
    personalization_enabled: bool | None = None

class StyleIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    module: Module = "DESIGN"
    category: str = Field(default="未指定产品", min_length=1, max_length=100)
    series: str = Field(default="", max_length=100)
    detail: Literal[("simple", "balanced", "complex")] = "balanced"

class KeyIn(Strict):
    key: SecretStr = Field(min_length=15, max_length=500)

class UserIn(Strict):
    username: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=150)
    role: Literal[("employee", "admin")] = "employee"
    budget_micros: int = Field(default=0, ge=0)

class BootstrapIn(Strict):
    setup_token: SecretStr; username: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=12, max_length=150)
    learning_enabled: bool = False

class RoleRoute(Strict):
    model: str = Field(min_length=1, max_length=100)
    cap_micros: int = Field(gt=0, le=100_000_000)
    tested: bool = False

class RouteIn(Strict):
    provider: Literal[("mock", "openai")]; roles: dict[(str, RoleRoute)] = Field(default_factory=dict)
    verified: bool = False
    size: Literal[("1024x1024", "1536x1024", "1024x1536")] = "1024x1024"
    quality: Literal[("low", "medium", "high")] = "medium"
    price_checked_at: str | None = None

class SceneExecution(Strict):
    camera_angle: str | None = None
    view_distance: str | None = None
    light_direction: str | None = None
    light_height: str | None = None
    light_softness: str | None = None
    environment_brightness: str | None = None
    contact_shadow: str | None = None
    environment: str = Field(min_length=1, max_length=500)
    camera: str = Field(min_length=1, max_length=300)
    composition: str = Field(min_length=1, max_length=300)
    lighting: str = Field(min_length=1, max_length=300)
    atmosphere: str = Field(min_length=1, max_length=300)
    placement_plane: str = Field(min_length=1, max_length=200)
    source_view_compatible: bool; center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)
    width_fraction: float = Field(gt=0, le=1)
    rotation_degrees: float = Field(ge=-45, le=45)
    shadow_dx: float = Field(ge=-0.2, le=0.2)
    shadow_dy: float = Field(ge=-0.2, le=0.2)
    shadow_blur: float = Field(ge=0, le=0.1)
    shadow_opacity: float = Field(ge=0, le=0.8)

class PlanBrief(Strict):
    structure_constraints: list[str] = Field(default_factory=list, max_length=20)
    scene_scale_advice: str = Field(default="", max_length=1000)
    scene_execution: SceneExecution | None = None
    execution_prompt: str = ""
    surface_finish_prompt: str = Field(default="", max_length=1600)
    subject_analysis: str = ""
    creative_direction: str = ""
    visual_language: str = ""
    composition: str = ""
    material_expression: str = ""
    key_features: list[str] = Field(default_factory=list)
    aesthetic_goal: str = ""
    lighting: str = ""
    camera: str = ""
    environment: str = ""
    must_avoid: list[str] = Field(default_factory=list)
    revision_goal: str = ""
    output_kind: Literal[("PRODUCT_DESIGN", "BITMAP_VISUAL")] = "PRODUCT_DESIGN"
    title: str
    intent: str
    motif: str
    detail: Literal[("simple", "balanced", "complex")]
    exploration: bool
    spatial: str

class PlanOutput(Strict):
    briefs: list[PlanBrief]

class DirectPlanBrief(Strict):
    """Only the creative choice consumed by the preset-direct compiler."""
    candidate_index: int = Field(ge=1, le=30)
    execution_direction: str = Field(min_length=3, max_length=1200)

class DirectPlanOutput(Strict):
    briefs: list[DirectPlanBrief] = Field(min_length=1, max_length=30)

class VisibleSubject(Strict):
    """One independently visible source-photo subject, never an inferred identity."""
    subject_id: str = Field(pattern="^subject_[0-9]{1,2}$")
    kind: Literal[("PERSON", "PET", "OBJECT", "OTHER")]; description: str = Field(min_length=1, max_length=300)
    identity_features: list[str] = Field(default_factory=list, max_length=12)
    pose: str | None = Field(default=None, max_length=300)
    relative_position: str | None = Field(default=None, max_length=300)
    relationships: list[str] = Field(default_factory=list, max_length=12)

class Diagnosis(Strict):
    supported: bool
    observations: list[str]
    preserved_features: list[str]
    suggestions: list[Literal[("simpler", "richer", "bridge", "engrave", "smaller", "room", "texture", "likeness")]]; visible_subject_count: int | None = Field(default=None, ge=0, le=30)
    subjects: list[VisibleSubject] = Field(default_factory=list, max_length=30)
    observed_detail: Literal[("simple", "balanced", "complex")] | None = None
    warmth: Literal[(-1, 0, 1)] | None = None
    motion: Literal[(-1, 0, 1)] | None = None
    product_deformed: bool | None = None
    observed_structure: str | None = None

class FeedbackSummary(Strict):
    feature: Literal[("detail", "warmth", "projection", "spatial_variety", "engraving", "connection", "overall")]
    direction: Literal[(-1, 0, 1)]
    scope: Literal[("IMAGE", "PROJECT", "CATEGORY")]
    evidence_summary: str

class ConnectAI(Strict):
    key: SecretStr = Field(min_length=15, max_length=500)
    local_budget_micros: int | None = Field(default=None, ge=100_000, le=1_000_000_000)
    account_quota_confirmed: bool

class TestAuthorizationIn(Strict):
    quote_hash: str; maximum_micros: int = Field(default=0, ge=0, le=10_000_000)
    approved: bool
    materials_confirmed: bool

class TestOutcomeIn(Strict):
    attempt_ids: list[str] = Field(min_length=1, max_length=20)
    acknowledge_possible_charge: bool

class JobAuthorizationIn(Strict):
    confirmed_snapshot_hash: str | None = None
    job: JobIn
    approved: bool
    materials_confirmed: bool

class ModeIn(Strict):
    mode: Literal[("DEMO", "LIVE")]

class ComparisonVote(Strict):
    choice: Literal[("left", "right", "tie", "neither")]

class ScheduleIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    job: JobIn; timezone: str = "Asia/Shanghai"
    weekdays: list[int] = Field(default_factory=(lambda: [0, 1, 2, 3, 4]), min_length=1, max_length=7)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    backlog_limit: int = Field(default=60, ge=1, le=1000)
    enabled: bool = False

class RetryIn(Strict):
    acknowledge_duplicate_charge: bool = False
    recover_vision: bool = False

class EnabledIn(Strict):
    enabled: bool
