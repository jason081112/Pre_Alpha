"""provider 注册表：定义可选 provider、模型与校验规则。

只做"有什么可选"，不负责创建客户端；创建由 shell.routing 交给上游
providers.registry 完成，保证与 OpenHer 的行为一致。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("providers.json")


class RouteError(ValueError):
    """路由不合法：provider 不存在、模型不在清单内、格式错误。"""


@dataclass(frozen=True)
class Route:
    """一条具体的路由：provider + model。"""

    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> "Route":
        if not value or "/" not in value:
            raise RouteError(f"路由格式应为 provider/model，收到: {value!r}")
        provider, model = value.split("/", 1)
        if not provider or not model:
            raise RouteError(f"路由格式应为 provider/model，收到: {value!r}")
        return cls(provider=provider, model=model)

    def as_str(self) -> str:
        return f"{self.provider}/{self.model}"

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.as_str()


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    upstream: str
    base_url: str
    api_key_env: str
    models: tuple[str, ...]
    caps: tuple[str, ...]

    def available(self) -> bool:
        """当前进程是否具备调用条件（只看 Key 是否存在）。"""
        if not self.api_key_env:
            return True
        return bool(os.getenv(self.api_key_env, "").strip())

    def has_model(self, model: str) -> bool:
        return not self.models or model in self.models

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "base_url": self.base_url,
            "models": list(self.models),
            "caps": list(self.caps),
            "api_key_env": self.api_key_env,
            "available": self.available(),
        }


class Registry:
    """provider 注册表。"""

    def __init__(self, specs: dict[str, ProviderSpec]):
        if not specs:
            raise RouteError("provider 注册表为空")
        self._specs = dict(specs)

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Registry":
        registry_path = Path(path or os.getenv("ALPHA_PROVIDERS_FILE") or DEFAULT_REGISTRY_PATH)
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        specs: dict[str, ProviderSpec] = {}
        for provider_id, item in (raw.get("providers") or {}).items():
            specs[provider_id] = ProviderSpec(
                id=provider_id,
                upstream=item.get("upstream") or provider_id,
                base_url=item.get("base_url") or "",
                api_key_env=item.get("api_key_env") or "",
                models=tuple(item.get("models") or ()),
                caps=tuple(item.get("caps") or ()),
            )
        return cls(specs)

    # ── 查询 ──────────────────────────────────────────────
    def get(self, provider: str) -> ProviderSpec:
        try:
            return self._specs[provider]
        except KeyError:
            known = ", ".join(sorted(self._specs))
            raise RouteError(f"未知 provider: {provider!r}（可选: {known}）") from None

    def resolve(self, provider: str, model: str) -> Route:
        spec = self.get(provider)
        if not model:
            raise RouteError("model 不能为空")
        if not spec.has_model(model):
            known = ", ".join(spec.models)
            raise RouteError(f"provider {provider!r} 不支持模型 {model!r}（可选: {known}）")
        return Route(provider=provider, model=model)

    def specs(self) -> Iterable[ProviderSpec]:
        return self._specs.values()

    def catalog(self) -> list[dict]:
        return [spec.to_dict() for spec in sorted(self._specs.values(), key=lambda s: s.id)]

    def all_routes(self) -> list[Route]:
        routes: list[Route] = []
        for spec in self._specs.values():
            for model in spec.models:
                routes.append(Route(spec.id, model))
        return routes
