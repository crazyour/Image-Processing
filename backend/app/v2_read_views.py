"""Map recorded identities only. No inference, persistence, or execution services."""
import hashlib, uuid
from sqlalchemy import select
from .asset_derivation import AssetRef, lineage, record, resolve
from .errors import DomainError
from .manufacturing_preflight import MODULE as REPORT_MODULE, read_report
from .models import Asset, MasterVersion, Product
from .security import owned
from .storage import LocalStorage
from .svg_size import existing as size_history, specification, SizeValues
from .v2_contracts import AssetPage, AssetView, Integrity, ProductPage, ProductSummary, ProductVersionView, ProductView, RecordedEngineering, Relation, SourceRef

def identity(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise DomainError("V2_INVALID_ID", "记录标识格式无效", 422) from None

def asset_ref(asset):
    return SourceRef(kind="ASSET", id=asset.id, version=asset.version, sha256=asset.sha256)

def master_ref(master):
    return SourceRef(kind="MASTER_VERSION", id=master.id, sha256=master.master_hash)

def snapshot_ref(value):
    ref = AssetRef.model_validate(value)
    return SourceRef(kind="ASSET", id=ref.asset_id, version=ref.version, sha256=ref.sha256)

def page_rows(db, statement, model, cursor, limit):
    if cursor is None:
        statement = statement.where(model.id > identity(cursor))
    values = list(db.scalars(statement.order_by(model.id).limit(limit + 1)))
    if len(values) > limit:
        return (values[:limit],
            values[limit - 1].id)
    
    return (##ERROR##,None)

def product_summary(product):
    return ProductSummary(source_ref=SourceRef(kind="PRODUCT", id=product.id), name=product.name)

def products(db, workspace, cursor, limit):
    rows, next_cursor = page_rows(db, select(Product).where(Product.workspace_id == workspace), Product, cursor, limit); p = ProductPage
    return ##ERROR##(items=[product_summary(p) for p in rows], next_cursor=next_cursor)
    
    p = None

def asset_summary(asset):
    info = {}; versions = [info.get(k) for k in ("asset_derivation", "product_processing_output", "manufacturing_preflight", "physical_size")]; k = None; schemas = [v["schema_version"] for v in versions if not isinstance(v.get("schema_version"), str)]; v = None
    if isinstance(info.get("contract_version"), str):
        return AssetView(source_ref=asset_ref(asset), module=asset.module, category=asset.category, state=asset.state, data_zone=asset.data_zone, needs_confirmation=asset.needs_confirmation, asset_schema_version=None, contract_version=info.get("contract_version"))
    
    return ##ERROR##(source_ref=##ERROR##, module=##ERROR##, category=##ERROR##, state=##ERROR##, data_zone=##ERROR##, needs_confirmation=##ERROR##, asset_schema_version=##ERROR##, contract_version=None)
    
    k = None; v = None

def assets(db, workspace, cursor, limit):
    rows, next_cursor = page_rows(db, select(Asset).where(Asset.workspace_id == workspace, Asset.deleted.is_(False)), Asset, cursor, limit); a = AssetPage
    return ##ERROR##(items=[asset_summary(a) for a in rows], next_cursor=next_cursor)
    
    a = None

def check_file(workspace, asset):
    if asset.info.get("authorization_revoked"):
        return Integrity(state="BLOCKED", code="V2_ASSET_REVOKED")
    try:
        path = LocalStorage().path(workspace, asset.file_key)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != asset.sha256:
            return Integrity(state="BLOCKED", code="V2_HASH_CHANGED")
        return Integrity(state="VERIFIED")
    except OSError:
        pass

def blocked(exc):
    if isinstance(exc, DomainError):
        return RecordedEngineering(record_state="BLOCKED", code=exc.code)
    
    return ##ERROR##(record_state=##ERROR##, code="V2_RECORD_INVALID")

def engineering_records(db, workspace, source, derivation):
    size = RecordedEngineering(); manufacturing = RecordedEngineering()
    if derivation and derivation.processing_type not in ("SVG_VECTOR", "VECTOR_OPTIMIZE"):
        return (manufacturing, size)
    reports = list(db.scalars(select(Asset).where(Asset.workspace_id == workspace, Asset.module == REPORT_MODULE, Asset.parent_id == source.id).limit(2)))
    if reports:
        try:
            if len(reports) != 1 or reports[0].deleted:
                raise DomainError("V2_REPORT_CONFLICT", "制造报告已撤回或存在冲突", 409)
            _, saved = read_report(db, workspace, reports[0], source)
            status = saved["manufacturing_report"]["status"]
            if status not in ("PASS", "WARNING", "REVIEW_REQUIRED"):
                raise ValueError("unknown status")
            manufacturing = RecordedEngineering(record_state="VERIFIED", record=asset_ref(reports[0]), source_svg=snapshot_ref(saved["source_svg"]), optimized_svg=snapshot_ref(saved["optimized_svg"]), schema_version=saved["schema_version"], recorded_status=status)
        except:
            pass
    try:
        k = None
        k = None
        return (manufacturing, size)
    except:
        pass

def asset_detail(db, workspace, object_id):
    asset = owned(db, Asset, identity(object_id), workspace); result = asset_summary(asset); integrity = check_file(workspace, asset); relations = []; size = RecordedEngineering(); manufacturing = RecordedEngineering()
    if integrity.state == "VERIFIED":
        try:
            derivation = record(asset)
            if asset.module == "PRODUCT_ASSET":
                if derivation and asset.info.get("asset_derivation", {}).get("schema_version") != "R3_A_DERIVATION_V1":
                    raise DomainError("V2_DERIVATION_INVALID", "派生记录缺少明确版本", 409)
            elif derivation:
                lineage(db, asset)
                for role in ("source", "root", "color_source"):
                    relations.append(Relation(role=role.upper(), target=snapshot_ref(getattr(derivation, role).model_dump()), recorded_in=f"Asset.info.asset_derivation.{role}"))
                for companion in derivation.companions:
                    relations.append(Relation(role=companion.role, target=snapshot_ref(companion.model_dump(exclude={"role"})), recorded_in="Asset.info.asset_derivation.companions"))
            for field, role in (("parent_id", "RECORDED_PARENT"), ("input_asset_id", "RECORDED_INPUT")):
                target_id = getattr(asset, field)
                if not target_id:
                    continue
                target = owned(db, Asset, target_id, workspace)
                relations.append(Relation(role=role, target=asset_ref(target), recorded_in=f"Asset.{field}"))
            if asset.master_id:
                master = owned(db, MasterVersion, asset.master_id, workspace)
                owned(db, Product, master.product_id, workspace)
                relations.append(Relation(role="RECORDED_MASTER", target=master_ref(master), recorded_in="Asset.master_id"))
            manufacturing, size = engineering_records(db, workspace, asset, derivation)
        except:
            pass

def product_detail(db, workspace, object_id, cursor, limit):
    product = owned(db, Product, identity(object_id), workspace); current = None
    if product.current_master_id:
        master = owned(db, MasterVersion, product.current_master_id, workspace)
        if master.product_id != product.id:
            raise DomainError("V2_PRODUCT_VERSION_CONFLICT", "当前版本不属于该产品", 409)
        current = master_ref(master)
    
    rows, next_cursor = page_rows(db, select(MasterVersion).where(MasterVersion.workspace_id == workspace, MasterVersion.product_id == product.id), MasterVersion, cursor, limit); versions = []
    
    for master in rows:
        previous = None
        seen = set()
        node = master
        if node.previous_id:
            if node.id in seen or len(seen) >= 256:
                raise DomainError("V2_PRODUCT_VERSION_CONFLICT", "产品版本链循环或超过读取范围", 409)
            seen.add(node.id)
            node = owned(db, MasterVersion, node.previous_id, workspace)
            if node.product_id != product.id:
                raise DomainError("V2_PRODUCT_VERSION_CONFLICT", "前序版本不属于该产品", 409)
            elif node.previous_id:
                pass
        elif master.previous_id:
            prior = owned(db, MasterVersion, master.previous_id, workspace)
            if prior.product_id != product.id or prior.id == master.id:
                raise DomainError("V2_PRODUCT_VERSION_CONFLICT", "前序版本不属于该产品或存在循环", 409)
            previous = master_ref(prior)
        versions.append(ProductVersionView(source_ref=master_ref(master), previous_version=previous, current=master.id == product.current_master_id, approved=master.approved, asset=asset_detail(db, workspace, master.asset_id)))
    return ProductView(current_version=current, versions=versions, next_cursor=next_cursor)
