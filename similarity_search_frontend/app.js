const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {
  libraryId: null,
  token: null,
  selectedFiles: [],
  queryFile: null,
  queryImage: null,
  queryMethod: "original",
  queryCrop: null,
  cropPointerId: null,
  queryMask: null,
  subjectSelectionMode: null,
  subjectPreview: false,
  segmentationSessionId: null,
  segmentationPoints: [],
  segmentationPointLabel: 1,
  segmentationBusy: false,
  segmentationMeta: null,
  segmentationGeneration: 0,
  folders: [],
  trash: [],
  objectUrls: new Set(),
  activeFolder: null,
  reindexBusy: false,
};
const labels = { image: "图片", dxf: "DXF", illustrator: "Illustrator" };
const allowed = /\.(png|jpe?g|webp|dxf|ai)$/i;
const uploadChunkBytes = 2_500_000;
const previewConcurrency = 3;
let libraryLoadSequence = 0;
let previewActive = 0;
let previewPaused = false;
const previewQueue = [];
const previewPending = new WeakSet();
const previewControllers = new Set();

function bytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function kindOf(name) {
  const suffix = name.toLowerCase().split(".").pop();
  if (["png", "jpg", "jpeg", "webp"].includes(suffix)) return "image";
  if (suffix === "dxf") return "dxf";
  if (suffix === "ai") return "illustrator";
  return null;
}

function headers(json = false) {
  const value = { "X-Material-Library": state.libraryId, "X-Material-Token": state.token };
  if (json) value["Content-Type"] = "application/json";
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: { ...headers(options.json), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const fallback = `请求失败（${response.status}）`;
    let detail = fallback;
    const body = await response.text();
    if (body) {
      try {
        const payload = JSON.parse(body);
        if (typeof payload.message === "string") detail = payload.message;
        else if (typeof payload.detail === "string") detail = payload.detail;
        else if (Array.isArray(payload.detail)) {
          detail = payload.detail.map(item => {
            const location = Array.isArray(item.loc) ? item.loc.filter(part => part !== "body").join(".") : "";
            return `${location ? `${location}：` : ""}${item.msg || "请求参数无效"}`;
          }).join("；");
        }
      } catch {
        detail = body.trim().slice(0, 500) || fallback;
      }
    }
    const error = new Error(detail);
    error.status = response.status;
    error.path = path;
    throw error;
  }
  return response;
}

async function sha256Hex(blob) {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}

async function ensureLibrary() {
  // The server returns the same public library to every visitor. Always
  // bootstrap it so browsers carrying credentials from the old private-library
  // version are moved to the shared library immediately.
  const response = await fetch("/api/material-search/libraries", { method: "POST" });
  if (!response.ok) throw new Error("无法创建素材库");
  const library = await response.json();
  state.libraryId = library.library_id;
  state.token = library.access_token;
  localStorage.setItem("lingjie-material-library", state.libraryId);
  localStorage.setItem("lingjie-material-token", state.token);
}

function setMessage(selector, text, type = "") {
  const element = $(selector);
  element.textContent = text;
  element.className = `message ${type}`.trim();
}

function safeText(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}

function clearObjectUrls() {
  for (const url of state.objectUrls) URL.revokeObjectURL(url);
  state.objectUrls.clear();
}

async function protectedObjectUrl(path, signal) {
  const response = await api(path, { signal });
  const url = URL.createObjectURL(await response.blob());
  state.objectUrls.add(url);
  return url;
}

function pumpPreviewQueue() {
  if (previewPaused) return;
  while (previewActive < previewConcurrency && previewQueue.length) {
    const job = previewQueue.shift();
    if (!job.image.isConnected || job.image.src) {
      previewPending.delete(job.image);
      continue;
    }
    previewActive += 1;
    const controller = new AbortController();
    previewControllers.add(controller);
    protectedObjectUrl(job.path, controller.signal)
      .then(url => {
        if (job.image.isConnected) job.image.src = url;
        else URL.revokeObjectURL(url);
      })
      .catch(error => {
        if (error.name === "AbortError") {
          if (job.image.isConnected) previewQueue.unshift(job);
          else previewPending.delete(job.image);
          return;
        }
        previewPending.delete(job.image);
        if (job.image.isConnected) {
          job.image.replaceWith(Object.assign(document.createElement("span"), {
            className: "thumb-placeholder",
            textContent: "预览失败",
          }));
        }
      })
      .finally(() => {
        previewControllers.delete(controller);
        previewActive -= 1;
        if (job.image.src) previewPending.delete(job.image);
        pumpPreviewQueue();
      });
  }
}

function enqueueProtectedImage(image, path) {
  if (!image.isConnected || image.src || previewPending.has(image)) return;
  previewPending.add(image);
  previewQueue.push({ image, path });
  pumpPreviewQueue();
}

const previewObserver = "IntersectionObserver" in window
  ? new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        previewObserver.unobserve(entry.target);
        enqueueProtectedImage(entry.target, entry.target.dataset.protectedSrc);
      }
    }, { rootMargin: "240px" })
  : null;

function setProtectedImage(image, path) {
  image.loading = "lazy";
  image.decoding = "async";
  image.dataset.protectedSrc = path;
  if (previewObserver) previewObserver.observe(image);
  else enqueueProtectedImage(image, path);
}

function pausePreviewLoading() {
  previewPaused = true;
  for (const controller of previewControllers) controller.abort();
}

function resumePreviewLoading() {
  previewPaused = false;
  pumpPreviewQueue();
}

function folderName(files) {
  const relative = files[0]?.webkitRelativePath || "";
  return relative.split("/")[0] || "新素材文件夹";
}

function renderSelection() {
  const selection = $("#selection");
  const name = $("#folder-name").value.trim();
  if (!state.selectedFiles.length) {
    selection.className = "selection empty";
    selection.innerHTML = "<p>尚未选择文件夹</p>";
    $("#upload-button").disabled = true;
    return;
  }
  const kinds = new Set(state.selectedFiles.map(file => kindOf(file.name)));
  const complete = ["image", "illustrator"].every(kind => kinds.has(kind));
  selection.className = "selection";
  selection.innerHTML = `<div class="selected-title"><span>${safeText(folderName(state.selectedFiles))}</span><small>${state.selectedFiles.length} 个有效文件</small></div><ul class="selected-files">${state.selectedFiles.map(file => `<li><span class="type-pill">${labels[kindOf(file.name)]}</span><span>${safeText(file.webkitRelativePath || file.name)}</span><small>${bytes(file.size)}</small></li>`).join("")}</ul>`;
  $("#upload-button").disabled = !name || !complete;
  if (!name) setMessage("#upload-message", "文件夹名称为必填项。", "error");
  else if (!complete) setMessage("#upload-message", "需要同时包含图片和Illustrator AI文件，DXF可选。", "error");
  else setMessage("#upload-message", "文件检查通过，可以开始上传。", "success");
}

