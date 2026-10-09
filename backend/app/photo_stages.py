"""Two photo-design stages on the existing queue and provider checkpoints.

No new provider, schema dialect, budget or image transport. Intermediate images
are immutable assets, never deliverables or evidence of a passed product QA.
"""
import copy, hashlib, time
from sqlalchemy import select
from .errors import DomainError
from .models import Asset, Step, uid
from .security import owned
from .source_profiles import identity_instruction
from .manufacturing_awareness import PHOTO_IDENTITY_POLICY, PHOTO_CUT_PREVIEW_CONTRACT; CONNECTED_PRODUCT_WORKFLOW = "CONNECTED_PRODUCT_V1"; CONNECTED_CUT_PREVIEW = "BLACK_RETAINED_WHITE_AIR_V1"
def effective_request(snapshot):
    if not snapshot.get("route"):
        snapshot.get("route")
    route = {}; request = snapshot["input"]
    if request.get("module") == "PHOTO_TO_PRODUCT" and route.get("photo_product_workflow") == CONNECTED_PRODUCT_WORKFLOW and route.get("photo_cut_preview") == CONNECTED_CUT_PREVIEW:
        return {"mode": "STENCIL"}
    
    return request

def bind_preview_context(context, route):
    if route.get("photo_product_workflow") != CONNECTED_PRODUCT_WORKFLOW or route.get("photo_cut_preview") != CONNECTED_CUT_PREVIEW:
        return context
    
    return {"input": {"mode": "STENCIL"}, "photo_cut_preview_contract": {"version": CONNECTED_CUT_PREVIEW, "main_preview_mode": "STENCIL", "black": "CONNECTED_RETAINED_MATERIAL", "white": "REMOVED_AIR", "engraving": "OPTIONAL_SEPARATE_FROM_MAIN_CUT_PREVIEW", "source_analysis_authority": "IDENTITY_POSE_AND_RELATIONSHIPS_ONLY"}}

def preview_contract_instruction(context):
    if not context.get("photo_cut_preview_contract"):
        context.get("photo_cut_preview_contract")
    if {}.get("version") != CONNECTED_CUT_PREVIEW:
        return ""
    return "CURRENT APPROVED MAIN CUT CONTRACT: STENCIL, black is retained sheet material and white is removed air. This contract overrides historical CUT_ENGRAVE defaults and earlier process suggestions. Use saved source_analysis only for visible subject identity, pose, facing and relationships; old claims that facial, neck or clothing white fields must remain solid, or that detached facial marks can be engraving inside the main preview, are not process authority. Build broad naturally joined black masses and supported recognizable features; do not make an engraving portrait. A requested engraving detail stays an optional separate process proposal and must never substitute for connected retained material, fill a white opening, or leave detached marks in this main cut preview. During revision reuse the current product artwork and saved identity observations; correct the defective connections without buying another identity analysis or an initial portrait. "

def white_preview(data):
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as source:
        rgba = source.convert("RGBA")
    if rgba.getextrema()[3] == (255, 255):
        None(None, None)
        return data
    canvas = Image.new("RGBA", rgba.size, "white"); canvas.alpha_composite(rgba); output = io.BytesIO()
    
    canvas.convert("RGB").save(output, format="PNG")
    
    None(None, None)
    return output.getvalue()

