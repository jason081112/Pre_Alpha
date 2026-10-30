# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""人格仓库 —— 人格作为可 CRUD 资源的存储与校验（#83）。

本模块刻意不依赖 FastAPI：核心逻辑（校验 / 版本 / 软删除 / 原子写）
全部是纯 Python，便于零依赖测试。HTTP 语义在 `shell/personas_api.py`。

存储布局（完整隔离与配额细化属 #84，这里是可用雏形）::

    <root>/<user_id>/<persona_id>/
        ├── profile.json      # PersonProfile v2（真相来源）
        ├── state.json        # version / created_at / updated_at / source / deleted_at
        ├── SOUL.md           # 渲染产物（派生物，可重建）
        └── versions/v0001.json …
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bridge.model import FORBIDDEN_KEYS, profile_from_dict, validate_profile
from bridge.profile2soul import profile_to_soul

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

DEFAULT_MAX_PERSONAS = 10
DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_KEEP_VERSIONS = 10


class PersonaError(Exception):
    """带 HTTP 语义的错误基类。"""

    status = 400
    code = "bad_request"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class NotFoundError(PersonaError):
    status = 404
    code = "not_found"


class ConflictError(PersonaError):
    status = 409
    code = "version_conflict"


class InvalidProfileError(PersonaError):
    status = 422
    code = "invalid_profile"


class RawTextNotAllowedError(PersonaError):
    status = 422
    code = "raw_text_not_allowed"


class ReadOnlyError(PersonaError):
    status = 403
    code = "readonly"


