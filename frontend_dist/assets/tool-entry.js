(() => {
  const tools = {
    "/tools/creative": { page: "DESIGN", title: "创意工作室" },
    "/tools/photo-craft": { page: "PHOTO_CRAFT", title: "照片定制工艺品" },
    "/tools/products": { page: "PRODUCTS", title: "产品中心" },
    "/tools/color-finish": { page: "COLOR_FINISH", title: "配色与工艺" },
    "/tools/visual": { page: "SCENE", title: "视觉工作室" },
    "/tools/svg": { page: "SVG_STUDIO", title: "SVG 工作室" },
    "/tools/dxf": { page: "DXF_STUDIO", title: "DXF 工程" },
    "/tools/files": { page: "LIBRARY", title: "文件中心" },
    "/tools/delivery": { page: "DELIVERY", title: "交付中心" },
  };

  const tool = tools[window.location.pathname.replace(/\/$/, "")];
  if (!tool) return;

  document.documentElement.dataset.standaloneTool = "true";
  document.title = `${tool.title} · 灵界`;
  const expectedHash = `#/center/${tool.page}`;
  if (window.location.hash !== expectedHash) {
    history.replaceState(null, "", `${window.location.pathname}${expectedHash}`);
  }
})();
