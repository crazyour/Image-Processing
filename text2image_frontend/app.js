const $ = (selector) => document.querySelector(selector);
if (new URLSearchParams(location.search).get("embedded") === "1") {
  document.body.classList.add("embedded");
}

const statusLabels = {
  QUEUED: "等待生成",
  GENERATING: "正在生成",
  SUCCEEDED: "已完成",
  FAILED: "生成失败",
};
const defaultSettings = {
  model: "gpt-image-2.5-flare",
  quality: "medium",
  size: "1024x1024",
};
let defaultAnimalPrompt = "";
let task = null;
let busy = false;
let animalTask = null;
let animalBusy = false;
let animalPreviewUrl = null;

function savedSession() {
  try { return JSON.parse(sessionStorage.getItem("text-image-task") || "null"); }
  catch { sessionStorage.removeItem("text-image-task"); return null; }
}

function savedSettings() {
  try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem("text-image-settings") || "{}") }; }
  catch { return { ...defaultSettings }; }
}

async function api(path, options = {}, timeout = 30_000, accessToken = task?.token) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { "X-Session-Token": accessToken } : {}),
        ...(options.headers || {}),
      },
    });
    let body;
    try { body = await response.json(); }
    catch { throw Object.assign(new Error("服务没有返回有效数据"), { code: "INVALID_RESPONSE" }); }
    if (!response.ok) throw Object.assign(new Error(body.message || body.detail || "请求失败"), { code: body.code || `HTTP_${response.status}` });
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw Object.assign(new Error("请求等待超时，请刷新任务状态后再操作"), { code: "REQUEST_TIMEOUT" });
    throw error;
  } finally { clearTimeout(timer); }
}

function showError(error) {
  $("#error-message").textContent = error.message || "操作未完成";
  $("#error-code").textContent = error.code || "UNKNOWN_ERROR";
  $("#error-panel").classList.remove("hidden");
}

function notify(message) {
  $("#notice").textContent = message;
  $("#notice").classList.remove("hidden");
  setTimeout(() => $("#notice").classList.add("hidden"), 4200);
}

function promptValues() {
  return [...document.querySelectorAll(".prompt-input")].map((input) => input.value.trim());
}

function updatePromptControls() {
  const rows = [...document.querySelectorAll(".prompt-row")];
  rows.forEach((row, index) => {
    row.querySelector(".prompt-number").textContent = String(index + 1).padStart(2, "0");
    row.querySelector("label").setAttribute("for", `prompt-${index}`);
    row.querySelector("textarea").id = `prompt-${index}`;
    row.querySelector(".remove-prompt").disabled = rows.length === 1 || busy;
  });
  $("#prompt-count").textContent = `${rows.length} / 5`;
  $("#add-prompt").disabled = rows.length >= 5 || busy;
}

function addPrompt(value = "") {
  if (document.querySelectorAll(".prompt-row").length >= 5) return;
  const row = document.createElement("div");
  row.className = "prompt-row";
  row.innerHTML = `
    <span class="prompt-number"></span>
    <label><span class="hidden">图片要求</span><textarea class="prompt-input" maxlength="2000" required placeholder="例如：现代客厅中的浅色橡木餐椅，柔和自然光，正面产品展示"></textarea></label>
    <button class="remove-prompt" type="button">删除</button>`;
  row.querySelector("textarea").value = value;
  row.querySelector(".remove-prompt").addEventListener("click", () => { row.remove(); updatePromptControls(); });
  $("#prompt-list").appendChild(row);
  updatePromptControls();
}

function settings() {
  return { model: $("#image-model").value.trim(), quality: $("#image-quality").value, size: $("#image-size").value };
}

function setBusy(value) {
  busy = value;
  $("#submit-task").disabled = value;
  $("#submit-task").textContent = value ? "队列生成中…" : "开始生成";
  document.querySelectorAll(".prompt-input, .remove-prompt").forEach((element) => { element.disabled = value; });
  updatePromptControls();
}

