"""文本人格 → OpenHer 数值驱力的桥接层。

设计见 `docs/architecture.md`「映射设计」与 `docs/p3-31-person-profile.md`。

- `bridge.model`        —— PersonProfile 数据模型与校验（#31）
- `bridge.profile2soul` —— 映射实现（#32 起，当前为占位）
"""

from bridge.model import (  # noqa: F401
    DRIVES,
    ENGINE_PARAM_KEYS,
    Asserted,
    ConflictProfile,
    Correction,
    EngineHints,
    Expression,
    Identity,
    LogicProfile,
    PersonProfile,
    Provenance,
    RelationContext,
    validate_profile,
)

__all__ = [
    "DRIVES",
    "ENGINE_PARAM_KEYS",
    "Asserted",
    "ConflictProfile",
    "Correction",
    "EngineHints",
    "Expression",
    "Identity",
    "LogicProfile",
    "PersonProfile",
    "Provenance",
    "RelationContext",
    "validate_profile",
]
