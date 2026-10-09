"""Deliver the current product decisions intact, without another model call.

Only new server-authorized scopes opt in. This is instruction provenance and
completeness, not a claim that a text plan proves cut geometry or image quality.
"""
import hashlib, json
from .errors import DomainError; CONTRACT = "PRODUCT_BRIEF_EXECUTION_V1"; STAGE_CONTRACT = "DESIGN_STAGE_EXECUTION_V2"; PHOTO_TOPOLOGY_LEGACY = "PHOTO_SUBTRACTIVE_TOPOLOGY_V1"; PHOTO_TOPOLOGY_CARRIER_PREVIEW = "PHOTO_LIGHT_CARVING_V2"; PHOTO_TOPOLOGY_CARRIER_BRANCH = "PHOTO_CARRIER_BRANCH_V3"; PHOTO_TOPOLOGY_CARRIER_RELIEF = "PHOTO_CARRIER_RELIEF_V4"; PHOTO_TOPOLOGY_CARRIER_VEINS = "PHOTO_CARRIER_VEINS_V5"; PHOTO_TOPOLOGY_SPARSE_SUPPORT = "PHOTO_SPARSE_SUPPORT_V6"; PHOTO_TOPOLOGY_CONNECTED_STEM = "PHOTO_CONNECTED_STEM_V7"; PHOTO_TOPOLOGY_CONTRACT = PHOTO_TOPOLOGY_CONNECTED_STEM; PHOTO_CARRIER_VERSIONS = (PHOTO_TOPOLOGY_CARRIER_PREVIEW, PHOTO_TOPOLOGY_CARRIER_BRANCH, PHOTO_TOPOLOGY_CARRIER_RELIEF, PHOTO_TOPOLOGY_CARRIER_VEINS, PHOTO_TOPOLOGY_SPARSE_SUPPORT, PHOTO_TOPOLOGY_CONNECTED_STEM); PHOTO_TOPOLOGY_VERSIONS = [PHOTO_TOPOLOGY_LEGACY]; INITIAL_FIELDS = ("title", "intent", "motif", "execution_prompt", "creative_direction", "visual_language", "composition", "key_features", "structure_constraints", "must_avoid", "aesthetic_goal", "spatial"); REVISION_FIELDS = ("current_subject", "execution_prompt", "revision_direction", "what_to_change", "what_to_preserve", "must_keep", "must_remove", "defect_targets", "qa_criteria")
def new_contract():
    return CONTRACT

def active(context):
    if not context.get("input"):
        context.get("input")
    request = {}
    if not context.get("product_artwork_brief"):
        context.get("product_artwork_brief")
        if not context.get("brief"):
            context.get("brief")
    brief = {}
    if context.get("product_execution_contract") == CONTRACT:
        context.get("product_execution_contract") == CONTRACT
        if request.get("module") in ("DESIGN", "PHOTO_TO_PRODUCT"):
            request.get("module") in ("DESIGN", "PHOTO_TO_PRODUCT")
            if brief.get("output_kind") != "BITMAP_VISUAL":
                brief.get("output_kind") != "BITMAP_VISUAL"
                if not context.get("surface_finish_stage"):
                    context.get("surface_finish_stage")
                if brief.get("surface_finish_stage") != "FINISH":
                    brief.get("surface_finish_stage") != "FINISH"
    return not request.get("finish_source_asset_id")

def bind_context(context, route):
    if route.get("product_brief_execution") != CONTRACT:
        return context
    result = {"product_execution_contract": CONTRACT}
    if route.get("design_stage_execution") == STAGE_CONTRACT:
        result["design_stage_execution"] = STAGE_CONTRACT
    if route.get("photo_topology_design") in PHOTO_TOPOLOGY_VERSIONS:
        result["photo_topology_design"] = route["photo_topology_design"]
    from .photo_references import binding
    if binding(route):
        result["photo_construction_references"] = binding(route)
    from .carrier_support import SUPPORTED as support_versions
    if route.get("photo_local_support") in support_versions:
        result["photo_local_support"] = route["photo_local_support"]
    
    return result

