// SPDX-FileCopyrightText: 2026 jason081112
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #98 P0 的前端行为：切换人格要换连接、断线重连要补历史。
//
//   node tests/ui_p0_smoke.mjs
//
// 用最小 DOM stub 跑真实页面脚本，断言「真的发生了这些动作」。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "shell/ui.py"), "utf8");
const code = src.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/)[1];

const store = new Map([
  ["alpha.token", "tok"],
  ["alpha.persona_id", "luna"],
  ["alpha.client_id", "cid"],
]);

const listeners = new Map();
const made = [];
function el(id) {
  const node = {
    id, hidden: false, value: "", style: {}, scrollTop: 0, scrollHeight: 100,
    children: [], className: "",
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(t, fn) { listeners.set(id + ":" + t, fn); },
    focus() {}, click() {}, removeChild() {},
  };
  // 真浏览器的 textContent="" 会清空子节点，stub 也要照做，否则测不出「切人格清屏」
  let tc = "";
  Object.defineProperty(node, "textContent", {
    get() { return tc; },
    set(v) { tc = String(v); if (tc === "") node.children = []; },
  });
  made.push(node);
  return node;
}
const els = new Map();
const document = {
  getElementById(id) {
    if (!els.has(id)) els.set(id, el(id));
    return els.get(id);
  },
  createElement() { return el("tmp"); },
  body: { appendChild() {}, removeChild() {} },
};

const sockets = [];
class WS {
  constructor(url) { this.url = url; this.readyState = 1; this.closed = false; sockets.push(this); }
  send() {}
  close() { this.closed = true; this.readyState = 3; }
}
WS.OPEN = 1; WS.CLOSED = 3;

let historyCalls = 0;
let historyPayload = { messages: [] };
const fetchStub = (url) => {
  const u = String(url);
  if (u.includes("/v1/models")) {
    return Promise.resolve({ status: 200, ok: true,
      json: () => Promise.resolve({ data: [{ id: "luna" }, { id: "kai" }] }) });
  }
  if (u.includes("/api/chat/history")) {
    historyCalls += 1;
    return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(historyPayload) });
  }
  return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) });
};

const sandbox = {
  document,
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
    removeItem: (k) => store.delete(k),
  },
  location: { search: "", protocol: "http:", host: "x.test" },
  navigator: { userAgent: "node" },
  WebSocket: WS,
  fetch: fetchStub,
  setTimeout, clearTimeout, setInterval, clearInterval, console,
  Blob: class {}, URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
  crypto: { randomUUID: () => "uuid" },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: "ui.js" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const logEl = els.get("log");
const results = [];
const check = (n, ok, d = "") => {
  results.push(ok);
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${n}${d ? " — " + d : ""}`);
};

await sleep(1200);

// ── 1. 切换人格 ────────────────────────────────────────────────
const before = sockets.length;
const histBefore = historyCalls;
listeners.get("personaSelect:change")({ target: { value: "kai" } });
await sleep(800);

check("切人格后建立了新 WS", sockets.length > before, `${before} → ${sockets.length}`);
check("旧 WS 被关闭", !!sockets[0] && sockets[0].closed);
check("切人格后重新拉历史", historyCalls > histBefore, `${histBefore} → ${historyCalls}`);
// 清屏后 loadHistory 会补一条「开始聊天吧」，所以断言是「只剩系统提示」
check("切人格后清屏（只剩系统提示）", logEl.children.length <= 1,
  `children=${logEl.children.length}`);
check("新 WS 用的是 stream 地址", sockets.at(-1).url.includes("/ws/chat/stream"));

// ── 2. 断线重连补历史 ──────────────────────────────────────────
els.get("text").value = "断线测试";
listeners.get("send:click")();
await sleep(100);

historyPayload = {
  messages: [
    { id: 1, role: "user", content: "断线测试", modality: "文字", created_at: 1 },
    { id: 2, role: "assistant", content: "我在。", modality: "文字", created_at: 2 },
  ],
};

const histBeforeResync = historyCalls;
sockets.at(-1).onclose({ code: 1006, reason: "", wasClean: false });

await sleep(3500); // 等 3s 重连定时器
const reconnected = sockets.at(-1);
check("重连建立了新连接", sockets.length > 2, `共 ${sockets.length} 个`);
if (reconnected && reconnected.onopen) reconnected.onopen();  // 真 WS 连上会回调
await sleep(900);
check("重连后补拉了历史", historyCalls > histBeforeResync,
  `history ${histBeforeResync} → ${historyCalls}`);

const texts = made.map((n) => n.textContent).join(" | ");
check("界面补上了断线期间的回复", texts.includes("我在。"));

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
