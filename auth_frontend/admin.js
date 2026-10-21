import {api, logout, showMessage} from "/auth-static/common.js";

const message = document.querySelector("#message");
const createMessage = document.querySelector("#create-message");
const list = document.querySelector("#user-list");
const stats = document.querySelector("#account-stats");
const search = document.querySelector("#user-search");
const createForm = document.querySelector("#create-form");
const editForm = document.querySelector("#edit-form");
const createDialog = document.querySelector("#create-dialog");
const dialog = document.querySelector("#user-dialog");
const usageDialog = document.querySelector("#usage-dialog");
let catalog = {};
let currentUser = null;
let users = [];
let editingUser = null;
let usageReport = {accounts: [], feature_totals: [], grand_total_usd: 0, grand_total_calls: 0};

document.querySelector("#logout").addEventListener("click", logout);
document.querySelector("#create-account").addEventListener("click", () => {
  createForm.reset();
  createMessage.classList.add("hidden");
  document.querySelector("#create-features").innerHTML = featureInputs([], "create-feature");
  createDialog.showModal();
  createForm.elements.username.focus();
});
document.querySelector("#create-dialog-close").addEventListener("click", () => createDialog.close());
document.querySelector("#create-dialog-cancel").addEventListener("click", () => createDialog.close());
document.querySelector("#dialog-close").addEventListener("click", () => dialog.close());
document.querySelector("#dialog-cancel").addEventListener("click", () => dialog.close());
document.querySelector("#usage-dialog-close").addEventListener("click", () => usageDialog.close());
search.addEventListener("input", renderUsers);
createDialog.addEventListener("click", event => {
  if (event.target === createDialog) createDialog.close();
});
dialog.addEventListener("click", event => {
  if (event.target === dialog) dialog.close();
});
usageDialog.addEventListener("click", event => {
  if (event.target === usageDialog) usageDialog.close();
});
editForm.elements.password_confirm.addEventListener("input", () => {
  editForm.elements.password_confirm.setCustomValidity("");
});