def connected_product_prompt(context):
    from .product_execution import active
    if active(context):
        return connected_product_execution_prompt(context)
    elif not context.get("product_artwork_brief"):
        context.get("product_artwork_brief")
        if not context.get("brief"):
            context.get("brief")
    brief = {}
    if not brief.get("revision_plan"):
        brief.get("revision_plan")
    if not {}.get("execution_prompt"):
        {}.get("execution_prompt")
    direction = brief.get("execution_prompt") or ""; editing = context.get("photo_current_product_bound"); target = "Transform the actual subject(s) in image 1, the ORIGINAL PHOTO, directly into a single-piece flat metal cutting design. Keep the visible subject count, pose, facing direction, relationships and crop. "
    if editing:
        return preview_contract_instruction(context) + target + "\nART DIRECTOR CORRECTIONS: " + direction + "\nConnected retained material is the first requirement; recognizable subject is sufficient. Build one substantial continuous BLACK silhouette first, then carve a restrained set of meaningful WHITE openings. Black is retained sheet metal; white is empty air, not a surface to color. Every black feature must belong to the main piece. Join eyes through brow/temple, noses and mouths through cheek/jaw shadows, and heads through broad natural neck/body masses. Never use hairline rails, point contacts, artificial crossbars or blacked-out faces. Simplify fragmented hair, tiny spots and textures into fewer broad flowing masses and clean negative spaces. Optional isolated markings may be removed; preserve identifying eyes/nose and major anatomical parts by natural connections instead of deleting them. Do not require exact coat spots, individual hairs or photographic matching. When only a small optional island remains, remove or naturally join that local island only; do not redesign the whole subject. Shorten long cutouts that nearly sever a head, neck or other substantial part. Keep tasteful natural fur tips, not a blanket rounded silhouette. No engraving-like detached marks in the cut preview. Do not invent unseen anatomy, frames, stands, text or a scene. Pure black on opaque white, complete silhouette with clear margins, no gray shading, color, texture, shadows or relief. " + ""
    
    return ##ERROR## + "\nRecognition anchors only: " + identity_instruction(context["identity_profile"])

def connected_product_execution_prompt(context):
    from .product_execution import decision_text, PHOTO_CARRIER_VERSIONS, PHOTO_TOPOLOGY_VERSIONS, PHOTO_TOPOLOGY_CARRIER_RELIEF, PHOTO_TOPOLOGY_CARRIER_VEINS, PHOTO_TOPOLOGY_SPARSE_SUPPORT, PHOTO_TOPOLOGY_CONNECTED_STEM, photo_topology_instruction
    if not context.get("product_artwork_brief"):
        context.get("product_artwork_brief")
        if not context.get("brief"):
            context.get("brief")
    brief = {}; editing = context.get("photo_current_product_bound"); target = "Transform the actual subject(s) in image 1, the ORIGINAL PHOTO, into one flat single-piece metal cutting product. Preserve the visible subject count, pose, facing direction, relationships and crop. "
    if context.get("photo_construction_references"):
        from .photo_references import instruction as reference_instruction
        target += reference_instruction()
    
    selected_anchors = [str(value) for value in brief.get("key_features", []) if str(value).strip().replace("：", ":", 1).startswith("IDENTITY_ANCHOR:")]; value = None; identity = "\nRecognition observations only; the product construction decisions govern simplification: " + "" if editing else identity_instruction(context["\nUse the selected IDENTITY_ANCHOR translations in the decisions above for recognition. Do not restore small source spots, hairs or marks that this product plan omits." if selected_anchors else "identity_profile"])
    if context.get("photo_topology_design") in PHOTO_TOPOLOGY_VERSIONS:
        if context["photo_topology_design"] in (PHOTO_TOPOLOGY_CARRIER_RELIEF, PHOTO_TOPOLOGY_CARRIER_VEINS, PHOTO_TOPOLOGY_SPARSE_SUPPORT, PHOTO_TOPOLOGY_CONNECTED_STEM):
            from .carrier_support import SUPPORTED as support_versions, instruction as support_instruction
            if context.get("photo_local_support") in support_versions:
                return target + "\nSubject-specific product decisions: " + decision_text(context) + "\nCONSTRUCT THE FINAL IMAGE AS FOLLOWS: " + _compact_carrier_construction(context["photo_topology_design"]) + "\nPreserve the planned attractive carrier outline, broad exterior supports and undrilled mounting region unless the employee explicitly requested otherwise. One complete front-view product alone with clear white margins. No surface color, hardware, text, perspective or scene." + identity + "\n" + support_instruction()
            return ##ERROR## + ""
        ending = "\nFINAL DELIVERABLE: a single solid black cut-paper stencil with separate white apertures, on plain opaque white. Black means retained material, white means air. No color, gray shading, engraved detail, texture, cast shadow, lettering, frame or invented anatomy. Preserve the main silhouette and pose; simplify fine facial detail in favor of one connected piece. Never leave floating dark facial features in a broad white face opening."
        return target + "\n" + preview_contract_instruction(context) + "\n" + photo_topology_instruction(context["photo_topology_design"]) + "\nCurrent subject-specific decisions: " + decision_text(context) + ending + identity
    
    return target + "\nEXECUTE THESE PRODUCT CONSTRUCTION DECISIONS: " + decision_text(context) + "\n" + preview_contract_instruction(context) + "Build the planned substantial connected BLACK retained material first, then carve the planned WHITE air openings. Execute the specified natural support paths and opening layout, not just the words single-piece or no islands. Source skin, fur and clothing colors are identity observations, not a map of what to cut away. A broad photographed light face must not become one white void surrounding detached eyes, nose and lips. Express these features with the planned connected brow/temple, nose/cheek and lip/chin masses and separated white openings. Black retained face material must still have recognizable facial articulation; never erase all features into a featureless black face or use artificial crossbars. Keep the head and other substantial parts broadly supported through natural contours; no hairline rails, point contacts or long cuts nearly severing the neck. Preserve expressive natural hair or fur tips, without needle-thin details or blanket smoothing. Every black feature in the main preview must belong to the same retained piece. Optional engraving remains a separate proposal, never detached marks in this cut preview. Do not invent unseen anatomy, frames, stands, lettering or a scene. Pure black on opaque white with clear margins; no gray shading, colors, surface texture, shadows or relief. " + identity
    
    value = None