function applyTaskState(state) {
  task.state = state;
  $("#refresh-task").disabled = false;
  const counts = state.items.reduce((result, item) => {
    result[item.status] = (result[item.status] || 0) + 1;
    return result;
  }, {});
  const finished = counts.SUCCEEDED || 0;
  const failed = counts.FAILED || 0;
  const generating = counts.GENERATING || 0;
  $("#queue-summary").textContent = `共 ${state.items.length} 张 · 已完成 ${finished} · 生成中 ${generating} · 失败 ${failed}`;
  const taskState = $("#task-state");
  taskState.className = state.status === "SUCCEEDED" ? "success" : state.status === "GENERATING" ? "running" : "";
  taskState.textContent = state.status === "SUCCEEDED" ? "全部完成" : state.status === "GENERATING" ? "队列处理中" : failed ? "部分未完成" : "等待生成";

  const grid = $("#result-grid");
  grid.replaceChildren(...state.items.map((item) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.innerHTML = `<header><strong></strong><span class="status"></span></header><div class="image-frame"></div><p class="result-prompt"></p><div class="card-actions"></div>`;
    card.querySelector("header strong").textContent = `图片 ${item.index + 1}`;
    const status = card.querySelector(".status");
    status.classList.add(item.status);
    status.textContent = statusLabels[item.status] || item.status;
    const frame = card.querySelector(".image-frame");
    if (item.status === "SUCCEEDED") {
      const image = document.createElement("img");
      image.src = item.url;
      image.alt = `第 ${item.index + 1} 张生成结果`;
      frame.appendChild(image);
      const download = document.createElement("a");
      download.href = item.url;
      download.download = "";
      download.textContent = "下载 PNG";
      card.querySelector(".card-actions").appendChild(download);
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = `placeholder${item.status === "FAILED" ? " error-copy" : ""}`;
      if (item.status === "GENERATING") {
        const spinner = document.createElement("div");
        spinner.className = "spinner";
        placeholder.append(spinner, "正在生成并保存…");
      } else {
        placeholder.textContent = item.error?.message || statusLabels[item.status] || "等待生成";
      }
      frame.appendChild(placeholder);
      if (item.status === "FAILED" || item.status === "QUEUED") {
        const retry = document.createElement("button");
        retry.className = "secondary retry-item";
        retry.type = "button";
        retry.textContent = item.status === "FAILED" ? "重试这一张" : "生成这一张";
        retry.addEventListener("click", () => runQueue([item.index]));
        card.querySelector(".card-actions").appendChild(retry);
      }
    }
    card.querySelector(".result-prompt").textContent = item.prompt;
    return card;
  }));
}

async function refreshTask() {
  if (!task) return;
  const state = await api(`/api/text-image/tasks/${task.id}`);
  applyTaskState(state);
  return state;
}

async function generateItem(index) {
  task.state.items[index].status = "GENERATING";
  task.state.status = "GENERATING";
  applyTaskState(task.state);
  try {
    const state = await api(`/api/text-image/tasks/${task.id}/items/${index}/generate`, { method: "POST" }, 290_000);
    applyTaskState(state);
  } catch (error) {
    showError(error);
    try { await refreshTask(); }
    catch { task.state.items[index].status = "FAILED"; task.state.items[index].error = { message: error.message }; applyTaskState(task.state); }
  }
}

async function runQueue(indexes = null) {
  if (!task || busy) return;
  setBusy(true);
  try {
    const targets = indexes || task.state.items.filter((item) => item.status !== "SUCCEEDED").map((item) => item.index);
    for (const index of targets) {
      if (task.state.items[index]?.status === "SUCCEEDED") continue;
      await generateItem(index);
    }
    const state = await refreshTask();
    if (state.status === "SUCCEEDED") notify("本批图片已经全部生成并保存");
  } finally { setBusy(false); }
}

function resetTask() {
  task = null;
  sessionStorage.removeItem("text-image-task");
  $("#result-grid").replaceChildren();
  $("#queue-summary").textContent = "尚未提交任务。";
  $("#refresh-task").disabled = true;
  $("#task-state").className = "";
  $("#task-state").textContent = "等待输入";
  setBusy(false);
}

function showWorkflow(name) {
  const animal = name === "animal";
  document.querySelectorAll(".text-workflow").forEach((section) => { section.hidden = animal; });
  $("#animal-workflow").hidden = !animal;
  $("#show-text-workflow").setAttribute("aria-pressed", String(!animal));
  $("#show-animal-workflow").setAttribute("aria-pressed", String(animal));
}

