const $ = (selector) => document.querySelector(selector);
if (new URLSearchParams(location.search).get("embedded") === "1") {
  document.body.classList.add("embedded");
}
const labels = { front: "正视图", left: "左视图", right: "右视图", back: "背视图" };
const allViews = ["front", "left", "right", "back"];
const defaultModelSettings = {
  vision_model: "gpt-6-luna",
  image_model: "gpt-image-2.5-flare",
  image_quality: "medium",
  hunyuan_model: "hy-3d-3.1",
};
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

function loadModelSettings() {
  try {
    return { ...defaultModelSettings, ...JSON.parse(localStorage.getItem("image3d-model-settings") || "{}") };
  } catch (_) {
    return { ...defaultModelSettings };
  }
}

function readModelSettings() {
  return {
    vision_model: $("#vision-model").value.trim(),
    image_model: $("#image-model").value.trim(),
    image_quality: $("#image-quality").value,
    hunyuan_model: $("#hunyuan-model").value.trim(),
  };
}

function initializeModelSettings() {
  const models = loadModelSettings();
  $("#vision-model").value = models.vision_model;
  $("#image-model").value = models.image_model;
  $("#image-quality").value = models.image_quality;
  $("#hunyuan-model").value = models.hunyuan_model;
  ["#vision-model", "#image-model", "#image-quality", "#hunyuan-model"].forEach((selector) => {
    $(selector).addEventListener("change", () => {
      localStorage.setItem("image3d-model-settings", JSON.stringify(readModelSettings()));
    });
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
    ? "手动上传模式不进行 AI 分析，也不会调用图片生成模型。"
    : "上传正面主图后显示识别结果。";
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
    const replacement = uploaded && ["PROMPT_REVIEW", "VIEWS_REVIEW", "CONFIRMED", "FAILED"].includes(state?.status)
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
  const canEditPrompt = Boolean(state) && !uploaded && ["PROMPT_REVIEW", "VIEWS_REVIEW", "FAILED"].includes(status);
  const canGenerateViews = canEditPrompt;
  $("#prompt-editor").disabled = !canEditPrompt;
  $("#generate-views").disabled = !canGenerateViews || actionInFlight;
  $("#regenerate-views").disabled = !canGenerateViews || actionInFlight;
  $("#confirm-views").disabled = !state || state.views_stale || status !== "VIEWS_REVIEW" || actionInFlight;
  if (!$("#confirm-views").dataset.busy) {
    $("#confirm-views").textContent = status === "CONFIRMED" || state?.confirmed_at ? "已确认 ✓" : "确认四个视角";
  }
  const generateButton = $("#generate-3d");
  const running = ["SUBMITTING_3D", "GENERATING_3D"].includes(status);
  const canSubmit3d = ["CONFIRMED", "FAILED"].includes(status) && Boolean(state?.confirmed_at);
  generateButton.disabled = !canSubmit3d || actionInFlight;
  generateButton.classList.toggle("pending", running);
  if (!generateButton.dataset.busy) {
    generateButton.textContent = running ? "正在生成 STL…" : status === "FAILED" ? "重新生成 STL" : "提交 STL 任务";
  }

  setStepState("#upload-state", !state ? "等待上传" : status === "GENERATING_VIEWS" ? "正在生成" : uploaded ? "四视图已上传" : "参考图已上传", Boolean(state), false);
  if (!state) setStepState("#views-state", "等待生成", false, false);
  else if (status === "GENERATING_VIEWS") setStepState("#views-state", "正在生成", true, false);
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
  renderAnalysis(state?.analysis || {});
  $("#prompt-editor").value = state?.user_prompt || "";
  renderViews(state?.views || {});
  syncControls();
  renderTask();
  renderError();
}

function renderTask() {
  const task = state?.hunyuan_task;
  const running = ["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status);
  $("#task-progress").classList.toggle("hidden", !running);
  $("#task-stage").textContent = state?.status === "SUBMITTING_3D" ? "正在提交腾讯混元…" : task?.status === "queued" ? "任务已进入队列" : "腾讯混元正在生成 STL…";
  $("#task-id").textContent = task?.id ? `任务编号：${task.id}` : "正在等待任务编号";
  updateElapsed();
  const models = task?.models || {};
  $("#model-results").innerHTML = Object.entries(models).filter(([type]) => type.toLowerCase() === "stl").map(([type, model]) =>
    `<a class="model-link" href="${model.url}" download="model-${state.session_id.slice(0, 8)}.stl"><span>${type.toUpperCase()} 模型</span><span>下载文件 ↓</span></a>`
  ).join("");
  const stl = models.stl || models.STL;
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
    const triangles = parseStl(await response.arrayBuffer());
    if (!triangles.length) throw new Error("STL 预览格式无效");
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
  const start = Number(state?.hunyuan_task?.submitted_at || state?.updated_at || 0) * 1000;
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
  const file = event.target.files?.[0];
  if (!file) return;
  $("#upload-preview").src = URL.createObjectURL(file);
  $("#upload-preview").classList.remove("hidden");
  $("#drop-copy").classList.add("hidden");
});

function updateViewSource() {
  const sourceInputs = [...document.querySelectorAll('input[name="view-source"]')];
  const uploaded = sourceInputs.find((input) => input.checked).value === "uploaded";
  sourceInputs.forEach((input) => input.closest(".source-option").classList.toggle("selected", input.checked));
  $("#reference-upload").classList.toggle("hidden", uploaded);
  $("#manual-views").classList.toggle("hidden", !uploaded);
  $("#prompt-requirement").classList.toggle("hidden", uploaded);
  $("#image-input").required = !uploaded;
  allViews.forEach((view) => { $(`#${view}-input`).required = uploaded; });
  $("#upload-submit").textContent = uploaded ? "上传正、左、右、背四视图" : "生成正、左、右、背四视图";
}

document.querySelectorAll('input[name="view-source"]').forEach((input) => input.addEventListener("change", updateViewSource));

function updateGenerateType() {
  const geometryOnly = $("#generate-type").value === "Geometry";
  $("#enable-pbr").disabled = geometryOnly;
  if (geometryOnly) $("#enable-pbr").checked = false;
}

$("#generate-type").addEventListener("change", updateGenerateType);

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  try {
    clearError();
    setActionInFlight(true);
    const viewSource = document.querySelector('input[name="view-source"]:checked').value;
    const uploadedViews = Object.fromEntries(allViews.map((view) => [view, $(`#${view}-input`).files?.[0]]));
    if (viewSource === "uploaded" && Object.values(uploadedViews).some((file) => !file)) {
      throw new Error("请分别选择正视图、左视图、右视图和背视图");
    }
    setBusy(button, true, viewSource === "uploaded" ? "正在上传四视图…" : "GPT 正在分析…");
    const source = viewSource === "uploaded" ? uploadedViews.front : $("#image-input").files?.[0];
    if (!source) throw new Error("请选择图片");
    const file = await compressedFile(source);
    const form = new FormData();
    form.set("image", file);
    form.set("prompt", $("#initial-prompt").value.trim());
    form.set("view_source", viewSource);
    const models = readModelSettings();
    if (!models.vision_model || !models.image_model || !models.hunyuan_model) {
      throw new Error("请填写完整的模型设置");
    }
    Object.entries(models).forEach(([name, value]) => form.set(name, value));
    localStorage.setItem("image3d-model-settings", JSON.stringify(models));
    const result = await api("/api/sessions", { method: "POST", body: form, timeout: 120_000 });
    session = { id: result.session_id, token: result.access_token };
    sessionStorage.setItem("image3d-session", JSON.stringify(session));
    render(result);
    if (viewSource === "generated" && ["SIDE", "BACK", "TOP"].includes(result.analysis?.source_view)) {
      const error = new Error("当前图片不是正面或斜前方视角，请重新选择接近正面的主图");
      error.code = "FRONT_VIEW_REQUIRED";
      throw error;
    }
    if (viewSource === "uploaded") {
      for (const view of ["left", "right", "back"]) {
        button.textContent = `正在上传${labels[view]}…`;
        const body = new FormData();
        body.set("image", await compressedFile(uploadedViews[view]));
        render(await api(`/api/sessions/${session.id}/views/${view}/upload`, { method: "POST", body, timeout: 90_000 }));
      }
      notify("正、左、右、背四视图已上传");
    } else {
      button.textContent = "正在生成四个视角…";
      render(await api(`/api/sessions/${session.id}/views`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ views: allViews }),
        timeout: 295_000,
      }));
      notify("正、左、右、背四视图已生成");
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
    notify("正、左、右、背四个视角已确认");
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
      body: JSON.stringify({ generate_type: $("#generate-type").value, enable_pbr: $("#enable-pbr").checked, result_format: "STL" }),
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

initializeModelSettings();
initializeStlPreview();
initializeUsageGuide();
updateViewSource();
updateGenerateType();
render(null);
refresh();
