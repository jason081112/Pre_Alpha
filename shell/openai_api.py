# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI 兼容 HTTP 端点：`POST /v1/chat/completions`。

这里只做协议适配，不复制上游聊天逻辑：
- 复用 `main.get_or_create_session` / `ChatAgent.chat` / `ChatAgent.chat_stream`
- 复用 `shell.routing.RoutingLLMClient` 的用户路由与回退链
- 认证仍走 `AuthMiddleware`；限流仍走 `LimitsMiddleware`
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth.scope import current_auth_user
from .content_safety import PromptSiftClient, PromptSiftError, refusal_for
from .custom import CustomProviderStore
from .limits import LimitsService, RateLimitExceeded
from .openai_format import (
    OpenAIInputError,
    ReplyStreamExtractor,
    completion_body,
    error_body,
    extract_last_user_text,
    is_openai_path,
    resolve_model_alias,
    stream_chunk_body,
)
from .output_guard import SAFE_REPLY, blocking_categories, review_output
from .persona_scope import DEFAULT_PERSONA, allowlist as allowed_personas, filter_personas
from .providers import Registry, Route, RouteError
from .routing import (
    AllProvidersFailed,
    RouteNotConfigured,
    route_used,
    routing_context,
)
from .store import RoutingStore

_SSE_DONE = "data: [DONE]\n\n"


class _StreamBlocked(Exception):
    """SSE 流式输出命中硬拦截；立即停止继续产出并标记 content_filter。"""

    def __init__(self, categories: tuple[str, ...]) -> None:
        super().__init__(",".join(categories))
        self.categories = categories


class ChatMessageIn(BaseModel):
    role: str
    content: Any = None
    name: str | None = None


class ChatCompletionIn(BaseModel):
    model: str = "alpha"
    messages: list[ChatMessageIn] = Field(default_factory=list)
    stream: bool = False
    user: str | None = None
    stream_options: dict | None = None


