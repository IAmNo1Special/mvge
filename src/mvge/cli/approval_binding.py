"""Host-side bind/unbind adapter for the Approval Rune presenter slot.

Canonical engine slot shape (``RuneRunner`` in ``mvge.runes``; this
adapter duck-types against it)::

    runner.set_approval_presenter(presenter) -> None
    runner.clear_approval_presenter() -> None

where ``presenter`` satisfies ``mvge.core.approval.ApprovalPresenter``
(``await presenter(request)``). Binding is host-privileged: only the host
(CLI/GUI) calls the setter. It is never exposed through ``RuneAPI`` —
otherwise any rune could install an auto-approver. With no presenter
bound, the rune's ``request_approval`` denies (fail closed).

Also carries the one-run ``--approval-mode`` helpers: the flag is never
persisted, and a warning prints before channeling begins whenever it is
explicitly set to a non-deny value. Environment variables never grant
approval and are not consulted here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from mvge.cli.approval_types import ApprovalMode, ApprovalPresenter

__all__ = [
    "NO_SLOT_WARNING",
    "approval_mode_notice",
    "approval_presenter_bound",
    "bind_approval_presenter",
    "resolve_approval_mode",
    "unbind_approval_presenter",
]

#: Warned when the engine runner has no approval slot yet. Execution stays
#: fail-closed: with no presenter bound the rune denies gated casts.
NO_SLOT_WARNING = (
    "Approval presenter could not bind: the engine runner has no approval "
    "slot. Gated spell casts will be denied."
)

_VALID_MODES: tuple[ApprovalMode, ...] = ("deny", "prompt", "allow-all")


def resolve_approval_mode(value: str | None) -> ApprovalMode:
    """Resolve the one-run ``--approval-mode`` flag value.

    ``None`` (flag not passed) means ``"prompt"``: the spec default is prompt
    on a TTY and deny without one, and the presenter itself denies when
    there is no terminal. Unknown values are a loud error, never a guess.
    """
    if value is None:
        return "prompt"
    if value in _VALID_MODES:
        return value
    raise ValueError(
        f"Unknown approval mode: {value!r} (expected one of: {', '.join(_VALID_MODES)})"
    )


def approval_mode_notice(mode: ApprovalMode) -> str | None:
    """Human-readable notice for an explicitly set approval mode.

    Printed before channeling begins. Always returns a line for explicit
    modes so the Summoner sees what the run will do.
    """
    if mode == "allow-all":
        return (
            "WARNING: --approval-mode=allow-all: every mutating spell cast "
            "will be approved automatically for this run only. "
            "This is never persisted."
        )
    if mode == "deny":
        return "Approval mode: deny. All gated spell casts will be denied for this run."
    return (
        "Approval mode: prompt. Each mutating spell cast asks for approval; "
        "without a terminal, casts are denied."
    )


def bind_approval_presenter(runner: Any, presenter: ApprovalPresenter) -> bool:
    """Bind ``presenter`` to the engine-owned runner's approval slot.

    Returns True when bound. Returns False (no raise) when the runner has
    no slot yet; the caller warns and execution stays fail-closed because
    the rune denies with no presenter bound.
    """
    setter = getattr(runner, "set_approval_presenter", None)
    if not callable(setter):
        return False
    setter(presenter)
    return True


def unbind_approval_presenter(
    runner: Any, presenter: ApprovalPresenter | None = None
) -> bool:
    """Unbind the presenter, resolving pending requests as denied.

    Closes ``presenter`` when given (its ``close()`` denies all further
    requests) before clearing the slot; the engine also fails every
    in-flight approval as denied when the slot changes. Returns True when
    a slot was present, False otherwise.
    """
    clearer = getattr(runner, "clear_approval_presenter", None)
    setter = getattr(runner, "set_approval_presenter", None)
    if not callable(clearer):
        if not callable(setter):
            return False
        # Legacy slot without clear: None-ing the setter is best-effort.
        if presenter is not None:
            close = getattr(presenter, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
        with suppress(Exception):
            setter(None)
        return True
    if presenter is not None:
        close = getattr(presenter, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
    clearer()
    return True


@contextmanager
def approval_presenter_bound(
    runner: Any, presenter: ApprovalPresenter
) -> Iterator[bool]:
    """Bind the presenter for the wrapped run; unbind on exit.

    Yields True when the presenter was bound, False when the runner has no
    approval slot (execution stays fail-closed). Unbinds even on error.
    """
    bound = bind_approval_presenter(runner, presenter)
    try:
        yield bound
    finally:
        if bound:
            unbind_approval_presenter(runner, presenter)
