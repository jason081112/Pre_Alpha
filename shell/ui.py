# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#46 极简聊天页：单文件 HTML，不依赖框架与构建。

设计约束：
- 只接 shell 的端点：认证 /api/auth/*、人格 /v1/models、历史 /api/chat/history、
  对话走 /ws/chat/stream（上游 /ws/chat 没有中间内容）
- CSP nonce：页面内联样式与脚本都带 nonce，无内联事件属性
- token 存 localStorage（#82 会换成 HttpOnly cookie + CSRF）
- 渲染只做「能读」，节奏与动效留给 #95 / #80
"""

from __future__ import annotations

import secrets

APP_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>Alpha</title>
<style nonce="__CSP_NONCE__">
:root {
  color-scheme: dark;
  --bg: #0f1115;
  --panel: #171a21;
  --line: #262b35;
  --text: #e8eaf0;
  --muted: #8b94a3;
  --accent: #6f8cff;
  --bubble-them: #232833;
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
}
button, input, select {
  font: inherit;
  color: inherit;
}
button {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 12px;
  padding: 10px 14px;
  cursor: pointer;
}
button:active { transform: scale(.98); }
button:disabled { opacity: .5; cursor: default; }

/* ── 登录 ── */
#login {
  height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
}
.card {
  width: 100%;
  max-width: 340px;
  padding: 26px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--panel);
}
.card h1 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 650;
}
.card p {
  margin: 0 0 20px;
  color: var(--muted);
  font-size: 13px;
}
.card input {
  width: 100%;
  margin-bottom: 10px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #10131a;
}
.card input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.card button { width: 100%; margin-top: 6px; background: var(--accent); border-color: transparent; color: #0b1020; font-weight: 650; }
.err { margin-top: 12px; color: #ff8080; font-size: 13px; min-height: 18px; }

/* ── 聊天 ── */
#chat {
  height: 100%;
  display: flex;
  flex-direction: column;
}
header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: calc(10px + env(safe-area-inset-top)) 14px 10px;
  border-bottom: 1px solid var(--line);
  background: rgba(23, 26, 33, .92);
  backdrop-filter: blur(8px);
}
header .name { font-weight: 600; flex: 1; }
header select {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #10131a;
  padding: 7px 10px;
}
header button { padding: 7px 10px; font-size: 13px; }

#log {
  flex: 1;
  overflow-y: auto;
  padding: 16px 14px 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  -webkit-overflow-scrolling: touch;
}
.row { display: flex; }
.row.me { justify-content: flex-end; }
.bubble {
  max-width: min(78%, 520px);
  padding: 9px 13px;
  border-radius: 16px;
  line-height: 1.55;
  font-size: 15px;
  white-space: pre-wrap;
  word-break: break-word;
  animation: pop .16s ease-out;
}
@keyframes pop {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: none; }
}
.me .bubble { background: var(--accent); color: #0b1020; border-bottom-right-radius: 5px; }
.them .bubble { background: var(--bubble-them); border-bottom-left-radius: 5px; }
.sys { color: var(--muted); font-size: 12px; text-align: center; width: 100%; }

/* 正在输入：做成对话流里的一个空气泡，而不是底部一行小字 */
.bubble.typing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 13px 15px;
}
.bubble.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c9d1e0;
  animation: hop 1s infinite ease-in-out;
}
.bubble.typing i:nth-child(2) { animation-delay: .15s; }
.bubble.typing i:nth-child(3) { animation-delay: .3s; }
@keyframes hop {
  0%, 60%, 100% { transform: translateY(0); opacity: .45; }
  30% { transform: translateY(-4px); opacity: 1; }
}

footer {
  display: flex;
  gap: 8px;
  padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--panel);
}
#text {
  flex: 1;
  min-height: 42px;
  max-height: 120px;
  padding: 11px 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #10131a;
  resize: none;
}
#text:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
#send { background: var(--accent); border-color: transparent; color: #0b1020; font-weight: 650; }
#dbgBtn { font-size: 12px; padding: 6px 9px; }
#dbg {
  position: fixed;
  left: 0; right: 0; bottom: 0;
  max-height: 38vh;
  overflow: auto;
  margin: 0;
  padding: 8px 10px;
  background: rgba(0, 0, 0, .94);
  color: #9fe8b0;
  font: 11px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-all;
  border-top: 1px solid #2a3a2e;
  z-index: 99;
}
</style>
</head>
<body>

