const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = { libraryId: null, token: null, selectedFiles: [], queryFile: null, folders: [], trash: [], objectUrls: new Set(), activeFolder: null };
const labels = { image: "图片", dxf: "DXF", illustrator: "Illustrator" };
const allowed = /\.(png|jpe?g|webp|dxf|ai)$/i;
const uploadChunkBytes = 2_500_000;

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
  const response = await fetch(path, { ...options, headers: { ...headers(options.json), ...(options.headers || {}) } });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response;
}

async function ensureLibrary() {
  state.libraryId = localStorage.getItem("lingjie-material-library");
  state.token = localStorage.getItem("lingjie-material-token");
  if (state.libraryId && state.token) return;
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

async function protectedObjectUrl(path) {
  const response = await api(path);
  const url = URL.createObjectURL(await response.blob());
  state.objectUrls.add(url);
  return url;
}

async function setProtectedImage(image, path) {
  try { image.src = await protectedObjectUrl(path); }
  catch { image.replaceWith(Object.assign(document.createElement("span"), { className: "thumb-placeholder", textContent: "预览失败" })); }
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
  const complete = ["image", "dxf", "illustrator"].every(kind => kinds.has(kind));
  selection.className = "selection";
  selection.innerHTML = `<div class="selected-title"><span>${safeText(folderName(state.selectedFiles))}</span><small>${state.selectedFiles.length} 个有效文件</small></div><ul class="selected-files">${state.selectedFiles.map(file => `<li><span class="type-pill">${labels[kindOf(file.name)]}</span><span>${safeText(file.webkitRelativePath || file.name)}</span><small>${bytes(file.size)}</small></li>`).join("")}</ul>`;
  $("#upload-button").disabled = !name || !complete;
  if (!name) setMessage("#upload-message", "文件夹名称为必填项。", "error");
  else if (!complete) setMessage("#upload-message", "需要同时包含图片、DXF和Illustrator AI文件。", "error");
  else setMessage("#upload-message", "文件检查通过，可以开始上传。", "success");
}

function uploadId() {
  return crypto.randomUUID().replaceAll("-", "");
}

async function uploadFileInChunks(file, chunkPath, completePath, completePayload, onChunk) {
  const chunkCount = Math.ceil(file.size / uploadChunkBytes);
  if (!chunkCount) throw new Error(`${file.name} 是空文件`);
  for (let chunkIndex = 0; chunkIndex < chunkCount; chunkIndex += 1) {
    const start = chunkIndex * uploadChunkBytes;
    const data = new FormData();
    data.append("chunk", file.slice(start, Math.min(start + uploadChunkBytes, file.size)), `${file.name}.part`);
    await api(`${chunkPath}/${chunkIndex}`, { method: "POST", body: data });
    onChunk?.();
  }
  return api(completePath, {
    method: "POST",
    json: true,
    body: JSON.stringify({ ...completePayload, filename: file.name, chunk_count: chunkCount }),
  });
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
    for (let index = 0; index < state.selectedFiles.length; index += 1) {
      const file = state.selectedFiles[index];
      $("#progress-label").textContent = `处理 ${file.name}`;
      const currentUploadId = uploadId();
      await uploadFileInChunks(
        file,
        `/api/material-search/folders/${folder.id}/uploads/${currentUploadId}/chunks`,
        `/api/material-search/folders/${folder.id}/uploads/${currentUploadId}/complete`,
        { relative_path: file.webkitRelativePath || file.name },
        () => {
          uploadedChunks += 1;
          const percent = Math.round((uploadedChunks / totalChunks) * 90);
          $("#progress-value").textContent = `${percent}%`;
          $("#progress-bar").style.width = `${percent}%`;
        },
      );
    }
    $("#progress-label").textContent = "生成文件夹下载包";
    $("#progress-bar").style.width = "94%";
    await api(`/api/material-search/folders/${folder.id}/finalize`, { method: "POST" });
    $("#progress-value").textContent = "100%";
    $("#progress-bar").style.width = "100%";
    setMessage("#upload-message", "上传完成，三类文件已经可以检索。", "success");
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
    state.folders = (await folderResponse.json()).folders;
    state.trash = (await trashResponse.json()).folders;
    $("#active-count").textContent = state.folders.length;
    $("#trash-count").textContent = state.trash.length;
    if (!state.folders.length) {
      list.innerHTML = '<p class="empty-copy">还没有素材。请先上传一个包含三类文件的文件夹。</p>';
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
    list.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
    cloudList.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
    trashList.innerHTML = `<p class="empty-copy">${safeText(error.message)}</p>`;
  }
}

function renderCloudFolders() {
  const list = $("#cloud-list");
  const totalFiles = state.folders.reduce((sum, folder) => sum + folder.assets.length, 0);
  const totalBytes = state.folders.reduce((sum, folder) => sum + folder.assets.reduce((folderSum, asset) => folderSum + asset.file_size, 0), 0);
  $("#cloud-summary").textContent = `共 ${state.folders.length} 个文件夹 · ${totalFiles} 个文件 · ${bytes(totalBytes)}`;
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

async function openFolder(folderId) {
  const response = await api(`/api/material-search/folders/${folderId}`);
  const folder = await response.json();
  state.activeFolder = folder;
  $("#dialog-title").textContent = folder.name;
  $("#dialog-content").innerHTML = folder.assets.map(asset => `<article class="asset-card"><img data-protected-src="${asset.preview_url}" alt="${safeText(asset.original_name)}"><div class="asset-meta"><strong>${safeText(asset.original_name)}</strong><span>${labels[asset.kind]} · ${bytes(asset.file_size)}</span><button type="button" data-download="${asset.blob_url}" data-name="${safeText(asset.original_name)}">下载原文件</button></div></article>`).join("");
  $("#dialog-content").querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
  $("#dialog-content").querySelectorAll("button[data-download]").forEach(button => button.addEventListener("click", () => downloadProtected(button.dataset.download, button.dataset.name)));
  $("#dialog-download").disabled = !folder.zip_url;
  $("#folder-dialog").showModal();
}

async function search() {
  if (!state.queryFile) return;
  const button = $("#search-button");
  button.disabled = true;
  setMessage("#search-message", "正在生成查询特征并比较素材库…");
  try {
    const currentUploadId = uploadId();
    const response = await uploadFileInChunks(
      state.queryFile,
      `/api/material-search/search/uploads/${currentUploadId}/chunks`,
      `/api/material-search/search/uploads/${currentUploadId}/complete`,
      { relative_path: state.queryFile.name },
    );
    const result = await response.json();
    $("#search-output").hidden = false;
    $("#results-title").textContent = `${labels[result.kind]}检索结果 · ${result.results.length} 个文件夹`;
    $("#query-preview").innerHTML = `<img src="${result.query_preview_data_url}" alt="查询文件预览">`;
    const list = $("#results-list");
    if (!result.results.length) {
      list.innerHTML = '<p class="empty-copy">素材库中还没有可检索的同类型文件。</p>';
    } else {
      list.innerHTML = result.results.map(item => `<article class="result-card"><div class="result-image"><span class="rank">${item.rank}</span><img data-protected-src="${item.matched_preview_url}" alt=""><span class="score">${item.score}%</span></div><div class="result-body"><h3>${safeText(item.folder_name)}</h3><p>匹配文件：${safeText(item.matched_file)}</p><div class="result-actions"><button type="button" data-folder="${item.folder_id}">查看</button><button type="button" data-zip="${item.zip_url || ""}" data-name="${safeText(item.folder_name)}.zip">下载</button></div></div></article>`).join("");
      list.querySelectorAll("img[data-protected-src]").forEach(image => setProtectedImage(image, image.dataset.protectedSrc));
      list.querySelectorAll("button[data-folder]").forEach(button => button.addEventListener("click", () => openFolder(button.dataset.folder)));
      list.querySelectorAll("button[data-zip]").forEach(button => button.addEventListener("click", () => downloadProtected(button.dataset.zip, button.dataset.name)));
    }
    setMessage("#search-message", "检索完成。相似度用于排序，不代表识别准确率。", "success");
  } catch (error) {
    setMessage("#search-message", error.message, "error");
  } finally {
    button.disabled = false;
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
$("#refresh-library").addEventListener("click", loadLibrary);
$("#refresh-cloud").addEventListener("click", loadLibrary);
$$('[data-cloud-view]').forEach(button => button.addEventListener("click", () => switchCloudView(button.dataset.cloudView)));
$("#query-input").addEventListener("change", event => {
  const file = event.target.files[0];
  state.queryFile = file || null;
  $("#query-label").textContent = file ? `${file.name} · ${bytes(file.size)}` : "选择图片、DXF或AI文件";
  $("#search-button").disabled = !file || !allowed.test(file.name);
  setMessage("#search-message", file ? "查询文件已就绪。" : "");
});
$("#search-button").addEventListener("click", search);
$("#dialog-close").addEventListener("click", () => $("#folder-dialog").close());
$("#dialog-download").addEventListener("click", () => {
  if (state.activeFolder?.zip_url) downloadProtected(state.activeFolder.zip_url, `${state.activeFolder.name}.zip`);
});
window.addEventListener("beforeunload", clearObjectUrls);

(async () => {
  try { await ensureLibrary(); await loadLibrary(); }
  catch (error) { setMessage("#upload-message", error.message, "error"); }
})();
