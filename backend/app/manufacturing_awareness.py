"""Shared visual design intent; engineering facts remain the vector engine's job."""
from pydantic import Field
from .schemas import Strict

class ProductStructureProfile(Strict):
    applicable: bool
    intent_basis: str
    outer_boundary: str; internal_openings: list[str] = Field(default_factory=list, max_length=12)
    connected_regions: list[str] = Field(default_factory=list, max_length=12)
    visual_bridges: list[str] = Field(default_factory=list, max_length=12)
    isolated_details: list[str] = Field(default_factory=list, max_length=12)
    fine_details: list[str] = Field(default_factory=list, max_length=12)
    structural_art: list[str] = Field(default_factory=list, max_length=12)
    surface_detail: list[str] = Field(default_factory=list, max_length=12)
    decorative_detail: list[str] = Field(default_factory=list, max_length=12)
    likely_cut_features: list[str] = Field(default_factory=list, max_length=12)
    likely_engrave_features: list[str] = Field(default_factory=list, max_length=12)
    structural_risks: list[str] = Field(default_factory=list, max_length=12)
    orientation: str; part_count: int | None = Field(default=None, ge=1)

DESIGN_INSTRUCTION = "For a physical cutting product, STRUCTURAL_VIABILITY_FIRST is the highest priority, ahead of likeness, detail and aesthetics. Retained material must form a viable supported structure; detached essential features cannot pass because they look good. Connectivity alone is insufficient: heads and other substantial parts need broad natural supports, not hairline necks or point contacts. Preserve natural fur outlines and tasteful angular tips; do not deliberately round or smooth every feature. Balance aesthetics and identity, revising obvious fragile or needle-like hazards only. Preserve recognizable identity and art quality within that requirement. Avoid floating material and over-fragmentation. Observed disconnection is a design violation even without dimensions; unknown measured strength remains unverified. Integrate connections naturally into fur, shadow, pattern, contour or negative space; no arbitrary face bars, props or blacked-out features. Decide from THIS image which features are likely CUT, ENGRAVE or decorative. Do not assume all white regions are through-cuts, all fine lines are engraving, or a fixed eye/nose bridge. Unknown thickness, kerf or bridge width is not a design failure. Ordinary graphics and scene backgrounds have no cutting constraints. "; LASER_ART_INSTRUCTION = "For laser-cut metal silhouettes, pierced wood, wall art or stakes, design LASER_CUT_AWARE_ARTWORK. STRUCTURAL_ART is the retained material: silhouette, main masses, limbs, tail and major openings. SURFACE_DETAIL is optional: whiskers, fine fur, small eye/nose texture; simplify, omit or propose ENGRAVE, never default through-cut. Use broad black/white masses, natural fur contours, continuous shadows and few meaningful negative spaces. No dense hatching, crosshatching, hairline shading, micro-holes, floating scraps or visibly fragile joins as the main language. Virtually remove planned cutouts: retained material and identifying features must stay supported. Repair by natural fur/shadow connections, regrouped negative space and merged fragments; no arbitrary face bars, props, fused eyes/mouth or blacked-out faces. Preserve individual likeness, pose and art quality. These are visual product-design checks, not measured DXF or material-strength certification. "; PHOTO_IDENTITY_POLICY = "PRODUCT_LEVEL_RECOGNIZABILITY_1.0.10"; PHOTO_IDENTITY_INSTRUCTION = "For PHOTO_TO_PRODUCT the identity standard is PRODUCT_LEVEL_RECOGNIZABILITY, ARTISTIC_SIMPLIFICATION and MANUFACTURING_AWARENESS, never pixel identity or photographic replication. Keep the recognizable overall pose, silhouette, head/ears, main facial and body marking relationships and tail. Select 1-3 distinctive IDENTITY_ANCHORS from the actual individual, not every spot. Translate color patches into broad black-white negative shapes, connected shadows or LIKELY_ENGRAVE features. Simplify hairs, tiny spots, texture and shadows proactively. Stable connected product structure takes precedence over likeness, detail and aesthetics; find structurally viable artistic substitutes for key identifying features. A recognizable artistic translation is PASS, including altered small patch boundaries and approximate proportions. Do not fail it for missing individual hairs, literal source colors or exact pixel/spot matching. Wrong subjects, lost major identity anchors or substantially changed pose/relationships still require repair. "; PHOTO_CUT_PREVIEW_CONTRACT = "The PHOTO_TO_PRODUCT main preview is one through-cut piece: black is retained material, white is removed space. Every black region must join the main material by a broad natural contour or shadow connection. A head joined to the body by a tiny neck is a structural failure even if all black pixels connect. Shorten or reorganize long cutouts that almost sever the neck; keep broad natural material on both sides of important connections. Keep expressive natural fur tips and angular contours where appropriate; do not force blanket smoothing or rounding. Correct obvious weak needle-like details without erasing the pet silhouette. An enclosed WHITE hole is removed scrap and is allowed; it is not a floating retained part. A BLACK island surrounded by white is unsupported and must be connected or redesigned. Keep optional engraving detail in the plan, not as detached marks in this main cut preview. "; PHOTO_ART_INSTRUCTION = "PHOTO_TO_PRODUCT always outputs LASER_CUT_AWARE_ARTWORK, not a portrait illustration. Use pure BLACK and WHITE only; source color names describe identity, never output hue. Redesign patch boundaries as black/white masses: no colored eyes/nose, gray wash, desaturation or threshold-filter substitute. " + PHOTO_CUT_PREVIEW_CONTRACT + LASER_ART_INSTRUCTION + PHOTO_IDENTITY_INSTRUCTION; PHOTO_PRODUCT_CHECKS = (("original_subject_resemblance", "ORIGINAL_SUBJECT_RESEMBLANCE", "整体能认出原主体；依据主要轮廓、头耳和1～3个个体辨识点评审，允许黑白块面或雕刻转译，不逐斑点、逐毛发或像素比较"), ("pose_and_relationship", "POSE_AND_RELATIONSHIP", "整体姿态、朝向、体型、尾巴及多主体互动关系相近；允许产品化概括比例和细节，不允许更换姿态或丢失主体关系"), ("artistic_quality", "ARTISTIC_QUALITY", "黑白块面、自然轮廓和负形美观；不能用横杆、黑脸或粘成一团的五官换取连接"), ("black_white_compliance", "BLACK_WHITE_COMPLIANCE", "真正纯黑与留白，无橙色、绿色、彩色眼鼻或灰度照片效果；允许正常边缘抗锯齿"), ("structural_awareness", "STRUCTURAL_AWARENESS", "本主预览黑色保留、白色切除；检查 FLOATING_ISLAND / DISCONNECTED_MATERIAL / MICRO_FRAGMENT / THIN_BRIDGE / FEATURE_DROP_OUT。像素相连不等于结构合理：头颈和大块材料不得只靠细颈或点接触，长镂空不得几乎切断主体。允许自然毛发轮廓和有美感的尖角，不要求刻意磨圆或全面平滑；只修正明显细弱或针刺状危险细节，兼顾美观和辨识度。封闭白孔是正常废料；悬空黑块才需连接。说明具体位置与自然修复，不凭未知尺寸判定毫米强度"), ("detail_density", "DETAIL_DENSITY", "OVER_DENSE_LINEWORK：禁止密集斜线、交叉排线、超细毛发或大量短线作为主体表达；不能仅贴雕刻标签就通过，主设计应为较大连续块面及少量有意义的细节"))
def laser_design(context):
    if not context.get("input"):
        context.get("input")
    module = {}.get("module")
    if not module == "PHOTO_TO_PRODUCT":
        module == "PHOTO_TO_PRODUCT"
        if module == "DESIGN":
            module == "DESIGN"
            if not context.get("brief"):
                context.get("brief")
            if not "LASER_CUT_AWARE_DESIGN" in {}.get("structure_constraints", []):
                "LASER_CUT_AWARE_DESIGN" in {}.get("structure_constraints", [])
                if not context.get("input"):
                    context.get("input")
                if {}.get("mode") == "STENCIL":
                    {}.get("mode") == "STENCIL"
                    if not context.get("brief"):
                        context.get("brief")
    return {}.get("output_kind", "PRODUCT_DESIGN") != "BITMAP_VISUAL"

