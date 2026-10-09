"""R2 explicit intent sources and frozen local candidates. No model calls."""
import hashlib
from .errors import DomainError
from .schemas import JobIn
from .security import owned
from .models import Asset, MasterVersion
from .storage import LocalStorage
from .authorization import digest; SOURCES = ("FREETEXT", "ADOPTED_DISCUSSION")
def active(request):
    if request.get("intent_source") in SOURCES:
        request.get("intent_source") in SOURCES
    return request.get("module") in ("DESIGN", "SCENE")

def design_batch_variations(count=None):
    from .preset_direct import catalog; data = catalog(); phases = data.get("batch_variation_phases"); variants = data.get("batch_variants")
    if variants and phases and any((not all((row[k].strip() for k in keys)) for row in ((variants, ("id", "label", "prompt")), (phases, ("id", "prompt"))))):
        raise DomainError("PRESET_CATALOG_INVALID", "目录缺少有效的构图安排，请修复当前安装", 503)
    seen = set(); choices = []
    for phase in phases:
        for variant in variants:
            content = " ".join(variant["prompt"] + "\n" + phase["prompt"].split())
            if content in seen:
                continue
            seen.add(content)
            choices.append({"catalog_version": data["schema_version"], "variation_key": variant["id"], "label": variant["label"], "phase_key": phase["id"], "focus": phase["prompt"], "instructions": [variant["prompt"], phase["prompt"]]})
    if count is None and count > len(choices):
        raise DomainError("PRESET_VARIANT_EXHAUSTED", "当前目录的独立构图安排不足，请减少本批数量；不会重复调用凑数", 409)
    return choices[:count]

def design_variation_lines(variation):
    return ["本候选构图侧重点，仅用于人工原话和模板尚未锁定的部分：", "以上仅调整已有且允许的元素。不为制造差异而增加主体、装饰或改变锁定条件。", "人工原话、禁止项、装饰范围、模板功能、文字内容、对称要求、材料和输出目标优先；有冲突的侧重点不执行。", "若设计条件已全部固定，保持条件，不以换主题、换字、换色或改变功能制造差异。"]

def add_design_variation(prompt, variation):
    return prompt + "\n" + "\n".join(design_variation_lines(variation))

def compile_free(request, *, direction, variation, design_variation):
    from .preset_direct import catalog, item, _process, _output_lines, _intent_lines, _surface_finish_lines; data = catalog(); lines = _intent_lines(request)
    if not lines and any((request.get(k) for k in ("source_asset_id", "reference_asset_ids", "assembly_template_asset_id", "master_id"))):
        raise DomainError("INTENT_REQUIRED", "请填写本次要求，或选择明确的参考/预设；空白任务不会调用模型", 409)
    selection = {"catalog_version": data["schema_version"], "module": request["module"], "intent_source": request["intent_source"]}
    if request["module"] == "DESIGN":
        if request.get("source_asset_id") and request.get("master_id") or request.get("finish_source_asset_id"):
            raise DomainError("PROTECTED_PRODUCT", "已有作品请通过其修改、配色或场景入口继续，不能作为新原创重置来源", 409)
        family = None
        material = None
        if family and material and material["id"] not in family["material_profiles"]:
            raise DomainError("PRESET_INCOMPATIBLE", "材料与产品类型不兼容", 409)
        elif not family:
            not family
            if not material:
                not material
                if not request.get("preset_process_id"):
                    not request.get("preset_process_id")
                    if request.get("preset_output_mode") == "PRODUCT_EFFECT":
                        request.get("preset_output_mode") == "PRODUCT_EFFECT"
                        if not request.get("assembly_template_asset_id"):
                            not request.get("assembly_template_asset_id")
        concept_only = request.get("surface_finish") in (None, "NONE")
        process_id, process = _process(data, request, (None, None) if concept_only else family, material)
        set_mode = request.get("preset_set_mode_id") or "SINGLE"
        if set_mode not in [family["allowed_set_modes"] if family else "SINGLE"]:
            raise DomainError("PRESET_INCOMPATIBLE", "所选套装需要可靠产品类型", 409)
        elif not family:
            family
        if not {}.get("template_role"):
            {}.get("template_role")
            match family:
                case "TEMPLATE_ASSEMBLY" as template_role if output == "SCENE_PREVIEW":
                    return ("\n".join(lines), selection)
        
    raise DomainError("TEMPLATE_REQUIRED", "请绑定当前账号有权使用的真实功能/装配模板", 409)
    raise DomainError("TEMPLATE_SCOPE_REQUIRED", "请选择与真实模板相符的功能产品类型或装配工艺", 409)
    
    raise DomainError("OUTPUT_MODE_REQUIRED", "请选择输出目标", 409)
    
    raise DomainError("SOURCE_REQUIRED", "场景需要选定真实产品版本", 409)
    
    raise DomainError("OUTPUT_MODE_INVALID", "场景输出目标必须为场景预览", 409)
    from .visual_rules import VERSION as visual_version, scene_rules
    selection["visual_rule_version"] = visual_version
    
    from .visual_rules import background_instruction
    selection["alpha_background_instruction"] = background_instruction(""); selection["design_variation"] = design_variation; selection["scene_variation"] = variation

