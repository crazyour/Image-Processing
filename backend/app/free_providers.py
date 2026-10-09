"""Shared image prompt compilation; this module contains no provider adapters."""
import json

def image_prompt(context):
    if context.get("preset_direct_prompt"):
        return context["preset_direct_prompt"]
    from .task_intent import image_instruction
    from .product_execution import structure_stage; prompt = _image_prompt(context) + image_instruction(context)
    if structure_stage(context):
        prompt += "\nCURRENT EXECUTION STAGE: STRUCTURE ONLY. This call returns exactly one flat binary cutting master: uniform solid black retained sheet, pure white openings and background. The original request describes the eventual finished delivery; its color, patina, metal texture, lighting and scene requests belong to later stages and MUST NOT be rendered in this image. Ignore reference surface color; use only its applicable shape information. No gray, gradients, highlights, shadows, copper, turquoise or colored material. Preserve every planned opening and broad natural connection; do not black-fill openings. Output this black-white structure now. The system applies the separately saved finish plan afterward."
    return prompt

def _image_prompt(context):
    match execution:
        case "SCENE" as scene:
            return "Create one empty scene background. The locked product will be composited locally. Do not draw a product, duplicate silhouette, hand, sign or text. Reserve the selected placement area. Follow this exact environment/camera/composition/light/atmosphere and placement plan: " + json.dumps(scene, ensure_ascii=False, separators=(",", ":")) + image_art_direction(context, background_only=True)
        case "SCENE":
            return "Edit image 1, the CURRENT FINISHED SCENE. Image 2 is the ORIGINAL PRODUCT photograph, for product fidelity only; its hand and background are not product features. Apply the specified local correction to image 1, preserving its otherwise approved environment. Keep exactly one same product, its orientation, silhouette, holes, rust texture and proportions. Remove unwanted hands/mask residue by reconstructing the local wall and natural contact shadow. Do not replace the product with a new design or invent hidden features. This is a FINAL SCENE, not an empty background plate; the expected product must remain. No collage or text. " + json.dumps({}, ensure_ascii=False)
        case "PHOTO_TO_PRODUCT":
            return stage_prompt(context)
        case _:
            return (execution or "Redesign the original subject as connected black-white product art on white.") + "\nOriginal identity (viewer left/right): " + identity + "\nFINAL PRODUCT CONTRACT (overrides illustration/linework wording above): " + PHOTO_ART_INSTRUCTION
        case "DESIGN" as reference_role if brief.get("design_intent") == "REFERENCE_TO_INNOVATION":
            return "Make exactly the one product specified by the following current art-director decisions. Execute the concrete silhouette, supports and opening endpoints together; do not replace them with a generic illustration. " + reference_role + "\nCURRENT PRODUCT DECISIONS: " + decision_text(context) + "\n" + laser_art_instruction(context) + "\nOne isolated product, complete readable contour on plain white, no room, props, text or montage. For a black-white cutting master, black is retained sheet and white is removed air. Surface finish belongs to the separate finish stage; never paint holes or fill planned openings."
        case _:
            return "" + "" + "\nOne isolated product, complete readable contour on plain white, no room, props, text or montage. For a black-white cutting master, black is retained sheet and white is removed air. Surface finish belongs to the separate finish stage; never paint holes or fill planned openings."
        case "DESIGN" as presentation:
            return execution + "\n" + laser_art_instruction(context) + presentation
        case _:
            return "" + "" + presentation
        case _:
            request = context["input"]; brief = context.get("brief", {})
            if request["module"] == "DESIGN" and context.get("surface_finish_stage") == "FINISH":
                return "Apply the following whole-piece metal surface finish to the exact product in image 1: " + str(brief.get("surface_finish_prompt") or "") + "\nRetain the exact front-view silhouette, dimensions in the canvas, all openings and natural connections. Coat only retained metal. Holes and outside space are air, never colored material. Use continuous chemical patina and natural variegation across the whole sheet, not tiny individually painted features, precise color borders, added ornaments, lettering, scene or backdrop. One product alone, same composition and camera, transparent empty background."
            elif not brief.get("revision_plan"):
                brief.get("revision_plan")
            revision = context.get("revision_plan")
            execution = revision.get("execution_prompt") if isinstance(revision, dict) else brief.get("execution_prompt")
            if request["module"] == "SCENE" and context.get("scene_direct_reference_edit"):
                from .schemas import SceneExecution
                from .scene_quality import image_art_direction
                scene = SceneExecution.model_validate(context["scene_execution"]).model_dump()
                return "Create one COMPLETE photorealistic product scene using image 1, the ORIGINAL PRODUCT PHOTOGRAPH. This is a direct reference edit, not an empty background plate or a pasted cutout. Keep the exact visible product identity, orientation, silhouette, openings, part count, proportions, markings and existing material/patina. Do not redesign, mirror, recolor or invent unseen surfaces. Remove the original background, hands and photographic cast shadows; these are not product parts. Preserve real hooks, supports and fine product features. Openings show the new environment, not filled metal. Integrate consistent perspective, natural reflected light, contact shadows and plausible mounting. Exactly one original product, no duplicate, collage, captions or watermark. Use confirmed size only when supplied; recommendations are not measurements and must not appear as text. Execute this full scene plan: " + json.dumps(scene, ensure_ascii=False, separators=(",", ":")) + "\nArt direction: " + (execution or "") + image_art_direction(context) + "\nFinal output MUST contain the original product; any empty-background wording above is inapplicable."
            from .schemas import SceneExecution
            from .scene_quality import image_art_direction
            if not context.get("source_analysis"):
                context.get("source_analysis")
            profile = {}.get("identity_profile")
            from .photo_stages import stage_prompt
            from .source_profiles import identity_instruction
            from .manufacturing_awareness import PHOTO_ART_INSTRUCTION; identity = "Use the attached original photo: preserve each visible individual, asymmetric patches, pose and relationships."
            from .product_execution import active, decision_text
            from .manufacturing_awareness import laser_art_instruction, laser_design
            if execution:
                from .manufacturing_awareness import laser_art_instruction, laser_design
            revision = brief.get("revision_plan") or context.get("revision_plan")
            for ##ERROR## in brief.items():
                pass
        case "DESIGN" as k if k != "revision_plan":
            target = {k: v}; k = None; v = {}
            return "制作当前选中的一个设计。以下AI总控修订方案是本次唯一执行方案，优先于旧稿外观与旧描述。逐项落实修改，保留明确受保护的特征。参考图片只提供方案所需信息，不能把待移除的背景、道具或错误形态带入新图。修改要求：" + json.dumps(revision.get("what_to_change", []), ensure_ascii=False) + "；保留要求：" + json.dumps(revision.get("what_to_preserve", []), ensure_ascii=False) + "；成图标准：" + json.dumps(revision.get("qa_criteria", []), ensure_ascii=False) + "；总控完整修订决策：" + json.dumps(revision, ensure_ascii=False) + "；当前单张完整设计方案：" + json.dumps(target, ensure_ascii=False)
    
    match execution:
        case "SCENE" as k if request.get("scene_mode") == "AI_COMPOSE":
            return "以第一张参考图中的真实产品为主体，生成一张完整、专业、美观的场景效果图。由AI艺术总控的具体方案决定适合产品的环境、构图、机位、镜头、光影、氛围和意境；不要套用固定房间模板。保留产品可见外形、比例、镂空、材质、颜色、标识和细节；不把原背景或持物的手当产品，不编造不可见结构。产品与环境必须有一致的透视、接触阴影、光线方向与自然反射，产品清楚可辨、摆放合理。附有第二张图时它是待修改效果图；依据AI意见修改，产品事实仍以第一张为准。只输出一个连续场景，无拼图、提案文字或水印。AI方案与检查意见：" + {k: context.get(k) for ##ERROR## in ("brief", "source_analysis", "suggestion")}(json.dumps, ensure_ascii=False) + "员工可选意图：" + str("")
        case "BITMAP_VISUAL" as title:
            return prompt
    
    design_intent = brief.get("design_intent", "IDEA_TO_DESIGN")
    
    from .brain import candidate_request; instruction += "这次是已选原图的局部修正。严格保留原图主要外形、构图、镂空和未涉及材质，只修改指定问题，不重新设计整件产品。"
    
    v = {k: context[k] for ##ERROR## in ("brief", "suggestion", "source_analysis", "room", "rules")}; k = instruction += "上一版未通过视觉检查。根据附带的真实检查观察重新生成一张新图，必须纠正主体或用途偏差；不得沿用失败版的错误主体和错误构图，其他明确正确的任务约束继续保持。"
    instruction += "原图没有可信物理比例，不得在图中编造毫米尺寸；本次只生成未定比例母稿。"
    
    k = "黑色为连续保留材料，白色为真正镂空；内部镂空也必须通过桥位与主体保持单片连接。" += "只用一个闭合外轮廓定义切割，适合切穿的少量闭合孔可以保留；其余面部、毛发或纹理改为简洁雕刻线。"; k = candidate_request(request, brief)
