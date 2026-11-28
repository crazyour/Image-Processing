const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const app = fs.readFileSync(path.join(root, "image3d_frontend/app.js"), "utf8");
const reset = fs.readFileSync(path.join(root, "frontend_dist/assets/reset-on-refresh.js"), "utf8");
const appInit = app.slice(0, app.indexOf("let state = null;") + "let state = null;".length);
const active = { id: "active-session", token: "active-private-token" };
const sessionKey = "image3d-session", markerKey = "image3d-processing-resume";

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) };
}

function boot(initial = {}, pathname = "/image-to-3d") {
  const sessionStorage = storage(initial), localStorage = storage({ [sessionKey]: "old-local-task" });
  const documentHandlers = new Map(), windowHandlers = new Map();
  let reloads = 0, formResets = 0;
  const fileInput = { value: "old-upload" };
  const context = {
    sessionStorage, localStorage, URLSearchParams,
    location: { pathname, search: "", reload() { reloads++; } },
    document: {
      addEventListener: (name, fn) => documentHandlers.set(name, fn),
      querySelector() { return null; },
      querySelectorAll(selector) { return selector === "form" ? [{ reset() { formResets++; } }] : [fileInput]; },
    },
    window: { addEventListener: (name, fn) => windowHandlers.set(name, fn) },
  };
  vm.createContext(context);
  vm.runInContext(reset, context);
  vm.runInContext(appInit, context);
  return { sessionStorage, localStorage, documentHandlers, windowHandlers, fileInput,
    currentSession: () => vm.runInContext("session", context), counts: () => ({ reloads, formResets }) };
}

test("retired processing markers never revive a task on document entry", () => {
  for (const status of ["pending", "running", "failed", "completed"]) {
    const env = boot({ [sessionKey]: JSON.stringify(active),
      [markerKey]: JSON.stringify({ ...active, source_url: "/original.stl", status }) });
    assert.equal(env.currentSession(), null);
    assert.equal(env.sessionStorage.getItem(sessionKey), null);
    assert.equal(env.sessionStorage.getItem(markerKey), null);
    assert.equal(env.localStorage.getItem(sessionKey), null);
  }
  const malformed = boot({ [sessionKey]: "{broken", [markerKey]: "{broken" });
  assert.equal(malformed.currentSession(), null);
  assert.equal(malformed.sessionStorage.getItem(markerKey), null);
});

test("all tool entries reset tasks while retaining account and preference values", () => {
  for (const pathname of ["/image-to-3d", "/text-to-image", "/tools/visual", "/animal-photo-cutout", "/"]) {
    const env = boot({ [sessionKey]: JSON.stringify(active), [markerKey]: "old-marker",
      "text-image-task": "text", "visual-studio-task": "visual", "animal-cutout-task": "animal",
      "preferences": "keep", "account-session": "keep-account" }, pathname);
    for (const key of [sessionKey, markerKey, "text-image-task", "visual-studio-task", "animal-cutout-task"]) {
      assert.equal(env.sessionStorage.getItem(key), null);
    }
    assert.equal(env.sessionStorage.getItem("preferences"), "keep");
    assert.equal(env.sessionStorage.getItem("account-session"), "keep-account");
  }
});

test("entry reset still clears restored forms and reloads back-forward cached pages", () => {
  const env = boot();
  env.documentHandlers.get("DOMContentLoaded")();
  assert.equal(env.fileInput.value, "");
  assert.equal(env.counts().formResets, 1);
  env.windowHandlers.get("pageshow")({ persisted: false });
  assert.equal(env.counts().reloads, 0);
  env.windowHandlers.get("pageshow")({ persisted: true });
  assert.equal(env.counts().reloads, 1);
});
