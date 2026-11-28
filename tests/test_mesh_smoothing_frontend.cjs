const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "image3d_frontend/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "image3d_frontend/index.html"), "utf8");
function section(start, end) {
  const begin = source.indexOf(start);
  const finish = source.indexOf(end, begin);
  assert.ok(begin >= 0 && finish > begin, `Missing section ${start}`);
  return source.slice(begin, finish);
}
const workflow = section("function renderMeshQuality()", "function parseStl(");

function harness() {
  const elements = new Map(), calls = [], errors = [], timers = new Map(), resizeCalls = [];
  let timerId = 0;
  const $ = selector => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        textContent: "", innerHTML: "", value: "", checked: false, disabled: false, attributes: {},
        classList: {
          add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
          toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
          contains(name) { return classes.has(name); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() { this.focused = true; },
      });
    }
    return elements.get(selector);
  };
  const context = {
    $, session: { id: "session-a" },
    state: { session_id: "session-a", status: "SUCCEEDED", model_task: { models: { stl: {
      url: "/original.stl", sha256: "original-sha", size_bytes: 1000,
      quality: { status: "passed", stats: {}, issues: [] },
    } } } },
    meshQualityInFlight: false, meshQualityTimer: null, attemptedMeshChecks: new Set(),
    meshSmoothingRequest: null, meshSmoothingError: null,
    stlPreviewSourceKey: null, stlPreviewVariant: "original", stlPreviewRequestId: 0,
    loadedPreviewUrl: null, stlPreview: null, generatedStlUrl: "/original.stl",
    loadedResizeUrl: null, resizeSourceKind: null,
    Date, Number, Math, JSON,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"); },
    setStepState(selector, title) { $(selector).textContent = title; },
    notify() {}, showError(error) { errors.push(error); },
    async refresh() { context.renderMeshQuality(); },
    render(next) { context.state = next; context.renderMeshQuality(); context.renderStlPreviewSource(); },
    async api(url, options) { calls.push({ url, options }); return structuredClone(context.state); },
    async fetch(url) { return { ok: true, async arrayBuffer() { return { url }; } }; },
    parseStl(buffer) { return [buffer.url]; },
    setResizeMesh(...args) { resizeCalls.push(args); },
    drawStlPreview() {},
  };
  $("#smoothing-strength").value = "gentle";
  vm.createContext(context);
  vm.runInContext(workflow, context);
  return { context, $, calls, errors, timers, resizeCalls };
}

function savedCopy(model) {
  model.smoothed = { url: "/smooth.stl", source_sha256: model.sha256, strength: "gentle", subdivide: false, size_bytes: 1200 };
  return model.smoothed;
}

test("smoothing is explicit, defaults to gentle, and makes no paid generation call", async () => {
  const { context, $, calls } = harness();
  context.renderMeshQuality();
  context.renderMeshQuality();
  assert.equal(calls.length, 0);
  assert.equal($("#smooth-stl").disabled, false);
  await context.runMeshSmoothing();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/sessions/session-a/3d/smooth");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.timeout, 240_000);
  assert.deepEqual(JSON.parse(calls[0].options.body), { strength: "gentle", subdivide: false });
  assert.match(html, /value="gentle" selected/);
  assert.match(html, /id="smoothing-subdivide"[^>]*disabled/);
  assert.doesNotMatch(html, /id="smoothing-subdivide"[^>]*checked/);
  assert.match(html, /id="mesh-smoothing-summary"[^>]*aria-live="polite"/);
  assert.match(source, /\$\("#smooth-stl"\)\.addEventListener\("click", runMeshSmoothing\)/);
});

test("double clicks and concurrent check/repair are blocked; selected payload is captured", async () => {
  const { context, $, calls } = harness();
  let resolve;
  context.api = (url, options) => {
    calls.push({ url, options });
    return new Promise(done => { resolve = done; });
  };
  $("#smoothing-strength").value = "standard";
  $("#smoothing-subdivide").checked = true;
  const first = context.runMeshSmoothing();
  await context.runMeshSmoothing();
  await context.runMeshQuality(true);
  assert.equal(calls.length, 1);
  assert.equal($("#smooth-stl").disabled, true);
  assert.equal($("#smoothing-strength").disabled, true);
  assert.equal($("#smoothing-subdivide").disabled, true);
  assert.deepEqual(JSON.parse(calls[0].options.body), { strength: "standard", subdivide: true });
  resolve(structuredClone(context.state));
  await first;
  assert.equal(context.meshQualityInFlight, false);
  assert.equal($("#smooth-stl").disabled, false);
});

