const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "image3d_frontend/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "image3d_frontend/index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "image3d_frontend/styles.css"), "utf8");
function section(start, end) {
  const begin = source.indexOf(start), finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, `Missing section ${start}`);
  return source.slice(begin, finish);
}
const workflow = section("function renderTask()", "function parseStl(")
  + section("async function loadResizeStlFromUrl(", "function targetDimensions(");
const settled = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const elements = new Map(), calls = [], fetches = [], resizeCalls = [], polls = [];
  const $ = selector => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        textContent: "", innerHTML: "", disabled: false, value: "", listeners: {},
        classList: {
          add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
          toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
          contains(name) { return classes.has(name); },
        },
        addEventListener(name, fn) { this.listeners[name] = fn; },
      });
    }
    return elements.get(selector);
  };
  const context = {
    $, session: { id: "session-a" },
    state: { status: "SUCCEEDED", session_id: "session-a", model_task: { provider: "hunyuan", models: { stl: {
      url: "/original.stl", sha256: "original-sha", size_bytes: 1000,
    } } } },
    stlPreviewSourceKey: null, stlPreviewRequestId: 0, loadedPreviewUrl: null, stlPreview: null,
    generatedStlUrl: null, loadedResizeUrl: null, loadingResizeRequest: null,
    resizeSourceKind: null, resizeMesh: null, resizeRequestId: 0,
    originalDownloadInFlight: false,
    taskClock: null, lastCompletedBalanceTaskId: null,
    escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"); },
    setStepState(selector, title) { $(selector).textContent = title; }, notify() {},
    render(next) { context.state = next; context.renderTask(); },
    async api(...args) { calls.push(args); return context.state; },
    async fetch(url) { fetches.push(url); return { ok: true, async arrayBuffer() { return { url }; } }; },
    async readOriginalStl(model) {
      const response = await context.fetch(model.url);
      if (!response.ok) throw Error("STL 下载失败");
      return response.arrayBuffer();
    },
    parseStl(buffer) { return [buffer.url]; },
    setResizeMesh(...args) { resizeCalls.push(args); context.resizeSourceKind = args[3]; },
    drawStlPreview() {}, updateElapsed() {},
    schedulePoll(delay) { polls.push(delay); }, stopPolling() { polls.push("stop"); },
    setInterval() { return "task-clock"; },
    setTimeout() { assert.fail("An STL result must not schedule another task"); },
    clearTimeout() {},
    loadMeshyBalance() {},
  };
  vm.createContext(context);
  vm.runInContext(workflow, context);
  return { context, $, calls, fetches, resizeCalls, polls };
}

test("the result UI has one original STL and no postprocessing controls, calls or recovery marker", () => {
  assert.doesNotMatch(html + css, /model-processing|stl-preview-switch|retry-model-processing|preview-processed-stl|修复后|自动修复|自动平滑/);
  assert.doesNotMatch(source, /3d\/(?:process|check|repair|smooth)|image3d-processing-resume|[Mm]odelProcessing|currentProcessedStl|\.processed\b/);
  assert.doesNotMatch(html, /<span>0[56]<\/span>/);
  assert.match(html, /<details id="resize-step"[^>]*>\s*<summary>调整尺寸（可选）/);
  assert.doesNotMatch(html, /<details id="resize-step"[^>]*open/);
  assert.match(html, /默认使用生成的原始 STL，也可选择本地文件/);
});