function formatUsd(value) {
  const amount = Number(value || 0);
  if (!amount) return "$0.00";
  if (Math.abs(amount) < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

function usageFor(userId, username) {
  return (usageReport.accounts || []).find(
    item => (item.user_id || item.username) === (userId || username),
  );
}

function openUsageDetail(key, fallbackName = "", fallbackUsername = "") {
  const account = (usageReport.accounts || []).find(
    item => (item.user_id || item.username) === key,
  );
  const name = account?.name || fallbackName || fallbackUsername || "未知账号";
  const username = account?.username || fallbackUsername;
  const total = account?.total_usd || 0;
  const calls = account?.total_calls || 0;
  document.querySelector("#usage-dialog-title").textContent = `${name} · 使用明细`;
  document.querySelector("#usage-dialog-summary").textContent =
    `${username ? `@${username} · ` : ""}合计 ${formatUsd(total)}`;
  const features = account?.features || [];
  document.querySelector("#usage-detail-list").innerHTML = features.length
    ? features.map(feature => `
      <tr>
        <td>${escapeHtml(feature.label)}</td>
        <td>${feature.calls} 次</td>
        <td>${formatUsd(feature.cost_usd)}</td>
      </tr>
    `).join("")
    : '<tr><td class="empty" colspan="3">该账号还没有产生费用的调用记录。</td></tr>';
  document.querySelector("#usage-detail-calls").textContent = `${calls} 次`;
  document.querySelector("#usage-detail-total").textContent = formatUsd(total);
  const models = features.flatMap(feature =>
    (feature.models || []).map(model => `
      <span class="usage-model-chip">${escapeHtml(feature.label)} · ${escapeHtml(model.model)}：${model.calls} 次 / ${formatUsd(model.cost_usd)}</span>
    `),
  );
  const sources = (account?.sources || []).map(item => `
    <span class="usage-source-chip source-provider">${escapeHtml(item.source)}：${item.calls} 次 / ${formatUsd(item.cost_usd)}</span>
  `);
  document.querySelector("#usage-detail-models").innerHTML = [
    models.length ? `<span class="panel-kicker">按模型</span><div class="usage-model-list">${models.join("")}</div>` : "",
    sources.length ? `<span class="panel-kicker">计价方式</span><div class="usage-model-list">${sources.join("")}</div>` : "",
  ].join("");
  usageDialog.showModal();
}

async function loadUsage() {
  try {
    usageReport = await api("/api/admin/usage");
    renderStats();
    renderUsers();
  } catch (error) {
    showMessage(message, error.message, true);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function featureInputs(selected = [], prefix = "feature") {
  return Object.entries(catalog).map(([key, label]) => `
    <label class="check">
      <input type="checkbox" name="${escapeHtml(prefix)}" value="${escapeHtml(key)}" ${selected.includes(key) ? "checked" : ""}>
      ${escapeHtml(label)}
    </label>
  `).join("");
}

function selectedFeatures(form, name) {
  return [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
}

function renderStats() {
  const active = users.filter(user => user.active).length;
  const admins = users.filter(user => user.active && user.role === "admin").length;
  stats.innerHTML = `
    <div><strong>${users.length}</strong><span>全部账号</span></div>
    <div><strong>${active}</strong><span>已启用</span></div>
    <div><strong>${admins}</strong><span>管理员</span></div>
    <div><strong>${formatUsd(usageReport.grand_total_usd)}</strong><span>使用额合计</span></div>
  `;
}

function renderUsers() {
  const query = search.value.trim().toLowerCase();
  const filtered = users.filter(user => (
    !query || user.name.toLowerCase().includes(query) || user.username.toLowerCase().includes(query)
  ));
  if (!filtered.length) {
    list.innerHTML = `<tr><td class="empty" colspan="7">${query ? "没有找到匹配的账号。" : "暂无账号。"}</td></tr>`;
    return;
  }
  list.innerHTML = filtered.map(user => {
    const enabledFeatures = user.role === "admin" ? Object.keys(catalog).length : user.features.length;
    const roleLabel = user.role === "admin" ? "管理员" : "普通成员";
    const usage = usageFor(user.id, user.username);
    return `
      <tr class="account-row ${user.active ? "" : "is-disabled"}">
        <td>
          <div class="member-cell">
            <div class="user-avatar">${escapeHtml((user.name || user.username).slice(0, 1).toUpperCase())}</div>
            <div class="user-title-line">
              <strong>${escapeHtml(user.name)}</strong>
            ${user.id === currentUser.id ? '<span class="badge current">当前账号</span>' : ""}
            </div>
          </div>
        </td>
        <td><code class="username-cell">${escapeHtml(user.username)}</code></td>
        <td><span class="badge">${roleLabel}</span></td>
        <td><span class="feature-count">${enabledFeatures} 项功能</span></td>
        <td><span class="status-dot ${user.active ? "online" : ""}">${user.active ? "已启用" : "已停用"}</span></td>
        <td><strong class="usage-amount">${formatUsd(usage?.total_usd || 0)}</strong></td>
        <td class="operation-column">
          <div class="row-actions">
            <button class="secondary manage-user" type="button" data-user-id="${escapeHtml(user.id)}">管理</button>
            <button class="secondary account-usage-button" type="button" data-user-id="${escapeHtml(user.id)}" data-user-name="${escapeHtml(user.name)}" data-username="${escapeHtml(user.username)}">使用额</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
  list.querySelectorAll(".manage-user").forEach(button => {
    button.addEventListener("click", () => openEditor(button.dataset.userId));
  });
  list.querySelectorAll(".account-usage-button").forEach(button => {
    button.addEventListener("click", async () => {
      await loadUsage();
      openUsageDetail(button.dataset.userId, button.dataset.userName, button.dataset.username);
    });
  });
}

function openEditor(userId) {
  editingUser = users.find(user => user.id === userId);
  if (!editingUser) return;
  editForm.reset();
  editForm.elements.username.value = editingUser.username;
  editForm.elements.name.value = editingUser.name;
  editForm.elements.role.value = editingUser.role;
  editForm.elements.active.value = String(editingUser.active);
  document.querySelector("#dialog-title").textContent = `管理 ${editingUser.name}`;
  document.querySelector("#dialog-summary").textContent = `@${editingUser.username}${editingUser.id === currentUser.id ? " · 当前登录账号" : ""}`;
  document.querySelector("#edit-features").innerHTML = featureInputs(editingUser.features, "edit-feature");
  dialog.showModal();
}

async function load() {
  const [me, data] = await Promise.all([api("/api/auth/me"), api("/api/admin/users")]);
  currentUser = me.user;
  catalog = data.feature_catalog;
  users = data.users;
  document.querySelector("#create-features").innerHTML = featureInputs([], "create-feature");
  renderStats();
  renderUsers();
  await loadUsage();
}

editForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!editingUser) return;
  const values = new FormData(editForm);
  const password = values.get("password");
  const passwordConfirm = values.get("password_confirm");
  if (password !== passwordConfirm) {
    editForm.elements.password_confirm.setCustomValidity("两次输入的密码不一致");
    editForm.elements.password_confirm.reportValidity();
    return;
  }
  editForm.elements.password_confirm.setCustomValidity("");
  const payload = {
    username: values.get("username"),
    name: values.get("name"),
    role: values.get("role"),
    active: values.get("active") === "true",
    features: selectedFeatures(editForm, "edit-feature"),
  };
  if (password) payload.password = password;
  const submit = editForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await api(`/api/admin/users/${editingUser.id}`, {method: "PATCH", body: JSON.stringify(payload)});
    dialog.close();
    showMessage(message, "账号修改已保存。", false);
    await load();
  } catch (error) {
    showMessage(message, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

createForm.addEventListener("submit", async event => {
  event.preventDefault();
  const values = new FormData(createForm);
  const payload = {
    username: values.get("username"),
    name: values.get("name"),
    password: values.get("password"),
    role: values.get("role"),
    features: selectedFeatures(createForm, "create-feature"),
  };
  const submit = createForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await api("/api/admin/users", {method: "POST", body: JSON.stringify(payload)});
    createForm.reset();
    createDialog.close();
    showMessage(message, "新账号已创建，可立即登录。", false);
    await load();
  } catch (error) {
    showMessage(createMessage, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

load().catch(error => showMessage(message, error.message, true));
