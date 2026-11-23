const $ = (selector) => document.querySelector(selector);
if (new URLSearchParams(location.search).get("embedded") === "1") {
  document.body.classList.add("embedded");
}
const labels = { front: "正视图", left: "左视图", right: "右视图", back: "背视图" };
const allViews = ["front", "left", "right", "back"];
localStorage.removeItem("image3d-session");
let session = null;
try {
  session = JSON.parse(sessionStorage.getItem("image3d-session") || "null");
} catch (_) {
  sessionStorage.removeItem("image3d-session");
}
let state = null;
let pollingTimer = null;
let pollingInFlight = false;
let taskClock = null;
let actionInFlight = false;
let transientError = null;
let dismissedErrorKey = null;
let loadedPreviewUrl = null;
let stlPreview = null;
let generatedStlUrl = null;
let loadedResizeUrl = null;
let resizeMesh = null;
let resizeSourceKind = null;
let resizeFileName = "model-resized.stl";
let viewFolders = [];
let selectedColorFolderId = null;
let colorDraft = null;

const workspaceContent = {
  "3d": {
    eyebrow: "灵界 · GPT MULTIVIEW · IMAGE TO 3D",
    title: "图生 3D",
    copy: "上传多张同一个对象的参考图，先确认 AI 对主体的理解，再确认四视图并生成 STL 模型。",
  },
  library: {
    eyebrow: "灵界 · MULTIVIEW LIBRARY",
    title: "四视图数据库",
    copy: "统一管理已保存的正、左、右、背四视图，可查看全图或删除整套视图。",
  },
  color: {
    eyebrow: "灵界 · MULTIVIEW COLOR",
    title: "四视图上色",
    copy: "从四视图数据库选择一套视图，作为后续颜色推荐与上色的独立工作区。",
  },
};

function setWorkspace(name, updateUrl = true) {
  const workspace = workspaceContent[name] ? name : "3d";
  document.querySelectorAll("[data-workspace]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.workspace === workspace));
  });
  document.querySelectorAll("[data-workspace-view]").forEach((section) => {
    const visible = section.dataset.workspaceView === workspace;
    section.classList.toggle("hidden", !visible);
    section.setAttribute("aria-hidden", String(!visible));
  });
  const content = workspaceContent[workspace];
  $("#workspace-eyebrow").textContent = content.eyebrow;
  $("#workspace-title").textContent = content.title;
  $("#workspace-copy").textContent = content.copy;
  document.title = `灵界 · ${content.title}`;
  $("#view-source-intro").classList.toggle("hidden", workspace !== "3d");
  $("#paid-feature-banner").classList.toggle("hidden", workspace === "library");
  $("#paid-feature-banner span").textContent = workspace === "color"
    ? "AI 图像识别和每张上色图都会按实际调用产生费用。请先确认正视图，满意后再生成其余三张，可避免无效费用。"
    : "AI 主体分析、四视图生成和 3D 模型生成会按实际调用产生费用。自行上传四视图可免除站内图片生成费，但生成 3D 模型仍可能产生费用。";
  $("#library-toggle").classList.toggle("hidden", workspace !== "3d");
  if (workspace !== "3d") {
    $("#library-panel").classList.add("hidden");
    $("#library-toggle").setAttribute("aria-expanded", "false");
  }
  if (workspace === "library" || workspace === "color") loadViewLibrary();
  if (updateUrl) {
    const url = new URL(location.href);
    if (workspace === "3d") url.searchParams.delete("workspace");
    else url.searchParams.set("workspace", workspace);
    history.replaceState(null, "", url);
  }
}

document.querySelectorAll("[data-workspace]").forEach((button) => {
  button.addEventListener("click", () => setWorkspace(button.dataset.workspace));
});

function initializeUsageGuide() {
  const toggle = $("#guide-toggle");
  const drawer = $("#usage-guide");
  const close = $("#guide-close");

  function setOpen(open, returnFocus = false) {
    document.body.classList.toggle("guide-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    drawer.setAttribute("aria-hidden", String(!open));
    drawer.inert = !open;
    if (open) close.focus();
    else if (returnFocus) toggle.focus();
  }

  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    setOpen(open, !open);
  });
  close.addEventListener("click", () => setOpen(false, true));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("guide-open")) {
      setOpen(false, true);
    }
  });
}

