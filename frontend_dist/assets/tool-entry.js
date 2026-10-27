(() => {
  const tools = {
    "/tools/creative": { page: "DESIGN", title: "创意工作室", paidNotice: "AI 方案规划、图片生成和质量检查可能调用模型并产生费用。保存要求、查看历史和下载不收费；请在正式调用前确认页面显示的调用范围。" },
    "/tools/photo-craft": { page: "PHOTO_CRAFT", title: "照片定制工艺品", paidNotice: "将照片转换为工艺设计以及 AI 修复会调用模型并产生费用。本地检查、查看历史和下载不收费。" },
    "/tools/products": { page: "PRODUCTS", title: "产品中心", paidNotice: "产品和版本管理免费；只有在本页发起 AI 修改、配色或生成新图时才会产生费用。发起前请确认页面显示的调用范围。" },
    "/tools/color-finish": { page: "COLOR_FINISH", title: "配色与工艺", paidNotice: "AI 配色和效果图生成会按方案调用图片模型并产生费用。保存工艺记录、本地整理和下载不收费。" },
    "/tools/visual": { page: "SCENE", title: "视觉工作室" },
    "/tools/svg": { page: "SVG_STUDIO", title: "SVG 工作室" },
    "/tools/dxf": { page: "DXF_STUDIO", title: "图片转 DXF", paidNotice: "使用 AI 清稿会调用图片模型并产生费用，调用前会显示范围并要求确认。上传图片、本地轮廓平滑和 DXF 导出免费，不会自动扣费。" },
    "/tools/files": { page: "LIBRARY", title: "文件中心" },
    "/tools/delivery": { page: "DELIVERY", title: "交付中心" },
  };

  const tool = tools[window.location.pathname.replace(/\/$/, "")];
  if (!tool) return;

  document.documentElement.dataset.standaloneTool = "true";
  if (tool.paidNotice) {
    document.documentElement.dataset.paidFeatureBanner = "true";
    document.addEventListener("DOMContentLoaded", () => {
      const message = document.querySelector("#paid-feature-message");
      if (message) message.textContent = tool.paidNotice;
    }, { once: true });
  }
  document.title = `${tool.title} · 灵界`;
  const expectedHash = `#/center/${tool.page}`;
  if (window.location.hash !== expectedHash) {
    history.replaceState(null, "", `${window.location.pathname}${expectedHash}`);
  }

  const privateConfigurationLabels = ["API设置", "连接我的AI", "AI连接中心", "模型设置", "填写密钥"];
  const removePrivateConfiguration = () => {
    document.querySelectorAll("button, a, summary, label").forEach((element) => {
      const label = (element.textContent || "").replace(/\s+/g, "");
      if (privateConfigurationLabels.some((value) => label.includes(value))) {
        const target = element.closest("section, details, form, li") || element;
        target.remove();
      }
    });
  };
  new MutationObserver(removePrivateConfiguration).observe(document.documentElement, { childList: true, subtree: true });
  removePrivateConfiguration();
})();
