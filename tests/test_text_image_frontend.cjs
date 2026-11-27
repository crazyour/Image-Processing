// Exercise scene editing and paid-request counts without calling an image provider.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../text2image_frontend/app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}

function harness(visualMode = false) {
  const rows = [], elements = new Map(), calls = [], generated = [], errors = [];
  let focused = null;
  function element() {
    const classes = new Set(), children = new Map();
    return {
      value: "", disabled: false, textContent: "", attributes: {}, listeners: {},
      classList: {
        add(name) { classes.add(name); },
        toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
        contains(name) { return classes.has(name); },
      },
      set innerHTML(markup) {
        for (const [selector, token] of [["textarea", "<textarea"], ["label", "<label"],
          [".prompt-number", 'class="prompt-number"'], [".remove-prompt", 'class="remove-prompt"']]) {
          if (markup.includes(token)) children.set(selector, element());
        }
      },
      querySelector(selector) { return children.get(selector) || null; },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, listener) { this.listeners[name] = listener; },
      appendChild(row) { rows.push(row); },
      remove() { rows.splice(rows.indexOf(this), 1); },
      get nextElementSibling() { return rows[rows.indexOf(this) + 1]; },
      get previousElementSibling() { return rows[rows.indexOf(this) - 1]; },
      focus() { focused = this; },
    };
  }
  const $ = selector => {
    if (selector === "#prompt-list .prompt-row:last-child textarea") return rows.at(-1)?.querySelector("textarea");
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  };
  const context = {
    $, visualMode, busy: false, task: null, textTaskSessionKey: "test-task",
    document: {
      createElement: element,
      querySelectorAll(selector) {
        if (selector === ".prompt-row") return rows;
        if (selector === ".prompt-input") return rows.map(row => row.querySelector("textarea"));
        if (selector === ".prompt-input, .remove-prompt") return rows.flatMap(row => [row.querySelector("textarea"), row.querySelector(".remove-prompt")]);
        return [];
      },
    },
    referenceGroups: { product: { files: [] }, example: { files: [] } },
    sessionStorage: { setItem() {} },
    notify() {}, showError(error) { errors.push(error); },
    applyTaskState(state) { context.task.state = state; },
    async refreshTask() { return context.task.state; },
    async generateItem(index) { generated.push(index); context.task.state.items[index].status = "SUCCEEDED"; },
    async api(path, options) {
      const body = JSON.parse(options.body);
      calls.push({ path, body });
      return { task_id: "test-task", access_token: "test-token", status: "QUEUED",
        items: body.prompts.map((prompt, index) => ({ prompt, index, status: "QUEUED" })) };
    },
  };
  vm.createContext(context);
  vm.runInContext([
    section("function promptValues()", "function renderReferenceGroup("),
    section("async function runQueue(", "function resetTask("),
    section('$("#task-form").addEventListener', '$("#add-product-reference").addEventListener'),
  ].join("\n"), context);
  $("#generation-count").value = "1";
  context.addPrompt();
  return { context, $, rows, calls, generated, errors,
    submit: () => $("#task-form").listeners.submit({ preventDefault() {} }),
    focused: () => focused };
}

test("text image can add five scenes and focuses the new prompt", () => {
  const { $, rows, focused } = harness();
  for (let i = 0; i < 5; i++) $("#add-prompt").listeners.click();
  assert.equal(rows.length, 5);
  assert.equal($("#add-prompt").disabled, true);
  assert.equal(focused(), rows[4].querySelector("textarea"));
  assert.equal($("#prompt-count").textContent, "5 个场景 · 5 张独立图片");
  assert.equal($("#generation-count-options").classList.contains("hidden"), true);
});

test("deleting a middle scene preserves remaining prompts, numbering and focus", () => {
  const { context, rows, focused } = harness();
  rows[0].querySelector("textarea").value = "客厅";
  context.addPrompt("庭院");
  context.addPrompt("展厅");
  rows[1].querySelector(".remove-prompt").listeners.click();
  assert.deepEqual(Array.from(context.promptValues()), ["客厅", "展厅"]);
  assert.equal(rows[1].querySelector("textarea").id, "prompt-1");
  assert.equal(rows[1].querySelector("label").attributes.for, "prompt-1");
  assert.equal(rows[1].querySelector(".prompt-number").textContent, "02");
  assert.equal(focused(), rows[1].querySelector("textarea"));
});

test("returning to one scene restores the chosen candidate count and keeps one row", () => {
  const { context, $, rows } = harness();
  $("#generation-count").value = "3";
  context.addPrompt("庭院");
  rows[1].querySelector(".remove-prompt").listeners.click();
  assert.equal($("#generation-count-options").classList.contains("hidden"), false);
  assert.equal($("#generation-count").disabled, false);
  assert.equal($("#prompt-count").textContent, "同一要求生成 3 张");
  assert.equal(rows[0].querySelector(".remove-prompt").disabled, true);
  rows[0].querySelector(".remove-prompt").listeners.click();
  assert.equal(rows.length, 1);
});

test("multiple scenes submit each prompt once regardless of the previous candidate count", async () => {
  const { context, $, rows, calls, generated, submit } = harness();
  $("#generation-count").value = "5";
  rows[0].querySelector("textarea").value = " 客厅 ";
  context.addPrompt("庭院");
  context.addPrompt("展厅");
  await submit();
  assert.deepEqual(calls, [{ path: "/api/text-image/tasks", body: { prompts: ["客厅", "庭院", "展厅"], purpose: "general" } }]);
  assert.deepEqual(generated, [0, 1, 2]);
});

test("one scene still generates the requested number of candidates", async () => {
  const { $, rows, calls, generated, submit } = harness();
  $("#generation-count").value = "3";
  rows[0].querySelector("textarea").value = "客厅";
  await submit();
  assert.deepEqual(calls[0].body.prompts, ["客厅", "客厅", "客厅"]);
  assert.deepEqual(generated, [0, 1, 2]);
});

test("an empty scene prevents every paid request", async () => {
  const { context, rows, calls, generated, errors, submit } = harness();
  rows[0].querySelector("textarea").value = "客厅";
  context.addPrompt("  ");
  await submit();
  assert.equal(errors[0].code, "PROMPT_REQUIRED");
  assert.equal(calls.length, 0);
  assert.equal(generated.length, 0);
});

test("busy state blocks scene changes and duplicate submission", async () => {
  const { context, $, rows, calls, submit } = harness();
  context.addPrompt("庭院");
  context.setBusy(true);
  assert.equal($("#add-prompt").disabled, true);
  assert.equal($("#generation-count").disabled, true);
  assert.ok(rows.every(row => row.querySelector("textarea").disabled && row.querySelector(".remove-prompt").disabled));
  context.addPrompt("展厅");
  rows[0].querySelector(".remove-prompt").listeners.click();
  await submit();
  assert.equal(rows.length, 2);
  assert.equal(calls.length, 0);
  context.setBusy(false);
  assert.equal($("#add-prompt").disabled, false);
});

test("visual studio keeps one image per scene and its original task purpose", async () => {
  const { context, $, rows, calls, generated, submit } = harness(true);
  $("#generation-count").value = "5";
  rows[0].querySelector("textarea").value = "客厅";
  await submit();
  assert.deepEqual(calls[0].body, { prompts: ["客厅"], purpose: "visual_studio" });
  assert.equal($("#generation-count-options").classList.contains("hidden"), true);
  context.addPrompt("庭院");
  await submit();
  assert.deepEqual(calls[1].body, { prompts: ["客厅", "庭院"], purpose: "visual_studio" });
  assert.deepEqual(generated, [0, 0, 1]);
});
