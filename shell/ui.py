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
.card input, .card select, .card textarea {
  width: 100%;
  margin-bottom: 10px;
  padding: 12px 14px;
  border: 1px solid var(--stroke);
  border-radius: 12px;
  background: rgba(8, 10, 22, .55);
}
.card input:focus, .card select:focus, .card textarea:focus { outline: 2px solid var(--blue); outline-offset: 1px; }
.card textarea { resize: vertical; }
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

/* ── #102 订阅 ── */
#subscribe {
  height: 100%;
  display: flex;
  flex-direction: column;
}
#subscribe .sub-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 16px 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
#subscribe .card { max-width: 420px; }
#subscribe .card h2 { margin: 0 0 6px; font-size: 18px; font-weight: 650; }
#subscribe .card > p { margin: 0 0 16px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.price-toggle {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 16px;
}
.price-toggle .seg {
  padding: 12px 8px;
  border: 1px solid var(--stroke);
  border-radius: 12px;
  background: rgba(8, 10, 22, .45);
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
}
.price-toggle .seg.on {
  border-color: var(--blue);
  background: rgba(91, 124, 250, .16);
  color: #fff;
  box-shadow: 0 0 0 1px rgba(91, 124, 250, .35) inset;
}
.price-toggle .seg .save {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  font-weight: 400;
  color: var(--cyan);
}
.sub-features {
  margin: 0 0 18px;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
  color: var(--fg);
}
.sub-features li { display: flex; gap: 8px; line-height: 1.5; }
.sub-features li::before { content: "✓"; color: var(--cyan); font-weight: 700; }
#subMsg.err { min-height: 18px; }
#subscribe .linkbtn { max-width: 420px; }

/* ── #102 支付完成：全屏确认 ── */
#payDone {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(900px 600px at 50% 20%, rgba(91, 124, 250, .3), transparent 62%),
    radial-gradient(700px 520px at 80% 82%, rgba(34, 211, 238, .16), transparent 60%),
    rgba(5, 7, 17, .84);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  animation: payFade .28s ease-out;
}
#payDone[hidden] { display: none; }
@keyframes payFade { from { opacity: 0; } to { opacity: 1; } }
.pay-card {
  width: min(100%, 380px);
  padding: 36px 26px 26px;
  text-align: center;
  border-radius: 24px;
  border: 1px solid var(--stroke);
  background: linear-gradient(180deg, rgba(26, 30, 56, .9), rgba(10, 13, 28, .94));
  box-shadow: 0 30px 80px rgba(0, 0, 0, .52), 0 0 0 1px rgba(255, 255, 255, .03) inset;
  animation: payPop .42s cubic-bezier(.2, .9, .25, 1.1);
}
@keyframes payPop {
  from { opacity: 0; transform: translateY(14px) scale(.96); }
  to { opacity: 1; transform: none; }
}
.pay-ring {
  width: 84px;
  height: 84px;
  margin: 0 auto;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 50% 40%, rgba(91, 124, 250, .35), transparent 68%),
    rgba(255, 255, 255, .04);
  border: 1px solid rgba(120, 150, 255, .38);
  box-shadow: 0 0 34px rgba(91, 124, 250, .35), 0 0 0 6px rgba(91, 124, 250, .08);
}
.pay-ring svg { stroke: var(--cyan); stroke-dasharray: 30; stroke-dashoffset: 30; }
.pay-ring.ok svg { animation: payCheck .5s .12s ease-out forwards; }
@keyframes payCheck { to { stroke-dashoffset: 0; } }
.pay-ring.busy svg { display: none; }
.pay-card h2 { margin: 18px 0 8px; font-size: 19px; font-weight: 650; }
.pay-card p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.65; }
.pay-meta {
  margin-top: 14px !important;
  padding-top: 14px;
  border-top: 1px solid var(--stroke-soft);
  color: var(--fg) !important;
  font-size: 13px;
}
.pay-spin {
  width: 22px;
  height: 22px;
  margin: 20px auto 0;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, .16);
  border-top-color: var(--cyan);
  animation: paySpin .8s linear infinite;
}
.pay-spin[hidden] { display: none; }
@keyframes paySpin { to { transform: rotate(360deg); } }
.pay-card .btn-primary { width: 100%; margin-top: 20px; }
.pay-card .linkbtn { margin-top: 10px; }

/* ── #76 隐私同意 ── */
#consent {
  height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  overflow-y: auto;
}
.consent-card { max-width: 420px; }
.consent-list {
  margin: 0 0 14px;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}
.consent-list li { margin-bottom: 6px; }
.consent-list strong { color: var(--text); }
.consent-list a, .consent-check a { color: var(--cyan); }
.consent-check {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin: 14px 0 6px;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.5;
}
.consent-check input {
  width: 16px;
  height: 16px;
  margin: 2px 0 0;
  padding: 0;
  flex: 0 0 auto;
  accent-color: var(--blue);
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
  background: var(--mood-tint, rgba(255, 255, 255, .06));
  border: 1px solid var(--stroke-soft);
  border-radius: var(--mood-radius, 16px);
  border-bottom-left-radius: 5px;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.sys { color: var(--muted); font-size: 12px; text-align: center; width: 100%; }



/* #75 注销：冷静期横幅 + 危险按钮 */
.del-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  font-size: 12.5px;
  color: #ffd9a8;
  background: rgba(255, 176, 82, .12);
  border-bottom: 1px solid rgba(255, 176, 82, .28);
}
.del-banner[hidden] { display: none; }
.del-banner span { flex: 1; min-width: 0; }
.del-banner button {
  flex: 0 0 auto;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255, 176, 82, .45);
  background: transparent;
  color: #ffd9a8;
  font-size: 12px;
}
.btn-danger {
  background: linear-gradient(100deg, #d1435b, #b3304a) !important;
  box-shadow: 0 8px 24px rgba(209, 67, 91, .3) !important;
}

/* 页眉收纳：手机上只留名字 + 试用徽章 + 更多，其余进底部菜单 */
header .icon-btn { flex: 0 0 auto; }
#menuSheet {
  position: fixed;
  inset: 0;
  z-index: 55;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
#menuSheet[hidden] { display: none; }
.sheet-mask {
  position: absolute;
  inset: 0;
  background: rgba(4, 6, 16, .58);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: fadeIn .18s ease-out;
}
.sheet-card {
  position: relative;
  width: 100%;
  max-width: 460px;
  margin: 0 8px calc(8px + env(safe-area-inset-bottom));
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-radius: 18px;
  border: 1px solid var(--stroke);
  background: rgba(18, 20, 38, .94);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  box-shadow: 0 20px 60px rgba(0, 0, 0, .5);
  animation: sheetUp .22s cubic-bezier(.2, .9, .25, 1);
}
.sheet-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 13px 14px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text);
  font-size: 14.5px;
  text-align: left;
  cursor: pointer;
}
.sheet-item:hover, .sheet-item:active { background: rgba(255, 255, 255, .07); }
.sheet-item.danger { color: #ff8a9a; }
.sheet-item svg { flex: 0 0 auto; opacity: .72; }
.sheet-item + .sheet-item { border-top: 1px solid rgba(255, 255, 255, .045); }
@keyframes sheetUp {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: none; }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .sheet-card, .sheet-mask { animation: none; }
}

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

