import {api, logout, showMessage} from "/auth-static/common.js";

const definitions = {
  workspace: {icon: "工", title: "主工作台", description: "进入灵界工艺设计工作台和云端兼容功能。", href: "/"},
  text_to_image: {icon: "文", title: "文生图", description: "输入多条图片要求，按队列生成并保存结果。", href: "/text-to-image"},
  animal_cutout: {icon: "镂", title: "照片转镂空剪影", description: "生成并检查单片黑白镂空剪影候选。", href: "/text-to-image?workflow=animal"},
  image_to_3d: {icon: "3D", title: "图生 3D", description: "从参考图生成四视图、拼装图和 STL。", href: "/image-to-3d"},
  similarity_search: {icon: "搜", title: "相似素材检索", description: "用图片、DXF 或 AI 文件查找相似素材。", href: "/similarity-search"},
  asset_library: {icon: "库", title: "成果素材库", description: "浏览和下载已经做好的共享成果。", href: "/file-upload"},
};

document.querySelector("#logout").addEventListener("click", logout);

try {
  const {user} = await api("/api/auth/me");
  document.querySelector("#identity").textContent = `${user.name} · ${user.role === "admin" ? "管理员" : user.username}`;
  document.querySelector("#welcome").textContent = `${user.name}，欢迎回来`;
  if (user.role === "admin") document.querySelector("#admin-link").classList.remove("hidden");
  const enabled = user.role === "admin" ? Object.keys(definitions) : user.features;
  const container = document.querySelector("#features");
  container.innerHTML = enabled.map(key => {
    const item = definitions[key];
    return item ? `<article class="feature-card"><div class="icon">${item.icon}</div><h2>${item.title}</h2><p>${item.description}</p><a href="${item.href}">打开功能 →</a></article>` : "";
  }).join("");
  if (!container.children.length) container.innerHTML = '<div class="panel empty">当前账号尚未开通业务功能，请联系管理员。</div>';
  if (new URLSearchParams(location.search).has("denied")) showMessage(document.querySelector("#message"), "当前账号没有访问该功能的权限。", true);
} catch (error) {
  showMessage(document.querySelector("#message"), error.message, true);
}
