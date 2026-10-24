"""AgentExtraStore 的零依赖测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.agent_state import AgentExtraStore  # noqa: E402


def _agent(user: str = "u1", persona: str = "luna", *, fresh: bool = False):
    return SimpleNamespace(
        user_id=user,
        persona=SimpleNamespace(persona_id=persona),
        _turn_count=0 if fresh else 7,
        _relationship_ema={} if fresh else {
            "relationship_depth": 0.42,
            "trust_level": 0.31,
            "emotional_valence": 0.2,
            "pending_foresight": 0.0,
        },
        _last_reward=0.5 if not fresh else 0.0,
        _last_modality="文字" if not fresh else "",
        _last_signals={} if fresh else {"connection": 0.7},
        _last_drive_satisfaction={} if fresh else {"connection": 0.1},
        _prev_signals={} if fresh else {"connection": 0.6},
        _search_hit=3 if not fresh else 0,
        _search_timeout=1 if not fresh else 0,
        _search_fallback=0 if not fresh else 0,
        _search_relevant_used=2 if not fresh else 0,
    )


def test_roundtrip_relationship_and_turn_count() -> None:
    store = AgentExtraStore(":memory:")
    saved = store.save(_agent())
    assert saved is True

    fresh = _agent(fresh=True)
    data = store.load("u1", "luna")
    assert data["relationship"]["relationship_depth"] == 0.42
    assert store.apply(fresh, data) is True
    assert fresh._turn_count == 7
    assert fresh._relationship_ema["trust_level"] == 0.31
    assert fresh._search_hit == 3
    assert fresh._last_modality == "文字"
    store.close()


def test_apply_does_not_overwrite_live_agent() -> None:
    store = AgentExtraStore(":memory:")
    store.save(_agent())
    live = _agent()  # 已有 turn_count / relationship
    assert store.apply(live, store.load("u1", "luna")) is False
    assert live._turn_count == 7
    store.close()


def test_empty_agent_is_not_saved() -> None:
    store = AgentExtraStore(":memory:")
    assert store.save(_agent(fresh=True)) is False
    assert store.load("u1", "luna") == {}
    store.close()


def test_user_and_persona_are_isolated() -> None:
    store = AgentExtraStore(":memory:")
    store.save(_agent("u1", "luna"))
    other = _agent("u2", "luna")
    other._relationship_ema = {"relationship_depth": 0.9}
    store.save(other)
    store.save(_agent("u1", "iris"))

    assert store.load("u1", "luna")["relationship"]["relationship_depth"] == 0.42
    assert store.load("u2", "luna")["relationship"]["relationship_depth"] == 0.9
    assert store.load("u1", "iris")["relationship"]["relationship_depth"] == 0.42
    assert store.load("u3", "luna") == {}
    store.close()


def test_file_persists_across_reopen() -> None:
    path = Path(tempfile.mkdtemp()) / "extra.db"
    first = AgentExtraStore(path)
    first.save(_agent())
    first.close()

    second = AgentExtraStore(path)
    assert second.load("u1", "luna")["turn_count"] == 7
    second.close()


if __name__ == "__main__":
    test_roundtrip_relationship_and_turn_count()
    test_apply_does_not_overwrite_live_agent()
    test_empty_agent_is_not_saved()
    test_user_and_persona_are_isolated()
    test_file_persists_across_reopen()
    print("agent state ok (5 tests)")
