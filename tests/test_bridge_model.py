# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#31 PersonProfile 数据模型与 JSON Schema 的回归校验。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import (  # noqa: E402
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

SCHEMA_PATH = ROOT / "bridge" / "profile.schema.json"


def demo_profile() -> PersonProfile:
    """以 demo-relationship 的 golden 内容为素材构造一份 profile。"""
    return PersonProfile(
        persona_id="xiaolan",
        identity=Identity(
            name=Asserted.source("小岚"),
            name_zh=Asserted.source("小岚"),
            lang="zh",
            age=Asserted.missing("meta.profile.age_range 为『未提供』"),
            gender=Asserted.missing("meta.profile.gender 为『未提供』"),
            mbti=Asserted.missing("六层不含 MBTI"),
        ),
        relation=RelationContext(
            label=Asserted.source("亲密朋友 / 暧昧关系"),
            status=Asserted.source("稳定，但会因为失约和重复承诺生气"),
            defining_facts=[
                Asserted.source("对方会临时被工作拖住，但并不是不在乎"),
                Asserted.source("在意『答应过的事有没有被记住』"),
                Asserted.source("具体的照顾记忆：下雨、伞、地铁口"),
            ],
        ),
        expression=Expression(
            rhythm=Asserted.source("短句为主，停顿多；不连续输出大段解释"),
            habits=[
                Asserted.source("嗯。"),
                Asserted.source("你自己想。"),
                Asserted.source("算了，你忙。"),
            ],
        ),
        logic=LogicProfile(
            opens_up_when=Asserted.source("对方记得具体细节并给出可执行补救"),
            pulls_away_when=Asserted.source("对方重复失约或用『我忙』盖过去"),
            defends_by=Asserted.source("把话收短，用反问让对方先意识到问题"),
            shows_care_by=Asserted.source("提醒具体风险、安排具体见面"),
        ),
        conflict=ConflictProfile(
            style=Asserted.source("先冷一下，再把具体不满说清楚"),
            boundaries=[
                Asserted.source("不接受反复承诺又不兑现"),
                Asserted.source("不接受把『忙』当成不解释的理由"),
            ],
        ),
        memory_signature=[
            Asserted.source("下雨天、伞、地铁口"),
            Asserted.source("被记住的照顾细节比甜言蜜语更重要"),
        ],
        hard_rules=[
            Asserted.source("被忽略时把话收短，不直接说『我需要你』"),
        ],
        corrections=[Correction(scene="被临时放鸽子", wrong="立刻撒娇", correct="收短回话")],
        engine=EngineHints(
            drive_baseline={d: Asserted.missing("尚未标定（#34）") for d in DRIVES},
            engine_params={},
            calibration=Asserted.missing("标定规则见 #33/#34"),
        ),
        meta={"source": "tests/fixtures/golden/demo-relationship"},
    )


def test_demo_profile_is_valid() -> None:
    errors = validate_profile(demo_profile())
    assert not errors, errors


def test_identity_name_required() -> None:
    profile = demo_profile()
    profile.identity.name = Asserted.missing("没有名字")
    errors = validate_profile(profile)
    assert any("identity.name" in e for e in errors), errors


def test_inferred_requires_basis() -> None:
    profile = demo_profile()
    profile.logic.defends_by = Asserted(
        value="用沉默表达不满", provenance=Provenance.INFERRED, basis="  "
    )
    errors = validate_profile(profile)
    assert any("inferred 必须给出 basis" in e for e in errors), errors


def test_inferred_basis_cannot_claim_insufficient() -> None:
    """#29 F5：既说材料不足又补推断，必须拒绝。"""
    profile = demo_profile()
    profile.logic.defends_by = Asserted(
        value="用沉默表达不满",
        provenance=Provenance.INFERRED,
        basis="insufficient source material; inferred from tone",
    )
    errors = validate_profile(profile)
    assert any("insufficient" in e for e in errors), errors


def test_missing_requires_reason_and_no_value() -> None:
    profile = demo_profile()
    profile.identity.mbti = Asserted(value="ENFP", provenance=Provenance.MISSING, reason="")
    errors = validate_profile(profile)
    assert any("missing 必须给出 reason" in e for e in errors), errors
    assert any("missing 不应携带 value" in e for e in errors), errors


def test_source_requires_value() -> None:
    profile = demo_profile()
    profile.expression.rhythm = Asserted(value=None, provenance=Provenance.SOURCE)
    errors = validate_profile(profile)
    assert any("source 状态必须有 value" in e for e in errors), errors


def test_rejects_unknown_drive() -> None:
    profile = demo_profile()
    profile.engine.drive_baseline["aggression"] = Asserted.source(0.8)
    errors = validate_profile(profile)
    assert any("不是合法的 drive" in e for e in errors), errors


def test_rejects_unknown_engine_param() -> None:
    profile = demo_profile()
    profile.engine.engine_params["temperature"] = Asserted.source(0.7)
    errors = validate_profile(profile)
    assert any("不在允许的 11 个 engine_params 内" in e for e in errors), errors


def test_rejects_forbidden_keys() -> None:
    profile = demo_profile()
    profile.meta["raw_text"] = "原始聊天记录"
    errors = validate_profile(profile)
    assert any("禁止字段" in e for e in errors), errors


def test_rejects_too_many_corrections() -> None:
    profile = demo_profile()
    profile.corrections = [Correction(scene="s", wrong="w", correct="c") for _ in range(51)]
    errors = validate_profile(profile)
    assert any("上限 50" in e for e in errors), errors


def test_persona_id_rules() -> None:
    profile = demo_profile()
    profile.persona_id = "XiaoLan"
    errors = validate_profile(profile)
    assert any("必须小写" in e for e in errors), errors

    profile.persona_id = ""
    errors = validate_profile(profile)
    assert any("不能为空" in e for e in errors), errors


def test_serialization_keeps_provenance() -> None:
    data = demo_profile().to_dict()
    name = data["identity"]["name"]
    assert name["kind"] == "asserted"
    assert name["provenance"] == "source"
    assert name["value"] == "小岚"
    assert data["schema_version"] == "2.0"
    assert isinstance(data["corrections"], list)


def test_schema_matches_model_constants() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    engine = schema["$defs"]["engine"]["properties"]
    assert set(engine["drive_baseline"]["properties"]) == set(DRIVES)
    assert set(engine["engine_params"]["properties"]) == set(ENGINE_PARAM_KEYS)
    asserted = schema["$defs"]["assertedText"]
    assert asserted["properties"]["provenance"]["$ref"].endswith("provenance")
    assert schema["$defs"]["provenance"]["enum"] == ["source", "inferred", "missing"]
    # 纠错层上限与模型一致
    assert schema["properties"]["corrections"]["maxItems"] == 50
    assert schema["properties"]["expression"]["$ref"].endswith("expression")


def test_schema_validates_serialized_profile() -> None:
    """有 jsonschema 时用 schema 严格校验序列化结果；CI 零依赖环境则跳过。"""
    try:
        import jsonschema
    except ImportError:
        print("SKIP: jsonschema 未安装")
        return
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(demo_profile().to_dict(), schema)


def test_schema_rejects_raw_text_fields() -> None:
    """schema 里不应出现任何允许原文的字段名。"""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    for forbidden in ("raw_text", "chat_log", "transcript", "conversation"):
        assert f'"{forbidden}"' not in text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} bridge model tests passed")