function savedAnimalSession() {
  try { return JSON.parse(sessionStorage.getItem("animal-cutout-task") || "null"); }
  catch { sessionStorage.removeItem("animal-cutout-task"); return null; }
}

async function loadAnimalPromptTemplate() {
  const prompt = $("#animal-prompt");
  try {
    const response = await api("/api/animal-cutout/prompt-template", {}, 30_000, null);
    defaultAnimalPrompt = response.prompt || "";
    if (!prompt.value.trim()) prompt.value = defaultAnimalPrompt;
    $("#reset-animal-prompt").disabled = !defaultAnimalPrompt;
  } catch {
    prompt.placeholder = "默认提示词加载失败；提交时将使用后台默认提示词。";
    $("#reset-animal-prompt").disabled = true;
  }
}

function setAnimalBusy(value) {
  animalBusy = value;
  $("#submit-animal").disabled = value;
  $("#submit-animal").textContent = value ? "正在生成…" : "确认提示词并生成";
  $("#animal-image").disabled = value;
  $("#animal-prompt").disabled = value;
  $("#animal-consent").disabled = value;
  $("#animal-image-model").disabled = value;
  $("#animal-image-quality").disabled = value;
  $("#animal-image-size").disabled = value;
  $("#retry-animal").disabled = value;
  $("#manual-repair-animal").disabled = value;
  $("#restore-animal").disabled = value;
}

function animalPlaceholder(message, spinning = false) {
  const frame = $("#animal-result-frame");
  frame.replaceChildren();
  const placeholder = document.createElement("div");
  placeholder.className = "placeholder";
  if (spinning) {
    const spinner = document.createElement("div");
    spinner.className = "spinner";
    placeholder.append(spinner, message);
  } else {
    placeholder.textContent = message;
  }
  frame.appendChild(placeholder);
}

function animalConnectivitySummary(connectivity) {
  if (!connectivity) return "尚未检测";
  if (connectivity.passed) return "复检通过 · 黑色材料整体连通";
  if (connectivity.status === "NO_BLACK_MATERIAL") return "未检测到有效黑色主体";
  return `未通过 · ${connectivity.island_count || 0} 个孤岛或薄弱连接`;
}

function processImageFigure(label, url) {
  const figure = document.createElement("figure");
  const caption = document.createElement("figcaption");
  caption.textContent = label;
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const image = document.createElement("img");
  image.src = url;
  image.alt = label;
  image.loading = "lazy";
  link.appendChild(image);
  figure.append(caption, link);
  return figure;
}

function processRecordCard(title, result, options = {}) {
  const connectivity = options.connectivity || result?.connectivity;
  const card = document.createElement("article");
  card.className = "process-record";
  const header = document.createElement("div");
  header.className = "process-record-header";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const status = document.createElement("span");
  status.textContent = options.error ? "调用失败" : animalConnectivitySummary(connectivity);
  header.append(heading, status);
  card.appendChild(header);

  const images = document.createElement("div");
  images.className = "process-image-grid";
  if (result?.url) images.appendChild(processImageFigure(options.resultLabel || "输出结果", result.url));
  if (connectivity?.diagnostic_url) {
    images.appendChild(processImageFigure("彩色连通性诊断图", connectivity.diagnostic_url));
  }
  if (connectivity?.repair_guide_url) {
    images.appendChild(processImageFigure("AI 圈注修复指引图", connectivity.repair_guide_url));
  }
  if (images.childElementCount) card.appendChild(images);

  if (options.prompt) {
    const promptDetails = document.createElement("details");
    promptDetails.className = "process-prompt";
    const summary = document.createElement("summary");
    summary.textContent = "查看本次实际发送的 AI 修复提示词";
    const prompt = document.createElement("pre");
    prompt.textContent = options.prompt;
    promptDetails.append(summary, prompt);
    card.appendChild(promptDetails);
  }
  if (options.error) {
    const error = document.createElement("p");
    error.className = "process-error";
    error.textContent = options.error.message || "本次 AI 修复调用失败";
    card.appendChild(error);
  }
  return card;
}

