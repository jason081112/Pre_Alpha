# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""最小登录页：只负责登录冒烟，不接聊天 UI。

页面使用同源 `/api/auth/*`，token 暂存 localStorage；正式聊天页接入时
应换成 HttpOnly cookie + CSRF 防护。
"""

from __future__ import annotations

import secrets

LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>Alpha 登录</title>
<style nonce="__CSP_NONCE__">
:root {
  color-scheme: dark;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #0f1115;
  color: #e8eaf0;
}
.card {
  width: 100%;
  max-width: 360px;
  padding: 28px;
  border: 1px solid #282c36;
  border-radius: 18px;
  background: #171a21;
  box-shadow: 0 18px 50px rgba(0, 0, 0, .35);
}
.brand {
  margin-bottom: 26px;
  color: #9aa4b2;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .18em;
  text-transform: uppercase;
}
h1 { margin: 0 0 6px; font-size: 26px; }
.muted { margin: 0 0 22px; color: #9aa4b2; font-size: 14px; }
label { display: block; margin: 14px 0 0; color: #c7ccd6; font-size: 14px; }
input {
  width: 100%;
  margin-top: 7px;
  padding: 12px 13px;
  border: 1px solid #303643;
  border-radius: 10px;
  background: #101319;
  color: #f2f4f8;
  font: inherit;
  outline: none;
}
input:focus { border-color: #6b8cff; box-shadow: 0 0 0 3px rgba(107, 140, 255, .18); }
button {
  width: 100%;
  margin-top: 20px;
  padding: 12px 14px;
  border: 0;
  border-radius: 10px;
  background: #6b8cff;
  color: #fff;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
button:disabled { cursor: wait; opacity: .55; }
button.secondary { background: #2a303c; }
.error {
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 107, 107, .45);
  border-radius: 9px;
  background: rgba(255, 107, 107, .1);
  color: #ffb3b3;
  font-size: 13px;
  line-height: 1.45;
}
details { margin-top: 22px; border-top: 1px solid #282c36; padding-top: 16px; }
summary { color: #9aa4b2; cursor: pointer; font-size: 14px; }
.hint { margin: 22px 0 0; color: #747d8c; font-size: 12px; line-height: 1.55; }
.ok {
  margin: 10px 0 8px;
  color: #7ee2a8;
  font-size: 64px;
  font-weight: 800;
  letter-spacing: .04em;
  line-height: 1;
}
#ok-email { min-height: 22px; margin: 0 0 8px; color: #c7ccd6; overflow-wrap: anywhere; }
</style>
</head>
<body>
<main class="card">
  <div class="brand">Alpha</div>

  <section id="auth-view">
    <h1>登录</h1>
    <p class="muted">邀请制内测</p>

    <form id="login-form">
      <label for="login-email">邮箱
        <input id="login-email" name="email" type="email" autocomplete="username" required>
      </label>
      <label for="login-password">密码
        <input id="login-password" name="password" type="password" autocomplete="current-password" minlength="8" required>
      </label>
      <button id="login-submit" type="submit">登录</button>
    </form>

    <div id="error" class="error" role="alert" aria-live="polite" hidden></div>

    <details>
      <summary>有邀请码？注册</summary>
      <form id="register-form">
        <label for="register-email">邮箱
          <input id="register-email" name="email" type="email" autocomplete="username" required>
        </label>
        <label for="register-password">密码（至少 8 位）
          <input id="register-password" name="password" type="password" autocomplete="new-password" minlength="8" required>
        </label>
        <label for="register-invite">邀请码
          <input id="register-invite" name="invite_code" type="text" autocomplete="one-time-code" required>
        </label>
        <button id="register-submit" type="submit">注册并登录</button>
      </form>
    </details>
  </section>

  <section id="ok-view" hidden>
    <div class="ok" role="status">OK</div>
    <p id="ok-email"></p>
    <button id="logout" class="secondary" type="button">退出</button>
  </section>

  <p class="hint">这里只做登录冒烟，不接聊天页。原始聊天记录不上传。</p>
</main>

<script nonce="__CSP_NONCE__">
(() => {
  "use strict";

  const ACCESS = "alpha_access";
  const REFRESH = "alpha_refresh";
  const EMAIL = "alpha_email";

  const $ = (id) => document.getElementById(id);
  const setError = (message) => {
    const box = $("error");
    box.textContent = message || "";
    box.hidden = !message;
  };

  const clearTokens = () => {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
    localStorage.removeItem(EMAIL);
  };

  const saveTokens = (data) => {
    if (data && data.access_token) localStorage.setItem(ACCESS, data.access_token);
    if (data && data.refresh_token) localStorage.setItem(REFRESH, data.refresh_token);
    const email = data && data.user && data.user.email;
    if (email) localStorage.setItem(EMAIL, email);
  };

  const authHeaders = () => {
    const token = localStorage.getItem(ACCESS);
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const request = async (path, options = {}) => {
    let response;
    try {
      response = await fetch(path, { cache: "no-store", ...options });
    } catch (_) {
      throw new Error("无法连接服务器");
    }

    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }

    if (!response.ok) {
      const detail = body && (body.detail || body.error);
      throw new Error(typeof detail === "string" ? detail : `请求失败 (${response.status})`);
    }
    return body;
  };

  const showAuth = () => {
    $("ok-view").hidden = true;
    $("auth-view").hidden = false;
    $("login-password").value = "";
    $("register-password").value = "";
  };

  const showOk = (email) => {
    $("auth-view").hidden = true;
    $("ok-view").hidden = false;
    $("ok-email").textContent = email || localStorage.getItem(EMAIL) || "已登录";
    setError("");
  };

  const refreshSession = async () => {
    const refresh = localStorage.getItem(REFRESH);
    if (!refresh) return false;
    try {
      const data = await request("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      saveTokens(data);
      return true;
    } catch (_) {
      clearTokens();
      return false;
    }
  };

  const restore = async () => {
    if (!localStorage.getItem(ACCESS) && !localStorage.getItem(REFRESH)) return;
    try {
      const data = await request("/api/auth/me", { headers: authHeaders() });
      showOk(data && data.user && data.user.email);
      return;
    } catch (_) {
      // access 可能过期，下面用 refresh 换一次。
    }

    if (await refreshSession()) {
      try {
        const data = await request("/api/auth/me", { headers: authHeaders() });
        showOk(data && data.user && data.user.email);
        return;
      } catch (_) {
        clearTokens();
      }
    }
    showAuth();
  };

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    const button = $("login-submit");
    button.disabled = true;
    try {
      const data = await request("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: $("login-email").value.trim(),
          password: $("login-password").value,
        }),
      });
      saveTokens(data);
      showOk(data && data.user && data.user.email);
    } catch (error) {
      setError(error.message);
    } finally {
      button.disabled = false;
    }
  });

  $("register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    const button = $("register-submit");
    button.disabled = true;
    try {
      const data = await request("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: $("register-email").value.trim(),
          password: $("register-password").value,
          invite_code: $("register-invite").value.trim(),
        }),
      });
      saveTokens(data);
      showOk(data && data.user && data.user.email);
    } catch (error) {
      setError(error.message);
    } finally {
      button.disabled = false;
    }
  });

  $("logout").addEventListener("click", async () => {
    const access = localStorage.getItem(ACCESS);
    const refresh = localStorage.getItem(REFRESH);
    clearTokens();
    showAuth();
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(access ? { Authorization: `Bearer ${access}` } : {}),
        },
        body: JSON.stringify({ refresh_token: refresh || null }),
      });
    } catch (_) {
      // 本地已清空；服务端撤销失败不阻塞退出。
    }
  });

  restore();
})();
</script>
</body>
</html>
"""


def build_page_router():
    """返回公开登录页路由；FastAPI 依赖延迟导入，便于零依赖测试。"""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    router = APIRouter(tags=["auth"], include_in_schema=False)

    @router.get("/login", response_class=HTMLResponse)
    def login_page():
        nonce = secrets.token_urlsafe(16)
        html = LOGIN_HTML.replace("__CSP_NONCE__", nonce)
        csp = (
            "default-src 'none'; "
            f"style-src 'nonce-{nonce}'; "
            f"script-src 'nonce-{nonce}'; "
            "connect-src 'self'; "
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
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            },
        )

    return router
