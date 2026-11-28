# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#46 极简聊天页 + #80 BYOK 模型设置：单文件 HTML，不依赖框架与构建。

设计约束：
- 只接 shell 的端点：认证 /api/auth/*、人格 /v1/models、历史 /api/chat/history、
  对话走 /ws/chat/stream（上游 /ws/chat 没有中间内容）
- BYOK 走 /api/me/providers*、/api/me/routing、/api/routes/catalog
- CSP nonce：页面内联样式与脚本都带 nonce，无内联事件属性
- token 存 localStorage（#82 会换成 HttpOnly cookie + CSRF）
- 视觉：仿 /glass 的深色极光背景 + 毛玻璃卡片 + 紫蓝青渐变
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
  --bg: #060711;
  --bg-2: #0a0c1a;
  --text: #f3f1ff;
  --muted: #a7a2c4;
  --violet: #8b5cf6;
  --blue: #5b7cfa;
  --cyan: #22d3ee;
  --green: #3fd08a;
  --red: #ff8f8f;
  --glass: rgba(22, 24, 45, .52);
  --stroke: rgba(255, 255, 255, .13);
  --stroke-soft: rgba(255, 255, 255, .07);
  --grad: linear-gradient(100deg, var(--violet), var(--blue) 55%, var(--cyan));
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { height: 100%; height: 100dvh; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
}
button, input, select, textarea { font: inherit; color: inherit; }
button {
  border: 1px solid var(--stroke);
  background: rgba(255, 255, 255, .05);
  border-radius: 12px;
  padding: 10px 14px;
  cursor: pointer;
}
button:active { transform: scale(.98); }
button:disabled { opacity: .5; cursor: default; }
svg { display: block; }

/* ── 极光背景 ── */
.aurora {
  position: fixed;
  inset: -20vmax;
  z-index: 0;
  overflow: hidden;
  background:
    radial-gradient(60% 50% at 50% 0%, rgba(91, 124, 250, .18), transparent 70%),
    linear-gradient(180deg, var(--bg-2), var(--bg) 60%);
}
.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: .5;
  will-change: transform;
}
.blob-violet {
  top: 8%; left: 6%;
  width: 46vmax; height: 46vmax;
  background: radial-gradient(circle at 40% 40%, rgba(139, 92, 246, .75), transparent 65%);
  animation: drift-a 22s ease-in-out infinite alternate;
}
.blob-blue {
  top: 40%; right: -6%;
  width: 42vmax; height: 42vmax;
  background: radial-gradient(circle at 50% 50%, rgba(91, 124, 250, .65), transparent 65%);
  animation: drift-b 26s ease-in-out infinite alternate;
}
.blob-cyan {
  bottom: -14%; left: 30%;
  width: 38vmax; height: 38vmax;
  background: radial-gradient(circle at 50% 50%, rgba(34, 211, 238, .45), transparent 65%);
  animation: drift-c 30s ease-in-out infinite alternate;
}
@keyframes drift-a { to { transform: translate3d(8vmax, 6vmax, 0) scale(1.12); } }
@keyframes drift-b { to { transform: translate3d(-10vmax, -6vmax, 0) scale(1.08); } }
@keyframes drift-c { to { transform: translate3d(6vmax, -8vmax, 0) scale(1.16); } }