def product_presentation_instruction(context):
    if not context.get("input"):
        context.get("input")
    if {}.get("module") != "DESIGN":
        return ""
    return "DESIGN is a physical craft PRODUCT DESIGN, shown alone on a clean white or neutral background, front-on with all material boundaries and openings readable. No room, garden, wall installation, hands, props or lifestyle scene. Employee words define the subject and requested changes. Structural reference examples demonstrate retained-material connections, negative-space organization and surface finishes, not subjects to copy or scenery to reproduce. First decide one connected cuttable material network with broad natural support for essential features. Describe outer silhouette, planned openings, how eyes/nose/limbs/decorative shapes stay attached, and optional engraving separately in the existing brief fields. Then choose original shape, composition, material finish and palette appropriate to the request; colour and patina may vary without changing cut topology. Write these concrete decisions into execution_prompt; do not delegate structural design to unrestricted image-model improvisation. "

def laser_art_instruction(context):
    if not context.get("input"):
        context.get("input")
    if {}.get("module") == "DESIGN":
        if not context.get("brief"):
            context.get("brief")
        if {}.get("surface_finish_stage") == "STRUCTURE":
            return LASER_ART_INSTRUCTION
    import re
    if not context.get("input"):
        context.get("input")
    request = {}
    
    explicit = " ".join((str(request.get(k) or "") for k in ("theme", "requirements")))
    if request.get("module") != "DESIGN" and request.get("mode") == "STENCIL" or re.search("黑白|纯黑白|黑色剪影|切割稿|black[ -]and[ -]white|monochrome|cutting template", explicit, re.I):
        return LASER_ART_INSTRUCTION
    
    return LASER_ART_INSTRUCTION.replace("Use broad black/white masses, natural fur contours, continuous shadows and few meaningful negative spaces. ", "Use broad connected material shapes, natural contours and meaningful negative spaces. Show the designed finished material and surface treatment: wood grain, metal, paint, colour or patina as appropriate to the request and design. Do not force a black-white cutting mask for a finished product design. Surface colour does not define a through-cut. ")

