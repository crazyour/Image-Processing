"""Local presentation rules for new scene recipes; no inference or asset changes."""; VERSION = "VISUAL_PRESENTATION_20261001_R3"
def locked_layer(raw):
    import io, numpy as np
    from PIL import Image; image = Image.open(io.BytesIO(raw)).convert("RGBA"); alpha = np.asarray(image.getchannel("A"))
    if not alpha == 0.mean() >= 0.02 and alpha >= 250.mean() >= 0.01:
        return None
    return image

def background_instruction(prompt):
    return prompt + "\n执行阶段：本次接口只制作供本地合成的空场景底图。图1是产品的外观与视角参考，最终产品由程序从图1保留透明主体并等比放入，不由你绘制。只输出环境，不出现产品、替代产品、重复轮廓、文字或占位框。在画面中央预留足够完整的展示空间，使用与原产品正面视角兼容的环境与柔和光照。墙饰留出干净墙面；挂件保留合理的悬挂背景；不使用需要改画产品的倾斜透视。前述完整产品照片是本地合成后的最终交付目标，此次API产物仅为环境底图。"

def compose_locked(background_raw, product):
    import io, hashlib
    from PIL import Image
    from .geometry import png
    from .locked_scene import composite_product; background = Image.open(io.BytesIO(background_raw)).convert("RGBA"); background.thumbnail((2048, 2048), Image.Resampling.LANCZOS); box = product.getchannel("A").getbbox(); h = box[3] - box[1]; w = box[2] - box[0]
    
    fraction = min(0.58, 0.76 * (background.height) * w / (background.width) * h); plan = dict(environment="本次选定的场景底图", camera="保留产品原视角", composition="完整主体中央展示，包含全部挂件", lighting="沿用原产品受光，不重绘产品", atmosphere="自然展示", placement_plane="原视角兼容展示平面", source_view_compatible=True, center_x=0.5, center_y=0.5, width_fraction=fraction, rotation_degrees=0, shadow_dx=0, shadow_dy=0.003, shadow_blur=0.004, shadow_opacity=0.12); final, proof = composite_product(background, product, execution=plan)
    return (png(final), {"method": "SOURCE_ALPHA_LOCAL_COMPOSITION", "source_alpha_sha256": hashlib.sha256(product.getchannel("A").tobytes()).hexdigest(), "source_bounds": list(box), "product_rgb_redrawn": False, "background_only_call": True, "physical_scale": "NOT_CERTIFIED"})

def scene_rules(request):
    if request.get("module") != "SCENE":
        return []
    
    return [f"展示规则版本：{VERSION}。只优化本次场景表现，不重新设计产品。", "产品与变体：图片1按已绑定角色作为唯一产品来源；沿用当前明确选择的版本、配色、材料、表面工艺与印花/裂纹分布。参考场景只负责环境，不借用其中的产品或表面效果；不能把彩色成品换成黑白结构稿。", "产品保护：保持外形、几何、孔洞、连接、方向、部件数量和比例；保留原有孔位及挂接结构。孔洞是空气，应透出新背景；不填孔、不加支撑、不新增挂链、不改变产品纹样。", "商品展示：以手工产品生活方式摄影（Handmade product photography）为目标，使用真实生活空间（Real home environment）。默认自然日光（Natural daylight）与可信的手机实拍质感（Phone camera realism），产品清晰、边缘自然、材质可辨；手机实拍质感不等于噪点、模糊、过曝或广角拉伸，不使用夸张HDR、塑料般高光或不真实悬浮。", "构图：先安排产品在真实空间中的位置、机位和光线，再安排少量环境元素。主体完整、占幅足以看清产品与表面，保留适当留白及尺度参照；不为了放大主体把小墙饰变成巨幅雕塑。家具、植物和装饰不遮挡或抢夺主体，避免无关道具堆砌。", "完整入镜：产品范围包括来源图已有的挂钩、挂链及其他连接部件，从最高挂件到最低触手或尖端全部入镜，四周留有可见余量。不能通过裁掉、缩短、重画挂件来放大主体；需要时退后取景。环境横梁或家具可以局部出框，产品部件不能出框。", "背景编辑方式：把来源中的产品与全部挂件视为同一个不可拆分的整体，只允许统一等比移动和缩放。沿用原照片观察角度，不重新摆正、拉长或压短某一部件；挂钩高度与主体宽度的比值、触手长度与主体高度的比值均沿用原图。新环境应适配这件原物，不能为适配环境重构产品。清楚保留原图可见的开口、连接端点和花纹位置。", "场景适配：沿用本次明确场景。墙艺应贴合真实墙面及已有挂装关系；户外挂饰使用实际可成立的悬挂或摆放位置；定制产品置于对应使用环境，保留定制内容。只有来源和当前要求提供了这些事实才应用，不凭产品名称推断功能或制造合格。", "优先级：已选产品与变体事实不可改变；本次明确的环境、光线、构图和禁止项优先于默认场景卡与摄影风格。例如明确要求夜景、无植物或悬挂时，不强加日景、植物或墙面贴装。未给实际尺寸时不编造测量值，已给尺寸只约束展示比例。"]
