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
- `vercel_app/`：OpenAI、腾讯混元、PostgreSQL 和 Vercel Blob 工作流；
- `frontend_dist/`：轻量功能选择首页及其公共静态资源；
- `image3d_frontend/`：图生 3D 工作流，由原灵界侧边栏入口在站内弹层中打开；
- `text2image_frontend/`：云端文生图工作流，支持一组统一提示词生成 1–5 张候选图；
- `similarity_search_frontend/`：图片、DXF、Illustrator 同格式相似素材检索；
- `requirements.txt`、`vercel.json`：Python 依赖和 5 分钟函数时限；
- `.vercelignore`：部署时排除旧后端、字节码和恢复证据。

网站首页是精简的功能选择页。文生图、图生 3D 和相似素材检索使用独立前端；原工作台中的创意、照片定制、产品、配色工艺、视觉、SVG、DXF、文件和交付功能通过 `/tools/*` 独立入口继续提供。旧的完整工作台入口和侧边栏式导航不再公开。

## 相似素材检索

左侧“相似素材检索”入口打开独立素材库。一个顶层文件夹是一条素材记录，必须至少包含一张
PNG/JPG/WebP 图片、一个 ASCII DXF 和一个启用了“创建 PDF 兼容文件”的 Illustrator `.ai`。
上传后系统为三类文件生成统一 PNG 预览和特征向量，并生成保留相对路径的 ZIP 下载包。

检索时上传图片、DXF 或 AI 中任意一种，系统只比较同格式文件，按文件夹聚合并返回前五项：

- 图片：颜色直方图、缩略图和边缘特征；
- DXF：实体统计、外接尺寸比例和标准渲染图特征；
- AI：读取其 PDF 兼容内容，渲染首页后提取视觉特征。

当前版本不为每个文件建立新数据库，而是把全部特征记录保存在同一 PostgreSQL/SQLite 素材库中。
相似度用于排序，不代表识别准确率。素材和查询文件会在浏览器中自动分片上传，不再设置 4 MB 的
应用层文件大小限制；二进制 DXF 和不含 PDF 兼容内容的 AI 会被明确拒绝。实际可处理大小仍受
Vercel 函数内存、执行时长以及 Vercel Blob 平台配额限制。

云端原文件、预览和 ZIP 必须放在独立的 **Private Vercel Blob** 中。所有读取均经过素材库令牌校验，
不会把 Blob 地址直接发送给浏览器。本地开发时则写入 `var/material-private/`。

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

花费优先使用接口响应里返回的价格：`usage` 块里或响应顶层的 `cost`、`total_cost`、`price`、`amount` 等字段都支持，嵌套写法（如 `usage.cost.total`）也能识别。抓到的是哪个字段会一并记在 `cost_field` 里，并在明细弹窗的“计价方式”中显示（例如“接口返回（usage.cost）”），便于核对。接口没有返回价格时，图片和 3D 任务使用 `USAGE_PRICE_IMAGE_PER_IMAGE` 与 `USAGE_PRICE_3D_PER_TASK` 的每次预设单价；视觉/文本模型则按 `USAGE_PRICE_VISION_INPUT_PER_MILLION` 和 `USAGE_PRICE_VISION_OUTPUT_PER_MILLION` 配置的每百万 Token 美元单价计算：`(输入 Token × 输入单价 + 输出 Token × 输出单价) / 1,000,000`。管理员页同时显示输入、输出和总 Token，即使单价未配置也能看到用量。每条记录还会保存接口返回的原始 `usage` 内容（`raw` 列），方便事后核对。没有 token 消耗的本地功能（相似素材检索、主工作台里的本地工具）不计入统计。

可以用下面的命令验证某个接口返回能否被抓到（把实际响应粘进去即可）：

```bash
echo '{"usage":{"total_tokens":1200,"cost":0.14}}' | python -m vercel_app.usage
```

未开通的功能不会隐藏，而是在功能列表页显示为灰色并带锁图标，点击后提示联系管理员；直接请求对应地址依然会被后端拦截。

密码使用带随机盐的 `scrypt` 哈希保存，登录状态使用 HttpOnly、SameSite=Strict Cookie。修改密码、角色或停用账号会撤销该账号的旧会话；功能权限同时在页面入口和后端 API 校验，不能通过直接请求绕过。

## 成果素材库

`/file-upload` 分成「素材展示」和「上传素材」两个功能。素材文件放在云端 Vercel Blob，所有登录成员都能浏览彼此上传的成果。

上传时需要选择文件并标注业务类型（文生图、照片转镂空剪影、图生 3D、设计与工程文件、其他成果），上传者由当前登录账号自动记录，不需要手填。展示页先按上传账号分组，每个账号下面再按业务类型分组，另外提供账号和业务类型两个筛选器。素材的归属信息保存在 `deliverables` 表里（与账号表同一个数据库），文件本体在 Blob 中；没有 `asset_library` 权限的账号不能上传。

## 文生图流程

左侧“文生图”入口打开独立云端工作区。用户填写一组统一提示词，选择生成 1–5 张候选图，并设置图片模型、质量与尺寸。系统按队列逐张调用图片生成接口，每张完成后立即转存到 Vercel Blob；任务状态、提示词、用量和文件地址保存在 PostgreSQL。单张失败不会清除已成功结果，可在对应卡片上单独重试。浏览器只在当前标签页保存任务 ID 和访问令牌，刷新后可以继续当前任务；本机存储模式还可从“作品记录”重新打开。

