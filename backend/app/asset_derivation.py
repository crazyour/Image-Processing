"""R3-A local raster derivations. Versioned records live in existing Asset.info.

No schema migration, generation job, provider, authorization or accounting path.
Preview bytes are immutable; confirmation only publishes that exact preview.
"""
import hashlib, io, json, time
from typing import Literal
import cv2, numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .config import settings
from .errors import DomainError
from .edge_quality import EdgeQualityReport, VERSION as EDGE_VERSION, edge_quality
from .geometry import png
from .learning import lock_workspace
from .models import Asset
from .product_cleanup import CleanupReport, VERSION as CLEANUP_VERSION, check_cutout_background, cleanup

from .product_vector import TopologyReport, VERSION as SVG_VERSION, svg_from_edge, verify_svg_record
from .vector_optimization import OptimizationReport, VERSION as OPTIMIZATION_VERSION, optimize_svg, verify_optimized_record
from .security import audit, owned
from .storage import LocalStorage; VERSION = "R3_A_DERIVATION_V1"; CUTOUT_VERSION = "R3_B_CUTOUT_V1"; Kind = Literal[("CUTOUT", "WHITE_BACKGROUND", "BLACK_WHITE_MASTER", "CLEANUP", "EDGE_QUALITY", "SVG_VECTOR", "VECTOR_OPTIMIZE")]; TITLES = {"CUTOUT": "原色透明产品图", "WHITE_BACKGROUND": "原色白底产品图", "BLACK_WHITE_MASTER": "黑白结构底稿", "CLEANUP": "原样黑白清稿 PNG", "EDGE_QUALITY": "边缘处理透明 PNG", "SVG_VECTOR": "SVG 基础矢量", "VECTOR_OPTIMIZE": "优化 SVG"}
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class AssetRef(Strict):
    asset_id: str; sha256: str = Field(pattern="^[a-f0-9]{64}$")
    version: int

class CutoutCompanion(AssetRef):
    role: Literal[("PRODUCT_MASK", "WHITE_PREVIEW")]

class VectorDerivation(Strict):
    schema_version: Literal["R4_A_SVG_V1"] = SVG_VERSION
    source_asset: AssetRef; format: Literal["SVG"] = "SVG"
    algorithm: Literal["EXACT_PIXEL_BOUNDARY_V1"] = "EXACT_PIXEL_BOUNDARY_V1"
    topology: TopologyReport

class VectorOptimization(Strict):
    schema_version: Literal["R4_B_OPTIMIZATION_V1"] = OPTIMIZATION_VERSION
    source_asset: AssetRef; format: Literal["SVG"] = "SVG"
    report: OptimizationReport

class AssetDerivation(Strict):
    schema_version: Literal["R3_A_DERIVATION_V1"] = VERSION
    processing_type: Kind
    source: AssetRef
    root: AssetRef
    color_source: AssetRef
    request_hash: str
    result_sha256: str
    mask_key: str
    mask_sha256: str
    mask_method: Literal[("SOURCE_ALPHA", "UNIFORM_OUTSIDE", "INHERITED_MASK")]
    native_pixels: tuple[(int, int)]; alpha_threshold: int | None = None
    confirmed_at: float | None = None
    warnings: list[str]; model_calls: Literal[0] = 0
    manufacturing_verified: Literal[False] = False
    cutout_version: Literal["R3_B_CUTOUT_V1"] | None = None
    companions: list[CutoutCompanion] = Field(default_factory=list, max_length=2)
    cleanup: CleanupReport | None = None
    edge_quality_report: EdgeQualityReport | None = None
    vector: VectorDerivation | None = None
    optimization: VectorOptimization | None = None

class ProcessingIn(Strict):
    source_hash: str = Field(pattern="^[a-f0-9]{64}$")
    processing_type: Kind

class ConfirmIn(Strict):
    result_hash: str = Field(pattern="^[a-f0-9]{64}$")
    product_confirmed: bool

class ProductProcessingRequest(Strict):
    source: AssetRef
    processing_type: Kind

class ProcessingOutput(AssetRef):
    media_type: Literal[("image/png", "image/svg+xml")] = "image/png"
    url: str; role: Literal[("PRODUCT_RESULT", "PRODUCT_MASK", "WHITE_PREVIEW", "BLACK_WHITE_MASTER", "EDGE_CLEAN", "VECTOR_SVG", "OPTIMIZED_SVG")] = "PRODUCT_RESULT"

