const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const { Painter, imagePoint, maskPixels } = require("../image3d_frontend/view-mask.js");

test("image coordinates stay aligned at different viewport sizes, offsets and aspect ratios", () => {
  assert.deepEqual(imagePoint({ clientX: 260, clientY: 148 }, { left: 10, top: 23, width: 500, height: 250 }, 2000, 1000), { x: 1000, y: 500 });
  assert.deepEqual(imagePoint({ clientX: 120, clientY: 100 }, { left: 20, top: 50, width: 200, height: 100 }, 2000, 1000), { x: 1000, y: 500 });
  assert.deepEqual(imagePoint({ clientX: -1, clientY: 999 }, { left: 20, top: 50, width: 200, height: 100 }, 2000, 1000), { x: 0, y: 1000 });
});

test("painted alpha becomes transparent edit area; untouched or erased pixels stay opaque", () => {
  const pixels = new Uint8ClampedArray([207, 62, 112, 255, 207, 62, 112, 128, 0, 0, 0, 0, 207, 62, 112, 127]);
  assert.equal(maskPixels(pixels), 2);
  assert.deepEqual([...pixels], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0, 255]);
  assert.equal(maskPixels(new Uint8ClampedArray(16)), 0);
});

function canvasHarness() {
  const events = {}, operations = [];
  const context = { clearRect() { operations.length = 0; }, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
    arc(x, y, radius) { operations.push({ x, y, radius, blend: this.globalCompositeOperation }); }, fill() {},
    getImageData: () => ({ data: new Uint8ClampedArray(16) }),
  };
  const canvas = { width: 1000, height: 500, addEventListener: (name, fn) => events[name] = fn,
    getContext: () => context, focus() {}, setPointerCapture() {},
    getBoundingClientRect: () => ({ left: 10, top: 20, width: 500, height: 250 }),
  };
  const painter = new Painter(canvas);
  function fire(name, args = {}) {
    events[name]({ preventDefault() {}, clientX: 110, clientY: 70, pointerId: 1, pointerType: "touch", ...args });
  }
  return { painter, operations, fire };
}

test("brush supports taps, pointer capture, erase and undo without retaining a mask for the next image", () => {
  const { painter, operations, fire } = canvasHarness();
  fire("pointerdown");
  fire("pointermove", { clientX: 210 });
  fire("pointerdown", { pointerId: 2 }); // Ignore a second finger during a stroke.
  fire("pointerup");
  assert.equal(painter.strokes.length, 1);
  assert.equal(painter.strokes[0].points.length, 2);
  painter.tool = "erase";
  fire("pointerdown");
  fire("pointercancel");
  assert.equal(operations.at(-1).blend, "destination-out");
  painter.undo();
  assert.ok(operations.every(operation => operation.blend === "source-over"));
  painter.reset(800, 600);
  assert.equal(painter.strokes.length, 0);
  assert.throws(() => painter.exportMask(), /先涂抹/);
});

test("keyboard can paint without dragging and disabled editing cannot add a stroke", () => {
  const { painter, fire } = canvasHarness();
  fire("keydown", { key: "ArrowRight" });
  fire("keydown", { key: " " });
  assert.deepEqual(painter.strokes[0].points[0], { x: 510, y: 250 });
  painter.enabled = false;
  fire("keydown", { key: " " });
  fire("pointerdown");
  assert.equal(painter.strokes.length, 1);
});

const source = readFileSync(join(__dirname, "../image3d_frontend/app.js"), "utf8");
const editorSource = source.slice(source.indexOf("function canEditViewMask("), source.indexOf("function currentViewsReady("))
  + source.slice(source.indexOf("let viewMaskEditor ="), source.indexOf("async function refine("));
function editorHarness() {
  const elements = new Map(), calls = [];
  function $(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      value: "移除多余装饰", textContent: "", disabled: false, events: {}, style: {},
      classList: { toggle() {}, add() {}, remove() {} },
      focus() {}, setAttribute() {}, removeAttribute() {},
      addEventListener(name, fn) { this.events[name] = fn; },
      close() { this.closed = true; }, showModal() { this.open = true; },
    });
    return elements.get(selector);
  }
  const context = {
    $, actionInFlight: false, session: { id: "session-1", token: "secret" },
    state: { status: "VIEWS_REVIEW", view_source: "generated", views: { left: { revision: 2, sha256: "a".repeat(64) } } },
    labels: { left: "左视图" }, viewModeChanged: () => false, isCurrentView: () => true,
    document: { querySelector: $ }, clearError() {}, notify() {}, refresh: async () => {},
    setActionInFlight: value => { context.actionInFlight = value; }, render: next => { context.state = next; },
    api: async (path, options) => { calls.push({ path, data: JSON.parse(options.body) }); return context.state; },
    AbortController, setTimeout, clearTimeout, URL, fetch: async () => { throw Error("offline"); },
  };
  vm.createContext(context);
  vm.runInContext(editorSource, context);
  vm.runInContext(`viewMaskEditor = { view: "left", sessionId: "session-1", revision: 2, sha256: "${"a".repeat(64)}", ready: true, saving: false };
    viewMaskPainter = { enabled: true, exportMask: () => "data:image/png;base64,mask", clear() {} };`, context);
  return { context, $, calls };
}

