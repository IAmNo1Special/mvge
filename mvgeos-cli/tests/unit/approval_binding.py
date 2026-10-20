"""Unit tests for the approval presenter bind/unbind adapter.

The adapter targets the canonical engine slot shape (``RuneRunner``)::

    runner.set_approval_presenter(presenter) -> None   (host-privileged)
    runner.clear_approval_presenter() -> None
"""

from __future__ import annotations

import io

import pytest

from mvgeos_cli.approval_binding import (
    approval_mode_notice,
    approval_presenter_bound,
    bind_approval_presenter,
    resolve_approval_mode,
    unbind_approval_presenter,
)
from mvgeos_cli.approval_presenter import CliApprovalPresenter


class StubRunner:
    """Carries the canonical engine presenter slot shape."""

    def __init__(self) -> None:
        self.presenter: object | None = None
        self.calls: list[str] = []

    def set_approval_presenter(self, presenter: object) -> None:
        self.calls.append("set")
        self.presenter = presenter

    def clear_approval_presenter(self) -> None:
        self.calls.append("clear")
        self.presenter = None


def _presenter() -> CliApprovalPresenter:
    return CliApprovalPresenter(
        mode="prompt", stdin=io.StringIO(), stdout=io.StringIO()
    )


def test_bind_calls_slot_setter_with_presenter() -> None:
    runner = StubRunner()
    presenter = _presenter()
    assert bind_approval_presenter(runner, presenter) is True
    assert runner.presenter is presenter
    assert runner.calls == ["set"]


def test_bind_without_slot_returns_false() -> None:
    assert bind_approval_presenter(object(), _presenter()) is False


def test_unbind_closes_presenter_and_clears_slot() -> None:
    runner = StubRunner()
    presenter = _presenter()
    bind_approval_presenter(runner, presenter)
    assert unbind_approval_presenter(runner, presenter) is True
    assert presenter.closed is True
    assert runner.presenter is None
    assert runner.calls == ["set", "clear"]


def test_unbind_without_presenter_still_clears_slot() -> None:
    runner = StubRunner()
    presenter = _presenter()
    bind_approval_presenter(runner, presenter)
    assert unbind_approval_presenter(runner) is True
    assert runner.presenter is None


def test_unbind_without_slot_returns_false() -> None:
    assert unbind_approval_presenter(object()) is False


def test_unbind_legacy_runner_without_clear() -> None:
    class LegacyRunner:
        def __init__(self) -> None:
            self.presenter: object = None

        def set_approval_presenter(self, p: object) -> None:
            self.presenter = p

    runner = LegacyRunner()
    presenter = _presenter()
    runner.set_approval_presenter(presenter)
    assert unbind_approval_presenter(runner, presenter) is True
    assert presenter.closed is True
    assert runner.presenter is None


def test_context_manager_binds_then_unbinds() -> None:
    runner = StubRunner()
    presenter = _presenter()
    with approval_presenter_bound(runner, presenter) as bound:
        assert bound is True
        assert runner.presenter is presenter
    assert runner.presenter is None
    assert presenter.closed is True


def test_context_manager_unbinds_on_exception() -> None:
    runner = StubRunner()
    presenter = _presenter()
    with (
        pytest.raises(RuntimeError, match="boom"),
        approval_presenter_bound(runner, presenter),
    ):
        raise RuntimeError("boom")
    assert runner.presenter is None
    assert presenter.closed is True


def test_context_manager_without_slot_yields_false() -> None:
    with approval_presenter_bound(object(), _presenter()) as bound:
        assert bound is False


def test_resolve_approval_mode_defaults_to_prompt() -> None:
    assert resolve_approval_mode(None) == "prompt"


def test_resolve_approval_mode_accepts_all_values() -> None:
    assert resolve_approval_mode("deny") == "deny"
    assert resolve_approval_mode("prompt") == "prompt"
    assert resolve_approval_mode("allow-all") == "allow-all"


def test_resolve_approval_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown approval mode"):
        resolve_approval_mode("sometimes")


def test_allow_all_notice_warns_and_states_not_persisted() -> None:
    notice = approval_mode_notice("allow-all")
    assert notice is not None
    assert "WARNING" in notice
    assert "never persisted" in notice


def test_deny_notice_states_denial() -> None:
    notice = approval_mode_notice("deny")
    assert notice is not None
    assert "denied" in notice


def test_prompt_notice_mentions_non_tty_denial() -> None:
    notice = approval_mode_notice("prompt")
    assert notice is not None
    assert "terminal" in notice