function notify(message, error = false) {
  const box = $("#notice");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.setAttribute("role", error ? "alert" : "status");
  box.classList.remove("hidden");
  clearTimeout(box.timer);
  box.timer = setTimeout(() => box.classList.add("hidden"), 6000);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (session?.token) headers.set("X-Session-Token", session.token);
  const timeout = options.timeout ?? 45_000;
  const { timeout: _ignoredTimeout, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(path, { ...fetchOptions, headers, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("请求等待超时，请检查网络后重试");
    throw new Error("网络连接失败，请检查网络后重试");
  } finally {
    clearTimeout(timer);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join("；")
      : typeof body.detail === "string" ? body.detail : "";
    const error = new Error(body.message || detail || `请求失败 (${response.status})`);
    error.code = body.code || `HTTP_${response.status}`;
    throw error;
  }
  return body;
}

function clearError() {
  dismissedErrorKey = errorKey(transientError || state?.last_error);
  transientError = null;
  renderError();
}

function showError(error) {
  transientError = { code: error?.code || "REQUEST_FAILED", message: error?.message || "操作未完成" };
  dismissedErrorKey = null;
  renderError();
  notify(transientError.message, true);
}

function renderError() {
  const candidate = transientError || state?.last_error;
  const error = candidate && errorKey(candidate) !== dismissedErrorKey ? candidate : null;
  const panel = $("#workflow-error");
  panel.classList.toggle("hidden", !error);
  if (!error) return;
  $("#workflow-error-message").textContent = error.message || "操作未完成，请重试";
  $("#workflow-error-code").textContent = error.code ? `错误编号：${error.code}` : "";
}

function errorKey(error) {
  return error ? `${error.code || ""}:${error.message || ""}` : null;
}

function setBusy(button, busy, text = "处理中…") {
  if (!button) return;
  if (busy) {
    if (!button.dataset.busy) button.dataset.label = button.textContent;
    button.dataset.busy = "1";
    button.textContent = text;
  }
  else if (button.dataset.label) { button.textContent = button.dataset.label; delete button.dataset.label; delete button.dataset.busy; }
  button.disabled = busy;
  if (!busy) syncControls();
}

function setActionInFlight(value) {
  actionInFlight = value;
  syncControls();
}

async function compressedFile(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1800 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext("2d");
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  if (!blob) throw new Error("浏览器无法处理这张图片");
  if (blob.size > 4_000_000) throw new Error("压缩后的图片仍超过 4 MB，请换一张分辨率较低的图片");
  return new File([blob], "upload.jpg", { type: "image/jpeg" });
}

function renderAnalysis(analysis = {}) {
  const available = Boolean(analysis.subject || analysis.geometry);
  const empty = $("#analysis-empty");
  empty.textContent = state?.view_source === "uploaded"
    ? "手动上传模式不进行 AI 分析，也不会产生站内图片生成费用。"
    : "上传参考图后，AI 会综合多张图片显示识别结果。";
  empty.classList.toggle("hidden", available);
  $("#analysis").classList.toggle("hidden", !available);
  $("#prompt-label").classList.toggle("hidden", !state || state.view_source === "uploaded");
  const entries = [
    ["主体", analysis.subject], ["几何结构", analysis.geometry],
    ["材质", (analysis.materials || []).join("、")], ["颜色", (analysis.colors || []).join("、")],
    ["遮挡区域", (analysis.occlusion_regions || []).join("、") || "未发现"],
    ["隐藏结构推测", (analysis.hidden_geometry_assumptions || []).join("；") || "无"],
    ["输入适用性", ({ GOOD: "适合生成", USABLE: "可以使用，建议检查风险", POOR: "不建议直接生成" })[analysis.image_suitability] || "未评估"],
    ["主图视角", ({ FRONT: "正面", FRONT_THREE_QUARTER: "斜前方", SIDE: "侧面", BACK: "背面", TOP: "顶部", UNKNOWN: "无法判断" })[analysis.source_view] || "未评估"],
    ["风险提示", (analysis.warnings || []).join("；") || "未发现明显风险"],
  ];
  $("#analysis").innerHTML = entries.map(([title, value]) =>
    `<article class="analysis-card"><strong>${title}</strong><p>${escapeHtml(value || "—")}</p></article>`
  ).join("");
}

function renderReferences(references = []) {
  const box = $("#server-reference-previews");
  if (!box) return;
  box.innerHTML = references.map((item, index) =>
    `<article><img src="${item.url}" alt="参考图 ${index + 1}"><span>${index === 0 ? "主参考" : `补充 ${index}`}</span></article>`
  ).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);
}

function renderViews(views = {}) {
  const uploaded = state?.view_source === "uploaded";
  $("#views-grid").innerHTML = allViews.map((view) => {
    const item = views[view];
    if (!item) {
      const upload = uploaded
        ? `<label class="replace-view">上传${labels[view]}<input data-replace-view="${view}" type="file" accept="image/png,image/jpeg,image/webp"></label>` : "";
      return `<article class="view-card"><h3>${labels[view]}</h3><p>尚未生成</p>${upload}</article>`;
    }
    const refinement = !uploaded && state?.status === "VIEWS_REVIEW"
      ? `<div class="refine"><input id="refine-${view}" type="text" placeholder="例如：不要增加装饰"><button data-refine="${view}">微调</button></div>` : "";
    const replacement = uploaded && ["VIEWS_REVIEW", "VIEWS_CONFIRMED", "CONFIRMED", "FAILED"].includes(state?.status)
      ? `<label class="replace-view">替换${labels[view]}<input data-replace-view="${view}" type="file" accept="image/png,image/jpeg,image/webp"></label>` : "";
    const titleNote = view === "front" ? (uploaded ? "<small>自行上传</small>" : "<small>AI 生成</small>") : "";
    return `<article class="view-card"><h3>${labels[view]}${titleNote}</h3><img data-zoom src="${item.url}" alt="${labels[view]}">${refinement}${replacement}</article>`;
  }).join("");
  document.querySelectorAll("[data-refine]").forEach((button) => button.addEventListener("click", () => refine(button.dataset.refine, button)));
  document.querySelectorAll("[data-replace-view]").forEach((input) => input.addEventListener("change", () => replaceUploadedView(input.dataset.replaceView, input)));
  document.querySelectorAll("[data-zoom]").forEach((image) => image.addEventListener("click", () => window.open(image.src, "_blank", "noopener")));
}

function syncControls() {
  const status = state?.status;
  const uploaded = state?.view_source === "uploaded";
  const viewsReady = allViews.every((view) => state?.views?.[view]?.prompt_revision === state?.prompt_revision);
  const descriptionConfirmed = Boolean(state?.description_confirmed_at);
  const canEditPrompt = Boolean(state) && !uploaded && status === "PROMPT_REVIEW";
  const canGenerateViews = Boolean(state) && !uploaded && descriptionConfirmed && ["DESCRIPTION_CONFIRMED", "VIEWS_REVIEW"].includes(status);
  $("#prompt-editor").disabled = !canEditPrompt;
  $("#confirm-description").disabled = !canEditPrompt || actionInFlight;
  $("#confirm-description").classList.toggle("hidden", uploaded);
  $("#generate-views").classList.toggle("hidden", uploaded);
  $("#regenerate-views").classList.toggle("hidden", uploaded);
  $("#generate-views").disabled = !canGenerateViews || actionInFlight;
  $("#regenerate-views").disabled = !canGenerateViews || !viewsReady || actionInFlight;
  $("#confirm-views").disabled = !state || state.views_stale || status !== "VIEWS_REVIEW" || actionInFlight;
  $("#save-view-folder").disabled = !viewsReady || actionInFlight;
  if (!$("#confirm-views").dataset.busy) {
    $("#confirm-views").textContent = state?.confirmed_at ? "已确认 ✓" : "确认四个视角";
  }
  const generateButton = $("#generate-3d");
  const running = ["SUBMITTING_3D", "GENERATING_3D"].includes(status);
  const canSubmit3d = ["VIEWS_CONFIRMED", "CONFIRMED", "FAILED"].includes(status) && Boolean(state?.confirmed_at);
  generateButton.disabled = !canSubmit3d || actionInFlight;
  document.querySelectorAll('input[name="model-provider"]').forEach((input) => {
    input.disabled = running || actionInFlight;
  });
  generateButton.classList.toggle("pending", running);
  if (!generateButton.dataset.busy) {
    generateButton.textContent = running ? "正在生成 STL…" : status === "FAILED" ? "重新生成 STL" : "提交 STL 任务";
  }
  const uploadButton = $("#upload-submit");
  uploadButton.disabled = actionInFlight || (Boolean(state) && status !== "REFERENCES_READY");
  if (state && !uploaded) $("#image-input").required = false;
  if (!uploadButton.dataset.busy) {
    uploadButton.textContent = uploaded
      ? "上传正、左、右、背四视图"
      : status === "REFERENCES_READY" ? "确认当前图片并分析参考图" : "上传并分析参考图";
  }

  setStepState("#upload-state", !state ? "等待上传" : uploaded ? "四视图已上传" : `${state.reference_images?.length || 1} 张已上传`, Boolean(state), false, Boolean(state));
  if (!state) setStepState("#description-state", "等待分析", false, false);
  else if (uploaded) setStepState("#description-state", "无需分析", false, false, true);
  else if (status === "ANALYZING_REFERENCES") setStepState("#description-state", "正在分析", true, false);
  else if (descriptionConfirmed) setStepState("#description-state", "已确认", false, false, true);
  else if (status === "PROMPT_REVIEW") setStepState("#description-state", "等待确认", true, false);
  else setStepState("#description-state", "等待分析", false, Boolean(state.last_error));
  if (!state) setStepState("#views-state", "等待生成", false, false);
  else if (status === "GENERATING_VIEWS") setStepState("#views-state", "正在生成", true, false);
  else if (status === "DESCRIPTION_CONFIRMED") setStepState("#views-state", "可以生成", true, false);
  else if (state.views_stale || !viewsReady) setStepState("#views-state", "尚未完成", false, Boolean(state.last_error));
  else if (state.confirmed_at) setStepState("#views-state", "已确认", true, false, true);
  else setStepState("#views-state", "等待确认", true, false);

  if (status === "SUCCEEDED") setStepState("#generate-state", "已完成", true, false, true);
  else if (status === "FAILED") setStepState("#generate-state", "生成失败", false, true);
  else if (running) setStepState("#generate-state", "生成中", true, false);
  else if (state?.confirmed_at) setStepState("#generate-state", "可以提交", true, false);
  else setStepState("#generate-state", "等待确认", false, false);
}

function setStepState(selector, text, active = false, error = false, success = false) {
  const badge = $(selector);
  badge.textContent = text;
  badge.classList.toggle("active", active && !success);
  badge.classList.toggle("error", error);
  badge.classList.toggle("success", success);
}

function render(next) {
  state = next;
  const savedProvider = (state?.model_task || state?.hunyuan_task)?.provider;
  const savedProviderInput = savedProvider && document.querySelector(`input[name="model-provider"][value="${savedProvider}"]`);
  if (savedProviderInput) savedProviderInput.checked = true;
  updateModelProvider();
  renderAnalysis(state?.analysis || {});
  renderReferences(state?.reference_images || []);
  $("#prompt-editor").value = state?.user_prompt || "";
  renderViews(state?.views || {});
  syncControls();
  renderTask();
  renderError();
}

function renderTask() {
  const task = state?.model_task || state?.hunyuan_task;
  const providerName = task?.provider === "hunyuan" ? "腾讯混元" : "Meshy 7.1";
  const running = ["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status);
  $("#task-progress").classList.toggle("hidden", !running);
  $("#task-stage").textContent = state?.status === "SUBMITTING_3D"
    ? `正在提交${providerName}…`
    : ["queued", "pending"].includes(task?.status) ? `${providerName}任务已进入队列`
      : `${providerName}正在生成模型${Number.isFinite(Number(task?.progress)) ? ` · ${task.progress}%` : ""}…`;
  $("#task-id").textContent = task?.id ? `任务编号：${task.id}` : "正在等待任务编号";
  updateElapsed();
  const models = task?.models || {};
  $("#model-results").innerHTML = Object.entries(models).filter(([type]) => ["glb", "stl"].includes(type.toLowerCase())).map(([type, model]) =>
    `<a class="model-link" href="${model.url}" download="model-${state.session_id.slice(0, 8)}.${type.toLowerCase()}"><span>${type.toUpperCase()} 模型</span><span>下载文件 ↓</span></a>`
  ).join("");
  const stl = models.stl || models.STL;
  generatedStlUrl = stl?.url || null;
  $("#use-generated-stl").disabled = !generatedStlUrl;
  $("#model-preview").classList.toggle("hidden", !stl);
  if (!stl && running) {
    loadedPreviewUrl = null;
    stlPreview = null;
    $("#stl-canvas").classList.add("hidden");
  }
  if (stl) {
    const size = stl.size_bytes ? formatBytes(stl.size_bytes) : "大小未知";
    $("#model-meta").textContent = `${size} · 已保存到云端，可直接下载`;
    const preview = $("#model-preview-image");
    preview.classList.toggle("hidden", !stl.preview_image_url);
    if (stl.preview_image_url) preview.src = stl.preview_image_url;
    if (loadedPreviewUrl !== stl.url) loadStlPreview(stl.url, stl.preview_image_url);
  }
  if (running) {
    schedulePoll(8000);
    if (!taskClock) taskClock = setInterval(updateElapsed, 1000);
  } else {
    stopPolling();
  }
}

function formatBytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

async function loadStlPreview(url, fallbackImage) {
  loadedPreviewUrl = url;
  const canvas = $("#stl-canvas");
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error("STL 预览下载失败");
    const buffer = await response.arrayBuffer();
    const triangles = parseStl(buffer);
    if (!triangles.length) throw new Error("STL 预览格式无效");
    if (resizeSourceKind !== "local" && loadedResizeUrl !== url) {
      loadedResizeUrl = url;
      setResizeMesh(buffer, "本次生成的 STL", "generated-model.stl", "generated");
    }
    stlPreview = { triangles, rotationX: -0.35, rotationY: 0.65, zoom: 0.9 };
    canvas.classList.remove("hidden");
    $("#model-preview-image").classList.add("hidden");
    drawStlPreview();
  } catch (_) {
    canvas.classList.add("hidden");
    $("#model-preview-image").classList.toggle("hidden", !fallbackImage);
  }
}

function parseStl(buffer) {
  const view = new DataView(buffer);
  const triangles = [];
  if (buffer.byteLength >= 84) {
    const count = view.getUint32(80, true);
    if (count > 0 && 84 + count * 50 <= buffer.byteLength) {
      const step = Math.max(1, Math.ceil(count / 12000));
      for (let index = 0; index < count; index += step) {
        const offset = 84 + index * 50 + 12;
        const triangle = [];
        for (let vertex = 0; vertex < 3; vertex += 1) {
          const base = offset + vertex * 12;
          triangle.push([view.getFloat32(base, true), view.getFloat32(base + 4, true), view.getFloat32(base + 8, true)]);
        }
        triangles.push(triangle);
      }
      return normalizeTriangles(triangles);
    }
  }
  const text = new TextDecoder().decode(buffer);
  const vertices = [...text.matchAll(/vertex\s+([+-]?[\d.eE]+)\s+([+-]?[\d.eE]+)\s+([+-]?[\d.eE]+)/g)]
    .map((match) => [Number(match[1]), Number(match[2]), Number(match[3])]);
  const step = Math.max(3, Math.ceil(vertices.length / 36000) * 3);
  for (let index = 0; index + 2 < vertices.length; index += step) triangles.push([vertices[index], vertices[index + 1], vertices[index + 2]]);
  return normalizeTriangles(triangles);
}

function normalizeTriangles(triangles) {
  const points = triangles.flat();
  if (!points.length) return [];
  const mins = [Infinity, Infinity, Infinity];
  const maxs = [-Infinity, -Infinity, -Infinity];
  points.forEach((point) => point.forEach((value, axis) => {
    mins[axis] = Math.min(mins[axis], value);
    maxs[axis] = Math.max(maxs[axis], value);
  }));
  const center = mins.map((value, axis) => (value + maxs[axis]) / 2);
  const span = Math.max(...maxs.map((value, axis) => value - mins[axis])) || 1;
  return triangles.map((triangle) => triangle.map((point) => point.map((value, axis) => (value - center[axis]) / span)));
}

function parseResizeStl(buffer) {
  if (buffer.byteLength < 15) throw new Error("STL 文件内容为空或格式无效");
  if (buffer.byteLength > 100 * 1024 * 1024) throw new Error("STL 文件不能超过 100 MB");
  const view = new DataView(buffer);
  let vertices;
  if (buffer.byteLength >= 84) {
    const count = view.getUint32(80, true);
    if (count > 0 && 84 + count * 50 <= buffer.byteLength) {
      vertices = new Float32Array(count * 9);
      for (let face = 0; face < count; face += 1) {
        const source = 84 + face * 50 + 12;
        for (let value = 0; value < 9; value += 1) vertices[face * 9 + value] = view.getFloat32(source + value * 4, true);
      }
    }
  }
  if (!vertices) {
    const text = new TextDecoder().decode(buffer);
    const values = [];
    const pattern = /vertex\s+([+-]?[\d.eE]+)\s+([+-]?[\d.eE]+)\s+([+-]?[\d.eE]+)/g;
    for (const match of text.matchAll(pattern)) values.push(Number(match[1]), Number(match[2]), Number(match[3]));
    if (values.length < 9 || values.length % 9) throw new Error("无法识别该 STL，请使用二进制或标准 ASCII STL");
    vertices = Float32Array.from(values);
  }
  const mins = [Infinity, Infinity, Infinity];
  const maxs = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < vertices.length; index += 3) {
    for (let axis = 0; axis < 3; axis += 1) {
      const value = vertices[index + axis];
      if (!Number.isFinite(value)) throw new Error("STL 包含无效坐标");
      mins[axis] = Math.min(mins[axis], value);
      maxs[axis] = Math.max(maxs[axis], value);
    }
  }
  const dimensions = mins.map((value, axis) => maxs[axis] - value);
  if (dimensions.every((value) => value <= 0)) throw new Error("STL 模型没有可调整的尺寸");
  return { vertices, dimensions };
}

