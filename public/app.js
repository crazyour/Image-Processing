const $ = (selector) => document.querySelector(selector);
if (new URLSearchParams(location.search).get("embedded") === "1") {
  document.body.classList.add("embedded");
}
const labels = { front: "正面（原图）", left: "左面", right: "右面", back: "背面" };
const defaultModelSettings = {
  vision_model: "gpt-6-luna",
  image_model: "gpt-image-2.5-flare",
  image_quality: "medium",
  hunyuan_model: "hy-3d-3.1",
};
let session = JSON.parse(localStorage.getItem("image3d-session") || "null");
let state = null;
let polling = null;

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
  box.classList.remove("hidden");
  clearTimeout(box.timer);
  box.timer = setTimeout(() => box.classList.add("hidden"), 6000);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (session?.token) headers.set("X-Session-Token", session.token);
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || `请求失败 (${response.status})`);
  return body;
}

function setBusy(button, busy, text = "处理中…") {
  if (!button) return;
  if (busy) { button.dataset.label = button.textContent; button.textContent = text; }
  else if (button.dataset.label) button.textContent = button.dataset.label;
  button.disabled = busy;
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
  const entries = [
    ["主体", analysis.subject], ["几何结构", analysis.geometry],
    ["材质", (analysis.materials || []).join("、")], ["颜色", (analysis.colors || []).join("、")],
    ["遮挡区域", (analysis.occlusion_regions || []).join("、") || "未发现"],
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
  $("#views-grid").innerHTML = ["front", "left", "right", "back"].map((view) => {
    const item = views[view];
    if (!item) return `<article class="view-card"><h3>${labels[view]}</h3><p>尚未生成</p></article>`;
    const refinement = view === "front" ? "" : `<div class="refine"><input id="refine-${view}" type="text" placeholder="例如：不要增加装饰"><button data-refine="${view}">微调</button></div>`;
    return `<article class="view-card"><h3>${labels[view]}</h3><img src="${item.url}" alt="${labels[view]}">${refinement}</article>`;
  }).join("");
  document.querySelectorAll("[data-refine]").forEach((button) => button.addEventListener("click", () => refine(button.dataset.refine, button)));
}

function render(next) {
  state = next;
  $("#prompt-step").classList.toggle("hidden", !state);
  if (!state) return;
  renderAnalysis(state.analysis);
  $("#prompt-editor").value = state.user_prompt || "";
  const hasGenerated = ["left", "right", "back"].some((view) => state.views?.[view]);
  $("#prompt-step").open = !hasGenerated && ["PROMPT_REVIEW", "VIEWS_REVIEW"].includes(state.status);
  $("#views-step").classList.toggle("hidden", !hasGenerated);
  if (hasGenerated) renderViews(state.views);
  const canGenerate = ["CONFIRMED", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED", "FAILED"].includes(state.status);
  $("#generate-step").classList.toggle("hidden", !canGenerate);
  $("#confirm-views").disabled = state.views_stale || state.status !== "VIEWS_REVIEW";
  $("#generate-3d").disabled = state.status !== "CONFIRMED";
  renderTask();
}

function renderTask() {
  const task = state?.hunyuan_task;
  const running = ["SUBMITTING_3D", "GENERATING_3D"].includes(state?.status);
  $("#task-progress").classList.toggle("hidden", !running);
  $("#task-progress span").textContent = state?.status === "SUBMITTING_3D" ? "正在提交腾讯混元…" : "正在生成 3D，页面会自动刷新状态…";
  const models = task?.models || {};
  $("#model-results").innerHTML = Object.entries(models).map(([type, model]) =>
    `<a class="model-link" href="${model.url}" target="_blank" rel="noopener"><span>${type.toUpperCase()} 模型</span><span>下载 ↗</span></a>`
  ).join("");
  if (running && !polling) polling = setInterval(refreshTask, 8000);
  if (!running && polling) { clearInterval(polling); polling = null; }
}

async function refresh() {
  if (!session) return;
  try { render(await api(`/api/sessions/${session.id}`)); }
  catch (error) { localStorage.removeItem("image3d-session"); session = null; notify(error.message, true); }
}

async function refreshTask() {
  if (!session || state?.status !== "GENERATING_3D") return;
  try {
    render(await api(`/api/sessions/${session.id}/3d`));
    if (state.status === "SUCCEEDED") notify("3D 模型生成完成");
    if (state.status === "FAILED") notify(state.last_error?.message || "3D 生成失败", true);
  } catch (error) { notify(error.message, true); }
}

$("#image-input").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  $("#upload-preview").src = URL.createObjectURL(file);
  $("#upload-preview").classList.remove("hidden");
  $("#drop-copy").classList.add("hidden");
});

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  try {
    setBusy(button, true, "GPT 正在分析…");
    const source = $("#image-input").files?.[0];
    if (!source) throw new Error("请选择图片");
    const file = await compressedFile(source);
    const form = new FormData();
    form.set("image", file);
    form.set("prompt", $("#initial-prompt").value.trim());
    const models = readModelSettings();
    if (!models.vision_model || !models.image_model || !models.hunyuan_model) {
      throw new Error("请填写完整的模型设置");
    }
    Object.entries(models).forEach(([name, value]) => form.set(name, value));
    localStorage.setItem("image3d-model-settings", JSON.stringify(models));
    const result = await api("/api/sessions", { method: "POST", body: form });
    session = { id: result.session_id, token: result.access_token };
    localStorage.setItem("image3d-session", JSON.stringify(session));
    render(result);
    button.textContent = "正在生成三个视角…";
    render(await api(`/api/sessions/${session.id}/views`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ views: ["left", "right", "back"] }),
    }));
    notify("左、右、背面图已生成");
    $("#views-step").scrollIntoView({ behavior: "smooth" });
  } catch (error) { notify(error.message, true); await refresh(); }
  finally { setBusy(button, false); }
});