def structure_stage(context):
    if context.get("design_stage_execution") == STAGE_CONTRACT:
        context.get("design_stage_execution") == STAGE_CONTRACT
        if not context.get("input"):
            context.get("input")
        if {}.get("module") == "DESIGN":
            {}.get("module") == "DESIGN"
            if not context.get("surface_finish_stage"):
                context.get("surface_finish_stage")
                if not context.get("brief"):
                    context.get("brief")
    return {}.get("surface_finish_stage") == "STRUCTURE"

def decisions(context):
    if not context.get("product_artwork_brief"):
        context.get("product_artwork_brief")
        if not context.get("brief"):
            context.get("brief")
    brief = {}
    if not brief.get("revision_plan"):
        brief.get("revision_plan")
    revision = context.get("revision_plan"); source = brief
    
    fields = INITIAL_FIELDS
    
    decision_fields = ("execution_prompt", "key_features", "creative_direction", "composition", "visual_language")
    if not any((source.get(key) for key in decision_fields)):
        raise DomainError("INVALID_PLAN", "总控尚未提供本件产品的具体造型方案，未提交图片生成；请重新规划本次设计。", 409)
    
    result = {key: source[key] for key in fields if not source.get(key) not in (None,
    "",
    [])}; key = source
    if not structure_stage(context) and isinstance(revision, dict):
        for key, value in result.items():
            pass
        result = {key: value}
        key = key
        value = value
        if "key_features" in result:
            v = None
            result["key_features"] = [v for v in result["key_features"] if not str(v).lstrip().startswith(("DECORATIVE_DETAIL:", "DECORATIVE_DETAIL："))]
    
    return result
    
    key = None; value = None; key = None; v = None

def decision_text(context):
    return json.dumps(decisions(context), ensure_ascii=False, separators=(",", ":"))

def manifest(context, prompt):
    if len(prompt) > 32_000:
        raise DomainError("PROMPT_LIMIT", "本件执行方案超过图片接口长度，已停止提交，未截掉结构要求。请缩小本次设计范围。", 409)
    value = decision_text(context)
    return {"version": CONTRACT, "fields": list(decisions(context)), "decision_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_characters": len(prompt), "geometry_verified": False}

def planner_instruction(text, context):
    if not active(context):
        return text
    text = text.replace("Use at most 240 ASCII bytes for execution_prompt:", "Use a concise execution_prompt without dropping concrete construction decisions:"); text = text.replace("execution_prompt in concise English, at most 760 UTF-8 bytes,", "execution_prompt in concise English,")
    if context.get("photo_topology_design") in PHOTO_TOPOLOGY_VERSIONS:
        text = photo_topology_instruction(context["photo_topology_design"]) + "\n" + text
    instruction = 'PRODUCT CONSTRUCTION BEFORE RENDERING. The complete current brief fields are delivered to the image model; they are not reduced to a short execution_prompt. Make one self-contained buildable visual plan per product. In key_features describe the main retained mass, how each essential retained feature joins it through a broad natural support, and where each important opening ends without encircling or severing that feature. Choose recognizable negative shapes or omission for optional details that cannot stay attached. A rule such as "no islands" alone is not a construction decision. Work out the actual shape relationships before choosing decorative detail. Keep natural contours and attractive silhouette; do not solve support by arbitrary bars, blacked-out faces or adding an unrequested frame. Avoid conflicting cut/engrave meanings. Keep these decisions concise in creative_direction, composition, key_features and must_avoid; execution_prompt should direct their execution without duplicating the entire brief. This planned structure still needs checking on the actual output; do not claim geometry is verified. ' + text
    if context.get("photo_topology_design") in PHOTO_CARRIER_VERSIONS:
        instruction = instruction.replace("or adding an unrequested frame.", "or adding a decorative frame unrelated to the permitted shaped carrier plate.")
    if context.get("photo_topology_design") in (PHOTO_TOPOLOGY_CARRIER_VEINS, PHOTO_TOPOLOGY_SPARSE_SUPPORT, PHOTO_TOPOLOGY_CONNECTED_STEM):
        instruction = instruction.replace("broad natural support, and where each important opening", "natural support (fine integrated veins are permitted for small features), and where each important opening")
        instruction = instruction.replace("Keep natural contours and attractive silhouette; do not solve support by arbitrary bars, blacked-out faces", "Keep natural contours and attractive silhouette; use the planned leaf-vein network for small facial features, never arbitrary bars or blacked-out faces")
    from .carrier_support import SUPPORTED as support_versions, instruction as support_instruction
    
    if context.get("photo_local_support") in support_versions:
        instruction += "\n" + support_instruction()
    if context.get("photo_construction_references"):
        from .photo_references import instruction as reference_instruction
        instruction += reference_instruction()
    return instruction

