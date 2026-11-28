const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "image3d_frontend/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "image3d_frontend/index.html"), "utf8");
function section(start, end) {
  const begin = source.indexOf(start), finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, `Missing section ${start}`);
  return source.slice(begin, finish);
}
const workflow = section("function formatBytes(", "function parseStl(")
  + section("async function loadResizeStlFromUrl(", "function targetDimensions(");

function completed(model) {
  model.processing = { status: "completed", stage: "done", version: "stl-auto-v2", source_sha256: model.sha256 };
  model.processed = { url: "/final.stl", source_sha256: model.sha256, sha256: "final-sha", size_bytes: 1200, version: "stl-auto-v2" };
  delete model.quality_job;
  return model.processed;
}

function harness() {
  const elements = new Map(), calls = [], timers = new Map(), resizeCalls = [], fetches = [];
  let timerId = 0;
  const $ = selector => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        textContent: "", innerHTML: "", disabled: false, attributes: {},
        classList: {
          add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
          toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
          contains(name) { return classes.has(name); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
      });
    }
    return elements.get(selector);
  };
  const context = {
    $, session: { id: "session-a" },
    state: { status: "SUCCEEDED", session_id: "session-a", model_task: { models: { stl: {
      url: "/original.stl", sha256: "original-sha", size_bytes: 1000,
    } } } },
    modelProcessingTimer: null, modelProcessingRequest: null, modelProcessingPoll: null,
    modelProcessingError: null, attemptedModelProcessing: new Set(),
    stlPreviewSourceKey: null, stlPreviewRequestId: 0, loadedPreviewUrl: null, stlPreview: null, stlPreviewVariant: "processed",
    generatedStlUrl: null, loadedResizeUrl: null, loadingResizeRequest: null, resizeSourceKind: null, resizeMesh: null, resizeRequestId: 0,
    Date, Number, Math, JSON,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"); },
    setStepState(selector, title) { $(selector).textContent = title; }, notify() {},
    render(next) { context.state = next; context.renderModelProcessing(); },
    async api(url, options) {
      calls.push({ url, options });
      const next = structuredClone(context.state);
      if (options?.method === "POST") completed(next.model_task.models.stl);
      return next;
    },
    async fetch(url) { fetches.push(url); return { ok: true, async arrayBuffer() { return { url }; } }; },
    parseStl(buffer) { return [buffer.url]; },
    setResizeMesh(...args) { resizeCalls.push(args); context.resizeSourceKind = args[3]; },
    drawStlPreview() {},
  };
  vm.createContext(context);
  vm.runInContext(workflow, context);
  async function nextTimer() {
    assert.ok(timers.size, "expected a scheduled action");
    const [id, timer] = timers.entries().next().value;
    timers.delete(id);
    await timer.fn();
  }
  return { context, $, calls, timers, resizeCalls, fetches, nextTimer };
}

