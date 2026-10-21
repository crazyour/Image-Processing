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
  const uploadTask = document.querySelector("#upload-task");
  const filterAccount = document.querySelector("#filter-account");
  const filterTask = document.querySelector("#filter-task");
  const viewerName = document.querySelector("#viewer-name");
  const uploadOwner = document.querySelector("#upload-owner");
  const tabBrowse = document.querySelector("#tab-browse");
  const tabUpload = document.querySelector("#tab-upload");
  const panelBrowse = document.querySelector("#panel-browse");
  const panelUpload = document.querySelector("#panel-upload");
  let files = [];
  let items = [];

  const formatSize = (bytes) => bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const formatTime = (value) => {
    if (!value) return "";
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) return "";
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  };

  function selectTab(tab) {
    const browse = tab === "browse";
    tabBrowse.classList.toggle("is-active", browse);
    tabUpload.classList.toggle("is-active", !browse);
    tabBrowse.setAttribute("aria-selected", String(browse));
    tabUpload.setAttribute("aria-selected", String(!browse));
    panelBrowse.hidden = !browse;
    panelUpload.hidden = browse;
  }

  tabBrowse.addEventListener("click", () => selectTab("browse"));
  tabUpload.addEventListener("click", () => selectTab("upload"));

  function renderFilters(payload) {
    const accounts = payload.accounts || [];
    const tasks = payload.tasks || [];
    const accountValue = filterAccount.value;
    const taskValue = filterTask.value;
    filterAccount.innerHTML = `<option value="">全部账号（${accounts.length}）</option>` + accounts.map(account =>
      `<option value="${escapeHtml(account.id)}">${escapeHtml(account.name)}${account.username ? `（${escapeHtml(account.username)}）` : ""}</option>`
    ).join("");
    filterTask.innerHTML = `<option value="">全部类型</option>` + tasks.map(task =>
      `<option value="${escapeHtml(task.key)}">${escapeHtml(task.label)}</option>`
    ).join("");
    filterAccount.value = accountValue;
    filterTask.value = taskValue;
  }

  function renderGallery() {
    const accountFilter = filterAccount.value;
    const taskFilter = filterTask.value;
    const visible = items.filter(item => (
      (!accountFilter || (item.uploader_id || item.uploader_name) === accountFilter)
      && (!taskFilter || item.task === taskFilter)
    ));
    assetCount.textContent = `${visible.length} 份素材 / 共 ${items.length} 份`;
    emptyState.hidden = visible.length > 0;

    const accounts = new Map();
    visible.forEach(item => {
      const key = item.uploader_id || item.uploader_name;
      if (!accounts.has(key)) {
        accounts.set(key, {
          name: item.uploader_name,
          username: item.uploader_username,
          items: [],
        });
      }
      accounts.get(key).items.push(item);
    });

    gallery.innerHTML = [...accounts.values()].map(account => {
      const byTask = new Map();
      account.items.forEach(item => {
        if (!byTask.has(item.task)) byTask.set(item.task, { label: item.task_label, items: [] });
        byTask.get(item.task).items.push(item);
      });
      const taskSections = [...byTask.values()].map(group => {
        const cards = group.items.map(item => {
          const preview = item.type === "image"
            ? `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" loading="lazy">`
            : `<span>${escapeHtml(item.extension)}</span>`;
          const time = formatTime(item.uploaded_at);
          return `<article class="asset-card"><div class="asset-preview">${preview}</div><div class="asset-body"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${formatSize(item.size)} · ${escapeHtml(item.extension)}${time ? ` · ${time}` : ""}</small><a href="${escapeHtml(item.url)}" download="${escapeHtml(item.name)}">下载素材</a></div></article>`;
        }).join("");
        return `<section class="task-group"><div class="task-group-heading"><h4>${escapeHtml(group.label)}</h4><span>${group.items.length} 份</span></div><div class="asset-gallery">${cards}</div></section>`;
      }).join("");
      const total = account.items.reduce((sum, item) => sum + (item.size || 0), 0);
      return `<section class="owner-group"><header class="owner-heading">
          <div class="owner-avatar">${escapeHtml((account.name || "?").slice(0, 1).toUpperCase())}</div>
          <div class="owner-title"><strong>${escapeHtml(account.name)}</strong>${account.username ? `<small>@${escapeHtml(account.username)}</small>` : ""}</div>
          <span class="owner-count">${account.items.length} 份 · ${formatSize(total)}</span>
        </header>${taskSections}</section>`;
    }).join("");
  }

  async function loadAssets() {
    refreshButton.disabled = true;
    assetCount.textContent = "正在读取…";
    try {
      const response = await fetch("/api/deliverables");
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || payload.message || "素材读取失败");
      items = payload.items || [];
      renderFilters(payload);
      renderGallery();
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
  filterAccount.addEventListener("change", renderGallery);
  filterTask.addEventListener("change", renderGallery);

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
      body.set("task", uploadTask.value);
      try {
        const response = await fetch("/api/deliverables/upload", { method: "POST", body });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || payload.message || `上传失败（${response.status}）`);
        const row = document.createElement("article");
        row.className = "file-row";
        row.innerHTML = `<div class="file-meta"><strong>${escapeHtml(payload.name)}</strong><small>${escapeHtml(payload.task_label)} · ${formatSize(payload.size)} · ${escapeHtml(payload.uploader_name || "")} 已上传</small></div><div class="file-actions"><button type="button" class="copy">复制链接</button><a href="${escapeHtml(payload.url)}" download="${escapeHtml(payload.name)}">下载</a></div>`;
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

  async function loadViewer() {
    try {
      const response = await fetch("/api/auth/me");
      if (!response.ok) return;
      const payload = await response.json();
      const user = payload.user || {};
      const label = user.role === "admin" ? `${user.name} · 管理员` : (user.name || "");
      if (viewerName) viewerName.textContent = label;
      if (uploadOwner) uploadOwner.textContent = label ? `将以上传账号「${label}」保存，团队成员都能在素材展示中看到。` : "";
    } catch (_) {
      /* 顶栏信息缺失不影响上传 */
    }
  }

  loadAssets();
  loadViewer();
})();