class QuotaExceededError(PersonaError):
    status = 429
    code = "quota_personas"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _find_forbidden(payload: Any, path: str = "$") -> str | None:
    """递归查找原文键；返回首个命中路径。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                return f"{path}.{key}"
            hit = _find_forbidden(value, f"{path}.{key}")
            if hit:
                return hit
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            hit = _find_forbidden(item, f"{path}[{index}]")
            if hit:
                return hit
    return None


def _merge(base: dict, patch: dict) -> dict:
    """浅递归合并：字典递归，其余值直接覆盖；null 表示删除该键。"""
    result = dict(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class PersonaRecord:
    persona_id: str
    profile: dict
    state: dict

    def summary(self) -> dict:
        identity = self.profile.get("identity") or {}
        name = (identity.get("name") or {}).get("value")
        return {
            "persona_id": self.persona_id,
            "display_name": name,
            "version": self.state.get("version"),
            "source": self.state.get("source"),
            "created_at": self.state.get("created_at"),
            "updated_at": self.state.get("updated_at"),
            "deleted_at": self.state.get("deleted_at"),
        }


class PersonaRepository:
    def __init__(
        self,
        root: Path,
        *,
        max_personas: int = DEFAULT_MAX_PERSONAS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep_versions: int = DEFAULT_KEEP_VERSIONS,
        render: Callable[[Any], str] | None = None,
        on_change: Callable[[str, str], None] | None = None,
        mirror_dir: Path | None = None,
    ) -> None:
        self.root = Path(root)
        # 引擎读取人格的目录（OpenHer persona/personas）。
        # #83 阶段靠复制 SOUL.md 让它"生效"；真正的按用户隔离由 #84 接管。
        self.mirror_dir = Path(mirror_dir) if mirror_dir else None
        self.max_personas = max_personas
        self.max_bytes = max_bytes
        self.keep_versions = keep_versions
        self._render = render or (lambda profile: profile_to_soul(profile)["markdown"])
        self._on_change = on_change

    # ── 路径 ──────────────────────────────────────────────
    def _user_dir(self, user: str) -> Path:
        if not USER_RE.match(user or ""):
            raise PersonaError(f"非法 user_id: {user!r}")
        return self.root / user

    def _dir(self, user: str, persona_id: str) -> Path:
        if not SLUG_RE.match(persona_id or ""):
            raise InvalidProfileError("persona_id 必须是 1-64 位小写字母/数字/连字符")
        return self._user_dir(user) / "personas" / persona_id

    # ── 读写 ──────────────────────────────────────────────
    def _read(self, user: str, persona_id: str, *, allow_deleted: bool = False) -> PersonaRecord:
        base = self._dir(user, persona_id)
        profile_file = base / "profile.json"
        state_file = base / "state.json"
        if not profile_file.is_file() or not state_file.is_file():
            raise NotFoundError(f"persona {persona_id} 不存在")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("deleted_at") and not allow_deleted:
            raise NotFoundError(f"persona {persona_id} 不存在")
        return PersonaRecord(persona_id, json.loads(profile_file.read_text(encoding="utf-8")), state)

    def _persist(self, user: str, persona_id: str, profile: Any, state: dict) -> PersonaRecord:
        base = self._dir(user, persona_id)
        payload = profile.to_dict()
        text = _dump(payload)
        if len(text.encode("utf-8")) > self.max_bytes:
            raise InvalidProfileError(f"profile 超过 {self.max_bytes} 字节上限")
        _atomic_write(base / "profile.json", text)
        _atomic_write(base / "state.json", _dump(state))
        version = int(state.get("version") or 1)
        _atomic_write(base / "versions" / f"v{version:04d}.json", text)
        self._prune_versions(base)
        try:
            markdown = self._render(profile)
            _atomic_write(base / "SOUL.md", markdown)
            if self.mirror_dir is not None:
                _atomic_write(self.mirror_dir / persona_id / "SOUL.md", markdown)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不应阻断持久化
            state["render_error"] = str(exc)[:200]
            _atomic_write(base / "state.json", _dump(state))
        if self._on_change:
            self._on_change(user, persona_id)
        return PersonaRecord(persona_id, payload, state)

    def _prune_versions(self, base: Path) -> None:
        versions = sorted((base / "versions").glob("v*.json"))
        for stale in versions[: max(0, len(versions) - self.keep_versions)]:
            stale.unlink(missing_ok=True)

    # ── 校验 ──────────────────────────────────────────────
    def _validate(self, payload: dict) -> Any:
        hit = _find_forbidden(payload)
        if hit:
            raise RawTextNotAllowedError(f"禁止字段 {hit}：人格不得携带原文")
        try:
            profile = profile_from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise InvalidProfileError(f"结构无法解析：{exc}") from exc
        errors = validate_profile(profile)
        if errors:
            raise InvalidProfileError("; ".join(errors[:5]))
        return profile

    # ── 公开操作 ──────────────────────────────────────────
    def list(self, user: str, *, include_deleted: bool = False) -> list[dict]:
        base = self._user_dir(user) / "personas"
        if not base.is_dir():
            return []
        out = []
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            try:
                record = self._read(user, entry.name, allow_deleted=include_deleted)
            except NotFoundError:
                continue
            out.append(record.summary())
        return out

    def get(self, user: str, persona_id: str) -> dict:
        record = self._read(user, persona_id)
        return {"profile": record.profile, "state": record.state}

    def create(self, user: str, payload: dict, *, source: str = "manual") -> dict:
        persona_id = payload.get("persona_id") or ""
        if not SLUG_RE.match(persona_id):
            raise InvalidProfileError("persona_id 必须是 1-64 位小写字母/数字/连字符")
        if (self._dir(user, persona_id) / "profile.json").exists():
            raise ConflictError(f"persona {persona_id} 已存在")
        active = [p for p in self.list(user) if not p["deleted_at"]]
        if len(active) >= self.max_personas:
            raise QuotaExceededError(f"每人最多 {self.max_personas} 个人格")
        profile = self._validate(payload)
        state = {
            "version": 1,
            "created_at": _now(),
            "updated_at": _now(),
            "source": source,
            "deleted_at": None,
        }
        record = self._persist(user, persona_id, profile, state)
        return record.summary()

    def replace(self, user: str, persona_id: str, payload: dict, expected_version: int) -> dict:
        record = self._read(user, persona_id)
        if int(record.state.get("version", 0)) != expected_version:
            raise ConflictError(
                f"版本冲突：当前 {record.state.get('version')}，请求 {expected_version}"
            )
        if payload.get("persona_id") and payload["persona_id"] != persona_id:
            raise InvalidProfileError("请求体 persona_id 与路径不一致")
        payload = {**payload, "persona_id": persona_id}
        profile = self._validate(payload)
        state = {**record.state, "version": expected_version + 1, "updated_at": _now()}
        return self._persist(user, persona_id, profile, state).summary()

    def patch(self, user: str, persona_id: str, changes: dict, expected_version: int) -> dict:
        record = self._read(user, persona_id)
        if int(record.state.get("version", 0)) != expected_version:
            raise ConflictError(
                f"版本冲突：当前 {record.state.get('version')}，请求 {expected_version}"
            )
        merged = _merge(record.profile, changes)
        merged["persona_id"] = persona_id
        profile = self._validate(merged)
        state = {**record.state, "version": expected_version + 1, "updated_at": _now()}
        return self._persist(user, persona_id, profile, state).summary()

    def delete(self, user: str, persona_id: str) -> dict:
        record = self._read(user, persona_id)
        state = {**record.state, "deleted_at": _now(), "updated_at": _now()}
        _atomic_write(self._dir(user, persona_id) / "state.json", _dump(state))
        if self.mirror_dir is not None:
            shutil.rmtree(self.mirror_dir / persona_id, ignore_errors=True)
        if self._on_change:
            self._on_change(user, persona_id)
        return {"persona_id": persona_id, "deleted_at": state["deleted_at"]}

    def restore(self, user: str, persona_id: str) -> dict:
        record = self._read(user, persona_id, allow_deleted=True)
        if not record.state.get("deleted_at"):
            return record.summary()
        state = {**record.state, "deleted_at": None, "updated_at": _now()}
        _atomic_write(self._dir(user, persona_id) / "state.json", _dump(state))
        if self.mirror_dir is not None:
            _atomic_write(
                self.mirror_dir / persona_id / "SOUL.md",
                self._render(profile_from_dict(record.profile)),
            )
        if self._on_change:
            self._on_change(user, persona_id)
        return PersonaRecord(persona_id, record.profile, state).summary()

    def fork(self, user: str, source_id: str, *, new_id: str | None = None) -> dict:
        """从当前用户已有的人格复制一份可编辑副本。"""
        record = self._read(user, source_id)
        target = new_id or f"{source_id}-copy"
        if (self._dir(user, target) / "profile.json").exists():
            raise ConflictError(f"persona {target} 已存在")
        payload = {**record.profile, "persona_id": target}
        return self.create(user, payload, source=f"fork:{source_id}")

    def purge(self, user: str, persona_id: str) -> None:
        shutil.rmtree(self._dir(user, persona_id), ignore_errors=True)


__all__ = [
    "PersonaError", "NotFoundError", "ConflictError", "InvalidProfileError",
    "RawTextNotAllowedError", "ReadOnlyError", "QuotaExceededError",
    "PersonaRecord", "PersonaRepository",
]
