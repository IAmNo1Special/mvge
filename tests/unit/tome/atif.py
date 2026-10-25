from __future__ import annotations

from pathlib import Path

from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import (
    ATIFMetrics,
    ATIFTrajectory,
    ATIFTrajectoryStep,
    TomeEntry,
    TomeEntryType,
)


def test_atif_dataclasses_and_dict() -> None:
    step = ATIFTrajectoryStep(
        step_id="s1",
        role="assistant",
        content="Let me check that file.",
        tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "a.txt"}}],
        reasoning_content="Need to see file content first.",
        timestamp=100.0,
        latency_ms=250.5,
        model="anthropic/claude-opus-4.6",
    )
    metrics = ATIFMetrics(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        reasoning_tokens=30,
    )
    trajectory = ATIFTrajectory(
        trajectory_id="tome-test",
        agent_name="coding_mvge",
        model="anthropic/claude-opus-4.6",
        created_at="2026-09-18T10:00:00Z",
        steps=[step],
        metrics=metrics,
        completed=True,
    )

    d = trajectory.to_dict()
    assert d["trajectory_id"] == "tome-test"
    assert d["agent_name"] == "coding_mvge"
    assert d["model"] == "anthropic/claude-opus-4.6"
    assert len(d["steps"]) == 1
    assert d["steps"][0]["role"] == "assistant"
    assert d["steps"][0]["reasoning_content"] == "Need to see file content first."
    assert d["steps"][0]["tool_calls"] == [
        {"id": "c1", "name": "read_file", "args": {"path": "a.txt"}}
    ]
    assert d["metrics"]["reasoning_tokens"] == 30
    assert d["completed"] is True


def test_export_and_replay_atif_trajectory(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    write = factory.create_tome(
        cwd=str(tmp_path),
        tome_id="tome_replay_1",
        model="anthropic/claude-opus-4.6",
    )

    # 1. User message
    e1 = TomeEntry(
        id="m1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": "user", "content": "What is 2+2?"},
    )
    write.append(e1)
    write.append_leaf(e1.id)

    # 2. Assistant message with contemplation and tool call
    e2 = TomeEntry(
        id="m2",
        parent_id="m1",
        type=TomeEntryType.MESSAGE,
        timestamp=1001.5,
        payload={
            "role": "assistant",
            "content": [
                {"type": "contemplation", "thinking": "Calculating 2+2"},
                {
                    "type": "spell_cast",
                    "id": "call_1",
                    "spell": "calculator",
                    "args": {"expr": "2+2"},
                },
                {"type": "text", "text": "I will calculate this."},
            ],
            "model": "anthropic/claude-opus-4.6",
            "mana_usage": {"prompt_tokens": 50, "completion_tokens": 20},
        },
    )
    write.append(e2)
    write.append_leaf(e2.id)

    # 3. Tool result
    e3 = TomeEntry(
        id="m3",
        parent_id="m2",
        type=TomeEntryType.MESSAGE,
        timestamp=1002.0,
        payload={
            "role": "spellResult",
            "spell_cast_id": "call_1",
            "content": "4",
        },
    )
    write.append(e3)
    write.append_leaf(e3.id)

    # 4. Final assistant message
    e4 = TomeEntry(
        id="m4",
        parent_id="m3",
        type=TomeEntryType.MESSAGE,
        timestamp=1003.0,
        payload={
            "role": "assistant",
            "content": "2+2 is 4.",
            "model": "anthropic/claude-opus-4.6",
            "mana_usage": {"prompt_tokens": 80, "completion_tokens": 15},
        },
    )
    write.append(e4)
    write.append_leaf(e4.id)

    # Test replay
    steps = factory.replay_tome_trajectory("tome_replay_1")
    assert len(steps) == 4
    assert steps[0].role == "user"
    assert steps[0].content == "What is 2+2?"

    assert steps[1].role == "assistant"
    assert steps[1].reasoning_content == "Calculating 2+2"
    assert len(steps[1].tool_calls) == 1
    assert steps[1].tool_calls[0]["name"] == "calculator"
    assert steps[1].latency_ms == 1500.0

    assert steps[2].role == "tool"
    assert steps[2].tool_call_id == "call_1"
    assert steps[2].content == "4"

    assert steps[3].role == "assistant"
    assert steps[3].content == "2+2 is 4."

    # Test export
    exported = factory.export_atif_trajectory("tome_replay_1", "test-agent")
    assert exported["trajectory_id"] == "tome_replay_1"
    assert exported["model"] == "anthropic/claude-opus-4.6"
    assert len(exported["steps"]) == 4
    assert exported["metrics"]["input_tokens"] == 130
    assert exported["metrics"]["output_tokens"] == 35