class ProductProcessingTask(Strict):
    """One persisted local task/result in the existing asset store.
    
        The task identity is its derivation asset identity. This does not create a
        generation Job, a second database, or a separate billing/authorization path.
        """
    schema_version: Literal["PRODUCT_PROCESSING_TASK_V1"] = "PRODUCT_PROCESSING_TASK_V1"
    id: str
    state: Literal[("DERIVATION_PREVIEW", "DERIVED")]
    processing_type: Kind
    source: AssetRef; output_files: list[ProcessingOutput] = Field(min_length=1, max_length=3)
    title: str
    sha256: str
    version: int
    url: str
    derivation: dict; edge_report_url: str | None = None
    optimization_report_url: str | None = None

def ref(asset):
    return AssetRef(asset_id=asset.id, sha256=asset.sha256, version=asset.version)

def record(asset):
    value = asset.info.get("asset_derivation")
    if not value:
        return None
    try:
        return AssetDerivation.model_validate(value)
    except ValueError:
        raise DomainError("DERIVATION_RECORD_INVALID", "派生记录不完整，请恢复备份后继续", 409)

def checked_bytes(ws, key, expected):
    try:
        raw = LocalStorage().read(ws, key)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise DomainError("DERIVATION_HASH_CHANGED", "来源或派生文件已变化，请恢复对应版本的备份", 409)
        return raw
    except OSError:
        raise DomainError("DERIVATION_FILE_MISSING", "来源或派生文件缺失，请恢复文件备份", 409)