/* ── 动效系统（#80）：涟漪 / 聚焦 / 视图切换 / 输入 glow ── */
button, .btn, .linkbtn, .ghost, .icon-btn, .route-card {
  position: relative;
  overflow: hidden;
}
.ripple {
  position: absolute;
  width: 110px; height: 110px;
  margin: -55px 0 0 -55px;
  border-radius: 50%;
  background: rgba(255, 255, 255, .28);
  transform: scale(0);
  animation: ripple .6s ease-out forwards;
  pointer-events: none;
}
@keyframes ripple {
  to { transform: scale(2.8); opacity: 0; }
}
:focus-visible {
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
}
section:not([hidden]) { animation: view-in .28s ease-out; }
@keyframes view-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
input, textarea, select {
  transition: box-shadow .18s ease, border-color .18s ease;
}
input:focus, textarea:focus, select:focus {
  border-color: rgba(34, 211, 238, .55);
  box-shadow: 0 0 0 3px rgba(34, 211, 238, .18), 0 0 18px rgba(34, 211, 238, .12);
}

@media (prefers-reduced-motion: reduce) {
  .blob { animation: none; }
  .ripple { display: none; }
  section:not([hidden]) { animation: none; }
  *, *::before, *::after {
    animation-duration: .01ms !important;
    transition-duration: .01ms !important;
  }
}
</style>
<!-- PWA（#80）：manifest + 主题色 + 状态栏 -->
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#060711">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Alpha">
<script nonce="__CSP_NONCE__">
(function () {
  "use strict";
  var swUrl = "/sw.js";
  if (window.trustedTypes && trustedTypes.createPolicy) {
    try {
      swUrl = trustedTypes.createPolicy("alpha-sw", {
        createScriptURL: function (u) { return u; }
      }).createScriptURL(swUrl);
    } catch (e) {}
  }
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register(swUrl).catch(function () {});
    });
  }
})();
</script>
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
    <button type="button" class="linkbtn" id="registerOpen">注册新账号</button>
  </form>
</section>

<dialog id="registerDialog" aria-labelledby="registerTitle">
  <form class="card glass" id="registerForm" autocomplete="on">
    <h1 id="registerTitle">注册</h1>
    <p>新用户可免费聊 30 轮</p>
    <input id="regEmail" type="email" placeholder="邮箱" autocomplete="username" required>
    <input id="regPassword" type="password" placeholder="密码（至少 8 位）" autocomplete="new-password" minlength="8" required>
    <input id="regInvite" type="text" placeholder="邀请码（有就填，可留空）" autocomplete="one-time-code">
    <button type="submit" id="registerBtn" class="btn-primary">注册并登录</button>
    <button type="button" class="ghost" id="registerClose">取消</button>
    <div class="err" id="registerErr"></div>
  </form>
</dialog>

<dialog id="reportDialog" aria-labelledby="reportTitle">
  <form class="card glass" id="reportForm">
    <h1 id="reportTitle">举报不当内容</h1>
    <p>举报会关联你的账号，便于我们核查与回复。恶意举报可能导致限权。</p>
    <select id="reportCategory" aria-label="问题类型">
      <option value="sexual">色情或露骨性内容</option>
      <option value="violence">暴力、血腥或煽动伤害</option>
      <option value="hate">仇恨与骚扰</option>
      <option value="csam">涉及未成年人</option>
      <option value="impersonation">冒充真人或虚假信息</option>
      <option value="copyright">侵犯知识产权</option>
      <option value="self_harm">自伤或自杀相关内容</option>
      <option value="other">其他</option>
    </select>
    <textarea id="reportDetail" rows="3" placeholder="发生了什么（可选）" maxlength="2000"></textarea>
    <button type="submit" id="reportSubmit" class="btn-primary">提交举报</button>
    <button type="button" class="ghost" id="reportClose">取消</button>
    <div class="err" id="reportErr"></div>
    <p class="sys">紧急情况请直接联系当地急救（120 / 110），不要等待举报处理。</p>
  </form>
</dialog>

<dialog id="deleteDialog" aria-labelledby="deleteTitle">
  <form class="card glass" id="deleteForm">
    <h1 id="deleteTitle">注销账号</h1>
    <p>提交后进入 <span id="deleteDays">7</span> 天冷静期，到期会删除：</p>
    <ul class="consent-list">
      <li>账号、登录凭证与隐私同意记录</li>
      <li>全部聊天记录、人格记忆与状态</li>
      <li>试用量、订阅记录、举报与处置记录</li>
      <li>你配置的模型服务商与密钥</li>
    </ul>
    <p>如已订阅，请先邮件 <a href="mailto:prealpha@163.com">prealpha@163.com</a> 取消订阅；注销不会自动退款。冷静期内可随时撤销。</p>
    <input id="deleteConfirm" type="text" placeholder="输入「删除」两个字确认" autocomplete="off">
    <button type="submit" id="deleteSubmit" class="btn-primary btn-danger">确认注销</button>
    <button type="button" class="ghost" id="deleteClose">取消</button>
    <div class="err" id="deleteErr"></div>
  </form>
