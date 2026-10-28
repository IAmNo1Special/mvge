from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.agent.harness.compaction import (
    DEFAULT_COMPACTION_SETTINGS,
    SUMMARIZATION_SYSTEM_PROMPT,
    CompactionRunner,
    CompactionSettings,
    calculate_context_mana,
    estimate_context_mana,
    estimate_invocation_mana,
    find_cut_point,
    generate_summary,
    prepare_compaction,
    should_compact,
)
from mvge.core.channel import (
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvge.core.events import (
    MvgeEvent,
    MvgeEventType,
)
from mvge.core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvge.core.spells import (
    MvgeSpell,
    SpellResultMessage,
)


class TestCompactionSettings:
    def test_defaults_mirror_pi(self) -> None:
        assert DEFAULT_COMPACTION_SETTINGS.enabled is True
        assert DEFAULT_COMPACTION_SETTINGS.reserve_mana == 16384
        assert DEFAULT_COMPACTION_SETTINGS.keep_recent_mana == 20000


class TestCalculateContextMana:
    def test_prefers_total(self) -> None:
        assert calculate_context_mana({"total": 500, "input": 1, "output": 2}) == 500

    def test_falls_back_to_sum(self) -> None:
        assert calculate_context_mana({"input": 300, "output": 200}) == 500

    def test_empty_usage_is_zero(self) -> None:
        assert calculate_context_mana({}) == 0


class TestShouldCompact:
    def test_false_when_disabled(self) -> None:
        settings = CompactionSettings(enabled=False)
        assert should_compact(999_999, 128_000, settings) is False

    def test_false_below_threshold(self) -> None:
        assert should_compact(1000, 128_000, DEFAULT_COMPACTION_SETTINGS) is False

    def test_true_above_threshold(self) -> None:
        contested = 128_000 - DEFAULT_COMPACTION_SETTINGS.reserve_mana + 1
        assert should_compact(contested, 128_000, DEFAULT_COMPACTION_SETTINGS) is True


class TestEstimateInvocationMana:
    def test_summoner_request_from_text(self) -> None:
        request = SummonerRequest(role="user", content="a" * 40)
        assert estimate_invocation_mana(request) == 10

    def test_mvge_response_counts_text_blocks(self) -> None:
        response = MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "b" * 20}],
        )
        assert estimate_invocation_mana(response) == 5

    def test_mvge_response_counts_spell_calls(self) -> None:
        response = MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "1", "name": "bash", "arguments": {}},
                }
            ],
        )
        assert estimate_invocation_mana(response) > 0

    def test_spell_result_from_text(self) -> None:
        result = SpellResultMessage(
            spell_cast_id="1",
            spell_name="bash",
            content=[{"type": "text", "text": "c" * 400}],
        )
        assert estimate_invocation_mana(result) == 100


class TestEstimateContextMana:
    def test_uses_last_assistant_usage_when_present(self) -> None:
        invocations = [
            SummonerRequest(role="user", content="hello"),
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "hi"}],
                stop_reason=StopReason.STOP,
                mana_usage={"total": 5000},
            ),
        ]
        estimate = estimate_context_mana(invocations)
        assert estimate.usage_mana == 5000
        assert estimate.last_usage_index == 1
        assert estimate.mana == 5000

    def test_adds_trailing_invocations_after_usage(self) -> None:
        invocations = [
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "hi"}],
                stop_reason=StopReason.STOP,
                mana_usage={"total": 1000},
            ),
            SummonerRequest(role="user", content="d" * 40),
        ]
        estimate = estimate_context_mana(invocations)
        assert estimate.usage_mana == 1000
        assert estimate.trailing_mana == 10
        assert estimate.mana == 1010

    def test_falls_back_to_heuristic_without_usage(self) -> None:
        invocations = [SummonerRequest(role="user", content="e" * 40)]
        estimate = estimate_context_mana(invocations)
        assert estimate.last_usage_index is None
        assert estimate.mana == 10

    def test_ignores_usage_from_errored_response(self) -> None:
        invocations = [
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "boom"}],
                stop_reason=StopReason.ERROR,
                mana_usage={"total": 9999},
            )
        ]
        estimate = estimate_context_mana(invocations)
        assert estimate.last_usage_index is None