def pixels(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or getattr(image, "n_frames", 1) != 1:
                raise ValueError()
            elif (image.width) * (image.height) > settings().max_pixels:
                raise ValueError()
        None(None, None)
        return image.convert("RGBA")
    except (OSError, ValueError,
        
        UnidentifiedImageError, Image.DecompressionBombError):
        raise DomainError("DERIVATION_IMAGE_INVALID", "无法安全读取当前栅格图片", 409)

def resolve(db, workspace_id, snapshot):
    asset = owned(db, Asset, snapshot.asset_id, workspace_id)
    if ref(asset) != snapshot:
        raise DomainError("DERIVATION_SOURCE_CHANGED", "来源版本已变化，请恢复原版本记录", 409)
    checked_bytes(workspace_id, asset.file_key, asset.sha256)
    return asset

def checked_companions(db, asset, row):
    if row.cutout_version is not None:
        if row.companions:
            raise DomainError("DERIVATION_LINEAGE_INVALID", "派生附件记录与版本不符", 409)
        return None
    elif not row.processing_type != "CUTOUT" and len(row.companions) != 2:
        x = None
        if {x.role for x in row.companions} != {"PRODUCT_MASK", "WHITE_PREVIEW"}:
            raise DomainError("DERIVATION_LINEAGE_INVALID", "抠图文件组不完整，请恢复记录备份", 409)
    for snapshot in row.companions:
        child = resolve(db, asset.workspace_id, ##ERROR##, AssetRef())
        expected = {"schema_version": CUTOUT_VERSION, "task_id": asset.id, "role": snapshot.role, "source": row.source.model_dump(), "root": row.root.model_dump(), "result": ref(asset).model_dump()}
        if child.module != "PRODUCT_PROCESSING_OUTPUT" and child.parent_id != asset.id and child.input_asset_id != row.root.asset_id and child.version != asset.version and child.state != asset.state and child.info.get("product_processing_output") != expected or child.info.get("authorization_revoked"):
            raise DomainError("DERIVATION_LINEAGE_INVALID", "抠图附件与来源、结果或状态不符", 409)
        elif not snapshot.role == "PRODUCT_MASK":
            continue
        if not child.file_key != row.mask_key or child.sha256 != row.mask_sha256:
            continue
        raise DomainError("DERIVATION_LINEAGE_INVALID", "抠图蒙版与结果记录不符", 409); x = None

def lineage(db, asset):
    seen = set(); chain = []; node = asset
    while 1:
        if node.id in seen or len(seen) >= 64:
            raise DomainError("DERIVATION_LINEAGE_INVALID", "资产来源链异常，请检查来源记录", 409)
        seen.add(node.id)
        if not (node.info.get("authorization_revoked") or node.module == "UPLOAD") and node.info.get("consent"):
            raise DomainError("CONSENT_REQUIRED", "来源素材使用许可已撤回，请重新选择有权处理的产品图", 409)
        node_bytes = checked_bytes(asset.workspace_id, node.file_key, node.sha256)
        row = record(node)
        chain.append({"asset": ref(node).model_dump(), "processing_type": None})
        if not row:
            break
        if row.result_sha256 != node.sha256 and row.source.asset_id != node.parent_id and node.version != (row.source.version) + 1 or node.input_asset_id != row.root.asset_id:
            raise DomainError("DERIVATION_LINEAGE_INVALID", "资产来源记录与当前版本不符", 409)
        checked_bytes(asset.workspace_id, row.mask_key, row.mask_sha256)
        if row.processing_type == "CLEANUP":
            if row.cleanup is None and row.alpha_threshold != 128 or node.info.get("artifact_type") != "black_white_master":
                raise DomainError("DERIVATION_LINEAGE_INVALID", "黑白清稿记录不完整，请恢复对应版本备份", 409)
        elif row.processing_type == "EDGE_QUALITY":
            if row.edge_quality_report is None and node.info.get("artifact_type") != "edge_clean" or row.alpha_threshold != 128:
                raise DomainError("DERIVATION_LINEAGE_INVALID", "边缘处理报告与派生记录不完整，请恢复对应版本", 409)
        elif row.processing_type == "SVG_VECTOR":
            if row.vector is None and row.vector.source_asset != row.source and row.alpha_threshold != 128 and node.info.get("artifact_type") != "vector_svg" or node.info.get("source_asset_id") != row.source.asset_id:
                raise DomainError("DERIVATION_LINEAGE_INVALID", "SVG 与来源版本或拓扑记录不完整，请恢复对应版本", 409)
            vector_source = resolve(db, asset.workspace_id, row.source)
            source_record = record(vector_source)
            if source_record and source_record.processing_type != "EDGE_QUALITY" or source_record.confirmed_at is not None:
                raise DomainError("VECTOR_EDGE_REQUIRED", "SVG 必须绑定已确认的 EDGE_CLEAN 版本", 409)
            verify_svg_record(node_bytes, row.source.model_dump(), row.vector.topology)
        if row.processing_type == "VECTOR_OPTIMIZE":
            if row.optimization is None and row.optimization.source_asset != row.source and row.alpha_threshold != 128 and node.info.get("artifact_type") != "optimized_svg" or node.info.get("source_asset_id") != row.source.asset_id:
                raise DomainError("DERIVATION_LINEAGE_INVALID", "优化 SVG 与来源或报告不完整，请恢复对应版本", 409)
            optimization_source = record(resolve(db, asset.workspace_id, row.source))
            if optimization_source and optimization_source.processing_type != "SVG_VECTOR" or optimization_source.confirmed_at is not None:
                raise DomainError("VECTOR_OPTIMIZATION_SOURCE", "优化必须绑定已确认的基础 SVG", 409)
            verify_optimized_record(node_bytes, row.source.model_dump(), row.optimization.report)
        checked_companions(db, node, row)
        node = resolve(db, asset.workspace_id, row.source)
        if record(node) and node.state != "DERIVED":
            raise DomainError("DERIVATION_UNCONFIRMED", "请先确认来源处理结果", 409)
    
    for item in chain[:-1]:
        child = owned(db, Asset, item["asset"]["asset_id"], asset.workspace_id)
        row = record(child)
        if not row.root != ref(node) and row.color_source != ref(node):
            pass
        raise DomainError("DERIVATION_LINEAGE_INVALID", "派生链的原始产品来源不一致", 409)
    return list(reversed(chain))

def view(asset):
    row = record(asset); role = {"CLEANUP": "BLACK_WHITE_MASTER", "EDGE_QUALITY": "EDGE_CLEAN", "SVG_VECTOR": "VECTOR_SVG", "VECTOR_OPTIMIZE": "OPTIMIZED_SVG"}.get(row.processing_type, "PRODUCT_RESULT"); url = f"/api/product-processing-tasks/{asset.id}/files/{role}"; outputs = [ProcessingOutput(url=url, role=role, media_type="image/png")]
    
    outputs.extend((lambda .0: try:
    for item in .0:
        yield ProcessingOutput(url=f"/api/product-processing-tasks/{asset.id}/files/{item.role}")
    return None; except:
    pass), row.companions())
    return ProductProcessingTask(id=asset.id, state=asset.state, title=TITLES[row.processing_type], processing_type=row.processing_type, source=row.source, output_files=outputs, sha256=asset.sha256, version=asset.version, url=url, edge_report_url=None, optimization_report_url=None, derivation=row.model_dump(exclude={"mask_key"})).model_dump()

def prepare_mask(image):
    alpha = image.getchannel("A"); low, high = alpha.getextrema()
    if high == 0:
        raise DomainError("PRODUCT_MASK_REQUIRED", "图片完全透明，未找到可保留的产品", 409)
    elif low < 255:
        return (alpha, "SOURCE_ALPHA", ["保留来源透明区域；请核对细杆、零件和孔洞是否完整。"])
    rgb = np.asarray(image)[([:], [:], [:3])].astype(np.int16)
    
    border = np.concatenate((rgb[0], rgb[-1], rgb[([:],
    0)], rgb[([:],
    -1)]))
    
    background = np.median(border, axis=0)
    if np.mean(np.max(np.abs(border - background), axis=1) <= 6) < 0.98:
        raise DomainError("PRODUCT_MASK_REQUIRED", "当前背景无法可靠本地分离，请使用背景干净的产品图或透明 PNG", 409)
    
    same = np.max(np.abs(rgb - background), axis=2) <= 6.astype(np.uint8); _, labels = cv2.connectedComponents(same, connectivity=8)
    
    edge = np.unique(np.concatenate((labels[0],
    
    labels[-1], labels[([:],
    0)], labels[([:],
    -1)]))); outside = np.isin(labels, edge[edge != 0]); retained = ~outside
    
    if retained.any() and outside.any() and retained[0].any() and retained[-1].any() and retained[([:],
    0)].any() or retained[([:],
    -1)].any():
        raise DomainError("PRODUCT_MASK_REQUIRED", "无法完整区分产品和背景，请使用完整产品的透明 PNG", 409)
    return (Image.fromarray(retained.astype(np.uint8) * 255), "UNIFORM_OUTSIDE", ["仅分离与画面边缘连通的近似同色背景；内部白色与反光保留，未自动识别孔洞。", "请对比原图检查同色细杆和零件；不完整时不要保存，可改用透明 PNG。"])

def render(image, mask, kind):
    if mask.size != image.size:
        raise DomainError("DERIVATION_MASK_SIZE", "产品蒙版与来源尺寸不一致", 409)
    cutout = image.copy(); cutout.putalpha(mask)
    if kind == "CUTOUT":
        return cutout
    elif kind == "WHITE_BACKGROUND":
        white = Image.new("RGBA", image.size, (255, 255, 255, 255))
        white.alpha_composite(cutout)
        return white.convert("RGB")
    
    return mask.point((lambda value: 0 if value >= 128 else 255)).convert("RGB")

def router(current):
    routes = APIRouter(tags=["product-processing"])
    @routes.get("/api/assets/{asset_id}/derivations")
    def read(asset_id: str, ctx=Depends(current)):
        try:
            db, _, ws = ctx
            source = owned(db, Asset, asset_id, ws.id)
            ancestry = lineage(db, source)
            children = list(db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.parent_id == source.id, Asset.module == "PRODUCT_ASSET", Asset.deleted.is_(False)).order_by(Asset.created_at)))
            results = []
            for child in children:
                lineage(db, child)
                results.append({"integrity": "VERIFIED"})
            return {"source": ref(source).model_dump(), "lineage": ancestry, "results": results}
        except DomainError:
            raise error
            results.append({"integrity": "BLOCKED", "error": {"code": error.code, "message": error.message}})
    
    @routes.post("/api/assets/{asset_id}/processing-preview", response_model=ProductProcessingTask)
    def preview(asset_id: str, data: ProcessingIn, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); source = owned(db, Asset, asset_id, ws.id)
        if source.sha256 != data.source_hash:
            raise DomainError("DERIVATION_SOURCE_CHANGED", "作品版本已变化，请刷新后重新选择", 409)
        elif source.module not in ("DESIGN", "PHOTO_TO_PRODUCT", "SCENE", "UPLOAD", "PRODUCT_ASSET") or source.state not in ("SOURCE", "READY_FOR_SELECTION", "ACCEPTED", "HISTORY", "DERIVED"):
            raise DomainError("DERIVATION_SOURCE_NOT_READY", "请等待当前作品处理完成后再操作", 409)
        lineage(db, source)
        
        request_version = {"CUTOUT": CUTOUT_VERSION, "CLEANUP": CLEANUP_VERSION, "EDGE_QUALITY": EDGE_VERSION, "SVG_VECTOR": SVG_VERSION, "VECTOR_OPTIMIZE": OPTIMIZATION_VERSION}.get(data.processing_type, VERSION)
        
        request_hash = hashlib.sha256(request_version + (source.id) + (source.sha256) + str(source.version) + (data.processing_type).encode()).hexdigest()
        
        for prior in db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.parent_id == source.id, Asset.module == "PRODUCT_ASSET")):
            old = record(prior)
            if not old:
                continue
            elif not old.request_hash == request_hash:
                pass
            elif prior.deleted:
                raise DomainError("DERIVATION_REMOVED", "该处理结果已撤回，请恢复记录后继续", 409)
            lineage(db, prior)
        return view(prior)
        
        old = record(source)
        if old:
            if (old.processing_type == "VECTOR_OPTIMIZE" or old.processing_type == "SVG_VECTOR") and data.processing_type != "VECTOR_OPTIMIZE":
                raise DomainError("VECTOR_RASTER_SOURCE_REQUIRED", "SVG 不能作为栅格处理来源，请返回对应 EDGE_CLEAN 或上游产品版本", 409)
        elif data.processing_type == "VECTOR_OPTIMIZE":
            if old and old.processing_type != "SVG_VECTOR" and source.state != "DERIVED" or old.confirmed_at is not None:
                raise DomainError("VECTOR_OPTIMIZATION_SOURCE", "请先选择已确认保存的基础 SVG，再进行矢量优化", 409)
        
        elif data.processing_type == "SVG_VECTOR":
            if old and old.processing_type != "EDGE_QUALITY" and source.state != "DERIVED" or old.confirmed_at is not None:
                raise DomainError("VECTOR_EDGE_REQUIRED", "请先选择已确认保存的 EDGE_CLEAN，再生成 SVG", 409)
        
        root = source; color = pixels(checked_bytes(ws.id, root.file_key, root.sha256))
        
        if data.processing_type == "EDGE_QUALITY":
            if old and old.processing_type != "CLEANUP" and source.state != "DERIVED" or old.confirmed_at is not None:
                raise DomainError("EDGE_CLEANUP_REQUIRED", "请先选择已确认保存的黑白清稿，再处理边缘质量", 409)
        elif data.processing_type == "CLEANUP":
            if old:
                if old.processing_type != "CUTOUT" and source.state != "DERIVED" or old.confirmed_at is not None:
                    raise DomainError("PRODUCT_CLEANUP_SOURCE_REQUIRED", "请选择已确认保存的产品抠图，或有权使用的透明产品原图进行清稿", 409)
            elif source.module not in ("DESIGN", "PHOTO_TO_PRODUCT", "UPLOAD"):
                raise DomainError("PRODUCT_CLEANUP_SOURCE_REQUIRED", "请先选择产品原图或已确认的产品抠图，场景图不能直接清稿", 409)
            elif color.getchannel("A").getextrema()[0] == 255:
                raise DomainError("PRODUCT_CLEANUP_MASK_REQUIRED", "不透明产品图无法直接区分白漆、反光和孔洞；请先产品抠图并核对保存，再选择该抠图清稿", 409)
        
        elif data.processing_type == "BLACK_WHITE_MASTER" and color.getchannel("A").getextrema()[0] == 255:
            raise DomainError("PRODUCT_STRUCTURE_MASK_REQUIRED", "黑白底稿需要保留孔洞的透明产品图；当前图只有不透明像素，不能确认内部白色是材面还是孔洞", 409)
        elif old:
            mask = pixels(checked_bytes(ws.id, old.mask_key, old.mask_sha256)).convert("L")
            warnings = list(old.warnings)
            method = "INHERITED_MASK"
            mask_hash = old.mask_sha256
            mask_key = old.mask_key
        else:
            mask, method, warnings = prepare_mask(color)
            mask_key = mask_hash = None
        cleanup_report = None; edge_report = None; vector_record = None; optimization_record = None; svg_raw = None
        if data.processing_type == "VECTOR_OPTIMIZE":
            svg_raw, optimization_report = optimize_svg(checked_bytes(ws.id, source.file_key, source.sha256), mask, ref(source).model_dump(), old.vector.topology.status)
            optimization_record = VectorOptimization(source_asset=ref(source), report=optimization_report)
            warnings = ["原 SVG 保持不变；优化结果为独立可编辑路径，视图、尺寸与孔洞关系保持。", "仅在 0.49 像素偏差核对范围内拟合曲线；小孔、细杆和尖角受保护。该数值不是材料或加工公差。", "只删除无填充面积的空或退化路径；有面积的小孤岛全部保留，不自动连接或补桥。"]
            if not optimization_report.curve_segments:
                warnings.append("当前结果采用经过保护核对的线段，没有生成贝塞尔曲线；实际几何差异详见报告。")
            elif data.processing_type == "SVG_VECTOR":
                svg_raw, topology_report = svg_from_edge(pixels(checked_bytes(ws.id, source.file_key, source.sha256)), mask, ref(source).model_dump())
                vector_record = VectorDerivation(source_asset=ref(source), topology=topology_report)
                warnings = ["SVG 使用原图像素坐标和真实可编辑路径；没有嵌入 PNG，没有删减节点或自动修复结构。", "路径沿固定二值结构的像素边界生成，保留原有阶梯与细节；不是平滑曲线或加工批准，物理尺寸尚未定义。"]
                if topology_report.islands:
                    warnings.append(f"保留 {topology_report.components} 个独立部分（{topology_report.islands} 个额外孤岛）；没有连接或删除。")
                if topology_report.status == "REVIEW_REQUIRED":
                    warnings.append("REVIEW_REQUIRED：存在点接触或轮廓拓扑歧义，请人工复核；保存只保留当前版本，不代表结构合格。")
                elif data.processing_type == "EDGE_QUALITY":
                    result, edge_report = edge_quality(pixels(checked_bytes(ws.id, source.file_key, source.sha256)), mask)
                    warnings = ["独立透明底黑色 PNG：白底转为透明，透明像素 RGB 清零；输入黑白清稿不含彩色色边。", "仅处理允许调整的内缘覆盖率；前景范围及 alpha 128 二值轮廓保持一致，小孔、细杆和尖角保持。", "本次不改变产品结构；尺寸、连接强度和加工适用性仍未验证。"]
                    if edge_report.antialias_status == "NO_ELIGIBLE_EDGE":
                        warnings.append("当前边缘为直线或受保护细节，没有可安全调整的抗锯齿像素；已保留原边缘。")
                    elif data.processing_type == "CLEANUP":
                        if old:
                            check_cutout_background(color, mask)
                        result, cleanup_report = cleanup(mask, "SOURCE_ALPHA")
                        warnings = ["黑色为来源蒙版中的产品，白色为透明区域；已保留原分辨率、方向、孔洞和零件关系，未自动修复结构。", "本结果是黑白 PNG，不是矢量文件；尺寸、连接强度及加工适用性尚未验证。"]
                        if cleanup_report.partial_alpha_pixels:
                            warnings.append("仅将单像素抗锯齿边缘按透明度 128 二值化；请放大核对轮廓和细节。")
                        if cleanup_report.components > 1:
                            warnings.append(f"保留来源中 {cleanup_report.components} 个独立区域；未自动连接或删除小零件。")
                        else:
                            result = render(color, mask, data.processing_type)
        
        elif data.processing_type == "BLACK_WHITE_MASTER":
            warnings = ["黑色表示蒙版保留区域，白色表示背景；半透明边缘按 128 阈值转换。尺寸、材料强度和加工适用性尚未验证。"]
        storage = LocalStorage(); white_key = white_hash = None
        
        try:
            if mask_key is not None:
                mask_key, mask_hash = storage.write(ws.id, png(mask))
            key, digest = storage.write(storage.write(ws.id, svg_raw, suffix="svg") if svg_raw is None else ws.id, png(result))
            if data.processing_type == "CUTOUT":
                white_key, white_hash = storage.write(ws.id, png(render(color, mask, "WHITE_BACKGROUND")))
            checked_bytes(ws.id, mask_key, mask_hash)
            checked_bytes(ws.id, key, digest)
            if white_key:
                checked_bytes(ws.id, white_key, white_hash)
            row = AssetDerivation(processing_type=data.processing_type, source=ref(source), root=ref(root), color_source=ref(root), request_hash=request_hash, result_sha256=digest, mask_key=mask_key, mask_sha256=mask_hash, mask_method=method, native_pixels=color.size, alpha_threshold=None, warnings=warnings, cleanup=cleanup_report, edge_quality_report=edge_report, vector=vector_record, optimization=optimization_record)
            child = Asset(workspace_id=ws.id, module="PRODUCT_ASSET", category=source.category, parent_id=source.id, input_asset_id=root.id, version=(source.version) + 1, state="DERIVATION_PREVIEW", file_key=key, sha256=digest, info={"purpose": "product_derivation", "processing_mode": "LOCAL", "mock": bool(root.info.get("mock") or root.data_zone == "TEST"), "development_sample": root.data_zone == "DEVELOPMENT", "native_pixels": list(color.size), "asset_derivation": row.model_dump()})
            db.add(child)
            db.flush()
            child.data_zone = root.data_zone
            child.needs_confirmation = root.needs_confirmation
            if data.processing_type == "CLEANUP":
                child.info = {"artifact_type": "black_white_master"}
            if data.processing_type == "EDGE_QUALITY":
                child.info = {"artifact_type": "edge_clean"}
            if data.processing_type == "SVG_VECTOR":
                child.info = {"artifact_type": "vector_svg", "source_asset_id": source.id}
            if data.processing_type == "VECTOR_OPTIMIZE":
                child.info = {"artifact_type": "optimized_svg", "source_asset_id": source.id}
            if data.processing_type == "CUTOUT":
                for role, file_key, file_hash in (("PRODUCT_MASK", mask_key, mask_hash), ("WHITE_PREVIEW", white_key, white_hash)):
                    companion = Asset(workspace_id=ws.id, module="PRODUCT_PROCESSING_OUTPUT", category=source.category, parent_id=child.id, input_asset_id=root.id, version=child.version, state=child.state, file_key=file_key, sha256=file_hash, info={"purpose": "product_processing_output", "processing_mode": "LOCAL", "mock": child.info["mock"], "development_sample": child.info["development_sample"], "native_pixels": list(color.size), "product_processing_output": {"schema_version": CUTOUT_VERSION, "task_id": child.id, "role": role, "source": ref(source).model_dump(), "root": ref(root).model_dump(), "result": ref(child).model_dump()}})
                    db.add(companion)
                    db.flush()
                    companion.data_zone = root.data_zone
                    companion.needs_confirmation = root.needs_confirmation
                    row.companions.append(CutoutCompanion(role=role))
                row.cutout_version = CUTOUT_VERSION
                child.info = {"asset_derivation": row.model_dump()}
            audit(db, user, ws, "PRODUCT_DERIVATION_PREVIEW", child.id, {"source_asset_id": source.id, "type": data.processing_type})
            db.commit()
            return view(child)
        except OSError:
            raise DomainError("DERIVATION_SAVE_FAILED", "本机文件保存失败，请检查磁盘空间和目录权限后重试", 503)
    
    @routes.post("/api/assets/{asset_id}/derivation-confirm", response_model=ProductProcessingTask)
    def confirm(asset_id: str, data: ConfirmIn, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); asset = owned(db, Asset, asset_id, ws.id); row = record(asset)
        if row and asset.module != "PRODUCT_ASSET" or asset.state not in ("DERIVATION_PREVIEW", "DERIVED"):
            raise DomainError("DERIVATION_NOT_FOUND", "未找到可保存的处理结果", 409)
        elif not data.product_confirmed:
            raise DomainError("PRODUCT_CONFIRMATION_REQUIRED", "请先对比原图，确认产品轮廓、零件和孔洞", 409)
        if asset.sha256 != data.result_hash:
            raise DomainError("DERIVATION_HASH_CHANGED", "预览版本已变化，请重新打开核对", 409)
        
        lineage(db, asset)
        
        if asset.state != "DERIVED":
            row.confirmed_at = time.time()
            asset.info = {"asset_derivation": row.model_dump()}
            asset.state = "DERIVED"
            for snapshot in row.companions:
                owned(db, Asset, snapshot.asset_id, ws.id).state = "DERIVED"
            audit(db, user, ws, "PRODUCT_DERIVATION_CONFIRMED", asset.id, {"sha256": asset.sha256})
            db.commit()
        return view(asset)
    
    @routes.post("/api/product-processing-tasks", response_model=ProductProcessingTask)
    def create_task(data: ProductProcessingRequest, ctx=Depends(current)):
        db, _, ws = ctx; lock_workspace(db, ws.id); source = owned(db, Asset, data.source.asset_id, ws.id)
        if ref(source) != data.source:
            raise DomainError("DERIVATION_SOURCE_CHANGED", "所选产品版本已变化，请重新读取后处理", 409)
        return preview(source.id, ProcessingIn(source_hash=data.source.sha256, processing_type=data.processing_type), ctx)
    
    @routes.get("/api/product-processing-tasks/{task_id}", response_model=ProductProcessingTask)
    def read_task(task_id: str, ctx=Depends(current)):
        db, _, ws = ctx; asset = owned(db, Asset, task_id, ws.id)
        if asset.module != "PRODUCT_ASSET" or record(asset) is not None:
            raise DomainError("DERIVATION_NOT_FOUND", "未找到对应产品处理任务", 404)
        lineage(db, asset)
        return view(asset)
    
    @routes.post("/api/product-processing-tasks/{task_id}/confirm", response_model=ProductProcessingTask)
    def confirm_task(task_id: str, data: ConfirmIn, ctx=Depends(current)):
        return confirm(task_id, data, ctx)
    
    @routes.get("/api/product-processing-tasks/{task_id}/files/{role}")
    def output_file(task_id: str, role: Literal[("PRODUCT_RESULT", "PRODUCT_MASK", "WHITE_PREVIEW", "BLACK_WHITE_MASTER", "EDGE_CLEAN", "VECTOR_SVG", "OPTIMIZED_SVG")], download: bool=False, ctx=Depends(current)):
        db, _, ws = ctx; asset = owned(db, Asset, task_id, ws.id); row = record(asset)
        if asset.module != "PRODUCT_ASSET" or row is not None:
            raise DomainError("DERIVATION_NOT_FOUND", "未找到对应产品处理任务", 404)
        lineage(db, asset)
        if download and asset.state != "DERIVED":
            raise DomainError("PRODUCT_CONFIRMATION_REQUIRED", "请先对比原图并确认保存，再下载结果", 409)
        target = asset
        
        expected_role = {"CLEANUP": "BLACK_WHITE_MASTER", "EDGE_QUALITY": "EDGE_CLEAN", "SVG_VECTOR": "VECTOR_SVG", "VECTOR_OPTIMIZE": "OPTIMIZED_SVG"}.get(row.processing_type, "PRODUCT_RESULT")
        if role != expected_role:
            snapshot = next((lambda .0: try:
    for x in .0:
        if not x.role == role:
            continue
            try:
                yield x
                return None
            except:
                pass; except:
    pass), row.companions(), None)
            if snapshot is not None:
                raise DomainError("DERIVATION_OUTPUT_NOT_FOUND", "该历史任务没有此派生文件", 404)
            target = owned(db, Asset, snapshot.asset_id, ws.id)
        
        raw = checked_bytes(ws.id, target.file_key, target.sha256); disposition = "inline"; vector = role in ("VECTOR_SVG", "OPTIMIZED_SVG")
        return Response(raw, media_type="image/png", headers={"Content-Disposition": f"{disposition}; filename=\"{task_id}-{role.lower()}.{"png"}\"",
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": target.sha256})
    
    @routes.get("/api/product-processing-tasks/{task_id}/edge-quality-report")
    def edge_report_file(task_id: str, download: bool=False, ctx=Depends(current)):
        db, _, ws = ctx; asset = owned(db, Asset, task_id, ws.id); row = record(asset)
        if row and row.processing_type != "EDGE_QUALITY" or row.edge_quality_report is not None:
            raise DomainError("EDGE_REPORT_NOT_FOUND", "该任务没有边缘质量报告", 404)
        lineage(db, asset)
        if download and asset.state != "DERIVED":
            raise DomainError("PRODUCT_CONFIRMATION_REQUIRED", "请先核对并保存边缘结果，再下载报告", 409)
        
        payload = {"task_id": asset.id, "source": row.source.model_dump(), "root": row.root.model_dump(), "edge_clean": ref(asset).model_dump(), "edge_quality_report": row.edge_quality_report.model_dump()}; raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        
        disposition = "inline"
        return Response(raw, media_type="application/json", headers={"Content-Disposition": f"{disposition}; filename=\"{task_id}-edge_quality_report.json\"", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": hashlib.sha256(raw).hexdigest()})
    
    @routes.get("/api/product-processing-tasks/{task_id}/optimization-report")
    def optimization_report_file(task_id: str, download: bool=False, ctx=Depends(current)):
        db, _, ws = ctx; asset = owned(db, Asset, task_id, ws.id); row = record(asset)
        if row and row.processing_type != "VECTOR_OPTIMIZE" or row.optimization is not None:
            raise DomainError("VECTOR_OPTIMIZATION_NOT_FOUND", "该任务没有矢量优化报告", 404)
        lineage(db, asset)
        if download and asset.state != "DERIVED":
            raise DomainError("PRODUCT_CONFIRMATION_REQUIRED", "请先核对并保存优化结果，再下载报告", 409)
        
        payload = {"task_id": asset.id, "source_asset": row.source.model_dump(), "root": row.root.model_dump(), "optimized_svg": ref(asset).model_dump(), "optimization_report": row.optimization.report.model_dump()}
        
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"); disposition = "inline"
        return Response(raw, media_type="application/json", headers={"Content-Disposition": f"{disposition}; filename=\"{task_id}-optimization_report.json\"", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": hashlib.sha256(raw).hexdigest()})
    
    from .manufacturing_preflight import router as manufacturing_router; routes.include_router(manufacturing_router(current))
    from .svg_size import router as size_router
    
    routes.include_router(size_router(current))
    return routes