test("server jobs share the lock, with a longer expiry for smoothing", async () => {
  const { context, $, calls, timers } = harness();
  const model = context.state.model_task.models.stl;
  model.quality_job = { operation: "smooth", started_at: Date.now() / 1000 - 300 };
  context.renderMeshQuality();
  assert.equal($("#smooth-stl").disabled, true);
  assert.equal($("#mesh-smoothing-state").textContent, "正在平滑");
  assert.equal(timers.size, 1);
  await context.runMeshSmoothing();
  await context.runMeshQuality(true);
  assert.equal(calls.length, 0);
  model.quality_job.started_at -= 61;
  context.renderMeshQuality();
  assert.equal($("#smooth-stl").disabled, false);
  model.quality_job = { operation: "repair", started_at: Date.now() / 1000 };
  context.renderMeshQuality();
  assert.equal($("#mesh-smoothing-state").textContent, "等待其他处理");
  await context.runMeshSmoothing();
  assert.equal(calls.length, 0);
  model.quality_job.started_at -= 181;
  context.renderMeshQuality();
  assert.equal($("#smooth-stl").disabled, false);
});

test("request failure keeps existing copy, reads saved state once, and does not retry", async () => {
  const { context, $, calls, errors, timers } = harness();
  const model = context.state.model_task.models.stl;
  savedCopy(model);
  context.api = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") throw new Error("处理超时。");
    return structuredClone(context.state);
  };
  await context.runMeshSmoothing();
  assert.deepEqual(calls.map(call => call.url), ["/api/sessions/session-a/3d/smooth", "/api/sessions/session-a"]);
  assert.equal(errors.length, 1);
  assert.equal(timers.size, 0);
  assert.equal(context.state.model_task.models.stl.url, "/original.stl");
  assert.match($("#mesh-smoothing-result").innerHTML, /\/smooth.stl/);
  assert.match($("#mesh-smoothing-summary").textContent, /不会自动重试/);
  assert.equal($("#smooth-stl").disabled, false);
});

test("rejected reports preserve the successful copy and escape limitations", () => {
  const { context, $ } = harness();
  const model = context.state.model_task.models.stl;
  savedCopy(model);
  model.smoothing = { status: "rejected", message: "尺寸变化超出限制。", limitations: ["<img onerror=bad>"],
    stats: { faces_before: 100, faces_after: 400, moved_vertices: 22, max_displacement: 0.02, p95_displacement: 0.01, dimension_change_ratio: 0.003 } };
  context.renderMeshSmoothing();
  assert.match($("#mesh-smoothing-summary").textContent, /上次成功/);
  assert.match($("#mesh-smoothing-result").innerHTML, /\/smooth.stl/);
  assert.match($("#mesh-smoothing-stats").innerHTML, /0.300%/);
  assert.match($("#mesh-smoothing-limitations").innerHTML, /&lt;img/);
  assert.doesNotMatch($("#mesh-smoothing-limitations").innerHTML, /<img/);
});

test("a saved result recovered after a timeout supersedes the network error", async () => {
  const { context, $, calls } = harness();
  context.api = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") throw new Error("请求超时。");
    const next = structuredClone(context.state);
    savedCopy(next.model_task.models.stl);
    next.model_task.models.stl.smoothing = { status: "smoothed", message: "平滑副本已保存。" };
    return next;
  };
  await context.runMeshSmoothing();
  assert.equal(calls.length, 2);
  assert.equal($("#mesh-smoothing-state").textContent, "平滑副本已保存");
  assert.equal($("#mesh-smoothing-summary").textContent, "平滑副本已保存。");
  assert.match($("#mesh-smoothing-result").innerHTML, /\/smooth.stl/);
});

test("a stale session or changed source cannot receive a smoothing response", async () => {
  for (const changeSource of [false, true]) {
    const { context, calls } = harness();
    let resolve;
    const old = structuredClone(context.state);
    savedCopy(old.model_task.models.stl);
    context.api = (url, options) => { calls.push({ url, options }); return new Promise(done => { resolve = done; }); };
    const request = context.runMeshSmoothing();
    if (changeSource) context.state.model_task.models.stl.url = "/new-source.stl";
    else { context.session = { id: "session-b" }; context.state = null; }
    resolve(old);
    await request;
    assert.equal(calls.length, 1);
    assert.equal(context.meshQualityInFlight, false);
    if (changeSource) assert.equal(context.state.model_task.models.stl.url, "/new-source.stl");
    else assert.equal(context.state, null);
  }
});