/* ── 玻璃 ── */
.glass {
  border: 1px solid var(--stroke);
  border-radius: 18px;
  background:
    linear-gradient(150deg, rgba(255, 255, 255, .10), rgba(255, 255, 255, .02) 55%),
    var(--glass);
  box-shadow:
    0 24px 70px rgba(0, 0, 0, .42),
    inset 0 1px 0 rgba(255, 255, 255, .18);
  backdrop-filter: blur(22px) saturate(150%);
  -webkit-backdrop-filter: blur(22px) saturate(150%);
}
section { position: relative; z-index: 1; }

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
}
.card h1 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 650;
  background: var(--grad);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
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
  border: 1px solid var(--stroke);
  border-radius: 12px;
  background: rgba(8, 10, 22, .55);
}
.card input:focus { outline: 2px solid var(--blue); outline-offset: 1px; }
.btn-primary {
  width: 100%;
  margin-top: 6px;
  background: var(--grad);
  border: 0;
  color: #fff;
  font-weight: 650;
  box-shadow: 0 8px 24px rgba(91, 124, 250, .35);
}
.err { margin-top: 12px; color: var(--red); font-size: 13px; min-height: 18px; }
.linkbtn {
  width: 100%;
  margin-top: 10px;
  padding: 6px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* ── 注册弹窗 ── */
#registerDialog {
  width: min(92vw, 360px);
  padding: 0;
  border: 0;
  border-radius: 18px;
  background: transparent;
  color: var(--text);
}
#registerDialog::backdrop { background: rgba(4, 6, 12, .72); backdrop-filter: blur(4px); }
#registerDialog form { margin: 0; }
#registerDialog .card button.ghost {
  margin-top: 8px;
  background: transparent;
  border-color: var(--stroke);
  color: var(--muted);
  font-weight: 400;
}

/* ── 聊天 ── */
#chat {
  height: 100%;
  display: flex;
  flex-direction: column;
}
header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: calc(10px + env(safe-area-inset-top)) 12px 10px;
  border-bottom: 1px solid var(--stroke-soft);
  background: rgba(16, 18, 36, .55);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
}
header .name {
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
header select {
  border: 1px solid var(--stroke);
  border-radius: 10px;
  background: rgba(8, 10, 22, .6);
  padding: 7px 10px;
  max-width: 38vw;
}
header button { padding: 7px 10px; font-size: 13px; }
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  flex: 0 0 auto;
  border-radius: 10px;
  color: var(--muted);
}
.icon-btn:active { border-color: rgba(139, 92, 246, .55); color: var(--text); }

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
.me .bubble {
  background: linear-gradient(100deg, #7d5cf0, #5b7cfa);
  color: #fff;
  border-bottom-right-radius: 5px;
  box-shadow: 0 6px 20px rgba(91, 124, 250, .3);
}
.them .bubble {
  background: rgba(255, 255, 255, .06);
  border: 1px solid var(--stroke-soft);
  border-bottom-left-radius: 5px;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
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
  border-top: 1px solid var(--stroke-soft);
  background: rgba(16, 18, 36, .55);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
}
#text {
  flex: 1;
  min-height: 42px;
  font-size: 16px;
  line-height: 1.4;
  max-height: 120px;
  padding: 11px 14px;
  border: 1px solid var(--stroke);
  border-radius: 14px;
  background: rgba(8, 10, 22, .55);
  resize: none;
}
#text:focus { outline: 2px solid var(--blue); outline-offset: 1px; }
#send {
  background: var(--grad);
  border: 0;
  color: #fff;
  font-weight: 650;
  box-shadow: 0 8px 24px rgba(91, 124, 250, .35);
}
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

/* ── 页面头（模型设置 / 选择模型）── */
.page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: calc(10px + env(safe-area-inset-top)) 12px 10px;
  border-bottom: 1px solid var(--stroke-soft);
  background: rgba(16, 18, 36, .55);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
}
.page-title { flex: 1; text-align: center; font-weight: 650; font-size: 16px; }

/* ── 模型设置（BYOK）── */
#byok { height: 100%; display: flex; flex-direction: column; }
.byok-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 14px 14px calc(24px + env(safe-area-inset-bottom));
  -webkit-overflow-scrolling: touch;
}
.sec-title {
  margin: 18px 4px 10px;
  font-size: 13px;
  font-weight: 700;
  color: var(--muted);
  letter-spacing: .04em;
}
.sec-title:first-child { margin-top: 4px; }