</dialog>

<section id="consent" hidden>
  <div class="card glass consent-card">
    <h1>隐私说明</h1>
    <p>开始对话前，请先了解你的数据怎么走。</p>
    <ul class="consent-list">
      <li><strong>存在哪</strong>：对话记录保存在你的账号与部署的数据目录，不训练、不出售。</li>
      <li><strong>发给谁</strong>：本轮对话与人格设定所需的上下文会发给所选模型服务商（可能位于境外），不含你的账号凭证。</li>
      <li><strong>不训练 / 不分享</strong>：不会用于训练模型，也不会分享给无关第三方。</li>
      <li><strong>可导出 / 可删除</strong>：随时导出对话，随时删除账号与数据。</li>
    </ul>
    <p>当前 beta 免费。完整条款见《隐私政策》。</p>
    <label class="consent-check">
      <input id="consentAgree" type="checkbox">
      <span>我已阅读并同意 <a href="/legal/privacy.html" target="_blank" rel="noopener">《隐私政策》</a>（版本 <span id="consentVersion">1.2</span>）</span>
    </label>
    <button type="button" id="consentBtn" class="btn-primary" disabled>开始使用</button>
    <button type="button" class="linkbtn" id="consentLogout">退出登录</button>
    <div class="err" id="consentErr"></div>
  </div>
</section>

<section id="chat" hidden>
  <header>
    <span class="name" id="personaName">聊天</span>
    <select id="personaSelect" aria-label="选择人格"></select>
    <button type="button" id="trialBadge" class="badge" title="免费试用剩余轮数">…</button>
    <button type="button" id="menuBtn" class="icon-btn" title="更多" aria-label="更多" aria-haspopup="menu">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg>
    </button>
  </header>

  <div id="delBanner" class="del-banner" hidden>
    <span id="delBannerText"></span>
    <button type="button" id="delUndo">撤销</button>
  </div>

  <div id="menuSheet" hidden>
    <div class="sheet-mask" data-menu-close></div>
    <div class="sheet-card" role="dialog" aria-label="更多操作">
      <button type="button" id="settingsBtn" class="sheet-item" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        <span>模型设置</span>
      </button>
      <button type="button" id="reportBtn" class="sheet-item" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22V4a1 1 0 0 1 1-1h9l-1 3h6a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-7l-1 3H5"/></svg>
        <span>举报不当内容</span>
      </button>
      <button type="button" id="exportBtn" class="sheet-item" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
        <span>导出这段对话</span>
      </button>
      <button type="button" id="dbgBtn" class="sheet-item" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/></svg>
        <span>事件日志</span>
      </button>
      <button type="button" id="deleteBtn" class="sheet-item danger" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
        <span>注销账号</span>
      </button>
      <button type="button" id="logoutBtn" class="sheet-item danger" data-menu-close>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>
        <span>退出登录</span>
      </button>
      <button type="button" id="menuCancel" class="sheet-item" data-menu-close>取消</button>
    </div>
  </div>
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
<section id="subscribe" hidden>
  <div class="page-head">
    <button type="button" id="subBack" class="icon-btn" aria-label="返回聊天">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
    </button>
    <span class="page-title">订阅</span>
    <span class="icon-btn" aria-hidden="true"></span>
  </div>
  <div class="sub-scroll">
    <div class="glass card">
      <h2>BYOK 订阅</h2>
      <p>免费 30 轮试用已经结束。订阅后不限条数，用你自己的 API Key。</p>
      <div class="price-toggle" role="radiogroup" aria-label="订阅周期">
        <button type="button" id="subMonthly" class="seg on" data-interval="monthly">$9 / 月</button>
        <button type="button" id="subYearly" class="seg" data-interval="yearly">$90 / 年<span class="save">省 $18</span></button>
      </div>
      <ul class="sub-features" id="subFeatures"></ul>
      <label class="field">
        <span class="field-label">促销码（可选）</span>
        <input id="promoCode" type="text" maxlength="64" autocomplete="off" placeholder="有开发者优惠码就填这里">
      </label>
      <button type="button" id="subBuy" class="btn-primary">去支付</button>
      <p class="hint" id="subMsg" role="status"></p>
      <p class="hint">支付由 Waffo Pancake 处理，本站不接触卡号。可随时取消，到期前仍可使用。</p>
    </div>
    <button type="button" id="subChat" class="linkbtn">先回聊天</button>
  </div>
</section>
<div id="payDone" hidden role="dialog" aria-modal="true" aria-labelledby="payTitle">
  <div class="pay-card">
    <div class="pay-ring" id="payRing">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m4.5 12.5 5 5 10-11"/></svg>
    </div>
    <h2 id="payTitle">正在确认订阅…</h2>
    <p id="payText">支付已完成，正在和支付服务核对，通常几秒内完成。</p>
    <p class="pay-meta" id="payMeta" hidden></p>
    <div class="pay-spin" id="paySpin"></div>
    <button type="button" id="payContinue" class="btn-primary" hidden>开始聊天</button>
    <button type="button" id="payRetry" class="linkbtn" hidden>还没生效？再查一次</button>
  </div>
</div>
<pre id="dbg" hidden></pre>