test("preview selection survives rendering, resets for a new source, and rejects stale copies", () => {
  const { context, $ } = harness();
  const model = context.state.model_task.models.stl;
  savedCopy(model);
  const loads = [];
  context.loadStlPreview = url => { loads.push(url); context.loadedPreviewUrl = url; };
  context.renderStlPreviewSource();
  context.selectStlPreview("smoothed");
  context.renderStlPreviewSource();
  assert.deepEqual(loads, ["/original.stl", "/smooth.stl"]);
  assert.equal(context.stlPreviewVariant, "smoothed");
  assert.equal($("#preview-smoothed-stl").attributes["aria-pressed"], "true");
  model.url = "/another.stl";
  model.sha256 = "another-sha";
  context.renderStlPreviewSource();
  assert.equal(context.stlPreviewVariant, "original");
  assert.equal($("#preview-smoothed-stl").disabled, true);
  assert.equal(loads.at(-1), "/another.stl");
  context.selectStlPreview("smoothed");
  assert.equal(context.stlPreviewVariant, "original");
});

test("a copy from a repaired mesh is matched to its original and its source is labeled", () => {
  const { context, $ } = harness();
  const model = context.state.model_task.models.stl;
  const copy = savedCopy(model);
  copy.source_sha256 = "repaired-sha";
  copy.original_sha256 = model.sha256;
  copy.source_kind = "repaired";
  assert.equal(context.currentSmoothedStl(), copy);
  context.renderMeshSmoothing();
  assert.match($("#mesh-smoothing-result").innerHTML, /来源：修复版/);
  model.sha256 = "changed-original";
  assert.equal(context.currentSmoothedStl(), null);
});

test("late preview responses cannot replace the selected model and camera pose is retained", async () => {
  const { context, $, resizeCalls } = harness();
  context.stlPreviewSourceKey = "session-a:/original.stl";
  context.stlPreview = { rotationX: 1.2, rotationY: 2.3, zoom: 1.4 };
  const completions = new Map();
  context.fetch = url => new Promise(resolve => { completions.set(url, () => resolve({ ok: true, async arrayBuffer() { return { url }; } })); });
  const old = context.loadStlPreview("/original.stl");
  const latest = context.loadStlPreview("/smooth.stl");
  completions.get("/smooth.stl")();
  await latest;
  assert.equal(context.stlPreview.triangles[0], "/smooth.stl");
  assert.equal(context.stlPreview.rotationX, 1.2);
  assert.equal(context.stlPreview.rotationY, 2.3);
  assert.equal(context.stlPreview.zoom, 1.4);
  assert.equal(resizeCalls.length, 0);
  completions.get("/original.stl")();
  await old;
  assert.equal(context.stlPreview.triangles[0], "/smooth.stl");
  assert.equal(resizeCalls.length, 1);
  assert.equal(resizeCalls[0][0].url, "/original.stl");
  assert.match($("#model-preview-status").textContent, /简化显示/);
});

test("a preview from a previous session changes neither preview nor resize source", async () => {
  const { context, resizeCalls } = harness();
  context.stlPreviewSourceKey = "session-a:/original.stl";
  let resolve;
  context.fetch = () => new Promise(done => { resolve = done; });
  const pending = context.loadStlPreview("/original.stl");
  context.session = { id: "session-b" };
  context.state = null;
  context.renderStlPreviewSource();
  resolve({ ok: true, async arrayBuffer() { return { url: "/original.stl" }; } });
  await pending;
  assert.equal(context.stlPreview, null);
  assert.equal(resizeCalls.length, 0);
});

test("preview switching preserves a locally selected resize file", async () => {
  const { context, resizeCalls } = harness();
  context.resizeSourceKind = "local";
  context.stlPreviewSourceKey = "session-a:/original.stl";
  await context.loadStlPreview("/original.stl");
  await context.loadStlPreview("/smooth.stl");
  assert.equal(resizeCalls.length, 0);
});
