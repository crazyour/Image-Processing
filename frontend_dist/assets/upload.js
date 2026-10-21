(() => {
  const input = document.querySelector("#file-input");
  const zone = document.querySelector("#drop-zone");
  const button = document.querySelector("#upload-button");
  const summary = document.querySelector("#selection-summary");
  const results = document.querySelector("#results");
  const list = document.querySelector("#file-list");
  const progress = document.querySelector("#progress");
  const gallery = document.querySelector("#asset-gallery");
  const assetCount = document.querySelector("#asset-count");
  const emptyState = document.querySelector("#empty-state");
  const refreshButton = document.querySelector("#refresh-button");
  let files = [];

  const formatSize = (bytes) => bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

  async function loadAssets() {
    refreshButton.disabled = true;
    assetCount.textContent = "正在读取…";
    try {
      const response = await fetch("/api/deliverables");
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "素材读取失败");
      const items = payload.items || [];
      assetCount.textContent = `${items.length} 份素材`;
      emptyState.hidden = Boolean(items.length);
      gallery.innerHTML = items.map(item => {
        const preview = item.type === "image"
          ? `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" loading="lazy">`
          : `<span>${escapeHtml(item.extension)}</span>`;
        return `<article class="asset-card"><div class="asset-preview">${preview}</div><div class="asset-body"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${formatSize(item.size)} · ${escapeHtml(item.extension)}</small><a href="${escapeHtml(item.url)}" download="${escapeHtml(item.name)}">下载素材</a></div></article>`;
      }).join("");
    } catch (error) {
      assetCount.textContent = error.message;
      gallery.innerHTML = "";
      emptyState.hidden = false;
    } finally {
      refreshButton.disabled = false;
    }
  }

  function select(nextFiles) {
    files = Array.from(nextFiles);
    summary.textContent = files.length ? `已选择 ${files.length} 个文件，共 ${formatSize(files.reduce((sum, file) => sum + file.size, 0))}` : "尚未选择文件";
    button.disabled = !files.length;
  }

  input.addEventListener("change", () => select(input.files));
  ["dragenter", "dragover"].forEach(type => zone.addEventListener(type, event => { event.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(type => zone.addEventListener(type, event => { event.preventDefault(); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", event => select(event.dataTransfer.files));
  refreshButton.addEventListener("click", loadAssets);

  button.addEventListener("click", async () => {
    if (!files.length) return;
    button.disabled = true;
    input.disabled = true;
    results.hidden = false;
    list.innerHTML = "";
    let completed = 0;
    for (const file of files) {
      progress.textContent = `正在上传 ${completed + 1} / ${files.length}`;
      const body = new FormData();
      body.set("file", file);
      try {
        const response = await fetch("/api/deliverables/upload", { method: "POST", body });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || `上传失败（${response.status}）`);
        const row = document.createElement("article");
        row.className = "file-row";
        row.innerHTML = `<div class="file-meta"><strong>${escapeHtml(payload.name)}</strong><small>${formatSize(payload.size)} · 已上传</small></div><div class="file-actions"><button type="button" class="copy">复制链接</button><a href="${escapeHtml(payload.url)}" download="${escapeHtml(payload.name)}">下载</a></div>`;
        row.querySelector(".copy").addEventListener("click", async event => {
          const absoluteUrl = new URL(payload.url, location.origin).href;
          try {
            await navigator.clipboard.writeText(absoluteUrl);
            event.currentTarget.textContent = "已复制";
          } catch (_) {
            window.prompt("复制文件链接", absoluteUrl);
          }
        });
        list.append(row);
      } catch (error) {
        const row = document.createElement("article");
        row.className = "file-row error";
        row.innerHTML = `<div class="file-meta"><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(error.message)}</small></div>`;
        list.append(row);
      }
      completed += 1;
    }
    progress.textContent = `已处理 ${completed} 个文件`;
    button.disabled = false;
    input.disabled = false;
    button.textContent = "再次上传";
    await loadAssets();
  });

  loadAssets();
})();