function uploadId() {
  return crypto.randomUUID().replaceAll("-", "");
}

function subjectCanvasPoint(event) {
  const canvas = $("#subject-canvas");
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(canvas.width, (event.clientX - bounds.left) * canvas.width / bounds.width)),
    y: Math.max(0, Math.min(canvas.height, (event.clientY - bounds.top) * canvas.height / bounds.height)),
  };
}

function normalisedSubjectPoint(event) {
  const canvas = $("#subject-canvas");
  const point = subjectCanvasPoint(event);
  return { x: point.x / canvas.width, y: point.y / canvas.height };
}

function invalidateSubjectMask() {
  state.queryMask = null;
  state.subjectPreview = false;
}

function disposeSegmentationSession() {
  state.segmentationGeneration += 1;
  const sessionId = state.segmentationSessionId;
  state.segmentationSessionId = null;
  state.segmentationMeta = null;
  if (!sessionId) return;
  api(`/api/material-search/segmentations/${sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
}

function drawAutoPoints(context, width, height) {
  for (const point of state.segmentationPoints) {
    const x = point.x * width;
    const y = point.y * height;
    const radius = Math.max(5, Math.min(width, height) / 70);
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = point.label === 1 ? "#8fbd36" : "#ef5b57";
    context.fill();
    context.lineWidth = Math.max(2, radius / 3);
    context.strokeStyle = "#fff";
    context.stroke();
    context.beginPath();
    context.moveTo(x - radius * .48, y);
    context.lineTo(x + radius * .48, y);
    if (point.label === 1) {
      context.moveTo(x, y - radius * .48);
      context.lineTo(x, y + radius * .48);
    }
    context.lineWidth = Math.max(1.5, radius / 4);
    context.strokeStyle = "#fff";
    context.stroke();
  }
}

function normalisedCrop(crop = state.queryCrop) {
  if (!crop) return null;
  const left = Math.max(0, Math.min(crop.x1, crop.x2));
  const top = Math.max(0, Math.min(crop.y1, crop.y2));
  const right = Math.min(1, Math.max(crop.x1, crop.x2));
  const bottom = Math.min(1, Math.max(crop.y1, crop.y2));
  if (right - left < .005 || bottom - top < .005) return null;
  return { left, top, right, bottom };
}

function drawCropSelection(context, image, width, height) {
  const crop = normalisedCrop();
  if (!crop) return;
  const x = crop.left * width;
  const y = crop.top * height;
  const cropWidth = (crop.right - crop.left) * width;
  const cropHeight = (crop.bottom - crop.top) * height;
  context.save();
  context.fillStyle = "rgba(9, 22, 17, .58)";
  context.fillRect(0, 0, width, height);
  context.drawImage(
    image,
    crop.left * image.naturalWidth,
    crop.top * image.naturalHeight,
    (crop.right - crop.left) * image.naturalWidth,
    (crop.bottom - crop.top) * image.naturalHeight,
    x, y, cropWidth, cropHeight,
  );
  context.strokeStyle = "#d8e7a5";
  context.lineWidth = Math.max(2, Math.min(width, height) / 180);
  context.setLineDash([10, 6]);
  context.strokeRect(x, y, cropWidth, cropHeight);
  context.restore();
}

function drawSubjectEditor() {
  const canvas = $("#subject-canvas");
  const context = canvas.getContext("2d");
  const image = state.queryImage;
  if (!image) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (state.subjectPreview && state.queryMask?.displayAlpha) {
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = "destination-in";
    context.drawImage(state.queryMask.displayAlpha, 0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = "destination-over";
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = "source-over";
    drawAutoPoints(context, canvas.width, canvas.height);
    return;
  }

  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  if (state.queryMethod === "crop") drawCropSelection(context, image, canvas.width, canvas.height);
  if (state.queryMethod === "mask" && state.queryMask?.displayOverlay) {
    context.drawImage(state.queryMask.displayOverlay, 0, 0);
  }
  if (state.queryMethod === "mask" && state.subjectSelectionMode === "auto") {
    drawAutoPoints(context, canvas.width, canvas.height);
  }
}

function updateSubjectStatus() {
  const editor = $("#query-subject-editor");
  const method = state.queryMethod;
  const autoMode = method === "mask" && state.subjectSelectionMode === "auto";
  const hasMask = Boolean(state.queryMask);
  const hasCrop = Boolean(normalisedCrop());
  editor.classList.toggle("has-selection", hasMask);
  editor.classList.toggle("auto-selecting", autoMode);
  editor.classList.toggle("crop-selecting", method === "crop");
  editor.classList.toggle("busy", state.segmentationBusy);
  $$('[data-query-method]').forEach(button => button.classList.toggle("active", button.dataset.queryMethod === method));
  $("#auto-point-tools").hidden = method !== "mask" || !autoMode;
  $("#subject-tools").hidden = method !== "mask";
  $("#undo-subject").disabled = state.segmentationBusy || state.segmentationPoints.length === 0;
  $("#reset-subject").disabled = state.segmentationBusy || state.segmentationPoints.length === 0;
  $("#preview-subject").disabled = state.segmentationBusy || !hasMask;
  $("#preview-subject").textContent = state.subjectPreview ? "返回选区" : "预览主体";
  $("#confirm-subject-search").disabled = state.segmentationBusy || (method === "crop" && !hasCrop) || (method === "mask" && !hasMask);
  $("#confirm-subject-search").textContent = method === "original" ? "使用原图检索" : method === "crop" ? "使用截图检索" : "使用遮罩检索";
  if (method === "original") {
    $("#subject-status-title").textContent = "原图检索已就绪";
    $("#subject-status-copy").textContent = "将直接使用上传的完整图片生成查询特征，不需要选择主体。";
    return;
  }
  if (method === "crop") {
    $("#subject-status-title").textContent = hasCrop ? "截图区域已选定" : "拖动框选截图区域";
    $("#subject-status-copy").textContent = hasCrop
      ? "可重新拖动调整范围，检索时只使用亮色框内的图片。"
      : "按住鼠标左键在图片上拖动，框住需要用于检索的区域。";
    return;
  }
  if (autoMode || state.segmentationBusy) {
    const count = state.segmentationPoints.length;
    $("#subject-status-title").textContent = state.segmentationBusy
      ? (state.segmentationSessionId ? "正在更新物体边界" : "正在加载轻量分割模型")
      : hasMask ? "已自动贴合主体" : "请在主体内部点一下";
    $("#subject-status-copy").textContent = state.segmentationBusy
      ? (state.segmentationSessionId ? "正在根据提示点计算新 Mask。" : "首次使用会下载约 38 MB 模型，并在 CPU 上分析图片。")
      : hasMask
        ? `已使用 ${count} 个提示点生成 ${state.queryMask.width}×${state.queryMask.height} 二值 Mask。绿色点保留主体，红色点排除背景。`
        : "左键添加主体点；切换“排除背景”或单击右键，可修正误选区域。";
    return;
  }
  $("#subject-status-title").textContent = "遮罩模型尚未启动";
  $("#subject-status-copy").textContent = "再次点击“遮罩检索”启动模型，然后在物体内部点一下。";
}

async function setupSubjectEditor(file) {
  const editor = $("#query-subject-editor");
  disposeSegmentationSession();
  state.queryMethod = "original";
  state.queryCrop = null;
  state.cropPointerId = null;
  state.queryMask = null;
  state.subjectSelectionMode = null;
  state.subjectPreview = false;
  state.segmentationPoints = [];
  state.segmentationPointLabel = 1;
  state.segmentationBusy = false;
  state.queryImage = null;
  if (!file || kindOf(file.name) !== "image") {
    editor.hidden = true;
    return;
  }
  const image = new Image();
  const url = URL.createObjectURL(file);
  state.objectUrls.add(url);
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error("无法读取查询图片"));
    image.src = url;
  });
  if (state.queryFile !== file) return;
  state.queryImage = image;
  const scale = Math.min(1, 1000 / image.naturalWidth, 620 / image.naturalHeight);
  const canvas = $("#subject-canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  editor.hidden = false;
  updateSubjectStatus();
  drawSubjectEditor();
}

function resetSubjectSelection() {
  if (!state.segmentationPoints.length) return;
  state.segmentationPoints = [];
  invalidateSubjectMask();
  updateSubjectStatus();
  drawSubjectEditor();
}

async function setQueryMethod(method) {
  if (!state.queryImage || !["original", "crop", "mask"].includes(method)) return;
  state.queryMethod = method;
  state.subjectPreview = false;
  if (method === "mask") {
    await startAutoSelection();
    return;
  }
  state.subjectSelectionMode = method === "crop" ? "crop" : null;
  updateSubjectStatus();
  drawSubjectEditor();
  setMessage("#search-message", method === "original" ? "已选择原图检索。" : "请在图片上拖动框选截图区域。", "success");
}

async function undoSubjectSelection() {
  if (!state.segmentationPoints.length || state.segmentationBusy) return;
  state.segmentationPoints.pop();
  invalidateSubjectMask();
  if (state.segmentationPoints.length) await requestAutoMask(false);
  else {
    updateSubjectStatus();
    drawSubjectEditor();
  }
}

function canvasBlob(canvas, type = "image/png", quality) {
  return new Promise(resolve => canvas.toBlob(resolve, type, quality));
}

async function segmentationInputFile() {
  const image = state.queryImage;
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, 1024 / Math.max(image.naturalWidth, image.naturalHeight));
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d");
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await canvasBlob(canvas, "image/jpeg", .92);
  if (!blob) throw new Error("智能分割图片准备失败");
  return new File([blob], "segmentation-query.jpg", { type: "image/jpeg" });
}

async function setQueryMaskFromBlob(blob) {
  const image = state.queryImage;
  const editorCanvas = $("#subject-canvas");
  const bitmap = await createImageBitmap(blob);
  const full = document.createElement("canvas");
  full.width = image.naturalWidth;
  full.height = image.naturalHeight;
  const fullContext = full.getContext("2d", { willReadFrequently: true });
  fullContext.imageSmoothingEnabled = false;
  fullContext.drawImage(bitmap, 0, 0, full.width, full.height);
  bitmap.close();
  const fullPixels = fullContext.getImageData(0, 0, full.width, full.height);
  for (let offset = 0; offset < fullPixels.data.length; offset += 4) {
    const value = fullPixels.data[offset] >= 128 ? 255 : 0;
    fullPixels.data[offset] = value;
    fullPixels.data[offset + 1] = value;
    fullPixels.data[offset + 2] = value;
    fullPixels.data[offset + 3] = 255;
  }
  fullContext.putImageData(fullPixels, 0, 0);
  const binaryBlob = await canvasBlob(full);
  if (!binaryBlob) throw new Error("二值 Mask 生成失败");

  const displayAlpha = document.createElement("canvas");
  displayAlpha.width = editorCanvas.width;
  displayAlpha.height = editorCanvas.height;
  const alphaContext = displayAlpha.getContext("2d", { willReadFrequently: true });
  alphaContext.imageSmoothingEnabled = false;
  alphaContext.drawImage(full, 0, 0, displayAlpha.width, displayAlpha.height);
  const alphaPixels = alphaContext.getImageData(0, 0, displayAlpha.width, displayAlpha.height);
  const displayOverlay = document.createElement("canvas");
  displayOverlay.width = displayAlpha.width;
  displayOverlay.height = displayAlpha.height;
  const overlayContext = displayOverlay.getContext("2d");
  const overlayPixels = overlayContext.createImageData(displayOverlay.width, displayOverlay.height);
  for (let offset = 0; offset < alphaPixels.data.length; offset += 4) {
    const selected = alphaPixels.data[offset] >= 128;
    alphaPixels.data[offset] = 255;
    alphaPixels.data[offset + 1] = 255;
    alphaPixels.data[offset + 2] = 255;
    alphaPixels.data[offset + 3] = selected ? 255 : 0;
    overlayPixels.data[offset] = 216;
    overlayPixels.data[offset + 1] = 231;
    overlayPixels.data[offset + 2] = 165;
    overlayPixels.data[offset + 3] = selected ? 112 : 0;
  }
  alphaContext.putImageData(alphaPixels, 0, 0);
  overlayContext.putImageData(overlayPixels, 0, 0);
  state.queryMask = {
    blob: binaryBlob,
    width: full.width,
    height: full.height,
    canvas: full,
    displayAlpha,
    displayOverlay,
  };
}

async function startAutoSelection() {
  if (!state.queryImage || state.segmentationBusy) return;
  if (state.segmentationSessionId) {
    state.subjectSelectionMode = "auto";
    updateSubjectStatus();
    drawSubjectEditor();
    return;
  }
  state.subjectSelectionMode = "auto";
  state.subjectPreview = false;
  state.segmentationPoints = [];
  invalidateSubjectMask();
  state.segmentationBusy = true;
  const image = state.queryImage;
  const generation = state.segmentationGeneration;
  updateSubjectStatus();
  drawSubjectEditor();
  try {
    const data = new FormData();
    data.append("image", await segmentationInputFile());
    const response = await api("/api/material-search/segmentations", { method: "POST", body: data });
    const payload = await response.json();
    if (generation !== state.segmentationGeneration || image !== state.queryImage) {
      api(`/api/material-search/segmentations/${payload.session_id}`, { method: "DELETE" }).catch(() => {});
      return;
    }
    state.segmentationSessionId = payload.session_id;
    state.segmentationMeta = payload;
    setMessage("#search-message", "图片分析完成。请在要查找的物体内部点一下。", "success");
  } catch (error) {
    if (generation !== state.segmentationGeneration) return;
    state.subjectSelectionMode = null;
    setMessage("#search-message", error.message, "error");
  } finally {
    if (generation !== state.segmentationGeneration) return;
    state.segmentationBusy = false;
    updateSubjectStatus();
    drawSubjectEditor();
  }
}

async function segmentationMaskResponse() {
  if (state.segmentationSessionId) {
    try {
      return await api(`/api/material-search/segmentations/${state.segmentationSessionId}/mask`, {
        method: "POST",
        json: true,
        body: JSON.stringify({ points: state.segmentationPoints }),
      });
    } catch (error) {
      if (error.status !== 410) throw error;
      state.segmentationSessionId = null;
      state.segmentationMeta = null;
      setMessage("#search-message", "服务器已切换实例，正在自动恢复遮罩分析…");
    }
  }
  const data = new FormData();
  data.append("image", await segmentationInputFile());
  data.append("points", JSON.stringify(state.segmentationPoints));
  const response = await api("/api/material-search/segmentations/mask", { method: "POST", body: data });
  const replacementSessionId = response.headers.get("X-Segmentation-Session");
  if (replacementSessionId) state.segmentationSessionId = replacementSessionId;
  return response;
}

async function requestAutoMask(rollbackPointOnError = true) {
  if (!state.segmentationPoints.length || state.segmentationBusy) return;
  state.segmentationBusy = true;
  state.subjectPreview = false;
  updateSubjectStatus();
  drawSubjectEditor();
  try {
    const response = await segmentationMaskResponse();
    await setQueryMaskFromBlob(await response.blob());
    const scoreHeader = response.headers.get("X-Segmentation-Score");
    const score = scoreHeader === null ? Number.NaN : Number(scoreHeader);
    const scoreCopy = Number.isFinite(score) ? `，模型置信度 ${Math.round(score * 100)}%` : "";
    setMessage("#search-message", `已自动贴合主体边界${scoreCopy}。可继续添加主体点或背景点修正。`, "success");
  } catch (error) {
    invalidateSubjectMask();
    if (rollbackPointOnError) state.segmentationPoints.pop();
    setMessage("#search-message", error.message, "error");
  } finally {
    state.segmentationBusy = false;
    updateSubjectStatus();
    drawSubjectEditor();
  }
}

async function subjectAlphaMaskCanvas(mask) {
  const canvas = document.createElement("canvas");
  canvas.width = mask.width;
  canvas.height = mask.height;
  if (mask.canvas) {
    canvas.getContext("2d").drawImage(mask.canvas, 0, 0);
  } else {
    const bitmap = await createImageBitmap(mask.blob);
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    bitmap.close();
  }
  const context = canvas.getContext("2d", { willReadFrequently: true });
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
  for (let offset = 0; offset < pixels.data.length; offset += 4) {
    pixels.data[offset + 3] = pixels.data[offset];
    pixels.data[offset] = 255;
    pixels.data[offset + 1] = 255;
    pixels.data[offset + 2] = 255;
  }
  context.putImageData(pixels, 0, 0);
  return canvas;
}

async function preparedQueryFile() {
  const file = state.queryFile;
  const image = state.queryImage;
  if (!file || !image || kindOf(file.name) !== "image") return file;
  if (state.queryMethod === "original") return file;
  const baseName = file.name.replace(/\.[^.]+$/, "") || "query";
  if (state.queryMethod === "crop") {
    const crop = normalisedCrop();
    if (!crop) throw new Error("请先在图片上框选截图区域");
    const sourceX = Math.round(crop.left * image.naturalWidth);
    const sourceY = Math.round(crop.top * image.naturalHeight);
    const sourceWidth = Math.max(1, Math.round((crop.right - crop.left) * image.naturalWidth));
    const sourceHeight = Math.max(1, Math.round((crop.bottom - crop.top) * image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = sourceWidth;
    canvas.height = sourceHeight;
    canvas.getContext("2d").drawImage(
      image,
      sourceX, sourceY, sourceWidth, sourceHeight,
      0, 0, sourceWidth, sourceHeight,
    );
    const blob = await canvasBlob(canvas);
    if (!blob) throw new Error("截图查询图生成失败，请重新框选");
    return new File([blob], `${baseName}-crop.png`, { type: "image/png", lastModified: Date.now() });
  }
  if (!state.queryMask) throw new Error("请先在遮罩检索中点选图片主体");
  const mask = state.queryMask;
  const alphaMask = await subjectAlphaMaskCanvas(mask);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  context.globalCompositeOperation = "destination-in";
  context.drawImage(alphaMask, 0, 0);
  context.globalCompositeOperation = "source-over";
  const blob = await canvasBlob(canvas);
  if (!blob) throw new Error("主体查询图生成失败，请重新选择主体");
  return new File([blob], `${baseName}-subject.png`, { type: "image/png", lastModified: Date.now() });
}

async function uploadFileInChunks(file, chunkPath, completePath, completePayload, onChunk) {
  const chunkCount = Math.ceil(file.size / uploadChunkBytes);
  if (!chunkCount) throw new Error(`${file.name} 是空文件`);
  for (let chunkIndex = 0; chunkIndex < chunkCount; chunkIndex += 1) {
    const start = chunkIndex * uploadChunkBytes;
    const chunk = file.slice(start, Math.min(start + uploadChunkBytes, file.size));
    const data = new FormData();
    data.append("chunk", chunk, `${file.name}.part`);
    try {
      await api(`${chunkPath}/${chunkIndex}`, {
        method: "POST",
        body: data,
        headers: {
          "X-Chunk-Size": String(chunk.size),
          "X-Chunk-SHA256": await sha256Hex(chunk),
        },
      });
    } catch (error) {
      throw new Error(`${file.name}：第 ${chunkIndex + 1}/${chunkCount} 个分片上传失败：${error.message}`, { cause: error });
    }
    onChunk?.();
  }
  try {
    return await api(completePath, {
      method: "POST",
      json: true,
      body: JSON.stringify({ ...completePayload, filename: file.name, chunk_count: chunkCount, file_size: file.size }),
    });
  } catch (error) {
    throw new Error(`${file.name}：文件校验或索引建立失败：${error.message}`, { cause: error });
  }
}

async function uploadFolder() {
  const button = $("#upload-button");
  const name = $("#folder-name").value.trim();
  if (!name) {
    renderSelection();
    $("#folder-name").focus();
    return;
  }
  button.disabled = true;
  $("#upload-progress").hidden = false;
  try {
    const response = await api("/api/material-search/folders", { method: "POST", json: true, body: JSON.stringify({ name }) });
    const folder = await response.json();
    const totalChunks = state.selectedFiles.reduce((sum, file) => sum + Math.ceil(file.size / uploadChunkBytes), 0);
    let uploadedChunks = 0;
    const failures = [];
    for (let index = 0; index < state.selectedFiles.length; index += 1) {
      const file = state.selectedFiles[index];
      const fileChunkCount = Math.ceil(file.size / uploadChunkBytes);
      const chunksBeforeFile = uploadedChunks;
      $("#progress-label").textContent = `处理 ${file.name}`;
      const currentUploadId = uploadId();
      try {
        await uploadFileInChunks(
          file,
          `/api/material-search/folders/${folder.id}/uploads/${currentUploadId}/chunks`,
          `/api/material-search/folders/${folder.id}/uploads/${currentUploadId}/complete`,
          { relative_path: file.webkitRelativePath || file.name },
          () => {
            uploadedChunks += 1;
            const percent = Math.round((uploadedChunks / Math.max(totalChunks, 1)) * 90);
            $("#progress-value").textContent = `${percent}%`;
            $("#progress-bar").style.width = `${percent}%`;
          },
        );
      } catch (error) {
        failures.push(error);
        uploadedChunks = chunksBeforeFile + Math.max(fileChunkCount, 1);
        const percent = Math.round((uploadedChunks / Math.max(totalChunks, 1)) * 90);
        $("#progress-value").textContent = `${percent}%`;
        $("#progress-bar").style.width = `${percent}%`;
      }
    }
    if (failures.length) {
      $("#progress-label").textContent = `检查完成，${failures.length} 个文件失败`;
      try {
        await api(`/api/material-search/folders/${folder.id}`, { method: "DELETE" });
        await api(`/api/material-search/trash/${folder.id}`, { method: "DELETE" });
      } catch {}
      const displayed = failures.slice(0, 6).map(error => error.message).join("；");
      const remaining = failures.length > 6 ? `；另有 ${failures.length - 6} 个失败文件` : "";
      setMessage("#upload-message", `本次上传未保存。${displayed}${remaining}`, "error");
      button.disabled = false;
      await loadLibrary();
      return;
    }
    $("#progress-label").textContent = "生成文件夹下载包";
    $("#progress-bar").style.width = "94%";
    await api(`/api/material-search/folders/${folder.id}/finalize`, { method: "POST" });
    $("#progress-value").textContent = "100%";
    $("#progress-bar").style.width = "100%";
    setMessage("#upload-message", "上传完成，素材文件已经可以检索。", "success");
    state.selectedFiles = [];
    $("#folder-input").value = "";
    $("#folder-name").value = "";
    renderSelection();
    await loadLibrary();
  } catch (error) {
    setMessage("#upload-message", error.message, "error");
    button.disabled = false;
  }
}

async function loadLibrary() {
  const loadSequence = ++libraryLoadSequence;
  const list = $("#library-list");
  list.innerHTML = '<p class="empty-copy">正在读取素材库…</p>';
  const cloudList = $("#cloud-list");
  cloudList.innerHTML = '<p class="empty-copy">正在读取云端素材…</p>';
  const trashList = $("#trash-list");
  trashList.innerHTML = '<p class="empty-copy">正在读取回收站…</p>';
  try {
    const [folderResponse, trashResponse] = await Promise.all([
      api("/api/material-search/folders"),
      api("/api/material-search/trash"),
    ]);
    const folders = (await folderResponse.json()).folders;
    const trash = (await trashResponse.json()).folders;
    if (loadSequence !== libraryLoadSequence) return;
    state.folders = folders;
    state.trash = trash;
    $("#active-count").textContent = state.folders.length;
    $("#trash-count").textContent = state.trash.length;
    if (!state.folders.length) {
      list.innerHTML = '<p class="empty-copy">还没有素材。请先上传一个包含图片和Illustrator AI文件的文件夹，DXF可选。</p>';
      cloudList.innerHTML = '<p class="empty-copy">云端还没有素材文件夹。</p>';
    } else {
      list.innerHTML = state.folders.map(folder => `<article class="library-item"><div class="thumb-stack">${Object.values(folder.previews).slice(0, 3).map(url => `<img data-protected-src="${url}" alt="">`).join("") || '<span class="thumb-placeholder">等待</span>'}</div><div class="library-info"><strong>${safeText(folder.name)}</strong><span>${folder.assets.length} 个文件</span><span class="status ${folder.status === "FAILED" ? "failed" : ""}">${folder.status === "READY" ? "可检索" : folder.status === "FAILED" ? "处理失败" : "处理中"}</span></div><button type="button" data-folder="${folder.id}">查看</button></article>`).join("");
      list.querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
      list.querySelectorAll("button[data-folder]").forEach(button => button.addEventListener("click", () => openFolder(button.dataset.folder)));
    }
    renderCloudFolders();
    renderTrashFolders();
    switchCloudView($("[data-cloud-view].active")?.dataset.cloudView || "active");
  } catch (error) {
    if (loadSequence !== libraryLoadSequence) return;
    list.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
    cloudList.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
    trashList.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
  }
}

async function rebuildAllImageIndexes() {
  if (state.reindexBusy) return;
  const button = $("#rebuild-indexes");
  let plan;
  try {
    const response = await api("/api/material-search/reindex/plan");
    plan = await response.json();
  } catch (error) {
    setMessage("#reindex-message", error.message, "error");
    return;
  }
  if (!plan.total) {
    setMessage("#reindex-message", "素材库中没有需要建立检索特征的图片。", "success");
    return;
  }
  if (!window.confirm(`将使用当前 Shape IoU 算法重新计算 ${plan.total} 张图片的保洞 Mask，并逐张覆盖旧索引。\n\n原图和预览不会被修改，是否继续？`)) return;

  state.reindexBusy = true;
  button.disabled = true;
  $("#refresh-library").disabled = true;
  $("#reindex-progress").hidden = false;
  $("#reindex-progress-bar").style.width = "0%";
  $("#reindex-progress-value").textContent = "0%";
  setMessage("#reindex-message", `正在重建 0 / ${plan.total} 张图片…`);
  const failures = [];
  let completed = 0;
  try {
    for (const item of plan.items) {
      $("#reindex-progress-label").textContent = `正在处理：${item.file_name}`;
      try {
        await api(`/api/material-search/reindex/assets/${encodeURIComponent(item.asset_id)}`, { method: "POST" });
      } catch (error) {
        failures.push(`${item.folder_name} / ${item.file_name}：${error.message}`);
      }
      completed += 1;
      const percent = Math.round(completed / plan.total * 100);
      $("#reindex-progress-bar").style.width = `${percent}%`;
      $("#reindex-progress-value").textContent = `${percent}%`;
      setMessage("#reindex-message", `已处理 ${completed} / ${plan.total} 张，成功 ${completed - failures.length} 张，失败 ${failures.length} 张。`, failures.length ? "error" : "");
    }
    $("#reindex-progress-label").textContent = failures.length ? "重建完成，部分文件失败" : "全部特征重建完成";
    if (failures.length) {
      const preview = failures.slice(0, 3).join("；");
      const remaining = failures.length > 3 ? `；另有 ${failures.length - 3} 个失败文件` : "";
      setMessage("#reindex-message", `成功覆盖 ${plan.total - failures.length} 张图片的旧特征，失败 ${failures.length} 张：${preview}${remaining}`, "error");
    } else {
      setMessage("#reindex-message", `已使用 ${plan.feature_version} 重建并保存 ${plan.total} 张图片的 Shape IoU Mask。`, "success");
    }
    await loadLibrary();
  } finally {
    state.reindexBusy = false;
    button.disabled = false;
    $("#refresh-library").disabled = false;
  }
}

function renderCloudFolders() {
  const list = $("#cloud-list");
  const totalFiles = state.folders.reduce((sum, folder) => sum + folder.assets.length, 0);
  const totalBytes = state.folders.reduce((sum, folder) => sum + folder.assets.reduce((folderSum, asset) => folderSum + asset.file_size, 0), 0);
  $("#cloud-summary").textContent = `共 ${state.folders.length} 个文件夹 · ${totalFiles} 个文件 · ${bytes(totalBytes)}`;
  if (!state.folders.length) {
    list.innerHTML = '<p class="empty-copy">云端还没有素材文件夹。请先在“素材库上传”中上传文件夹。</p>';
    return;
  }
  list.innerHTML = state.folders.map(folder => {
    const size = folder.assets.reduce((sum, asset) => sum + asset.file_size, 0);
    const date = new Date(folder.created_at * 1000).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    const previews = Object.values(folder.previews).slice(0, 3).map(url => `<img data-protected-src="${url}" alt="">`).join("") || '<span class="thumb-placeholder">等待</span>';
    const status = folder.status === "READY" ? "可检索" : folder.status === "FAILED" ? "处理失败" : "处理中";
    return `<article class="cloud-item"><div class="thumb-stack">${previews}</div><div class="cloud-info"><strong>${safeText(folder.name)}</strong><span>${folder.assets.length} 个文件 · ${bytes(size)} · ${date}</span><span class="status ${folder.status === "FAILED" ? "failed" : ""}">${status}</span></div><div class="cloud-actions"><button type="button" data-folder="${folder.id}">查看</button><button type="button" data-zip="${folder.zip_url || ""}" data-name="${safeText(folder.name)}.zip" ${folder.zip_url ? "" : "disabled"}>下载</button><button class="danger" type="button" data-delete-folder="${folder.id}" data-folder-name="${safeText(folder.name)}">删除</button></div></article>`;
  }).join("");
  list.querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
  list.querySelectorAll("button[data-folder]").forEach(button => button.addEventListener("click", () => openFolder(button.dataset.folder)));
  list.querySelectorAll("button[data-zip]").forEach(button => button.addEventListener("click", () => downloadProtected(button.dataset.zip, button.dataset.name)));
  list.querySelectorAll("button[data-delete-folder]").forEach(button => button.addEventListener("click", () => deleteCloudFolder(button)));
}

async function deleteCloudFolder(button) {
  const folderId = button.dataset.deleteFolder;
  const name = button.dataset.folderName;
  if (!window.confirm(`确定将“${name}”移入回收站吗？\n\n回收站保留 30 天，期间可以恢复。`)) return;
  button.disabled = true;
  setMessage("#cloud-message", `正在删除“${name}”…`);
  try {
    await api(`/api/material-search/folders/${folderId}`, { method: "DELETE" });
    if (state.activeFolder?.id === folderId) $("#folder-dialog").close();
    clearObjectUrls();
    setMessage("#cloud-message", `“${name}”已移入回收站，将保留 30 天。`, "success");
    await loadLibrary();
  } catch (error) {
    setMessage("#cloud-message", error.message, "error");
    button.disabled = false;
  }
}

function renderTrashFolders() {
  const list = $("#trash-list");
  if (!state.trash.length) {
    list.innerHTML = '<p class="empty-copy">回收站为空。</p>';
    return;
  }
  list.innerHTML = state.trash.map(folder => {
    const size = folder.assets.reduce((sum, asset) => sum + asset.file_size, 0);
    const remainingMs = Math.max(0, folder.expires_at * 1000 - Date.now());
    const remainingDays = Math.max(1, Math.ceil(remainingMs / 86400000));
    return `<article class="cloud-item trash-item"><div class="trash-icon">♻</div><div class="cloud-info"><strong>${safeText(folder.name)}</strong><span>${folder.assets.length} 个文件 · ${bytes(size)}</span><span class="trash-expiry">${remainingDays} 天后自动永久删除</span></div><div class="cloud-actions"><button type="button" data-restore-folder="${folder.id}" data-folder-name="${safeText(folder.name)}">恢复</button><button class="danger" type="button" data-purge-folder="${folder.id}" data-folder-name="${safeText(folder.name)}">永久删除</button></div></article>`;
  }).join("");
  list.querySelectorAll("button[data-restore-folder]").forEach(button => button.addEventListener("click", () => restoreCloudFolder(button)));
  list.querySelectorAll("button[data-purge-folder]").forEach(button => button.addEventListener("click", () => permanentlyDeleteCloudFolder(button)));
}

async function restoreCloudFolder(button) {
  const folderId = button.dataset.restoreFolder;
  const name = button.dataset.folderName;
  button.disabled = true;
  try {
    await api(`/api/material-search/trash/${folderId}/restore`, { method: "POST" });
    setMessage("#cloud-message", `“${name}”已恢复，可以重新检索。`, "success");
    await loadLibrary();
  } catch (error) {
    setMessage("#cloud-message", error.message, "error");
    button.disabled = false;
  }
}

async function permanentlyDeleteCloudFolder(button) {
  const folderId = button.dataset.purgeFolder;
  const name = button.dataset.folderName;
  if (!window.confirm(`确定永久删除“${name}”吗？\n\n云端原文件、预览、ZIP 和检索索引都会立即删除，无法恢复。`)) return;
  button.disabled = true;
  try {
    await api(`/api/material-search/trash/${folderId}`, { method: "DELETE" });
    clearObjectUrls();
    setMessage("#cloud-message", `“${name}”已永久删除。`, "success");
    await loadLibrary();
  } catch (error) {
    setMessage("#cloud-message", error.message, "error");
    button.disabled = false;
  }
}

function switchCloudView(view) {
  $$('[data-cloud-view]').forEach(button => button.classList.toggle("active", button.dataset.cloudView === view));
  $("#cloud-list").hidden = view !== "active";
  $("#trash-list").hidden = view !== "trash";
  if (view === "active") {
    const totalFiles = state.folders.reduce((sum, folder) => sum + folder.assets.length, 0);
    const totalBytes = state.folders.reduce((sum, folder) => sum + folder.assets.reduce((folderSum, asset) => folderSum + asset.file_size, 0), 0);
    $("#cloud-summary").textContent = `共 ${state.folders.length} 个文件夹 · ${totalFiles} 个文件 · ${bytes(totalBytes)}`;
  } else {
    $("#cloud-summary").textContent = `回收站共 ${state.trash.length} 个文件夹 · 最长保留 30 天`;
  }
}

async function downloadProtected(path, filename) {
  const response = await api(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openFolder(folderId, matchedAssetId = null) {
  const response = await api(`/api/material-search/folders/${folderId}`);
  const folder = await response.json();
  state.activeFolder = folder;
  $("#dialog-title").textContent = folder.name;
  $("#dialog-content").innerHTML = folder.assets.map(asset => `<article class="asset-card${asset.id === matchedAssetId ? " matched" : ""}" data-asset-id="${asset.id}"><img data-protected-src="${asset.preview_url}" alt="${safeText(asset.original_name)}"><div class="asset-meta"><strong>${safeText(asset.original_name)}</strong><span>${labels[asset.kind]} · ${bytes(asset.file_size)}</span><button type="button" data-download="${asset.blob_url}" data-name="${safeText(asset.original_name)}">下载原文件</button></div></article>`).join("");
  $("#dialog-content").querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
  $("#dialog-content").querySelectorAll("button[data-download]").forEach(button => button.addEventListener("click", () => downloadProtected(button.dataset.download, button.dataset.name)));
  $("#dialog-download").disabled = !folder.zip_url;
  $("#folder-dialog").showModal();
  if (matchedAssetId) {
    requestAnimationFrame(() => {
      const matchedCard = $("#dialog-content").querySelector(`[data-asset-id="${CSS.escape(matchedAssetId)}"]`);
      matchedCard?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    });
  }
}

function resultCard(item, options = {}) {
  const stage = options.stage || "feature";
  const isBest = stage === "best";
  const scoreLabel = stage === "shape" || isBest ? "Shape IoU" : "特征余弦相似度";
  return `<article class="result-card${isBest ? " geometric-best" : ""}">${isBest ? '<span class="best-marker">Shape IoU 第 1 名</span>' : ""}<div class="result-image"><span class="rank">${item.rank}</span><img data-protected-src="${item.matched_preview_url}" alt=""><span class="score">${scoreLabel} · ${item.score}%</span></div><div class="result-body"><h3>${safeText(item.folder_name)}</h3><p>匹配文件：${safeText(item.matched_file)}</p><div class="result-actions"><button type="button" data-folder="${item.folder_id}" data-asset="${item.matched_asset_id}">定位查看</button><button type="button" data-zip="${item.zip_url || ""}" data-name="${safeText(item.folder_name)}.zip" ${item.zip_url ? "" : "disabled"}>下载</button></div></div></article>`;
}

function activateResultCards(root) {
  root.querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
  root.querySelectorAll("button[data-folder]").forEach(button => button.addEventListener("click", () => openFolder(button.dataset.folder, button.dataset.asset)));
  root.querySelectorAll("button[data-zip]").forEach(button => button.addEventListener("click", () => downloadProtected(button.dataset.zip, button.dataset.name)));
}

async function search() {
  if (!state.queryFile) return;
  const button = $("#search-button");
  const confirmButton = $("#confirm-subject-search");
  button.disabled = true;
  confirmButton.disabled = true;
  const methodCopy = state.queryMethod === "mask"
    ? "1/3 正在应用二值 Mask并准备查询图…"
    : state.queryMethod === "crop"
      ? "1/3 正在裁剪截图区域并准备查询图…"
      : "1/3 正在准备原图…";
  setMessage("#search-message", methodCopy);
  pausePreviewLoading();
  try {
    const queryFile = await preparedQueryFile();
    setMessage("#search-message", "2/3 正在上传查询图并匹配本地向量库…");
    const currentUploadId = uploadId();
    const response = await uploadFileInChunks(
      queryFile,
      `/api/material-search/search/uploads/${currentUploadId}/chunks`,
      `/api/material-search/search/uploads/${currentUploadId}/complete`,
      { relative_path: queryFile.name },
    );
    setMessage("#search-message", "3/3 匹配完成，正在整理结果…");
    const result = await response.json();
    $("#search-output").hidden = false;
    const imagePipeline = result.algorithm === "subject-mask-shape-iou-holes";
    $("#results-title").textContent = `${labels[result.kind]}${imagePipeline ? " Shape IoU" : ""}检索结果`;
    $("#first-search-copy").textContent = imagePipeline
      ? `只按保留内部镂空的主体 Mask 计算 Shape IoU，展示匹配度最高的前 ${result.results.length} 张图片。`
      : `使用特征余弦相似度取前 ${result.results.length} 个文件。`;
    $("#query-preview").innerHTML = `<img src="${result.query_preview_data_url}" alt="查询文件预览">`;
    const list = $("#results-list");
    if (!result.results.length) {
      list.innerHTML = '<p class="empty-copy">素材库中还没有可检索的同类型文件。</p>';
    } else {
      list.innerHTML = result.results.map(item => resultCard(item, {
        stage: imagePipeline ? "shape" : "feature",
      })).join("");
    }
    const secondSearch = $("#second-search");
    secondSearch.hidden = !result.best_match;
    $("#geometric-best-list").innerHTML = result.best_match
      ? resultCard(result.best_match, { stage: "best" })
      : "";
    activateResultCards($("#search-output"));
    if (result.outdated_images) {
      setMessage("#search-message", `已跳过 ${result.outdated_images} 张旧特征图片。请在“素材库上传”页点击“一键重建全部特征”。`, "error");
    } else {
      const upgraded = result.reindexed_images ? `已自动升级 ${result.reindexed_images} 张旧图片索引。` : "";
      setMessage("#search-message", imagePipeline ? `${upgraded}检索完成。下方为 Shape IoU 前20名，上方单独展示最高匹配结果。` : "检索完成。相似度用于排序，不代表识别准确率。", "success");
    }
  } catch (error) {
    setMessage("#search-message", error.message, "error");
  } finally {
    resumePreviewLoading();
    button.disabled = false;
    updateSubjectStatus();
  }
}

$$('.tab').forEach(tab => tab.addEventListener("click", () => {
  $$('.tab').forEach(item => item.classList.toggle("active", item === tab));
  $$('.panel').forEach(panel => panel.classList.toggle("active", panel.id === `${tab.dataset.tab}-panel`));
  if (tab.dataset.tab === "cloud") loadLibrary();
}));

$("#folder-input").addEventListener("change", event => {
  state.selectedFiles = [...event.target.files].filter(file => allowed.test(file.name));
  renderSelection();
});
$("#folder-name").addEventListener("input", renderSelection);
$("#upload-button").addEventListener("click", uploadFolder);
$("#rebuild-indexes").addEventListener("click", rebuildAllImageIndexes);
$("#refresh-library").addEventListener("click", loadLibrary);
$("#refresh-cloud").addEventListener("click", loadLibrary);
$$('[data-cloud-view]').forEach(button => button.addEventListener("click", () => switchCloudView(button.dataset.cloudView)));
$("#query-input").addEventListener("change", async event => {
  const file = event.target.files[0];
  disposeSegmentationSession();
  state.queryFile = file || null;
  state.queryImage = null;
  state.queryMethod = "original";
  state.queryCrop = null;
  state.cropPointerId = null;
  state.queryMask = null;
  state.subjectSelectionMode = null;
  state.subjectPreview = false;
  state.segmentationPoints = [];
  state.segmentationPointLabel = 1;
  state.segmentationBusy = false;
  $("#query-label").textContent = file ? `${file.name} · ${bytes(file.size)}` : "选择图片、DXF或AI文件";
  $("#search-button").disabled = !file || !allowed.test(file.name);
  if (!file) {
    $("#query-subject-editor").hidden = true;
    setMessage("#search-message", "");
    return;
  }
  try {
    await setupSubjectEditor(file);
    if (kindOf(file.name) !== "image") setMessage("#search-message", "查询文件已就绪。", "success");
  } catch (error) {
    if (state.queryFile !== file) return;
    $("#search-button").disabled = true;
    $("#query-subject-editor").hidden = true;
    setMessage("#search-message", error.message, "error");
  }
});
$$('[data-query-method]').forEach(button => button.addEventListener("click", () => setQueryMethod(button.dataset.queryMethod)));
$$('[data-point-label]').forEach(button => button.addEventListener("click", () => {
  state.segmentationPointLabel = Number(button.dataset.pointLabel);
  $$('[data-point-label]').forEach(item => item.classList.toggle("active", item === button));
}));
$("#subject-canvas").addEventListener("contextmenu", event => {
  if (state.queryMethod === "mask" && state.subjectSelectionMode === "auto") event.preventDefault();
});
$("#subject-canvas").addEventListener("pointerdown", event => {
  if (!state.queryImage) return;
  if (state.queryMethod === "crop") {
    if (event.button !== 0) return;
    const point = normalisedSubjectPoint(event);
    state.cropPointerId = event.pointerId;
    state.queryCrop = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
    event.currentTarget.setPointerCapture(event.pointerId);
    updateSubjectStatus();
    drawSubjectEditor();
    event.preventDefault();
    return;
  }
  if (state.queryMethod === "mask" && state.subjectSelectionMode === "auto") {
    if (!state.segmentationSessionId || state.segmentationBusy) return;
    if (event.pointerType === "mouse" && ![0, 2].includes(event.button)) return;
    state.subjectPreview = false;
    const point = normalisedSubjectPoint(event);
    state.segmentationPoints.push({
      x: point.x,
      y: point.y,
      label: event.pointerType === "mouse" && event.button === 2 ? 0 : state.segmentationPointLabel,
    });
    if (state.segmentationPoints.length > 32) state.segmentationPoints.shift();
    drawSubjectEditor();
    requestAutoMask();
    event.preventDefault();
  }
});
$("#subject-canvas").addEventListener("pointermove", event => {
  if (state.queryMethod !== "crop" || state.cropPointerId !== event.pointerId || !state.queryCrop) return;
  const point = normalisedSubjectPoint(event);
  state.queryCrop.x2 = point.x;
  state.queryCrop.y2 = point.y;
  updateSubjectStatus();
  drawSubjectEditor();
  event.preventDefault();
});
function finishCropSelection(event) {
  if (state.cropPointerId !== event.pointerId) return;
  if (!normalisedCrop()) state.queryCrop = null;
  state.cropPointerId = null;
  updateSubjectStatus();
  drawSubjectEditor();
  setMessage("#search-message", state.queryCrop ? "截图区域已选定，可以检索。" : "截图区域太小，请重新拖动框选。", state.queryCrop ? "success" : "error");
}
$("#subject-canvas").addEventListener("pointerup", finishCropSelection);
$("#subject-canvas").addEventListener("pointercancel", finishCropSelection);
$("#undo-subject").addEventListener("click", undoSubjectSelection);
$("#reset-subject").addEventListener("click", resetSubjectSelection);
$("#preview-subject").addEventListener("click", async () => {
  if (!state.queryMask) return;
  try {
    state.subjectPreview = !state.subjectPreview;
    updateSubjectStatus();
    drawSubjectEditor();
  } catch (error) {
    setMessage("#search-message", error.message, "error");
  }
});
$("#confirm-subject-search").addEventListener("click", async () => {
  if (state.queryMethod === "crop" && !normalisedCrop()) return;
  if (state.queryMethod === "mask" && !state.queryMask) return;
  try {
    state.subjectPreview = state.queryMethod === "mask";
    updateSubjectStatus();
    drawSubjectEditor();
    await search();
  } catch (error) {
    setMessage("#search-message", error.message, "error");
  }
});
$("#search-button").addEventListener("click", search);
$("#dialog-close").addEventListener("click", () => $("#folder-dialog").close());
$("#dialog-download").addEventListener("click", () => {
  if (state.activeFolder?.zip_url) downloadProtected(state.activeFolder.zip_url, `${state.activeFolder.name}.zip`);
});
window.addEventListener("beforeunload", () => {
  disposeSegmentationSession();
  clearObjectUrls();
});

(async () => {
  try { await ensureLibrary(); await loadLibrary(); }
  catch (error) { setMessage("#upload-message", error.message, "error"); }
})();
