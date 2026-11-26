// Exercise the actual workflow functions without a browser or paid provider.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../image3d_frontend/app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "workflow functions must exist");
  return source.slice(a, b);
}
const workflow = [
  section("function selectedViewMode()", "function setStepState("),
  section("async function reviewCurrentViews(", '$("#confirm-description").addEventListener'),
  section("async function refine(", "async function replaceUploadedView("),
].join("\n");
const allViews = ["front", "left", "right", "back"];
function completedState() {
  return {
    status: "VIEWS_REVIEW", view_source: "generated", prompt_revision: 1,
    description_confirmed_at: 1, user_prompt: "雕塑", views_approved: false,
    views: Object.fromEntries(allViews.map(view => [view, { prompt_revision: 1, url: view }])),
  };
}
function harness(state = completedState()) {
  const elements = new Map(), calls = [], errors = [];
  function $(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      value: "雕塑", dataset: {}, textContent: "", disabled: false,
      classList: { toggle() {} }, focus() { this.focused = true; },
      scrollIntoView() {},
    });
    return elements.get(selector);
  }
  const context = {
    state, allViews, labels: { front: "正视图", left: "左视图", right: "右视图", back: "背视图" },
    session: { id: "test-session" }, actionInFlight: false, $, document: { querySelectorAll: () => [] },
    setStepState() {}, clearError() {}, notify() {},
    setActionInFlight(value) { context.actionInFlight = value; context.syncControls(); },
    setBusy(button, busy) { button.disabled = busy; },
    render(next) { context.state = next; $("#view-mode").value = next?.view_mode || "faithful"; context.syncControls(); },
    showError(error) { errors.push(error); }, refresh: async () => {},
    api: async (path, options) => {
      calls.push({ path, options });
      const next = structuredClone(context.state);
      if (path.endsWith("/prompt")) {
        const data = JSON.parse(options.body);
        next.user_prompt = data.prompt;
        next.view_mode = data.view_mode || next.view_mode || "faithful";
        next.prompt_revision += 1;
        next.status = "PROMPT_REVIEW";
        next.description_confirmed_at = null;
        next.confirmed_at = null;
        next.views_approved = false;
        next.view_review = null;
      } else if (path.endsWith("/description/confirm")) {
        next.status = "DESCRIPTION_CONFIRMED";
        next.description_confirmed_at = 1;
      } else if (path.endsWith("/views")) {
        for (const view of JSON.parse(options.body).views) next.views[view] = { prompt_revision: next.prompt_revision, url: view };
        next.status = "VIEWS_REVIEW";
        next.view_review = null;
        next.views_approved = false;
      } else if (path.endsWith("/review")) {
        next.view_review = { status: "passed", failed_views: [] };
        next.views_approved = true;
      }
      return next;
    },
  };
  vm.createContext(context);
  vm.runInContext(workflow, context);
  $("#view-mode").value = state?.view_mode || "faithful";
  return { context, $, calls, errors };
}

test("missing state cannot enable confirmation or archive", () => {
  const { context, $ } = harness(null);
  context.syncControls();
  assert.equal($("#confirm-views").disabled, true);
  assert.equal($("#save-view-folder").disabled, true);
  assert.equal($("#review-views").disabled, true);
});

test("complete but unchecked views offer review, not another paid generation", async () => {
  const { context, $, calls } = harness();
  context.syncControls();
  assert.equal($("#generate-views").disabled, true);
  assert.equal($("#review-views").disabled, false);
  assert.equal($("#confirm-views").disabled, true);
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 1);
  assert.ok(calls[0].path.endsWith("/views/review"));
  assert.equal($("#confirm-views").disabled, false);
});

test("retry only requests the failed angle, followed by one batch check", async () => {
  const state = completedState();
  state.view_review = { status: "failed", failed_views: ["right"] };
  const { context, $, calls } = harness(state);
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[0].options.body).views, ["right"]);
  assert.ok(calls[1].path.endsWith("/views/review"));
  assert.equal(context.actionInFlight, false);
});

test("a single-card retry does not reroll other failed views", async () => {
  const state = completedState();
  state.view_review = { status: "failed", failed_views: ["left", "right"] };
  const { context, $, calls } = harness(state);
  await context.generateViews($("retry-left"), false, ["left"]);
  assert.deepEqual(JSON.parse(calls[0].options.body).views, ["left"]);
  assert.equal(calls.length, 2);
});

