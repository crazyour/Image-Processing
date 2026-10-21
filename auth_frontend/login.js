import {api, showMessage} from "/auth-static/common.js";

const form = document.querySelector("#login-form");
const message = document.querySelector("#message");
const setup = document.querySelector("#setup");

api("/api/auth/status").then(data => {
  if (data.setup_required) showMessage(setup, "系统尚未初始化管理员。请由部署人员配置 ADMIN_USERNAME 和 ADMIN_PASSWORD 后重新启动。", true);
}).catch(() => {});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  const values = new FormData(form);
  try {
    await api("/api/auth/login", {method: "POST", body: JSON.stringify({username: values.get("username"), password: values.get("password")})});
    const requested = new URLSearchParams(location.search).get("next");
    location.href = requested && requested.startsWith("/") && !requested.startsWith("//") ? requested : "/";
  } catch (error) {
    showMessage(message, error.message, true);
  } finally {
    button.disabled = false;
  }
});