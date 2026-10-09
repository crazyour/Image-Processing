"""Local photo contracts and candidate-scoped two-pass boundary; no model calls."""
import copy, hashlib, re
from sqlalchemy import select
from .errors import DomainError
from .models import Asset, Job, Step, ProviderAttempt
from .security import owned
from .storage import LocalStorage, safe_image
from .preset_direct import VERSION, compile_prompt, frozen_photo_contract, _ordered_reference_bindings, _intent_lines

def read_binding(db, workspace_id, binding):
    asset = owned(db, Asset, binding["asset_id"], workspace_id)
    if asset.sha256 != binding["sha256"] or asset.version != binding["version"]:
        raise DomainError("STALE_VERSION", "照片链的冻结素材版本已变化，请重新选择", 409)
    elif not asset.module == "UPLOAD" and asset.info.get("consent"):
        raise DomainError("CONSENT_REQUIRED", "照片或样板使用授权已撤销", 409)
    try:
        raw = LocalStorage().read(workspace_id, asset.file_key)
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise DomainError("STALE_VERSION", "照片链文件内容与冻结版本不一致", 409)
        safe_image(raw)
        return raw
    except OSError:
        raise DomainError("REFERENCE_FILE_MISSING", "照片链引用文件缺失，已有作品仍保留", 409) from None

def freeze_bindings(db, ws, pairs):
    rows = []
    for identity, role in _ordered_reference_bindings(pairs, 8):
        asset = owned(db, Asset, identity, ws.id)
        row = {"asset_id": asset.id, "role": role, "version": asset.version, "sha256": asset.sha256}
        read_binding(db, ws.id, row)
        rows.append(row)
    return rows

def lineage(db, ws, asset):
    passes = 0; seen = set(); node = asset
    while node.module != "UPLOAD":
        if node.id in seen:
            raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "照片版本来源关系异常，保留作品等待确认", 409)
        seen.add(node.id)
        job = None
        step = None
        if node.info.get("local_vector") == "LOCAL_VECTOR_V2":
            from .local_vector import trusted_job
            if not job and step and trusted_job(db, job) and step.result.get("asset_id") != node.id or node.parent_id:
                raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "本地派生来源无法核实，不能重置照片次数", 409)
            node = owned(db, Asset, node.parent_id, ws.id)
            continue
        elif job and step and job.snapshot.get("workflow_version") != VERSION and step.kind != "GENERATE" and step.result.get("asset_id") != node.id or step.result.get("model_calls") != 1:
            raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "旧作品缺少可核实的照片处理次数，保留作品等待人工确认", 409)
        passes += 1
        root = node.id
        if not node.parent_id:
            original_id = node.input_asset_id
            break
        node = owned(db, Asset, node.parent_id, ws.id)
    original_id = node.id; original = None
    
    if original and original.module != "UPLOAD" or asset.input_asset_id != original.id:
        raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "无法确认原始照片，不能把生成稿当作原照", 409)
    return {"root_asset_id": root, "original_asset_id": original.id, "used_passes": passes}

def require_slot(db, ws, asset, exclude_job_id=None):
    info = lineage(db, ws, asset)
    if info["used_passes"] >= 2:
        raise DomainError("PHOTO_PASS_LIMIT", "该候选已用完两次生成式处理，原稿保留；不能继续创建第三次", 409)
    for job in db.scalars(select(Job).where(Job.workspace_id == ws.id)):
        if job.id == exclude_job_id:
            continue
        elif job.snapshot.get("purpose") == "DISCUSSION_V1":
            from .models import CallAuthorization
            grant = db.get(CallAuthorization, job.snapshot.get("input", {}).get("authorization_id"))
            discussion_steps = list(db.scalars(select(Step).where(Step.job_id == job.id)))
            limits = {}
            if grant and grant.workspace_id == ws.id and grant.plan.get("root_job_id") == job.id and grant.plan.get("purpose") == "DISCUSSION_V1" and limits.get("planning") == 1 and all((limits.get(k) == 0 for k in ("image_generation", "image_edit", "vision", "quality", "feedback"))) and len(discussion_steps) == 1 or discussion_steps[0].kind == "DISCUSS":
                continue
        from .local_vector import trusted_job
        if trusted_job(db, job):
            continue
        if not job.snapshot.get("photo_direct", {}).get("lineage"):
            job.snapshot.get("photo_direct", {}).get("lineage")
        reservation = {}
        if not reservation.get("root_asset_id") == info["root_asset_id"]:
            if not job.source_asset_id == info["root_asset_id"] or job.module in ("PHOTO_TO_PRODUCT", "DESIGN", "BASIC_DXF"):
                continue
        steps = list(db.scalars(select(Step).where(Step.job_id == job.id)))
        attempts = list(db.scalars(select(ProviderAttempt).join(Step).where(Step.job_id == job.id, ProviderAttempt.capability.in_(["image_generation", "image_edit"]))))
        definitely_unsent = all(({}.get("request_delivery") == "NOT_SENT" for a in attempts))
        delivered = any((s.result.get("model_calls") for s in steps))
        if not job.status in ("FAILED", "CANCELED") and definitely_unsent or delivered:
            continue
        raise DomainError("PHOTO_PASS_RESERVED", "当前候选的第二次处理已占用或结果待确认，请查看原任务；不会重新提交", 409)
    return {"source_asset_id": asset.id, "pass_number": info["used_passes"] + 1}