同一页面的“照片转镂空剪影”支持上传一张 PNG、JPEG 或 WebP 照片，主体可以是人物、动物、植物、器物、车辆、建筑、风景或组合场景。用户可选择一次生成 1–5 张纯黑白平面镂空候选，然后选中其中一张；只有所选候选会进入本地 OpenCV 连通性检测和后续 AI 局部修复。页面从服务端读取并显示通用默认提示词，用户可以修改、恢复默认值，或不修改直接生成。

每张候选图生成后都会由本地 OpenCV 自动检测黑色连通域，并生成“主体为蓝色、全部孤岛或薄弱区域统一为红色”的彩色连通图。红蓝图只用于页面诊断，不会发送给 AI。只有检测不通过时，系统才使用独立配置的修复模型（默认 `gpt-image-2.5-sunburst`、`high`），提交当前黑白图、局部透明蒙版和修复提示词。蒙版仅开放问题区及其到主体之间的短连接走廊，要求模型在局部自然连接孤岛并尽量保持其他区域不变。若 AI 仍留下断开的孤岛，本地会只保留最大黑色连通域作为安全回退；薄弱连接则继续修复并复检，自动修复最多两次。仍未通过时会显示剩余问题数量，并允许继续手动修复或恢复首次生成图。页面按三个固定模块展示：第 01 部分上传与设置，第 02 部分展示首次生成图、生成提示词和本地诊断图片，第 03 部分展示修复模型、提示词、每轮蒙版、修复结果及复检记录。生成图仍需人工检查和后续矢量化，不代表已经通过实际材料、尺寸、刀宽或强度验证。

## 图生 3D 流程

面向普通用户的操作步骤和故障排查见 [图生 3D 功能使用说明](IMAGE_TO_3D_USER_GUIDE.md)。

Vercel 应用支持 AI 生成或用户上传正、左、右、背四视图，并在提交 3D 前分阶段人工确认：

- 站内 AI 自动生成约 1 元/张，四视图约 4 元/次；
- 也可先用豆包等网页 AI 生成四视图后自行上传，此时本站不收图片生成费（外部平台额度与费用以其规则为准）。

1. AI 模式上传 1–6 张同一对象的参考图；图片逐张上传，安全解码并统一为 PNG；
2. GPT 综合所有参考图输出可编辑的主体、结构、材质、遮挡与风险描述；用户必须先确认描述，才能生成四视图；
3. GPT Image 使用全部参考图生成正、左、右、背四个统一视角；用户可单独微调任一视图；
4. 选择手动模式时，用户直接上传四张图，跳过 GPT 分析和四视图生成；
5. 用户确认四视图后，将四视图的 Vercel Blob 公网地址提交给腾讯 TokenHub `hy-3d-3.1`；
6. 固定请求 STL，查询异步任务，成功后把 STL 转存到 Vercel Blob，并提供旋转、缩放预览和下载。

复制 `.env.example` 为 `.env` 并配置：

```dotenv
OPENAI_API_KEY=你的OpenAI密钥
OPENAI_BASE_URL=https://api.openai.com/v1

HUNYUAN_API_KEY=你的TokenHub密钥
HUNYUAN_BASE_URL=https://tokenhub.tencentmaas.com/v1

BLOB_READ_WRITE_TOKEN=Vercel自动提供
MATERIAL_BLOB_READ_WRITE_TOKEN=素材专用Private Blob令牌
MATERIAL_LIBRARY_TOKEN=公共素材库令牌（可选，建议在Vercel中设置为长随机值）
DATABASE_URL=PostgreSQL连接字符串
LOCAL_DATA_DIR=可选的本地数据目录（默认为项目下的 var）
```

本地开发时可以留空 `BLOB_READ_WRITE_TOKEN` 和 `DATABASE_URL`。应用会自动改用
`var/image3d.sqlite3` 保存会话，并将图片与模型文件保存到 `var/blobs/`。这些文件已被
`.gitignore` 排除。本地图片可用于界面预览和 GPT 多视图流程，但腾讯混元 3D 需要公网可访问的图片 URL，
因此提交 3D 任务前仍需配置 Public Vercel Blob。

本机存储模式提供“作品记录”入口，会从 SQLite 中列出文生图、照片转镂空剪影和图生 3D
任务。关闭标签页后，可以从作品记录重新签发任务访问令牌并恢复原进度；图片本体仍保存在
`var/blobs/`，不会写入浏览器存储。作品名称、收藏状态和个人生图偏好也保存在 SQLite 中。
个人偏好默认关闭，启用后只会锁定到之后新建的任务，并在对应记录中标明已应用。

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

网页默认使用 `gpt-6-luna` 做图片结构分析，使用 `gpt-image-2.5-flare` 以 `medium` 质量生成正、左、右、背四视图，并使用 `hy-3d-3.1` 生成 3D。GPT Image 2.5 按输入/输出 token 计费，编辑请求还会计算参考图片输入，因此单张没有固定价格；实际费用以接口返回的 `usage` 为准。需要更强的跨视角编辑精度时，可在网页把多视图生成模型改为 `gpt-image-2.5-sunburst`。

描述确认、四视图确认和 STL 提交是独立门禁。修改上游内容会自动使后续确认失效。混元任务固定请求 STL，应用也只保存和展示 STL 输出。部分视图生成失败时会保留已成功结果；分析、图片生成或 3D 提交超时后会自动解除页面锁定并显示明确错误。
