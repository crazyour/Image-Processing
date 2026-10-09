"""Local, versioned intent provenance. This module does not interpret arbitrary prose."""
import copy, hashlib, json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .errors import DomainError; VERSION = "R2_INTENT_V2"; Scope = Literal[("SUBJECT", "DECORATION", "LAYOUT", "ENVIRONMENT", "PRODUCT_GEOMETRY", "MATERIAL_COLOR", "OUTPUT")]
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class PreferenceSelection(Strict):
    evidence_id: str = Field(min_length=1, max_length=160)
    text_sha256: str = Field(min_length=64, max_length=64)

class IntentControls(Strict):
    version: Literal["R2_INTENT_V2"] = VERSION
    use_theme: bool = False
    decoration_mode: Literal[("UNSPECIFIED", "NONE", "ALLOWLIST", "OPEN")] = "UNSPECIFIED"
    allowed_elements: list[str] = Field(default_factory=list, max_length=24)
    confirmed_preferences: list[PreferenceSelection] = Field(default_factory=list, max_length=2)
    
    @model_validator(mode="after")
    def valid_decoration(self):
        if not self.decoration_mode == "ALLOWLIST" and self.allowed_elements:
            raise ValueError("只允许指定元素时，请填写允许的元素")
        if self.decoration_mode != "ALLOWLIST" and self.allowed_elements:
            raise ValueError("只有指定元素模式可提交元素清单")
        elif len(set(self.allowed_elements)) != len(self.allowed_elements) or any((lambda .0: try:
    for s in .0:
        if not not s.strip():
            not s.strip()
        yield len(s) > 120
    return None; except:
    pass), self.allowed_elements()):
            raise ValueError("允许元素必须非空、不重复且不超过120字")
        return self

class IntentOrigin(Strict):
    kind: Literal[("USER_FORM", "USER_TURN", "ADOPTED_PROPOSAL", "EXPLICIT_PRESET", "FUNCTIONAL_TEMPLATE", "PERSONAL_PREFERENCE")]; source_id: str = Field(min_length=1, max_length=160)
    source_revision: int | None = Field(default=None, ge=1)
    evidence_quote: str | None = Field(default=None, max_length=2000)

class IntentClause(Strict):
    id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    scope: Scope
    origin: IntentOrigin

class SourceDecision(Strict):
    source_id: str = Field(min_length=1, max_length=160)
    kind: Literal[("THEME", "DIRECTION", "SCENE_CARD", "PREFERENCE", "PROPOSAL")]
    disposition: Literal[("ACTIVE", "EXCLUDED", "NEEDS_CONFIRMATION")]
    reason: Literal[("EXPLICIT_COMPATIBLE", "EXPLICIT_USER_PRIORITY", "INACTIVE_INPUT_SOURCE", "SUPERSEDED", "CONFLICT_WITH_USER", "NOT_ADOPTED", "REVOKED", "UNRESOLVED")]
    
    @model_validator(mode="after")
    def lifecycle(self):
        if self.disposition == "ACTIVE" != self.reason in ("EXPLICIT_COMPATIBLE", "EXPLICIT_USER_PRIORITY"):
            raise ValueError("只有明确适用来源可以启用")
        elif self.disposition == "NEEDS_CONFIRMATION" != self.reason == "UNRESOLVED":
            raise ValueError("未知兼容性必须明确待确认")
        return self

class Decoration(Strict):
    mode: Literal[("UNSPECIFIED", "NONE", "ALLOWLIST", "OPEN")]; allowed_elements: list[str] = Field(max_length=24)
    origin: IntentOrigin | None
    
    @model_validator(mode="after")
    def valid(self):
        IntentControls(decoration_mode=self.mode, allowed_elements=self.allowed_elements)
        if self.mode != "UNSPECIFIED" and self.origin is not None:
            raise ValueError("明确装饰范围需要来源")
        return self

class IntentState(Strict):
    schema_version: Literal["R2_INTENT_V2"] = VERSION
    draft_revision: int = Field(ge=1)
    current_requirements: str = Field(max_length=2000)
    user_requirements: list[IntentClause] = Field(max_length=32)
    adopted_suggestions: list[IntentClause] = Field(max_length=12)
    decoration: Decoration; source_decisions: list[SourceDecision] = Field(max_length=64)
    superseded_clause_ids: list[str] = Field(max_length=64)
    
    @model_validator(mode="after")
    def unique(self):
        ids = [c.id for c in (self.user_requirements) + (self.adopted_suggestions)]; c = None
        if len(ids) != len(set(ids)) or set(ids) & set(self.superseded_clause_ids):
            raise ValueError("有效和已替代条件不能重复")
        elif ##ERROR## != "\n".join((lambda .0: try:
    for c in .0:
        yield c.text
    return None; except:
    pass), self.user_requirements()):
            raise ValueError("有效原话快照不一致")
        return self
        
        c = None

class ProposedExtraction(Strict):
    text: str = Field(min_length=1, max_length=2000)
    scope: Scope; source_id: str = Field(min_length=1, max_length=160)
    evidence_quote: str = Field(min_length=1, max_length=2000)

class IntentPatch(Strict):
    remove_clause_ids: list[str] = Field(default_factory=list, max_length=32)
    extracted_requirements: list[ProposedExtraction] = Field(default_factory=list, max_length=32)