test("first generation requests four images and never automatically regenerates a failed review", async () => {
  const state = completedState();
  state.views = {};
  state.status = "DESCRIPTION_CONFIRMED";
  const { context, $, calls } = harness(state);
  const api = context.api;
  context.api = async (...args) => {
    const next = await api(...args);
    next.status = "VIEWS_REVIEW";
    if (args[0].endsWith("/review")) {
      next.view_review = { status: "failed", failed_views: ["right"] };
      next.views_approved = false;
    }
    return next;
  };
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[0].options.body).views, allViews);
  assert.equal($("#confirm-views").disabled, true);
  assert.equal($("#view-review-summary").focused, true);
});

test("review outage preserves images and exposes review-only retry", async () => {
  const { context, $, calls, errors } = harness();
  const originalViews = structuredClone(context.state.views);
  context.api = async (path, options) => { calls.push({ path, options }); throw Error("review unavailable"); };
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 1);
  assert.deepEqual(context.state.views, originalViews);
  assert.equal(errors.length, 1);
  assert.equal(context.actionInFlight, false);
  context.syncControls();
  assert.equal($("#review-views").disabled, false);
});

test("busy state suppresses duplicate paid generation calls", async () => {
  const { context, $, calls } = harness();
  context.actionInFlight = true;
  await context.generateViews($("#generate-views"), true);
  await context.refine("left", $("refine-left"));
  assert.equal(calls.length, 0);
});

test("unsaved sculpture selection blocks old views and makes no paid call", async () => {
  const state = completedState();
  state.views_approved = true;
  state.confirmed_at = 1;
  const { context, $, calls } = harness(state);
  $("#view-mode").value = "print_sculpture";
  context.syncControls();
  for (const id of ["generate-views", "regenerate-views", "review-views", "confirm-views", "save-view-folder", "generate-3d"]) {
    assert.equal($(`#${id}`).disabled, true, id);
  }
  assert.equal($("#confirm-description").disabled, false);
  await context.generateViews($("#generate-views"));
  await context.refine("left", $("refine-left"));
  assert.equal(calls.length, 0);
});

test("applying sculpture mode confirms description but does not generate images", async () => {
  const { context, $, calls, errors } = harness();
  $("#view-mode").value = "print_sculpture";
  await context.confirmDescription($("#confirm-description"));
  assert.equal(errors.length, 0);
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[0].options.body), { prompt: "雕塑", view_mode: "print_sculpture" });
  assert.ok(calls[1].path.endsWith("/description/confirm"));
  assert.equal(context.state.view_mode, "print_sculpture");
  assert.equal(context.state.prompt_revision, 2);
  assert.equal($("#confirm-views").disabled, true);
  await context.generateViews($("#generate-views"));
  assert.deepEqual(JSON.parse(calls[2].options.body).views, allViews);
  assert.equal(calls.length, 4); // one four-view generation request + existing batch review
});

test("reverting unsaved mode selection preserves approval without requests", () => {
  const state = completedState();
  state.views_approved = true;
  const { context, $, calls } = harness(state);
  $("#view-mode").value = "print_sculpture";
  context.syncControls();
  $("#view-mode").value = "faithful";
  context.syncControls();
  assert.equal($("#confirm-views").disabled, false);
  assert.equal(calls.length, 0);
});

test("view mode is locked for uploaded views and active or completed 3D tasks", () => {
  for (const status of ["GENERATING_VIEWS", "REVIEWING_VIEWS", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"]) {
    const state = completedState();
    state.status = status;
    const { context, $ } = harness(state);
    context.syncControls();
    assert.equal($("#view-mode").disabled, true, status);
  }
  const state = completedState();
  state.view_source = "uploaded";
  const { context, $ } = harness(state);
  context.syncControls();
  assert.equal($("#view-mode").disabled, true);
});

test("failed mode save does not trigger generation or lose saved mode", async () => {
  const { context, $, calls, errors } = harness();
  $("#view-mode").value = "print_sculpture";
  context.api = async path => { calls.push(path); throw Error("save failed"); };
  await context.confirmDescription($("#confirm-description"));
  assert.equal(calls.length, 1);
  assert.equal(errors.length, 1);
  assert.equal(context.state.view_mode || "faithful", "faithful");
  assert.equal(context.actionInFlight, false);
});

const meshWorkflow = section("function renderMeshQuality()", "function formatBytes(");
function meshHarness(status = "pending") {
  const elements = new Map(), calls = [], timers = new Map(), errors = [];
  let timerId = 0;
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: "", innerHTML: "", disabled: false,
      classList: { toggle() {} }, focus() { this.focused = true; },
    });
    return elements.get(selector);
  };
  const context = {
    $, session: { id: "mesh-session" }, meshQualityInFlight: false, meshQualityTimer: null,
    attemptedMeshChecks: new Set(), Date, Number,
    state: { status: "SUCCEEDED", model_task: { models: { stl: {
      url: "/local-blobs/original.stl", quality: { status, stats: {}, issues: [], repairable: status === "issues" },
    } } } },
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    setStepState(selector, title) { $(selector).textContent = title; },
    escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"); },
    notify() {}, showError(error) { errors.push(error); },
    async refresh() { context.renderMeshQuality(); },
    render(next) { context.state = next; context.renderMeshQuality(); },
    async api(path) {
      calls.push(path);
      const next = structuredClone(context.state);
      next.model_task.models.stl.quality.status = "issues";
      return next;
    },
  };
  vm.createContext(context);
  vm.runInContext(meshWorkflow, context);
  return { context, $, calls, timers, errors };
}

