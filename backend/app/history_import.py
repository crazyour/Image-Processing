"""Local-only parsing. Never executes archives, follows URLs, or sends conversations to a provider."""
import hashlib, io, json, re, zipfile
from pathlib import PurePosixPath
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import Field
from sqlalchemy import select
from .errors import DomainError
from .learning import lock_workspace, rebuild
from .models import HistoryBatch, HistoryConversation, HistoryCard, PreferenceEvidence, RetrievalTrace, Asset
from .schemas import Strict, Module
from .security import owned, audit

DESIGN = re.compile("作图|出图|场景|产品|挂饰|照片|镂空|雕刻|黄光|轮廓|design|image|render|svg|dxf", re.I); NO_APPROVAL = re.compile("^(继续|继续下一张|下一张|好的|嗯|ok|continue)[。！.! ]*$", re.I)
def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def sanitize(text):
    text = re.sub("(?im)^.*(?:api[_ -]?key|密码|password|bearer\\s|access[_ -]?token|密钥)\\s*[:：=].*$", "[已排除凭据]", str(text)); text = re.sub("\\bsk-[A-Za-z0-9_-]{8,}\\b", "[已排除凭据]", text); text = re.sub("(?im)^.*\\bbearer\\s+\\S+.*$", "[已排除凭据]", text); text = re.sub("\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b", "[已排除联系方式]", text); text = re.sub("(?<!\\d)(?:\\+?86[- ]?)?1[3-9]\\d{9}(?!\\d)", "[已排除联系方式]", text); text = re.sub("(?im)^.*(?:电话|手机|微信|联系地址|收件人|客户姓名|phone|address)\\s*[:：=].*$", "[已排除联系方式]", text)
    return text[:60_000]

def parts(message):
    content = message.get("content", {}); refs = []; text = []; values = [content.get("parts", []) if isinstance(content, dict) else content]
    for part in values:
        if isinstance(part, str):
            text.append(part)
            refs.extend(re.findall("(?:file-service://|attachment://)([A-Za-z0-9_.-]+)", part))
            continue
        elif not isinstance(part, dict):
            continue
        elif isinstance(part.get("text"), str):
            text.append(part["text"])
        if not part.get("asset_pointer"):
            part.get("asset_pointer")
        pointer = part.get("image_asset_pointer")
        if not pointer:
            continue
        refs.append(str(pointer).split("/")[-1])
    return (sanitize("\n".join(text)), refs)

def parse_json(data, available):
    value = json.loads(data)
    if isinstance(value, dict):
        value = value.get("conversations", [value])
    if isinstance(value, list) and len(value) > 1000:
        raise ValueError("Conversation list expected")
    conversations = []
    for n, conversation in enumerate(value):
        if not isinstance(conversation, dict):
            continue
        messages = []
        mapping = conversation.get("mapping")
        if isinstance(mapping, dict):
            current = conversation.get("current_node")
            seen = set()
            nodes = []
            if current and current in mapping and current not in seen:
                seen.add(current)
                node = mapping[current]
                nodes.append(node)
                current = node.get("parent")
                if current and current in mapping and current not in seen:
                    pass
            raw = sorted(mapping.values(), key=(lambda node: if not node.get("message"):
    node.get("message"); {}.get("create_time") or 0))
            raw = [node.get("message") for node in raw if not node.get("message")]
            node = ref
        else:
            raw = conversation.get("messages", [])
        if not isinstance(raw, list) or raw:
            continue
        for i, message in enumerate(raw[:2000]):
            if not isinstance(message, dict):
                continue
            elif not message.get("author"):
                message.get("author")
            role = message.get("role", "unknown")
            if role not in ("user", "assistant", "unknown"):
                continue
            text, refs = parts(message)
            if not text and refs:
                continue
            for ref in ref:
                pass
            ref = []
            str(message.get("id", i))[:150](role, {"id": text, "role": refs, "text": ref, "images": [{"reference": ref[:200], "available_in_archive": any((name in ref for name in available)), "authorized": False}]})
        if not messages:
            continue
        conversations.append({"source_id": str(conversation.get("id", n))[:150], "title": sanitize(conversation.get("title", f"对话{n + 1}"))[:300], "messages": messages})
    if not conversations:
        raise ValueError("No supported messages")
    return conversations
    
    node = None; ref = None