def _compact_carrier_construction(version):
    from .product_execution import PHOTO_TOPOLOGY_CARRIER_RELIEF, carrier_relief_construction, photo_topology_instruction
    if version == PHOTO_TOPOLOGY_CARRIER_RELIEF:
        return carrier_relief_construction()
    
    return photo_topology_instruction(version)

def repair_route(qa):
    c = checks; checks = {c["requirement_id"]: c["status"] for c in qa.get("constraint_checks", [])}
    for k, v in checks.items():
        pass
    failed = {k}; k = k; v = v
    
    if (failed & {"relationship", "pose_fidelity", "source_likeness", "subject_identity", "body_relationship", "photo_subject_pose", "photo_subject_count", "identity_resemblance", "pose_and_relationship", "photo_subject_identity", "photo_subject_relationship", "original_subject_resemblance"} or any((i["code"] in ("wrong_subject", "identity_loss", "pose_changed") for i in qa.get("issues", [])))) and checks.get("original_subject_resemblance") != "PASS":
        return "IDENTITY_FAIL"
    other_core = ("original_subject_resemblance", "pose_and_relationship", "artistic_quality", "structural_awareness", "detail_density")
    
    if failed == {"black_white_compliance"} and all((checks.get(k) == "PASS" for k in other_core)) and all((i.get("severity") == "LOW" for i in qa.get("issues", []))):
        return "COLOR_ONLY_FAILURE"
    elif checks.get("detail_density") == "VIOLATION":
        return "HATCHING_FAILURE"
    elif checks.get("structural_awareness") == "VIOLATION":
        return "STRUCTURAL_FAILURE"
    
    return "PRODUCT_ARTWORK_EDIT"; c = None; v = None; k = None