class TestFindCutPoint:
    def test_no_candidates_keeps_everything(self) -> None:
        result = find_cut_point([], 0, 0, 20000)
        assert result.first_kept_index == 0
        assert result.is_split_turn is False

    def test_cuts_at_summoner_request_boundary(self) -> None:
        invocations = [
            SummonerRequest(role="user", content="f" * 400),
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "g" * 400}],
                stop_reason=StopReason.STOP,
            ),
            SummonerRequest(role="user", content="h" * 400),
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "i" * 400}],
                stop_reason=StopReason.STOP,
            ),
        ]
        result = find_cut_point(invocations, 0, len(invocations), keep_recent_mana=150)
        assert 0 <= result.first_kept_index < len(invocations)

    def test_never_cuts_before_start(self) -> None:
        invocations = [SummonerRequest(role="user", content="j" * 40)]
        result = find_cut_point(invocations, 0, 1, keep_recent_mana=999_999)
        assert result.first_kept_index == 0

    def test_spell_result_is_not_a_cut_point(self) -> None:
        invocations = [
            SummonerRequest(role="user", content="k" * 40),
            SpellResultMessage(
                spell_cast_id="1",
                spell_name="bash",
                content=[{"type": "text", "text": "l" * 4000}],
            ),
        ]
        result = find_cut_point(invocations, 0, 2, keep_recent_mana=10)
        cut = invocations[result.first_kept_index]
        assert not isinstance(cut, SpellResultMessage)


def _long_transcript() -> list[SummonerRequest | MvgeResponse]:
    invocations: list[SummonerRequest | MvgeResponse] = []
    for index in range(6):
        invocations.append(SummonerRequest(role="user", content=f"ask {index} " * 60))
        invocations.append(
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": f"reply {index} " * 60}],
                stop_reason=StopReason.STOP,
            )
        )
    return invocations