function renderAnimalProcessDetails(state) {
  const details = $("#animal-process-details");
  const process = state.process_details;
  const original = process?.original_result;
  details.classList.toggle("hidden", !original);
  if (!original) return;

  const wasOpen = details.open;
  const repairs = process.repairs || [];
  details.querySelector(":scope > summary").textContent = `查看生成与修复过程（${repairs.length + 1} 条记录）`;
  $("#animal-generation-prompt").textContent = process.generation_prompt || "未记录";
  $("#animal-repair-prompt-template").textContent = process.repair_prompt_template || "未记录";

  const records = [processRecordCard("首次生成与本地检测", original, {
    resultLabel: "首次生成图",
    connectivity: original.connectivity,
  })];
  repairs.forEach((repair) => {
    const result = repair.result || (repair.url ? {
      url: repair.url,
      connectivity: repair.connectivity,
    } : null);
    records.push(processRecordCard(
      `${repair.automatic ? "自动" : "手动"} AI 修复 · 第 ${repair.attempt} 次`,
      result,
      {
        resultLabel: "本轮修复结果",
        connectivity: repair.connectivity,
        prompt: repair.prompt,
        error: repair.error,
      },
    ));
  });
  $("#animal-process-records").replaceChildren(...records);
  details.open = wasOpen;
}

function applyAnimalState(state) {
  animalTask.state = state;
  $("#animal-result-section").classList.remove("hidden");
  $("#animal-source-result").src = state.source_url;
  const labels = {
    READY: "等待生成",
    GENERATING: state.operation === "repair" ? "AI 修复中" : "正在生成并检测",
    REPAIR_REQUIRED: "等待自动修复",
    REVIEW_REQUIRED: "需要人工处理",
    SUCCEEDED: "连通性检测通过",
    FAILED: "生成失败",
  };
  const status = $("#animal-task-state");
  status.textContent = labels[state.status] || state.status;
  status.className = state.status === "SUCCEEDED" ? "success" : state.status === "GENERATING" ? "running" : "";
  $("#retry-animal").classList.toggle("hidden", state.status !== "FAILED");
  $("#manual-repair-animal").classList.toggle("hidden", state.status !== "REVIEW_REQUIRED" || !state.can_manual_repair);
  $("#restore-animal").classList.toggle("hidden", state.status === "GENERATING" || !state.can_restore_original);
  $("#download-animal").classList.add("hidden");
  const connectivity = state.connectivity;
  renderAnimalProcessDetails(state);
  const connectivityStatus = $("#animal-connectivity-status");
  if (state.status === "SUCCEEDED" && connectivity?.passed) {
    const repairs = state.repair_attempts || 0;
    connectivityStatus.textContent = repairs
      ? `检测通过：全部黑色材料已连通，共完成 ${repairs} 次修复。`
      : "检测通过：全部黑色材料属于同一连通主体，未调用 AI 修复。";
  } else if (state.status === "REVIEW_REQUIRED" && connectivity) {
    const count = connectivity.island_count || 0;
    connectivityStatus.textContent = count
      ? `自动修复已停止，仍有 ${count} 个孤岛或单像素薄弱连接，请手动再次修复或恢复原图。`
      : "未检测到有效黑色主体，请恢复原图或重新生成后人工处理。";
  } else if (state.status === "GENERATING") {
    connectivityStatus.textContent = state.operation === "repair"
      ? `检测未通过，正在进行第 ${(state.automatic_repair_attempts || 0)} 次 AI 修复并将在完成后复检…`
      : "正在生成黑白图；生成后将立即在本地检测连通性…";
  } else {
    connectivityStatus.textContent = "生成后会自动检测黑色连通性；仅检测不通过时调用 AI 修复。";
  }
  if (["SUCCEEDED", "REVIEW_REQUIRED"].includes(state.status) && state.result?.url) {
    const image = document.createElement("img");
    image.src = state.result.url;
    image.alt = "根据动物照片生成的黑白镂空设计候选";
    $("#animal-result-frame").replaceChildren(image);
    $("#download-animal").href = state.result.url;
    $("#download-animal").classList.remove("hidden");
  } else if (state.status === "GENERATING") {
    animalPlaceholder(
      state.operation === "repair"
        ? "检测到孤岛，正在 AI 修复并重新检测…"
        : "正在提取动物并生成黑白镂空图…",
      true,
    );
  } else if (state.status === "REPAIR_REQUIRED") {
    animalPlaceholder("检测到孤岛，准备自动修复…", true);
  } else if (state.status === "FAILED") {
    animalPlaceholder(state.last_error?.message || "生成失败，可以重试");
  } else {
    animalPlaceholder("参考照片已保存，等待生成");
  }
}