class _RequestFailure(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str,
        param: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.param = param


def _error_response(
    status_code: int,
    message: str,
    *,
    error_type: str = "invalid_request_error",
    code: str | None = None,
    param: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = dict(headers or {})
    if code:
        merged.setdefault("X-Alpha-Error", code)
    return JSONResponse(
        status_code=status_code,
        content=error_body(
            message,
            error_type=error_type,
            code=code,
            param=param,
        ),
        headers=merged,
    )


def _rate_limit_response(exc: RateLimitExceeded) -> JSONResponse:
    return _error_response(
        429,
        exc.detail,
        error_type="rate_limit_error",
        code=exc.code,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _safety_failure(exc: PromptSiftError) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    if exc.request_id:
        headers["X-Waffo-Request-Id"] = exc.request_id
    if exc.status_code == 429:
        error_type = "rate_limit_error"
    elif exc.status_code >= 500:
        error_type = "server_error"
    else:
        error_type = "invalid_request_error"
    return _error_response(
        exc.status_code,
        exc.message,
        error_type=error_type,
        code=exc.code,
        headers=headers,
    )


async def _check_prompt_safety(
    safety: PromptSiftClient | None,
    text: str,
) -> JSONResponse | None:
    """返回拒答响应；None 表示允许继续。"""
    if safety is None:
        return None
    try:
        result = await safety.scan(text)
    except PromptSiftError as exc:
        return _safety_failure(exc)

    refusal = refusal_for(result)
    if refusal is None:
        return None
    status_code, message, code = refusal
    return _error_response(
        status_code,
        message,
        error_type=(
            "invalid_request_error" if status_code < 500 else "server_error"
        ),
        code=code,
        headers=(
            {"X-Waffo-Request-Id": result.request_id}
            if result.request_id
            else None
        ),
    )


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n\n"


def _persona_ids(upstream: Any) -> list[str]:
    loader = getattr(upstream, "persona_loader", None)
    if loader is None:
        return []
    try:
        ids = [str(item) for item in loader.list_ids()]
    except Exception as exc:  # noqa: BLE001 - 上游加载失败时给统一错误
        print(f"  [openai] 读取人格失败: {type(exc).__name__}")
        return []
    # #99 上线阶段只暴露 ALPHA_PERSONAS 里的人格（未设置则不限制）
    return filter_personas(ids, allowed_personas())


def _default_persona(persona_ids: list[str]) -> str:
    configured = (
        os.getenv("ALPHA_OPENAI_DEFAULT_PERSONA", "").strip()
        or os.getenv("ALPHA_PERSONA", "").strip()
        or DEFAULT_PERSONA
    )
    if configured in persona_ids:
        return configured
    if DEFAULT_PERSONA in persona_ids:
        return DEFAULT_PERSONA
    if "luna" in persona_ids:
        return "luna"
    return persona_ids[0] if persona_ids else configured


def _available_registry(
    registry: Registry,
    custom_store: CustomProviderStore | None,
    user: str,
) -> Registry:
    specs = {spec.id: spec for spec in registry.specs()}
    if custom_store is not None:
        for provider in custom_store.list(user):
            specs[provider.id] = provider.to_spec()
    return Registry(specs)


def _resolve_route(
    *,
    alias_route_key: str | None,
    record: Any,
    registry: Registry,
    custom_store: CustomProviderStore | None,
    dev_route: Route | None,
    user: str,
) -> tuple[Route, tuple[Route, ...]]:
    available = _available_registry(registry, custom_store, user)

    if alias_route_key:
        try:
            provider, model = alias_route_key.split("/", 1)
            route = available.resolve(provider, model)
            spec = available.get(provider)
        except (RouteError, ValueError) as exc:
            raise _RequestFailure(
                400,
                f"model 指定的路由不可用: {exc}",
                code="model_not_found",
                param="model",
            ) from exc
        if not spec.available():
            raise _RequestFailure(
                400,
                f"provider {provider!r} 当前不可用（缺少 Key 或已撤销）",
                code="provider_unavailable",
                param="model",
            )
        fallback: list[Route] = []
        if record is not None:
            for item in (record.route, *record.fallback):
                if item != route and item not in fallback:
                    fallback.append(item)
        return route, tuple(fallback)

    if record is not None:
        route = record.route
        try:
            spec = available.get(route.provider)
        except RouteError as exc:
            raise _RequestFailure(
                400,
                f"当前路由不可用: {exc}",
                code="provider_unavailable",
                param="model",
            ) from exc
        if not spec.available():
            raise _RequestFailure(
                400,
                f"provider {route.provider!r} 当前不可用（缺少 Key 或已撤销）",
                code="provider_unavailable",
                param="model",
            )
        return route, tuple(record.fallback)

    if dev_route is not None:
        return dev_route, ()
    raise _RequestFailure(
        400,
        "当前账号尚未配置模型路由；请先设置 /api/me/routing，或在 model 中传 provider/model",
        code="route_not_configured",
        param="model",
    )


def _persist_agent(upstream: Any, agent: Any) -> None:
    persist = getattr(upstream, "_persist_agent", None)
    if not callable(persist):
        return
    try:
        persist(agent)
    except Exception as exc:  # noqa: BLE001 - 落盘失败不应覆盖模型回复
        print(f"  [openai] agent 落盘失败: {type(exc).__name__}")


def _save_turn(
    upstream: Any,
    *,
    client_id: str,
    persona_id: str,
    user_text: str,
    reply: str,
    modality: str,
) -> None:
    store = getattr(upstream, "chat_log_store", None)
    if store is None:
        return
    try:
        store.save_turn(
            client_id=client_id,
            persona_id=persona_id,
            user_msg=user_text,
            agent_reply=reply,
            modality=modality or "文字",
        )
    except Exception as exc:  # noqa: BLE001 - 聊天记录失败不应覆盖回复
        print(f"  [openai] 聊天记录保存失败: {type(exc).__name__}")


def _provider_failure(exc: BaseException) -> JSONResponse:
    print(f"  [openai] 上游调用失败: {type(exc).__name__}: {exc}")
    return _error_response(
        500,
        "上游模型调用失败，请稍后重试",
        error_type="server_error",
        code="provider_unavailable",
    )


def _stream_error(exc: BaseException) -> str:
    print(f"  [openai] 流式调用失败: {type(exc).__name__}: {exc}")
    return _sse(
        error_body(
            "上游模型调用失败，请稍后重试",
            error_type="server_error",
            code="provider_unavailable",
        )
    )


def build_openai_router(
    upstream: Any,
    *,
    store: RoutingStore,
    registry: Registry,
    custom_store: CustomProviderStore | None,
    dev_route: Route | None,
    limits: LimitsService | None = None,
    safety: PromptSiftClient | None = None,
    banned_check: Any = None,
    guard_log: Any = None,
    access_check: Any = None,
    turn_hook: Any = None,
) -> APIRouter:
    router = APIRouter(tags=["openai"])

    @router.get("/v1/models")
    def list_models() -> dict:
        data = [
            {
                "id": "alpha",
                "object": "model",
                "created": 0,
                "owned_by": "alpha",
            }
        ]
        for persona_id in _persona_ids(upstream):
            data.append(
                {
                    "id": persona_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "alpha",
                }
            )
        return {"object": "list", "data": data}

    @router.post("/v1/chat/completions")
    async def chat_completions(
        payload: ChatCompletionIn,
        request: Request,
    ):
        user = str(
            getattr(request.state, "user_id", None)
            or current_auth_user.get()
            or "local"
        )
        persona_ids = _persona_ids(upstream)
        default_persona = _default_persona(persona_ids)

        try:
            user_text = extract_last_user_text(payload.messages)
            if not persona_ids:
                raise _RequestFailure(
                    500,
                    "服务端尚未加载任何人格",
                    code="personas_unavailable",
                )
            if banned_check is not None and banned_check(user):
                raise _RequestFailure(
                    403,
                    "账号已被限制使用；如有异议请发送邮件至 prealpha@163.com 申诉。",
                    code="account_suspended",
                )
            # #102 试用/订阅：30 轮用完后不再进入生成
            if access_check is not None:
                allowed, reason = access_check(user)
                if not allowed:
                    raise _RequestFailure(
                        402,
                        "免费试用的 30 轮已经用完了。订阅 BYOK 后可以继续调用。",
                        code="trial_exhausted",
                    )
            refused = await _check_prompt_safety(safety, user_text)
            if refused is not None:
                return refused
            alias = resolve_model_alias(
                payload.model,
                persona_ids=persona_ids,
                default_persona=default_persona,
            )
            record = store.get(user)
            route, fallback = _resolve_route(
                alias_route_key=alias.route_key,
                record=record,
                registry=registry,
                custom_store=custom_store,
                dev_route=dev_route,
                user=user,
            )
            if limits is not None:
                limits.check_platform_quota(user, route)
        except OpenAIInputError as exc:
            return _error_response(
                400,
                exc.message,
                code=exc.code,
                param=exc.param,
            )
        except _RequestFailure as exc:
            return _error_response(
                exc.status_code,
                exc.message,
                error_type=(
                    "server_error"
                    if exc.status_code >= 500
                    else "invalid_request_error"
                ),
                code=exc.code,
                param=exc.param,
            )
        except RateLimitExceeded as exc:
            return _rate_limit_response(exc)

        client_id = (payload.user or "").strip() or "openai"
        model_name = alias.requested
        request_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())

        try:
            with routing_context(route, fallback, user=user):
                session_id, agent = upstream.get_or_create_session(
                    None,
                    alias.persona_id,
                    user,
                    client_id,
                )
        except RouteNotConfigured as exc:
            return _error_response(
                400,
                str(exc),
                code="route_not_configured",
                param="model",
            )
        except ValueError as exc:
            return _error_response(
                400,
                str(exc),
                code="persona_not_found",
                param="model",
            )
        except RateLimitExceeded as exc:
            return _rate_limit_response(exc)
        except Exception as exc:  # noqa: BLE001 - 建会话阶段的错误统一 500
            return _provider_failure(exc)

        if payload.stream:
            return _streaming_response(
                upstream=upstream,
                agent=agent,
                route=route,
                fallback=fallback,
                user=user,
                client_id=client_id,
                persona_id=alias.persona_id,
                user_text=user_text,
                model_name=model_name,
                request_id=request_id,
                created=created,
                session_id=session_id,
                guard_log=guard_log,
                turn_hook=turn_hook,
            )

        finish_reason = "stop"
        try:
            with routing_context(route, fallback, user=user):
                result = await agent.chat(user_text)
                used = route_used.get()
                reply = str(result.get("reply") or "") if isinstance(result, dict) else ""
                modality = (
                    str(result.get("modality") or "文字")
                    if isinstance(result, dict)
                    else "文字"
                )
                # #100 输出侧审核：命中硬拦截则整段替换，软提示追加在末尾
                guard = review_output(reply, user_text=user_text)
                if guard.action != "allow":
                    if guard_log is not None:
                        try:
                            guard_log(user, guard.action, guard.categories)
                        except Exception:  # noqa: BLE001
                            pass
                    reply = guard.reply
                finish_reason = "content_filter" if guard.blocked else "stop"
                _persist_agent(upstream, agent)
        except RateLimitExceeded as exc:
            return _rate_limit_response(exc)
        except RouteNotConfigured as exc:
            return _error_response(
                400,
                str(exc),
                code="route_not_configured",
                param="model",
            )
        except RouteError as exc:
            return _error_response(
                400,
                str(exc),
                code="provider_unavailable",
                param="model",
            )
        except AllProvidersFailed as exc:
            return _provider_failure(exc)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            return _provider_failure(exc)
        except Exception as exc:  # noqa: BLE001 - 不把第三方异常直接暴露给客户端
            return _provider_failure(exc)

        _save_turn(
            upstream,
            client_id=client_id,
            persona_id=alias.persona_id,
            user_text=user_text,
            reply=reply,
            modality=modality,
        )
        if turn_hook is not None:
            try:
                turn_hook(user)
            except Exception:  # noqa: BLE001
                pass
        headers = {"X-Alpha-Session-Id": str(session_id)}
        if used is not None:
            headers["X-Alpha-Route"] = used.as_str()
        return JSONResponse(
            status_code=200,
            content=completion_body(
                request_id=request_id,
                created=created,
                model=model_name,
                content=reply,
                finish_reason=finish_reason,
            ),
            headers=headers,
        )

    return router


def _streaming_response(
    *,
    upstream: Any,
    agent: Any,
    route: Route,
    fallback: tuple[Route, ...],
    user: str,
    client_id: str,
    persona_id: str,
    user_text: str,
    model_name: str,
    request_id: str,
    created: int,
    session_id: str,
    guard_log: Any = None,
    turn_hook: Any = None,
) -> StreamingResponse:
    async def event_stream():
        extractor = ReplyStreamExtractor()
        completed = False
        saved = False
        final_reply = ""
        guard_tail = ""
        try:
            with routing_context(route, fallback, user=user):
                yield _sse(
                    stream_chunk_body(
                        request_id=request_id,
                        created=created,
                        model=model_name,
                        delta={"role": "assistant", "content": ""},
                    )
                )
                async for raw in agent.chat_stream(user_text):
                    for piece in extractor.feed(raw):
                        hit = blocking_categories(guard_tail + piece)
                        if hit:
                            raise _StreamBlocked(hit)
                        guard_tail = (guard_tail + piece)[-80:]
                        yield _sse(
                            stream_chunk_body(
                                request_id=request_id,
                                created=created,
                                model=model_name,
                                delta={"content": piece},
                            )
                        )
                for piece in extractor.finish():
                    hit = blocking_categories(guard_tail + piece)
                    if hit:
                        raise _StreamBlocked(hit)
                    guard_tail = (guard_tail + piece)[-80:]
                    yield _sse(
                        stream_chunk_body(
                            request_id=request_id,
                            created=created,
                            model=model_name,
                            delta={"content": piece},
                        )
                    )

                # 软提示（#58）：完整回复里出现排他性依赖 / 用户表达自伤念头
                guard = review_output(extractor.reply_text, user_text=user_text)
                if guard.action != "allow" and guard_log is not None:
                    try:
                        guard_log(user, guard.action, guard.categories)
                    except Exception:  # noqa: BLE001
                        pass
                if guard.action == "soften":
                    yield _sse(
                        stream_chunk_body(
                            request_id=request_id,
                            created=created,
                            model=model_name,
                            delta={"content": "\n\n" + "\n\n".join(guard.care)},
                        )
                    )
                final_reply = guard.reply
                completed = True
                _persist_agent(upstream, agent)
                _save_turn(
                    upstream,
                    client_id=client_id,
                    persona_id=persona_id,
                    user_text=user_text,
                    reply=final_reply,
                    modality=str(getattr(agent, "_last_modality", "") or "文字"),
                )
                saved = True
                if turn_hook is not None:
                    try:
                        turn_hook(user)
                    except Exception:  # noqa: BLE001
                        pass

                yield _sse(
                    stream_chunk_body(
                        request_id=request_id,
                        created=created,
                        model=model_name,
                        finish_reason=("content_filter" if guard.blocked else "stop"),
                    )
                )
                yield _SSE_DONE
        except _StreamBlocked as exc:
            final_reply = SAFE_REPLY
            if guard_log is not None:
                try:
                    guard_log(user, "block", exc.categories)
                except Exception:  # noqa: BLE001
                    pass
            _persist_agent(upstream, agent)
            _save_turn(
                upstream,
                client_id=client_id,
                persona_id=persona_id,
                user_text=user_text,
                reply=SAFE_REPLY,
                modality=str(getattr(agent, "_last_modality", "") or "文字"),
            )
            completed = saved = True
            if turn_hook is not None:
                try:
                    turn_hook(user)
                except Exception:  # noqa: BLE001
                    pass
            yield _sse(
                stream_chunk_body(
                    request_id=request_id,
                    created=created,
                    model=model_name,
                    finish_reason="content_filter",
                )
            )
            yield _SSE_DONE
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 已开始 SSE 后只能发错误事件
            yield _stream_error(exc)
            yield _SSE_DONE
        finally:
            if completed and not saved:
                _persist_agent(upstream, agent)
                _save_turn(
                    upstream,
                    client_id=client_id,
                    persona_id=persona_id,
                    user_text=user_text,
                    reply=final_reply or extractor.reply_text,
                    modality=str(getattr(agent, "_last_modality", "") or "文字"),
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Alpha-Session-Id": str(session_id),
        },
    )


def install_openai_error_handlers(app: Any) -> None:
    """只对 `/v1` 覆盖 FastAPI 默认错误格式，其他接口保持原样。"""

    @app.exception_handler(RequestValidationError)
    async def _openai_validation_handler(request: Request, exc: RequestValidationError):
        if not is_openai_path(request.url.path):
            return await request_validation_exception_handler(request, exc)
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = [str(item) for item in (first.get("loc") or ()) if item != "body"]
        return _error_response(
            400,
            str(first.get("msg") or "请求参数不合法"),
            code="invalid_request",
            param=".".join(location) or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _openai_http_handler(request: Request, exc: StarletteHTTPException):
        if not is_openai_path(request.url.path):
            return await http_exception_handler(request, exc)
        detail = exc.detail
        message = detail if isinstance(detail, str) else "请求失败"
        return _error_response(
            exc.status_code,
            message,
            error_type=(
                "server_error" if exc.status_code >= 500 else "invalid_request_error"
            ),
            code="not_found" if exc.status_code == 404 else "http_error",
            headers=dict(exc.headers or {}),
        )
