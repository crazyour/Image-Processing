# LingJie V2 Python 源码恢复包

本目录从 `LingJie-V2-Final-20261001-r6.exe` 中静态提取并整理，未执行原安装包。

## 目录内容

- `backend/app/`：从 PyInstaller Python 3.12 字节码反编译得到的 125 个业务模块。
- `backend/migrations/`：安装包原样包含的 Alembic 数据库迁移源码。
- `desktop/launcher.decompiled.py`：桌面启动器的近似反编译结果。
- `recovery_evidence/bytecode/`：125 个原始 `.pyc`，供后续重新反编译和核对。
- `recovery_evidence/disassembly/`：重点失败模块的字节码反汇编。
- `COMPILE_ERRORS.txt`：当前未通过语法检查的模块和错误位置。

## 当前恢复程度

- 125 个后端业务模块均已生成对应 `.py` 文件。
- 68 个模块通过 Python 语法编译检查。
- 57 个模块仍需要依据 `.pyc`/反汇编进行人工修复。
- 自动反编译会丢失原注释、排版，并可能错误重排控制流；通过语法检查不代表与原程序语义完全一致。
- React 前端只能从已经压缩的 JavaScript 构建产物重建，不能无损转换成 `.py`。

## Vercel 集成版

仓库根目录包含一个可部署到 Vercel 的集成应用：

- `app.py`：Vercel 自动识别的 FastAPI 入口；
- `vercel_app/`：OpenAI、腾讯混元、Meshy、PostgreSQL 和 Vercel Blob 工作流；
- `frontend_dist/`：轻量功能选择首页及其公共静态资源；
- `image3d_frontend/`：图生 3D 工作流，由原灵界侧边栏入口在站内弹层中打开；
- `text2image_frontend/`：云端文生图工作流，支持单个场景生成 1–5 张候选图，或添加最多 5 个场景各生成一张；
- `similarity_search_frontend/`：图片、DXF、Illustrator 同格式相似素材检索；
- `requirements.txt`、`vercel.json`：Python 依赖和 5 分钟函数时限；
- `.vercelignore`：部署时排除旧后端、字节码和恢复证据。

网站首页是精简的功能选择页。文生图、图生 3D 和相似素材检索使用独立前端；原工作台中的创意、照片定制、产品、配色工艺、视觉、SVG、DXF、文件和交付功能通过 `/tools/*` 独立入口继续提供。旧的完整工作台入口和侧边栏式导航不再公开。

## 相似素材检索

左侧“相似素材检索”入口打开独立素材库。一个顶层文件夹是一条素材记录，必须至少包含一张
PNG/JPG/WebP 图片、一个 ASCII DXF 和一个启用了“创建 PDF 兼容文件”的 Illustrator `.ai`。
上传后系统为三类文件生成统一 PNG 预览和特征向量，并生成保留相对路径的 ZIP 下载包。

检索时上传图片、DXF 或 AI 中任意一种，系统只比较同格式文件。图片只使用保留内部镂空的主体 Mask 计算 Shape IoU，返回匹配度最高的前 20 张，并把其中第 1 名在上方单独展示；DXF 和 AI 保持特征余弦相似度前五项：

- 图片：归一化为 `96×96` 主体 Mask，保留环形、字母孔洞等内部空缺，只按 Mask 像素交并比（Shape IoU）排序；
- DXF：实体统计、外接尺寸比例和标准渲染图特征；
- AI：读取其 PDF 兼容内容，渲染首页后提取视觉特征。

当前版本不为每个文件建立新数据库，而是把全部特征记录保存在同一 PostgreSQL/SQLite 素材库中。
相似度用于排序，不代表识别准确率。素材和查询文件会在浏览器中自动分片上传，不再设置 4 MB 的
应用层文件大小限制；二进制 DXF 和不含 PDF 兼容内容的 AI 会被明确拒绝。实际可处理大小仍受
Vercel 函数内存、执行时长以及 Vercel Blob 平台配额限制。