async function runAutomaticAnimalRepairs(initialState) {
  let state = initialState;
  while (state.status === "REPAIR_REQUIRED") {
    const pending = {
      ...state,
      status: "GENERATING",
      operation: "repair",
      automatic_repair_attempts: (state.automatic_repair_attempts || 0) + 1,
    };
    applyAnimalState(pending);
    state = await api(
      `/api/animal-cutout/tasks/${animalTask.id}/repair`,
      { method: "POST", body: JSON.stringify({ automatic: true }) },
      290_000,
      animalTask.token,
    );
  }
  applyAnimalState(state);
  return state;
}

async function refreshAnimalTask() {
  if (!animalTask) return null;
  const state = await api(`/api/animal-cutout/tasks/${animalTask.id}`, {}, 30_000, animalTask.token);
  applyAnimalState(state);
  return state;
}

async function generateAnimalCutout() {
  if (!animalTask || animalBusy) return;
  setAnimalBusy(true);
  applyAnimalState({ ...animalTask.state, status: "GENERATING", last_error: null });
  try {
    const state = await api(
      `/api/animal-cutout/tasks/${animalTask.id}/generate`,
      { method: "POST" },
      290_000,
      animalTask.token,
    );
    const finalState = await runAutomaticAnimalRepairs(state);
    notify(finalState.status === "SUCCEEDED"
      ? "最终结果已通过黑色连通性检测"
      : "自动修复已停止，请查看剩余问题并人工处理");
  } catch (error) {
    showError(error);
    try { await refreshAnimalTask(); }
    catch { applyAnimalState({ ...animalTask.state, status: "FAILED", last_error: { message: error.message } }); }
  } finally {
    setAnimalBusy(false);
  }
}

async function repairAnimalCutoutManually() {
  if (!animalTask || animalBusy) return;
  setAnimalBusy(true);
  applyAnimalState({ ...animalTask.state, status: "GENERATING", operation: "repair" });
  try {
    const state = await api(
      `/api/animal-cutout/tasks/${animalTask.id}/repair`,
      { method: "POST", body: JSON.stringify({ automatic: false }) },
      290_000,
      animalTask.token,
    );
    applyAnimalState(state);
    notify(state.status === "SUCCEEDED" ? "本次修复已通过检测" : "本次修复后仍有连通性问题");
  } catch (error) {
    showError(error);
    try { await refreshAnimalTask(); }
    catch { applyAnimalState({ ...animalTask.state, status: "REVIEW_REQUIRED", last_error: { message: error.message } }); }
  } finally {
    setAnimalBusy(false);
  }
}

async function restoreOriginalAnimalCutout() {
  if (!animalTask || animalBusy) return;
  setAnimalBusy(true);
  try {
    const state = await api(
      `/api/animal-cutout/tasks/${animalTask.id}/restore-original`,
      { method: "POST" },
      30_000,
      animalTask.token,
    );
    applyAnimalState(state);
    notify("已恢复首次生成的原图");
  } catch (error) {
    showError(error);
  } finally {
    setAnimalBusy(false);
  }
}

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  $("#error-panel").classList.add("hidden");
  const prompts = promptValues();
  if (prompts.some((prompt) => !prompt)) return showError({ message: "请填写每一条图片要求", code: "PROMPT_REQUIRED" });
  const modelSettings = settings();
  if (!modelSettings.model) return showError({ message: "请填写图片生成模型", code: "MODEL_REQUIRED" });
  localStorage.setItem("text-image-settings", JSON.stringify(modelSettings));
  setBusy(true);
  try {
    const state = await api("/api/text-image/tasks", { method: "POST", body: JSON.stringify({ prompts, ...modelSettings }) });
    task = { id: state.task_id, token: state.access_token, state };
    sessionStorage.setItem("text-image-task", JSON.stringify({ id: task.id, token: task.token }));
    applyTaskState(state);
  } catch (error) {
    showError(error);
    setBusy(false);
    return;
  }
  setBusy(false);
  await runQueue();
});

