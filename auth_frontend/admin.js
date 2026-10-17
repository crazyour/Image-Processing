import {api, logout, showMessage} from "/auth-static/common.js";

const message = document.querySelector("#message");
const list = document.querySelector("#user-list");
const stats = document.querySelector("#account-stats");
const search = document.querySelector("#user-search");
const createForm = document.querySelector("#create-form");
const editForm = document.querySelector("#edit-form");
const dialog = document.querySelector("#user-dialog");
let catalog = {};
let currentUser = null;
let users = [];
let editingUser = null;

document.querySelector("#logout").addEventListener("click", logout);
document.querySelector("#dialog-close").addEventListener("click", () => dialog.close());
document.querySelector("#dialog-cancel").addEventListener("click", () => dialog.close());
search.addEventListener("input", renderUsers);
dialog.addEventListener("click", event => {
  if (event.target === dialog) dialog.close();
});

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
  `;
}

function renderUsers() {
  const query = search.value.trim().toLowerCase();
  const filtered = users.filter(user => (
    !query || user.name.toLowerCase().includes(query) || user.username.toLowerCase().includes(query)
  ));
  if (!filtered.length) {
    list.innerHTML = `<tr><td class="empty" colspan="6">${query ? "没有找到匹配的账号。" : "暂无账号。"}</td></tr>`;
    return;
  }
  list.innerHTML = filtered.map(user => {
    const enabledFeatures = user.role === "admin" ? Object.keys(catalog).length : user.features.length;
    const roleLabel = user.role === "admin" ? "管理员" : "普通成员";
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
        <td class="operation-column"><button class="secondary manage-user" type="button" data-user-id="${escapeHtml(user.id)}">管理</button></td>
      </tr>
    `;
  }).join("");
  list.querySelectorAll(".manage-user").forEach(button => {
    button.addEventListener("click", () => openEditor(button.dataset.userId));
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
}

editForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!editingUser) return;
  const values = new FormData(editForm);
  const payload = {
    username: values.get("username"),
    name: values.get("name"),
    role: values.get("role"),
    active: values.get("active") === "true",
    features: selectedFeatures(editForm, "edit-feature"),
  };
  if (values.get("password")) payload.password = values.get("password");
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
    showMessage(message, "新账号已创建，可立即登录。", false);
    await load();
  } catch (error) {
    showMessage(message, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

load().catch(error => showMessage(message, error.message, true));
