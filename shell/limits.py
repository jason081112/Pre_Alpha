# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P1-67 限流、平台配额与 token 审计。

设计边界：
- 按用户 + IP 双维度做 RPM/RPD；未登录请求只按 IP。
- 平台 provider 额外检查每日 token 配额；BYOK 只做防滥用限流。
- 配置存 SQLite，支持热更新；多进程下最多 2 秒延迟。
- 只记 token 数量，不记消息原文。
- 自定义 provider 探活：每小时次数、并发上限、失败退避。
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence
from urllib.parse import urlsplit

from .openai_format import error_body, is_openai_path
from .providers import Route

_TRUTHY = {"1", "true", "yes", "on"}
_ALLOWED_KEYS = {
    "user_rpm",
    "user_rpd",
    "ip_rpm",
    "ip_rpd",
    "platform_daily_tokens",
    "probe_per_hour",
    "probe_concurrency",
    "probe_backoff_base",
    "probe_backoff_max",
}


class LimitConfigError(ValueError):
    """限流配置不合法。"""


class RateLimitExceeded(Exception):
    """带 HTTP/WS 语义的限流异常。"""

    def __init__(
        self,
        code: str,
        detail: str,
        retry_after: float = 1,
        *,
        action: str = "wait",
        scope: str = "",
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retry_after = max(1, int(math.ceil(float(retry_after))))
        self.action = action
        self.scope = scope

    def as_dict(self) -> dict:
        return {
            "error": self.code,
            "code": self.code,
            "detail": self.detail,
            "action": self.action,
            "retry_after": self.retry_after,
        }


@dataclass(frozen=True)
class LimitConfig:
    user_rpm: int = 60
    user_rpd: int = 2000
    ip_rpm: int = 120
    ip_rpd: int = 5000
    platform_daily_tokens: int = 200_000
    probe_per_hour: int = 10
    probe_concurrency: int = 2
    probe_backoff_base: int = 2
    probe_backoff_max: int = 300

    @classmethod
    def from_env(cls) -> "LimitConfig":
        def read(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            user_rpm=read("ALPHA_USER_RPM", 60),
            user_rpd=read("ALPHA_USER_RPD", 2000),
            ip_rpm=read("ALPHA_IP_RPM", 120),
            ip_rpd=read("ALPHA_IP_RPD", 5000),
            platform_daily_tokens=read("ALPHA_PLATFORM_DAILY_TOKENS", 200_000),
            probe_per_hour=read("ALPHA_PROBE_PER_HOUR", 10),
            probe_concurrency=read("ALPHA_PROBE_CONCURRENCY", 2),
            probe_backoff_base=read("ALPHA_PROBE_BACKOFF_BASE", 2),
            probe_backoff_max=read("ALPHA_PROBE_BACKOFF_MAX", 300),
        ).validated()

    def validated(self) -> "LimitConfig":
        for name in _ALLOWED_KEYS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise LimitConfigError(f"{name} 必须是整数")
            if value < 0:
                raise LimitConfigError(f"{name} 不能为负数")
        if self.probe_backoff_max < self.probe_backoff_base:
            raise LimitConfigError("probe_backoff_max 不能小于 probe_backoff_base")
        return self

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in _ALLOWED_KEYS}

    @classmethod
    def updated(cls, base: "LimitConfig", changes: dict) -> "LimitConfig":
        unknown = set(changes) - _ALLOWED_KEYS
        if unknown:
            raise LimitConfigError(f"未知配置项: {', '.join(sorted(unknown))}")
        values = {}
        for key, value in changes.items():
            if value is None:
                continue
            if isinstance(value, bool):
                raise LimitConfigError(f"{key} 必须是整数")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise LimitConfigError(f"{key} 必须是整数") from exc
            values[key] = number
        return replace(base, **values).validated()


def utc_day(now: float | None = None) -> str:
    stamp = time.time() if now is None else float(now)
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")


def seconds_until_utc_midnight(now: float) -> int:
    current = datetime.fromtimestamp(float(now), timezone.utc)
    tomorrow = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
    return max(1, int(math.ceil(tomorrow.timestamp() - float(now))))