class TestPrepareCompaction:
    def test_returns_none_for_empty_transcript(self) -> None:
        assert prepare_compaction([], DEFAULT_COMPACTION_SETTINGS) is None

    def test_splits_transcript_into_summarize_and_retain(self) -> None:
        invocations = _long_transcript()

        prepared = prepare_compaction(
            invocations, CompactionSettings(keep_recent_mana=100)
        )

        assert prepared is not None
        assert prepared.to_summarize
        assert prepared.retained_tail
        total = len(prepared.to_summarize) + len(prepared.retained_tail)
        assert total == len(invocations)

    def test_records_mana_before(self) -> None:
        invocations = _long_transcript()

        prepared = prepare_compaction(
            invocations, CompactionSettings(keep_recent_mana=100)
        )

        assert prepared is not None
        assert prepared.mana_before > 0

    def test_carries_previous_summary_forward(self) -> None:
        invocations = _long_transcript()

        prepared = prepare_compaction(
            invocations,
            CompactionSettings(keep_recent_mana=100),
            previous_summary="earlier work",
        )

        assert prepared is not None
        assert prepared.previous_summary == "earlier work"

    def test_returns_none_when_nothing_to_summarize(self) -> None:
        invocations = [SummonerRequest(role="user", content="short")]

        prepared = prepare_compaction(
            invocations, CompactionSettings(keep_recent_mana=999_999)
        )

        assert prepared is None


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_calls_summarize_with_system_prompt(self) -> None:
        captured: dict[str, object] = {}

        async def summarize(
            messages: list[dict[str, str]], signal: Any | None = None
        ) -> str:
            captured["messages"] = messages
            return "the summary"

        result = await generate_summary(
            [SummonerRequest(role="user", content="hello")], summarize
        )

        assert result == "the summary"
        messages = captured["messages"]
        assert isinstance(messages, list)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SUMMARIZATION_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_includes_transcript_text(self) -> None:
        captured: dict[str, object] = {}

        async def summarize(
            messages: list[dict[str, str]], signal: Any | None = None
        ) -> str:
            captured["messages"] = messages
            return "s"

        await generate_summary(
            [SummonerRequest(role="user", content="find the bug")], summarize
        )

        messages = captured["messages"]
        assert isinstance(messages, list)
        joined = " ".join(m["content"] for m in messages)
        assert "find the bug" in joined

    @pytest.mark.asyncio
    async def test_previous_summary_switches_to_update_prompt(self) -> None:
        captured: dict[str, object] = {}

        async def summarize(
            messages: list[dict[str, str]], signal: Any | None = None
        ) -> str:
            captured["messages"] = messages
            return "s"

        await generate_summary(
            [SummonerRequest(role="user", content="more work")],
            summarize,
            previous_summary="what came before",
        )

        messages = captured["messages"]
        assert isinstance(messages, list)
        joined = " ".join(m["content"] for m in messages)
        assert "what came before" in joined

    @pytest.mark.asyncio
    async def test_returns_none_when_summarize_fails(self) -> None:
        async def summarize(
            messages: list[dict[str, str]], signal: Any | None = None
        ) -> str:
            raise RuntimeError("realm exploded")

        result = await generate_summary(
            [SummonerRequest(role="user", content="hello")], summarize
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_blank_summary(self) -> None:
        async def summarize(
            messages: list[dict[str, str]], signal: Any | None = None
        ) -> str:
            return "   "

        result = await generate_summary(
            [SummonerRequest(role="user", content="hello")], summarize
        )

        assert result is None


def _model() -> Model:
    return Model(
        id="test-model",
        name="Test",
        realm="test",
        base_url="",
        api_key="",
        context_window=1000,
    )


def _long_transcript() -> list[MvgeInvocation]:
    invocations: list[MvgeInvocation] = []
    for index in range(8):
        invocations.append(SummonerRequest(role="user", content=f"ask {index} " * 80))
        invocations.append(
            MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": f"reply {index} " * 80}],
                stop_reason=StopReason.STOP,
                mana_usage={"total": 900},
            )
        )
    return invocations


class Recorder:
    def __init__(self) -> None:
        self.events: list[MvgeEvent] = []

    async def __call__(self, event: MvgeEvent) -> None:
        self.events.append(event)

    def types(self) -> list[MvgeEventType]:
        return [event.type for event in self.events]


def _realm(summary: str = "## Goal\nShip it.") -> Any:
    realm = MagicMock()
    realm.complete = AsyncMock(
        return_value=RealmResponse(
            model=_model(),
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": summary}],
                stop_reason=StopReason.STOP,
            ),
        )
    )
    return realm


class TestNoCompactionNeeded:
    @pytest.mark.asyncio
    async def test_short_transcript_returns_none(self) -> None:
        runner = CompactionRunner(realm=_realm(), model=_model(), emit=Recorder())

        result = await runner.maybe_compact(
            [SummonerRequest(role="user", content="hi")]
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_settings_never_compact(self) -> None:
        emit = Recorder()
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=emit,
            settings=CompactionSettings(enabled=False),
        )

        result = await runner.maybe_compact(_long_transcript())

        assert result is None
        assert MvgeEventType.COMPACTION_START not in emit.types()

    @pytest.mark.asyncio
    async def test_realm_not_called_when_below_threshold(self) -> None:
        realm = _realm()
        runner = CompactionRunner(realm=realm, model=_model(), emit=Recorder())

        await runner.maybe_compact([SummonerRequest(role="user", content="hi")])

        realm.complete.assert_not_called()


