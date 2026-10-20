"""Unit tests for the ``python -m mvgeos_gui`` entry point and runtime helpers."""

from __future__ import annotations

import runpy
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mvgeos_gui
from mvgeos_gui.main import _get_storage_secret, _shutdown_thread_excepthook


def test_main_module_calls_main() -> None:
    """Running the package as __main__ delegates to main()."""
    main_py = Path(mvgeos_gui.__file__).parent / "__main__.py"
    with patch("mvgeos_gui.main.main") as mock_main:
        runpy.run_path(str(main_py), run_name="__main__")
    mock_main.assert_called_once_with()


def test_get_storage_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """STORAGE_SECRET environment variable takes precedence."""
    monkeypatch.setenv("STORAGE_SECRET", "custom-secret-key")
    assert _get_storage_secret() == "custom-secret-key"


def test_get_storage_secret_from_persisted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing storage secret file is read when env var is absent."""
    monkeypatch.delenv("STORAGE_SECRET", raising=False)
    secret_file = tmp_path / ".storage_secret"
    secret_file.write_text("persisted-token\n", encoding="utf-8")
    with patch("mvgeos_gui.main._STORAGE_SECRET_FILE", secret_file):
        assert _get_storage_secret() == "persisted-token"


def test_get_storage_secret_generates_and_persists_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing secret file generates a new token and persists it."""
    monkeypatch.delenv("STORAGE_SECRET", raising=False)
    secret_file = tmp_path / "sub" / ".storage_secret"
    with patch("mvgeos_gui.main._STORAGE_SECRET_FILE", secret_file):
        token = _get_storage_secret()
        assert len(token) > 20
        assert secret_file.read_text(encoding="utf-8") == token


def test_get_storage_secret_handles_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handles read/write OSError when persisting or reading secret."""
    monkeypatch.delenv("STORAGE_SECRET", raising=False)
    secret_file = tmp_path / ".storage_secret"
    secret_file.touch()
    with (
        patch("mvgeos_gui.main._STORAGE_SECRET_FILE", secret_file),
        patch.object(Path, "read_text", side_effect=OSError("denied")),
        patch.object(Path, "write_text", side_effect=OSError("write denied")),
    ):
        token = _get_storage_secret()
        assert len(token) > 20


def test_shutdown_thread_excepthook_suppresses_system_exit() -> None:
    """KeyboardInterrupt and SystemExit are quietly suppressed."""
    with patch("threading.__excepthook__") as mock_hook:
        args = threading.ExceptHookArgs(
            (SystemExit, SystemExit(0), None, threading.current_thread())
        )
        _shutdown_thread_excepthook(args)
        mock_hook.assert_not_called()

        args_kb = threading.ExceptHookArgs(
            (KeyboardInterrupt, KeyboardInterrupt(), None, threading.current_thread())
        )
        _shutdown_thread_excepthook(args_kb)
        mock_hook.assert_not_called()


def test_shutdown_thread_excepthook_suppresses_check_shutdown_thread() -> None:
    """check_shutdown thread exceptions are quietly suppressed."""
    mock_thread = MagicMock()
    mock_thread.name = "check_shutdown_worker"
    args = threading.ExceptHookArgs((ValueError, ValueError("bang"), None, mock_thread))
    with patch("threading.__excepthook__") as mock_hook:
        _shutdown_thread_excepthook(args)
        mock_hook.assert_not_called()


def test_shutdown_thread_excepthook_logs_and_delegates() -> None:
    """Non-suppressed exceptions log error and delegate to threading.__excepthook__."""
    mock_thread = MagicMock()
    mock_thread.name = "worker_1"
    err = RuntimeError("something broke")
    args = threading.ExceptHookArgs((RuntimeError, err, None, mock_thread))

    with (
        patch("threading.__excepthook__") as mock_hook,
        patch("logging.Logger.error") as mock_log,
    ):
        _shutdown_thread_excepthook(args)
        assert mock_log.called
        mock_hook.assert_called_once_with(args)


def test_shutdown_thread_excepthook_logs_when_exc_value_none() -> None:
    """Handles thread exceptions when exc_value is None."""
    mock_thread = MagicMock()
    mock_thread.name = "worker_2"
    args = threading.ExceptHookArgs((RuntimeError, None, None, mock_thread))

    with (
        patch("threading.__excepthook__") as mock_hook,
        patch("logging.Logger.error") as mock_log,
    ):
        _shutdown_thread_excepthook(args)
        assert mock_log.called
        mock_hook.assert_called_once_with(args)