def guard_new_source(db, ws, request, *, local):
    if not request.get("source_asset_id"):
        request.get("source_asset_id")
    identity = request.get("finish_source_asset_id")
    if identity and local or request.get("module") == "SCENE":
        return None
    asset = owned(db, Asset, identity, ws.id)
    if asset.module == "PHOTO_TO_PRODUCT" or asset.info.get("photo_lineage"):
        require_slot(db, ws, asset)
        raise DomainError("PHOTO_REVISION_ENTRY", "请从当前照片作品发起人工修改或表示转换，不能另建任务重置处理次数", 409)

def initial(db, ws, request):
    source = owned(db, Asset, request.get("source_asset_id"), ws.id)
    if source.module != "UPLOAD":
        guard_new_source(db, ws, request)
        raise DomainError("PHOTO_SOURCE_REQUIRED", "独立照片创作需要选择原始照片", 409)
    prompt, recipe = compile_prompt(request); contract = frozen_photo_contract(request); pairs = [(source.id,
    "SOURCE_PHOTO")]; x = pairs; pairs = ##ERROR## += [(x, "CONNECTION_STYLE_REFERENCE") for x in request.get("reference_asset_ids", [])]
    
    x = pairs; pairs = ##ERROR## += [(x, "TARGET_COLOR_SWATCH") for x in request.get("color_swatch_asset_ids", [])]
    return {"prompt": prompt, "recipe": recipe, "contract": contract, "bindings": freeze_bindings(db, ws, pairs), "lineage": None}
    
    x = None; x = None

def _legacy_intent_matches(prompt, intent):
    terminator = "只输出本次要求的图像，不附分析、说明、尺寸、标题、标志或未请求文字。"; lines = prompt.split("\n")
    if lines.count(terminator) != 1:
        return False
    body = lines[:lines.index(terminator)]; labels = ("本次主题：", "本次人工意图：")
    for i, line in enumerate(body):
        for label in labels:
            pass
    fields = [(i, label)]; line = []; i = line; label = i; expected = _intent_lines(intent)
    for key, label in zip(("theme", "requirements"), labels):
        pass
    expected_labels = [label]; key = key; label = label
    for _, label in fields:
        pass
    label = label; _ = _
    if [label] != expected_labels:
        return False
    elif not not fields:
        not fields
    
    return "\n".join(body[fields[0][0]:]) == "\n".join(expected)
    
    label = None; line = None; i = None; label = None; key = None; label = None; _ = None

def original_intent(db, ws, asset, contract):
    if "original_intent" in contract:
        intent = copy.deepcopy(contract["original_intent"])
    else:
        job = owned(db, Job, asset.job_id, ws.id)
        if not job.snapshot.get("input"):
            job.snapshot.get("input")
        request = {}
        if not asset.info.get("brief"):
            asset.info.get("brief")
        prompt = {}.get("execution_prompt", "")
        if not job.parent_id and asset.parent_id and job.source_asset_id != asset.input_asset_id and all((key in request for key in ("theme", "requirements"))) and prompt:
            raise DomainError("PHOTO_INTENT_UNCONFIRMED", "无法核实当前候选的原始人工要求，请先确认来源；已有作品保留", 409)
        intent = {key: (request[key] or "").strip() for key in ("theme", "requirements")}
        key = request
        if not _legacy_intent_matches(prompt, intent):
            raise DomainError("PHOTO_INTENT_UNCONFIRMED", "原任务输入与候选冻结提示词的完整字段不符或边界不明，不能猜测原始要求", 409)
    _intent_lines(intent)
    return intent
    
    key = None

