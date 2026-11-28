const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const app = fs.readFileSync(path.join(root, "image3d_frontend/app.js"), "utf8");
const reset = fs.readFileSync(path.join(root, "frontend_dist/assets/reset-on-refresh.js"), "utf8");
const sessionValue = { id: "active-session", token: "active-private-token" };
const sessionKey = "image3d-session", markerKey = "image3d-processing-resume";
const appInit = app.slice(0, app.indexOf("let state = null;") + "let state = null;".length);
const markerFunctions = app.slice(app.indexOf("function currentStlModel()"), app.indexOf("function modelFileJobActive("));

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) };
}

function marker(status = "pending") {
  return { ...sessionValue, source_url: "/original.stl", status };
}

function boot(initial = {}, pathname = "/image-to-3d") {
  const sessionStorage = storage(initial), localStorage = storage({ [sessionKey]: "old-local-task" });
  const documentHandlers = new Map(), windowHandlers = new Map();
  const context = {
    sessionStorage, localStorage, URLSearchParams,
    location: { pathname, search: "", reload() {} },
    document: { addEventListener: (name, fn) => documentHandlers.set(name, fn), querySelector() { return null; } },
    window: { addEventListener: (name, fn) => windowHandlers.set(name, fn) },
  };
  vm.createContext(context);
  // Execute the production scripts in their actual HTML order.
  vm.runInContext(reset, context);
  vm.runInContext(appInit + "\n" + markerFunctions, context);
  function currentSession() { return vm.runInContext("session", context); }
  function sync(next, nextSession) {
    context.nextState = next;
    if (nextSession !== undefined) { context.nextSession = nextSession; vm.runInContext("session = nextSession;", context); }
    vm.runInContext("state = nextState; syncModelProcessingResume();", context);
  }
  return { context, sessionStorage, localStorage, currentSession, sync, documentHandlers, windowHandlers };
}

function state(status) {
  return { status: "SUCCEEDED", session_id: sessionValue.id, model_task: { models: { stl: {
    url: "/original.stl", sha256: "source-sha", ...(status ? { processing: { status, version: "pipeline-v1", stage: "smooth" } } : {}),
  } } } };
}

test("actual reset then app initialization retains only matching unfinished processing sessions", () => {
  for (const status of ["pending", "running", "failed"]) {
    const env = boot({ [sessionKey]: JSON.stringify(sessionValue), [markerKey]: JSON.stringify(marker(status)),
      "text-image-task": "text", "visual-studio-task": "visual", "animal-cutout-task": "animal", "preferences": "keep" });
    assert.equal(JSON.stringify(env.currentSession()), JSON.stringify(sessionValue));
    env.sync(null); // Initial render(null) before the authenticated GET must not erase recovery.
    assert.ok(env.sessionStorage.getItem(markerKey));
    for (const key of ["text-image-task", "visual-studio-task", "animal-cutout-task"]) assert.equal(env.sessionStorage.getItem(key), null);
    assert.equal(env.sessionStorage.getItem("preferences"), "keep");
    assert.equal(env.localStorage.getItem(sessionKey), null);
    assert.ok(env.documentHandlers.has("DOMContentLoaded"));
    assert.ok(env.windowHandlers.has("pageshow"));
  }
});

test("an ordinary task, cleared session or mismatched credentials cannot revive from a marker", () => {
  const cases = [
    { [sessionKey]: JSON.stringify(sessionValue) },
    { [markerKey]: JSON.stringify(marker()) },
    { [sessionKey]: JSON.stringify({ ...sessionValue, id: "new-session" }), [markerKey]: JSON.stringify(marker()) },
    { [sessionKey]: JSON.stringify({ ...sessionValue, token: "different-token" }), [markerKey]: JSON.stringify(marker()) },
    { [sessionKey]: JSON.stringify(sessionValue), [markerKey]: JSON.stringify(marker("completed")) },
    { [sessionKey]: JSON.stringify(sessionValue), [markerKey]: "{broken" },
    { [sessionKey]: "{broken", [markerKey]: JSON.stringify(marker()) },
  ];
  for (const values of cases) {
    const env = boot(values);
    assert.equal(env.currentSession(), null);
    assert.equal(env.sessionStorage.getItem(markerKey), null);
  }
});

test("other tools retain their original reset behavior even with a valid processing marker", () => {
  for (const pathname of ["/text-to-image", "/tools/visual", "/animal-photo-cutout", "/"]) {
    const env = boot({ [sessionKey]: JSON.stringify(sessionValue), [markerKey]: JSON.stringify(marker()) }, pathname);
    assert.equal(env.currentSession(), null);
    assert.equal(env.sessionStorage.getItem(markerKey), null);
  }
});

test("only an unfinished returned STL writes the marker; completion removes it", () => {
  const env = boot();
  env.sync({ status: "GENERATING_3D", session_id: sessionValue.id }, sessionValue);
  assert.equal(env.sessionStorage.getItem(markerKey), null);
  for (const status of [undefined, "pending", "running", "failed"]) {
    env.sync(state(status));
    assert.deepEqual(JSON.parse(env.sessionStorage.getItem(markerKey)), marker(status || "pending"));
  }
  const complete = state("completed");
  complete.model_task.models.stl.processed = { url: "/final.stl", version: "pipeline-v1", source_sha256: "source-sha" };
  env.sync(complete);
  assert.equal(env.sessionStorage.getItem(markerKey), null);
  const nextBoot = boot({ [sessionKey]: JSON.stringify(sessionValue) });
  assert.equal(nextBoot.currentSession(), null);
});

test("switching or rejecting a session clears the old recovery marker", () => {
  for (const nextSession of [null, { id: "new-session", token: "new-token" }]) {
    const env = boot({ [sessionKey]: JSON.stringify(sessionValue), [markerKey]: JSON.stringify(marker()) });
    env.sync(null, nextSession);
    assert.equal(env.sessionStorage.getItem(markerKey), null);
  }
  const env = boot({ [sessionKey]: JSON.stringify(sessionValue), [markerKey]: JSON.stringify(marker()) });
  env.sync({ ...state("pending"), session_id: "another-session" });
  assert.equal(env.sessionStorage.getItem(markerKey), null);
});
