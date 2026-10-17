export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {credentials: "same-origin", ...options, headers});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") location.href = "/login";
    throw new Error(data.message || "请求未完成");
  }
  return data;
}

export function showMessage(element, message, error = false) {
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.remove("hidden");
}

export async function logout() {
  await api("/api/auth/logout", {method: "POST"});
  location.href = "/login";
}