test("submit sends prompt, transparent mask and source version exactly once, without extra paid review", async () => {
  const { context, $, calls } = editorHarness();
  let resolve;
  const api = context.api;
  context.api = async (...args) => { await new Promise(done => { resolve = done; }); return api(...args); };
  const first = context.submitViewMask();
  await context.submitViewMask();
  assert.equal(context.actionInFlight, true);
  assert.equal($("#view-mask-controls").disabled, true);
  resolve();
  await first;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/sessions/session-1/views/left/refine");
  assert.deepEqual(calls[0].data, { prompt: "移除多余装饰", mask_data_url: "data:image/png;base64,mask", source_revision: 2, source_sha256: "a".repeat(64) });
  assert.equal($("#view-mask-dialog").closed, true);
  assert.equal(context.actionInFlight, false);
});

test("empty prompt, blank mask and stale image cannot trigger a paid request", async () => {
  for (const setup of [
    h => { h.$("#view-mask-prompt").value = "  "; },
    h => { h.context.state.views.left.revision = 3; },
    h => { h.context.session.id = "new-session"; },
    h => vm.runInContext('viewMaskPainter.exportMask = () => { throw Error("请先涂抹需要修改的区域"); }', h.context),
  ]) {
    const h = editorHarness();
    setup(h);
    await h.context.submitViewMask();
    assert.equal(h.calls.length, 0);
    assert.ok(h.$("#view-mask-error").textContent);
  }
});

test("request failure retains selection and prompt, and restores controls for a deliberate retry", async () => {
  const { context, $ } = editorHarness();
  context.api = async () => { throw Error("provider unavailable"); };
  await context.submitViewMask();
  assert.match($("#view-mask-error").textContent, /provider unavailable/);
  assert.equal($("#view-mask-prompt").value, "移除多余装饰");
  assert.equal($("#view-mask-dialog").closed, undefined);
  assert.equal($("#view-mask-controls").disabled, false);
  assert.equal(vm.runInContext("viewMaskPainter.exportMask()", context), "data:image/png;base64,mask");
  assert.equal(context.actionInFlight, false);
});

test("source loading failure disables painting and displays an actionable error", async () => {
  const { context, $ } = editorHarness();
  let request;
  context.fetch = async (url, options) => { request = { url, options }; throw Error("offline"); };
  await context.openViewMask("left");
  assert.equal(request.options.headers["X-Session-Token"], "secret");
  assert.match(request.url, /edit-image\?revision=2$/);
  assert.equal($("#view-mask-controls").disabled, true);
  assert.equal($("#close-view-mask").disabled, false);
  assert.match($("#view-mask-error").textContent, /offline/);
});

test("uploaded views can open and submit a mask from review or confirmation without AI analysis", async () => {
  for (const status of ["VIEWS_REVIEW", "VIEWS_CONFIRMED", "CONFIRMED", "FAILED"]) {
    const { context, $, calls } = editorHarness();
    context.state.view_source = "uploaded";
    context.state.status = status;
    await context.submitViewMask();
    assert.equal(calls.length, 1, status);
    let fetched = false;
    context.fetch = async () => { fetched = true; throw Error("test offline"); };
    await context.openViewMask("left");
    assert.equal(fetched, true, status);
    assert.match($("#view-mask-scope-help").textContent, /替换上传/);
    assert.doesNotMatch($("#view-mask-scope-help").textContent, /单张重试/);
  }
});

test("uploaded missing views and active or completed tasks cannot open or submit mask edits", async () => {
  for (const status of ["GENERATING_VIEWS", "REVIEWING_VIEWS", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED", "VIEWS_REVIEW"]) {
    const { context, $, calls } = editorHarness();
    context.state.view_source = "uploaded";
    context.state.status = status;
    if (status === "VIEWS_REVIEW") context.isCurrentView = () => false;
    let fetched = false;
    context.fetch = async () => { fetched = true; throw Error("unexpected fetch"); };
    await context.openViewMask("left");
    await context.submitViewMask();
    assert.equal(fetched, false);
    assert.equal(calls.length, 0);
    assert.equal($("#view-mask-dialog").open, undefined);
  }
});
