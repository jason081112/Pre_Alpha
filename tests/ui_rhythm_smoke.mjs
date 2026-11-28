// SPDX-FileCopyrightText: 2026 jason081112
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #95 渲染节奏调度器的行为验证。
//
//   node tests/ui_rhythm_smoke.mjs
//
// 用最小 DOM stub + 可控时钟跑真实页面脚本，断言：
// 节奏分布 / 不丢字对账 / 逐条气泡 / 中断补完 / 短句加速 / 情绪驱动 / fast 直出。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "shell/ui.py"), "utf8");
// #80 之后页面有两个 nonce script（SW 注册 + 主逻辑），取最长的才是主脚本
const code = [...src.matchAll(/<script nonce="[^"]+">([\s\S]*?)<\/script>/g)]
  .map((m) => m[1]).sort((a, b) => b.length - a.length)[0];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeEnv(search) {
  let clock = 0;
  let rafSeq = 0;
  const rafQ = new Map();
  const props = {};
  const store = new Map([
    ["alpha.token", "tok"],
    ["alpha.persona_id", "luna"],
    ["alpha.client_id", "cid"],
  ]);
  const listeners = new Map();
  const made = [];

  function el(id) {
    const node = {
      id, hidden: false, value: "", style: {}, scrollTop: 0,
      scrollHeight: 100, clientHeight: 600,
      children: [], className: "", parentNode: null,
      _writes: [],
      appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
      removeChild(c) {
        const i = this.children.indexOf(c);
        if (i >= 0) this.children.splice(i, 1);
        if (c) c.parentNode = null;
      },
      addEventListener(t, fn) { listeners.set(id + ":" + t, fn); },
      setAttribute() {}, focus() {}, click() {},
    };
    let tc = "";
    Object.defineProperty(node, "textContent", {
      get() { return tc; },
      set(v) {
        tc = String(v);
        node._writes.push({ at: clock, len: tc.length });
        if (tc === "") node.children = [];
      },
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
    documentElement: { style: { setProperty(k, v) { props[k] = v; } } },
    body: { appendChild() {}, removeChild() {} },
    addEventListener() {},
  };

  const sockets = [];
  class WS {
    constructor(url) { this.url = url; this.readyState = 1; this.closed = false; sockets.push(this); }
    send() {}
    close() { this.closed = true; this.readyState = 3; }
  }
  WS.OPEN = 1; WS.CLOSED = 3;

  const fetchStub = (url) => {
    const u = String(url);
    if (u.includes("/v1/models")) {
      return Promise.resolve({ status: 200, ok: true,
        json: () => Promise.resolve({ data: [{ id: "luna" }] }) });
    }
    if (u.includes("/api/chat/history")) {
      return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({ messages: [] }) });
    }
    if (u.includes("/api/me/consent")) {
      return Promise.resolve({ status: 200, ok: true,
        json: () => Promise.resolve({ accepted: true, required_version: "1.2" }) });
    }
    return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) });
  };

  const sandbox = {
    document,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    location: { search: search || "", protocol: "http:", host: "x.test" },
    navigator: { userAgent: "node" },
    performance: { now: () => clock },
    requestAnimationFrame: (fn) => { const id = ++rafSeq; rafQ.set(id, fn); return id; },
    cancelAnimationFrame: (id) => rafQ.delete(id),
    matchMedia: () => ({ matches: false }),
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

  function pump(ms) {
    const end = clock + ms;
    while (clock < end) {
      clock = Math.min(end, clock + 16);
      const q = [...rafQ.values()];
      rafQ.clear();
      q.forEach((fn) => fn(clock));
    }
  }

  const send = (obj) => sockets.at(-1).onmessage({ data: JSON.stringify(obj) });
  const themBubbles = (since) => made.slice(since).filter((n) =>
    n.className === "bubble" && n.parentNode && n.parentNode.className === "row them" &&
    n.parentNode.parentNode);  // row 仍挂在 logEl 上（被对账撤下的不算）
  const isTyping = () => made.some((n) =>
    n.className === "bubble typing" && n.parentNode && n.parentNode.parentNode);

  return { els, made, sockets, listeners, store, props, pump, send, themBubbles, isTyping,
    get clock() { return clock; } };
}