class TestCompactionRuns:
    @pytest.mark.asyncio
    async def test_returns_shorter_transcript(self) -> None:
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )
        invocations = _long_transcript()

        result = await runner.maybe_compact(invocations)

        assert result is not None
        assert len(result) < len(invocations)

    @pytest.mark.asyncio
    async def test_summary_is_first_invocation(self) -> None:
        runner = CompactionRunner(
            realm=_realm("## Goal\nShip it."),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        result = await runner.maybe_compact(_long_transcript())

        assert result is not None
        first = result[0]
        assert isinstance(first, SummonerRequest)
        assert "Ship it." in str(first.content)

    @pytest.mark.asyncio
    async def test_emits_start_and_end_events(self) -> None:
        emit = Recorder()
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=emit,
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        await runner.maybe_compact(_long_transcript())

        types = emit.types()
        assert MvgeEventType.COMPACTION_START in types
        assert MvgeEventType.COMPACTION_END in types

    @pytest.mark.asyncio
    async def test_realm_called_without_spells(self) -> None:
        realm = _realm()
        runner = CompactionRunner(
            realm=realm,
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        await runner.maybe_compact(_long_transcript())

        config = realm.complete.await_args.args[2]
        assert not config.tools

    @pytest.mark.asyncio
    async def test_summary_config_carries_the_tome_session_id(self) -> None:
        """A compaction summary is a turn of the same conversation.

        Its config must carry the tome id as session_id, so a realm that
        needs a per-conversation routing header (opencode) sends the same
        id for the summary as for the turns it summarizes.
        """
        realm = _realm()
        tome = MagicMock()
        tome.tome_id = "tome-123"
        runner = CompactionRunner(
            realm=realm,
            model=_model(),
            emit=Recorder(),
            tome=tome,
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        await runner.maybe_compact(_long_transcript())

        config = realm.complete.await_args.args[2]
        assert config.meta_data["session_id"] == "tome-123"


class TestCompactionFailure:
    @pytest.mark.asyncio
    async def test_realm_error_leaves_transcript_untouched(self) -> None:
        realm = MagicMock()
        realm.complete = AsyncMock(
            return_value=RealmResponse(
                model=_model(),
                error_message="rate limited",
                error_code="rate_limited",
            )
        )
        runner = CompactionRunner(
            realm=realm,
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        result = await runner.maybe_compact(_long_transcript())

        assert result is None

    @pytest.mark.asyncio
    async def test_realm_exception_does_not_propagate(self) -> None:
        realm = MagicMock()
        realm.complete = AsyncMock(side_effect=RuntimeError("boom"))
        runner = CompactionRunner(
            realm=realm,
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        result = await runner.maybe_compact(_long_transcript())

        assert result is None

    @pytest.mark.asyncio
    async def test_failure_reported_on_end_event(self) -> None:
        realm = MagicMock()
        realm.complete = AsyncMock(side_effect=RuntimeError("boom"))
        emit = Recorder()
        runner = CompactionRunner(
            realm=realm,
            model=_model(),
            emit=emit,
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )

        await runner.maybe_compact(_long_transcript())

        end = [e for e in emit.events if e.type == MvgeEventType.COMPACTION_END]
        assert end
        assert end[-1].data.get("error")


class TestTomePersistence:
    @pytest.mark.asyncio
    async def test_appends_compaction_entry(self) -> None:
        tome = MagicMock()
        tome.record_compaction = MagicMock()
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=tome,
        )

        await runner.maybe_compact(_long_transcript())

        tome.record_compaction.assert_called_once()
        payload = tome.record_compaction.call_args.kwargs
        assert "summary" in payload
        assert "mana_before" in payload
        assert "retained_tail" in payload

    @pytest.mark.asyncio
    async def test_compaction_reaches_the_loop(self) -> None:
        """The runner must actually swap the transcript during a real run."""
        from collections.abc import AsyncIterator

        from mvge.agent.harness import MvgeHarness
        from mvge.agent.harness._state import MvgeState

        spell = MagicMock(spec=MvgeSpell)
        spell.name = "x"
        spell.description = "spell"
        spell.parameters = {"type": "object", "properties": {}}
        spell.execute = AsyncMock(return_value={"ok": True})

        initial_invocations: list[MvgeInvocation] = []
        for index in range(4):
            initial_invocations.append(
                SummonerRequest(role="user", content=f"ask {index} " * 40)
            )
            initial_invocations.append(
                MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": f"reply {index} " * 40}],
                    stop_reason=StopReason.STOP,
                    mana_usage={"total": 600},
                )
            )

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model"},
            spells=[spell],
            invocations=initial_invocations,
        )
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
        )
        harness = MvgeHarness(
            state=state,
            compaction=runner,
            model=_model(),
            realm=_realm(),
        )

        seen: list[list[MvgeInvocation]] = []
        replies = [
            RealmResponse(
                model=_model(),
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {"id": "1", "name": "x", "arguments": {}},
                        }
                    ],
                    mana_usage={"total": 950},
                    stop_reason=StopReason.SPELL_USE,
                ),
            ),
            RealmResponse(
                model=_model(),
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "done"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        turn = -1

        def stream_fn(
            invocations: list[MvgeInvocation], signal: Any | None = None
        ) -> AsyncIterator[Any]:
            nonlocal turn
            turn += 1
            seen.append(list(invocations))
            response = replies[min(turn, len(replies) - 1)]

            async def gen() -> AsyncIterator[Any]:
                yield response

            return gen()

        await harness.run(stream_fn, {"id": "test-model"}, "none")

        # Turn two must be shorter: compaction replaced the history.
        assert len(seen) == 2
        assert len(seen[1]) < len(seen[0])
        assert "Summary of earlier conversation" in str(seen[1][0].content)

    @pytest.mark.asyncio
    async def test_no_tome_is_tolerated(self) -> None:
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=None,
        )

        result = await runner.maybe_compact(_long_transcript())

        assert result is not None

    @pytest.mark.asyncio
    async def test_force_compact_success(self) -> None:
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=None,
        )

        result = await runner.force_compact(_long_transcript())

        assert result is not None
        assert len(result) < len(_long_transcript())
        assert "Summary of earlier conversation" in str(result[0].content)

    @pytest.mark.asyncio
    async def test_force_compact_short_transcript(self) -> None:
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=None,
        )

        result = await runner.force_compact(
            [SummonerRequest(role="user", content="hi")]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_force_compact_summary_failure(self) -> None:
        broken_realm = MagicMock()
        broken_realm.complete = AsyncMock(
            return_value=RealmResponse(
                model=_model(),
                error_message="Realm failure",
            )
        )
        runner = CompactionRunner(
            realm=broken_realm,
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=None,
        )

        result = await runner.force_compact(_long_transcript())
        assert result is None

    @pytest.mark.asyncio
    async def test_compaction_preserves_active_skill_content(self) -> None:
        runner = CompactionRunner(
            realm=_realm(),
            model=_model(),
            emit=Recorder(),
            settings=CompactionSettings(reserve_mana=200, keep_recent_mana=200),
            tome=None,
        )

        transcript = [
            SummonerRequest(
                role="user",
                content=(
                    '<skill_content name="pdf-tool">\n'
                    "# Instructions\n"
                    "Extract text from PDFs.\n"
                    "</skill_content>"
                ),
            ),
            *_long_transcript(),
        ]

        result = await runner.force_compact(transcript)
        assert result is not None
        # Check that the summary is present
        assert "Summary of earlier conversation" in str(result[0].content)
        # Check that skill_content for pdf-tool was preserved across compaction
        preserved_skill_invs = [
            inv
            for inv in result
            if '<skill_content name="pdf-tool">' in str(inv.content)
        ]
        assert len(preserved_skill_invs) == 1
        assert "Extract text from PDFs." in str(preserved_skill_invs[0].content)