def stage_prompt(context):
    stage = context["photo_stage"]
    if stage == CONNECTED_PRODUCT_WORKFLOW:
        return connected_product_prompt(context)
    profile = context["identity_profile"]; identity = identity_instruction(profile)
    if stage == "STAGE_1_IDENTITY_DESIGN":
        if not context.get("brief"):
            context.get("brief")
            if not context.get("product_artwork_brief"):
                context.get("product_artwork_brief")
        brief = {}
        if not brief.get("revision_plan"):
            brief.get("revision_plan")
        if not {}.get("execution_prompt"):
            {}.get("execution_prompt")
        direction = brief.get("execution_prompt") or ""
        return "Create an isolated recognizable identity study of the individual(s) in image 1, on plain white. Keep the overall pose, silhouette, head/ears, main facial/body marking relationships and tail shape. Select 1-3 distinguishing anchors, allow artistic simplification of hairs, tiny spots and shadows. This is product-level recognition, not photographic or pixel replication. Do not invent a new pose. Keep image 1 framing and viewer-left/right direction. A head/shoulder photo stays a head/shoulder portrait; never invent unseen torso, legs, paws, tail or a ground stand. Apply the art director corrections to framing and identity. Natural color, gray shading and detail are allowed. This is an intermediate identity study, not the final black-white product; do not impose a stencil, cartoon or generic breed template. Source identity, viewer left/right: " + identity + "\nArt director corrections: " + direction
    route = context.get("photo_repair_route", "PRODUCT_ARTWORK_EDIT")
    
    isolated_edit = context.get("photo_single_product_reference", False); target_number = 3
    if route == "COLOR_ONLY_FAILURE":
        return "COLOR_STYLE_EDIT. Image 1 is the original identity, image 2 the identity master, image 3 the current product artwork. " + f"Edit ONLY the color treatment of image {target_number} into neutral black/white and neutral antialiasing. Lock all contours, positions, silhouette, pose, negative spaces and facial features. Do not redraw, simplify, add or remove details. No chromatic pixels. "
    brief = context["product_artwork_brief"]
    
    if not brief.get("revision_plan"):
        brief.get("revision_plan")
    revision = {}
    if not revision.get("execution_prompt"):
        revision.get("execution_prompt")
    direction = brief.get("execution_prompt") or ""
    
    repair = {"HATCHING_FAILURE": "Replace the entire hatching vocabulary with broad joined shadow masses and clean negative shapes. ", "STRUCTURAL_FAILURE": "Reorganize unsupported details through natural fur/shadow connections and fewer larger openings. "}.get(route, ""); target = "IMAGE_EDIT: transform the individual in image 2 into a refined flat black-white laser-cut-aware product artwork. "
    if context.get("photo_current_product_bound"):
        return target + "\nART DIRECTOR EDITS: " + direction + "\n" + repair + PHOTO_CUT_PREVIEW_CONTRACT + "PURE BLACK AND WHITE on white. Structure comes first: merge unsupported shapes with natural broad fur/shadow masses; no thin bars or blacked-out facial features. No dense hatching, gradients, chromatic colors or individual hair strokes. Keep the entire designed silhouette within a clear white margin; finish the shoulder crop as a broad connected contour. Preserve recognizable head/ears, main marking relationships, pose and facing direction from image 1; never invent unseen anatomy. Keep unaffected shapes; change defective geometry as needed to actually complete the corrections. " + ""
    
    return ##ERROR## + "\nIdentity observations (not output colors): " + identity

