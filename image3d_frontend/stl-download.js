/* Read the original bytes from storage; never export the sampled preview mesh. */
const StlDownload = (() => {
  const MAX_BYTES = 250_000_000;
  const CHUNK_BYTES = 8 * 1024 * 1024;

  async function readBody(response, expected, onProgress) {
    const limit = expected || MAX_BYTES;
    const reader = response.body.getReader();
    const output = expected ? new Uint8Array(expected) : null;
    const chunks = [];
    let received = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (received + value.byteLength > limit) throw new Error("STL 下载大小与原文件不一致，请刷新后重试");
        if (output) output.set(value, received);
        else chunks.push(value);
        received += value.byteLength;
        onProgress(received);
      }
      if (!received || (expected && received !== expected)) {
        throw new Error(`STL 下载不完整（收到 ${received} / ${expected || "未知"} 字节），请重试`);
      }
      if (output) return output;
      const combined = new Uint8Array(received);
      let offset = 0;
      for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.byteLength; }
      return combined;
    } finally {
      // Cancel an oversized or failed response instead of leaving it downloading.
      try { await reader.cancel(); } catch (_) { /* The connection may already be closed. */ }
      reader.releaseLock();
    }
  }

  async function fetchModel(model, onProgress = () => {}) {
    const size = Number(model.size_bytes) || 0;
    if (!Number.isSafeInteger(size) || size < 0 || size > MAX_BYTES) throw new Error("STL 文件大小无效或超过 250 MB");
    const digest = model.sha256 || "";
    if (digest && (!/^[a-f\d]{64}$/i.test(digest) || !globalThis.crypto?.subtle)) {
      throw new Error("无法校验 STL，请使用支持安全下载的浏览器并通过 HTTPS 打开页面");
    }
    const ranged = size > CHUNK_BYTES;
    let output = null;
    let offset = 0;
    do {
      const end = ranged ? Math.min(offset + CHUNK_BYTES, size) - 1 : 0;
      let completed = false;
      for (let attempt = 0; attempt < 3 && !completed; attempt += 1) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 60_000);
        try {
          const response = await fetch(model.url, {
            cache: "no-store", signal: controller.signal,
            headers: ranged ? { Range: `bytes=${offset}-${end}` } : {},
          });
          if (response.status !== 200 && response.status !== 206) {
            await response.body?.cancel();
            throw new Error(`STL 下载失败（HTTP ${response.status}），请重试`);
          }
          if (response.status === 206) {
            const range = response.headers.get("Content-Range");
            const expectedRange = `bytes ${offset}-${ranged ? end : size - 1}/${size}`;
            // Cross-origin stores may hide Content-Range. The final hash must
            // then authenticate the assembled byte sequence before it is used.
            if (!size || (range ? range !== expectedRange : !digest)) {
              await response.body?.cancel();
              throw new Error("STL 下载分段与原文件不一致，请重试");
            }
            const chunk = await readBody(response, ranged ? end - offset + 1 : size,
              received => onProgress(offset + received, size, "downloading"));
            if (ranged) {
              if (!output) output = new Uint8Array(size);
              output.set(chunk, offset);
            }
            else output = chunk;
            offset += chunk.byteLength;
          } else {
            // A server may ignore Range and send the entire file. Validate and
            // use that full response once; never append it to earlier chunks.
            output = await readBody(response, size, received => onProgress(received, size, "downloading"));
            offset = output.byteLength;
          }
          completed = true;
        } catch (error) {
          if (attempt === 2) throw error;
          onProgress(offset, size, "retrying");
        } finally {
          clearTimeout(timer);
        }
      }
    } while (offset < size);
    onProgress(output.byteLength, size, "verifying");
    if (digest) {
      const hash = await crypto.subtle.digest("SHA-256", output);
      const actual = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
      if (actual !== digest.toLowerCase()) throw new Error("STL 完整性校验失败，文件已停止保存，请刷新后重试下载");
    }
    return output.buffer;
  }

  // Preview, resizing and the download button share one verified original.
  // Rejected reads are evicted so a later click can retry.
  function createLoader() {
    let entry = null;
    return function load(model, onProgress) {
      const key = JSON.stringify([model.url, model.size_bytes, model.sha256]);
      if (entry?.key !== key) {
        const current = { key, listeners: new Set(), progress: null };
        entry = current;
        current.promise = fetchModel(model, (...progress) => {
          current.progress = progress;
          current.listeners.forEach(listener => listener(...progress));
        }).catch(error => {
          if (entry === current) entry = null;
          throw error;
        });
      }
      const current = entry;
      if (onProgress) {
        current.listeners.add(onProgress);
        if (current.progress) onProgress(...current.progress);
      }
      return current.promise.finally(() => current.listeners.delete(onProgress));
    };
  }

  return { fetchModel, createLoader };
})();