test("all historical processing states immediately expose only the original STL without API calls", async () => {
  for (const status of [null, "pending", "running", "completed", "failed"]) {
    const { context, $, calls, fetches, resizeCalls, polls } = harness();
    const model = context.currentStlModel();
    model.processing = status && { status, stage: "smooth", version: "stl-auto-v2", retry_after: Date.now() / 1000 + 5 };
    model.quality_job = { operation: "process", started_at: Date.now() / 1000 };
    model.processed = { url: "/processed.stl", source_sha256: model.sha256, version: "stl-auto-v2" };
    model.previous_processed = { url: "/old-v1.stl" };
    context.renderTask();
    context.renderTask();
    assert.match($("#model-results").innerHTML, /href="\/original.stl" download="model-original.stl"/);
    assert.equal(($("#model-results").innerHTML.match(/<a /g) || []).length, 1);
    assert.doesNotMatch($("#model-results").innerHTML, /processed|old-v1/);
    assert.equal($("#model-preview").classList.contains("hidden"), false);
    assert.equal($("#use-generated-stl").disabled, false);
    assert.equal(context.generatedStlUrl, "/original.stl");
    await settled();
    assert.deepEqual(fetches, ["/original.stl"]);
    assert.deepEqual(context.stlPreview.triangles, ["/original.stl"]);
    assert.equal(resizeCalls.length, 1);
    assert.equal(resizeCalls[0][1], "原始 STL");
    assert.deepEqual(calls, []);
    assert.deepEqual(polls, ["stop", "stop"]);
  }
});

test("pending generation still polls while missing or failed results cannot download", () => {
  for (const status of ["GENERATING_3D", "SUBMITTING_3D", "FAILED", null]) {
    const { context, $, fetches, polls } = harness();
    if (status) context.state.status = status;
    else context.state = null;
    context.renderTask();
    assert.equal($("#model-results").innerHTML, "");
    assert.equal($("#model-preview").classList.contains("hidden"), true);
    assert.equal($("#use-generated-stl").disabled, true);
    assert.deepEqual(fetches, []);
    assert.deepEqual(polls, [status?.includes("_3D") ? 8000 : "stop"]);
  }
  const { context, $ } = harness();
  delete context.currentStlModel().url;
  context.renderStlResult();
  assert.equal($("#model-results").innerHTML, "");
});

test("uppercase and legacy provider STL fields still use the original file", async () => {
  const { context, fetches } = harness();
  context.state.hunyuan_task = { models: { STL: context.currentStlModel() } };
  delete context.state.model_task;
  context.renderTask();
  await settled();
  assert.deepEqual(fetches, ["/original.stl"]);
  assert.equal(context.loadedResizeUrl, "/original.stl");
});

test("a selected local resize source survives source changes until explicitly replaced", async () => {
  const { context, resizeCalls } = harness();
  context.resizeSourceKind = "local";
  context.renderStlResult();
  await settled();
  context.currentStlModel().url = "/new-original.stl";
  context.renderStlResult();
  await settled();
  assert.equal(resizeCalls.length, 0);
  assert.equal(context.resizeSourceKind, "local");
  await context.loadResizeStlFromUrl("/new-original.stl", true);
  assert.equal(resizeCalls.length, 1);
  assert.equal(resizeCalls[0][0].url, "/new-original.stl");
});

test("late preview responses cannot replace another source or session", async () => {
  for (const change of [
    context => { context.state = null; },
    context => { context.currentStlModel().sha256 = "changed-hash-at-same-url"; },
    context => { context.session.id = "another-session"; },
  ]) {
    const { context, resizeCalls } = harness();
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const pending = context.loadStlPreview("/original.stl");
    const finishOld = finish;
    change(context);
    context.renderStlPreviewSource();
    finishOld({ ok: true, async arrayBuffer() { return { url: "/original.stl" }; } });
    await pending;
    assert.equal(context.stlPreview, null);
    assert.equal(resizeCalls.length, 0);
  }
});

test("late preview and resize downloads do not overwrite a newly selected local file", async () => {
  for (const load of ["loadStlPreview", "loadResizeStlFromUrl"]) {
    const { context, resizeCalls } = harness();
    let finish;
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const pending = context[load]("/original.stl", true);
    context.resizeRequestId += 1;
    context.resizeSourceKind = "local";
    finish({ ok: true, async arrayBuffer() { return { url: "/original.stl" }; } });
    await pending;
    assert.equal(resizeCalls.length, 0);
  }
});