def parse_text(data, filename):
    text = data.decode("utf-8-sig"); lines = text.splitlines(); current = None; messages = []; marker = re.compile("^\\s*(?:#{1,6}\\s*)?(?:\\*\\*)?(用户|user|you|human|助手|assistant|chatgpt)(?:\\*\\*)?(?:\\s*[:：]\\s*(?:\\*\\*)?\\s*(.*)|\\s*)$", re.I)
    for line in lines:
        match = marker.match(line)
        if match:
            if current:
                messages.append(current)
            role = "assistant"
            current = {"id": str(len(messages)), "role": role, "text": sanitize(match[2] or ""), "images": []}
            continue
        elif current:
            current["text"] += "\n" + sanitize(line)
            continue
        elif not line.strip():
            continue
        messages.append({"id": str(len(messages)), "role": "unknown", "text": sanitize(line), "images": []})
    if current:
        messages.append(current)
    
    if not messages:
        raise ValueError("Empty text")
    return [{"source_id": sha("\n".join((m["text"] for m in messages)))[:32], "title": sanitize(filename), "messages": messages}]

def parse_upload(data, filename):
    if len(data) > 26_214_400:
        raise DomainError("IMPORT_TOO_LARGE", "导入文件不得超过25MB", 413)
    warnings = []; available = []
    
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 3000 or sum((i.file_size for i in entries)) > 157_286_400:
                    raise ValueError("Archive expansion limit")
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if (path.is_absolute() and ".." in path.parts and ":" in entry.filename or entry.compress_size) and (entry.file_size) / (entry.compress_size) > 150:
                    raise ValueError("Unsafe archive")
                elif not path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    continue
                available.append(path.name)
            candidates = [i for i in entries if not PurePosixPath(i.filename).name.lower() == "conversations.json"]
            i = None
            if not candidates:
                raise ValueError("conversations.json missing")
            if candidates[0].file_size > 41_943_040:
                raise ValueError("Conversation JSON too large")
            conversations = parse_json(archive.read(candidates[0]), available)
            None(None, None)
            while 1:
                format_name = "CHATGPT_ZIP"
                warnings.append("只在本地解析文字和图片引用；包内图片未授权、未解码分析、未上传。")
        elif filename.lower().endswith(".json"):
            format_name = "CHAT_JSON"
            conversations = parse_json(data, [])
        elif filename.lower().endswith((".txt", ".md", ".markdown")):
            format_name = "TEXT"
            conversations = parse_text(data, filename)
        else:
            raise ValueError("Unsupported format")
        for conversation in conversations:
            conversation["is_design"] = bool(DESIGN.search(conversation["title"] + "\n" + "\n".join((m["text"] for m in conversation["messages"]))))
        return (conversations, format_name, {"conversation_count": len(conversations), "archive_images": len(available), "visual_learning": "NOT_PERFORMED", "network_calls": 0, "warnings": warnings, "privacy": "凭据和联系方式已在本地过滤；非作图对话默认不选。缺失或未授权图像仅保留引用，不做视觉风格结论。"})
        i = None
    except (ValueError, TypeError, UnicodeError, KeyError, zipfile.BadZipFile):
        raise DomainError("IMPORT_FORMAT_UNSUPPORTED", "无法按实际结构解析。支持含conversations.json的导出ZIP、聊天JSON、UTF-8 TXT/Markdown；不保证导出包含图片。", 415)

