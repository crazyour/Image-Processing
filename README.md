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
- `frontend_dist/`：从原安装包恢复的 React 前端构建产物，作为网站首页；
- `image3d_frontend/`：图生 3D 工作流，由原灵界侧边栏入口在站内弹层中打开；
- `requirements.txt`、`vercel.json`：Python 依赖和 5 分钟函数时限；
- `.vercelignore`：部署时排除旧后端、字节码和恢复证据。

首页恢复原“灵界 · 工艺设计工作台”的已编译界面，并增加“图生 3D”入口。旧的 `backend/app/main.py` 仍有反编译语法错误，因此原工作台中依赖旧 API 的功能在 Vercel 上暂不可用；新增的图生 3D 工作流可独立运行。
为避免已编译前端因缺少桌面后端而阻断启动，Vercel 入口提供了明确标记的云端兼容状态和空集合读取接口。它只用于展示原工作台外壳，不会伪造或覆盖原桌面数据。

## 图生 3D 流程

面向普通用户的操作步骤和故障排查见 [图生 3D 功能使用说明](IMAGE_TO_3D_USER_GUIDE.md)。

Vercel 应用支持 AI 生成或用户上传正、左、右、背四视图，并在提交 3D 前人工确认：

- 站内 AI 自动生成约 1 元/张，四视图约 4 元/次；
- 也可先用豆包等网页 AI 生成四视图后自行上传，此时本站不收图片生成费（外部平台额度与费用以其规则为准）。

1. 上传参考图片，安全解码并统一为 PNG；
2. 选择 AI 模式时，GPT 先判断参考图视角与适用性，再由 GPT Image 生成正、左、右、背四个统一视角；明显的侧面、背面或顶部参考图会要求更换；选择手动模式时，用户直接上传正、左、右、背四张图片且不会调用 GPT；
3. 用户并排检查四视图；AI 模式还可用中文指令单独微调任一视图，或修改内部主体描述后重新生成；
4. 用户确认后锁定四视图的版本；
5. 将 Vercel Blob 的公网图片地址提交给腾讯 TokenHub `hy-3d-3.1`；
6. 固定请求 STL，查询异步任务，并在成功后把 STL 文件转存到 Vercel Blob；网页提供 STL 旋转、缩放预览和下载。

复制 `.env.example` 为 `.env` 并配置：

```dotenv
OPENAI_API_KEY=你的OpenAI密钥
OPENAI_BASE_URL=https://api.openai.com/v1

HUNYUAN_API_KEY=你的TokenHub密钥
HUNYUAN_BASE_URL=https://tokenhub.tencentmaas.com/v1

BLOB_READ_WRITE_TOKEN=Vercel自动提供
DATABASE_URL=PostgreSQL连接字符串
LOCAL_DATA_DIR=可选的本地数据目录（默认为项目下的 var）
```

本地开发时可以留空 `BLOB_READ_WRITE_TOKEN` 和 `DATABASE_URL`。应用会自动改用
`var/image3d.sqlite3` 保存会话，并将图片与模型文件保存到 `var/blobs/`。这些文件已被
`.gitignore` 排除。本地图片可用于界面预览和 GPT 多视图流程，但腾讯混元 3D 需要公网可访问的图片 URL，
因此提交 3D 任务前仍需配置 Public Vercel Blob。

供应商 API Key 和端点只从环境变量读取，不会发送到浏览器。图片理解、多视图生成和 3D 生成的模型名称以及图片质量在网页“模型设置”中配置，保存在当前浏览器并在新建会话时锁定。缺少 API Key 或端点时接口会返回 `NOT_CONFIGURED`。

## 部署到 Vercel

1. 将本目录提交到 GitHub，并在 Vercel 中导入仓库。
2. 在 Vercel 项目的 Storage 页面创建一个 **Public Blob**，Vercel 会自动写入 `BLOB_READ_WRITE_TOKEN`。
3. 通过 Vercel Marketplace 连接 Neon、Supabase 或其他 PostgreSQL，并设置 `DATABASE_URL`。
4. 按 `.env.example` 在项目的 Environment Variables 中配置 OpenAI/混元端点和 API Key；`BLOB_READ_WRITE_TOKEN` 与 `DATABASE_URL` 由对应存储集成提供。
5. 重新部署。首次创建会话时会自动建立 `image3d_sessions` 数据表。

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
PATCH /api/sessions/{id}/prompt
POST  /api/sessions/{id}/views
POST  /api/sessions/{id}/views/{view}/upload
POST  /api/sessions/{id}/views/{view}/refine
POST  /api/sessions/{id}/confirm
POST  /api/sessions/{id}/generate
GET   /api/sessions/{id}/3d
GET   /api/sessions/{id}
```

浏览器只在当前标签页的 `sessionStorage` 中保存会话访问令牌，后续请求通过 `X-Session-Token` 发送；关闭标签页后不会恢复上一次任务。服务器数据库只保存令牌哈希。上传图片会在浏览器中缩放并压缩到 4 MB 以下，以避开 Vercel Function 的 4.5 MB 请求体限制。

网页默认使用 `gpt-6-luna` 做图片结构分析，使用 `gpt-image-2.5-flare` 以 `medium` 质量生成正、左、右、背四视图，并使用 `hy-3d-3.1` 生成 3D。GPT Image 2.5 按输入/输出 token 计费，编辑请求还会计算参考图片输入，因此单张没有固定价格；实际费用以接口返回的 `usage` 为准。需要更强的跨视角编辑精度时，可在网页把多视图生成模型改为 `gpt-image-2.5-sunburst`。

确认与提交是两个独立操作。AI 模式会把上传原图作为内部参考并重新生成正、左、右、背四视图；手动模式则直接使用用户上传的四视图，不调用 GPT 分析或图片生成。四视图齐全并确认后才能调用 `generate`。混元任务固定请求 STL，应用也只保存和展示 STL 输出。部分视图生成失败时会保留已成功结果，用户只需重试未完成视图；提交或生成状态超时后也会自动解除页面锁定并给出明确错误。