云端原文件、预览和 ZIP 必须放在独立的 **Private Vercel Blob** 中。所有读取均经过素材库令牌校验，
不会把 Blob 地址直接发送给浏览器。本地开发时则写入 `var/material-private/`。

需要把线上素材库带到本机时，运行：

```bash
python scripts/sync_material_library.py --base-url https://你的生产域名
```

脚本通过网站已有登录与下载接口同步原文件、预览图、ZIP，并从配置的 PostgreSQL 复制检索特征到
`var/material-library/`。下载支持断点续传式跳过已校验文件。该目录存在后，普通的
`uvicorn app:app --reload` 会让相似素材检索自动优先使用本地副本；其他业务仍可继续使用原有数据库。
可用 `LOCAL_MATERIAL_LIBRARY_DIR` 指定另一个本地素材包目录。

云端文件夹删除后先进入回收站，并立即从普通列表、下载和相似检索中隐藏。30 天内可以恢复，也可以
手动永久删除；打开素材列表或回收站时会自动清理所有超过 30 天的项目。回收站保留期间原文件、预览
和 ZIP 仍保存在 Blob 中并占用存储容量，只有永久删除后才会释放对应存储。

## 登录与管理员网站

Vercel 集成版要求所有业务功能先登录。登录页不提供注册入口，新账号只能由管理员在 `/admin` 创建。