def photo_topology_instruction(version=PHOTO_TOPOLOGY_LEGACY):
    if version == PHOTO_TOPOLOGY_CONNECTED_STEM:
        return connected_stem_construction()
    elif version == PHOTO_TOPOLOGY_SPARSE_SUPPORT:
        return sparse_support_construction()
    elif version == PHOTO_TOPOLOGY_CARRIER_VEINS:
        return carrier_vein_construction()
    elif version == PHOTO_TOPOLOGY_CARRIER_RELIEF:
        return photo_topology_instruction(PHOTO_TOPOLOGY_CARRIER_BRANCH) + "\n" + carrier_relief_construction()
    elif version in PHOTO_CARRIER_VERSIONS:
        instruction = "LIGHT-CARVING PORTRAIT CONSTRUCTION. Borrow the visual principle of a backlit carved leaf: a recognizable face emerges from the distribution of light openings within a continuous material body. This is opaque sheet metal, not a translucent leaf. Translate tones into a few shaped THROUGH-CUT highlight apertures and broad retained shadow planes, never gray transparency, surface etching, a printed photograph, or a stippled field of tiny holes. The leaf is an optical analogy, not a required leaf-shaped product.\nCHOOSE A BEAUTIFUL CARRIER. A shaped solid plaque is a supported product approach, not merely a frame pasted around disconnected features. For a portrait choose an elegant outer board contour suited to its pose, crop and intended placement, and compose the recognizable portrait INSIDE it. Prefer this approach when an isolated anatomical silhouette would need fragile facial bridges. The employee may still explicitly request a subject silhouette without a carrier; honor that choice. Keep generous retained plate around and between the portrait openings, integrating portrait shadows into the carrier through broad natural material regions. Do not cut a continuous white moat around the head that detaches it from the carrier. No mandatory oval, leaf, rectangle, ornate border or unrelated decoration. Plan a visually balanced mounting region in substantial retained plate; only add through mounting holes when the intended attachment calls for them, well away from portrait windows and the outer edge. Do not invent exact hole size, fastener strength or material thickness. Show the flat product alone; no hook, screw, stand or installation scene in this cut mask.\nDESIGN FOR THE VIEWING EXPERIENCE. If the employee wants a sky-facing or backlit portrait, compose the light windows so a readable face emerges when the bright background is visible through them. Record the viewing/background intention with the product decisions for downstream scene planning; do not render a sky or perspective into the structure master itself. Appearance and manufacturing are separate: a pleasing backlit view cannot excuse disconnected material.\nDESIGN THE OUTER LOAD PATH FIRST. Preserve this subject's head shape, hair sweep, pose and shoulder crop. Build the hair/temple, cheek/jaw, neck and shoulders as one substantial natural material body. For a carrier design these anatomical shadow masses also merge into the surrounding board. Describe which broad outer silhouette regions join one another and where each interior opening stops before it cuts through those joins. An outline stroke around an empty face is not a body. Do not close the entire face with a contour-following moat, hollow out the neck, or leave a large head hanging from a thin strip. Separate hair locks must merge naturally into larger hair/shoulder masses. Retain characteristic angular tips without isolated chips or needle-like strands.\nTHEN DISTRIBUTE LIGHT WITHIN THAT BODY. Choose a subject-specific shadow side and a restrained set of disjoint highlight windows that suggest forehead, eye light, cheek, nose turn and lower lip. The face is retained material with light windows, not a white face with dark facial symbols placed inside it. The mouth is a white negative opening shaped within an uninterrupted dark lower-face plane; never a separate black lip floating inside a white opening. Let cheek-to-chin shadow remain broad and continuous. Use negative eye shapes, not detached pupils or eyebrows. If a facial mark cannot remain supported, express it by the edge of a light opening or omit that minor mark. Keep recognizability through proportions, facial light placement and pose, not fine marks.\nIn the subject-specific plan state OUTER_BODY: the chosen carrier or subject silhouette and natural joined exterior masses; MOUNTING_REGION: the supported attachment approach or pending use decision, without invented dimensions; LIGHT_WINDOWS: the expressive openings and where they terminate; and FACE_SUPPORT: the broad retained routes separating those openings. For a revision put these construction choices in the current revision decisions. These are one coherent construction, not unrelated prohibitions. No arbitrary bars, repeated grid, featureless black face or thin decorative border used as a shortcut. Deliver black retained material on white air. The actual generated outer contour and interior material still require local checks; this visual plan is not proof of geometry or strength."
        if version == PHOTO_TOPOLOGY_CARRIER_BRANCH:
            instruction = instruction.replace("Prefer this approach when an isolated anatomical silhouette would need fragile facial bridges. The employee may still explicitly request a subject silhouette without a carrier; honor that choice. ", "Execute this selected carrier approach; the separate subject-silhouette branch retains the earlier workflow. ")
            instruction += "\nTHE EMPLOYEE SELECTED THE SHAPED CUTOUT BRANCH. It supports people, pets, and mixed groups. Preserve every visible requested subject, their relative positions, facing, poses and meaningful relationships; never merge two individuals into one face or silently remove a pet. Integrate their retained shadow masses into the same shaped board without arbitrary bars crossing faces. For pets use species-specific muzzle, ears and coat masses, never impose human facial features. OUTER CONTOUR IS A PRIMARY DESIGN DECISION: design its silhouette rhythm, curved/angled transitions, negative-space balance and visual center in response to the subjects inside. Choose and explain a coherent beautiful outline; do not default to an arbitrary oval or rectangle merely to achieve connectivity. Integrate any attachment area into the contour without awkward tabs. Keep enough simple substantial carrier material; ornamental complexity and a thin ring are not substitutes for a well-proportioned board. "
            instruction += "LEAF-VEIN CONNECTIONS ARE AN ALLOWED VISUAL LANGUAGE: use a few flowing main ribs with subordinate branching supports integrated into the carrier outline and portrait shadows. Their trajectories should follow hair flow, cheek/jaw planes, clothing folds or spaces between subjects, giving the composition a coherent organic rhythm. These ribs are actual retained material, not painted lines over openings. Design broad merged junctions and supported ends; do not imitate fragile botanical hairlines or add a uniform mesh. Avoid an arbitrary straight bar across eyes or mouth. Use this approach when it improves the particular composition, not as mandatory decoration for every carrier. "
        return instruction
    return "CONSTRUCTION PRIORITY: design a subtractive one-piece paper-cut stencil, not a line portrait. Recognizability is sufficient; do not preserve every facial mark. Start from a solid black head/hair/neck/torso mass and describe a SMALL NUMBER of separate white highlight apertures within it. Keep a broad black facial shadow plane continuous with the temple, cheek and jaw. Shape that plane naturally to express the face. The forehead, eye-light, cheek and chin apertures must be separated by substantial retained facial planes, never merged into a single white face field containing black eye/nose/mouth islands. Prefer eyes and mouth as recognizable white negative apertures in that retained plane; omit pupils, lashes, separate eyebrows and nostril dots when they would create loose material. Do not draw a conventional white face and try to reconnect all its small black features afterwards. The actual subject remains recognizable through outer head shape, hair sweep, eye placement, nose profile, mouth placement and pose. Select the shadow side and highlight openings for THIS subject; no fixed template or arbitrary straight bars. Deliver one finished black cutout with articulated white openings. Structure takes priority over fine likeness. This instruction is a construction strategy, not certification of the generated pixels or physical strength."