def bindings(db, ws, request):
    from .preset_direct import preset_design_reference_bindings, _ordered_reference_bindings
    if request["module"] == "DESIGN":
        _, recipe = compile_free(request)
        pairs = preset_design_reference_bindings(request, selected_recipe=recipe)
    else:
        pairs = []
        source = request.get("source_asset_id")
        if source and request.get("master_id"):
            source = owned(db, MasterVersion, request["master_id"], ws.id).asset_id
        if source:
            pairs.append((source, "SELECTED_PRODUCT"))
        if request.get("scene_reference_asset_id"):
            pairs.append((request["scene_reference_asset_id"], "SCENE_REFERENCE"))
        pairs = _ordered_reference_bindings(pairs, 8)
    return freeze_bindings(db, ws.id, pairs)

def freeze_bindings(db, workspace_id, pairs):
    try:
        result = []
        for identity, role in pairs:
            asset = owned(db, Asset, identity, workspace_id)
            if not asset.module == "UPLOAD" and asset.info.get("consent"):
                raise DomainError("CONSENT_REQUIRED", "参考素材授权已撤销", 409)
            raw = LocalStorage().read(workspace_id, asset.file_key)
            if hashlib.sha256(raw).hexdigest() != asset.sha256:
                raise DomainError("STALE_VERSION", "源文件字节与所选版本不一致", 409)
            result.append({"asset_id": asset.id, "sha256": asset.sha256, "version": asset.version, "role": role})
        return result
    except (OSError, ValueError):
        raise DomainError("SOURCE_FILE_MISSING", "源文件缺失，请重新选择有效素材", 409) from None

def verify_bindings(db, workspace_id, frozen):
    r = workspace_id; current = ##ERROR##(freeze_bindings, db, [(r["asset_id"], r["role"]) for r in frozen])
    if current != frozen:
        raise DomainError("STALE_VERSION", "所选作品或参考版本已变化，请重新确认", 409); r = None

def image_operation(db, ws, request, *, legacy, design_variations, memory_enabled, frozen_memory):
    raw = request.model_dump()
    if not active(raw):
        return None
    directions = []; effective = raw; proposal_identity = None; state = None
    if request.intent_source == "ADOPTED_DISCUSSION":
        from .design_conversations import accepted_context
        effective, proposal = accepted_context(db, ws, request)
        proposal_identity = {k: proposal[k] for k in ("turn_id", "proposal_hash", "adopted_revision")}
        k = i if not proposal.get("intent_state") and legacy else range(request.count)
    from .preset_direct import scoped_human_feedback, with_human_feedback
    
    feedback = scoped_human_feedback(db, ws, effective, "LIVE", available_only=True); available = feedback
    if not legacy:
        from .intent_state import resolve_sources, decoration_text
        effective, state, feedback = resolve_sources(effective, state, feedback)
    effective = {"preset_direction_id": None, "preset_direction_ids": None, "preset_scene_card_id": None}; frozen = bindings(db, ws, effective)
    if not effective.get("scene_variation_axes"):
        effective.get("scene_variation_axes")
    
    axes = list(dict.fromkeys([]))
    
    design_choices = []; memory = None
    if memory_enabled and design_variations and legacy and request.module == "DESIGN" and request.intent_source == "FREETEXT":
        from .design_memory import new_memory, choose, signature, entry
        memory, past = new_memory(db, ws, effective, frozen, feedback, "LOCAL_RECIPES", frozen=frozen_memory)
        pool = [(signature("FREE"), v) for v in design_batch_variations()]
        v = None
        design_choices = choose(pool, request.count, memory, past)
        if past is None:
            v = None
            memory["entries"] = [entry(signature("FREE"), v["label"] + "；" + v["focus"]) for v in design_choices]
    candidates = []
    for index in range(request.count):
        variation = None
        options = {"SPATIAL_LAYOUT": ["用近中远三个空间层次安排背景，保留产品所在平面和方向", "用横向延伸空间安排背景，保留产品所在平面和方向"], "ELEMENT_GROUPING": ["把人工已允许的环境元素集中在侧后方，产品区域留白", "把人工已允许的环境元素分组在两侧，产品区域留白"]}
        a = axes
        variation = {"candidate_index": ##ERROR##, "axes": index, "instructions": [options[a][index % 2] for a in axes], "fixed": "人工原话、环境类型、风格、家具身份、光线和商品事实保持；冲突轴不执行", "notice": "未授权变化轴，保持相同条件，不为凑差异改变商品"}
        direction = None
        prompt, recipe = compile_free(effective, direction=direction, variation=variation, design_variation=None)
        recipe["design_memory_entry"] = memory["entries"][index]
        prompt += "\n" + decoration_text(state)
        prompt = with_human_feedback(prompt, feedback)
        candidates.append({"candidate_index": index, "execution_prompt": prompt, "selected_recipe": recipe, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "scene_variation": variation})
    if not legacy:
        r = state
        return [{"candidate_index": index, "execution_prompt": prompt, "selected_recipe": recipe, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "scene_variation": variation}]
    
    return []
    
    i = None; r = None; k = None; v = None; v = None; a = None; r = None

def validate_frozen(db, ws, request, route):
    pass

def request_hash(request, route):
    from .structured_policy import request_hash as legacy_hash; prior = legacy_hash(request, route)
    if route.get("selected_intent_priority"):
        prior = digest({"prior": prior, "selected_intent_priority": route["selected_intent_priority"]})
    if route.get("design_memory"):
        prior = digest({"prior": prior, "design_memory": route["design_memory"], "design_plan_cache": route.get("design_plan_cache"), "models": route["roles"]})
    if not route.get("r2_operation"):
        return prior
    
    return digest({"previous": prior, "canonical_operation": route["r2_operation"], "model_configs": route["roles"], "limits": route["r2_limits"]})