.route-card {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  text-align: left;
}
.route-icon {
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  border-radius: 12px;
  display: grid;
  place-items: center;
  background: linear-gradient(150deg, rgba(139, 92, 246, .35), rgba(34, 211, 238, .18));
  border: 1px solid var(--stroke);
  color: #c4b5fd;
}
.route-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.route-name { font-weight: 650; font-size: 15px; }
.route-key {
  color: var(--muted);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.badge {
  flex: 0 0 auto;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .03em;
  border: 1px solid var(--stroke);
}
.badge.run { color: var(--green); border-color: rgba(63, 208, 138, .4); background: rgba(63, 208, 138, .1); }
.badge.free { color: var(--cyan); border-color: rgba(34, 211, 238, .4); background: rgba(34, 211, 238, .1); }
.badge.off { color: var(--muted); }
.badge.custom { color: #c4b5fd; border-color: rgba(139, 92, 246, .45); background: rgba(139, 92, 246, .12); }
.badge.platform { color: #a9bcff; border-color: rgba(91, 124, 250, .45); background: rgba(91, 124, 250, .12); }
.hint { margin: 8px 6px 0; color: var(--muted); font-size: 12px; }

.byok-form { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; font-weight: 650; color: var(--muted); }
.field-label em { font-style: normal; font-weight: 400; opacity: .8; }
.field input, .field select {
  width: 100%;
  padding: 11px 13px;
  border: 1px solid var(--stroke);
  border-radius: 12px;
  background: rgba(8, 10, 22, .55);
}
.field input:focus, .field select:focus { outline: 2px solid var(--blue); outline-offset: 1px; }
.key-row { display: flex; gap: 8px; }
.key-row input { flex: 1; }
.form-actions { display: flex; gap: 10px; }
.form-actions .btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.btn.ghost { background: transparent; border-color: var(--stroke); color: var(--text); }
.btn.primary {
  background: var(--grad);
  border: 0;
  color: #fff;
  font-weight: 650;
  box-shadow: 0 8px 24px rgba(91, 124, 250, .3);
}
.form-msg { min-height: 16px; font-size: 12.5px; }
.form-msg.ok { color: var(--green); }
.form-msg.err { color: var(--red); }

.key-list { padding: 6px 16px; }
.key-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid var(--stroke-soft);
}
.key-item:last-child { border-bottom: 0; }
.key-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.key-label {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.key-mask {
  color: var(--muted);
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.key-empty { padding: 18px 0; color: var(--muted); font-size: 13px; text-align: center; }

/* ── 选择模型 ── */
#picker { height: 100%; display: flex; flex-direction: column; }
.picker-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 14px 14px calc(24px + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
  gap: 12px;
  -webkit-overflow-scrolling: touch;
}
.search {
  width: 100%;
  padding: 11px 14px;
  border: 1px solid var(--stroke);
  border-radius: 14px;
  background: rgba(8, 10, 22, .55);
}
.search:focus { outline: 2px solid var(--blue); outline-offset: 1px; }
.group { padding: 6px 14px 10px; }
.group-head { display: flex; align-items: center; gap: 8px; padding: 10px 2px 8px; }
.group-name { font-weight: 700; font-size: 14px; }
.model-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 10px;
  margin: 2px 0;
  border-radius: 12px;
  text-align: left;
  background: transparent;
  border-color: transparent;
}
.model-row:active { background: rgba(255, 255, 255, .05); }
.model-row.active {
  background: rgba(139, 92, 246, .14);
  border-color: rgba(139, 92, 246, .5);
}
.radio {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  border-radius: 50%;
  border: 2px solid var(--muted);
  display: grid;
  place-items: center;
}
.radio::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: transparent; }
.model-row.active .radio, .default-route.active .radio { border-color: var(--violet); }
.model-row.active .radio::after, .default-route.active .radio::after {
  background: var(--violet);
  box-shadow: 0 0 8px rgba(139, 92, 246, .8);
}
.model-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag { flex: 0 0 auto; font-size: 11px; color: #c4b5fd; }
.picker-msg { min-height: 16px; margin: 0 4px; font-size: 12.5px; color: var(--red); }
.default-route {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  text-align: left;
}
.default-route.active {
  border-color: rgba(34, 211, 238, .55);
  background: rgba(34, 211, 238, .08);
}
.dr-main { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.dr-name { font-weight: 650; font-size: 14px; }
.dr-sub { color: var(--muted); font-size: 12px; }

@media (prefers-reduced-motion: reduce) {
  .blob { animation: none; }
  *, *::before, *::after {
    animation-duration: .01ms !important;
    transition-duration: .01ms !important;
  }
}
</style>
</head>
<body>
<div class="aurora" aria-hidden="true">
  <span class="blob blob-violet"></span>
  <span class="blob blob-blue"></span>
  <span class="blob blob-cyan"></span>
</div>

<section id="login">
  <form class="card glass" id="loginForm" autocomplete="on">
    <h1>Alpha</h1>
    <p>登录后开始对话</p>
    <input id="email" type="email" placeholder="邮箱" autocomplete="username" required>
    <input id="password" type="password" placeholder="密码" autocomplete="current-password" required>
    <button type="submit" id="loginBtn" class="btn-primary">登录</button>
    <div class="err" id="loginErr"></div>
    <button type="button" class="linkbtn" id="registerOpen">有邀请码？注册</button>
  </form>
</section>

<dialog id="registerDialog" aria-labelledby="registerTitle">
  <form class="card glass" id="registerForm" autocomplete="on">
    <h1 id="registerTitle">注册</h1>
    <p>需要有效邀请码</p>
    <input id="regEmail" type="email" placeholder="邮箱" autocomplete="username" required>
    <input id="regPassword" type="password" placeholder="密码（至少 8 位）" autocomplete="new-password" minlength="8" required>
    <input id="regInvite" type="text" placeholder="邀请码" autocomplete="one-time-code" required>
    <button type="submit" id="registerBtn" class="btn-primary">注册并登录</button>
    <button type="button" class="ghost" id="registerClose">取消</button>
    <div class="err" id="registerErr"></div>
  </form>
</dialog>

<section id="chat" hidden>
  <header>
    <span class="name" id="personaName">聊天</span>
    <select id="personaSelect" aria-label="选择人格"></select>
    <button type="button" id="settingsBtn" class="icon-btn" title="模型设置" aria-label="模型设置">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
    </button>
    <button type="button" id="exportBtn" title="导出这段对话">导出</button>
    <button type="button" id="dbgBtn" title="导出事件日志">日志</button>
    <button type="button" id="logoutBtn">退出</button>
  </header>
  <div id="log"></div>
  <footer>
    <textarea id="text" rows="1" placeholder="说点什么…" enterkeyhint="send"></textarea>
    <button type="button" id="send">发送</button>
  </footer>
</section>

<section id="byok" hidden>
  <div class="page-head">
    <button type="button" id="byokBack" class="icon-btn" aria-label="返回聊天">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
    </button>
    <span class="page-title">模型设置</span>
    <span class="icon-btn" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/></svg>
    </span>
  </div>
  <div class="byok-scroll">
    <h2 class="sec-title">当前模型</h2>
    <button type="button" id="currentRouteCard" class="glass route-card">
      <span class="route-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/></svg>
      </span>
      <span class="route-main">
        <span class="route-name" id="routeName">加载中…</span>
        <span class="route-key" id="routeKey"></span>
      </span>
      <span class="badge off" id="routeBadge">…</span>
    </button>
    <p class="hint" id="routeHint"></p>

    <h2 class="sec-title" id="byokFormTitle">添加自己的 Key</h2>
    <form class="glass byok-form" id="byokForm" autocomplete="off">
      <label class="field">
        <span class="field-label">服务商</span>
        <select id="provSelect"></select>
      </label>
      <label class="field">
        <span class="field-label">API Key</span>
        <span class="key-row">
          <input id="apiKey" type="password" placeholder="粘贴你的密钥" autocomplete="new-password">
          <button type="button" id="keyToggle" class="icon-btn" aria-label="显示或隐藏密钥"></button>
        </span>
      </label>
      <label class="field">
        <span class="field-label">Base URL <em>（内置服务商已预填）</em></span>
        <input id="baseUrl" type="url" placeholder="https://api.example.com/v1" autocomplete="url" inputmode="url">
      </label>
      <label class="field">
        <span class="field-label">模型名称 <em>（多个用逗号分隔）</em></span>
        <input id="modelNames" type="text" placeholder="如 qwen-plus-latest" autocomplete="off">
      </label>
      <div class="form-actions">
        <button type="button" id="probeBtn" class="btn ghost">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
          测试连接
        </button>
        <button type="submit" id="saveBtn" class="btn primary">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
          保存
        </button>
      </div>
      <div class="form-msg" id="formMsg" role="status"></div>
    </form>

    <h2 class="sec-title">已配置的 Key</h2>
    <div class="glass key-list" id="keyList"></div>
  </div>
</section>

<section id="picker" hidden>
  <div class="page-head">
    <button type="button" id="pickerBack" class="icon-btn" aria-label="返回模型设置">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
    </button>
    <span class="page-title">选择模型</span>
    <button type="button" id="pickerClose" class="icon-btn" aria-label="关闭">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
    </button>
  </div>
  <div class="picker-scroll">
    <input id="pickerSearch" class="search" type="search" placeholder="搜索模型或服务商" autocomplete="off">
    <div id="pickerGroups"></div>
    <p class="picker-msg" id="pickerMsg" role="status"></p>
    <button type="button" id="defaultRouteBtn" class="glass default-route">
      <span class="radio"></span>
      <span class="dr-main">
        <span class="dr-name">使用默认模型（免费）</span>
        <span class="dr-sub">平台自动为你选择合适的模型</span>
      </span>
    </button>
  </div>
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

  /* ── 视图切换：login / chat / byok / picker ── */
  var VIEWS = ["login", "chat", "byok", "picker"];
  function showView(name) {
    VIEWS.forEach(function (id) { $(id).hidden = id !== name; });
    if (name === "byok") loadByok();
    if (name === "picker") loadPicker();
  }

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
    showView("login");
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

  /* ── 注册 ── */
  var regDialog = $("registerDialog");
  $("registerOpen").addEventListener("click", function () {
    $("registerErr").textContent = "";
    regDialog.showModal();
    $("regEmail").focus();
  });
  $("registerClose").addEventListener("click", function () { regDialog.close(); });
  $("registerForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = $("registerBtn");
    var err = $("registerErr");
    err.textContent = "";
    btn.disabled = true;
    fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("regEmail").value.trim(),
        password: $("regPassword").value,
        invite_code: $("regInvite").value.trim()
      })
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.detail || ("注册失败 " + res.status));
          return data;
        });
      })
      .then(function (data) {
        dbg("auth.register", { ok: true });
        state.token = data.access_token;
        localStorage.setItem(TOKEN_KEY, state.token);
        regDialog.close();
        $("regPassword").value = "";
        $("regInvite").value = "";
        return boot();
      })
      .catch(function (e) { err.textContent = e.message || "注册失败"; })
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
        addSystem(msg.content || msg.detail || "未知错误");
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
    lastHeight = 0;
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
  // 每次输入都读 scrollHeight 会强制同步布局，手机上就是那种「卡一下」。
  // 折到 rAF 里，且高度没变就不写 style。
  var heightPending = false;
  var lastHeight = 0;
  textEl.addEventListener("input", function () {
    if (heightPending) return;
    heightPending = true;
    requestAnimationFrame(function () {
      heightPending = false;
      textEl.style.height = "auto";
      var h = Math.min(textEl.scrollHeight, 120);
      if (h !== lastHeight) {
        lastHeight = h;
        textEl.style.height = h + "px";
      }
    });
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

  /* ── 模型设置（BYOK）── */
  var EYE_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c6.5 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3.5 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" x2="22" y1="2" y2="22"/></svg>';
  var TRASH_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="m19 6-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';

  var byok = {
    catalog: [],
    routing: null,
    providers: [],
    editingId: ""
  };

  function prettyProvider(id) {
    return String(id || "").split("-").map(function (part) {
      return part.charAt(0).toUpperCase() + part.slice(1);
    }).join("-");
  }

  function catalogHit(id) {
    return byok.catalog.filter(function (p) { return p.id === id; })[0] || null;
  }

  function setFormMsg(kind, text) {
    var el = $("formMsg");
    el.className = "form-msg" + (kind ? " " + kind : "");
    el.textContent = text;
  }

  function loadByok() {
    setFormMsg("", "");
    return Promise.all([
      api("/api/routes/catalog").then(function (r) { return r.ok ? r.json() : { providers: [] }; }),
      api("/api/me/routing").then(function (r) { return r.ok ? r.json() : null; }),
      api("/api/me/providers").then(function (r) { return r.ok ? r.json() : { providers: [] }; })
    ]).then(function (results) {
      byok.catalog = results[0].providers || [];
      byok.routing = results[1];
      byok.providers = results[2].providers || [];
      renderRouteCard();
      fillProviderSelect();
      renderKeyList();
    }).catch(function (e) { dbg("byok.load", { error: e.message }); });
  }

  function renderRouteCard() {
    var r = byok.routing;
    var nameEl = $("routeName");
    var keyEl = $("routeKey");
    var badge = $("routeBadge");
    var hint = $("routeHint");
    badge.className = "badge";
    if (!r || !r.route) {
      nameEl.textContent = "未配置模型";
      keyEl.textContent = "";
      badge.classList.add("off");
      badge.textContent = "未配置";
      hint.textContent = "添加自己的 Key，或使用平台默认模型";
      return;
    }
    var hit = catalogHit(r.route.provider);
    var isCustom = !!(hit && hit.custom);
    nameEl.textContent = isCustom ? (hit.label || hit.id) : prettyProvider(r.route.provider);
    keyEl.textContent = r.route_key || "";
    if (r.source === "default") {
      badge.classList.add("free");
      badge.textContent = "免费";
      hint.textContent = "平台默认模型，无需配置";
    } else {
      badge.classList.add("run");
      badge.textContent = "运行中";
      hint.textContent = isCustom
        ? "你的 Key 加密存储，只显示末四位"
        : "当前使用平台 Key";
    }
  }

  function fillProviderSelect() {
    var sel = $("provSelect");
    sel.textContent = "";
    byok.catalog.filter(function (p) { return !p.custom; }).forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id;
      o.textContent = prettyProvider(p.id);
      sel.appendChild(o);
    });
    var custom = document.createElement("option");
    custom.value = "__custom__";
    custom.textContent = "自定义（其他 OpenAI 兼容端点）";
    sel.appendChild(custom);
    // 默认选中当前路由的 provider（若是内置），否则第一个
    var def = byok.routing && byok.routing.route ? byok.routing.route.provider : "";
    var found = null;
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === def) { found = sel.options[i].value; break; }
    }
    sel.value = found || (sel.options[0] ? sel.options[0].value : "");
    onProvChange();
  }

  function onProvChange() {
    var sel = $("provSelect");
    var hit = byok.catalog.filter(function (p) { return p.id === sel.value; })[0];
    if (hit) {
      $("baseUrl").value = hit.base_url || "";
      $("modelNames").value = (hit.models || [])[0] || "";
    } else {
      $("baseUrl").value = "";
      $("modelNames").value = "";
    }
  }

  $("provSelect").addEventListener("change", onProvChange);

  $("keyToggle").addEventListener("click", function () {
    var input = $("apiKey");
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    this.innerHTML = show ? EYE_OFF_SVG : EYE_SVG;
    this.setAttribute("aria-label", show ? "隐藏密钥" : "显示密钥");
  });
  $("keyToggle").innerHTML = EYE_SVG;

  $("probeBtn").addEventListener("click", function () {
    var baseUrl = $("baseUrl").value.trim();
    var key = $("apiKey").value.trim();
    if (!baseUrl || !key) {
      setFormMsg("err", "先填 Base URL 和 API Key");
      return;
    }
    var btn = $("probeBtn");
    btn.disabled = true;
    setFormMsg("", "正在测试连接…");
    api("/api/me/providers/probe", { method: "POST", body: { base_url: baseUrl, api_key: key } })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (res.ok) setFormMsg("ok", "连接成功：" + res.d.message);
        else setFormMsg("err", "连接失败：" + (res.d.detail || res.d.message || "未知错误"));
      })
      .catch(function (e) { setFormMsg("err", e.message || "测试连接失败"); })
      .then(function () { btn.disabled = false; });
  });

  $("byokForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var baseUrl = $("baseUrl").value.trim();
    var key = $("apiKey").value.trim();
    if (!baseUrl) { setFormMsg("err", "请填写 Base URL"); return; }
    if (!key) { setFormMsg("err", "请填写 API Key"); return; }
    var models = $("modelNames").value.split(/[,，\s]+/).filter(Boolean);
    var btn = $("saveBtn");
    btn.disabled = true;
    setFormMsg("", "正在保存…");

    var promise;
    if (byok.editingId) {
      promise = api("/api/me/providers/" + encodeURIComponent(byok.editingId), {
        method: "PATCH",
        body: { api_key: key, base_url: baseUrl, models: models }
      });
    } else {
      var prov = $("provSelect").value;
      var label;
      if (prov === "__custom__") {
        try { label = new URL(baseUrl).hostname; } catch (e) { label = "自定义"; }
      } else {
        label = prettyProvider(prov);
      }
      promise = api("/api/me/providers", {
        method: "POST",
        body: { label: label, base_url: baseUrl, api_key: key, models: models, probe: true }
      });
    }
    promise
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || "保存失败");
        setFormMsg("ok", byok.editingId ? "Key 已更新" : "已保存");
        $("apiKey").value = "";
        stopReconfigure();
        loadByok();
      })
      .catch(function (e) { setFormMsg("err", e.message || "保存失败"); })
      .then(function () { btn.disabled = false; });
  });

  function startReconfigure(p) {
    byok.editingId = p.id;
    $("byokFormTitle").textContent = "重新配置 Key · " + p.label;
    $("baseUrl").value = p.base_url;
    $("modelNames").value = (p.models || []).join(", ");
    $("apiKey").value = "";
    setFormMsg("", "");
    $("byokForm").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function stopReconfigure() {
    byok.editingId = "";
    $("byokFormTitle").textContent = "添加自己的 Key";
  }

  function renderKeyList() {
    var list = $("keyList");
    list.textContent = "";
    if (!byok.providers.length) {
      var empty = document.createElement("div");
      empty.className = "key-empty";
      empty.textContent = "还没有配置 Key";
      list.appendChild(empty);
      return;
    }
    byok.providers.forEach(function (p) {
      var item = document.createElement("div");
      item.className = "key-item";

      var main = document.createElement("div");
      main.className = "key-main";
      var label = document.createElement("span");
      label.className = "key-label";
      label.textContent = p.label + (p.models && p.models.length ? " · " + p.models[0] : "");
      var mask = document.createElement("span");
      mask.className = "key-mask";
      mask.textContent = p.has_key ? "sk-" + "••••••••••••" + p.key_last4 : "Key 已撤销";
      main.appendChild(label);
      main.appendChild(mask);

      var actions = document.createElement("div");
      actions.style.display = "flex";
      actions.style.gap = "8px";
      if (p.revoked) {
        var re = document.createElement("button");
        re.type = "button";
        re.className = "btn ghost";
        re.style.cssText = "padding: 6px 10px; font-size: 12px;";
        re.textContent = "重新配置";
        re.addEventListener("click", function () { startReconfigure(p); });
        actions.appendChild(re);
      }
      var del = document.createElement("button");
      del.type = "button";
      del.className = "icon-btn";
      del.setAttribute("aria-label", "删除 " + p.label);
      del.innerHTML = TRASH_SVG;
      del.addEventListener("click", function () { removeProvider(p, del); });
      actions.appendChild(del);

      item.appendChild(main);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  function removeProvider(p, btn) {
    if (!confirm("删除 " + p.label + " 的 Key？")) return;
    btn.disabled = true;
    api("/api/me/providers/" + encodeURIComponent(p.id), { method: "DELETE" })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || "删除失败");
        loadByok();
      })
      .catch(function (e) {
        setFormMsg("err", e.message);
        btn.disabled = false;
      });
  }

  /* ── 选择模型 ── */
  var picker = { catalog: [], routing: null };

  function loadPicker() {
    $("pickerMsg").textContent = "";
    $("pickerSearch").value = "";
    return Promise.all([
      api("/api/routes/catalog").then(function (r) { return r.ok ? r.json() : { providers: [] }; }),
      api("/api/me/routing").then(function (r) { return r.ok ? r.json() : null; })
    ]).then(function (results) {
      picker.catalog = results[0].providers || [];
      picker.routing = results[1];
      renderPicker("");
    }).catch(function (e) { $("pickerMsg").textContent = e.message || "加载失败"; });
  }

  function renderPicker(query) {
    var q = (query || "").trim().toLowerCase();
    var wrap = $("pickerGroups");
    wrap.textContent = "";
    var current = picker.routing && picker.routing.route_key ? picker.routing.route_key : "";
    var shown = 0;

    picker.catalog.forEach(function (p) {
      // 平台 provider 看平台 Key 是否可用；自定义 provider 看用户 Key
      var usable = p.custom ? p.has_key : p.available;
      if (!usable) return;
      var name = (p.custom ? p.label : prettyProvider(p.id)) || p.id;
      var nameHit = name.toLowerCase().indexOf(q) >= 0 || p.id.toLowerCase().indexOf(q) >= 0;
      var models = (p.models || []).filter(function (m) {
        return !q || nameHit || m.toLowerCase().indexOf(q) >= 0;
      });
      if (!models.length) return;
      shown += 1;

      var group = document.createElement("section");
      group.className = "glass group";
      var head = document.createElement("header");
      head.className = "group-head";
      var gname = document.createElement("span");
      gname.className = "group-name";
      gname.textContent = name;
      head.appendChild(gname);
      var badge = document.createElement("span");
      badge.className = "badge " + (p.custom ? "custom" : "platform");
      badge.textContent = p.custom ? "已配置" : "平台";
      head.appendChild(badge);
      group.appendChild(head);

      models.forEach(function (m) {
        var key = p.id + "/" + m;
        var row = document.createElement("button");
        row.type = "button";
        row.className = "model-row" + (key === current ? " active" : "");
        var radio = document.createElement("span");
        radio.className = "radio";
        var mname = document.createElement("span");
        mname.className = "model-name";
        mname.textContent = m;
        row.appendChild(radio);
        row.appendChild(mname);
        if (key === current) {
          var tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = "当前";
          row.appendChild(tag);
        }
        row.addEventListener("click", function () { chooseRoute(p.id, m, row); });
        group.appendChild(row);
      });
      wrap.appendChild(group);
    });

    if (!shown) {
      var none = document.createElement("div");
      none.className = "key-empty";
      none.textContent = q ? "没有匹配的模型" : "暂无可用模型，先添加自己的 Key";
      wrap.appendChild(none);
    }

    var defBtn = $("defaultRouteBtn");
    defBtn.className = "glass default-route" +
      (picker.routing && picker.routing.source === "default" ? " active" : "");
  }

  function chooseRoute(provider, model, row) {
    $("pickerMsg").textContent = "";
    row.disabled = true;
    api("/api/me/routing", {
      method: "PUT",
      body: { provider: provider, model: model, fallback: [] }
    })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || "切换失败");
        dbg("ui.choose_route", { route: provider + "/" + model });
        showView("byok");
      })
      .catch(function (e) {
        $("pickerMsg").textContent = e.message || "切换失败";
        row.disabled = false;
      });
  }

  $("defaultRouteBtn").addEventListener("click", function () {
    $("pickerMsg").textContent = "";
    api("/api/me/routing", { method: "DELETE" })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || "操作失败");
        dbg("ui.choose_default_route", {});
        showView("byok");
      })
      .catch(function (e) { $("pickerMsg").textContent = e.message || "操作失败"; });
  });

  $("pickerSearch").addEventListener("input", function (ev) { renderPicker(ev.target.value); });
  $("pickerBack").addEventListener("click", function () { showView("byok"); });
  $("pickerClose").addEventListener("click", function () { showView("byok"); });
  $("byokBack").addEventListener("click", function () { showView("chat"); });
  $("settingsBtn").addEventListener("click", function () { showView("byok"); });
  $("currentRouteCard").addEventListener("click", function () { showView("picker"); });

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