def carrier_relief_construction():
    return "BUILD A SIDE-LIT WOODCUT RELIEF, rather than outlining the face. Imagine strong raking light from one side of the subject while preserving the photographed pose. The shadow side of the face is one broad sculptural black plane joined to hair, cheek, jaw, neck and the carrier. Its silhouette contains the brow, nose turn and mouth corner. Sculpt only a few separate white highlights into this substantial plane: an eye glint, forehead light, cheek light and a short lip opening. The lit side can have a larger white cheek window whose EDGE suggests the nose and mouth; it contains no dark drawn facial symbols. At least one recognizable eye is expressed by light within shadow; omit unsupported eyelashes, pupils and secondary eye detail. Likeness comes from head shape, hair, proportions and placement of light. For several people or pets apply the same light-and-shadow construction to each requested subject, preserving their poses and relationships. The artist may simplify facial detail substantially. Draw the flowing carrier ribs as broad shadow continuations around the portrait and clothing, with generous merged junctions. The result should look like a confident cut-paper portrait with dramatic positive and negative masses. On a revision, remove the old floating facial line drawing and reconstruct that facial light pattern; adding bridges to the old lips and pupils is not the requested construction. Deliver a flat BLACK retained sheet / WHITE open-air mask, not a rendered lit object. Raking light is the design method for choosing apertures, not gray shading or a scene. Actual connectivity remains subject to the local technical gate."