def clause(kind, source_id, revision, text, scope="SUBJECT"):
    return {"id": source_id + ":" + digest(text)[:12], "text": text, "scope": scope, "origin": {"kind": kind, "source_id": source_id, "source_revision": revision, "evidence_quote": None}}

def normalize(state):
    state = copy.deepcopy(state)
    state["current_requirements"] = "\n".join((c["text"] for c in state["user_requirements"]))
    
    try:
        return IntentState.model_validate(state).model_dump()
    except ValueError as error:
        raise DomainError("INTENT_STATE_INVALID", "有效要求过长或来源不一致；请明确精简当前条件，不会静默截断", 409) from error

def initial(request, revision=1, source_id="current-form"):
    if not request.get("intent_controls"):
        request.get("intent_controls")
    controls = IntentControls.model_validate({}); text = (request.get("requirements") or "").strip(); state = {"schema_version": VERSION, "draft_revision": revision, "current_requirements": text, "user_requirements": [], "adopted_suggestions": [], "decoration": {"mode": controls.decoration_mode, "allowed_elements": controls.allowed_elements, "origin": None}, "source_decisions": [], "superseded_clause_ids": []}
    return normalize(state)

def edit_form(previous, request, revision, source_id, removed_ids=null):
    current = initial(request, revision, source_id)
    if not previous:
        return current
    previous = normalize(previous); old_form = [c for c in previous["user_requirements"] if not c["origin"]["kind"] == "USER_FORM"]; c = None
    if "\n".join((c["text"] for c in old_form)) == current["current_requirements"]:
        current["user_requirements"] = copy.deepcopy(old_form)
    removable = {c["id"] for c in previous["user_requirements"] + previous["adopted_suggestions"]}; c = None
    if not set(removed_ids) <= removable:
        raise DomainError("INTENT_SOURCE_INVALID", "要移除的条件不属于当前版本", 409)
    
    c = None
    current["user_requirements"] = [c for c in current["user_requirements"] if not c["id"] not in removed_ids]
    
    c = current["user_requirements"]
    ##ERROR##[current] = "user_requirements" += [c for c in previous["user_requirements"] if c["id"] not in removed_ids]
    
    active = {c["id"] for c in current["user_requirements"]}; c = None
    
    c = previous["superseded_clause_ids"]
    current["superseded_clause_ids"] = ##ERROR##(list((dict.fromkeys) + [c["id"] for c in previous["user_requirements"] + previous["adopted_suggestions"] if not c["id"] not in active])); return normalize(current)
    
    c = None; c = None; c = None; c = None
    
    c = None; c = None

def submitted_turn(state, turn_id, text, revision):
    state = copy.deepcopy(state)
    state["draft_revision"] = revision
    
    state["user_requirements"].append(clause("USER_TURN", turn_id, revision, text.strip()))
    return normalize(state)

def validate_patch(patch, state, human_sources):
    if not patch:
        patch
    patch = IntentPatch.model_validate({}); current = {c["id"]: c for c in state["user_requirements"] + state["adopted_suggestions"]}; c = None
    raise DomainError("INTENT_SOURCE_INVALID", "拟议删除未命中当前有效条件", 409)
    for ##ERROR## in patch.extracted_requirements:
        raise DomainError("INTENT_EVIDENCE_INVALID", "拟议条件没有命中本会话已存人工原话，不能作为用户事实", 409)
    c = human_sources.get(item.source_id)

def preview_patch(state, patch, human_sources, revision):
    patch = validate_patch(patch, state, human_sources); next_state = copy.deepcopy(state)
    next_state["draft_revision"] = revision
    
    c = item
    next_state["user_requirements"] = [c for c in next_state["user_requirements"] if not c["id"] not in patch.remove_clause_ids]
    
    c = None
    next_state["adopted_suggestions"] = [c for c in next_state["adopted_suggestions"] if not c["id"] not in patch.remove_clause_ids]
    
    for item in patch.extracted_requirements:
        source = next((c for c in state["user_requirements"]), None)
        if not source:
            raise DomainError("INTENT_SOURCE_INVALID", "条件来源已被替代，请重新读取", 409)
        extracted = clause(source["origin"]["kind"], item.source_id, source["origin"]["source_revision"], item.text, item.scope)
        c = extracted["id"]
        if not ##ERROR## not in {c["id"] for c in next_state["user_requirements"]}:
            continue
        next_state["user_requirements"].append(extracted)
    
    active = {c["id"] for c in next_state["user_requirements"] + next_state["adopted_suggestions"]}; c = None
    
    i = None
    next_state["superseded_clause_ids"] = [i for i in dict.fromkeys(next_state["superseded_clause_ids"] + (patch.remove_clause_ids)) if not i not in active]; return normalize(next_state)
    
    c = None; c = None; c = None; c = None; i = None

def resolve_sources(request, state=None, feedback=null, *, direction_priority):
    pass

def decoration_text(state):
    decoration = state["decoration"]; mode = decoration["mode"]
    return {"NONE": "人工明确范围：不增加装饰元素；功能底座、插槽、孔位及已确认尺寸保留。", "UNSPECIFIED": "装饰未指定，不把缺省当成额外装饰授权；按当前人工要求及已采用集合执行。", "OPEN": "人工明确允许在当前任务与功能条件内发挥装饰创意。", "ALLOWLIST": "人工只允许这些装饰元素：" + "、".join(decoration["allowed_elements"]) + "；不增加清单外元素。"}[mode]