<section id="login">
  <form class="card" id="loginForm" autocomplete="on">
    <h1>Alpha</h1>
    <p>登录后开始对话</p>
    <input id="email" type="email" placeholder="邮箱" autocomplete="username" required>
    <input id="password" type="password" placeholder="密码" autocomplete="current-password" required>
    <button type="submit" id="loginBtn">登录</button>
    <div class="err" id="loginErr"></div>
  </form>
</section>

<section id="chat" hidden>
  <header>
    <span class="name" id="personaName">聊天</span>
    <select id="personaSelect" aria-label="选择人格"></select>
    <button type="button" id="exportBtn" title="导出这段对话">导出</button>
    <button type="button" id="dbgBtn" title="导出事件日志">日志</button>
    <button type="button" id="logoutBtn">退出</button>
  </header>
  <div id="log"></div>
  <footer>
    <textarea id="text" rows="1" placeholder="说点什么…"></textarea>
    <button type="button" id="send">发送</button>
  </footer>
</section>
<pre id="dbg" hidden></pre>

<script nonce="__CSP_NONCE__">
(function () {
  "use strict";

  var TOKEN_KEY = "alpha.token";
  var CLIENT_KEY = "alpha.client_id";
  var PERSONA_KEY = "alpha.persona_id";

  var state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    clientId: localStorage.getItem(CLIENT_KEY) || "",
    personaId: localStorage.getItem(PERSONA_KEY) || "",
    personas: [],
    ws: null,
    reconnectTimer: null,
    currentBubble: null,
    currentSegment: -1,
    seenChunks: 0,
    unanswered: 0,
    switching: false,
    needResync: false,
    lastMsgKey: ""
  };

  if (!state.clientId) {
    state.clientId = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(CLIENT_KEY, state.clientId);
  }

  /* ── 事件日志（#98）：把发生过的事记下来，出问题能回看 ── */
  var DBG_KEY = "alpha.debug";
  var DBG_MAX = 800;
  var DBG_PANEL = /[?&]debug=1/.test(location.search);
  var dbgBuf = [];
  try { dbgBuf = JSON.parse(localStorage.getItem(DBG_KEY) || "[]"); } catch (e) { dbgBuf = []; }
  if (!dbgBuf.length) dbgBuf = [];
  var dbgDirty = false;
  var dbgTimer = null;
  var dbgEl = null;

  function flushDbg() {
    dbgTimer = null;
    if (!dbgDirty) return;
    dbgDirty = false;
    try {
      localStorage.setItem(DBG_KEY, JSON.stringify(dbgBuf));
    } catch (e) {
      // 配额满了：丢一半再来一次
      dbgBuf = dbgBuf.slice(-Math.floor(DBG_MAX / 2));
      try { localStorage.setItem(DBG_KEY, JSON.stringify(dbgBuf)); } catch (e2) {}
    }
  }

  function dbg(k, d) {
    dbgBuf.push({ t: Date.now(), k: k, d: d || null });
    if (dbgBuf.length > DBG_MAX) dbgBuf.splice(0, dbgBuf.length - DBG_MAX);
    dbgDirty = true;
    if (!dbgTimer) dbgTimer = setTimeout(flushDbg, 800);
    if (DBG_PANEL) renderDbg();
  }

  function renderDbg() {
    if (!dbgEl) dbgEl = document.getElementById("dbg");
    if (!dbgEl) return;
    var tail = dbgBuf.slice(-14).map(function (e) {
      var d = e.d ? JSON.stringify(e.d) : "";
      if (d.length > 120) d = d.slice(0, 120) + "…";
      return new Date(e.t).toTimeString().slice(0, 8) + "  " + e.k + "  " + d;
    });
    dbgEl.textContent = tail.join("\n");
    dbgEl.scrollTop = dbgEl.scrollHeight;
  }

  function exportDbg() {
    flushDbg();
    var stamp = new Date().toISOString().replace(/[:.]/g, "-");
    var blob = new Blob([JSON.stringify(dbgBuf, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "alpha-debug-" + stamp + ".json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
  }

  var $ = function (id) { return document.getElementById(id); };
  var logEl = $("log");
  var textEl = $("text");
  var typingRow = null;

  /* 正在输入 = 对话流里一个空气泡。放在底部一行字上，用户视线根本扫不到。 */
  function setTyping(on) {
    if (on) {
      if (typingRow) return;
      typingRow = document.createElement("div");
      typingRow.className = "row them";
      var b = document.createElement("div");
      b.className = "bubble typing";
      for (var i = 0; i < 3; i++) b.appendChild(document.createElement("i"));
      typingRow.appendChild(b);
      logEl.appendChild(typingRow);
      scrollDown();
      return;
    }
    if (typingRow) {
      if (typingRow.parentNode) typingRow.parentNode.removeChild(typingRow);
      typingRow = null;
    }
  }

  function scrollDown() {
    logEl.scrollTop = logEl.scrollHeight;
  }

  function addBubble(who, text) {
    var row = document.createElement("div");
    row.className = "row " + who;
    var b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    row.appendChild(b);
    logEl.appendChild(row);
    scrollDown();
    return b;
  }

  function addSystem(text) {
    var d = document.createElement("div");
    d.className = "sys";
    d.textContent = text;
    logEl.appendChild(d);
    scrollDown();
  }

  function authHeaders(extra) {
    var h = extra || {};
    if (state.token) h["Authorization"] = "Bearer " + state.token;
    return h;
  }

  function api(path, options) {
    var opts = options || {};
    opts.headers = authHeaders(opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(path, opts).then(function (res) {
      dbg("http", { path: path.split("?")[0], status: res.status });
      if (res.status === 401) {
        clearSession();
        throw new Error("登录已过期");
      }
      return res;
    });
  }

  function clearSession() {
    state.token = "";
    localStorage.removeItem(TOKEN_KEY);
    if (state.ws) { try { state.ws.close(); } catch (e) {} state.ws = null; }
    $("chat").hidden = true;
    $("login").hidden = false;
  }

  /* ── 登录 ── */
  $("loginForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = $("loginBtn");
    var err = $("loginErr");
    err.textContent = "";
    btn.disabled = true;
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value, password: $("password").value })
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.detail || ("登录失败 " + res.status));
          return data;
        });
      })
      .then(function (data) {
        dbg("auth.login", { ok: true });
        state.token = data.access_token;
        localStorage.setItem(TOKEN_KEY, state.token);
        return boot();
      })
      .catch(function (e) { err.textContent = e.message || "登录失败"; })
      .then(function () { btn.disabled = false; });
  });

  $("logoutBtn").addEventListener("click", function () {
    api("/api/auth/logout", { method: "POST", body: {} }).catch(function () {});
    clearSession();
  });

  /* ── 人格 ── */
  function loadPersonas() {
    return api("/v1/models")
      .then(function (r) { return r.ok ? r.json() : { data: [] }; })
      .then(function (data) {
        state.personas = (data.data || [])
          .map(function (m) { return m.id; })
          .filter(function (id) { return id && id !== "alpha"; });
        var sel = $("personaSelect");
        sel.textContent = "";
        state.personas.forEach(function (id) {
          var o = document.createElement("option");
          o.value = id;
          o.textContent = id;
          sel.appendChild(o);
        });
        if (!state.personaId || state.personas.indexOf(state.personaId) < 0) {
          state.personaId = state.personas[0] || "";
        }
        sel.value = state.personaId;
        $("personaName").textContent = state.personaId || "聊天";
      });
  }

  $("personaSelect").addEventListener("change", function (ev) {
    switchPersona(ev.target.value);
  });

  /* 切人格 = 换一段关系：
     - 旧 WS 上可能还有上一轮的 chunk 在飞，直接重连把它掐掉
     - 服务端 session 也要重建（否则上游会复用旧 agent，人格不换） */
  function switchPersona(nextId) {
    var prev = state.personaId;
    if (nextId === prev) return;
    dbg("ui.switch_persona", { from: prev, to: nextId });

    state.personaId = nextId;
    localStorage.setItem(PERSONA_KEY, nextId);
    $("personaName").textContent = nextId;

    state.switching = true;
    state.needResync = false;
    state.unanswered = 0;
    state.lastMsgKey = "";
    setTyping(false);

    if (state.ws) {
      try { state.ws.close(); } catch (e) {}
      state.ws = null;
    }
    clearTimeout(state.reconnectTimer);

    resetConversation();
    loadHistory().then(function () {
      state.switching = false;
      connect();
    });
  }

  function resetConversation() {
    logEl.textContent = "";
    state.currentBubble = null;
    state.currentSegment = -1;
    state.seenChunks = 0;
  }

  /* ── 历史 ── */
  function loadHistory(opts) {
    var silent = !!(opts && opts.silent);
    if (!state.personaId) return Promise.resolve();
    if (!silent) resetConversation();
    return api(
      "/api/chat/history/" + encodeURIComponent(state.personaId) +
      "?client_id=" + encodeURIComponent(state.clientId) + "&limit=60"
    )
      .then(function (r) { return r.ok ? r.json() : { messages: [] }; })
      .then(function (data) {
        var msgs = data.messages || [];
        var key = msgs.length
          ? msgs[0].id + ":" + msgs[msgs.length - 1].id + ":" + msgs.length
          : "empty";
        if (silent && key === state.lastMsgKey) return false;  // 服务端没有新消息
        state.lastMsgKey = key;

        if (silent) resetConversation();   // 有变化才重绘
        if (!msgs.length) {
          addSystem("开始聊天吧");
          return;
        }
        msgs.forEach(function (m) {
          addBubble(m.role === "user" ? "me" : "them", m.content);
        });
        return true;
      })
      .catch(function () { if (!silent) addSystem("历史加载失败"); return false; });
  }

  /* ── WebSocket ── */
  function wsUrl() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/ws/chat/stream?token=" +
      encodeURIComponent(state.token);
  }

  /* 断线重连后对齐历史：服务端那一轮可能已经跑完并落了库，
     但 chunk 都发在断掉的连接上，前端什么都没收到。
     这里轮询几次历史，拿到就把界面补上。 */
  function resyncAfterReconnect(pending) {
    var tries = 0;
    function attempt() {
      loadHistory({ silent: true }).then(function (changed) {
        if (changed) {
          // 拿到了断线期间生成的回复
          state.unanswered = 0;
          setTyping(false);
          addSystem("已重新连接");
          return;
        }
        if (pending && tries < 5) {
          tries += 1;
          setTimeout(attempt, 4000);
          return;
        }
        state.unanswered = 0;
        setTyping(false);
        addSystem("已重新连接");
      });
    }
    attempt();
  }

  function connect() {
    if (state.ws && state.ws.readyState <= 1) return;
    var ws = new WebSocket(wsUrl());
    state.ws = ws;

    ws.onopen = function () {
      dbg("ws.open", {
        url: wsUrl().replace(/token=[^&]+/, "token=***"),
        resync: state.needResync,
        unanswered: state.unanswered
      });
      if (state.needResync) {
        state.needResync = false;
        resyncAfterReconnect(state.unanswered > 0);
      } else {
        addSystem("已连接");
      }
    };

    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { dbg("recv.bad", { raw: String(ev.data).slice(0, 200) }); return; }
      handle(msg);
    };

    ws.onclose = function (ev) {
      dbg("ws.close", { code: ev.code, reason: ev.reason, wasClean: ev.wasClean, buffered: state.unanswered });
      state.ws = null;
      setTyping(false);
      if (ev.code === 1008) {
        clearSession();
        return;
      }
      if (state.switching) {
        // 主动切换人格断开的，不重连也不提示
        return;
      }
      if (state.token) {
        // 还有没等到回复的消息 → 重连后要补历史，否则用户看着像「发了没人理」
        if (state.unanswered > 0) state.needResync = true;
        dbg("ws.reconnect_scheduled", {
          unanswered: state.unanswered, in_ms: 3000, reason: "unclean close"
        });
        addSystem("连接断开，3 秒后重连");
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = setTimeout(connect, 3000);
      }
    };

    ws.onerror = function () {
      dbg("ws.error", { readyState: ws.readyState });
    };
  }

  function newBubble() {
    var row = document.createElement("div");
    row.className = "row them";
    var b = document.createElement("div");
    b.className = "bubble";
    row.appendChild(b);
    logEl.appendChild(row);
    scrollDown();
    state.currentBubble = b;
    return b;
  }

  function handle(msg) {
    if (msg.type === "chunk") {
      dbg("recv.chunk", { s: msg.segment, i: msg.index, n: (msg.content || "").length, c: msg.content });
    } else if (msg.type === "chat_end") {
      dbg("recv.chat_end", {
        chunks: msg.chunks, segs: (msg.segments || []).length,
        first_chunk_ms: msg.first_chunk_ms, elapsed_ms: msg.elapsed_ms,
        reply: msg.reply
      });
    } else if (msg.type === "segment_end") {
      dbg("recv.segment_end", { s: msg.segment, len: (msg.reply || "").length, pause_ms: msg.pause_ms });
    } else {
      dbg("recv." + msg.type, msg.content ? { content: msg.content } : null);
    }
    switch (msg.type) {
      case "chat_start":
        setTyping(true);
        break;

      case "chunk": {
        setTyping(false);
        var seg = typeof msg.segment === "number" ? msg.segment : 0;
        if (!state.currentBubble || seg !== state.currentSegment) {
          state.currentSegment = seg;
          newBubble();
        }
        state.currentBubble.textContent += (msg.content || "");
        state.seenChunks += 1;
        scrollDown();
        break;
      }

      case "segment_end":
        state.currentBubble = null;
        state.currentSegment = -1;
        break;

      case "chat_end":
        state.unanswered = Math.max(0, state.unanswered - 1);
        setTyping(false);
        state.currentBubble = null;
        state.currentSegment = -1;
        if (!state.seenChunks && msg.reply) {
          addBubble("them", msg.reply);
        }
        state.seenChunks = 0;
        break;

      case "error":
        setTyping(false);
        addSystem("出错了：" + (msg.content || "未知错误"));
        break;

      case "pong":
        break;
    }
  }

  /* ── 发送 ── */
  function send() {
    var text = textEl.value.trim();
    if (!text) return;
    if (!state.ws || state.ws.readyState !== 1) {
      addSystem("还没连上，稍等");
      connect();
      return;
    }
    addBubble("me", text);
    textEl.value = "";
    textEl.style.height = "auto";
    state.seenChunks = 0;
    state.unanswered += 1;
    dbg("send.chat", {
      content: text, persona: state.personaId,
      client_id: state.clientId, unanswered: state.unanswered
    });
    state.ws.send(JSON.stringify({
      type: "chat",
      content: text,
      persona_id: state.personaId,
      client_id: state.clientId
    }));
    // 输入框已空 = 光标离开 → 让后端把防抖窗口从 3s 缩到 2s
    state.ws.send(JSON.stringify({ type: "typing", active: false }));
    setTyping(true);
  }

  $("send").addEventListener("click", send);
  textEl.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      send();
    }
  });
  textEl.addEventListener("input", function () {
    textEl.style.height = "auto";
    textEl.style.height = Math.min(textEl.scrollHeight, 120) + "px";
  });

  /* ── 导出对话（#50）── */
  $("exportBtn").addEventListener("click", function () {
    if (!state.personaId) return;
    var url = "/api/export/chat/" + encodeURIComponent(state.personaId) +
      "?client_id=" + encodeURIComponent(state.clientId) + "&format=md";
    dbg("ui.export_chat", { persona: state.personaId, format: "md" });
    fetch(url, { headers: authHeaders({}) })
      .then(function (res) {
        if (!res.ok) throw new Error("导出失败（" + res.status + "）");
        var name = "alpha-" + state.personaId + ".md";
        var cd = res.headers.get("content-disposition") || "";
        var hit = /filename="([^"]+)"/.exec(cd);
        if (hit) name = hit[1];
        return res.blob().then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = name;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
          addSystem("已导出 " + name);
        });
      })
      .catch(function (e) { addSystem(e.message || "导出失败"); });
  });

  /* ── 日志按钮 ── */
  $("dbgBtn").addEventListener("click", function () {
    exportDbg();
    addSystem("日志已导出（" + dbgBuf.length + " 条）");
  });

  if (DBG_PANEL) {
    var panel = $("dbg");
    if (panel) { panel.hidden = false; renderDbg(); }
  }
  dbg("app.start", {
    debug_panel: DBG_PANEL,
    has_token: !!state.token,
    persona: state.personaId || null,
    client_id: state.clientId
  });

  /* ── 启动 ── */
  function boot() {
    $("login").hidden = true;
    $("chat").hidden = false;
    return loadPersonas()
      .then(loadHistory)
      .then(function () {
        connect();
        textEl.focus();
      })
      .catch(function (e) {
        clearSession();
        $("loginErr").textContent = e.message || "加载失败";
      });
  }

  if (state.token) {
    boot();
  } else {
    $("login").hidden = false;
  }
})();
</script>
</body>
</html>
"""


def build_ui_router():
    """聊天页 `/`。FastAPI 延迟导入，便于零依赖测试。"""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    router = APIRouter(tags=["ui"], include_in_schema=False)

    @router.get("/", response_class=HTMLResponse)
    def app_page():
        nonce = secrets.token_urlsafe(16)
        html = APP_HTML.replace("__CSP_NONCE__", nonce)
        csp = (
            "default-src 'none'; "
            f"style-src 'nonce-{nonce}'; "
            f"script-src 'nonce-{nonce}'; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": csp,
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
