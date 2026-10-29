const state = {user: null, folders: [], activeFolder: null, items: [], selected: new Set(), admin: null, editingFolder: null};

const $ = selector => document.querySelector(selector);
const folderList = $("#folder-list");
const mediaGrid = $("#media-grid");
const message = $("#message");

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[character]);
}

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const response = await fetch(path, {credentials: "same-origin", ...options, headers: {...headers, ...(options.headers || {})}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || "请求未完成");
    error.status = response.status;
    throw error;
  }
  return data;
}

function showMessage(text, isError = false, target = message) {
  target.textContent = text;
  target.classList.toggle("error", isError);
  target.classList.remove("hidden");
}

function hideMessage(target = message) { target.classList.add("hidden"); }

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  for (const unit of units) {
    if (size < 1024 || unit === "TB") return `${size >= 100 ? size.toFixed(0) : size.toFixed(1)} ${unit}`;
    size /= 1024;
  }
}

function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
}

function renderFolders() {
  $("#folder-count").textContent = state.folders.length;
  $("#saved-total").textContent = formatBytes(state.folders.reduce((sum, folder) => sum + Math.max(0, folder.original_size_bytes - folder.stored_size_bytes), 0));
  if (!state.folders.length) {
    folderList.innerHTML = `<p class="loading">${state.user.role === "admin" ? "还没有文件夹，请先创建。" : "管理员尚未分配可访问的文件夹。"}</p>`;
    return;
  }
  folderList.innerHTML = state.folders.map(folder => `
    <button class="folder-button ${state.activeFolder?.id === folder.id ? "active" : ""}" type="button" data-folder-id="${folder.id}">
      <span class="folder-icon" aria-hidden="true"></span><strong>${escapeHtml(folder.name)}</strong><small>${folder.item_count}</small>
    </button>
  `).join("");
}

function updateSelection() {
  const count = state.selected.size;
  $("#selected-count").textContent = count;
  $("#batch-download").classList.toggle("hidden", count === 0);
  document.querySelectorAll(".media-card").forEach(card => card.classList.toggle("selected", state.selected.has(card.dataset.mediaId)));
}

function renderMedia() {
  if (!state.items.length) {
    mediaGrid.innerHTML = `<div class="empty-state"><span class="aperture" aria-hidden="true"></span><h3>这个文件夹还是空的</h3><p>上传第一张图片或视频。系统会在保存前自动压缩，原文件不会占用服务器空间。</p></div>`;
    return;
  }
  mediaGrid.innerHTML = state.items.map(item => `
    <article class="media-card" data-media-id="${item.id}">
      <input class="select-box" type="checkbox" aria-label="选择 ${escapeHtml(item.name)}" data-select-id="${item.id}">
      <button class="media-preview" type="button" data-preview-id="${item.id}" aria-label="预览 ${escapeHtml(item.name)}">
        ${item.media_type === "image" ? `<img src="${item.content_url}" loading="lazy" alt="">` : `<video src="${item.content_url}#t=0.1" preload="metadata" muted playsinline></video><span class="video-mark"></span>`}
      </button>
      <div class="media-copy">
        <div class="media-title-line"><h3 title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</h3><a href="${item.download_url}">下载</a></div>
        <p class="media-meta">${item.stored_width} × ${item.stored_height} · ${formatBytes(item.stored_size_bytes)}<br><span class="saving">节省 ${item.saving_percent}%</span> · ${escapeHtml(item.uploader_name)} · ${formatDate(item.created_at)}</p>
      </div>
    </article>
  `).join("");
  updateSelection();
}

async function selectFolder(folderId) {
  const folder = state.folders.find(item => item.id === folderId);
  if (!folder) return;
  state.activeFolder = folder;
  state.selected.clear();
  renderFolders();
  $("#stage-path").textContent = `摄影目录 / ${folder.item_count} 个文件`;
  $("#folder-title").textContent = folder.name;
  const saved = Math.max(0, folder.original_size_bytes - folder.stored_size_bytes);
  $("#folder-summary").textContent = `压缩存储 ${formatBytes(folder.stored_size_bytes)}，已节省 ${formatBytes(saved)}。下载会恢复到原始宽高。`;
  $("#upload-open").disabled = false;
  mediaGrid.innerHTML = '<p class="loading">正在读取媒体…</p>';
  try {
    const data = await api(`/api/photography/folders/${folder.id}/media`);
    state.items = data.items;
    renderMedia();
  } catch (error) {
    showMessage(error.message, true);
  }
}

folderList.addEventListener("click", event => {
  const button = event.target.closest("[data-folder-id]");
  if (button) selectFolder(button.dataset.folderId);
});

mediaGrid.addEventListener("change", event => {
  const checkbox = event.target.closest("[data-select-id]");
  if (!checkbox) return;
  checkbox.checked ? state.selected.add(checkbox.dataset.selectId) : state.selected.delete(checkbox.dataset.selectId);
  updateSelection();
});

