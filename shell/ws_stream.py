# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#94 真流式 WS 端点 `/ws/chat/stream`。

上游 `main.py` 的 `/ws/chat` 拿不到中间内容——`output_router.stream_to_ws`
收了 `ws_send` 却从不调用，客户端只能等 `chat_end`（首字延迟 = 全文生成时间）。

本端点直接消费 `agent.chat_stream()`，用 `ReplyExtractor` 边解析边推送：

    ← {"type":"chat_start","session_id":"..."}
    ← {"type":"chunk","content":"我在","index":0}
    ← {"type":"chunk","content":"。","index":1}
    ← {"type":"chat_end","reply":"我在。","modality":"文字",...}

保留上游两个语义：
- **打字防抖**：连发消息合并成一轮（默认 3s 窗口，显式 typing:false 收到后缩到 2s）
- **落盘**：一轮结束后调 `upstream._persist_agent`（已被外壳包装，会存关系 EMA / turn_count）

不改 vendor。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth.scope import current_auth_user
from .stream_parse import ReplyExtractor, SegmentEvent, Segmenter

__all__ = ["build_stream_ws_router"]

# 段间停顿（毫秒）。上游原本固定 300ms —— 每条间隔一模一样，机械感就是这么来的。
# 真人连发间隔是浮动的：基础 450ms ± 120ms，长段再多 100ms（「她打了很久才发出来」）。
# 设了 ALPHA_SEGMENT_PAUSE_MS 就用固定值（测试要可复现）。
PAUSE_OVERRIDE_MS = os.getenv("ALPHA_SEGMENT_PAUSE_MS", "").strip()
PAUSE_BASE_MS = 450
PAUSE_JITTER_MS = 120
PAUSE_MIN_MS = 250
PAUSE_MAX_MS = 1500


def segment_pause(segment_text: str) -> int:
    """这一段落之后该停多久。

    延迟的是「下一段第一个字」，影响它的是她打了多久，不是上一段多长 ——
    所以不做精确映射，只给长段一个轻微加成。
    """
    if PAUSE_OVERRIDE_MS:
        try:
            return max(0, min(2000, int(PAUSE_OVERRIDE_MS)))
        except ValueError:
            pass
    import random

    base = PAUSE_BASE_MS + (100 if len(segment_text or "") > 30 else 0)
    value = base + random.randint(-PAUSE_JITTER_MS, PAUSE_JITTER_MS)
    return max(PAUSE_MIN_MS, min(PAUSE_MAX_MS, value))

DEBOUNCE_FALLBACK_SEC = 3.0
DEBOUNCE_GRACE_SEC = 2.0
MAX_WAIT_SEC = 20.0  # 用户一直在打字时的硬上限，避免无限不回复


