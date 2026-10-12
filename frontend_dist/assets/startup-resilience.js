(() => {
  "use strict";

  const nativeFetch = window.fetch.bind(window);
  const optionalReads = new Map([
    ["/api/assets", { key: "assets", fallback: [] }],
    ["/api/jobs", { key: "jobs", fallback: [] }],
    ["/api/masters", { key: "masters", fallback: [] }],
    ["/api/reviews", { key: "reviews", fallback: [] }],
    [
      "/api/budget",
      {
        key: "budget",
        fallback: {
          quota_source: "暂时不可用",
          limit_micros: null,
          held_micros: 0,
          spent_micros: 0,
          available_micros: 0,
          pools: [],
        },
      },
    ],
  ]);
  const cache = new Map();
  const failed = new Set();
  const softTimeoutMs = 8_000;
  let banner = null;

  function requestUrl(input) {
    try {
      return new URL(
        typeof input === "string" || input instanceof URL ? input : input.url,
        window.location.href,
      );
    } catch {
      return null;
    }
  }

  function optionalRead(input, init) {
    const method = String(init?.method || input?.method || "GET").toUpperCase();
    if (method !== "GET") return null;
    const url = requestUrl(input);
    if (!url || url.origin !== window.location.origin) return null;
    return optionalReads.get(url.pathname) || null;
  }

  function currentRequest(input, definition) {
    if (definition.key !== "assets") return input;
    const url = requestUrl(input);
    if (!url || url.searchParams.get("include_history") !== "true") return input;
    url.searchParams.set("include_history", "false");
    if (typeof input === "string") return `${url.pathname}${url.search}${url.hash}`;
    if (input instanceof URL) return url;
    return new Request(url, input);
  }

  function showPartialLoadNotice() {
    const attach = () => {
      if (banner?.isConnected || failed.size === 0) return;
      banner = document.createElement("div");
      banner.id = "workbench-partial-load-notice";
      banner.setAttribute("role", "status");
      banner.style.cssText =
        "position:fixed;right:20px;bottom:20px;z-index:2147483647;max-width:390px;" +
        "padding:12px 15px;border:1px solid #d5c48f;border-radius:12px;background:#fff9e8;" +
        "color:#4d4633;box-shadow:0 8px 28px rgba(37,52,45,.16);font:600 13px/1.55 system-ui";
      banner.textContent = "部分数据读取较慢，工作台已先打开；系统会在后台自动重试。";
      document.body.appendChild(banner);
    };
    if (document.body) attach();
    else document.addEventListener("DOMContentLoaded", attach, { once: true });
  }

  function markFailed(key) {
    failed.add(key);
    showPartialLoadNotice();
  }

  function markRecovered(key) {
    failed.delete(key);
    if (failed.size === 0 && banner) {
      banner.remove();
      banner = null;
    }
  }

  function fallbackResponse(definition) {
    const value = cache.has(definition.key)
      ? cache.get(definition.key)
      : definition.fallback;
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-Workbench-Partial-Load": "1",
      },
    });
  }

  window.fetch = function resilientFetch(input, init) {
    const definition = optionalRead(input, init);
    if (!definition) return nativeFetch(input, init);

    const request = nativeFetch(currentRequest(input, definition), init)
      .then(async (response) => {
        if (response.ok) {
          try {
            cache.set(definition.key, await response.clone().json());
            markRecovered(definition.key);
            return response;
          } catch {
            markFailed(definition.key);
            return fallbackResponse(definition);
          }
        }
        if (![408, 423, 429, 500, 502, 503, 504].includes(response.status)) {
          return response;
        }
        markFailed(definition.key);
        return fallbackResponse(definition);
      })
      .catch(() => {
        markFailed(definition.key);
        return fallbackResponse(definition);
      });

    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        markFailed(definition.key);
        resolve(fallbackResponse(definition));
      }, softTimeoutMs);
      request.then((response) => {
        window.clearTimeout(timer);
        resolve(response);
      });
    });
  };
})();