def connected_stem_construction():
    return "CHOOSE A BEAUTIFUL CARRIER. BUILD THE CONNECTED STEM FIRST, THEN OPEN ITS WINDOWS. Create one recognizable portrait inside an elegant flowing shaped plate. Start with a continuous graceful BLACK support skeleton: outer carrier joins the hair/head and shoulders; a slender facial stem merges with hair or brow, follows the nose, reaches a lip contour and continues to the chin/neck. Continue the body support naturally through collar, clothing seam or another suitable contour to the lower carrier. Adapt these routes to the observed pose; do not copy another person or force frontal symmetry. The facial stem may be deliberately visible. A sparse network STILL NEEDS ITS STEM; never interpret fewer/thinner branches as no facial stem. Attach each eye/brow to the hair or facial stem, and each retained button, lettering group or clothing mark to the clothing stem or an already joined contour with one short branch. Trace every branch back to the carrier. Preserve requested recognizable features; simplify optional marks only when needed. The shape may be a flowing organic frame, a leaf or another requested attractive carrier, not a compulsory maple template. Keep ample open light between supports. After designing connectivity, give the stem and branches a restrained graceful line weight. Use blended endpoints and visible nonzero widths; do not sever joins when thinning, fill the face black, or add a dense mesh. Substantial body/frame attachments remain substantial. Write a concise executable construction in key_features: OUTER_BODY, STEM_ROUTE, BRANCH_JOINS, LIGHT_WINDOWS, MOUNTING_REGION. For a revision keep the approved likeness, gesture and unaffected contours while joining only the detached parts; thin requested heavy segments without breaking their endpoints. Deliver one flat front-view BLACK retained-material / WHITE open-air master. No gray, surface texture, shadows, hardware, background or unsolicited labels. This is a construction instruction, not a claim of verified connectivity or material strength; do not invent physical widths. Keep an undrilled mounting region unless requested otherwise."