def prepare_product(worker, step_id, token, context, source):
    with worker.sessions.begin() as db:
        step = db.get(Step, step_id)
        job, _ = worker._live_state(db, step)
    if step.lease_token != token:
        return None
    
    original = owned(db, Asset, source.input_asset_id or source.id, job.workspace_id)
    if not context.get("source_analysis"):
        context.get("source_analysis")
    if not {}.get("identity_profile"):
        {}.get("identity_profile")
    
    profile = context["brief"].get("identity_profile")
    if not profile:
        raise DomainError("PHOTO_IDENTITY_PROFILE_MISSING", "缺少原照识别结果，已停止购买图片")
    if job.snapshot["route"].get("photo_product_workflow") == CONNECTED_PRODUCT_WORKFLOW:
        editing = source.module == "PHOTO_TO_PRODUCT"
        target = original
        data = worker.storage.read(job.workspace_id, target.file_key)
        if hashlib.sha256(data).hexdigest() != target.sha256:
            raise DomainError("STALE_VERSION", "照片或当前产品稿文件已改变")
        role = "SOURCE_PHOTO"
        preview = white_preview(data)
        context.update(photo_stage=CONNECTED_PRODUCT_WORKFLOW, photo_current_product_bound=editing, identity_profile=copy.deepcopy(profile), product_artwork_brief=copy.deepcopy(context["brief"]), capability="image_edit", image_reference_bindings={target.id: "image 1 / " + role})
        from .product_execution import bind_context
        context.update(bind_context(context, job.snapshot["route"]))
        lineage = {"version": 2, "stage": CONNECTED_PRODUCT_WORKFLOW, "identity_policy": PHOTO_IDENTITY_POLICY, "source_asset_id": original.id, "source_hash": original.sha256, "current_product_asset_id": None, "current_product_hash": None, "identity_profile": copy.deepcopy(profile), "product_artwork_brief": copy.deepcopy(context["brief"]), "edit_reference_policy": "SOURCE_PHOTO_ONLY", "analysis_copy_hash": hashlib.sha256(preview).hexdigest(), "analysis_background": "white"}
        if job.snapshot["route"].get("photo_cut_preview") == CONNECTED_CUT_PREVIEW:
            lineage["cut_preview_contract"] = CONNECTED_CUT_PREVIEW
        from .product_execution import active, CONTRACT
        if active(context):
            lineage["product_execution_contract"] = CONTRACT
        references = [preview]
        from .photo_references import append_images
        examples = append_images(worker, db, job.workspace_id, job.snapshot["route"], references, context["image_reference_bindings"])
        if examples:
            lineage["construction_examples"] = examples
            lineage["edit_reference_policy"] += "_WITH_CONSTRUCTION_EXAMPLES"
        checks(None, None, None)
        return (references, lineage)
    elif not source.info.get("qa_result"):
        source.info.get("qa_result")
    route = "PRODUCT_ARTWORK_EDIT"; master_id = step.payload.get("photo_identity_master_id")
    if master_id and route != "IDENTITY_FAIL":
        if not source.info.get("photo_pipeline"):
            source.info.get("photo_pipeline")
        master_id = {}.get("identity_master_id")
    if not master_id:
        stage_id = step.payload.get("photo_identity_step_id")
        if not stage_id:
            stage_id = uid()
            db.add(Step(id=stage_id, workspace_id=job.workspace_id, job_id=job.id, ordinal=1000 + (step.ordinal), kind="PHOTO_IDENTITY", status="QUEUED", payload={"product_step_id": step.id, "source_asset_id": original.id, "source_hash": original.sha256, "identity_profile": copy.deepcopy(profile), "source_analysis": copy.deepcopy(context.get("source_analysis")), "brief": copy.deepcopy(context["brief"]), "index": context["index"]}))
        step.payload = {"photo_identity_step_id": stage_id, "photo_repair_route": route}
        step.status = "QUEUED"
        step.lease_token = None
        job.status = "QUEUED"
        None(None, None)
        return None
    master = owned(db, Asset, master_id, job.workspace_id)
    
    if master.info.get("artifact_role") != "IDENTITY_MASTER" or master.input_asset_id != original.id:
        raise DomainError("PHOTO_IDENTITY_MASTER_MISMATCH", "主体中间稿与当前原照不匹配")
    
    refs = [worker.storage.read(job.workspace_id, original.file_key),
        
        worker.storage.read(job.workspace_id, master.file_key)]
    
    if hashlib.sha256(refs[0]).hexdigest() != original.sha256 or hashlib.sha256(refs[1]).hexdigest() != master.sha256:
        raise DomainError("STALE_VERSION", "照片或主体中间稿文件已改变")
    
    bindings = {original.id: "image 1 / SOURCE_PHOTO",
        master.id: "image 2 / IDENTITY_MASTER"}
    
    current_product_bound = source.module == "PHOTO_TO_PRODUCT" and route in ("COLOR_ONLY_FAILURE", "STRUCTURAL_FAILURE", "PRODUCT_ARTWORK_EDIT")
    if current_product_bound:
        current_pixels = worker.storage.read(job.workspace_id, source.file_key)
        if hashlib.sha256(current_pixels).hexdigest() != source.sha256:
            raise DomainError("STALE_VERSION", "当前产品稿文件已改变")
        refs.append(current_pixels)
        bindings[source.id] = "image 3 / CURRENT_PRODUCT"
    if not source.info.get("qa_result"):
        source.info.get("qa_result")
    
    c = None; checks = {c["requirement_id"]: c["status"] for c in {}.get("constraint_checks", [])}
    if current_product_bound:
        current_product_bound
    single_product_reference = all((checks.get(k) == "PASS" for k in ("original_subject_resemblance", "pose_and_relationship")))
    if single_product_reference:
        refs = [current_pixels]
        bindings = {source.id: "image 1 / CURRENT_PRODUCT"}
    context.update(photo_stage="STAGE_2_PRODUCT_ARTWORK", photo_repair_route=route, photo_current_product_bound=current_product_bound, photo_single_product_reference=single_product_reference, identity_profile=copy.deepcopy(profile), product_artwork_brief=copy.deepcopy(context["brief"]), capability="image_edit", image_reference_bindings=bindings)
    
    lineage = {"version": 1, "identity_policy": PHOTO_IDENTITY_POLICY, "stage": "STAGE_2_PRODUCT_ARTWORK", "source_asset_id": original.id, "source_hash": original.sha256, "identity_master_id": master.id, "identity_master_hash": master.sha256, "repair_route": route, "identity_profile": copy.deepcopy(profile), "product_artwork_brief": copy.deepcopy(context["brief"]), "current_product_asset_id": None, "current_product_hash": None, "edit_reference_policy": "IDENTITY_REFERENCES"}
    
    step.payload = {"photo_identity_master_id": master.id, "photo_repair_route": route}
    
    None(None, None)
    return (refs, lineage)
    
    c = None

