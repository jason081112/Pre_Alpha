// SPDX-FileCopyrightText: 2026 jason081112
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #98 事件日志的行为验证：不靠浏览器，靠最小 DOM stub 跑真实页面脚本。
//
//   node tests/ui_debug_smoke.mjs
//
// 验的是「日志真的被写进 localStorage」，不是「某个字符串出现在 HTML 里」。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "shell/ui.py"), "utf8");
const m = src.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/);
if (!m) {
  console.error("✗ 找不到页面脚本");
  process.exit(1);
}
const code = m[1];

// ── 最小 DOM / BOM stub ──────────────────────────────────────────
const store = new Map();
store.set("alpha.token", "tok");
store.set("alpha.persona_id", "luna");
store.set("alpha.client_id", "cid-test");

const listeners = new Map();
function el(id) {
  return {
    id,
    hidden: false,
    textContent: "",
    value: "",
    style: {},
    scrollTop: 0,
    scrollHeight: 100,
    children: [],
    appendChild(c) { this.children.push(c); },
    addEventListener(t, fn) { listeners.set(id + ":" + t, fn); },
    focus() {},
    click() {},
    removeChild() {},
  };
}
const els = new Map();
const document = {
  getElementById(id) {
    if (!els.has(id)) els.set(id, el(id));
    return els.get(id);
  },
  createElement() { return el("tmp"); },
  createTextNode() { return {}; },
  body: { appendChild() {}, removeChild() {} },
};

let wsInstance = null;
class WebSocketStub {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    wsInstance = this;
  }
  send() {}
  close() {}
}
WebSocketStub.OPEN = 1;
WebSocketStub.CLOSED = 3;

const fetchStub = (url) =>
  Promise.resolve({
    status: 200,
    ok: true,
    json: () =>
      Promise.resolve(
        String(url).includes("/v1/models")
          ? { data: [{ id: "luna" }] }
          : { messages: [] }
      ),
  });

const sandbox = {
  document,
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
    removeItem: (k) => store.delete(k),
  },
  location: { search: "?debug=1", protocol: "http:", host: "x.test" },
  navigator: { userAgent: "node" },
  WebSocket: WebSocketStub,
  fetch: fetchStub,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  console,
  Blob: class {},
  URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
  crypto: { randomUUID: () => "uuid-fixed" },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: "ui.js" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const dbg = () =>
  JSON.parse(store.get("alpha.debug") || "[]");
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? " — " + detail : ""}`);
};

await sleep(1500); // 等 boot() 里的 fetch 链跑完

check("连接已建立（stub）", !!wsInstance, wsInstance ? wsInstance.url.replace(/token=.*/, "token=***") : "无");

if (wsInstance) {
  const before = dbg().length;

  wsInstance.onopen && wsInstance.onopen();
  wsInstance.onmessage({ data: JSON.stringify({ type: "chat_start", session_id: "s1" }) });
  wsInstance.onmessage({ data: JSON.stringify({ type: "chunk", content: "我在", index: 0, segment: 0 }) });
  wsInstance.onmessage({ data: JSON.stringify({ type: "chunk", content: "。", index: 1, segment: 0 }) });
  wsInstance.onmessage({ data: JSON.stringify({ type: "segment_end", segment: 0, reply: "我在。", pause_ms: 300 }) });
  wsInstance.onmessage({ data: JSON.stringify({ type: "chat_end", reply: "我在。", chunks: 2, segments: ["我在。"] }) });
  wsInstance.onmessage({ data: "not-json" });
  if (wsInstance.onclose) wsInstance.onclose({ code: 1006, reason: "", wasClean: false });

  await sleep(1200); // 等节流写入

  const log = dbg();
  const kinds = log.map((e) => e.k);
  check("日志条数增加", log.length > before, `${before} → ${log.length}`);
  check("记录 ws.open", kinds.includes("ws.open"));
  check("记录 recv.chunk", kinds.filter((k) => k === "recv.chunk").length === 2,
    `n=${kinds.filter((k) => k === "recv.chunk").length}`);
  check("chunk 带 segment/index", (() => {
    const c = log.find((e) => e.k === "recv.chunk");
    return c && c.d && c.d.s === 0 && c.d.i === 0 && c.d.c === "我在";
  })());
  check("记录 recv.segment_end", kinds.includes("recv.segment_end"));
  check("记录 recv.chat_end", kinds.includes("recv.chat_end"));
  check("记录 ws.close 含 code", (() => {
    const c = log.find((e) => e.k === "ws.close");
    return c && c.d && c.d.code === 1006;
  })());
  check("非法 JSON 被单独记录", kinds.includes("recv.bad"));
  check("每条都有时间戳", log.every((e) => typeof e.t === "number" && e.t > 0));
}

const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