def inherited_intent_lines(intent, converted):
    _intent_lines(intent); lines = []; representation = re.compile("(?:请|必须|务必|只要|仅需|输出|生成|制作|呈现|显示|采用|使用|保持|保留|为|一张|的|应|要|是|成)*(?:彩色(?:材质|材料)?效果(?:图)?|(?:材质|材料)效果图|黑白(?:切割)?(?:主稿|稿|图)|平面切割主稿|外切内雕主稿)(?:表示|输出|即可)?")
    for key, label in (("theme", "原始主题"), ("requirements", "原始人工要求")):
        value = intent.get(key, "")
        if converted:
            parts = re.split("([；;。\\n])", value)
            value = "".join((part + "" for i, part in enumerate(parts))).strip("；;。\n ")
        if not value:
            continue
        lines.append(f"{label}（仅继承仍有效的内容与保护条件）：{value}")
    if lines:
        lines.insert(0, "继承当前候选的原始人工要求；本次人工修改只覆盖明确涉及的对应部分，未涉及的要求继续保留，不能借此改变冻结工艺、主体或其他产品事实。")
        if converted:
            lines.insert(1, "本次仅转换输出表示，以当前目标表示为准；原要求中的旧显示方式不再执行，仍保留产品内容、定制文字和禁止项，不改变加工工艺或产品几何。")
    return lines

def revision(db, ws, asset, suggestion):
    used = require_slot(db, ws, asset)
    if suggestion.kind != "manual" or suggestion.info.get("ai_directed"):
        raise DomainError("PHOTO_MANUAL_REQUIRED", "请明确填写本次修改要求，照片不调用规划或自动修改", 409)
    change = (suggestion.info.get("human_feedback_original") or "").strip()
    if not change:
        raise DomainError("REVISION_INSTRUCTION_REQUIRED", "请填写本次修改要求", 409)
    
    contract = copy.deepcopy(asset.info.get("photo_contract"))
    
    recipe = copy.deepcopy(asset.info.get("selected_recipe"))
    if not contract and recipe and all((k in recipe for k in ("process", "material", "output", "photo_construction"))):
        raise DomainError("PHOTO_CONTRACT_UNCONFIRMED", "当前候选缺少冻结工艺或输出要求，保留作品等待确认", 409)
    
    target = suggestion.info.get("target_output_mode") or recipe["output"]
    if target not in contract["edit_prompts"]:
        raise DomainError("PHOTO_OUTPUT_INCOMPATIBLE", "该候选未支持此表示转换，请保留原工艺并选择可用输出", 409)
    intent = original_intent(db, ws, asset, contract); intent_lines = inherited_intent_lines(intent, target != recipe["output"])
    contract["original_intent"] = intent; recipe["output"] = target
    
    original_binding = next((r for r in asset.info.get("photo_bindings", [])), None)
    if not original_binding:
        raise DomainError("PHOTO_LINEAGE_UNCONFIRMED", "原始照片版本未冻结，保留作品等待确认", 409)
    prior_bindings = asset.info["photo_bindings"]; rows = freeze_bindings(db, ws, [(asset.id,
    "CURRENT_PRODUCT")])
    
    rows += [{"role": "ORIGINAL_IDENTITY_REFERENCE"}]
    
    r = rows; rows = used += [copy.deepcopy(r) for r in prior_bindings if r["role"] in ("CONNECTION_STYLE_REFERENCE", "TARGET_COLOR_SWATCH")]
    for row in rows:
        read_binding(db, ws.id, row)
    if len(rows) > 8:
        raise DomainError("REFERENCE_LIMIT", "当前接口最多接收8张参考图", 409)
    return {"prompt": "\n".join([contract["edit_prompts"][target],
    
    "本次人工修改：" + change]), "recipe": recipe, "contract": contract, "bindings": rows, "lineage": used}
    recipe
    r = None

def apply_recipe(request, bundle):
    recipe = bundle["recipe"]
    return {"preset_process_id": recipe["process"], "preset_material_id": recipe["material"], "preset_output_mode": recipe["output"], "photo_construction": recipe["photo_construction"]}