<script nonce="__CSP_NONCE__">
(function () {
  "use strict";

  /* ── #82 Trusted Types ──
     CSP 已开启 require-trusted-types-for 'script'，innerHTML 等注入点
     必须经过命名策略。当前页面只有静态 SVG 图标需要写 HTML。 */
  var htmlPolicy = null;
  if (window.trustedTypes && trustedTypes.createPolicy) {
    try {
      htmlPolicy = trustedTypes.createPolicy("alpha-html", {
        createHTML: function (s) { return s; }
      });
    } catch (e) { htmlPolicy = null; }
  }
  function setHTML(el, markup) {
    el.innerHTML = htmlPolicy ? htmlPolicy.createHTML(markup) : markup;
  }

  /* ── #82 错误脱敏 ──
     界面、日志里不出现 Key / Bearer / query 里的凭证。 */
  function redact(s) {
    return String(s == null ? "" : s)
      .replace(/Bearer\s+[^\s"']+/gi, "Bearer ***")
      .replace(/([?&](?:token|key|api_key|code)=)[^&\s"']+/gi, "$1***")
      .replace(/\b(sk|pk|ak)-[A-Za-z0-9_-]{6,}/g, "$1-***");
  }

  var TOKEN_KEY = "alpha.token";
  var CLIENT_KEY = "alpha.client_id";
  var PERSONA_KEY = "alpha.persona_id";
  // 界面上显示名字，不显示内部 id（#99 收窄后只剩小兰）
  var PERSONA_LABELS = { xiaolan: "小兰", luna: "露娜", linzhou: "林舟" };
  function personaLabel(id) {
    if (!id) return "聊天";
    return PERSONA_LABELS[id] || id;
  }

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
    lastMsgKey: "",
    billing: null,
    subInterval: "monthly",
    consent: { accepted: false, required_version: "1.2" }
  };

  if (!state.clientId) {
    state.clientId = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(CLIENT_KEY, state.clientId);
  }

  /* ── #95 节奏调度器 ──
     WS chunk 的边界是 LLM token 边界，不是说话节奏。
     chunk → 字符队列 → rAF 单循环按标点延迟消费 → 每帧一次 DOM 写入。
     不用每字符 setTimeout（中端安卓会掉帧）。 */
  var nowMs = (window.performance && performance.now)
    ? function () { return performance.now(); }
    : function () { return Date.now(); };
  var raf = window.requestAnimationFrame
    ? function (fn) { return window.requestAnimationFrame(fn); }
    : function (fn) { return setTimeout(function () { fn(nowMs()); }, 16); };
  var caf = window.cancelAnimationFrame
    ? function (id) { window.cancelAnimationFrame(id); }
    : function (id) { clearTimeout(id); };
  var REDUCE_MOTION = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var FAST_RENDER = /[?&]fast=1/.test(location.search);  // 调试：关掉节奏直出

  var rhythm = {
    q: [],             // 字符队列 [{ch, seg}]
    due: 0,            // 下一个字符应出现的时刻
    rafId: 0,
    running: false,
    bubbles: [],       // 本轮气泡 [{seg, el}]
    rendered: 0,       // 本轮已渲染字符数
    pendingEnd: null,  // 等队列排空后对账的 chat_end
    typingPending: false,
    sentences: [],     // 最近两个已完成句子的长度（hurry 检测）
    sentenceLen: 0,
    mood: { frustration: 0, temperature: 0 },  // 上一轮状态，滞后一轮
    autoScroll: true,
    lastCh: "",
    t0: 0,
    lastAt: 0,
    gaps: []           // 字符间隔采样（dbg 统计）
  };

  function clamp01(v) { return Math.max(0, Math.min(1, v || 0)); }

  /* 延迟是「这个字出现之后」的呼吸。数值先拍脑袋，后调（#95 §2）。 */
  function charDelay(ch, prevCh) {
    if (FAST_RENDER || REDUCE_MOTION) return 0;
    if (ch === "\n") return prevCh === "\n" ? 800 : 600;
    if ("。！？!?…".indexOf(ch) >= 0) return 400;
    if ("，、,;；:：".indexOf(ch) >= 0) return 120;
    return 26 + (ch.charCodeAt(0) % 15);  // 26–40ms，确定性抖动（测试可复现）
  }

  /* 情绪驱动（#95 §4）：frustration 高 → 更快、停顿更短。
     引擎的 frustration 是各 drive 之和（无上界，5 已算高），temperature ≈ 0–0.3。 */
  function moodF() { return clamp01(rhythm.mood.frustration / 5); }
  function speedFactor() {
    var s = 1 + moodF() * 0.4;
    var hurry = rhythm.sentences.length >= 2 &&
      rhythm.sentences.every(function (n) { return n < 10; });
    if (hurry) s *= 1.5;  // 连续短句 → 「急着说」
    return s;
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
  var VIEWS = ["login", "consent", "chat", "byok", "picker", "subscribe"];
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
      var dur = (1.1 - moodF() * 0.5).toFixed(2) + "s";  // 烦躁 → 呼吸更急
      for (var i = 0; i < 3; i++) {
        var dot = document.createElement("i");
        dot.style.animationDuration = dur;
        b.appendChild(dot);
      }
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

  /* 用户上滑看历史时不抢滚动条；回到底部附近才恢复自动跟随。 */
  function scrollDown(force) {
    if (force || rhythm.autoScroll) logEl.scrollTop = logEl.scrollHeight;
  }

  function addBubble(who, text) {
    var row = document.createElement("div");
    row.className = "row " + who;
    var b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    row.appendChild(b);
    logEl.appendChild(row);
    scrollDown(true);
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

  /* ── #75 账号注销 ── */
  function loadDeleteStatus() {
    if (!state.token) return Promise.resolve(null);
    return api("/api/me/delete")
      .then(function (res) { return res.json(); })
      .then(function (d) {
        state.deletion = d;
        var banner = $("delBanner");
        if (!banner) return d;
        if (d && d.pending) {
          var when = d.execute_at ? new Date(d.execute_at) : null;
          var ok = when && !isNaN(when.getTime());
          var days = 0;
          if (ok) {
            days = Math.max(0, Math.ceil((when.getTime() - Date.now()) / 86400000));
          }
          $("delBannerText").textContent = "账号将在 " +
            (days || 1) + " 天后删除" +
            (ok ? "（" + when.toLocaleDateString("zh-CN") + "）" : "") +
            "，期间可以随时撤销。";
          banner.hidden = false;
        } else {
          banner.hidden = true;
        }
        return d;
      })
      .catch(function (e) {
        dbg("delete.load", { error: redact(e.message) });
        return null;
      });
  }

  var deleteDialog = $("deleteDialog");

  $("deleteBtn").addEventListener("click", function () {
    $("deleteErr").textContent = "";
    $("deleteConfirm").value = "";
    var d = state.deletion || {};
    if (d.cooling_days) $("deleteDays").textContent = d.cooling_days;
    deleteDialog.showModal();
    $("deleteConfirm").focus();
  });

  $("deleteClose").addEventListener("click", function () { deleteDialog.close(); });

  $("deleteForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = $("deleteSubmit");
    var err = $("deleteErr");
    err.textContent = "";
    btn.disabled = true;
    api("/api/me/delete", { method: "POST", body: { confirm: $("deleteConfirm").value.trim() } })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.detail || ("申请失败 " + res.status));
          return data;
        });
      })
      .then(function (data) {
        deleteDialog.close();
        dbg("delete.requested", { at: data.execute_at });
        loadDeleteStatus();
        addSystem("已提交注销申请，冷静期内可随时撤销。");
      })
      .catch(function (e) { err.textContent = redact(e.message); })
      .then(function () { btn.disabled = false; });
  });

  $("delUndo").addEventListener("click", function () {
    var btn = $("delUndo");
    btn.disabled = true;
    api("/api/me/delete/cancel", { method: "POST" })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.detail || "撤销失败");
          return data;
        });
      })
      .then(function (data) {
        dbg("delete.cancelled", data);
        loadDeleteStatus();
        addSystem("已撤销注销申请，账号保持正常。");
      })
      .catch(function (e) { addSystem(redact(e.message)); })
      .then(function () { btn.disabled = false; });
  });

  /* ── #102 订阅与试用 ── */
  function loadBilling() {
    if (!state.token) return Promise.resolve(null);
    return api("/api/billing/status")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.billing = data;
        updateTrialBadge();
        return data;
      })
      .catch(function (e) {
        dbg("billing.load", { error: redact(e.message) });
        return null;
      });
  }

  /* 支付回跳：本地没生效就请后端去支付服务对账（回调可能丢） */
  function syncBilling() {
    if (!state.token) return Promise.resolve(null);
    return api("/api/billing/sync", { method: "POST" })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.billing = data;
        updateTrialBadge();
        return data;
      })
      .catch(function (e) {
        dbg("billing.sync", { error: redact(e.message) });
        return state.billing;
      });
  }

  function updateTrialBadge() {
    var el = $("trialBadge");
    var b = state.billing;
    if (!el) return;
    if (!b) { el.hidden = true; return; }
    el.hidden = false;
    if (b.subscribed) {
      el.textContent = "BYOK";
      el.className = "badge on";
    } else {
      var left = b.trial ? b.trial.remaining : 0;
      var limit = b.trial ? b.trial.limit : 30;
      el.textContent = "试用 " + left;
      el.title = "免费试用剩余 " + left + "/" + limit + " 轮";
      el.className = "badge" + (b.can_chat ? "" : " off");
    }
  }

  function renderSubFeatures() {
    var list = $("subFeatures");
    if (!list || list.children.length) return;
    [
      "自带 API Key，聊天不消耗平台额度",
      "网页端与 OpenAI 兼容接口共用一份订阅",
      "不限消息条数，只受你自己的 Key 额度限制",
      "可随时取消，到期前仍可使用",
      "年付按 10 个月计价（省 2 个月）"
    ].forEach(function (text) {
      var li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    });
  }

  function showSubscribe() {
    renderSubFeatures();
    showView("subscribe");
    if (!state.billing) loadBilling();
  }

  function pickInterval(name) {
    state.subInterval = name;
    $("subMonthly").className = "seg" + (name === "monthly" ? " on" : "");
    $("subYearly").className = "seg" + (name === "yearly" ? " on" : "");
  }

  /* ── 支付完成：全屏确认 ──
     回调可能比跳转晚几秒，所以不给静态「成功页」，而是轮询到订阅真正生效。
     30 秒还没等到就说清楚，而不是假装成功。 */
  var payTimer = null;
  var payTries = 0;

  function payReset() {
    if (payTimer) { clearTimeout(payTimer); payTimer = null; }
    payTries = 0;
  }

  function payMetaText(b) {
    var sub = b && b.subscription;
    if (!sub) return "";
    var bits = [];
    if (sub.interval) bits.push(sub.interval === "yearly" ? "年付" : "月付");
    if (sub.current_period_end) {
      var d = new Date(sub.current_period_end);
      if (!isNaN(d.getTime())) bits.push("有效期至 " + d.toLocaleDateString("zh-CN"));
    }
    if (sub.email) bits.push(sub.email);
    return bits.join(" · ");
  }

  function payWaitState() {
    $("payRing").className = "pay-ring";
    $("payTitle").textContent = "正在确认订阅…";
    $("payText").textContent = "支付已完成，正在和支付服务核对，通常几秒内完成。";
    $("payMeta").hidden = true;
    $("paySpin").hidden = false;
    $("payRetry").hidden = true;
    $("payContinue").hidden = true;
  }

  function payDoneState(b) {
    payReset();
    $("payRing").className = "pay-ring ok";
    $("payTitle").textContent = "订阅已生效";
    $("payText").textContent = "BYOK 已开通，不限条数，可以继续聊天了。";
    var meta = payMetaText(b);
    $("payMeta").textContent = meta;
    $("payMeta").hidden = !meta;
    $("paySpin").hidden = true;
    $("payRetry").hidden = true;
    $("payContinue").hidden = false;
    updateTrialBadge();
    dbg("billing.confirmed");
  }

  function payTimeoutState() {
    $("payRing").className = "pay-ring";
    $("payTitle").textContent = "还没收到支付确认";
    $("payText").textContent = "支付可能还在处理中，生效后聊天会自动放开。也可以点下面再查一次。";
    $("paySpin").hidden = true;
    $("payRetry").hidden = false;
    $("payContinue").hidden = false;
  }

  function pollBillingForPay() {
    payReset();
    payWaitState();
    (function tick() {
      payTimer = null;
      syncBilling().then(function (b) {
        if (b && b.subscribed) { payDoneState(b); return; }
        payTries += 1;
        if (payTries >= 20) { payTimeoutState(); return; }
        payTimer = setTimeout(tick, 1500);
      });
    })();
  }

  function showPayConfirm() {
    showView("chat");
    $("payDone").hidden = false;
    pollBillingForPay();
  }

  function closePayConfirm() {
    payReset();
    $("payDone").hidden = true;
    if (window.history && history.replaceState) {
      history.replaceState(null, "", location.pathname);
    }
  }

  $("payContinue").addEventListener("click", closePayConfirm);
  $("payRetry").addEventListener("click", pollBillingForPay);

  $("trialBadge").addEventListener("click", showSubscribe);

  /* 页眉「更多」：底部菜单。手机上页眉只留 名字 + 试用徽章 + 更多。 */
  function openMenu() { $("menuSheet").hidden = false; dbg("ui.menu_open"); }
  function closeMenu() { $("menuSheet").hidden = true; }
  $("menuBtn").addEventListener("click", openMenu);
  $("menuSheet").addEventListener("click", function (ev) {
    var t = ev.target;
    if (t && t.closest && t.closest("[data-menu-close]")) closeMenu();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeMenu();
  });
  $("subBack").addEventListener("click", function () { showView("chat"); });
  $("subChat").addEventListener("click", function () { showView("chat"); });
  $("subMonthly").addEventListener("click", function () { pickInterval("monthly"); });
  $("subYearly").addEventListener("click", function () { pickInterval("yearly"); });

  $("subBuy").addEventListener("click", function () {
    var btn = $("subBuy");
    var msg = $("subMsg");
    msg.textContent = "";
    msg.className = "hint";
    btn.disabled = true;
    var promo = ($("promoCode").value || "").trim();
    api("/api/billing/checkout", {
      method: "POST",
      body: { interval: state.subInterval, promo_code: promo }
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.detail || ("创建支付失败 " + res.status));
          return data;
        });
      })
      .then(function (data) {
        dbg("billing.checkout", { interval: state.subInterval, promo: !!promo });
        if (!data.url) throw new Error("未拿到支付链接");
        location.href = data.url;
      })
      .catch(function (e) {
        msg.className = "err";
        msg.textContent = redact(e.message);
      })
      .then(function () { btn.disabled = false; });
  });

  /* ── #100 举报 ── */
  var reportDialog = $("reportDialog");
  $("reportBtn").addEventListener("click", function () {
    $("reportErr").textContent = "";
    $("reportDetail").value = "";
    reportDialog.showModal();
    $("reportDetail").focus();
  });
  $("reportClose").addEventListener("click", function () { reportDialog.close(); });
  $("reportForm").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var err = $("reportErr");
    var btn = $("reportSubmit");
    var category = $("reportCategory").value;
    err.textContent = "";
    btn.disabled = true;
    api("/api/moderation/report", {
      method: "POST",
      body: {
        category: category,
        detail: $("reportDetail").value.trim(),
        target: state.personaId || ""
      }
    })
      .then(function (res) {
        return res.json().then(function (d) {
          if (!res.ok) throw new Error(d.detail || ("提交失败 " + res.status));
          return d;
        });
      })
      .then(function (d) {
        dbg("report.submitted", { id: d.id, category: category });
        reportDialog.close();
        addSystem("举报已提交（编号 " + d.id + "），我们会尽快核查。");
      })
      .catch(function (e) { err.textContent = redact(e.message); })
      .then(function () { btn.disabled = false; });
  });

  /* ── #76 隐私同意 ── */
  function loadConsent() {
    return api("/api/me/consent")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.consent = d || { accepted: false, required_version: "1.2" };
        $("consentVersion").textContent = state.consent.required_version || "1.2";
        $("consentAgree").checked = false;
        $("consentBtn").disabled = true;
        $("consentErr").textContent = "";
        return state.consent;
      });
  }

  $("consentAgree").addEventListener("change", function () {
    $("consentBtn").disabled = !this.checked;
  });

  $("consentBtn").addEventListener("click", function () {
    var btn = $("consentBtn");
    var err = $("consentErr");
    err.textContent = "";
    if (!$("consentAgree").checked) {
      err.textContent = "请先勾选同意";
      return;
    }
    btn.disabled = true;
    api("/api/me/consent", {
      method: "POST",
      body: { version: state.consent.required_version, accepted: true }
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "保存同意记录失败");
          return d;
        });
      })
      .then(function (d) {
        dbg("consent.accepted", { version: d.accepted_version });
        state.consent.accepted = true;
        return boot();
      })
      .catch(function (e) {
        err.textContent = redact(e.message || "保存失败");
        btn.disabled = false;
      });
  });

  $("consentLogout").addEventListener("click", function () { clearSession(); });

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
          o.textContent = personaLabel(id);
          sel.appendChild(o);
        });
        if (!state.personaId || state.personas.indexOf(state.personaId) < 0) {
          state.personaId = state.personas[0] || "";
          if (state.personaId) localStorage.setItem(PERSONA_KEY, state.personaId);
        }
        // #99 单人格收窄：只有一个可选人格时不显示切换入口
        sel.hidden = state.personas.length <= 1;
        sel.value = state.personaId;
        $("personaName").textContent = personaLabel(state.personaId);
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
    $("personaName").textContent = personaLabel(nextId);

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
    resetRhythm();
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

  function bubbleFor(seg) {
    var last = rhythm.bubbles[rhythm.bubbles.length - 1];
    if (last && last.seg === seg) return last.el;
    var b = newBubble();
    rhythm.bubbles.push({ seg: seg, el: b });
    return b;
  }

  function renderBatch(items) {
    var groups = [];
    items.forEach(function (it) {
      var last = groups[groups.length - 1];
      if (last && last.seg === it.seg) last.text += it.ch;
      else groups.push({ seg: it.seg, text: it.ch });
    });
    groups.forEach(function (g) {
      bubbleFor(g.seg).textContent += g.text;
    });
    rhythm.rendered += items.length;
  }

  function enqueue(text, seg) {
    if (!text) return;
    if (FAST_RENDER || REDUCE_MOTION) {
      if (!rhythm.rendered) { setTyping(false); rhythm.typingPending = false; }
      bubbleFor(seg).textContent += text;
      rhythm.rendered += text.length;
      scrollDown();
      return;
    }
    for (var i = 0; i < text.length; i++) {
      rhythm.q.push({ ch: text.charAt(i), seg: seg });
    }
    if (!rhythm.running) {
      rhythm.running = true;
      rhythm.due = 0;  // 首字符前不额外加延迟（#95 §2）
      rhythm.rafId = raf(pump);
    }
  }

  /* rAF 单循环：每帧消费「到点」的字符，一次 DOM 写入。
     due = max(due, now) + delay —— 不追赶积压，LLM 一口气吐 30 字也按节奏出。 */
  function pump(ts) {
    rhythm.rafId = 0;
    var now = typeof ts === "number" ? ts : nowMs();
    var out = [];
    var prevCh = rhythm.lastCh || "";
    while (rhythm.q.length && now >= rhythm.due && out.length < 500) {
      var item = rhythm.q.shift();
      out.push(item);
      var d = charDelay(item.ch, prevCh);
      prevCh = item.ch;
      rhythm.due = Math.max(rhythm.due, now) + d / speedFactor();
      rhythm.sentenceLen += 1;
      if ("。！？!?\n".indexOf(item.ch) >= 0) {
        rhythm.sentences.push(rhythm.sentenceLen);
        if (rhythm.sentences.length > 2) rhythm.sentences.shift();
        rhythm.sentenceLen = 0;
      }
      if (rhythm.lastAt) {
        rhythm.gaps.push(Math.round(now - rhythm.lastAt));
        if (rhythm.gaps.length > 200) rhythm.gaps.shift();
      } else {
        rhythm.t0 = now;
      }
      rhythm.lastAt = now;
    }
    rhythm.lastCh = prevCh;
    if (out.length) {
      if (!rhythm.rendered) { setTyping(false); rhythm.typingPending = false; }
      renderBatch(out);
      scrollDown();
    }
    if (rhythm.q.length) {
      rhythm.rafId = raf(pump);
    } else {
      rhythm.running = false;
      if (rhythm.typingPending) { rhythm.typingPending = false; setTyping(true); }
      if (rhythm.pendingEnd) finishTurn();
    }
  }

  /* 中断①：发新消息 → 旧轮剩余字符立即补完（不丢字、不串轮），再开新轮。 */
  function flushRhythm() {
    if (rhythm.rafId) { caf(rhythm.rafId); rhythm.rafId = 0; }
    rhythm.running = false;
    rhythm.typingPending = false;
    var rest = {};
    var order = [];
    rhythm.q.forEach(function (it) {
      if (!(it.seg in rest)) { rest[it.seg] = ""; order.push(it.seg); }
      rest[it.seg] += it.ch;
    });
    rhythm.q = [];
    order.forEach(function (seg) {
      var text = rest[seg];
      bubbleFor(seg).textContent += text;
      rhythm.rendered += text.length;
    });
    if (rhythm.pendingEnd) finishTurn();
    else { setTyping(false); state.currentBubble = null; state.currentSegment = -1; }
  }

  /* 中断②：清屏/切人格 → 队列直接丢弃（历史会重绘，不需要补完）。 */
  function resetRhythm() {
    if (rhythm.rafId) { caf(rhythm.rafId); rhythm.rafId = 0; }
    rhythm.q = [];
    rhythm.running = false;
    rhythm.bubbles = [];
    rhythm.rendered = 0;
    rhythm.pendingEnd = null;
    rhythm.typingPending = false;
    rhythm.sentences = [];
    rhythm.sentenceLen = 0;
    rhythm.lastCh = "";
    rhythm.t0 = 0;
    rhythm.lastAt = 0;
    rhythm.gaps = [];
    rhythm.autoScroll = true;
    state.currentBubble = null;
    state.currentSegment = -1;
    state.seenChunks = 0;
  }

  /* 队列排空 + chat_end 已到 → 权威对账：DOM 文本归一化后必须 == reply。
     Segmenter 切段会丢弃分隔空行，所以比较前去掉全部空白。 */
  /* 后端可能在末尾追加软提示（#58）：渲染文本是 reply 的前缀，只补差额。
     norm() 去掉了全部空白，所以按「跳过空白比较字符」求剩余原文。 */
  function tailAfter(renderedNorm, reply) {
    var i = 0;
    var j = 0;
    while (i < renderedNorm.length && j < reply.length) {
      var ch = reply.charAt(j);
      if (/\s/.test(ch)) { j += 1; continue; }
      if (ch !== renderedNorm.charAt(i)) return null;
      i += 1;
      j += 1;
    }
    return i === renderedNorm.length ? reply.slice(j) : null;
  }

  function finishTurn() {
    var msg = rhythm.pendingEnd;
    rhythm.pendingEnd = null;
    setTyping(false);
    state.currentBubble = null;
    state.currentSegment = -1;
    if (!msg) return;
    var reply = msg.reply || "";
    var norm = function (s) { return String(s).replace(/\s+/g, ""); };
    var renderedText = rhythm.bubbles.map(function (b) { return b.el.textContent; }).join("\n\n");
    var matched = norm(renderedText) === norm(reply);
    if (!matched && reply) {
      var tail = tailAfter(norm(renderedText), reply);
      if (rhythm.rendered && tail !== null) {
        // 后端在末尾追加了软提示（#58）或补齐段尾 → 只补差额，整段重绘会闪
        tail.split(/\n{2,}/).forEach(function (part) {
          var clean = part.trim();
          if (clean) addBubble("them", clean);
        });
        dbg("rhythm.append_tail", { rendered: renderedText.length, added: tail.length });
        matched = true;
      } else if (rhythm.rendered) {
        // 渲染过但对不上 → 异常，撤掉本轮气泡整段替换
        rhythm.bubbles.forEach(function (b) {
          var row = b.el.parentNode;
          if (row && row.parentNode) row.parentNode.removeChild(row);
        });
        dbg("rhythm.mismatch", { rendered: renderedText.length, reply: reply.length });
        addBubble("them", reply);
      } else {
        // 一个 chunk 都没收到 → 整段兜底
        addBubble("them", reply);
      }
    }
    var avg = rhythm.gaps.length
      ? Math.round(rhythm.gaps.reduce(function (a, b) { return a + b; }, 0) / rhythm.gaps.length)
      : 0;
    dbg("rhythm.stats", {
      chars: rhythm.rendered,
      segs: rhythm.bubbles.length || (reply ? 1 : 0),
      ms: Math.round(rhythm.lastAt - rhythm.t0),
      avg_gap_ms: avg,
      verify: matched || !reply
    });
    state.seenChunks = 0;
    rhythm.bubbles = [];
    rhythm.rendered = 0;
    rhythm.sentences = [];
    rhythm.sentenceLen = 0;
    rhythm.lastCh = "";
    rhythm.t0 = 0;
    rhythm.lastAt = 0;
    rhythm.gaps = [];
  }

  /* 情绪驱动（#95 §4）：用上一轮 chat_end 的状态影响下一轮。 */
  function saveMood(msg) {
    var f = Number(msg.frustration);
    var t = Number(msg.temperature);
    rhythm.mood = {
      frustration: isFinite(f) ? f : 0,
      temperature: isFinite(t) ? t : 0
    };
    applyMood();
  }

  function applyMood() {
    var root = document.documentElement;
    if (!root || !root.style || !root.style.setProperty) return;
    var f = moodF();
    var t = clamp01(rhythm.mood.temperature / 0.3);
    root.style.setProperty("--mood-radius", (16 - f * 5).toFixed(1) + "px");
    root.style.setProperty("--mood-tint",
      "rgba(255," + Math.round(255 - t * 40) + "," + Math.round(255 - t * 70) + ",.06)");
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
        // 队列里还有上一段的字在渲染 → 等排空再挂「正在输入」，避免插错位置
        if (rhythm.running || rhythm.q.length) rhythm.typingPending = true;
        else setTyping(true);
        break;

      case "chunk": {
        var seg = typeof msg.segment === "number" ? msg.segment : 0;
        enqueue(msg.content || "", seg);
        state.seenChunks += 1;
        break;
      }

      case "segment_end":
        // 不立即清气泡：队列里可能还有字没渲染完。
        // 调度器消费到新段字符时自然开新气泡，上一条不消失（微信式堆叠）。
        break;

      case "chat_end":
        state.unanswered = Math.max(0, state.unanswered - 1);
        saveMood(msg);
        loadBilling();
        if (msg.guard) dbg("guard", msg.guard);
        rhythm.pendingEnd = msg;
        if (!rhythm.q.length && !rhythm.running) finishTurn();
        break;

      case "error":
        if (msg.code === "trial_exhausted") {
          dbg("billing.exhausted");
          flushRhythm();
          addSystem(msg.content || "免费试用已结束");
          showSubscribe();
          break;
        }
        flushRhythm();
        setTyping(false);
        if (msg.code === "consent_required") {
          // #76：服务端门禁兜底（例如政策版本更新后旧页面还在发消息）
          loadConsent().then(function (c) {
            if (!c.accepted) showView("consent");
          });
          break;
        }
        if (msg.code === "account_suspended") {
          // #100：账号被暂停 → 停掉输入，保留历史可读
          dbg("ws.suspended", {});
          addSystem(msg.content || "账号已被暂停，可通过 prealpha@163.com 申诉。");
          $("send").disabled = true;
          textEl.disabled = true;
          break;
        }
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
    // 上一轮还在渲染 → 立即补完剩余字符，不丢字、不串轮
    flushRhythm();
    rhythm.autoScroll = true;
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
  // 上滑看历史 → 暂停自动跟随；回到底部 40px 内 → 恢复
  logEl.addEventListener("scroll", function () {
    var gap = logEl.scrollHeight - logEl.scrollTop - (logEl.clientHeight || 0);
    rhythm.autoScroll = gap < 40;
  });

  // 每次输入都读 scrollHeight 会强制同步布局，手机上就是那种「卡一下」。
  // 折到 rAF 里，且高度没变就不写 style。
  var heightPending = false;
  var lastHeight = 0;
  textEl.addEventListener("input", function () {
    if (heightPending) return;
    heightPending = true;
    raf(function () {
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
    el.textContent = redact(text);
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
    }).catch(function (e) { dbg("byok.load", { error: redact(e.message) }); });
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
    setHTML(this, show ? EYE_OFF_SVG : EYE_SVG);
    this.setAttribute("aria-label", show ? "隐藏密钥" : "显示密钥");
  });
  setHTML($("keyToggle"), EYE_SVG);

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
    key = "";  // #82：请求体已序列化，局部变量不保留 Key
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
    // #82：请求体在 api() 里已同步序列化，立刻清掉 DOM 与局部变量里的 Key
    key = "";
    $("apiKey").value = "";
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
      setHTML(del, TRASH_SVG);
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

  /* ── 涟漪：事件委托统一绑定（覆盖动态生成的按钮） ── */
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("button, .btn, .linkbtn, .ghost, .icon-btn, .route-card");
    if (!btn || btn.disabled) return;
    var rect = btn.getBoundingClientRect();
    var r = document.createElement("span");
    r.className = "ripple";
    r.style.left = (ev.clientX - rect.left) + "px";
    r.style.top = (ev.clientY - rect.top) + "px";
    btn.appendChild(r);
    setTimeout(function () { r.remove(); }, 600);
  });

  /* ── 启动 ── */
  function boot() {
    $("login").hidden = true;
    return loadConsent()
      .then(function (c) {
        if (!c.accepted) {
          // #76：未同意当前版本政策前不进入对话
          showView("consent");
          $("consentAgree").focus();
          return;
        }
        showView("chat");
        return loadBilling()
          .then(function () {
            if (/[?&]subscribe=1/.test(location.search)) showSubscribe();
            if (/[?&]billing=success/.test(location.search)) showPayConfirm();
            return loadDeleteStatus();
          })
          .then(loadPersonas)
          .then(loadHistory)
          .then(function () {
            connect();
            textEl.focus();
          });
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
            "manifest-src 'self'; "
            # #82 Trusted Types：所有 HTML/脚本注入点必须经过命名策略
            "trusted-types alpha-html alpha-sw; "
            "require-trusted-types-for 'script'; "
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