function dimensionText(value) {
  if (!Number.isFinite(value)) return "—";
  return Number(value.toFixed(value >= 100 ? 2 : value >= 1 ? 3 : 5)).toString();
}

function setResizeMesh(buffer, sourceLabel, fileName, sourceKind) {
  resizeMesh = parseResizeStl(buffer);
  resizeSourceKind = sourceKind;
  resizeFileName = `${(fileName || "model").replace(/\.stl$/i, "")}-resized.stl`;
  $("#resize-source-status").textContent = `${sourceLabel} · ${resizeMesh.vertices.length / 9} 个三角面`;
  $("#original-dimensions").textContent = resizeMesh.dimensions.map((value, axis) => `${"XYZ"[axis]} ${dimensionText(value)} mm`).join(" · ");
  $("#scale-percent").value = "100";
  resizeMesh.dimensions.forEach((value, axis) => { $(`[data-dimension="${axis}"]`).value = dimensionText(value); });
  $("#stl-resize-editor").classList.remove("hidden");
  setStepState("#resize-state", "可以调整", true, false, true);
  updateResizeSummary();
}

async function loadResizeStlFromUrl(url, force = false) {
  if (!url || (!force && (resizeSourceKind === "local" || loadedResizeUrl === url))) return;
  try {
    $("#resize-source-status").textContent = "正在载入本次生成的 STL…";
    const response = await fetch(url);
    if (!response.ok) throw new Error("本次生成的 STL 下载失败");
    const buffer = await response.arrayBuffer();
    loadedResizeUrl = url;
    setResizeMesh(buffer, "本次生成的 STL", "generated-model.stl", "generated");
  } catch (error) {
    setStepState("#resize-state", "载入失败", false, true);
    notify(error.message || "STL 载入失败", true);
  }
}

