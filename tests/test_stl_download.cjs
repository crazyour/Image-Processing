const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { webcrypto, createHash } = require("node:crypto");
const source = fs.readFileSync(path.join(__dirname, "../image3d_frontend/stl-download.js"), "utf8");
const CHUNK = 8 * 1024 * 1024;
const hash = data => createHash("sha256").update(data).digest("hex");

function harness(fetch) {
  const context = vm.createContext({ fetch, crypto: webcrypto, AbortController, setTimeout, clearTimeout });
  vm.runInContext(source, context);
  return vm.runInContext("StlDownload", context);
}
function response(bytes, start, end, size, exposeRange = true) {
  return new Response(bytes, { status: 206, headers: exposeRange ? { "Content-Range": `bytes ${start}-${end}/${size}` } : {} });
}

test("an interrupted 190 MB original resumes its failed chunk and keeps every byte", async () => {
  const size = 84 + 4_000_000 * 50; // A 190.7 MiB, four-million-face binary STL.
  function part(start, end) {
    const bytes = Buffer.alloc(end - start + 1, 0x45);
    if (start === 0) {
      bytes.fill(0, 0, 84);
      bytes.write("large original STL");
      bytes.writeUInt32LE(4_000_000, 80);
    }
    return bytes;
  }
  const expected = createHash("sha256");
  for (let start = 0; start < size; start += CHUNK) expected.update(part(start, Math.min(start + CHUNK, size) - 1));
  const calls = [], progress = [];
  let interrupted = false;
  const downloader = harness(async (url, options) => {
    assert.equal(options.cache, "no-store");
    const [, first, last] = /bytes=(\d+)-(\d+)/.exec(options.headers.Range);
    const start = Number(first), end = Number(last);
    calls.push(start);
    if (start === 3 * CHUNK && !interrupted) {
      interrupted = true;
      // A response ending early must not be accepted as a complete range.
      return response(part(start, end - 1_000_000), start, end, size);
    }
    return response(part(start, end), start, end, size);
  });
  const output = await downloader.fetchModel({ url: "/large.stl", size_bytes: size, sha256: expected.digest("hex") },
    (received, total, phase) => progress.push([received, total, phase]));
  assert.equal(output.byteLength, size);
  assert.equal(new DataView(output).getUint32(80, true), 4_000_000);
  assert.equal(new Uint8Array(output).at(-1), 0x45);
  assert.equal(calls.filter(start => start === 3 * CHUNK).length, 2);
  assert.equal(calls.filter(start => start === 0).length, 1);
  assert.ok(progress.some(([, , phase]) => phase === "retrying"));
  assert.deepEqual(progress.at(-1), [size, size, "verifying"]);
});

test("short full responses and permanently truncated chunks never become downloads", async () => {
  for (const size of [1000, CHUNK + 500]) {
    let calls = 0;
    const downloader = harness(async () => { calls += 1; return new Response(new Uint8Array(31)); });
    await assert.rejects(downloader.fetchModel({ url: "/truncated.stl", size_bytes: size }), /下载不完整/);
    assert.equal(calls, 3);
  }
});

test("a full response ignoring Range is used once without appending partial data", async () => {
  const bytes = Buffer.alloc(CHUNK + 100, 42);
  let calls = 0;
  const downloader = harness(async () => { calls += 1; return new Response(bytes); });
  const output = await downloader.fetchModel({ url: "/ignores-range.stl", size_bytes: bytes.length, sha256: hash(bytes) });
  assert.equal(output.byteLength, bytes.length);
  assert.equal(calls, 1);
});

test("wrong ranges, wrong totals and same-length corrupt files are rejected", async () => {
  const size = CHUNK + 100;
  for (const range of [`bytes 1-${CHUNK}/${size}`, `bytes 0-${CHUNK - 1}/${size + 1}`]) {
    const downloader = harness(async () => new Response(new Uint8Array(CHUNK), { status: 206, headers: { "Content-Range": range } }));
    await assert.rejects(downloader.fetchModel({ url: "/wrong-range.stl", size_bytes: size }), /分段与原文件不一致/);
  }
  const downloader = harness(async () => new Response(new Uint8Array([1, 2, 9])));
  await assert.rejects(downloader.fetchModel({ url: "/corrupt.stl", size_bytes: 3, sha256: hash(Buffer.from([1, 2, 3])) }), /完整性校验失败/);
});

test("hidden cross-origin range headers require and pass the stored hash", async () => {
  const bytes = Buffer.alloc(CHUNK + 1, 3);
  const downloader = harness(async (url, { headers }) => {
    const [, start, end] = /bytes=(\d+)-(\d+)/.exec(headers.Range).map(Number);
    return response(bytes.subarray(start, end + 1), start, end, bytes.length, false);
  });
  await assert.rejects(downloader.fetchModel({ url: "/no-hash.stl", size_bytes: bytes.length }), /分段与原文件不一致/);
  const output = await downloader.fetchModel({ url: "/hash.stl", size_bytes: bytes.length, sha256: hash(bytes) });
  assert.equal(output.byteLength, bytes.length);
});

test("preview and download share a verified read; failures can retry and changed hashes reload", async () => {
  const bytes = Buffer.from([1, 2, 3]);
  let calls = 0, fail = true;
  const downloader = harness(async () => { calls += 1; if (fail) throw Error("offline"); return new Response(bytes); });
  const load = downloader.createLoader();
  const model = { url: "/shared.stl", size_bytes: bytes.length, sha256: hash(bytes) };
  await assert.rejects(load(model), /offline/);
  fail = false;
  const [preview, download] = await Promise.all([load(model), load(model)]);
  assert.equal(preview, download);
  assert.equal(calls, 4);
  assert.equal(await load(model), preview);
  await assert.rejects(load({ ...model, sha256: "0".repeat(64) }), /完整性校验失败/);
  assert.equal(calls, 5);
});

test("legacy files without size still download; empty and oversized bodies are rejected", async () => {
  const downloader = harness(async () => new Response(Buffer.from([1, 2, 3])));
  assert.equal((await downloader.fetchModel({ url: "/legacy.stl" })).byteLength, 3);
  await assert.rejects(downloader.fetchModel({ url: "/oversized.stl", size_bytes: 2 }), /大小与原文件不一致/);
  await assert.rejects(harness(async () => new Response()).fetchModel({ url: "/empty.stl", size_bytes: 1 }));
});
