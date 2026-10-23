export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {credentials: "same-origin", ...options, headers});
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => ({}))
    : {};
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") location.href = "/login";
    const detail = typeof data.detail === "string" ? data.detail : "";
    const fallback = response.status >= 500
      ? `服务暂时不可用（HTTP ${response.status}），请稍后重试`
      : `请求未完成（HTTP ${response.status}）`;
    throw new Error(data.message || detail || fallback);
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