mediaGrid.addEventListener("click", event => {
  const button = event.target.closest("[data-preview-id]");
  if (!button) return;
  const item = state.items.find(value => value.id === button.dataset.previewId);
  if (!item) return;
  $("#preview-title").textContent = item.name;
  $("#preview-meta").textContent = `压缩版 ${item.stored_width} × ${item.stored_height} · 下载恢复到 ${item.original_width} × ${item.original_height}`;
  $("#preview-download").href = item.download_url;
  $("#preview-body").innerHTML = item.media_type === "image"
    ? `<img src="${item.content_url}" alt="">`
    : `<video src="${item.content_url}" controls autoplay playsinline></video>`;
  $("#preview-dialog").showModal();
});

$("#batch-download").addEventListener("click", () => {
  if (!state.activeFolder || !state.selected.size) return;
  location.href = `/api/photography/folders/${state.activeFolder.id}/download?ids=${encodeURIComponent([...state.selected].join(","))}`;
});

$("#help-open").addEventListener("click", () => $("#help-dialog").showModal());

const uploadDialog = $("#upload-dialog");
const uploadForm = $("#upload-form");
const fileInput = $("#upload-file");

function selectedFileChanged() {
  const file = fileInput.files[0];
  $("#file-label").textContent = file ? `${file.name} · ${formatBytes(file.size)}` : "选择图片或视频";
  if (file && !$("#custom-filename").value) $("#custom-filename").value = file.name.replace(/\.[^.]+$/, "");
}

fileInput.addEventListener("change", selectedFileChanged);
$("#upload-open").addEventListener("click", () => {
  if (!state.activeFolder) return;
  uploadForm.reset();
  selectedFileChanged();
  hideMessage($("#upload-message"));
  $("#upload-progress").classList.add("hidden");
  $("#upload-folder-name").textContent = state.activeFolder.name;
  uploadDialog.showModal();
});