$("#add-prompt").addEventListener("click", () => addPrompt());
$("#show-text-workflow").addEventListener("click", () => showWorkflow("text"));
$("#show-animal-workflow").addEventListener("click", () => showWorkflow("animal"));
$("#reset-animal-prompt").addEventListener("click", () => {
  if (!defaultAnimalPrompt) return;
  $("#animal-prompt").value = defaultAnimalPrompt;
  $("#animal-prompt").focus();
});
$("#animal-image").addEventListener("change", (event) => {
  if (animalPreviewUrl) URL.revokeObjectURL(animalPreviewUrl);
  const file = event.target.files?.[0];
  const preview = $("#animal-source-preview");
  if (!file) {
    animalPreviewUrl = null;
    preview.innerHTML = "<span>支持 PNG、JPEG、WebP，至少 128 × 128，最大 4 MB</span>";
    return;
  }
  animalPreviewUrl = URL.createObjectURL(file);
  const image = document.createElement("img");
  image.src = animalPreviewUrl;
  image.alt = "待处理的动物参考照片";
  preview.replaceChildren(image);
});
$("#animal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (animalBusy) return;
  $("#error-panel").classList.add("hidden");
  const file = $("#animal-image").files?.[0];
  if (!file) return showError({ message: "请上传一张动物参考照片", code: "ANIMAL_IMAGE_REQUIRED" });
  if (!$("#animal-consent").checked) return showError({ message: "请先确认照片使用授权", code: "PHOTO_CONSENT_REQUIRED" });
  const form = new FormData();
  form.append("image", file);
  form.append("prompt", $("#animal-prompt").value.trim() || defaultAnimalPrompt);
  form.append("image_model", $("#animal-image-model").value.trim());
  form.append("image_quality", $("#animal-image-quality").value);
  form.append("size", $("#animal-image-size").value);
  setAnimalBusy(true);
  try {
    const state = await api("/api/animal-cutout/tasks", { method: "POST", body: form }, 60_000, null);
    animalTask = { id: state.task_id, token: state.access_token, state };
    sessionStorage.setItem("animal-cutout-task", JSON.stringify({ id: animalTask.id, token: animalTask.token }));
    applyAnimalState(state);
  } catch (error) {
    showError(error);
    setAnimalBusy(false);
    return;
  }
  setAnimalBusy(false);
  await generateAnimalCutout();
});
$("#retry-animal").addEventListener("click", generateAnimalCutout);
$("#manual-repair-animal").addEventListener("click", repairAnimalCutoutManually);
$("#restore-animal").addEventListener("click", restoreOriginalAnimalCutout);
$("#dismiss-error").addEventListener("click", () => $("#error-panel").classList.add("hidden"));
$("#refresh-task").addEventListener("click", () => refreshTask().catch(showError));
$("#new-task").addEventListener("click", resetTask);

const initialSettings = savedSettings();
$("#image-model").value = initialSettings.model;
$("#image-quality").value = initialSettings.quality;
$("#image-size").value = initialSettings.size;
$("#animal-image-model").value = initialSettings.model;
$("#animal-image-quality").value = initialSettings.quality;
$("#animal-image-size").value = initialSettings.size;
$("#reset-animal-prompt").disabled = true;
loadAnimalPromptTemplate();
addPrompt();
const previous = savedSession();
if (previous) {
  task = previous;
  refreshTask().catch((error) => { showError(error); resetTask(); });
}
const previousAnimal = savedAnimalSession();
if (previousAnimal) {
  animalTask = previousAnimal;
  showWorkflow("animal");
  refreshAnimalTask()
    .then(async (state) => {
      if (state.status !== "REPAIR_REQUIRED") return;
      setAnimalBusy(true);
      try { await runAutomaticAnimalRepairs(state); }
      catch (error) { showError(error); await refreshAnimalTask().catch(() => {}); }
      finally { setAnimalBusy(false); }
    })
    .catch((error) => {
      showError(error);
      animalTask = null;
      sessionStorage.removeItem("animal-cutout-task");
    });
}