function targetDimensions() {
  return [0, 1, 2].map((axis) => Number($(`[data-dimension="${axis}"]`).value));
}

function updateResizeSummary() {
  if (!resizeMesh) return;
  const targets = targetDimensions();
  const valid = targets.every((value) => Number.isFinite(value) && value > 0);
  $("#download-resized-stl").disabled = !valid;
  $("#resize-result-summary").textContent = valid
    ? `输出尺寸：${targets.map((value, axis) => `${"XYZ"[axis]} ${dimensionText(value)} mm`).join(" · ")}`
    : "请输入大于 0 的目标尺寸";
}

function applyUniformScale(percent) {
  if (!resizeMesh || !Number.isFinite(percent) || percent <= 0) return;
  const scale = percent / 100;
  resizeMesh.dimensions.forEach((value, axis) => { $(`[data-dimension="${axis}"]`).value = dimensionText(value * scale); });
  updateResizeSummary();
}

function exportResizedStl() {
  const targets = targetDimensions();
  if (!resizeMesh || !targets.every((value) => Number.isFinite(value) && value > 0)) throw new Error("请输入有效的目标尺寸");
  const scales = targets.map((value, axis) => resizeMesh.dimensions[axis] > 0 ? value / resizeMesh.dimensions[axis] : 1);
  const faceCount = resizeMesh.vertices.length / 9;
  const output = new ArrayBuffer(84 + faceCount * 50);
  const view = new DataView(output);
  new Uint8Array(output, 0, 80).set(new TextEncoder().encode("Resized in browser"));
  view.setUint32(80, faceCount, true);
  for (let face = 0; face < faceCount; face += 1) {
    const source = face * 9;
    const points = [0, 1, 2].map((vertex) => [0, 1, 2].map((axis) => resizeMesh.vertices[source + vertex * 3 + axis] * scales[axis]));
    const a = points[1].map((value, axis) => value - points[0][axis]);
    const b = points[2].map((value, axis) => value - points[0][axis]);
    const normal = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const length = Math.hypot(...normal) || 1;
    const offset = 84 + face * 50;
    normal.forEach((value, axis) => view.setFloat32(offset + axis * 4, value / length, true));
    points.flat().forEach((value, index) => view.setFloat32(offset + 12 + index * 4, value, true));
    view.setUint16(offset + 48, 0, true);
  }
  const url = URL.createObjectURL(new Blob([output], { type: "model/stl" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = resizeFileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function drawStlPreview() {
  const canvas = $("#stl-canvas");
  if (!stlPreview || canvas.classList.contains("hidden")) return;
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.max(240, canvas.clientWidth || 240);
  const height = Math.max(220, canvas.clientHeight || 220);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const cosY = Math.cos(stlPreview.rotationY), sinY = Math.sin(stlPreview.rotationY);
  const cosX = Math.cos(stlPreview.rotationX), sinX = Math.sin(stlPreview.rotationX);
  const scale = Math.min(width, height) * 0.78 * stlPreview.zoom;
  const rotate = ([x, y, z]) => {
    const x1 = x * cosY + z * sinY;
    const z1 = -x * sinY + z * cosY;
    return [x1, y * cosX - z1 * sinX, y * sinX + z1 * cosX];
  };
  const faces = stlPreview.triangles.map((triangle) => {
    const rotated = triangle.map(rotate);
    const ax = rotated[1][0] - rotated[0][0], ay = rotated[1][1] - rotated[0][1];
    const bx = rotated[2][0] - rotated[0][0], by = rotated[2][1] - rotated[0][1];
    return { rotated, depth: rotated.reduce((sum, point) => sum + point[2], 0) / 3, light: Math.max(0.18, Math.min(1, 0.5 + (ax * by - ay * bx) * 2.2)) };
  }).sort((a, b) => a.depth - b.depth);
  faces.forEach(({ rotated, light }) => {
    context.beginPath();
    rotated.forEach((point, index) => {
      const px = width / 2 + point[0] * scale;
      const py = height / 2 - point[1] * scale;
      if (index) context.lineTo(px, py); else context.moveTo(px, py);
    });
    context.closePath();
    const shade = Math.round(120 + light * 105);
    context.fillStyle = `rgb(${shade - 18},${shade},${shade - 8})`;
    context.strokeStyle = "rgba(47,80,65,.18)";
    context.lineWidth = 0.45;
    context.fill();
    context.stroke();
  });
}

function updateElapsed() {
  const start = Number((state?.model_task || state?.hunyuan_task)?.submitted_at || state?.updated_at || 0) * 1000;
  if (!start || !["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status)) {
    $("#task-elapsed").textContent = "";
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const minutes = Math.floor(seconds / 60);
  $("#task-elapsed").textContent = `已等待 ${minutes ? `${minutes} 分 ` : ""}${seconds % 60} 秒；可以返回工作台，当前标签页会继续查询`;
}

function stopPolling() {
  clearTimeout(pollingTimer);
  pollingTimer = null;
  clearInterval(taskClock);
  taskClock = null;
}

function schedulePoll(delay) {
  if (pollingTimer || !["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status)) return;
  pollingTimer = setTimeout(() => {
    pollingTimer = null;
    refreshTask();
  }, delay);
}

async function refresh() {
  if (!session) return;
  try { render(await api(`/api/sessions/${session.id}`)); }
  catch (error) {
    if (error.code === "NOT_FOUND") {
      sessionStorage.removeItem("image3d-session");
      session = null;
      render(null);
    }
    showError(error);
  }
}

async function refreshTask() {
  if (!session || pollingInFlight || !["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status)) return;
  pollingInFlight = true;
  try {
    const path = state.status === "SUBMITTING_3D" ? `/api/sessions/${session.id}` : `/api/sessions/${session.id}/3d`;
    render(await api(path, { timeout: 140_000 }));
    if (state.status === "SUCCEEDED") notify("3D 模型生成完成");
    if (state.status === "FAILED") showError(state.last_error || new Error("3D 生成失败"));
  } catch (error) {
    showError(error);
  } finally {
    pollingInFlight = false;
    schedulePoll(transientError ? 15_000 : 8000);
  }
}

$("#image-input").addEventListener("change", (event) => {
  const files = [...(event.target.files || [])];
  if (files.length > 6) {
    event.target.value = "";
    $("#upload-previews").innerHTML = "";
    return notify("最多选择 6 张参考图", true);
  }
  $("#upload-previews").innerHTML = files.map((file, index) =>
    `<article><img src="${URL.createObjectURL(file)}" alt="待上传参考图 ${index + 1}"><span>${index === 0 ? "主参考" : `补充 ${index}`}</span></article>`
  ).join("");
  $("#drop-copy").textContent = files.length ? `已选择 ${files.length} 张；点击或拖入可重新选择` : "点击选择或拖入 1–6 张 PNG、JPEG 或 WebP 参考图";
  syncControls();
});

function updateViewSource() {
  const sourceInputs = [...document.querySelectorAll('input[name="view-source"]')];
  const uploaded = sourceInputs.find((input) => input.checked).value === "uploaded";
  sourceInputs.forEach((input) => input.closest(".source-option").classList.toggle("selected", input.checked));
  $("#reference-upload").classList.toggle("hidden", uploaded);
  $("#manual-views").classList.toggle("hidden", !uploaded);
  $("#image-input").required = !uploaded;
  allViews.forEach((view) => { $(`#${view}-input`).required = uploaded; });
  $("#upload-submit").textContent = uploaded ? "上传正、左、右、背四视图" : "上传并分析参考图";
}

document.querySelectorAll('input[name="view-source"]').forEach((input) => input.addEventListener("change", updateViewSource));

const modelProviderDetails = {
  hunyuan: {
    name: "腾讯混元 HY 3.1",
    note: "此操作会将确认后的四视图发送至腾讯混元，并按对应账户规则产生费用。HY 3.1 不支持 LowPoly 智能拓扑；STL 只保存物体形状，不包含普通彩色纹理。",
  },
  meshy: {
    name: "Meshy 7.1",
    note: "此操作会将确认后的四视图发送至 Meshy，并按 Meshy 7.1 的账户规则产生费用。STL 只保存物体形状，不包含普通彩色纹理。",
  },
};

function selectedModelProvider() {
  return document.querySelector('input[name="model-provider"]:checked')?.value || "meshy";
}

function updateModelProvider() {
  const provider = selectedModelProvider();
  const details = modelProviderDetails[provider];
  $("#model-provider-heading").textContent = `确认四视图后，即可提交${details.name}生成模型。`;
  $("#model-provider-note").textContent = details.note;
  updateGenerateType();
}

function updateGenerateType() {
  const generateType = $("#generate-type").value;
  const geometryOnly = generateType === "Geometry";
  const details = modelProviderDetails[selectedModelProvider()];
  $("#enable-pbr").disabled = geometryOnly;
  if (geometryOnly) $("#enable-pbr").checked = false;
  $("#generate-type-description").textContent = geometryOnly
    ? `使用${details.name}生成无纹理白模，适合 3D 打印。`
    : `使用${details.name}执行带颜色和表面纹理的生成流程；最终 STL 文件仍只保存几何形状。`;
}

$("#generate-type").addEventListener("change", updateGenerateType);
document.querySelectorAll('input[name="model-provider"]').forEach((input) => input.addEventListener("change", updateModelProvider));

async function uploadSelectedReferences(button) {
  if (session && state?.status === "REFERENCES_READY") return;
  const referenceFiles = [...($("#image-input").files || [])];
  if (!referenceFiles.length) throw new Error("请先选择参考图");
  if (referenceFiles.length > 6) throw new Error("最多上传 6 张参考图");
  if (session || state) throw new Error("当前任务状态不能重新上传参考图，请刷新页面后重试");

  button.textContent = "正在准备参考图…";
  const form = new FormData();
  form.set("image", await compressedFile(referenceFiles[0]));
  form.set("prompt", "");
  form.set("view_source", "generated");
  const result = await api("/api/sessions", { method: "POST", body: form, timeout: 120_000 });
  session = { id: result.session_id, token: result.access_token };
  sessionStorage.setItem("image3d-session", JSON.stringify(session));
  render(result);
  for (let index = 1; index < referenceFiles.length; index += 1) {
    button.textContent = `正在准备参考图 ${index + 1}/${referenceFiles.length}…`;
    const body = new FormData();
    body.set("image", await compressedFile(referenceFiles[index]));
    render(await api(`/api/sessions/${session.id}/references`, { method: "POST", body, timeout: 90_000 }));
  }
}

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  try {
    clearError();
    setActionInFlight(true);
    const viewSource = document.querySelector('input[name="view-source"]:checked').value;
    if (viewSource === "generated") {
      await uploadSelectedReferences(button);
      setBusy(button, true, "AI 正在分析当前图片…");
      render(await api(`/api/sessions/${session.id}/analyze`, { method: "POST", timeout: 140_000 }));
      notify("AI 已基于原始参考图生成描述");
      $("#prompt-step").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    const uploadedViews = Object.fromEntries(allViews.map((view) => [view, $(`#${view}-input`).files?.[0]]));
    if (viewSource === "uploaded" && Object.values(uploadedViews).some((file) => !file)) {
      throw new Error("请分别选择正视图、左视图、右视图和背视图");
    }
    setBusy(button, true, viewSource === "uploaded" ? "正在上传四视图…" : "正在上传参考图…");
    const source = uploadedViews.front;
    if (!source) throw new Error("请选择图片");
    const file = await compressedFile(source);
    const form = new FormData();
    form.set("image", file);
    form.set("prompt", "");
    form.set("view_source", viewSource);
    const result = await api("/api/sessions", { method: "POST", body: form, timeout: 120_000 });
    session = { id: result.session_id, token: result.access_token };
    sessionStorage.setItem("image3d-session", JSON.stringify(session));
    render(result);
    if (viewSource === "uploaded") {
      for (const view of ["left", "right", "back"]) {
        button.textContent = `正在上传${labels[view]}…`;
        const body = new FormData();
        body.set("image", await compressedFile(uploadedViews[view]));
        render(await api(`/api/sessions/${session.id}/views/${view}/upload`, { method: "POST", body, timeout: 90_000 }));
      }
      notify("正、左、右、背四视图已上传");
    }
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
});

async function generateViews(button, regenerateAll = false) {
  try {
    clearError();
    setActionInFlight(true);
    setBusy(button, true, "正在生成四个视角…");
    const prompt = $("#prompt-editor").value.trim();
    if (!prompt) throw new Error("主体描述不能为空");
    if (prompt !== state.user_prompt) {
      render(await api(`/api/sessions/${session.id}/prompt`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
      }));
    }
    const missing = allViews.filter((view) => state?.views?.[view]?.prompt_revision !== state?.prompt_revision);
    const views = regenerateAll || missing.length === 0 ? allViews : missing;
    button.textContent = views.length === 4 ? "正在生成四个视角…" : `正在重试${views.map((view) => labels[view]).join("、")}…`;
    render(await api(`/api/sessions/${session.id}/views`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ views }), timeout: 295_000,
    }));
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
}

$("#confirm-description").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    clearError();
    setActionInFlight(true);
    setBusy(button, true, "正在确认…");
    const prompt = $("#prompt-editor").value.trim();
    if (!prompt) throw new Error("AI 图片描述不能为空");
    if (prompt !== state.user_prompt) {
      render(await api(`/api/sessions/${session.id}/prompt`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
      }));
    }
    render(await api(`/api/sessions/${session.id}/description/confirm`, { method: "POST" }));
    notify("AI 对图片的描述已确认，现在可以生成四视图");
    $("#views-step").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
});

$("#generate-views").addEventListener("click", (event) => generateViews(event.currentTarget));
$("#regenerate-views").addEventListener("click", (event) => generateViews(event.currentTarget, true));

async function refine(view, button) {
  const prompt = $(`#refine-${view}`).value.trim();
  if (!prompt) return notify("请输入该视图的微调要求", true);
  try {
    clearError();
    setActionInFlight(true);
    setBusy(button, true);
    render(await api(`/api/sessions/${session.id}/views/${view}/refine`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
      timeout: 295_000,
    }));
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
}

async function replaceUploadedView(view, input) {
  const file = input.files?.[0];
  if (!file) return;
  try {
    clearError();
    setActionInFlight(true);
    const body = new FormData();
    body.set("image", await compressedFile(file));
    render(await api(`/api/sessions/${session.id}/views/${view}/upload`, { method: "POST", body, timeout: 90_000 }));
    notify(`${labels[view]}已替换，请重新确认`);
  } catch (error) {
    showError(error);
    await refresh();
  } finally {
    setActionInFlight(false);
  }
}

$("#confirm-views").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    clearError();
    setActionInFlight(true);
    setBusy(button, true);
    render(await api(`/api/sessions/${session.id}/confirm`, { method: "POST" }));
    notify("正、左、右、背四个视角已确认，现在可以生成 STL");
    $("#generate-step").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
});

$("#generate-3d").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    clearError();
    setActionInFlight(true);
    setBusy(button, true, "正在提交…");
    render(await api(`/api/sessions/${session.id}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_provider: selectedModelProvider(), generate_type: $("#generate-type").value, enable_pbr: $("#enable-pbr").checked, result_format: "STL" }),
      timeout: 140_000,
    }));
  } catch (error) { showError(error); await refresh(); }
  finally { setBusy(button, false); setActionInFlight(false); }
});

$("#dismiss-error").addEventListener("click", clearError);
$("#refresh-task").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true, "刷新中…");
  try { clearError(); await refreshTask(); }
  finally { setBusy(button, false); }
});

$("#use-generated-stl").addEventListener("click", () => loadResizeStlFromUrl(generatedStlUrl, true));

$("#resize-stl-input").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    $("#resize-source-status").textContent = "正在读取本地 STL…";
    setResizeMesh(await file.arrayBuffer(), `本地文件：${file.name}`, file.name, "local");
  } catch (error) {
    resizeMesh = null;
    $("#stl-resize-editor").classList.add("hidden");
    setStepState("#resize-state", "文件无效", false, true);
    notify(error.message || "无法读取 STL 文件", true);
  }
});

$("#scale-percent").addEventListener("input", (event) => applyUniformScale(Number(event.target.value)));

document.querySelectorAll("[data-dimension]").forEach((input) => input.addEventListener("input", () => {
  if (!resizeMesh) return;
  const axis = Number(input.dataset.dimension);
  const target = Number(input.value);
  if ($("#lock-proportions").checked && Number.isFinite(target) && target > 0 && resizeMesh.dimensions[axis] > 0) {
    const scale = target / resizeMesh.dimensions[axis];
    resizeMesh.dimensions.forEach((value, index) => {
      if (index !== axis) $(`[data-dimension="${index}"]`).value = dimensionText(value * scale);
    });
    $("#scale-percent").value = dimensionText(scale * 100);
  } else if (!$("#lock-proportions").checked) {
    $("#scale-percent").value = "";
  }
  updateResizeSummary();
}));

$("#lock-proportions").addEventListener("change", (event) => {
  if (!event.target.checked || !resizeMesh) return;
  const firstAxis = resizeMesh.dimensions.findIndex((value) => value > 0);
  if (firstAxis < 0) return;
  const current = Number($(`[data-dimension="${firstAxis}"]`).value);
  if (Number.isFinite(current) && current > 0) {
    const scale = current / resizeMesh.dimensions[firstAxis];
    $("#scale-percent").value = dimensionText(scale * 100);
    applyUniformScale(scale * 100);
  }
});

$("#download-resized-stl").addEventListener("click", () => {
  try {
    exportResizedStl();
    notify("调整后的 STL 已开始下载");
  } catch (error) {
    notify(error.message || "STL 导出失败", true);
  }
});

function initializeStlPreview() {
  const canvas = $("#stl-canvas");
  let dragging = false;
  let previousX = 0;
  let previousY = 0;
  canvas.addEventListener("pointerdown", (event) => {
    dragging = true;
    previousX = event.clientX;
    previousY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging || !stlPreview) return;
    stlPreview.rotationY += (event.clientX - previousX) * 0.012;
    stlPreview.rotationX += (event.clientY - previousY) * 0.012;
    previousX = event.clientX;
    previousY = event.clientY;
    drawStlPreview();
  });
  canvas.addEventListener("pointerup", () => { dragging = false; });
  canvas.addEventListener("pointercancel", () => { dragging = false; });
  canvas.addEventListener("wheel", (event) => {
    if (!stlPreview) return;
    event.preventDefault();
    stlPreview.zoom = Math.max(0.35, Math.min(2.5, stlPreview.zoom * (event.deltaY > 0 ? 0.9 : 1.1)));
    drawStlPreview();
  }, { passive: false });
  if ("ResizeObserver" in window) new ResizeObserver(drawStlPreview).observe(canvas);
}

function viewCards(views = {}) {
  return allViews.map((view) => {
    const item = views[view];
    return `<article class="view-card"><h3>${labels[view]}</h3>${item?.url
      ? `<img data-zoom src="${item.url}" alt="${labels[view]}">`
      : "<p>图片不可用</p>"}</article>`;
  }).join("");
}

function renderViewFolderCards(target, selectable = false) {
  target.innerHTML = viewFolders.map((folder) => `
    <article class="view-folder-card">
      <button class="view-folder-cover" type="button" data-open-view-folder="${folder.id}">
        <img src="${folder.views.front.url}" alt="${escapeHtml(folder.name)}的正视图">
      </button>
      <div><strong>${escapeHtml(folder.name)}</strong><small>${new Date(folder.created_at * 1000).toLocaleString("zh-CN")}</small></div>
      <div class="view-folder-actions">
        <button class="secondary" type="button" data-open-view-folder="${folder.id}">查看全图</button>
        ${selectable ? `<button class="primary" type="button" data-select-view-folder="${folder.id}">选择</button>` : `<button class="danger" type="button" data-delete-view-folder="${folder.id}">删除</button>`}
      </div>
      ${!selectable && folder.color_schemes?.length ? `<div class="view-folder-schemes"><small>配色方案 · ${folder.color_schemes.length}</small>${folder.color_schemes.map((scheme) => `
        <button class="scheme-row" type="button" data-open-color-scheme="${scheme.id}" data-folder-id="${folder.id}">
          <img src="${scheme.views.front.url}" alt=""><span>${escapeHtml(scheme.name)}</span>
        </button>`).join("")}</div>` : ""}
    </article>`).join("");
}

function bindViewFolderActions(root) {
  root.querySelectorAll("[data-open-color-scheme]").forEach((button) => button.addEventListener("click", () => {
    const folder = viewFolders.find((item) => item.id === button.dataset.folderId);
    const scheme = folder?.color_schemes?.find((item) => item.id === button.dataset.openColorScheme);
    if (!scheme) return;
    $("#view-folder-dialog-title").textContent = `${folder.name} / ${scheme.name}`;
    $("#view-folder-dialog-grid").innerHTML = viewCards(scheme.views);
    $("#view-folder-dialog").showModal();
  }));
  root.querySelectorAll("[data-open-view-folder]").forEach((button) => button.addEventListener("click", () => {
    const folder = viewFolders.find((item) => item.id === button.dataset.openViewFolder);
    if (!folder) return;
    $("#view-folder-dialog-title").textContent = folder.name;
    $("#view-folder-dialog-grid").innerHTML = viewCards(folder.views);
    $("#view-folder-dialog").showModal();
  }));
  root.querySelectorAll("[data-delete-view-folder]").forEach((button) => button.addEventListener("click", async () => {
    const folder = viewFolders.find((item) => item.id === button.dataset.deleteViewFolder);
    if (!folder || !confirm(`确定删除“四视图库 / ${folder.name}”吗？`)) return;
    try {
      await api(`/api/view-library/folders/${folder.id}`, { method: "DELETE" });
      if (selectedColorFolderId === folder.id) {
        selectedColorFolderId = null;
        colorDraft = null;
        $("#color-view-title").textContent = "尚未选择四视图";
        $("#color-view-preview").classList.add("hidden");
        $("#color-draft").classList.add("hidden");
        $("#generate-color-front").disabled = true;
      }
      await loadViewLibrary();
      notify("四视图文件夹已删除");
    } catch (error) { showError(error); }
  }));
  root.querySelectorAll("[data-select-view-folder]").forEach((button) => button.addEventListener("click", () => {
    const folder = viewFolders.find((item) => item.id === button.dataset.selectViewFolder);
    if (!folder) return;
    selectedColorFolderId = folder.id;
    colorDraft = null;
    $("#color-view-title").textContent = `已选择：${folder.name}`;
    $("#color-view-preview").innerHTML = viewCards(folder.views);
    $("#color-view-preview").classList.remove("hidden");
    $("#generate-color-front").disabled = false;
    $("#color-draft").classList.add("hidden");
    $("#color-workflow-state").textContent = "可以开始";
    $("#choose-view-dialog").close();
  }));
}

function renderColorDraft() {
  if (!colorDraft) return;
  const analysis = colorDraft.analysis || {};
  $("#color-draft").classList.remove("hidden");
  $("#color-scheme-name").textContent = colorDraft.name || "AI配色";
  $("#color-analysis-summary").textContent = [
    analysis.object,
    analysis.parts?.length ? `部件：${analysis.parts.join("、")}` : "",
    analysis.materials?.length ? `材质：${analysis.materials.join("、")}` : "",
    analysis.style ? `风格：${analysis.style}` : "",
  ].filter(Boolean).join("；") || "AI 未能稳定识别，已按可见外形生成配色。";
  $("#color-palette").innerHTML = (analysis.palette || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("") || "<span>未识别具体色板</span>";
  $("#color-result-grid").innerHTML = ["COMPLETE", "SAVED"].includes(colorDraft.status)
    ? viewCards(colorDraft.views)
    : `<article class="view-card"><h3>正视图配色候选</h3><img data-zoom src="${colorDraft.views.front.url}" alt="AI 生成的正视图配色候选"></article>`;
  bindZoomImages($("#color-result-grid"));
  const complete = ["COMPLETE", "SAVED"].includes(colorDraft.status);
  $("#complete-color-views").classList.toggle("hidden", complete);
  $("#retry-color-front").classList.toggle("hidden", complete);
  $("#save-color-scheme").classList.toggle("hidden", !complete || colorDraft.status === "SAVED");
  $("#color-workflow-state").textContent = colorDraft.status === "SAVED" ? "已保存" : complete ? "四视图已完成" : "等待确认正视图";
}

function bindZoomImages(root) {
  root.querySelectorAll("[data-zoom]").forEach((image) => image.addEventListener("click", () => window.open(image.src, "_blank", "noopener")));
}

async function generateColoredFront(button) {
  if (!selectedColorFolderId) return notify("请先选择一套四视图", true);
  const previousScheme = colorDraft ? {
    previous_name: colorDraft.name || "",
    previous_palette: colorDraft.analysis?.palette || [],
  } : {};
  const replacing = Boolean(colorDraft);
  setBusy(button, true, replacing ? "AI 正在重选配色…" : "AI 正在分析并上色…");
  $("#color-workflow-state").textContent = replacing ? "重新选择配色" : "AI 分析中";
  try {
    colorDraft = await api(`/api/view-library/folders/${selectedColorFolderId}/color-drafts/front`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(previousScheme), timeout: 360_000,
    });
    renderColorDraft();
    notify(replacing ? "已重新选择不同配色并生成正视图" : "正视图配色已生成，请确认是否满意");
  } catch (error) { showError(error); }
  finally { setBusy(button, false); }
}

$("#generate-color-front").addEventListener("click", (event) => generateColoredFront(event.currentTarget));
$("#retry-color-front").addEventListener("click", (event) => generateColoredFront(event.currentTarget));
$("#complete-color-views").addEventListener("click", async (event) => {
  if (!colorDraft) return;
  const button = event.currentTarget;
  setBusy(button, true, "正在统一其余三个视角…");
  $("#color-workflow-state").textContent = "生成其余三张";
  try {
    colorDraft = await api(`/api/view-library/color-drafts/${colorDraft.id}/complete`, {
      method: "POST", timeout: 360_000,
    });
    renderColorDraft();
    notify("配色四视图已全部生成");
  } catch (error) { showError(error); }
  finally { setBusy(button, false); }
});
$("#save-color-scheme").addEventListener("click", async (event) => {
  if (!colorDraft) return;
  const button = event.currentTarget;
  setBusy(button, true, "正在保存…");
  try {
    const saved = await api(`/api/view-library/color-drafts/${colorDraft.id}/save`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: colorDraft.name }),
    });
    colorDraft.status = "SAVED";
    colorDraft.name = saved.name;
    renderColorDraft();
    await loadViewLibrary();
    notify(`配色方案“${saved.name}”已保存到原四视图文件夹`);
  } catch (error) { showError(error); }
  finally { setBusy(button, false); }
});

async function loadViewLibrary() {
  try {
    const result = await api("/api/view-library/folders");
    viewFolders = result.folders || [];
    $("#view-library-empty").classList.toggle("hidden", viewFolders.length > 0);
    renderViewFolderCards($("#view-library-grid"));
    renderViewFolderCards($("#choose-view-grid"), true);
    bindViewFolderActions($("#view-library-grid"));
    bindViewFolderActions($("#choose-view-grid"));
  } catch (error) { showError(error); }
}

$("#save-view-folder").addEventListener("click", () => {
  if (!session || !allViews.every((view) => state?.views?.[view]?.url)) return notify("请先完成四视图", true);
  $("#save-view-form").reset();
  $("#save-view-dialog").showModal();
  $("#view-folder-name").focus();
});
$("#save-view-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("#view-folder-name").value.trim();
  if (!name) return;
  const button = event.submitter;
  setBusy(button, true, "正在保存…");
  try {
    await api("/api/view-library/folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, session_id: session.id }),
    });
    $("#save-view-dialog").close();
    await loadViewLibrary();
    notify("四视图已加入数据库");
  } catch (error) { showError(error); }
  finally { setBusy(button, false); }
});
$("#cancel-save-view").addEventListener("click", () => $("#save-view-dialog").close());
$("#close-view-folder").addEventListener("click", () => $("#view-folder-dialog").close());
$("#choose-color-views").addEventListener("click", () => {
  renderViewFolderCards($("#choose-view-grid"), true);
  bindViewFolderActions($("#choose-view-grid"));
  $("#choose-view-dialog").showModal();
});
$("#close-choose-view").addEventListener("click", () => $("#choose-view-dialog").close());
$("#refresh-view-library").addEventListener("click", loadViewLibrary);

initializeStlPreview();
initializeUsageGuide();
setWorkspace(new URLSearchParams(location.search).get("workspace") || "3d", false);
updateViewSource();
updateModelProvider();
render(null);
refresh();
loadViewLibrary();
