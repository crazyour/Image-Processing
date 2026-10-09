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
- `public/`：新增的图生 3D 页面，路径为 `/image-to-3d`；
- `requirements.txt`、`vercel.json`：Python 依赖和 5 分钟函数时限；
- `.vercelignore`：部署时排除旧后端、字节码和恢复证据。

首页恢复原“灵界 · 工艺设计工作台”的已编译界面，并增加“图生 3D”入口。旧的 `backend/app/main.py` 仍有反编译语法错误，因此原工作台中依赖旧 API 的功能在 Vercel 上暂不可用；新增的图生 3D 工作流可独立运行。

## 图生 3D 流程

Vercel 应用采用必须经过用户确认的两阶段流程：

1. 上传正面图片，安全解码并统一为 PNG；
2. GPT 分析主体、几何、材质、颜色、遮挡区域和不可见面假设，生成可编辑的中文提示词；
3. 用户确认或修改提示词；
4. GPT Image 根据正面图和中文提示词生成左、右、后三个视角；
5. 用户预览多视图，可用中文指令单独微调任意生成视图；
6. 用户确认后锁定提示词与四张图片的版本；
7. 将 Vercel Blob 的公网图片地址提交给腾讯 TokenHub `hy-3d-3.1`；
8. 查询异步任务，并在成功后把 GLB/OBJ 等临时结果转存到 Vercel Blob。

复制 `.env.example` 为 `.env` 并配置：

```dotenv
OPENAI_API_KEY=你的OpenAI密钥
OPENAI_VISION_MODEL=gpt-6-luna
OPENAI_IMAGE_MODEL=gpt-image-2.5-flare
OPENAI_IMAGE_QUALITY=medium

HUNYUAN_API_KEY=你的TokenHub密钥
HUNYUAN_3D_MODEL=hy-3d-3.1

BLOB_READ_WRITE_TOKEN=Vercel自动提供
DATABASE_URL=PostgreSQL连接字符串
```

## 部署到 Vercel

1. 将本目录提交到 GitHub，并在 Vercel 中导入仓库。
2. 在 Vercel 项目的 Storage 页面创建一个 **Public Blob**，Vercel 会自动写入 `BLOB_READ_WRITE_TOKEN`。
3. 通过 Vercel Marketplace 连接 Neon、Supabase 或其他 PostgreSQL，并设置 `DATABASE_URL`。
4. 在项目的 Environment Variables 中添加 `OPENAI_API_KEY` 和 `HUNYUAN_API_KEY`。
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
POST  /api/sessions/{id}/views/{view}/refine
POST  /api/sessions/{id}/confirm
POST  /api/sessions/{id}/generate
GET   /api/sessions/{id}/3d
GET   /api/sessions/{id}
```

浏览器会把会话访问令牌保存在 `localStorage`，后续请求通过 `X-Session-Token` 发送。服务器数据库只保存令牌哈希。上传图片会在浏览器中缩放并压缩到 4 MB 以下，以避开 Vercel Function 的 4.5 MB 请求体限制。

默认使用 `gpt-6-luna` 做低成本图片结构分析，使用 `gpt-image-2.5-flare` 以 `medium` 质量生成三个补充视角。GPT Image 2.5 按输入/输出 token 计费，编辑请求还会计算参考图片输入，因此单张没有固定价格；实际费用以接口返回的 `usage` 为准。需要更强的跨视角编辑精度时，可以把 `OPENAI_IMAGE_MODEL` 改为 `gpt-image-2.5-sunburst`。

确认与提交是两个独立操作。只有当前提示词版本对应的四个视角齐全时才能确认；确认后才能调用 `generate`。用户提示词始终保存为中文，系统在后台追加跨视角一致性约束。由于混元普通多视图生成不接收同时提交的文字提示词，提示词负责约束 GPT 多视图生成，混元阶段只读取最终确认的四张图片。