def sparse_support_construction():
    return "CHOOSE A BEAUTIFUL CARRIER. BUILD SPARSE REAL CONNECTIONS, NOT A DENSE VEIN PATTERN. Preserve the requested people, pets or mixed subjects, their expression, pose and relationships. Compose an attractive shaped plate with open light windows and a small number of fine retained supports. Fine secondary branches are expressly allowed for small facial and clothing details. Use the FEWEST additional branches needed to join every retained feature to the main piece; already attached features need no extra decorative support. This is not a request for a grid, dense leaf skeleton, half-black face or thick bars across the features.\nPLAN EACH ACTUAL CONNECTION. Inventory the detached or potentially detached brow/eye groups, nose/lips, hair marks, sleeve folds, fingers and any other retained details. For each group choose one short, visually quiet curved route to an already connected neighbor: brow/outer eye to nearby hair or temple, inner eye to an attached nose contour, lip corner to cheek/jaw or a nearby support, sleeve marks along a fold to the sleeve edge. These are examples, not mandatory routes regardless of pose. Share a short branch where suitable; if a feature joins another feature, trace that chain all the way to the main carrier rather than leaving a connected group floating. Do not impose a face-crossing central midrib. One gentle vein may cross the face if it offers the clearest sparse connection. Keep readable eyes, gaze and smile; integrate tiny optional marks into their parent feature rather than creating more independent islands.\nDraw every support BLACK with visible nonzero width and endpoints merged into both target masses; white lines are cuts, not connectors. Keep the substantial head/body/frame connections already present. Small details may have fine branches without forcing every junction to become a thick shadow mass. On a revision retain the existing attractive outer silhouette, recognizable face, hair, pose and all good connected regions; add only the necessary local branches, not a replacement portrait or another decorative network. Stop adding ribs once all retained groups have a route to the carrier. Leave ample open light around the features.\nIn the subject-specific decisions include OUTER_BODY, CONNECTION_ROUTES (feature -> connected neighbor -> main carrier, plus already-connected features to preserve), LIGHT_WINDOWS and MOUNTING_REGION. Keep an undrilled mounting region unless holes were requested. Honor current employee instructions over older style preferences. Preserve any downstream sky/backlight intent. Deliver one front-view flat BLACK retained-material / WHITE open-air master on white with clear margins, no screenshot interface, scene, text, gray, texture or hardware. Do not invent physical widths or strength. Actual local connectivity and later vector checks remain required; a written connection plan is not a verified connected output."

def carrier_vein_construction():
    return "CHOOSE A BEAUTIFUL CARRIER. THE EMPLOYEE SELECTED THE SHAPED CUTOUT BRANCH, supporting people, pets and mixed groups without changing their count, poses or relationships. BUILD A CONNECTED LEAF-VEIN PORTRAIT. The reference principle is a luminous open face whose recognizable brow, eyes, nose and lips are physically supported by a graceful vein network inside an attractive shaped sheet. Do not force a solid black face or dramatic half-face woodcut shadow when the requested approach is delicate open leaf carving. Design the carrier silhouette, main vein, branch veins and facial features TOGETHER in the first composition. For a leaf-inspired plate let its central vein continue from the substantial base/clothing region through the portrait to the upper plate. It may cross the face as a deliberate visible botanical vein: do not prohibit this requested connection. Use gently tapering branches from that main vein or the side carrier into brow/eye groups, nose turn, lips, hair and shoulder masses. Each retained feature must merge into a traced support route to the carrier; a line that merely touches at one pixel is not joined. Fine secondary ribs are allowed, but design readable nonzero widths and blended junctions. Keep the main frame, head/hair masses, shoulders and attachment region substantial. Plan explicit routes for BOTH eye/brow groups, the nose and the lips; no floating pupil, nostril dot, lip or hair chip. Preserve expressive eye direction, nose profile and mouth placement with simplified shapes instead of deleting every facial feature. Support small features with the planned veins, not an arbitrary grid or thick horizontal bars. Cut light-filled windows BETWEEN these retained routes. White is completely open air; black is opaque retained metal, never translucent leaf skin or surface-drawn veins. Preserve subject likeness through observed head shape, hair, proportions and pose, without inventing a different face. Pets use their own species-specific anatomy. For mixed subjects integrate the network in the spaces between subjects without merging their identities. OUTER CONTOUR IS A PRIMARY DESIGN DECISION: give the plate an elegant balanced organic silhouette suited to the composition, with flowing turns and tasteful tips. A leaf is appropriate to leaf-carving intent but not a compulsory template for every future task. In the subject-specific decisions state OUTER_BODY, VEIN_ROUTES (feature-to-vein-to-carrier connections), LIGHT_WINDOWS and MOUNTING_REGION. Preserve an undrilled mounting region unless the employee requests holes; do not invent dimensions, fasteners or strength. Record any sky/backlight viewing intent for downstream scenes. Deliver just one front-view flat BLACK retained-material / WHITE open-air master with clear margins, no gray, texture, photographic background, hardware or text. Actual local connectivity and later vector checks are still required; this plan does not certify physical strength."