test("explicit resize downloads are deduplicated and reject processed URLs", async () => {
  const { context, fetches, resizeCalls } = harness();
  let finish;
  context.fetch = url => { fetches.push(url); return new Promise(resolve => { finish = resolve; }); };
  const pending = context.loadResizeStlFromUrl("/original.stl", true);
  await context.loadResizeStlFromUrl("/original.stl", true);
  await context.loadResizeStlFromUrl("/processed.stl", true);
  await context.loadStlPreview("/processed.stl");
  assert.deepEqual(fetches, ["/original.stl"]);
  finish({ ok: true, async arrayBuffer() { return { url: "/original.stl" }; } });
  await pending;
  assert.equal(resizeCalls.length, 1);
});

test("preview failure leaves the original download available", async () => {
  const { context, $ } = harness();
  context.fetch = async () => { throw Error("offline"); };
  context.renderStlResult();
  await settled();
  assert.match($("#model-results").innerHTML, /\/original.stl/);
  assert.match($("#model-preview-status").textContent, /预览暂不可用/);
});

test("optional resize failure cannot suppress a verified original preview", async () => {
  const { context, $ } = harness();
  context.setResizeMesh = () => { throw Error("STL 文件不能超过 100 MB"); };
  context.renderStlResult();
  await settled();
  assert.equal($("#stl-canvas").classList.contains("hidden"), false);
  assert.deepEqual(context.stlPreview.triangles, ["/original.stl"]);
  assert.match($("#resize-source-status").textContent, /100 MB/);
});

test("the download button only saves verified original bytes, never preview triangles", async () => {
  const { context, $ } = harness();
  const buffer = new ArrayBuffer(284), saves = [];
  context.Blob = Blob;
  context.URL = { createObjectURL(blob) { saves.push(blob); return "blob:verified"; }, revokeObjectURL() {} };
  const link = { click() { saves.push(this.download); }, remove() {} };
  context.document = { createElement: () => link, body: { appendChild() {} } };
  context.setTimeout = () => {};
  context.readOriginalStl = async () => buffer;
  await context.downloadOriginalStl();
  assert.equal(saves[0].size, buffer.byteLength);
  assert.equal(saves[1], "model-original.stl");
  assert.equal(link.href, "blob:verified");
  assert.match($("#model-download-status").textContent, /已校验/);

  context.readOriginalStl = async () => { throw Error("STL 下载不完整"); };
  await context.downloadOriginalStl();
  assert.equal(saves.length, 2);
  assert.match($("#model-download-status").textContent, /下载不完整/);
  assert.equal(context.originalDownloadInFlight, false);
});

test("a download finishing after switching sessions does not save the old file", async () => {
  const { context } = harness();
  let finish;
  context.readOriginalStl = () => new Promise(resolve => { finish = resolve; });
  const pending = context.downloadOriginalStl();
  context.session.id = "new-session";
  finish(new ArrayBuffer(284));
  await pending;
  assert.equal(context.originalDownloadInFlight, false);
});

test("generation always submits good quality for either provider without displaying a quality option", async () => {
  assert.doesNotMatch(html + source, /generation-quality|quality-description|普通效果|好效果|所选效果|生成效果/);
  const handler = section('$("#generate-3d").addEventListener(', '$("#dismiss-error").addEventListener(');
  for (const provider of ["meshy", "hunyuan"]) {
    const { context, $, calls } = harness();
    Object.assign(context, { clearError() {}, setActionInFlight() {}, setBusy() {}, selectedModelProvider: () => provider, showError: assert.fail, refresh: assert.fail });
    vm.runInContext(handler, context);
    await $("#generate-3d").listeners.click({ currentTarget: $("#generate-3d") });
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/sessions/session-a/generate");
    assert.equal(JSON.parse(calls[0][1].body).quality, "good");
    assert.equal(JSON.parse(calls[0][1].body).model_provider, provider);
  }
});