test("the fourth step retains both STL versions and collapsed optional resizing", () => {
  assert.doesNotMatch(html, /id="(?:mesh-quality-step|check-stl|repair-stl|smooth-stl|preview-smoothed-stl|smoothing-strength)"/);
  assert.doesNotMatch(html, /<span>0[56]<\/span>/);
  assert.match(html, /id="model-processing-summary"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(html, /<details id="resize-step"[^>]*>\s*<summary>调整尺寸（可选）/);
  assert.doesNotMatch(html, /<details id="resize-step"[^>]*open/);
  assert.match(source, /\$\("#retry-model-processing"\)\.addEventListener\("click", runModelProcessing\)/);
  assert.doesNotMatch(source, /3d\/(?:check|repair|smooth)[`"']/);
  assert.match(html, /id="preview-original-stl"[^>]*aria-pressed="false"/);
  assert.match(html, /id="preview-processed-stl"[^>]*aria-pressed="false"[^>]*disabled/);
});

test("historical completed models process once while retaining the original download", async () => {
  const { context, $, calls, timers, fetches, nextTimer } = harness();
  context.renderModelProcessing();
  context.renderModelProcessing();
  assert.equal(timers.size, 1);
  assert.match($("#model-results").innerHTML, /href="\/original.stl" download="model-original.stl"/);
  assert.doesNotMatch($("#model-results").innerHTML, /修复后 STL/);
  assert.equal($("#model-preview").classList.contains("hidden"), false);
  assert.equal($("#use-generated-stl").disabled, false);
  assert.equal(context.generatedStlUrl, "/original.stl");
  assert.deepEqual(fetches, ["/original.stl"]);
  await nextTimer();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/sessions/session-a/3d/process");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, undefined);
  assert.ok(calls[0].options.timeout >= 300_000);
  assert.equal(context.generatedStlUrl, "/final.stl");
  assert.match($("#model-results").innerHTML, /\/final.stl/);
  assert.match($("#model-results").innerHTML, /\/original.stl/);
  assert.equal(($("#model-results").innerHTML.match(/<a /g) || []).length, 2);
  assert.equal($("#model-processing-summary").textContent, "已完成保形修复与表面处理。");
  assert.equal(timers.size, 0);
});

test("repair and adaptive smoothing pending stages continue serially", async () => {
  const { context, calls, timers, nextTimer } = harness();
  const steps = ["preserve", "smooth", "smooth_cautious", "smooth_minimal", "done"];
  context.api = async (url, options) => {
    calls.push({ url, options });
    const next = structuredClone(context.state);
    const stage = steps[calls.length - 1];
    if (stage === "done") completed(next.model_task.models.stl);
    else next.model_task.models.stl.processing = { status: "pending", stage, version: "stl-auto-v2" };
    return next;
  };
  context.renderModelProcessing();
  for (let index = 0; index < steps.length; index++) {
    assert.equal(timers.size, 1);
    await nextTimer();
    assert.equal(calls.length, index + 1);
  }
  assert.equal(context.currentProcessedStl().url, "/final.stl");
  assert.equal(timers.size, 0);
  assert.ok(calls.every(call => call.url.endsWith("/3d/process")));
});

test("repeated pending same-stage responses cannot cause an automatic POST loop", async () => {
  const { context, $, calls, timers, nextTimer } = harness();
  context.currentStlModel().processing = { status: "pending", stage: "smooth", version: "stl-auto-v2" };
  context.api = async (url, options) => { calls.push({ url, options }); return structuredClone(context.state); };
  context.renderModelProcessing();
  await nextTimer();
  context.renderModelProcessing();
  assert.equal(calls.length, 1);
  assert.equal(timers.size, 0);
  assert.equal($("#retry-model-processing").disabled, false);
});

test("a busy computation slot retries the same stage only after the server backoff", async () => {
  const { context, $, calls, timers, nextTimer } = harness();
  let now = 100_000;
  context.Date = { now: () => now };
  context.currentStlModel().processing = { status: "pending", stage: "repair", version: "stl-auto-v2" };
  context.api = async (url, options) => {
    calls.push({ url, options });
    const next = structuredClone(context.state);
    if (calls.length === 1) next.model_task.models.stl.processing.retry_after = 105;
    else completed(next.model_task.models.stl);
    return next;
  };
  await context.runModelProcessing();
  assert.equal(calls.length, 1);
  assert.equal(timers.size, 1);
  assert.equal(timers.values().next().value.delay, 5000);
  assert.equal($("#model-processing-summary").textContent, "正在等待继续处理…");
  assert.equal($("#retry-model-processing").disabled, true);
  await context.runModelProcessing();
  assert.equal(calls.length, 1);
  await nextTimer(); // An early timer cannot bypass the timestamp.
  assert.equal(calls.length, 1);
  now = 105_000;
  await nextTimer();
  assert.equal(calls.length, 2);
  assert.equal(context.currentProcessedStl().url, "/final.stl");
  assert.equal(timers.size, 0);
  assert.ok(calls.every(call => call.url.endsWith("/3d/process")));
});

test("one backoff timestamp cannot authorize endless retries after an unchanged response", async () => {
  const { context, $, calls, timers, nextTimer } = harness();
  context.Date = { now: () => 105_000 };
  context.currentStlModel().processing = { status: "pending", stage: "repair", version: "stl-auto-v2", retry_after: 105 };
  context.api = async (url, options) => { calls.push({ url, options }); return structuredClone(context.state); };
  context.renderModelProcessing();
  await nextTimer();
  assert.equal(calls.length, 1);
  assert.equal(timers.size, 0);
  assert.equal($("#retry-model-processing").disabled, false);
  context.currentStlModel().processing.retry_after = 110;
  context.renderModelProcessing();
  assert.equal(timers.values().next().value.delay, 5000);
});

test("changing session, source, or stage invalidates a scheduled backoff retry", async () => {
  for (const change of [
    context => { context.session = { id: "session-b" }; },
    context => { context.currentStlModel().url = "/new-source.stl"; },
    context => { context.currentStlModel().sha256 = "new-source-sha"; },
    context => { context.currentStlModel().processing.stage = "smooth"; },
  ]) {
    const { context, calls, timers } = harness();
    context.Date = { now: () => 100_000 };
    context.currentStlModel().processing = { status: "pending", stage: "repair", version: "stl-auto-v2", retry_after: 105 };
    context.renderModelProcessing();
    const previousTimer = context.modelProcessingTimer;
    const callback = timers.get(previousTimer).fn;
    change(context);
    context.renderModelProcessing();
    assert.equal(timers.has(previousTimer), false);
    context.Date = { now: () => 105_000 };
    await callback();
    assert.equal(calls.length, 0);
  }
});

test("a new hash at the same source URL does not reuse the previous stage attempt", async () => {
  const { context, calls, timers } = harness();
  const model = context.currentStlModel();
  model.processing = { status: "pending", stage: "repair", version: "stl-auto-v2" };
  context.attemptedModelProcessing.add(context.modelProcessingAttemptKey());
  context.renderModelProcessing();
  assert.equal(timers.size, 0);
  model.sha256 = "replacement-source-sha";
  context.renderModelProcessing();
  assert.equal(timers.size, 1);
  await context.runModelProcessing();
  assert.equal(calls.length, 1);
  assert.equal(context.currentProcessedStl().source_sha256, "replacement-source-sha");
});

test("double clicks are suppressed while processing and status polling never posts", async () => {
  const { context, $, calls, timers, nextTimer } = harness();
  let finish;
  context.api = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") return new Promise(resolve => { finish = resolve; });
    const next = structuredClone(context.state);
    next.model_task.models.stl.processing = { status: "running", stage: "smooth", version: "stl-auto-v2" };
    next.model_task.models.stl.quality_job = { operation: "process", started_at: Date.now() / 1000 };
    return next;
  };
  const running = context.runModelProcessing();
  await context.runModelProcessing();
  assert.equal(calls.length, 1);
  assert.equal($("#model-processing-summary").textContent, "正在修复结构…");
  assert.equal(timers.values().next().value.delay, 5000);
  await nextTimer();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options, undefined);
  assert.equal($("#model-processing-summary").textContent, "正在优化表面…");
  const next = structuredClone(context.state);
  completed(next.model_task.models.stl);
  finish(next);
  await running;
  assert.equal(context.modelProcessingRequest, null);
  assert.equal(timers.size, 0);
});

test("running server jobs are resumed with GET only and expiry offers retry", async () => {
  for (const operation of ["process", "smooth"]) {
    const { context, $, calls, timers, nextTimer } = harness();
    const model = context.currentStlModel();
    model.processing = { status: "running", stage: "smooth", version: "stl-auto-v2" };
    model.quality_job = { operation, started_at: Date.now() / 1000 - 300 };
    context.renderModelProcessing();
    await context.runModelProcessing();
    assert.equal(calls.length, 0);
    assert.equal($("#retry-model-processing").disabled, true);
    await nextTimer();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options, undefined);
    context.currentStlModel().quality_job.started_at -= 61;
    context.renderModelProcessing();
    assert.equal($("#retry-model-processing").disabled, false);
    assert.equal(timers.size, 0);
    assert.match($("#model-processing-summary").textContent, /处理中断|处理已中断/);
  }
});

test("explicit failure remains honest and does not schedule an automatic retry", async () => {
  const { context, $, calls, timers } = harness();
  const model = context.currentStlModel();
  model.processing = { status: "failed", stage: "smooth", version: "stl-auto-v2", message: "<img onerror=bad> 无法完成" };
  model.repaired = { url: "/repaired.stl" };
  model.smoothed = { url: "/old-smooth.stl", source_sha256: model.sha256 };
  model.previous_processed = { url: "/old-processed.stl", source_sha256: model.sha256 };
  context.renderModelProcessing();
  assert.equal(timers.size, 0);
  assert.match($("#model-results").innerHTML, /\/original.stl/);
  assert.doesNotMatch($("#model-results").innerHTML, /old-|\/repaired.stl/);
  assert.equal($("#model-processing-summary").textContent, "暂未完成：<img onerror=bad> 无法完成");
  assert.equal($("#retry-model-processing").disabled, false);
  await context.runModelProcessing();
  assert.equal(calls.length, 1);
  assert.equal(context.currentProcessedStl().url, "/final.stl");
});

test("adaptive smoothing stages share one status and residual structure issues stay visible", () => {
  const { context, $ } = harness();
  const model = context.currentStlModel();
  model.quality_job = { operation: "process", started_at: Date.now() / 1000 };
  for (const stage of ["smooth", "smooth_cautious", "smooth_minimal"]) {
    model.processing = { status: "running", stage, version: "stl-auto-v2" };
    context.renderModelProcessing();
    assert.equal($("#model-processing-summary").textContent, "正在优化表面…");
  }
  completed(model).needs_review = true;
  context.renderModelProcessing();
  assert.equal($("#model-processing-summary").textContent, "已保形处理，仍有结构问题。请核对后使用。");
  assert.equal($("#generate-state").textContent, "已处理 · 待核对");
  assert.match($("#model-results").innerHTML, /\/final.stl/);
});

test("network failure reads saved state once without resubmission", async () => {
  const { context, $, calls, timers } = harness();
  context.api = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") throw Error("请求超时");
    return structuredClone(context.state);
  };
  await context.runModelProcessing();
  assert.deepEqual(calls.map(call => call.url), ["/api/sessions/session-a/3d/process", "/api/sessions/session-a"]);
  assert.equal(timers.size, 0);
  assert.equal($("#retry-model-processing").disabled, false);
  assert.match($("#model-results").innerHTML, /\/original.stl/);
  assert.doesNotMatch($("#model-results").innerHTML, /修复后 STL/);
});

test("a completed result recovered after a timeout supersedes the request error", async () => {
  const { context, $, calls, timers } = harness();
  context.api = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") throw Error("请求超时");
    const next = structuredClone(context.state);
    completed(next.model_task.models.stl);
    return next;
  };
  await context.runModelProcessing();
  assert.equal(calls.length, 2);
  assert.equal(timers.size, 0);
  assert.equal($("#model-processing-summary").textContent, "已完成保形修复与表面处理。");
  assert.match($("#model-results").innerHTML, /\/final.stl/);
});

test("late polling cannot overwrite the completed POST response", async () => {
  const { context } = harness();
  let finishPost, finishGet;
  const stale = structuredClone(context.state);
  context.api = (url, options) => new Promise(resolve => {
    if (options?.method === "POST") finishPost = resolve;
    else finishGet = resolve;
  });
  const running = context.runModelProcessing();
  const polling = context.pollModelProcessing();
  const final = structuredClone(stale);
  completed(final.model_task.models.stl);
  finishPost(final);
  await running;
  finishGet(stale);
  await polling;
  assert.equal(context.currentProcessedStl().url, "/final.stl");
});

test("stale responses cannot replace another session or source", async () => {
  for (const changeSource of [false, true]) {
    const { context } = harness();
    let finish;
    const previous = structuredClone(context.state);
    completed(previous.model_task.models.stl);
    context.api = () => new Promise(resolve => { finish = resolve; });
    const running = context.runModelProcessing();
    if (changeSource) context.currentStlModel().url = "/new-original.stl";
    else { context.session = { id: "session-b" }; context.state = null; }
    finish(previous);
    await running;
    assert.equal(context.modelProcessingRequest, null);
    if (changeSource) assert.equal(context.currentStlModel().url, "/new-original.stl");
    else assert.equal(context.state, null);
  }
});

test("invalid repaired copies never replace the original download, preview or resize source", () => {
  for (const invalidate of [
    model => { model.processing.status = "pending"; },
    model => { model.processed.source_sha256 = "another-source"; },
    model => { delete model.processed.source_sha256; },
    model => { delete model.sha256; },
    model => { model.processed.version = "older-pipeline"; },
    model => { delete model.processed.version; },
    model => { delete model.processing.version; },
  ]) {
    const { context, $, fetches } = harness();
    const model = context.currentStlModel();
    completed(model);
    invalidate(model);
    context.renderModelProcessing();
    assert.equal(context.currentProcessedStl(), null);
    assert.match($("#model-results").innerHTML, /\/original.stl/);
    assert.doesNotMatch($("#model-results").innerHTML, /\/final.stl/);
    assert.equal(context.generatedStlUrl, "/original.stl");
    assert.equal($("#model-preview").classList.contains("hidden"), false);
    assert.deepEqual(fetches, ["/original.stl"]);
  }
});

test("the repaired file is the default preview and resize source when available", async () => {
  const { context, fetches, resizeCalls } = harness();
  completed(context.currentStlModel());
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(fetches, ["/final.stl"]);
  assert.equal(resizeCalls.length, 1);
  assert.equal(resizeCalls[0][0].url, "/final.stl");
  assert.equal(resizeCalls[0][1], "修复后 STL");
  assert.equal(context.loadedResizeUrl, "/final.stl");
  await context.loadStlPreview("/original.stl");
  await context.loadResizeStlFromUrl("/original.stl", true);
  assert.deepEqual(fetches, ["/final.stl"]);
});

test("the default preview changes from original to repaired while preserving its camera", async () => {
  const { context, $, resizeCalls } = harness();
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(context.stlPreview.triangles, ["/original.stl"]);
  assert.equal($("#model-preview-title").textContent, "原始 STL");
  assert.equal($("#preview-processed-stl").disabled, true);
  assert.match($("#resize-source-hint").textContent, /原始 STL/);
  context.stlPreview.rotationX = 1.2;
  completed(context.currentStlModel());
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(context.stlPreview.triangles, ["/final.stl"]);
  assert.equal(context.stlPreview.rotationX, 1.2);
  assert.equal($("#model-preview-title").textContent, "修复后 STL");
  assert.equal($("#preview-processed-stl").attributes["aria-pressed"], "true");
  assert.equal(resizeCalls.at(-1)[0].url, "/final.stl");
});

test("an explicit original preview survives processing and the size tool still defaults to repaired", async () => {
  const { context, $, fetches, resizeCalls } = harness();
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  context.selectStlPreview("original");
  let finishResize;
  context.fetch = async url => {
    fetches.push(url);
    return new Promise(resolve => { finishResize = resolve; });
  };
  completed(context.currentStlModel());
  context.renderModelProcessing();
  context.renderModelProcessing();
  assert.deepEqual(fetches, ["/original.stl", "/final.stl"], "repeated renders reuse the pending size download");
  assert.equal($("#model-preview-title").textContent, "原始 STL");
  assert.equal($("#preview-original-stl").attributes["aria-pressed"], "true");
  assert.deepEqual(context.stlPreview.triangles, ["/original.stl"]);
  finishResize({ ok: true, async arrayBuffer() { return { url: "/final.stl" }; } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(resizeCalls.at(-1)[0].url, "/final.stl");
  assert.equal(resizeCalls.at(-1)[1], "修复后 STL");
  assert.equal(context.generatedStlUrl, "/final.stl");
  context.fetch = async url => ({ ok: true, async arrayBuffer() { return { url }; } });
  context.selectStlPreview("processed");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(context.stlPreview.triangles, ["/final.stl"]);
});

test("late preview switches cannot replace the chosen version or its independent resize source", async () => {
  const { context, resizeCalls } = harness();
  completed(context.currentStlModel());
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  let finishOriginal;
  context.fetch = url => url === "/original.stl"
    ? new Promise(resolve => { finishOriginal = resolve; })
    : Promise.resolve({ ok: true, async arrayBuffer() { return { url }; } });
  context.selectStlPreview("original");
  context.selectStlPreview("processed");
  await new Promise(resolve => setImmediate(resolve));
  finishOriginal({ ok: true, async arrayBuffer() { return { url: "/original.stl" }; } });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(context.stlPreview.triangles, ["/final.stl"]);
  assert.equal(resizeCalls.length, 1);
  assert.equal(context.loadedResizeUrl, "/final.stl");
});

test("an invalidated historical repair stays hidden while its original is reprocessed", async () => {
  const { context, $, fetches, nextTimer } = harness();
  const model = context.currentStlModel();
  model.previous_processed = { url: "/unsafe-v1.stl", version: "stl-auto-v1", source_sha256: model.sha256 };
  model.processing = { status: "pending", stage: "repair", version: "stl-auto-v2" };
  context.renderModelProcessing();
  assert.match($("#model-results").innerHTML, /\/original.stl/);
  assert.doesNotMatch($("#model-results").innerHTML, /unsafe-v1/);
  assert.equal(context.currentProcessedStl(), null);
  await nextTimer();
  assert.match($("#model-results").innerHTML, /\/final.stl/);
  assert.doesNotMatch($("#model-results").innerHTML, /unsafe-v1/);
  assert.ok(!fetches.includes("/unsafe-v1.stl"));
});

test("a local resize source survives automatic final-file preview", async () => {
  const { context, resizeCalls } = harness();
  completed(context.currentStlModel());
  context.resizeSourceKind = "local";
  context.renderModelProcessing();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(resizeCalls.length, 0);
  assert.equal(context.resizeSourceKind, "local");
  await context.loadResizeStlFromUrl("/final.stl", true);
  assert.equal(resizeCalls.length, 1);
  assert.equal(resizeCalls[0][0].url, "/final.stl");
});

test("late preview responses cannot change another final model or its resize source", async () => {
  const { context, resizeCalls } = harness();
  completed(context.currentStlModel());
  let finish;
  context.fetch = () => new Promise(resolve => { finish = resolve; });
  context.stlPreviewSourceKey = "old-preview";
  const pending = context.loadStlPreview("/final.stl");
  context.state = null;
  context.renderStlPreviewSource();
  finish({ ok: true, async arrayBuffer() { return { url: "/final.stl" }; } });
  await pending;
  assert.equal(context.stlPreview, null);
  assert.equal(resizeCalls.length, 0);
});

test("late resize downloads cannot overwrite a new session or a selected local file", async () => {
  for (const local of [true, false]) {
    const { context, resizeCalls } = harness();
    completed(context.currentStlModel());
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const pending = context.loadResizeStlFromUrl("/final.stl", true);
    if (local) { context.resizeRequestId += 1; context.resizeSourceKind = "local"; }
    else { context.state = null; context.stlPreviewSourceKey = "new-session"; }
    finish({ ok: true, async arrayBuffer() { return { url: "/final.stl" }; } });
    await pending;
    assert.equal(resizeCalls.length, 0);
  }
});
