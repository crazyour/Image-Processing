(() => {
  "use strict";

  const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"]);
  const TEXT_INPUT_TYPES = new Set(["", "text", "search", "url", "tel", "email", "password", "number"]);
  const TARGET_MAX_AGE_MS = 5 * 60 * 1000;
  let lastTarget = null;
  let lastTargetAt = 0;
  let hoverTarget = null;
  let dragAnchor = null;
  let toastTimer = 0;

  function isFileInput(input) {
    return input instanceof HTMLInputElement && input.type === "file" && !input.disabled;
  }

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

  function inputFromElement(element, predicate = isImageInput) {
    if (!(element instanceof Element)) return null;
    const redirect = element.closest("[data-paste-for]");
    if (redirect) {
      const id = redirect.dataset.pasteFor;
      const input = id ? document.getElementById(id) : null;
      if (predicate(input)) return input;
    }
    const direct = element.closest('input[type="file"]');
    if (predicate(direct)) return direct;
    let container = element;
    while (container && container !== document.body) {
      const inputs = Array.from(container.querySelectorAll('input[type="file"]')).filter(predicate);
      if (inputs.length === 1) return inputs[0];
      container = container.parentElement;
    }
    return null;
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

  function isFileDrag(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
  }

  function acceptsFile(input, file) {
    const rules = (input.accept || "").split(",").map((rule) => rule.trim().toLowerCase()).filter(Boolean);
    if (!rules.length) return true;
    const name = (file.name || "").toLowerCase();
    const type = (file.type || "").toLowerCase();
    return rules.some((rule) => {
      const value = rule.split(";")[0].trim();
      if (value.startsWith(".")) return name.endsWith(value);
      if (value.endsWith("/*")) return type.startsWith(value.slice(0, -1));
      return type === value;
    });
  }

  function setRelativePath(file, relativePath) {
    try {
      Object.defineProperty(file, "webkitRelativePath", { configurable: true, value: relativePath });
    } catch (_) {
      // Older browsers may expose a non-configurable empty property. The file
      // is still uploadable; only its nested folder display is unavailable.
    }
    return file;
  }

  function readFileEntry(entry, relativePath) {
    return new Promise((resolve, reject) => {
      entry.file((file) => resolve(setRelativePath(file, `${relativePath}${entry.name}`)), reject);
    });
  }

  async function readDirectoryEntries(reader) {
    const entries = [];
    while (true) {
      const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) return entries;
      entries.push(...batch);
    }
  }

  async function filesFromEntry(entry, relativePath = "") {
    if (entry.isFile) return [await readFileEntry(entry, relativePath)];
    if (!entry.isDirectory) return [];
    const prefix = `${relativePath}${entry.name}/`;
    const children = await readDirectoryEntries(entry.createReader());
    const nested = await Promise.all(children.map((child) => filesFromEntry(child, prefix)));
    return nested.flat();
  }

  async function droppedFiles(input, dataTransfer) {
    const acceptsDirectory = input.hasAttribute("webkitdirectory") || input.hasAttribute("directory");
    if (!acceptsDirectory) return Array.from(dataTransfer.files || []);
    const entries = Array.from(dataTransfer.items || [])
      .filter((item) => item.kind === "file")
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.some((entry) => entry.isDirectory)) return Array.from(dataTransfer.files || []);
    return (await Promise.all(entries.map((entry) => filesFromEntry(entry)))).flat();
  }

  function clearDragAnchor() {
    if (dragAnchor) delete dragAnchor.dataset.fileDropActive;
    dragAnchor = null;
  }

  function showDragAnchor(input) {
    const anchor = targetAnchor(input);
    if (anchor === dragAnchor) return;
    clearDragAnchor();
    dragAnchor = anchor;
    dragAnchor.dataset.fileDropActive = "true";
  }

  function assignDroppedFiles(input, files) {
    const transfer = new DataTransfer();
    const accepted = files.filter((file) => acceptsFile(input, file));
    const selected = input.multiple ? accepted : accepted.slice(0, 1);
    selected.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    lastTarget = isImageInput(input) ? input : lastTarget;
    lastTargetAt = Date.now();
    return { accepted: selected.length, ignored: files.length - selected.length };
  }

  const dragStyle = document.createElement("style");
  dragStyle.textContent = `
    [data-file-drop-active="true"] {
      outline: 3px solid #2f6b57 !important;
      outline-offset: 3px !important;
      box-shadow: 0 0 0 7px rgba(47, 107, 87, .16) !important;
      cursor: copy !important;
    }
  `;
  document.head.appendChild(dragStyle);

  document.addEventListener("pointerover", rememberHoverTarget, true);
  document.addEventListener("focusin", rememberTarget, true);
  document.addEventListener("click", rememberTarget, true);
  document.addEventListener("change", rememberTarget, true);
  document.addEventListener("dragover", (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    const input = inputFromElement(event.target, isFileInput);
    if (event.dataTransfer) event.dataTransfer.dropEffect = input ? "copy" : "none";
    if (input) showDragAnchor(input);
    else clearDragAnchor();
  }, true);
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) clearDragAnchor();
  }, true);
  document.addEventListener("dragend", clearDragAnchor, true);
  document.addEventListener("drop", async (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    const input = inputFromElement(event.target, isFileInput);
    clearDragAnchor();
    if (!input) {
      showToast("请将文件拖到具体的上传区域", "notice");
      return;
    }
    event.stopImmediatePropagation();
    try {
      const files = await droppedFiles(input, event.dataTransfer);
      const result = assignDroppedFiles(input, files);
      if (!result.accepted) {
        showToast("拖入的文件格式不受此上传区支持", "error");
      } else if (result.ignored) {
        showToast(`已拖入 ${result.accepted} 个文件，忽略 ${result.ignored} 个不支持或超出数量的文件`, "notice");
      } else {
        showToast(`已拖入 ${result.accepted} 个文件到“${targetLabel(input)}”`);
      }
    } catch (error) {
      console.error("File drop upload failed", error);
      showToast("读取拖入文件失败，请重试或使用文件选择器", "error");
    }
  }, true);
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