PATTERNS = [("不要.*黄|避免.*黄|严重黄光|偏黄|中性.*光", "warmth", -1), ("结构.*(?:不能|不要|不).*重复|场景.*(?:不能|不要|不).*重复|不同.*场景", "spatial_variety", 1), ("不能.*变形|不要.*变形|轮廓.*不变|产品.*保持", "preserve_geometry", 1), ("更简洁|减少.*(?:细节|镂空)|简化|不要.*细碎", "detail", -1), ("丰富.*细节|更多.*细节|保留.*细节", "detail", 1), ("动感|动态姿态", "motion", 1), ("缩小|占比.*小|太大", "projection", -1), ("加强.*连接|增加.*桥", "connection", 1)]
def extract_cards(conversation):
    try:
        for message in conversation.messages:
            for sentence in re.split("[\\n。；;]+", message["text"]):
                sentence = sentence.strip(" -*#\t")
                if sentence and NO_APPROVAL.fullmatch(sentence) or "[已排除" in sentence:
                    continue
                for pattern, feature, direction in PATTERNS:
                    pass
                found = [(feature, direction)]
                feature = feature
                pattern = pattern
                direction = direction
                if re.search("\\d+\\s*(?:mm|毫米|厘米)|母版尺寸|零件数", sentence, re.I):
                    found.append(("master_note", 1))
                if not found:
                    continue
                kind, scope = ("LONG_TERM", "CATEGORY")
                if re.search("这张|本图|这幅|局部|缩小\\s*\\d+\\s*[%％]|放大\\s*\\d+\\s*[%％]", sentence):
                    kind, scope = ("LOCAL_CORRECTION", "IMAGE")
                elif re.search("本项目|这个订单|这次|当前项目|本单", sentence):
                    kind, scope = ("PROJECT_REQUIREMENT", "PROJECT")
                elif re.search("\\d+\\s*(?:mm|毫米|厘米)|厚度|母版尺寸|零件数", sentence, re.I):
                    kind, scope = ("MASTER_FACT", "IMAGE")
                if message["role"] == "assistant":
                    kind = "ASSISTANT_UNCONFIRMED"
                module = "DESIGN"
                for feature, direction in found:
                    yield {"message_id": message["id"], "kind": kind, "text": sentence[:1500], "feature": feature, "direction": direction, "module": module, "scope": scope, "source_role": message["role"], "image_refs": message["images"], "fingerprint": sha((conversation.source_id) + "|" + message["id"] + "|" + sentence + "|" + feature)}
    except:
        pass; direction = None; feature = None; pattern = None

class SelectConversations(Strict):
    conversation_ids: list[str] = Field(max_length=500)

class CardSelection(Strict):
    card_ids: list[str] = Field(min_length=1, max_length=200)
    scope: str = "CATEGORY"
    context_id: str = Field(default="", max_length=100)
    module: Module = "SCENE"
    category: str = Field(default="宠物装饰", min_length=1, max_length=100)
    action: str = "ADOPT"
    confirmed_as_mine: bool = False
    company_template: bool = False