def generate_identity(worker, step_id, token):
    snapshot, payload, job_id, workspace_id = worker._context(step_id)
    with worker.sessions() as db:
        source = owned(db, Asset, payload["source_asset_id"], workspace_id)
        content = worker.storage.read(workspace_id, source.file_key)
        if hashlib.sha256(content).hexdigest() != payload["source_hash"]:
            raise DomainError("STALE_VERSION", "原照已变化")
    None(None, None)
    while 1:
        context = {"input": snapshot["input"], "brief": payload["brief"], "index": payload["index"], "source_analysis": payload.get("source_analysis"), "identity_profile": payload["identity_profile"], "photo_stage": "STAGE_1_IDENTITY_DESIGN", "capability": "image_edit", "image_reference_bindings": {source.id: "image 1 / SOURCE_PHOTO"}}
        result = worker.call(step_id, "image", context, content)
        key, digest = worker.write_artifact(workspace_id, result)
        with worker.sessions.begin() as db:
            step = db.get(Step, step_id)
            job, _ = worker._live_state(db, step)
        if step.lease_token != token:
            worker.discard_unpublished()
            return None
        master = db.scalar(select(Asset).where(Asset.step_id == step.id))
        if not master:
            master = Asset(workspace_id=workspace_id, job_id=job_id, step_id=step.id, input_asset_id=source.id, module="PHOTO_TO_PRODUCT", category=source.category, state="HISTORY", file_key=key, sha256=digest, info={"artifact_role": "IDENTITY_MASTER", "employee_deliverable": False, "mock": snapshot["route"]["provider"] == "mock", "brief": {"title": "原照主体中间稿"}, "identity_profile": payload["identity_profile"], "source_analysis": payload.get("source_analysis"), "quality_validation": "NOT_RUN", "raw_key": key, "raw_hash": digest})
            db.add(master)
            db.flush()
        step.result = {"asset_id": master.id, "finished_at": time.time()}
        step.status = "DONE"
        parent = owned(db, Step, payload["product_step_id"], workspace_id)
        parent.payload = {"photo_identity_master_id": master.id}
        job.status = "QUEUED"
        None(None, None)
        worker.pending_files = []
    
    worker.pending_files = []