const results = [];
const check = (n, ok, d = "") => {
  results.push(ok);
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${n}${d ? " — " + d : ""}`);
};
const norm = (s) => String(s).replace(/\s+/g, "");
const gaps = (b) => b._writes.slice(1).map((w, i) => w.at - b._writes[i].at);
const avg = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);

// ── 正常节奏环境 ────────────────────────────────────────────────
const env = makeEnv("");
await sleep(1200);  // 等 boot 完成
const sock = () => env.sockets.at(-1);

// T1 节奏分布：普通字 25–40ms，句号后 ≥400ms
let since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
check("chat_start 出现打字气泡", env.isTyping());
env.send({ type: "chunk", content: "我在。好", index: 0, segment: 0 });
env.pump(2000);
let bs = env.themBubbles(since);
check("首字渲染后打字气泡消失", !env.isTyping());
check("T1 单气泡逐字渲染", bs.length === 1 && bs[0].textContent === "我在。好",
  bs.map((b) => b.textContent).join("|"));
let g = gaps(bs[0]);
// "我在。好"：g0=我→在 g1=在→。 g2=。→好（句号后的停顿体现在下一个字）
check("T1 普通字间隔 20–60ms", g[0] >= 20 && g[0] <= 60 && g[1] >= 20 && g[1] <= 60,
  `gaps=${g.join(",")}`);
check("T1 句号后停顿 ≥390ms", g[2] >= 390, `句号后 gap=${g[2]}`);
env.send({ type: "segment_end", segment: 0, reply: "我在。好", pause_ms: 300 });
env.send({ type: "chat_end", reply: "我在。好", segments: ["我在。好"], chunks: 4 });
env.pump(100);

// T2 不丢字对账：两段（分隔空行被 Segmenter 丢弃）归一化后 == reply
since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "我在。", index: 0, segment: 0 });
env.send({ type: "segment_end", segment: 0, reply: "我在。", pause_ms: 300 });
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "快去换鞋。", index: 1, segment: 1 });
env.send({ type: "segment_end", segment: 1, reply: "快去换鞋。", pause_ms: 300 });
env.send({ type: "chat_end", reply: "我在。\n\n快去换鞋。",
  segments: ["我在。", "快去换鞋。"], chunks: 2 });
env.pump(6000);
bs = env.themBubbles(since);
check("T2 两段 → 两个气泡（微信式堆叠）", bs.length === 2,
  bs.map((b) => b.textContent).join("|"));
check("T2 不丢字：DOM 归一化 == reply",
  norm(bs.map((b) => b.textContent).join("\n\n")) === norm("我在。\n\n快去换鞋。"));
check("T2 逐条出现：第二段首字晚于第一段末字",
  bs.length === 2 && bs[1]._writes[0].at > bs[0]._writes.at(-1).at);

// T3 三条 segments 逐条弹出
since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
["一。", "二。", "三。"].forEach((text, i) => {
  env.send({ type: "chunk", content: text, index: i, segment: i });
  env.send({ type: "segment_end", segment: i, reply: text, pause_ms: 300 });
  if (i < 2) env.send({ type: "chat_start", session_id: "s" });
});
env.send({ type: "chat_end", reply: "一。\n\n二。\n\n三。",
  segments: ["一。", "二。", "三。"], chunks: 3 });
env.pump(8000);
bs = env.themBubbles(since);
check("T3 三条 segments → 三个气泡", bs.length === 3,
  bs.map((b) => b.textContent).join("|"));
check("T3 气泡按序出现", bs.length === 3 &&
  bs[0]._writes[0].at < bs[1]._writes[0].at && bs[1]._writes[0].at < bs[2]._writes[0].at);

// T4 中断：发新消息 → 旧轮剩余字符立即补完，不丢字不串轮
since = env.made.length;
const longText = "这是一段很长的回复还在渲染";
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: longText, index: 0, segment: 0 });
env.pump(64);  // 只渲染了开头几个字
bs = env.themBubbles(since);
check("T4 中断前只渲染了部分", bs.length === 1 && bs[0].textContent.length < longText.length,
  `已渲染 ${bs[0] ? bs[0].textContent.length : 0}/${longText.length}`);
env.els.get("text").value = "新消息";
env.listeners.get("send:click")();
bs = env.themBubbles(since);
check("T4 发送后旧轮立即补完（不丢字）", bs.length === 1 && bs[0].textContent === longText);
const meBubbles = env.made.slice(since).filter((n) =>
  n.className === "bubble" && n.parentNode && n.parentNode.className === "row me");
check("T4 新消息独立成泡（不串字）", meBubbles.length === 1 && meBubbles[0].textContent === "新消息");
env.send({ type: "chat_end", reply: longText, segments: [longText], chunks: 1 });
env.pump(100);

// T5 连续短句加速：第三句（hurry）字符间隔 < 第一句
since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "短句。短句。短句。", index: 0, segment: 0 });
env.send({ type: "chat_end", reply: "短句。短句。短句。",
  segments: ["短句。短句。短句。"], chunks: 1 });
env.pump(8000);
bs = env.themBubbles(since);
g = gaps(bs[0]);
// 9 个字符 8 个间隔：第一句 2 个普通字间隔（idx 0,1），第三句 2 个（idx 6,7）
const firstSentence = avg([g[0], g[1]]);
const thirdSentence = avg([g[6], g[7]]);
check("T5 连续短句 hurry：第三句比第一句快", thirdSentence < firstSentence,
  `第一句 avg=${firstSentence.toFixed(0)}ms 第三句 avg=${thirdSentence.toFixed(0)}ms`);

// T6 情绪驱动：高 frustration → 下一轮更快 + 气泡圆度收紧
since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "速度测试。", index: 0, segment: 0 });
env.send({ type: "chat_end", reply: "速度测试。", segments: ["速度测试。"], chunks: 1 });
env.pump(6000);
const baseGap = avg(gaps(env.themBubbles(since)[0]).filter((x) => x < 100));
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "速度测试。", index: 0, segment: 0 });
env.send({ type: "chat_end", reply: "速度测试。", segments: ["速度测试。"], chunks: 1,
  frustration: 5, temperature: 0.3 });
env.pump(6000);
bs = env.themBubbles(since);
const moodGap = avg(gaps(bs[1]).filter((x) => x < 100));
check("T6 高 frustration → 下一轮打字更快", moodGap < baseGap * 0.85,
  `基准 ${baseGap.toFixed(0)}ms → 情绪 ${moodGap.toFixed(0)}ms`);
check("T6 气泡圆度随情绪收紧", env.props["--mood-radius"] === "11.0px",
  `--mood-radius=${env.props["--mood-radius"]}`);

// T7 兜底：一个 chunk 都没收到 → chat_end.reply 整段补上
since = env.made.length;
env.send({ type: "chat_end", reply: "兜底整段", segments: ["兜底整段"], chunks: 0 });
env.pump(100);
bs = env.themBubbles(since);
check("T7 无 chunk 时整段兜底", bs.length === 1 && bs[0].textContent === "兜底整段");

// T8 权威对账：流式文本与 reply 不符 → 以 reply 替换
since = env.made.length;
env.send({ type: "chat_start", session_id: "s" });
env.send({ type: "chunk", content: "错误文本", index: 0, segment: 0 });
env.send({ type: "chat_end", reply: "权威文本", segments: ["权威文本"], chunks: 1 });
env.pump(6000);
bs = env.themBubbles(since);
check("T8 对账不符 → 替换为权威 reply", bs.length === 1 && bs[0].textContent === "权威文本",
  bs.map((b) => b.textContent).join("|"));

// dbg 统计落盘（800ms 防抖，用真实时钟）
await sleep(1000);
const dbgLog = JSON.parse(env.store.get("alpha.debug") || "[]");
const stats = dbgLog.filter((e) => e.k === "rhythm.stats");
const okStats = stats.filter((e) => e.d && e.d.verify === true);
const mismatches = dbgLog.filter((e) => e.k === "rhythm.mismatch");
check("dbg 记录 rhythm.stats 且对账通过", okStats.length >= 6 && mismatches.length === 1,
  `verify通过 ${okStats.length} 条，异常替换 ${mismatches.length} 条（T8 预期 1）`);

// ── fast 直出环境：同一段文本，时间分布明显不同 ──────────────────
const fast = makeEnv("?fast=1");
await sleep(1200);
since = fast.made.length;
fast.send({ type: "chat_start", session_id: "s" });
fast.send({ type: "chunk", content: "我在。好", index: 0, segment: 0 });
fast.pump(48);
const fbs = fast.themBubbles(since);
check("fast=1 直出：48ms 内全部渲染", fbs.length === 1 && fbs[0].textContent === "我在。好");
const fastElapsed = fbs[0]._writes.at(-1).at - fbs[0]._writes[0].at;
check("fast 与正常节奏分布明显不同", fastElapsed <= 48,
  `fast 总耗时 ${fastElapsed}ms（正常模式同文本 >400ms）`);

const failed = results.filter((r) => !r).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
