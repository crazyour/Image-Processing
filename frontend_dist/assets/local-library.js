(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const panel = $("#library-panel");
  const toggle = $("#library-toggle");
  if (!panel || !toggle) return;

  const workflowLabels = {
    "image-to-3d": "图生 3D",
    "text-to-image": "文生图",
    "animal-photo-cutout": "照片转镂空剪影",
  };
  const statusLabels = {
    QUEUED: "等待生成", GENERATING: "正在生成", PARTIAL: "部分完成",
    READY: "等待生成", CANDIDATES_READY: "候选待选择", REPAIR_REQUIRED: "需要修复",
    REVIEW_REQUIRED: "等待检查", SUCCEEDED: "已完成", FAILED: "失败",
    REFERENCES_READY: "参考图已保存", ANALYZING_REFERENCES: "正在分析",
    PROMPT_REVIEW: "描述待确认", DESCRIPTION_CONFIRMED: "描述已确认",
    GENERATING_VIEWS: "正在生成四视图", VIEWS_REVIEW: "四视图待确认",
    VIEWS_CONFIRMED: "四视图已确认", CONFIRMED: "已确认",
    GENERATING_ASSEMBLY: "正在生成拼装图", ASSEMBLY_REVIEW: "拼装图待确认",
    ASSEMBLY_CONFIRMED: "拼装图已确认", SUBMITTING_3D: "正在提交 3D",
    GENERATING_3D: "正在生成 3D",
  };
  let tasks = [];

  async function libraryApi(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Local-Library", "1");
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || body.detail || "本地作品库操作失败");
    return body;
  }

  function announce(message, error = false) {
    const notice = $("#notice");
    if (!notice) return;
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.classList.remove("hidden");
    clearTimeout(notice.libraryTimer);
    notice.libraryTimer = setTimeout(() => notice.classList.add("hidden"), 4500);
  }

  function formatDate(value) {
    if (!value) return "时间未知";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(String(value).replace(" ", "T") + (String(value).includes("Z") ? "" : "Z"));
    if (Number.isNaN(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  async function updateTask(id, values) {
    const updated = await libraryApi(`/api/local-library/tasks/${id}`, {
      method: "PATCH", body: JSON.stringify(values),
    });
    const index = tasks.findIndex((item) => item.id === id);
    if (index >= 0) tasks[index] = updated;
    renderTasks();
    return updated;
  }

  async function resumeTask(item, button) {
    button.disabled = true;
    button.textContent = "正在打开…";
    try {
      const resumed = await libraryApi(`/api/local-library/tasks/${item.id}/resume`, { method: "POST" });
      const saved = JSON.stringify({ id: resumed.task_id, token: resumed.access_token });
      if (resumed.workflow === "image-to-3d") {
        sessionStorage.setItem("image3d-session", saved);
      } else if (resumed.workflow === "animal-photo-cutout") {
        sessionStorage.setItem("animal-cutout-task", saved);
      } else {
        sessionStorage.setItem("text-image-task", saved);
      }
      location.href = resumed.page;
    } catch (error) {
      announce(error.message, true);
      button.disabled = false;
      button.textContent = "打开记录";
    }
  }

  function renderTasks() {
    const grid = $("#library-grid");
    const empty = $("#library-empty");
    const query = ($("#library-search")?.value || "").trim().toLowerCase();
    const visible = tasks.filter((item) => [item.title, item.prompt, workflowLabels[item.workflow]]
      .join(" ").toLowerCase().includes(query));
    grid.replaceChildren();
    empty.classList.toggle("hidden", visible.length > 0);
    for (const item of visible) {
      const card = document.createElement("article");
      card.className = `library-card${item.favorite ? " favorite" : ""}`;

      const visual = document.createElement("div");
      visual.className = "library-visual";
      if (item.preview_url) {
        const image = document.createElement("img");
        image.src = item.preview_url;
        image.alt = "作品预览";
        image.loading = "lazy";
        visual.appendChild(image);
      } else {
        visual.textContent = workflowLabels[item.workflow]?.slice(0, 1) || "作";
      }

      const body = document.createElement("div");
      body.className = "library-card-body";
      const meta = document.createElement("div");
      meta.className = "library-card-meta";
      const workflow = document.createElement("span");
      workflow.textContent = workflowLabels[item.workflow] || item.workflow;
      const status = document.createElement("span");
      status.textContent = statusLabels[item.status] || item.status;
      meta.append(workflow, status);

      const title = document.createElement("input");
      title.className = "library-title";
      title.value = item.title;
      title.maxLength = 120;
      title.setAttribute("aria-label", "作品名称");
      title.addEventListener("change", () => {
        updateTask(item.id, { title: title.value.trim() }).then(() => announce("作品名称已保存")).catch((error) => announce(error.message, true));
      });

      const prompt = document.createElement("p");
      prompt.className = "library-prompt";
      prompt.textContent = item.prompt || "没有附加提示词";
      const foot = document.createElement("div");
      foot.className = "library-card-foot";
      const facts = document.createElement("small");
      facts.textContent = `${formatDate(item.updated_at)} · ${item.asset_count} 个文件${item.preference_applied ? " · 已应用偏好" : ""}`;
      const actions = document.createElement("div");
      const favorite = document.createElement("button");
      favorite.type = "button";
      favorite.className = "library-favorite";
      favorite.textContent = item.favorite ? "★ 已收藏" : "☆ 收藏";
      favorite.addEventListener("click", () => updateTask(item.id, { favorite: !item.favorite }).catch((error) => announce(error.message, true)));
      const open = document.createElement("button");
      open.type = "button";
      open.className = "library-open";
      open.textContent = "打开记录";
      open.addEventListener("click", () => resumeTask(item, open));
      actions.append(favorite, open);
      foot.append(facts, actions);
      body.append(meta, title, prompt, foot);
      card.append(visual, body);
      grid.appendChild(card);
    }
    $("#library-count").textContent = `${visible.length} / ${tasks.length} 条记录`;
  }

  async function loadLibrary() {
    $("#library-status").textContent = "正在读取本地记录…";
    try {
      const [taskResult, preferences] = await Promise.all([
        libraryApi("/api/local-library/tasks"),
        libraryApi("/api/local-library/preferences"),
      ]);
      tasks = taskResult.items || [];
      $("#library-preferences-enabled").checked = Boolean(preferences.enabled);
      $("#library-preferences-prompt").value = preferences.prompt || "";
      $("#library-status").textContent = "记录保存在本机 SQLite 和 var/blobs；关闭浏览器不会丢失。";
      renderTasks();
    } catch (error) {
      if (String(error.message).includes("不可用")) toggle.classList.add("hidden");
      $("#library-status").textContent = error.message;
    }
  }

  toggle.addEventListener("click", () => {
    const willOpen = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !willOpen);
    toggle.setAttribute("aria-expanded", String(willOpen));
    if (willOpen) loadLibrary();
  });
  $("#library-close")?.addEventListener("click", () => {
    panel.classList.add("hidden");
    toggle.setAttribute("aria-expanded", "false");
  });
  $("#library-refresh")?.addEventListener("click", loadLibrary);
  $("#library-search")?.addEventListener("input", renderTasks);
  $("#library-save-preferences")?.addEventListener("click", async () => {
    const button = $("#library-save-preferences");
    button.disabled = true;
    try {
      await libraryApi("/api/local-library/preferences", {
        method: "PUT",
        body: JSON.stringify({
          enabled: $("#library-preferences-enabled").checked,
          prompt: $("#library-preferences-prompt").value.trim(),
        }),
      });
      announce("个人偏好已保存，将应用到之后创建的新任务");
    } catch (error) {
      announce(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("#library-new-task")?.addEventListener("click", () => {
    if (location.pathname.includes("image-to-3d")) {
      sessionStorage.removeItem("image3d-session");
    } else {
      sessionStorage.removeItem("text-image-task");
      sessionStorage.removeItem("animal-cutout-task");
    }
    location.reload();
  });
}());
