const LOCK_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>';
const ALWAYS_AVAILABLE = ["workspace"];

const accountName = document.querySelector("#account-name");
const adminEntry = document.querySelector("#admin-entry");
const logoutButton = document.querySelector("#logout");
const accessMessage = document.querySelector("#access-message");
const passwordEntry = document.querySelector("#password-entry");
const passwordDialog = document.querySelector("#password-dialog");
const passwordForm = document.querySelector("#password-form");
const passwordMessage = document.querySelector("#password-message");

async function api(path, options = {}) {
  const response = await fetch(path, {credentials: "same-origin", ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || "请求未完成");
    error.status = response.status;
    throw error;
  }
  return data;
}

function showAccessMessage(text) {
  if (!accessMessage) return;
  accessMessage.textContent = text;
  accessMessage.classList.remove("hidden");
}

function featureTitle(element, catalog) {
  const heading = element.querySelector("strong")?.textContent?.trim();
  if (heading) return heading;
  const key = (element.dataset.features || "").split(",")[0].trim();
  return catalog[key] || "该功能";
}

function lockElement(element, catalog) {
  const title = featureTitle(element, catalog);
  element.classList.add("is-locked");
  element.setAttribute("aria-disabled", "true");
  element.setAttribute("title", `${title} 尚未开通，请联系管理员分配权限`);
  element.removeAttribute("href");
  if (element.classList.contains("feature-card")) {
    const lock = document.createElement("span");
    lock.className = "lock-icon";
    lock.setAttribute("aria-hidden", "true");
    lock.innerHTML = LOCK_ICON;
    element.append(lock);
  }
  element.addEventListener("click", event => {
    event.preventDefault();
    showAccessMessage(`“${title}”尚未开通，请联系管理员分配权限。`);
  });
}

function applyPermissions(user, catalog) {
  const granted = new Set([
    ...ALWAYS_AVAILABLE,
    ...(user.role === "admin" ? Object.keys(catalog) : (user.features || [])),
  ]);
  document.querySelectorAll("[data-features]").forEach(element => {
    const required = (element.dataset.features || "").split(",").map(value => value.trim()).filter(Boolean);
    if (!required.length || required.some(feature => granted.has(feature))) return;
    lockElement(element, catalog);
  });
}

function renderAccount(user) {
  if (accountName) accountName.textContent = user.role === "admin" ? `${user.name} · 管理员` : user.name;
  if (adminEntry && user.role === "admin") adminEntry.classList.remove("hidden");
}

logoutButton?.addEventListener("click", async () => {
  logoutButton.disabled = true;
  try {
    await api("/api/auth/logout", {method: "POST"});
    location.href = "/login";
  } catch (_) {
    logoutButton.disabled = false;
  }
});

function closePasswordDialog() {
  passwordDialog.close();
  passwordForm.reset();
  passwordMessage.classList.add("hidden");
  passwordMessage.textContent = "";
}

passwordEntry?.addEventListener("click", () => {
  passwordForm.reset();
  passwordMessage.classList.add("hidden");
  passwordDialog.showModal();
  passwordForm.elements.current_password.focus();
});
document.querySelector("#password-dialog-close")?.addEventListener("click", closePasswordDialog);
document.querySelector("#password-cancel")?.addEventListener("click", closePasswordDialog);
passwordDialog?.addEventListener("click", event => {
  if (event.target === passwordDialog) closePasswordDialog();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && passwordDialog?.open) closePasswordDialog();
});

passwordForm?.addEventListener("submit", async event => {
  event.preventDefault();
  const values = new FormData(passwordForm);
  const currentPassword = values.get("current_password");
  const newPassword = values.get("new_password");
  const confirmPassword = values.get("confirm_password");
  if (newPassword !== confirmPassword) {
    passwordMessage.textContent = "两次输入的新密码不一致";
    passwordMessage.classList.remove("hidden");
    return;
  }
  const submit = passwordForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({current_password: currentPassword, new_password: newPassword}),
    });
    closePasswordDialog();
    showAccessMessage("密码已修改，其它设备需要重新登录。");
  } catch (error) {
    passwordMessage.textContent = error.message;
    passwordMessage.classList.remove("hidden");
  } finally {
    submit.disabled = false;
  }
});

if (new URLSearchParams(location.search).has("denied")) {
  showAccessMessage("当前账号没有访问该功能的权限，请联系管理员开通。");
}

api("/api/auth/me")
  .then(data => {
    renderAccount(data.user);
    applyPermissions(data.user, data.feature_catalog || {});
  })
  .catch(error => {
    if (error.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      return;
    }
    showAccessMessage(error.message);
  });