def _connect(path: str | Path) -> sqlite3.Connection:
    text = str(path)
    if text != ":memory:":
        Path(text).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(text, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 3000")
    if text != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
    return conn


class LimitConfigStore:
    """SQLite 配置覆盖层；默认值来自环境变量。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS limit_config (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """

    def __init__(
        self,
        path: str | Path,
        *,
        defaults: LimitConfig | None = None,
        clock: Callable[[], float] = time.time,
        ttl: float = 2.0,
    ):
        self.path = str(path)
        self._defaults = (defaults or LimitConfig.from_env()).validated()
        self._clock = clock
        self._ttl = max(0.0, float(ttl))
        self._lock = threading.Lock()
        self._conn = _connect(path)
        self._conn.executescript(self._SCHEMA)
        self._cache: LimitConfig | None = None
        self._cache_at = 0.0

    def get(self) -> LimitConfig:
        now = self._clock()
        with self._lock:
            if (
                self._cache is not None
                and self._ttl > 0
                and now - self._cache_at < self._ttl
            ):
                return self._cache
            rows = self._conn.execute(
                "SELECT key, value FROM limit_config"
            ).fetchall()
            values = {}
            for row in rows:
                if row["key"] not in _ALLOWED_KEYS:
                    continue
                try:
                    values[row["key"]] = int(row["value"])
                except (TypeError, ValueError):
                    continue
            config = LimitConfig.updated(self._defaults, values)
            self._cache = config
            self._cache_at = now
            return config

    def update(self, changes: dict) -> LimitConfig:
        current = self.get()
        config = LimitConfig.updated(current, changes)
        now = self._clock()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for key, value in changes.items():
                    if value is None:
                        continue
                    self._conn.execute(
                        "INSERT INTO limit_config (key, value, updated_at) "
                        "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                        "value = excluded.value, updated_at = excluded.updated_at",
                        (key, str(int(value)), now),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            self._cache = config
            self._cache_at = now
        return config

    def close(self) -> None:
        with self._lock:
            self._conn.close()


@dataclass(frozen=True)
class Bucket:
    label: str
    key: str
    limit: int
    window: int


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    retry_after: int = 0
    label: str = ""
    limit: int = 0
    remaining: int = 0


class RateLimitStore:
    """固定窗口计数。检查全部通过后才统一 +1，避免部分扣减。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS rate_counter (
        key            TEXT NOT NULL,
        window_seconds INTEGER NOT NULL,
        window_start   INTEGER NOT NULL,
        count          INTEGER NOT NULL DEFAULT 0,
        updated_at     REAL NOT NULL,
        PRIMARY KEY (key, window_seconds, window_start)
    );
    CREATE INDEX IF NOT EXISTS idx_rate_counter_start
        ON rate_counter(window_start);
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.path = str(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = _connect(path)
        self._conn.executescript(self._SCHEMA)

    def consume(
        self,
        buckets: Sequence[Bucket],
        *,
        now: float | None = None,
    ) -> LimitDecision:
        stamp = self._clock() if now is None else float(now)
        active = [b for b in buckets if b.limit > 0 and b.window > 0]
        if not active:
            return LimitDecision(True)

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                denied: list[tuple[Bucket, int]] = []
                remaining: int | None = None
                for bucket in active:
                    start = int(stamp // bucket.window * bucket.window)
                    row = self._conn.execute(
                        "SELECT count FROM rate_counter "
                        "WHERE key = ? AND window_seconds = ? AND window_start = ?",
                        (bucket.key, bucket.window, start),
                    ).fetchone()
                    count = int(row["count"]) if row else 0
                    if count >= bucket.limit:
                        retry = int(math.ceil(start + bucket.window - stamp))
                        denied.append((bucket, max(1, retry)))
                    else:
                        left = bucket.limit - count - 1
                        remaining = left if remaining is None else min(remaining, left)

                if denied:
                    self._conn.execute("ROLLBACK")
                    worst = max(denied, key=lambda item: item[1])
                    return LimitDecision(
                        False,
                        retry_after=worst[1],
                        label=worst[0].label,
                        limit=worst[0].limit,
                    )

                for bucket in active:
                    start = int(stamp // bucket.window * bucket.window)
                    self._conn.execute(
                        "INSERT INTO rate_counter "
                        "(key, window_seconds, window_start, count, updated_at) "
                        "VALUES (?, ?, ?, 1, ?) ON CONFLICT(key, window_seconds, window_start) "
                        "DO UPDATE SET count = count + 1, updated_at = excluded.updated_at",
                        (bucket.key, bucket.window, start, stamp),
                    )
                self._conn.execute("COMMIT")
                return LimitDecision(True, remaining=max(0, remaining or 0))
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def cleanup(self, *, before: float | None = None) -> int:
        cutoff = self._clock() - 2 * 86400 if before is None else float(before)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM rate_counter WHERE window_start < ?", (int(cutoff),)
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class UsageStore:
    """token 用量审计：只存数量，不存原文。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS token_usage (
        user_id             TEXT NOT NULL,
        day                 TEXT NOT NULL,
        kind                TEXT NOT NULL,
        provider            TEXT NOT NULL,
        model               TEXT NOT NULL,
        prompt_tokens       INTEGER NOT NULL DEFAULT 0,
        completion_tokens   INTEGER NOT NULL DEFAULT 0,
        total_tokens        INTEGER NOT NULL DEFAULT 0,
        requests            INTEGER NOT NULL DEFAULT 0,
        estimated_requests  INTEGER NOT NULL DEFAULT 0,
        updated_at          REAL NOT NULL,
        PRIMARY KEY (user_id, day, kind, provider, model)
    );
    CREATE INDEX IF NOT EXISTS idx_token_usage_day
        ON token_usage(day, user_id);
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.path = str(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = _connect(path)
        self._conn.executescript(self._SCHEMA)

    @staticmethod
    def _int(value) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def record(
        self,
        *,
        user_id: str,
        provider: str,
        model: str,
        kind: str,
        usage: dict | None,
        estimated: bool = False,
        now: float | None = None,
    ) -> None:
        stamp = self._clock() if now is None else float(now)
        payload = usage if isinstance(usage, dict) else {}
        prompt = self._int(payload.get("prompt_tokens"))
        completion = self._int(payload.get("completion_tokens"))
        total = self._int(payload.get("total_tokens")) or prompt + completion
        day = utc_day(stamp)
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_usage "
                "(user_id, day, kind, provider, model, prompt_tokens, "
                "completion_tokens, total_tokens, requests, estimated_requests, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(user_id, day, kind, provider, model) DO UPDATE SET "
                "prompt_tokens = prompt_tokens + excluded.prompt_tokens, "
                "completion_tokens = completion_tokens + excluded.completion_tokens, "
                "total_tokens = total_tokens + excluded.total_tokens, "
                "requests = requests + 1, "
                "estimated_requests = estimated_requests + excluded.estimated_requests, "
                "updated_at = excluded.updated_at",
                (
                    user_id,
                    day,
                    kind,
                    provider,
                    model,
                    prompt,
                    completion,
                    total,
                    1 if estimated else 0,
                    stamp,
                ),
            )

    def daily_tokens(
        self,
        user_id: str,
        day: str | None = None,
        *,
        kind: str | None = None,
    ) -> int:
        target_day = day or utc_day(self._clock())
        sql = (
            "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM token_usage "
            "WHERE user_id = ? AND day = ?"
        )
        args: list = [user_id, target_day]
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        return int(row["total"]) if row else 0

    def totals(self, user_id: str, day: str | None = None) -> dict:
        target_day = day or utc_day(self._clock())
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "COALESCE(SUM(requests), 0) AS requests, "
                "COALESCE(SUM(estimated_requests), 0) AS estimated_requests "
                "FROM token_usage WHERE user_id = ? AND day = ? GROUP BY kind",
                (user_id, target_day),
            ).fetchall()
        kinds = {
            row["kind"]: {
                "prompt_tokens": int(row["prompt_tokens"]),
                "completion_tokens": int(row["completion_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "requests": int(row["requests"]),
                "estimated_requests": int(row["estimated_requests"]),
            }
            for row in rows
        }
        return {
            "user_id": user_id,
            "day": target_day,
            "total_tokens": sum(item["total_tokens"] for item in kinds.values()),
            "requests": sum(item["requests"] for item in kinds.values()),
            "kinds": kinds,
        }

    def rows(
        self,
        *,
        user_id: str | None = None,
        day: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        sql = (
            "SELECT user_id, day, kind, provider, model, prompt_tokens, "
            "completion_tokens, total_tokens, requests, estimated_requests, updated_at "
            "FROM token_usage WHERE 1 = 1"
        )
        args: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        if day is not None:
            sql += " AND day = ?"
            args.append(day)
        sql += " ORDER BY day DESC, user_id, provider, model LIMIT ?"
        args.append(max(1, min(int(limit), 5000)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ProbeStateStore:
    """探活失败计数与指数退避。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS probe_state (
        target_key    TEXT PRIMARY KEY,
        failures      INTEGER NOT NULL DEFAULT 0,
        blocked_until REAL NOT NULL DEFAULT 0,
        updated_at    REAL NOT NULL
    );
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.path = str(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = _connect(path)
        self._conn.executescript(self._SCHEMA)

    def check(self, key: str, *, now: float | None = None) -> int:
        stamp = self._clock() if now is None else float(now)
        with self._lock:
            row = self._conn.execute(
                "SELECT blocked_until FROM probe_state WHERE target_key = ?", (key,)
            ).fetchone()
        if row is None:
            return 0
        remaining = float(row["blocked_until"]) - stamp
        return max(0, int(math.ceil(remaining)))

    def fail(
        self,
        key: str,
        *,
        base: int,
        maximum: int,
        now: float | None = None,
    ) -> int:
        stamp = self._clock() if now is None else float(now)
        with self._lock:
            row = self._conn.execute(
                "SELECT failures FROM probe_state WHERE target_key = ?", (key,)
            ).fetchone()
            failures = (int(row["failures"]) if row else 0) + 1
            delay = 0
            if base > 0:
                delay = min(maximum, base * (2 ** max(0, failures - 1)))
            blocked_until = stamp + delay
            self._conn.execute(
                "INSERT INTO probe_state (target_key, failures, blocked_until, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(target_key) DO UPDATE SET "
                "failures = excluded.failures, blocked_until = excluded.blocked_until, "
                "updated_at = excluded.updated_at",
                (key, failures, blocked_until, stamp),
            )
        return delay

    def success(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM probe_state WHERE target_key = ?", (key,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ProbeSlots:
    """单进程探活并发上限。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, int] = {}

    @contextmanager
    def slot(self, user_id: str, limit: int) -> Iterator[None]:
        if limit <= 0:
            yield
            return
        with self._lock:
            current = self._active.get(user_id, 0)
            if current >= limit:
                raise RateLimitExceeded(
                    "probe_concurrency",
                    "已有探活请求在进行，请稍后再试",
                    1,
                    scope="probe_concurrency",
                )
            self._active[user_id] = current + 1
        try:
            yield
        finally:
            with self._lock:
                current = self._active.get(user_id, 0)
                if current <= 1:
                    self._active.pop(user_id, None)
                else:
                    self._active[user_id] = current - 1


def _probe_key(user_id: str, target_url: str) -> str:
    parsed = urlsplit(target_url or "")
    host = parsed.hostname or "unknown"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{user_id or 'anonymous'}|{host}:{port}"


def client_ip(scope: dict, *, trust_proxy: bool = False) -> str:
    """从 ASGI scope 取客户端 IP；默认不信任 X-Forwarded-For。"""
    if trust_proxy:
        for key, value in scope.get("headers") or []:
            if key.lower() != b"x-forwarded-for":
                continue
            candidate = value.decode("latin-1").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                break
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"


class LimitsService:
    """限流、配额、用量与探活限制的统一门面。"""

    def __init__(
        self,
        path: str | Path,
        *,
        defaults: LimitConfig | None = None,
        clock: Callable[[], float] = time.time,
        trust_proxy: bool | None = None,
    ):
        self.path = str(path)
        self._clock = clock
        if trust_proxy is None:
            trust_proxy = (
                os.getenv("ALPHA_TRUST_PROXY", "").strip().lower() in _TRUTHY
            )
        self.trust_proxy = bool(trust_proxy)
        self.config_store = LimitConfigStore(
            path,
            defaults=defaults,
            clock=clock,
        )
        self.buckets = RateLimitStore(path, clock=clock)
        self.usage = UsageStore(path, clock=clock)
        self.probes = ProbeStateStore(path, clock=clock)
        self.slots = ProbeSlots()
        self.buckets.cleanup()

    def config(self) -> LimitConfig:
        return self.config_store.get()

    def update_config(self, changes: dict) -> LimitConfig:
        config = self.config_store.update(changes)
        self.buckets.cleanup()
        return config

    def _request_buckets(
        self, user_id: str | None, ip: str, config: LimitConfig
    ) -> list[Bucket]:
        buckets: list[Bucket] = []
        if user_id:
            buckets.extend(
                [
                    Bucket("user_rpm", f"u:{user_id}:rpm", config.user_rpm, 60),
                    Bucket("user_rpd", f"u:{user_id}:rpd", config.user_rpd, 86400),
                ]
            )
        buckets.extend(
            [
                Bucket("ip_rpm", f"i:{ip}:rpm", config.ip_rpm, 60),
                Bucket("ip_rpd", f"i:{ip}:rpd", config.ip_rpd, 86400),
            ]
        )
        return buckets

    def check_request(
        self,
        user_id: str | None,
        ip: str,
        chain: Sequence[Route] = (),
    ) -> None:
        config = self.config()
        now = self._clock()
        decision = self.buckets.consume(
            self._request_buckets(user_id, ip, config),
            now=now,
        )
        if not decision.allowed:
            messages = {
                "user_rpm": "请求太快，请稍后再试",
                "user_rpd": "今日请求次数已用完",
                "ip_rpm": "当前网络请求太快，请稍后再试",
                "ip_rpd": "当前网络今日请求次数已用完",
            }
            raise RateLimitExceeded(
                "rate_limited",
                messages.get(decision.label, "请求过于频繁，请稍后再试"),
                decision.retry_after,
                scope=decision.label,
            )

        if not user_id or not chain:
            return
        self.check_platform_quota(user_id, chain[0], now=now, config=config)

    def check_platform_quota(
        self,
        user_id: str | None,
        route: Route,
        *,
        now: float | None = None,
        config: LimitConfig | None = None,
    ) -> None:
        """只检查平台额度，不消耗 RPM/RPD。

        OpenAI 兼容端点在解析出本次实际主路由后单独调用，避免
        `model=provider/model` 覆盖路由时绕过平台配额。
        """
        if not user_id:
            return
        # 配额按主路由判定：BYOK 主路由只做防滥用限流，不扣平台额度。
        if route.provider.startswith("custom-"):
            return
        config = config or self.config()
        if config.platform_daily_tokens <= 0:
            return
        now = self._clock() if now is None else now
        used = self.usage.daily_tokens(user_id, utc_day(now), kind="platform")
        if used >= config.platform_daily_tokens:
            raise RateLimitExceeded(
                "quota_exceeded",
                "今日平台 token 额度已用完，可切换到自有 Key 或明天再试",
                seconds_until_utc_midnight(now),
                action="wait_or_byok",
                scope="platform_quota",
            )

    def record_usage(
        self,
        user_id: str,
        route: Route,
        usage: dict | None,
        estimated: bool = False,
    ) -> None:
        kind = "byok" if route.provider.startswith("custom-") else "platform"
        self.usage.record(
            user_id=user_id or "local",
            provider=route.provider,
            model=route.model,
            kind=kind,
            usage=usage,
            estimated=estimated,
        )

    def check_probe(self, user_id: str, ip: str, target_url: str) -> None:
        config = self.config()
        now = self._clock()
        retry = self.probes.check(_probe_key(user_id, target_url), now=now)
        if retry > 0:
            raise RateLimitExceeded(
                "probe_backoff",
                f"该地址探活失败过多，请 {retry} 秒后再试",
                retry,
                scope="probe_backoff",
            )
        buckets = [
            Bucket("probe_user", f"p:u:{user_id}:h", config.probe_per_hour, 3600),
            Bucket("probe_ip", f"p:i:{ip}:h", config.probe_per_hour, 3600),
        ]
        decision = self.buckets.consume(buckets, now=now)
        if not decision.allowed:
            raise RateLimitExceeded(
                "probe_rate_limited",
                "自定义 provider 探活请求过多，请稍后再试",
                decision.retry_after,
                scope=decision.label,
            )

    def probe_slot(self, user_id: str):
        return self.slots.slot(user_id, self.config().probe_concurrency)

    def record_probe_failure(self, user_id: str, target_url: str) -> int:
        config = self.config()
        return self.probes.fail(
            _probe_key(user_id, target_url),
            base=config.probe_backoff_base,
            maximum=config.probe_backoff_max,
        )

    def record_probe_success(self, user_id: str, target_url: str) -> None:
        self.probes.success(_probe_key(user_id, target_url))

    def usage_snapshot(
        self,
        *,
        user_id: str | None = None,
        day: str | None = None,
    ) -> dict:
        target_day = day or utc_day(self._clock())
        return {
            "day": target_day,
            "user_id": user_id,
            "rows": self.usage.rows(user_id=user_id, day=target_day),
        }

    def close(self) -> None:
        self.config_store.close()
        self.buckets.close()
        self.usage.close()
        self.probes.close()


_HTTP_LIMITED = {
    "/api/chat",
    "/api/tts",
    "/api/image",
    "/api/demo/inject",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/v1/chat/completions",
}
_WS_LIMITED = {"/ws/chat"}


def _is_chat_message(message: dict) -> bool:
    text = message.get("text")
    if not text:
        return False
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("type") == "chat"


class LimitsMiddleware:
    """HTTP 请求级限流 + WebSocket 每条 chat 消息限流。"""

    def __init__(
        self,
        app,
        limits: LimitsService,
        route_lookup: Callable[[str], Sequence[Route]] | None = None,
    ):
        self.app = app
        self.limits = limits
        self.route_lookup = route_lookup

    def _user(self, scope: dict) -> str | None:
        state = scope.get("state") or {}
        user = state.get("user_id")
        return str(user) if user else None

    def _chain(self, user_id: str | None) -> tuple[Route, ...]:
        if not user_id or self.route_lookup is None:
            return ()
        return tuple(self.route_lookup(user_id) or ())

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "http":
            await self._http(scope, receive, send)
            return
        if kind == "websocket":
            await self._websocket(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _http(self, scope, receive, send) -> None:
        path = (scope.get("path", "") or "/").rstrip("/") or "/"
        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS" or path not in _HTTP_LIMITED:
            await self.app(scope, receive, send)
            return

        user_id = self._user(scope)
        ip = client_ip(scope, trust_proxy=self.limits.trust_proxy)
        # /v1 的 model 可以覆盖本次实际主路由，配额由端点在解析路由后检查；
        # 中间件这里只做 RPM/RPD，避免用旧路由重复判断或误拦 BYOK。
        chain = () if path == "/v1/chat/completions" else self._chain(user_id)
        try:
            self.limits.check_request(user_id, ip, chain)
        except RateLimitExceeded as exc:
            await self._reject_http(scope, send, exc)
            return
        await self.app(scope, receive, send)

    async def _websocket(self, scope, receive, send) -> None:
        path = (scope.get("path", "") or "/").rstrip("/") or "/"
        if path not in _WS_LIMITED:
            await self.app(scope, receive, send)
            return

        user_id = self._user(scope)
        ip = client_ip(scope, trust_proxy=self.limits.trust_proxy)

        async def guarded_receive():
            while True:
                message = await receive()
                if message.get("type") != "websocket.receive":
                    return message
                if not _is_chat_message(message):
                    return message
                try:
                    self.limits.check_request(
                        user_id,
                        ip,
                        self._chain(user_id),
                    )
                except RateLimitExceeded as exc:
                    if not await self._send_ws_error(send, exc):
                        return {"type": "websocket.disconnect", "code": 1006}
                    continue
                return message

        await self.app(scope, guarded_receive, send)

    @staticmethod
    async def _reject_http(scope, send, exc: RateLimitExceeded) -> None:
        path = (scope.get("path", "") or "/").rstrip("/") or "/"
        if is_openai_path(path):
            payload = error_body(
                exc.detail,
                error_type="rate_limit_error",
                code=exc.code,
            )
        else:
            payload = exc.as_dict()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", str(exc.retry_after).encode("ascii")),
                    (b"x-alpha-error", exc.code.encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _send_ws_error(send, exc: RateLimitExceeded) -> bool:
        payload = {"type": "error", **exc.as_dict()}
        try:
            await send(
                {
                    "type": "websocket.send",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            )
            return True
        except Exception:  # noqa: BLE001 - 连接已断开时不再处理
            return False