def build_stream_ws_router(
    upstream: Any,
    *,
    on_turn_done: Callable[[Any, str, str], None] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["stream"])

    @router.websocket("/ws/chat/stream")
    async def chat_stream_ws(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()

        user = current_auth_user.get() or "local"
        buffer: list[dict] = []
        pending = asyncio.Event()
        typing_active = False
        session_id: str | None = None
        session_persona: str | None = None
        agent = None
        closed = False

        async def send(payload: dict) -> None:
            nonlocal closed
            if closed:
                return
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                closed = True

        async def run_turn(msgs: list[dict]) -> None:
            nonlocal session_id, session_persona, agent
            first = msgs[0]
            persona_id = str(first.get("persona_id") or "").strip()
            text = "\n".join(str(m.get("content") or "").strip() for m in msgs).strip()
            if not persona_id or not text:
                return
            user_name = first.get("user_name")
            # client_id 决定历史命名空间；未带则按用户兜底，保证能落盘与恢复
            client_id = str(first.get("client_id") or f"ws:{user}").strip()[:128]

            # 上游 get_or_create_session 只按 session_id 复用，**不校验 persona**：
            # 同一连接里换了人格，会把旧 agent 原样返回（人格没变、历史串台）。
            # 这里按人格重建会话，绕开那个行为。
            if session_persona is not None and session_persona != persona_id:
                session_id = None
            session_persona = persona_id

            try:
                session_id, agent = upstream.get_or_create_session(
                    session_id, persona_id, user_name, client_id
                )
            except ValueError as exc:
                await send({"type": "error", "content": str(exc)})
                return

            extractor = ReplyExtractor()
            segmenter = Segmenter()
            started = time.monotonic()
            first_chunk_ms: int | None = None
            total_chunks = 0
            # 每段单独算，见 segment_pause()
            pending_restart = False

            async def handle(ev) -> None:
                """chunk → 直接推；segment_end → 停一下并挂起「重开打字」。"""
                nonlocal first_chunk_ms, total_chunks, pending_restart
                if ev.kind == "chunk":
                    if pending_restart:
                        # 上一段刚结束，这一段开始 —— 重新拉起点「对方正在输入」
                        await send({"type": "chat_start", "session_id": session_id})
                        pending_restart = False
                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.monotonic() - started) * 1000)
                    total_chunks += 1
                    await send(
                        {
                            "type": "chunk",
                            "content": ev.text,
                            "index": ev.index,
                            "segment": ev.segment,
                        }
                    )
                else:
                    pause_ms = segment_pause(ev.text)
                    await send(
                        {
                            "type": "segment_end",
                            "segment": ev.segment,
                            "reply": ev.text,
                            "pause_ms": pause_ms,
                        }
                    )
                    # 段间停顿 —— 「连发微信」的感觉就来自这里
                    await asyncio.sleep(pause_ms / 1000.0)
                    pending_restart = True

            try:
                async for chunk in agent.chat_stream(text):
                    if chunk == "__FEEL_DONE__":
                        # prompt 就绪，LLM 即将返回首字 —— 前端此时开打字动画
                        await send(
                            {
                                "type": "chat_start",
                                "session_id": session_id,
                                "user_content": text,
                            }
                        )
                        continue
                    piece = extractor.feed(chunk)
                    if not piece:
                        continue
                    for ev in segmenter.feed(piece):
                        await handle(ev)

                tail = extractor.finish()
                if tail:
                    for ev in segmenter.feed(tail):
                        await handle(ev)
                for ev in segmenter.finish():
                    await handle(ev)
            except Exception as exc:  # noqa: BLE001
                await send(
                    {
                        "type": "error",
                        "content": f"{type(exc).__name__}: {str(exc)[:200]}",
                    }
                )
                return

            # 权威解析：以引擎自己的 parser 为准，不信流式拼接结果
            from agent.output_router import parse_raw_output

            parsed = parse_raw_output(extractor.raw)
            reply = parsed["reply"]
            modality = parsed["modality"]

            # 模型没守协议（整段无标记）时，流式一个字都没发 → 兜底整段补发
            if total_chunks == 0 and reply:
                pending_restart = False
                await handle(
                    SegmentEvent(kind="chunk", segment=0, text=reply, index=0)
                )
                await handle(
                    SegmentEvent(kind="segment_end", segment=0, text=reply, index=1)
                )

            status: dict = {}
            try:
                status = agent.get_status()
            except Exception:  # noqa: BLE001
                status = {}
            engine_segments = status.pop("segments", None)
            status.pop("image_path", None)
            status.pop("audio_path", None)
            pushed = segmenter.segments or ([reply] if reply else [])

            await send(
                {
                    "type": "chat_end",
                    "session_id": session_id,
                    "reply": reply,
                    "modality": modality,
                    "segments": pushed,
                    "engine_segments": engine_segments,
                    "chunks": total_chunks,
                    "first_chunk_ms": first_chunk_ms,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    **status,
                }
            )

            try:
                upstream._persist_agent(agent)
            except Exception:  # noqa: BLE001
                pass

            log = getattr(upstream, "chat_log_store", None)
            if log is not None:
                try:
                    log.save_turn(
                        client_id=client_id,
                        persona_id=persona_id,
                        user_msg=text,
                        agent_reply=reply,
                        modality=modality,
                    )
                except Exception:  # noqa: BLE001
                    pass

            if on_turn_done is not None:
                try:
                    on_turn_done(agent, text, reply)
                except Exception:  # noqa: BLE001
                    pass

        async def worker() -> None:
            """单 worker：等消息 → 开防抖窗口 → 到点合并成一轮。"""
            while not closed:
                await pending.wait()
                pending.clear()

                window = DEBOUNCE_GRACE_SEC if not typing_active else DEBOUNCE_FALLBACK_SEC
                deadline = loop.time() + window
                hard_stop = loop.time() + MAX_WAIT_SEC

                while True:
                    remain = min(deadline, hard_stop) - loop.time()
                    if remain <= 0:
                        break
                    try:
                        await asyncio.wait_for(pending.wait(), timeout=remain)
                    except asyncio.TimeoutError:
                        break
                    pending.clear()
                    window = (
                        DEBOUNCE_GRACE_SEC if not typing_active else DEBOUNCE_FALLBACK_SEC
                    )
                    deadline = loop.time() + window

                msgs, buffer[:] = list(buffer), []
                if msgs:
                    await run_turn(msgs)

        worker_task = asyncio.create_task(worker())
        try:
            while True:
                message = await ws.receive_json()
                mtype = message.get("type")

                if mtype == "typing":
                    typing_active = bool(message.get("active"))
                    if not typing_active and buffer:
                        pending.set()
                    continue

                if mtype == "chat":
                    if not str(message.get("content") or "").strip():
                        continue
                    buffer.append(message)
                    pending.set()
                    continue

                if mtype == "ping":
                    await send({"type": "pong", "ts": time.time()})
                    continue

                if mtype == "close":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            closed = True
            pending.set()  # 唤醒可能正卡在 wait 上的 worker，让它看到 closed 退出
            if not worker_task.done():
                # 关键：客户端断开时**不要立刻取消正在跑的一轮**。
                # 那一轮还在等 LLM，取消掉就等于「消息发出去了，但什么都没发生」——
                # 而 chat_log 也没写，前端重连后连历史都补不回来（#98 第 6 条）。
                # 给它最多 90 秒收尾；期间的 send 已被 closed 短路，不会报错。
                try:
                    await asyncio.wait_for(asyncio.shield(worker_task), timeout=90)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                    worker_task.cancel()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return router