def binary_cut_preview(context):
    if not context.get("input"):
        context.get("input")
    module = {}.get("module")
    if not context.get("brief"):
        context.get("brief")
    stage = {}.get("surface_finish_stage")
    if module == "DESIGN" and stage in ("STRUCTURE", "FINISH"):
        return stage == "STRUCTURE"
    elif not module in ("PHOTO_TO_PRODUCT", "BASIC_DXF"):
        module in ("PHOTO_TO_PRODUCT", "BASIC_DXF")
        if laser_design(context):
            laser_design(context)
    return laser_art_instruction(context) == LASER_ART_INSTRUCTION

REVIEW_INSTRUCTION = "Return product_structure_profile based on the CURRENT visible image, including specific regions, not generic rules. Set applicable only for a future physical product (always for PHOTO_TO_PRODUCT product art and BASIC_DXF); an ordinary graphic or empty background is not a physical product. Profile entries are VISUAL hypotheses and likely process intent, never verified geometry. Review in order: subject correctness, design quality, identity/product fidelity, structural awareness, module checks. For PHOTO_TO_PRODUCT follow the six qa_contract checks in their given order. The PHOTO_TO_PRODUCT main preview explicitly uses black as retained material and white as removed space for one piece. Its retained regions must visibly connect. A proposed engraving label cannot excuse detached black material in that preview; Also inspect load-bearing bottlenecks: one connected component can still leave a head on a hairline ligament. Look at both sides of necks and long negative cuts, and reject obvious weak bridges. Natural fur tips and tasteful angular contours are allowed: do not fail a design merely for corners or require blanket rounding. Flag obvious fragile or needle-like hazards while preserving beauty and recognizability. engraving intentions belong in the plan, with the main cut artwork structurally complete. Record STRUCTURAL_ART and SURFACE_DETAIL separately in structural_art and surface_detail, with image-specific likely_cut_features, likely_engrave_features and decorative_detail. Simulate removing PLANNED openings, not every white surface detail; identify retained black regions that would drop, disconnect or lose recognizable features; also consider whether those details should instead be ENGRAVE. For SCENE, protect original boundaries, holes, bridges, connected regions, orientation and cut/engrave appearance; restore original product and composite locally for damage, never redesign it. For BASIC_DXF, compare actual cut and engrave layers; deterministic geometry is final authority. " + DESIGN_INSTRUCTION
def level(module):
    return {"DESIGN": "DESIGN_AWARENESS", "PHOTO_TO_PRODUCT": "DESIGN_AWARENESS", "SCENE": "PRODUCT_PRESERVATION", "BASIC_DXF": "ENGINEERING_REVIEW"}.get(module)

def evidence(profile, module):
    if not profile:
        return {}
    validated = ProductStructureProfile.model_validate(profile).model_dump()
    return {"product_structure_profile": validated, "process_intent": {"STRUCTURAL_ART": validated["structural_art"], "SURFACE_DETAIL": validated["surface_detail"], "LIKELY_CUT": validated["likely_cut_features"], "LIKELY_ENGRAVE": validated["likely_engrave_features"], "DECORATIVE_DETAIL": validated["decorative_detail"]}, "manufacturing_awareness": {"enabled": validated["applicable"], "level": level(module), "authority": "VISUAL_DESIGN_INTENT", "geometry_verified": False}}