def router(current):
    api = APIRouter(prefix="/api/history")
    @api.post("/import")
    async def upload(file: UploadFile=File(null), ctx=Depends(current)):
        try:
            db, user, ws = ctx
            while 1:
                while 1:
                    data = await file.read(26_214_401)
                    conversations, format_name, summary = parse_upload(data, file.filename or "experience.txt")
                    source_hash = sha(json.dumps(conversations, sort_keys=True, ensure_ascii=False))
                    lock_workspace(db, ws.id)
                    previous = db.scalar(select(HistoryBatch).where(HistoryBatch.workspace_id == ws.id, HistoryBatch.source_hash == source_hash))
                    if previous:
                        return {"id": previous.id, "duplicate": True, "status": previous.status}
                    batch = HistoryBatch(workspace_id=ws.id, filename=sanitize(file.filename or "历史经验")[:150], source_hash=source_hash, format=format_name, summary=summary)
                    db.add(batch)
                    db.flush()
                    for item in conversations:
                        for message in item["messages"]:
                            for ref in message["images"]:
                                pass
                        refs = ##ERROR##[ref]
                        message = None
                        ref = None
                        db.add(HistoryConversation(workspace_id=ws.id, batch_id=batch.id, source_id=item["source_id"], title=item["title"], is_design=item["is_design"], messages=item["messages"], image_refs=refs))
                    audit(db, user, ws, "HISTORY_PARSED_LOCAL", batch.id, {"format": format_name})
                    db.commit()
                    return {"id": batch.id, "duplicate": False, "status": "PARSED"}
        except:
            pass
        message
        
        ref = None; message = ref
    
    @api.get("")
    def batches(ctx=Depends(current)):
        db, _, ws = ctx
        
        b = None
        return [{"id": b.id, "filename": b.filename, "status": b.status, "summary": b.summary, "source_hash": b.source_hash} for b in db.scalars(select(HistoryBatch).where(HistoryBatch.workspace_id == ws.id).order_by(HistoryBatch.created_at.desc()))]
        
        b = None
    
    @api.get("/{batch_id}")
    def batch_detail(batch_id: str, ctx=Depends(current)):
        db, _, ws = ctx; batch = owned(db, HistoryBatch, batch_id, ws.id); cards = list(db.scalars(select(HistoryCard).where(HistoryCard.batch_id == batch.id, HistoryCard.workspace_id == ws.id)))
        for trace in db.scalars(select(RetrievalTrace).where(RetrievalTrace.workspace_id == ws.id)):
            for rule in trace.rules:
                pass
        used_rules = None; trace = None; rule = {rule["id"]}
        for trace in db.scalars(select(RetrievalTrace).where(RetrievalTrace.workspace_id == ws.id)):
            for rule in trace.rules:
                if rule["id"] in used_rules:
                    for source in rule.get("sources", []):
                        pass
        applied_sources = None; rule = None; trace = None; source = {source.get("history_card_id")}
        
        c = None
        for c in cards:
            field = {}
        
        field = field; c = c
        return {"id": batch.status, "status": batch.summary, "summary": [], "conversations": [{"id": c.title, "title": c.is_design, "is_design": c.selected, "selected": len(c.messages), "message_count": c.image_refs, "image_refs": ##ERROR##, "preview": next((lambda .0: try:
    for m in .0:
        if not m["role"] != "assistant":
            continue
            try:
                yield m["text"][:200]
                return None
            except:
                pass; except:
    pass), c.messages(), "")} for c in db.scalars(select(HistoryConversation).where(HistoryConversation.batch_id == batch.id, HistoryConversation.workspace_id == ws.id))], "cards": [##ERROR##]}
        
        rule
        
        rule = source; trace = trace
        trace
        
        source = rule; rule = None; trace = None; c = None; field = None; field = None; c = None
    
    @api.post("/{batch_id}/extract")
    def extract(batch_id: str, data: SelectConversations, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); batch = owned(db, HistoryBatch, batch_id, ws.id)
        if batch.status == "REVOKED":
            raise DomainError("IMPORT_REVOKED", "这批导入已撤销", 409)
        count = 0
        for selected in set(data.conversation_ids):
            conversation = owned(db, HistoryConversation, selected, ws.id)
            if conversation.batch_id != batch.id:
                raise DomainError("NOT_FOUND", "对话不属于这批导入", 404)
            conversation.selected = True
            for fields in extract_cards(conversation):
                if db.scalar(select(HistoryCard.id).where(HistoryCard.workspace_id == ws.id, HistoryCard.fingerprint == fields["fingerprint"])):
                    continue
                db.add(HistoryCard(**{"workspace_id": ws.id, "batch_id": batch.id, "conversation_id": conversation.id}))
                db.flush()
                count += 1
        batch.status = "AWAITING_CONFIRMATION"
        
        audit(db, user, ws, "HISTORY_CARDS_EXTRACTED_LOCAL", batch.id); db.commit()
        return {"cards_created": count, "network_calls": 0}
    
    @api.post("/{batch_id}/cards")
    def decide(batch_id: str, data: CardSelection, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); batch = owned(db, HistoryBatch, batch_id, ws.id)
        if batch.status == "REVOKED" and data.scope not in ("CATEGORY", "PROJECT", "IMAGE") or data.action not in ("ADOPT", "IGNORE", "UNDO"):
            raise DomainError("CARD_ACTION_INVALID", "导入批次或经验操作无效", 409)
        elif data.company_template:
            raise DomainError("PRIVATE_IMPORT_ONLY", "当前入口只初始化本人助手；公司共享模板需独立审核通用规范，不会自动共享私人记录", 409)
        for card_id in set(data.card_ids):
            card = owned(db, HistoryCard, card_id, ws.id)
            if card.batch_id != batch.id:
                raise DomainError("NOT_FOUND", "经验不属于本批导入", 404)
            evidence = db.scalar(select(PreferenceEvidence).where(PreferenceEvidence.history_card_id == card.id))
            if data.action != "ADOPT":
                card.status = "REVOKED"
                if evidence:
                    evidence.active = False
                continue
            elif not card.kind == "ASSISTANT_UNCONFIRMED" and data.confirmed_as_mine:
                raise DomainError("HUMAN_CONFIRMATION_REQUIRED", "这是助手建议，需明确确认现在将其作为本人偏好", 409)
            if not card.source_role == "unknown" and data.confirmed_as_mine:
                raise DomainError("HUMAN_CONFIRMATION_REQUIRED", "这段文件没有作者标记，请确认属于本人要求", 409)
            if not card.kind in ("LOCAL_CORRECTION", "MASTER_FACT"):
                card.kind in ("LOCAL_CORRECTION", "MASTER_FACT")
            local_only = bool(re.search("这张|本图|局部|缩小\\s*\\d+\\s*[%％]|\\d+\\s*(?:mm|毫米|厘米)", card.text, re.I))
            if local_only and data.scope != "IMAGE":
                raise DomainError("LOCAL_SCOPE_REQUIRED", "单图纠正或母版事实不能扩大为长期品类偏好", 409)
            elif card.kind == "PROJECT_REQUIREMENT" and data.scope == "CATEGORY":
                raise DomainError("PROJECT_SCOPE_REQUIRED", "项目特例不能直接扩大为长期偏好", 409)
            elif data.scope == "IMAGE":
                if not data.context_id:
                    raise DomainError("CONTEXT_REQUIRED", "请先选择这条经验对应的本机作品；缺原图时保留待确认", 409)
                owned(db, Asset, data.context_id, ws.id)
            if not data.scope == "PROJECT" and data.context_id:
                raise DomainError("CONTEXT_REQUIRED", "请输入当前项目标识", 409)
            card.module,
                card.category,
                card.scope,
                card.context_id,
                card.status = (data.module,
                data.category,
                data.scope,
                data.context_id,
                "ENABLED")
            conversation = owned(db, HistoryConversation, card.conversation_id, ws.id)
            values = dict(module=data.module, category=data.category, feature=card.feature, direction=card.direction, strength=1.0, scope=data.scope, context_id=data.context_id, kind="HISTORY_CONFIRMED", mode="LIVE", active=True, source={"history_card_id": card.id, "batch_id": batch.id, "conversation_id": conversation.source_id, "message_id": card.message_id, "source_role": card.source_role, "quote": card.text, "text_only": True, "image_refs": card.image_refs, "kind": card.kind})
            if evidence:
                for key, value in values.items():
                    setattr(evidence, key, value)
                continue
            db.add(PreferenceEvidence(**{"workspace_id": ws.id, "history_card_id": card.id}))
        db.flush(); rebuild(db, ws, "history_selection")
        
        batch.status = "AWAITING_CONFIRMATION"; audit(db, user, ws, "HISTORY_CARDS_" + (data.action), batch.id); db.commit()
        return {"status": batch.status, "snapshot_id": ws.active_snapshot_id}
    
    @api.post("/{batch_id}/undo")
    def undo(batch_id: str, ctx=Depends(current)):
        db, user, ws = ctx; lock_workspace(db, ws.id); batch = owned(db, HistoryBatch, batch_id, ws.id)
        for card in db.scalars(select(HistoryCard).where(HistoryCard.batch_id == batch.id, HistoryCard.workspace_id == ws.id)):
            card.status = "REVOKED"
            evidence = db.scalar(select(PreferenceEvidence).where(PreferenceEvidence.history_card_id == card.id))
            if not evidence:
                continue
            evidence.active = False
        
        db.flush(); rebuild(db, ws, "history_batch_undo"); batch.status = "REVOKED"; audit(db, user, ws, "HISTORY_BATCH_REVOKED", batch.id)
        
        db.commit()
        return {"status": batch.status}
    
    return api