const drop = $("#file-drop");
["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => {event.preventDefault(); drop.classList.add("dragging");}));
["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => {event.preventDefault(); drop.classList.remove("dragging");}));
drop.addEventListener("drop", event => {
  if (!event.dataTransfer.files.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(event.dataTransfer.files[0]);
  fileInput.files = transfer.files;
  selectedFileChanged();
});

function uploadChunk(path, blob, filename, onProgress) {
  return new Promise((resolve, reject) => {
    const data = new FormData();
    data.append("chunk", blob, filename);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    xhr.onload = () => {
      let response = {};
      try { response = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(response);
      else reject(new Error(response.message || "上传分片失败"));
    };
    xhr.onerror = () => reject(new Error("网络连接中断，请重新上传"));
    xhr.send(data);
  });
}

uploadForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.activeFolder || !fileInput.files[0]) return;
  const file = fileInput.files[0];
  if (file.size > 200_000_000) {
    showMessage("单个文件不能超过 200 MB", true, $("#upload-message"));
    return;
  }
  const progress = $("#upload-progress");
  const bar = progress.querySelector("i");
  const text = progress.querySelector("span");
  const submit = uploadForm.querySelector('[type="submit"]');
  progress.classList.remove("hidden");
  submit.disabled = true;
  hideMessage($("#upload-message"));
  const chunkSize = 3_000_000;
  const chunkCount = Math.ceil(file.size / chunkSize);
  const uploadId = crypto.randomUUID().replaceAll("-", "");
  try {
    for (let index = 0; index < chunkCount; index += 1) {
      const start = index * chunkSize;
      const chunk = file.slice(start, Math.min(file.size, start + chunkSize));
      await uploadChunk(
        `/api/photography/folders/${state.activeFolder.id}/uploads/${uploadId}/chunks/${index}`,
        chunk,
        `${file.name}.part`,
        loaded => {
          const percent = Math.min(99, Math.round((start + loaded) / file.size * 100));
          bar.style.width = `${percent}%`;
          text.textContent = `正在上传 ${percent}% · ${index + 1}/${chunkCount} 分片`;
        },
      );
    }
    bar.style.width = "100%";
    text.textContent = "上传完成，正在压缩…";
    const response = await api(
      `/api/photography/folders/${state.activeFolder.id}/uploads/${uploadId}/complete`,
      {method: "POST", body: JSON.stringify({
        filename: file.name,
        custom_filename: $("#custom-filename").value,
        chunk_count: chunkCount,
      })},
    );
    uploadDialog.close();
    showMessage(`“${response.item.name}”已压缩保存，节省 ${response.item.saving_percent}% 空间。`);
    await loadFolders(false);
    await selectFolder(state.activeFolder.id);
  } catch (error) {
    showMessage(error.message, true, $("#upload-message"));
  } finally {
    submit.disabled = false;
  }
});

function renderAdminFolders() {
  const list = $("#admin-folder-list");
  $("#admin-folder-count").textContent = `${state.admin.folders.length} 个文件夹`;
  if (!state.admin.folders.length) {
    list.innerHTML = '<p class="loading">还没有摄影文件夹，点击“新建文件夹”开始。</p>';
    return;
  }
  list.innerHTML = state.admin.folders.map(folder => `
    <div class="admin-folder-row">
      <div><strong>${escapeHtml(folder.name)}</strong><p>${folder.item_count} 个文件 · ${formatBytes(folder.stored_size_bytes)}</p></div>
      <div class="member-chips">${folder.members.length ? folder.members.map(member => `<span>${escapeHtml(member.name)}</span>`).join("") : "<span>未分配成员</span>"}</div>
      <button class="secondary-button" type="button" data-edit-folder="${folder.id}">编辑</button>
    </div>
  `).join("");
}

async function loadAdmin() {
  state.admin = await api("/api/admin/photography/folders");
  renderAdminFolders();
}

$("#admin-open").addEventListener("click", async () => {
  $("#admin-folder-list").innerHTML = '<p class="loading">正在读取文件夹…</p>';
  $("#admin-dialog").showModal();
  try { await loadAdmin(); } catch (error) { $("#admin-folder-list").innerHTML = `<p class="message error">${escapeHtml(error.message)}</p>`; }
});

function openFolderEditor(folder = null) {
  state.editingFolder = folder;
  const form = $("#folder-form");
  form.reset();
  hideMessage($("#folder-message"));
  $("#folder-dialog-title").textContent = folder ? `编辑 ${folder.name}` : "新建文件夹";
  form.elements.name.value = folder?.name || "";
  const selected = new Set(folder?.member_ids || []);
  $("#member-options").innerHTML = state.admin.members.length ? state.admin.members.map(member => `
    <label class="member-option"><input type="checkbox" name="member" value="${member.id}" ${selected.has(member.id) ? "checked" : ""}><span>${escapeHtml(member.name)}<small>@${escapeHtml(member.username)}</small></span></label>
  `).join("") : '<p class="loading">还没有可分配的普通成员。</p>';
  $("#folder-delete").classList.toggle("hidden", !folder);
  $("#folder-dialog").showModal();
  form.elements.name.focus();
}

$("#folder-create").addEventListener("click", () => openFolderEditor());
$("#admin-folder-list").addEventListener("click", event => {
  const button = event.target.closest("[data-edit-folder]");
  if (button) openFolderEditor(state.admin.folders.find(folder => folder.id === button.dataset.editFolder));
});

$("#folder-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {name: form.elements.name.value, member_ids: [...form.querySelectorAll('[name="member"]:checked')].map(input => input.value)};
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const path = state.editingFolder ? `/api/admin/photography/folders/${state.editingFolder.id}` : "/api/admin/photography/folders";
    await api(path, {method: state.editingFolder ? "PUT" : "POST", body: JSON.stringify(payload)});
    $("#folder-dialog").close();
    await Promise.all([loadAdmin(), loadFolders(false)]);
  } catch (error) { showMessage(error.message, true, $("#folder-message")); }
  finally { submit.disabled = false; }
});

$("#folder-delete").addEventListener("click", async () => {
  const folder = state.editingFolder;
  if (!folder || !confirm(`确定删除空文件夹“${folder.name}”吗？`)) return;
  try {
    await api(`/api/admin/photography/folders/${folder.id}`, {method: "DELETE"});
    $("#folder-dialog").close();
    await Promise.all([loadAdmin(), loadFolders(true)]);
  } catch (error) { showMessage(error.message, true, $("#folder-message")); }
});

document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => {
  const dialog = document.getElementById(button.dataset.close);
  if (dialog.id === "preview-dialog") $("#preview-body").innerHTML = "";
  dialog.close();
}));

document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("click", event => {
  if (event.target === dialog) {
    if (dialog.id === "preview-dialog") $("#preview-body").innerHTML = "";
    dialog.close();
  }
}));

async function loadFolders(selectFirst = true) {
  const data = await api("/api/photography/folders");
  state.folders = data.folders;
  if (state.activeFolder) state.activeFolder = state.folders.find(folder => folder.id === state.activeFolder.id) || null;
  renderFolders();
  if (selectFirst && !state.activeFolder && state.folders.length) await selectFolder(state.folders[0].id);
}

async function initialize() {
  try {
    const auth = await api("/api/auth/me");
    state.user = auth.user;
    $("#account-name").textContent = `${state.user.name}${state.user.role === "admin" ? " · 管理员" : ""}`;
    if (state.user.role === "admin") $("#admin-open").classList.remove("hidden");
    await loadFolders(true);
  } catch (error) {
    if (error.status === 401) location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    else showMessage(error.message, true);
  }
}

initialize();