首次部署前在环境变量中设置：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置管理员密码
ADMIN_NAME=系统管理员
```

账号表为空时，服务会使用这些变量创建首个管理员；已有账号后不会用环境变量覆盖密码。登录成功后直接进入主工作室的功能列表页，右上角显示当前账号，管理员可从同一处进入“账号管理”，修改账号、显示名称、密码、角色、启停状态，并分别授权文生图、照片转镂空剪影、图生 3D、相似素材检索和成果素材库。管理员始终拥有全部业务功能，系统禁止停用或降级最后一个有效管理员。

管理员在账号列表里可以删除账号：删除后该账号立即退出登录且无法再登录，已有使用额记录会保留用于对账；不能删除当前登录的账号，也不能删除最后一个有效管理员。成员可以在功能列表页右上角点“修改密码”自助修改，需要输入当前密码，修改后当前浏览器保持登录、其它设备会被强制下线。

主工作台（工艺设计与交付的各个工作室）对所有账号默认开放，不需要单独授权，也不能被关闭。

## 使用额统计

每次调用付费接口（文生图、照片转镂空剪影、图生 3D 的 AI 分析 / 四视图 / 微调 / 混元 STL）都会写入 `auth_usage_events` 表，记录账号、功能、模型、token 数和美元花费。管理员在 `/admin` 的账号列表里能看到每个账号的使用额，点“使用额”按钮会打开该账号的明细弹窗（各功能的调用次数与费用，以及按模型、按计价方式的拆分）。

花费优先使用接口响应里返回的价格：`usage` 块里或响应顶层的 `cost`、`total_cost`、`price`、`amount` 等字段都支持，嵌套写法（如 `usage.cost.total`）也能识别。抓到的是哪个字段会一并记在 `cost_field` 里，并在明细弹窗的“计价方式”中显示（例如“接口返回（usage.cost）”），便于核对。

接口没有返回价格时，按“token × 单价”计算，token 数与花费一一对应，单价表见 `vercel_app/pricing.py`，默认取 OpenAI 官方价目（https://platform.openai.com/docs/pricing）：GPT Image 家族按“文本输入 $5/1M、图像输入 $8/1M、缓存输入 $2/1M、输出 $30/1M”计算，参考图按图像输入价计费；视觉/文本模型按各自的每百万 token 单价计算。`USAGE_PRICE_IMAGE_TEXT_INPUT_PER_MILLION`、`USAGE_PRICE_IMAGE_INPUT_PER_MILLION`、`USAGE_PRICE_IMAGE_OUTPUT_PER_MILLION`、`USAGE_PRICE_CACHED_INPUT_PER_MILLION`、`USAGE_PRICE_VISION_INPUT_PER_MILLION`、`USAGE_PRICE_VISION_OUTPUT_PER_MILLION` 可覆盖这些单价，留空即使用官方价。只有完全没有 token 的调用才退回固定单价：腾讯混元与 Meshy 3D 按 `USAGE_PRICE_3D_PER_TASK` 计费，图片按 `USAGE_PRICE_IMAGE_PER_IMAGE` 兜底。

管理员页同时显示输入、输出和总 Token，即使单价未配置也能看到用量。每条记录还会保存接口返回的原始 `usage` 内容（`raw` 列），方便事后核对。升级到按 token 计费后，系统会自动用同一套单价重算历史记录里“token 与花费对不上”的行（接口自带价格的记录保持不变），因此管理员页上的花费会与 token 对得上。没有 token 消耗的本地功能（相似素材检索、主工作台里的本地工具）不计入统计。

管理员可在每个账号的“额度控制”弹窗中设置每日、每周和每月的美元上限；留空表示不限额，`0` 表示禁止新的付费 AI 请求。系统在调用供应商之前先预占预估费用，因此并发请求也不会轻易穿透限额；调用成功后再用实际费用结算。默认按 `Asia/Tokyo` 的自然日、周一起始的自然周和自然月计算，可用 `USAGE_LIMIT_TIMEZONE` 修改时区。“重置”只改变当前周期的计算起点，不删除历史明细。未知单价的视觉调用默认预占 `$0.10`，可通过 `USAGE_RESERVE_VISION_PER_CALL_USD` 调整。

可以用下面的命令验证某个接口返回会被记成多少花费（带上模型名可以查到对应单价）：

```bash
echo '{"usage":{"total_tokens":1200}}' | python -m vercel_app.usage gpt-image-2.5-flare
```

未开通的功能不会隐藏，而是在功能列表页显示为灰色并带锁图标，点击后提示联系管理员；直接请求对应地址依然会被后端拦截。

密码使用带随机盐的 `scrypt` 哈希保存，登录状态使用 HttpOnly、SameSite=Strict Cookie。修改密码、角色或停用账号会撤销该账号的旧会话；功能权限同时在页面入口和后端 API 校验，不能通过直接请求绕过。

## 成果素材库

`/file-upload` 分成「素材展示」和「上传素材」两个功能。素材文件放在云端 Vercel Blob，所有登录成员都能浏览彼此上传的成果。

上传时需要选择文件并标注业务类型（文生图、照片转镂空剪影、图生 3D、设计与工程文件、其他成果），上传者由当前登录账号自动记录，不需要手填。展示页先按上传账号分组，每个账号下面再按业务类型分组，另外提供账号和业务类型两个筛选器。素材的归属信息保存在 `deliverables` 表里（与账号表同一个数据库），文件本体在 Blob 中；没有 `asset_library` 权限的账号不能上传。

## 文生图流程

左侧“文生图”入口打开独立云端工作区。用户填写一个场景的图片要求，选择生成 1–5 张候选图；也可点击“添加一个场景”，分别填写最多 5 个场景，每个场景各生成一张独立图片，共享本次上传的产品图和生成样例。多个场景时显示场景数和总图片数，删除至一个场景后恢复候选数量选择。系统按队列逐张调用图片生成接口，每张完成后立即转存到 Vercel Blob；任务状态、提示词、用量和文件地址保存在 PostgreSQL。单张失败不会清除已成功结果，可在对应卡片上单独重试。浏览器只在当前标签页保存任务 ID 和访问令牌，刷新后可以继续当前任务；本机存储模式还可从“作品记录”重新打开。

同一页面的“照片转镂空剪影”支持上传一张 PNG、JPEG 或 WebP 照片，主体可以是人物、动物、植物、器物、车辆、建筑、风景或组合场景。用户可选择一次生成 1–5 张纯黑白平面镂空候选，然后选中其中一张；只有所选候选会进入本地 OpenCV 连通性检测和后续 AI 局部修复。页面从服务端读取并显示通用默认提示词，用户可以修改、恢复默认值，或不修改直接生成。

每张候选图生成后都会由本地 OpenCV 自动检测黑色连通域，并生成“主体为蓝色、全部孤岛或薄弱区域统一为红色”的彩色连通图。红蓝图只用于页面诊断，不会发送给 AI。只有检测不通过时，系统才使用独立配置的修复模型（默认 `gpt-image-2.5-sunburst`、`high`），提交当前黑白图、局部透明蒙版和修复提示词。蒙版仅开放问题区及其到主体之间的短连接走廊，要求模型在局部自然连接孤岛并尽量保持其他区域不变。为避免误删眼睛、花纹、孔位或其他有效结构，本地检测只做诊断，不会自动删除任何孤岛。AI 修复后会再次本地复检，自动修复最多两次；仍未通过时会显示剩余问题数量，并允许继续手动修复或恢复首次生成图。页面按三个固定模块展示：第 01 部分上传与设置，第 02 部分展示首次生成图、生成提示词和本地诊断图片，第 03 部分展示修复模型、提示词、每轮蒙版、修复结果及复检记录。生成图仍需人工检查和后续矢量化，不代表已经通过实际材料、尺寸、刀宽或强度验证。

## 图生 3D 流程

面向普通用户的操作步骤和故障排查见 [图生 3D 功能使用说明](IMAGE_TO_3D_USER_GUIDE.md)。

Vercel 应用支持 AI 生成或用户上传正、左、右、背四视图，并在提交 3D 前分阶段人工确认：

- 站内 AI 自动生成的四视图按实际 token 计费：文生图单张约 $0.01，带多张参考图的编辑单张约 $0.2~0.3，一次四视图约 $1；
- 也可先用豆包等网页 AI 生成四视图后自行上传，此时本站不收图片生成费（外部平台额度与费用以其规则为准）。

1. AI 模式上传 1–6 张同一对象的参考图；图片逐张上传，安全解码并统一为 PNG；
2. GPT 综合所有参考图输出可编辑的主体、结构、材质、遮挡与风险描述；用户必须先确认描述，才能生成四视图；
3. GPT Image 按正、左、右、背顺序串行生成；每次使用全部原始参考图，并加入此前已生成且当前有效、未被检查判错的视图；用户可单独微调任一视图；
4. 选择手动模式时，用户直接上传四张图，跳过 GPT 分析和四视图生成；
5. 用户确认四视图后，可选择国内模型腾讯混元 `hy-3d-3.1`，或默认的国外模型 Meshy `meshy-7.1`；
6. 固定请求 STL，查询异步任务，成功后把 STL 转存到 Vercel Blob，并提供旋转、缩放预览和下载。
7. 页面在 STL 保存后自动调用独立的结构检查接口；报告与生成成功分开显示。可手动生成低风险修复副本，保留原文件及修复前后报告。
8. 对曲面有块状感的 STL，可手动生成独立平滑副本，选择轻度或标准强度，并按需启用一级细分；支持原版 / 平滑版对照和分别下载。

复制 `.env.example` 为 `.env` 并配置：

```dotenv
OPENAI_API_KEY=你的OpenAI密钥
OPENAI_BASE_URL=https://api.openai.com/v1

