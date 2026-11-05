(() => {
  "use strict";

  const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"]);
  const TEXT_INPUT_TYPES = new Set(["", "text", "search", "url", "tel", "email", "password", "number"]);
  const TARGET_MAX_AGE_MS = 5 * 60 * 1000;
  let lastTarget = null;
  let lastTargetAt = 0;
  let hoverTarget = null;
  let toastTimer = 0;

  function isImageInput(input) {
    if (!(input instanceof HTMLInputElement) || input.type !== "file" || input.disabled) return false;
    if (input.dataset.pasteImage === "true") return true;
    const accept = (input.accept || "").toLowerCase();
    if (!accept) return false;
    return accept.split(",").some((part) => {
      const value = part.trim().split(";")[0];
      return value === "image/*" || value.startsWith("image/") || IMAGE_EXTENSIONS.has(value);
    });
  }

  function isVisible(element) {
    if (!(element instanceof Element)) return false;
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  }

  function targetAnchor(input) {
    const selector = input.dataset.pasteAnchor;
    if (selector) {
      try {
        const anchor = document.querySelector(selector);
        if (anchor) return anchor;
      } catch (_) {
        // An invalid optional selector must not disable ordinary uploads.
      }
    }
    if (input.id) {
      const labels = Array.from(document.querySelectorAll("label[for]"));
      const label = labels.find((item) => item.htmlFor === input.id);
      if (label) return label;
    }
    const wrappingLabel = input.closest("label");
    if (wrappingLabel) return wrappingLabel;
    if (isVisible(input)) return input;
    let ancestor = input.parentElement;
    while (ancestor && ancestor !== document.body) {
      if (isVisible(ancestor)) return ancestor;
      ancestor = ancestor.parentElement;
    }
    return input;
  }

  function isAvailable(input) {
    return isImageInput(input) && input.isConnected && isVisible(targetAnchor(input));
  }

  function inputFromElement(element) {
    if (!(element instanceof Element)) return null;
    const redirect = element.closest("[data-paste-for]");
    if (redirect) {
      const id = redirect.dataset.pasteFor;
      const input = id ? document.getElementById(id) : null;
      if (isImageInput(input)) return input;
    }
    const direct = element.closest('input[type="file"]');
    if (isImageInput(direct)) return direct;
    const container = element.closest("label, [data-paste-target], .dropzone, .drop-zone, .file-drop, .reference-upload, .manual-views");
    if (!container) return null;
    const nested = container.querySelector('input[type="file"]');
    return isImageInput(nested) ? nested : null;
  }

  function rememberTarget(event) {
    const input = inputFromElement(event.target);
    if (!input) return;
    lastTarget = input;
    lastTargetAt = Date.now();
  }

  function rememberHoverTarget(event) {
    hoverTarget = inputFromElement(event.target);
  }

  function isEditingText() {
    const active = document.activeElement;
    if (!(active instanceof Element)) return false;
    if (active.closest('[contenteditable="true"], [contenteditable="plaintext-only"]')) return true;
    if (active instanceof HTMLTextAreaElement) return true;
    return active instanceof HTMLInputElement && active.type !== "file" && TEXT_INPUT_TYPES.has(active.type);
  }

  function visibleImageInputs() {
    return Array.from(document.querySelectorAll('input[type="file"]')).filter(isAvailable);
  }

  function chooseTarget() {
    if (hoverTarget && isAvailable(hoverTarget)) return hoverTarget;
    if (lastTarget && Date.now() - lastTargetAt <= TARGET_MAX_AGE_MS && isAvailable(lastTarget)) return lastTarget;
    const activeTarget = inputFromElement(document.activeElement);
    if (activeTarget && isAvailable(activeTarget)) return activeTarget;
    const inputs = visibleImageInputs();
    return inputs.length === 1 ? inputs[0] : null;
  }

  function clipboardImages(event) {
    const data = event.clipboardData;
    if (!data) return [];
    const fromItems = Array.from(data.items || [])
      .filter((item) => item.kind === "file" && item.type.toLowerCase().startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (fromItems.length) return fromItems;
    return Array.from(data.files || []).filter((file) => (file.type || "").toLowerCase().startsWith("image/"));
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function timestamp() {
    const now = new Date();
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  }

  function extensionFor(type) {
    return {
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/gif": "gif",
      "image/bmp": "bmp",
      "image/tiff": "tiff",
      "image/avif": "avif",
    }[type] || "png";
  }

  function namedClipboardFile(file, index, total) {
    const type = (file.type || "image/png").toLowerCase();
    const suffix = total > 1 ? `-${index + 1}` : "";
    return new File([file], `clipboard-${timestamp()}${suffix}.${extensionFor(type)}`, {
      type,
      lastModified: Date.now(),
    });
  }

  function targetLabel(input) {
    if (input.dataset.pasteLabel) return input.dataset.pasteLabel;
    if (input.getAttribute("aria-label")) return input.getAttribute("aria-label");
    const anchor = targetAnchor(input);
    const text = (anchor?.innerText || "").replace(/\s+/g, " ").trim();
    return text ? text.slice(0, 24) : "图片上传区";
  }

  function showToast(message, kind = "success") {
    let toast = document.getElementById("paste-upload-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "paste-upload-toast";
      toast.setAttribute("role", "status");
      Object.assign(toast.style, {
        position: "fixed",
        zIndex: "2147483647",
        left: "50%",
        bottom: "28px",
        maxWidth: "min(520px, calc(100vw - 32px))",
        padding: "12px 18px",
        borderRadius: "12px",
        color: "#fff",
        font: "600 14px/1.45 system-ui, -apple-system, sans-serif",
        textAlign: "center",
        boxShadow: "0 10px 30px rgba(20, 50, 38, .24)",
        transform: "translateX(-50%)",
        transition: "opacity .18s ease",
        pointerEvents: "none",
      });
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.background = kind === "error" ? "#a94438" : kind === "notice" ? "#725a22" : "#245d49";
    toast.style.opacity = "1";
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.style.opacity = "0"; }, 3200);
  }

  function assignFiles(input, pastedFiles) {
    const transfer = new DataTransfer();
    const files = pastedFiles.map((file, index) => namedClipboardFile(file, index, pastedFiles.length));
    const selected = input.multiple ? [...Array.from(input.files || []), ...files] : files.slice(0, 1);
    selected.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    lastTarget = input;
    lastTargetAt = Date.now();
    return input.multiple ? files.length : Math.min(files.length, 1);
  }

  document.addEventListener("pointerover", rememberHoverTarget, true);
  document.addEventListener("focusin", rememberTarget, true);
  document.addEventListener("click", rememberTarget, true);
  document.addEventListener("change", rememberTarget, true);
  document.addEventListener("paste", (event) => {
    const images = clipboardImages(event);
    if (!images.length || (isEditingText() && !isAvailable(hoverTarget))) return;
    const target = chooseTarget();
    if (!target) {
      const count = visibleImageInputs().length;
      if (count > 1) showToast("请先点击或将鼠标移到要上传图片的区域，再粘贴截图", "notice");
      return;
    }
    event.preventDefault();
    try {
      const count = assignFiles(target, images);
      showToast(`已粘贴 ${count} 张截图到“${targetLabel(target)}”`);
    } catch (error) {
      console.error("Clipboard image upload failed", error);
      showToast("截图粘贴失败，请重试或使用文件选择器", "error");
    }
  }, true);
})();