test("STL auto-check survives repeated rendering and runs only once", async () => {
  const { context, timers, calls } = meshHarness();
  context.renderMeshQuality();
  context.renderMeshQuality();
  assert.equal(timers.size, 1);
  await [...timers.values()][0]();
  assert.equal(calls.length, 1);
  assert.ok(calls[0].endsWith("/3d/check"));
  context.renderMeshQuality();
  assert.equal(timers.size, 0);
});

test("STL check never automatically repairs or rerolls a model", async () => {
  const { context, $, calls, timers } = meshHarness("issues");
  context.renderMeshQuality();
  assert.equal(timers.size, 0);
  assert.equal($("#repair-stl").disabled, false);
  assert.equal($("#check-stl").disabled, true);
  assert.equal(calls.length, 0);
  await context.runMeshQuality(true);
  assert.ok(calls[0].endsWith("/3d/repair"));
});

test("incomplete report offers check retry without calling a generator", () => {
  const { context, $, timers } = meshHarness("incomplete");
  context.renderMeshQuality();
  assert.equal($("#mesh-quality-state").textContent, "检查未完成");
  assert.equal($("#check-stl").disabled, false);
  assert.equal($("#repair-stl").disabled, true);
  assert.equal(timers.size, 0);
});

test("STL network failure allows manual retry without a retry loop", async () => {
  const { context, $, timers, errors } = meshHarness();
  context.api = async () => { throw Error("offline"); };
  await context.runMeshQuality();
  assert.equal(errors.length, 1);
  assert.equal(context.meshQualityInFlight, false);
  assert.equal($("#check-stl").disabled, false);
  assert.equal(timers.size, 0);
});

test("STL job from another tab is shown as busy and becomes retryable after expiry", () => {
  const { context, $, timers } = meshHarness("incomplete");
  const model = context.state.model_task.models.stl;
  model.quality_job = { started_at: Date.now() / 1000 };
  context.renderMeshQuality();
  assert.equal($("#check-stl").disabled, true);
  assert.equal($("#mesh-quality-state").textContent, "处理中");
  assert.equal(timers.size, 1);
  model.quality_job.started_at -= 181;
  context.renderMeshQuality();
  assert.equal($("#check-stl").disabled, false);
  assert.equal(timers.size, 0);
});

test("repair report preserves unresolved issues and escapes report content", () => {
  const { context, $ } = meshHarness("issues");
  const model = context.state.model_task.models.stl;
  model.repaired = { url: "/local-blobs/repaired.stl", removed_duplicate_faces: 1,
    removed_degenerate_faces: 0, flipped_faces: 0, report: { status: "issues", issues: ["开放边界：3", "<img onerror=bad>"] } };
  context.renderMeshQuality();
  assert.equal(model.url, "/local-blobs/original.stl");
  assert.equal($("#repair-stl").disabled, true);
  assert.match($("#mesh-repair-result").innerHTML, /开放边界/);
  assert.match($("#mesh-repair-result").innerHTML, /&lt;img/);
  assert.doesNotMatch($("#mesh-repair-result").innerHTML, /基础检查通过/);
});

test("STL duplicate clicks and stale session responses are ignored", async () => {
  const { context, calls } = meshHarness();
  let resolve;
  const original = structuredClone(context.state);
  context.api = path => { calls.push(path); return new Promise(done => { resolve = done; }); };
  const first = context.runMeshQuality();
  await context.runMeshQuality(true);
  assert.equal(calls.length, 1);
  context.session = { id: "another-session" };
  context.state = null;
  resolve(original);
  await first;
  assert.equal(context.state, null);
  assert.equal(context.meshQualityInFlight, false);
});
