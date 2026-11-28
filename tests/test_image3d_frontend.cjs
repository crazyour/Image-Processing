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
    setStepState(selector, text) { $(selector).textContent = text; }, clearError() {}, notify() {},
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
        const data = JSON.parse(options.body);
        const requested = allViews.filter(view => data.views.includes(view));
        for (const view of requested) {
          if (next.views[view]) next.views[view].prompt_revision = 0;
        }
        for (const view of data.single_step ? requested.slice(0, 1) : requested) {
          next.views[view] = { prompt_revision: next.prompt_revision, view_mode: next.view_mode || "faithful", url: view };
        }
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

test("complete but unchecked views allow confirmation and archive with an optional review", async () => {
  const { context, $, calls } = harness();
  context.syncControls();
  assert.equal($("#generate-views").disabled, true);
  assert.equal($("#review-views").disabled, false);
  assert.equal($("#confirm-views").disabled, false);
  assert.equal($("#save-view-folder").disabled, false);
  assert.match($("#view-review-summary").textContent, /可以直接确认/);
  assert.doesNotMatch($("#view-review-summary").textContent, /检查通过/);
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 1);
  assert.ok(calls[0].path.endsWith("/views/review"));
  assert.equal($("#confirm-views").disabled, false);
});

test("failed review remains visible but allows confirmation, archive, and confirmed 3D submission", () => {
  const state = completedState();
  state.view_review = { status: "failed", failed_views: ["right"] };
  const { context, $, calls } = harness(state);
  context.syncControls();
  assert.equal($("#confirm-views").disabled, false);
  assert.equal($("#save-view-folder").disabled, false);
  assert.equal($("#generate-3d").disabled, true);
  assert.equal($("#generate-views").disabled, false);
  assert.match($("#view-review-summary").textContent, /右视图.*仅供参考/);
  assert.doesNotMatch($("#view-review-summary").textContent, /检查通过/);
  assert.equal($("#views-state").textContent, "等待确认（有提示）");
  state.status = "VIEWS_CONFIRMED";
  state.confirmed_at = 1;
  context.syncControls();
  assert.equal($("#generate-3d").disabled, false);
  assert.equal($("#views-state").textContent, "已确认");
  assert.equal(state.view_review.status, "failed");
  assert.equal(calls.length, 0);
});

test("missing or outdated views remain blocked even with a previous approval", () => {
  for (const change of [
    state => { delete state.views.back; },
    state => { delete state.views.back.url; },
    state => { state.views.back.prompt_revision = 0; },
    state => { state.views.back.view_mode = "print_sculpture"; },
  ]) {
    const state = completedState();
    state.views_approved = true;
    state.confirmed_at = 1;
    change(state);
    const { context, $ } = harness(state);
    context.syncControls();
    assert.equal($("#confirm-views").disabled, true);
    assert.equal($("#save-view-folder").disabled, true);
    state.status = "VIEWS_CONFIRMED";
    context.syncControls();
    assert.equal($("#generate-3d").disabled, true);
  }
});

test("active view generation or review keeps confirmation and archive disabled after reload", () => {
  for (const status of ["GENERATING_VIEWS", "REVIEWING_VIEWS"]) {
    const state = completedState();
    state.status = status;
    state.views_approved = true;
    const { context, $ } = harness(state);
    context.syncControls();
    assert.equal($("#confirm-views").disabled, true, status);
    assert.equal($("#save-view-folder").disabled, true, status);
  }
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

test("first generation requests four serial steps and never automatically regenerates a failed review", async () => {
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
  assert.equal(calls.length, 5);
  for (let index = 0; index < allViews.length; index += 1) {
    assert.deepEqual(JSON.parse(calls[index].options.body), { views: allViews.slice(index), single_step: true });
  }
  assert.ok(calls[4].path.endsWith("/views/review"));
  assert.equal($("#confirm-views").disabled, false);
  assert.equal($("#save-view-folder").disabled, false);
  assert.equal(context.state.view_review.status, "failed");
  assert.equal($("#view-review-summary").focused, true);
});

test("later angles wait until the preceding generation request resolves", async () => {
  const state = completedState();
  state.views = {};
  state.status = "DESCRIPTION_CONFIRMED";
  const { context, $, calls } = harness(state);
  const api = context.api;
  let releaseFirst;
  const firstComplete = new Promise(resolve => { releaseFirst = resolve; });
  context.api = async (...args) => {
    const next = await api(...args);
    if (calls.length === 1) await firstComplete;
    return next;
  };
  const running = context.generateViews($("#generate-views"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(context.actionInFlight, true);
  releaseFirst();
  await running;
  assert.equal(calls.length, 5);
});

test("interrupted regeneration keeps completed work and resumes only missing angles", async () => {
  const { context, $, calls, errors } = harness();
  const api = context.api;
  context.api = async (path, options) => {
    if (path.endsWith("/views") && JSON.parse(options.body).views[0] === "left") {
      calls.push({ path, options });
      throw Error("left generation failed");
    }
    return api(path, options);
  };
  await context.generateViews($("#regenerate-views"), true);
  assert.equal(calls.length, 2);
  assert.equal(errors.length, 1);
  assert.equal(context.actionInFlight, false);
  assert.equal(context.state.views.front.prompt_revision, context.state.prompt_revision);
  for (const view of ["left", "right", "back"]) assert.equal(context.state.views[view].prompt_revision, 0);
  assert.equal($("#confirm-views").disabled, true);
  assert.equal($("#review-views").disabled, true);
  context.api = api;
  calls.length = 0;
  await context.generateViews($("#generate-views"));
  assert.equal(calls.length, 4);
  assert.deepEqual(JSON.parse(calls[0].options.body).views, ["left", "right", "back"]);
  assert.ok(calls[3].path.endsWith("/views/review"));
});

test("review outage preserves images and allows confirmation alongside review-only retry", async () => {
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
  assert.equal($("#confirm-views").disabled, false);
  assert.equal($("#save-view-folder").disabled, false);
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
  assert.equal(calls.length, 7); // four serial generation requests + existing batch review
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
