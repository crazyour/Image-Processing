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
let task = null;
let busy = false;

function savedSession() {
  try { return JSON.parse(sessionStorage.getItem("text-image-task") || "null"); }
  catch { sessionStorage.removeItem("text-image-task"); return null; }
}

function savedSettings() {
  try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem("text-image-settings") || "{}") }; }
  catch { return { ...defaultSettings }; }
}

async function api(path, options = {}, timeout = 30_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(task?.token ? { "X-Session-Token": task.token } : {}),
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
$("#dismiss-error").addEventListener("click", () => $("#error-panel").classList.add("hidden"));
$("#refresh-task").addEventListener("click", () => refreshTask().catch(showError));
$("#new-task").addEventListener("click", resetTask);

const initialSettings = savedSettings();
$("#image-model").value = initialSettings.model;
$("#image-quality").value = initialSettings.quality;
$("#image-size").value = initialSettings.size;
addPrompt();
const previous = savedSession();
if (previous) {
  task = previous;
  refreshTask().catch((error) => { showError(error); resetTask(); });
}