HUNYUAN_API_KEY=你的TokenHub密钥
HUNYUAN_BASE_URL=https://tokenhub.tencentmaas.com/v1

MESHY_API_KEY=你的Meshy密钥
MESHY_BASE_URL=https://api.meshy.ai

BLOB_READ_WRITE_TOKEN=Vercel自动提供
MATERIAL_BLOB_READ_WRITE_TOKEN=素材专用Private Blob令牌
MATERIAL_LIBRARY_TOKEN=公共素材库令牌（可选，建议在Vercel中设置为长随机值）
DATABASE_URL=PostgreSQL连接字符串
LOCAL_DATA_DIR=可选的本地数据目录（默认为项目下的 var）
```

本地开发时可以留空 `BLOB_READ_WRITE_TOKEN` 和 `DATABASE_URL`。应用会自动改用
`var/image3d.sqlite3` 保存会话，并将图片与模型文件保存到 `var/blobs/`。这些文件已被
`.gitignore` 排除。本地图片可用于界面预览和 GPT 多视图流程，但腾讯混元与 Meshy 3D 都需要公网可访问的图片 URL，
因此提交 3D 任务前仍需配置 Public Vercel Blob。

本机存储模式提供“作品记录”入口，会从 SQLite 中列出文生图、照片转镂空剪影和图生 3D
任务。关闭标签页后，可以从作品记录重新签发任务访问令牌并恢复原进度；图片本体仍保存在
`var/blobs/`，不会写入浏览器存储。作品名称、收藏状态和个人生图偏好也保存在 SQLite 中。
个人偏好默认关闭，启用后只会锁定到之后新建的任务，并在对应记录中标明已应用。

### 摄影中心站

`/photography-center` 提供按文件夹授权的图片和视频库。管理员创建文件夹并选择允许访问的
普通成员；成员获得访问权后，可使用该文件夹中的预览、上传、单文件下载和批量下载。

上传采用 3 MB 分片避开 Vercel 单请求大小限制，因此摄影中心不受 4 MB 上传限制
（当前单文件上限为 200 MB）。分片合并后立即压缩并删除临时原文件：
图片最长边限制为 2560 像素并转为 WebP；视频使用 H.264 CRF 29 和 AAC 96 kbps 转为 MP4，
最高存储为 1080p。下载时使用 Lanczos 恢复到记录的原始宽高。这能恢复尺寸，但不能逐像素恢复
有损压缩已删除的细节。服务器会优先使用系统 FFmpeg，没有时使用 `imageio-ffmpeg` 附带的可执行文件。

供应商 API Key 和端点只从环境变量读取，不会发送到浏览器。图片理解、多视图生成和 3D 生成的模型名称以及图片质量在网页“模型设置”中配置，保存在当前浏览器并在新建会话时锁定。缺少 API Key 或端点时接口会返回 `NOT_CONFIGURED`。

## 部署到 Vercel

1. 将本目录提交到 GitHub，并在 Vercel 中导入仓库。
2. 在 Vercel 项目的 Storage 页面创建一个 **Public Blob**，Vercel 会自动写入 `BLOB_READ_WRITE_TOKEN`。
3. 再创建一个 **Private Blob**，在 Advanced Options 中把环境变量前缀设为 `MATERIAL_BLOB`，确保最终变量名为 `MATERIAL_BLOB_READ_WRITE_TOKEN`，不要覆盖原来的公开 Blob 令牌。
4. 通过 Vercel Marketplace 连接 Neon、Supabase 或其他 PostgreSQL，并设置 `DATABASE_URL`。
5. 按 `.env.example` 在项目的 Environment Variables 中配置 OpenAI/混元端点和 API Key；Blob 令牌与 `DATABASE_URL` 由对应存储集成提供。
6. 重新部署。首次请求时会自动建立图生 3D 和素材检索所需数据表。

本地运行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

主要接口：

```text
POST  /api/sessions
POST  /api/sessions/{id}/references
POST  /api/sessions/{id}/analyze
PATCH /api/sessions/{id}/prompt
POST  /api/sessions/{id}/description/confirm
POST  /api/sessions/{id}/views
POST  /api/sessions/{id}/views/{view}/upload
POST  /api/sessions/{id}/views/{view}/refine
POST  /api/sessions/{id}/confirm
POST  /api/sessions/{id}/generate
GET   /api/sessions/{id}/3d
GET   /api/sessions/{id}
POST  /api/text-image/tasks
GET   /api/text-image/tasks/{id}
POST  /api/text-image/tasks/{id}/reference
POST  /api/text-image/tasks/{id}/references
POST  /api/text-image/tasks/{id}/items/{index}/generate
POST  /api/animal-cutout/tasks
GET   /api/animal-cutout/tasks/{id}
POST  /api/animal-cutout/tasks/{id}/generate
POST  /api/animal-cutout/tasks/{id}/select
POST  /api/animal-cutout/tasks/{id}/repair
POST  /api/animal-cutout/tasks/{id}/restore-original
POST  /api/material-search/libraries
POST  /api/material-search/folders
POST  /api/material-search/folders/{id}/assets
POST  /api/material-search/folders/{id}/uploads/{upload_id}/chunks/{chunk_index}
POST  /api/material-search/folders/{id}/uploads/{upload_id}/complete
POST  /api/material-search/folders/{id}/finalize
GET   /api/material-search/folders
GET   /api/material-search/folders/{id}
DELETE /api/material-search/folders/{id}
GET   /api/material-search/trash
POST  /api/material-search/trash/{id}/restore
DELETE /api/material-search/trash/{id}
GET   /api/material-search/folders/{id}/download
GET   /api/material-search/assets/{id}/preview
GET   /api/material-search/assets/{id}/download
POST  /api/material-search/search
POST  /api/material-search/search/uploads/{upload_id}/chunks/{chunk_index}
POST  /api/material-search/search/uploads/{upload_id}/complete
```

浏览器只在当前标签页的 `sessionStorage` 中保存当前任务访问令牌，后续请求通过 `X-Session-Token` 发送；服务器数据库只保存令牌哈希。本机存储模式可通过“作品记录”重新打开任务并轮换令牌，云端模式不会开放本地作品库。上传图片会在浏览器中缩放并压缩到 4 MB 以下，以避开 Vercel Function 的 4.5 MB 请求体限制。

文生图参考输入分为“产品图片”和“生成样例图片”，每组最多 6 张。产品图片用于锁定产品主体、结构、材质和细节；生成样例只用于约束摆放位置、构图、视角和场景效果。浏览器会逐张上传，生成时后端按产品图在前、样例图在后的顺序提交，并在提示词中明确两组用途。

网页默认使用 `gpt-6-luna` 做图片结构分析，使用 `gpt-image-2.5-flare` 以 `medium` 质量生成正、左、右、背四视图，并默认使用国外模型 `meshy-7.1` 生成 3D；用户也可以切换到国内模型 `hy-3d-3.1`。GPT Image 2.5 按输入/输出 token 计费，编辑请求还会计算参考图片输入，因此单张没有固定价格；实际费用以接口返回的 `usage` 为准。

四视图提示词明确主体自身的左右、正交相机和原图实际视角。仍只生成四张图片，后续生成会额外参考前序结果。生成后通过独立的 `POST /api/sessions/{session_id}/views/review` 检查：本地比较解码后的像素和感知相似度；完全重复直接拦截，不调用 AI；否则将一张原始参考和四张结果缩到最多 512 像素，以 `detail=low` 做一次批量视觉检查，输出上限 900 token。相似度仅作为线索，固有对称不直接判为错误。检查结果按图片修订和提示词版本缓存；确认、保存和提交 3D 不再重复调用。检查通过后仍需人工确认细节，低分辨率检查不能保证精确几何或不可见面的真实性。

问题视角会显示原因，可单独重试并带入修正提示；不会自动追加生成。检查接口失败时图片保留，可以只重试检查。生成与微调都按正、左、右、背的固定顺序选用前序生成图；过期、模式不匹配或已被判错的图不作为参考，微调的最后一张输入仍为当前待修目标图。提示词逐张标记原始参考、前序生成视角和目标图，明确原始证据、主体要求与造型模式优先。前序图尚未经整套检查时仍可能传播错误，四图完成后必须统一检查及人工确认。自行上传四视图仍由用户人工确认。

网页通过 `POST /api/sessions/{session_id}/views` 的 `single_step: true` 逐张请求，每次提交剩余待生成视角，服务端先使这些旧视图失效，只生成固定顺序中的第一张并保存，返回 `pending_views`。中断后可按缺失或过期视图继续，不会自动重付已完成图片的生成费用。未指定 `single_step` 的批量请求同样串行执行、逐张保存，若剩余函数时间不足以安全启动下一次调用，会返回未完成的 `pending_views`，调用方需继续请求。任一生成失败立即停止后续视角；检查修正提示在分步请求间保留。串行等待时间和参考图输入 token 会增加，仍只生成四张，不保证精确几何一致。

描述确认、四视图确认和 STL 提交是独立门禁。修改上游内容会自动使后续确认失效。腾讯混元和 Meshy 任务都固定请求 STL，应用也只保存和展示 STL 输出。部分视图生成失败时会保留已成功结果；分析、图片生成或 3D 提交超时后会自动解除页面锁定并显示明确错误。

### 四视图造型模式

在第 02 步确认描述时，可选择默认的“忠实还原”或“打印雕塑（保留配色）”。打印雕塑只替换现有四视图提示词中的几何约束：保留原配色、材质表现、纹理、风格、主体身份和主要轮廓，允许整理细碎发束、减少穿插、适度加厚及连接薄弱细节，不转换成黑白、白模或黏土效果，也不添加底座/打印支撑。该模式是造型引导，不是打印认证，不改变 STL 不含颜色的输出限制。

仍沿用四张图和一次批量视角检查，参考图数量、图像模型与质量、检查输出上限均不变，只增加短文本约束。检查允许预期的局部调整，但仍检查主体/配色/姿势及四图布局一致性。没有新增效果预览图或付费调用轮次；切换本身不调用 AI，生成或重试仍按实际调用收费。

`PATCH /api/sessions/{session_id}/prompt` 接受可选的 `view_mode: faithful | print_sculpture`，省略则保留当前模式；旧会话默认 faithful。模式与描述原子保存。切换模式后增加提示词版本，旧图片保留但标记过期，同时清除描述确认、视角检查、四视图确认及下游任务关联。页面先要求应用模式并确认描述，再由用户点击重新生成四张，不能将旧模式图片直接提交 3D。微调、批量检查及确认快照均携带模式。手动上传四视图不应用此模式，生成进行中及 3D 已完成后不可切换。

### STL 基础结构检查与低风险修复

STL 保存后，图生 3D 页面会调用 `POST /api/sessions/{session_id}/3d/check`。检查在独立请求中执行，不拖延原文件保存；刷新页面可恢复未开始的检查，失败时仅手动重试检查，不重新生成模型。无需 GPU、额外服务或新依赖，使用现有 NumPy，不调用 AI、不增加模型 token；仍有函数 CPU、内存和 Blob 读取开销。接口沿用登录权限和会话令牌校验，只接受会话中已保存的模型地址。

检查二进制 / ASCII STL 的完整性、有限坐标、零面积面、重复面、开放边界边、非流形边、相邻面朝向和按边连接的部件数，并记录包围盒原始单位尺寸。顶点只按完全相同的坐标匹配，不焊接微小间隙；拓扑检查在排除重复及零面积面后进行。报告按文件 SHA-256 和检查器版本缓存，状态分为 `pending / passed / issues / invalid / incomplete`；`SUCCEEDED` 仅表示文件生成保存成功。开放边界边数不是孔洞数，多部件也不一定是错误。

`POST /api/sessions/{session_id}/3d/repair` 由用户点击触发，只去除完全重复面、严格零面积面，并在没有非流形边且朝向约束可解时统一相邻面朝向；不判断内外朝向，不补洞、重建或删除独立部件。不改变保留顶点的位置；高精度 ASCII 坐标不能无损转为二进制浮点数时拒绝导出。修复副本复检后以单独的内容哈希路径保存，原下载链接不变，仍存在的问题继续显示。尺寸调整导出的新文件不继承检查结论。

每文件上限 64 MiB、100 万三角形；计算采用 45 秒阶段间/循环内协作式时限（单次 NumPy 运算不可中断），同一 Python 进程最多一个网格计算。读取设置 30 秒网络操作超时及流式总时间检查；数据库任务锁 180 秒后可重试，重复请求不会覆盖进行中的结果。资源超限、下载失败、异常或内存不足均不得报通过，也不影响原文件下载。基础检查不包含自相交、非流形顶点、壁厚、支撑、形状真实性或可打印认证。

离线验证：`python -m unittest tests.test_mesh_quality`、`node --test tests/test_image3d_frontend.cjs`。资源测试：`python -m tests.benchmark_mesh_quality --grid 707 --repair`（999,698 面）；本地结果不能替代 Vercel 部署环境实测。

### STL 表面平滑副本

`POST /api/sessions/{session_id}/3d/smooth` 接受 `{"strength":"gentle","subdivide":false}`，由用户点击“生成平滑副本”触发。`strength` 支持默认轻度 `gentle` 和标准 `standard`；`subdivide` 可选一级三角形中点细分，然后平滑，面数约为原来的 4 倍。有与原件匹配且经过校验的结构修复副本时，优先读取修复副本；否则读取原始 STL，不在已有平滑结果上重复处理。使用现有 NumPy 执行有位移上限的 Taubin 顶点平滑，不调用生成模型、不增加 AI 计费；仍有计算、存储与下载开销。

平滑保护开放边界和明显锐边，限制累计位移、包围盒尺寸变化，以及可可靠计算的闭合部件体积变化。轻度使用 6 组正负步长迭代，累计位移不超过包围盒对角线的 0.25% 和局部最短边的 18% 中较小者；标准为 12 组、0.5% 和 25%。这些是保守初始阈值，实际效果需按模型验证，不能自动区分发丝与粗糙折角。存在重复面、退化面、朝向冲突或非流形结构时停止平滑，不自动补洞或删除部件。

仅在实际二进制 STL 的坐标、面朝向、面积、拓扑与尺寸复检通过后保存独立副本。`models.stl.url` 和基础检查报告保持原义；最新处理报告写入 `models.stl.smoothing`，最后成功副本保存在 `models.stl.smoothed`（含原件 original_sha256、实际输入 source_sha256、source_kind、算法版本、参数及前后报告）。重复相同参数可复用匹配副本，失败保留已有成功副本。状态为 `smoothed / unchanged / rejected / incomplete / invalid`；除 `smoothed` 外不产生新下载文件。

输入及输出限制 64 MiB、100 万面；细分超过上限时拒绝，不自动减面。计算预算为 90 秒协作式时限，与结构检查共享单进程网格计算槽位；平滑会话任务锁为 360 秒，检查 / 修复仍为 180 秒。超时或复检失败不影响原文件。页面预览会抽样显示，尺寸调整仍默认使用原版；如需调整平滑版尺寸，可下载后从“选择本地 STL”载入。

平滑会改变真实几何，可能影响细节，不能恢复缺失形状；未验证自相交、壁厚或打印支撑。STL 不包含单位，位移显示为文件原始单位。请下载两版，在相同尺寸和视角下检查曲面、发梢、沟槽与薄壁。页面对比不能替代完整 STL 和切片检查。

离线验证：`python -m unittest discover -s tests -p 'test_mesh_smoothing*.py'`、`node --test tests/test_mesh_smoothing_frontend.cjs`。