$("#save-prompt").addEventListener("click", async (event) => {
  try {
    setBusy(event.currentTarget, true);
    render(await api(`/api/sessions/${session.id}/prompt`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: $("#prompt-editor").value.trim() }),
    }));
    notify("提示词已保存；旧多视图需要重新生成");
  } catch (error) { notify(error.message, true); }
  finally { setBusy(event.currentTarget, false); }
});

async function generateViews(button) {
  try {
    setBusy(button, true, "正在生成三个视角…");
    const prompt = $("#prompt-editor").value.trim();
    if (prompt !== state.user_prompt) {
      render(await api(`/api/sessions/${session.id}/prompt`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
      }));
    }
    render(await api(`/api/sessions/${session.id}/views`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ views: ["left", "right", "back"] }),
    }));
    $("#views-step").scrollIntoView({ behavior: "smooth" });
  } catch (error) { notify(error.message, true); await refresh(); }
  finally { setBusy(button, false); }
}

$("#generate-views").addEventListener("click", (event) => generateViews(event.currentTarget));
$("#regenerate-views").addEventListener("click", (event) => generateViews(event.currentTarget));

async function refine(view, button) {
  const prompt = $(`#refine-${view}`).value.trim();
  if (!prompt) return notify("请输入该视图的微调要求", true);
  try {
    setBusy(button, true);
    render(await api(`/api/sessions/${session.id}/views/${view}/refine`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
    }));
  } catch (error) { notify(error.message, true); }
  finally { setBusy(button, false); }
}

$("#confirm-views").addEventListener("click", async (event) => {
  try {
    setBusy(event.currentTarget, true);
    render(await api(`/api/sessions/${session.id}/confirm`, { method: "POST" }));
    notify("提示词和四视图已锁定");
    $("#generate-step").scrollIntoView({ behavior: "smooth" });
  } catch (error) { notify(error.message, true); }
  finally { setBusy(event.currentTarget, false); }
});

$("#generate-3d").addEventListener("click", async (event) => {
  try {
    setBusy(event.currentTarget, true, "正在提交…");
    render(await api(`/api/sessions/${session.id}/generate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ generate_type: $("#generate-type").value, enable_pbr: $("#enable-pbr").checked }),
    }));
  } catch (error) { notify(error.message, true); await refresh(); }
  finally { setBusy(event.currentTarget, false); }
});

initializeModelSettings();
refresh();
