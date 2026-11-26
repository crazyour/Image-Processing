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
  section("function syncControls()", "function setStepState("),
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
    });
    return elements.get(selector);
  }
  const context = {
    state, allViews, labels: { front: "正视图", left: "左视图", right: "右视图", back: "背视图" },
    session: { id: "test-session" }, actionInFlight: false, $, document: { querySelectorAll: () => [] },
    setStepState() {}, clearError() {}, notify() {},
    setActionInFlight(value) { context.actionInFlight = value; context.syncControls(); },
    setBusy(button, busy) { button.disabled = busy; },
    render(next) { context.state = next; context.syncControls(); },
    showError(error) { errors.push(error); }, refresh: async () => {},
    api: async (path, options) => {
      calls.push({ path, options });
      const next = structuredClone(context.state);
      if (path.endsWith("/views")) {
        for (const view of JSON.parse(options.body).views) next.views[view] = { prompt_revision: 1, url: view };
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
