from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from watchdog.events import FileModifiedEvent, FileMovedEvent

import mvge.runes.watcher as watcher_module
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.watcher import RuneWatcher, _RuneReloadHandler


class TestRuneWatcher:
    def test_create_watcher(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = RuneWatcher(Path(tmpdir), runner)
            # The watcher resolves the directory at construction (e.g.
            # Windows 8.3 short names), so compare against resolved.
            assert watcher._extensions_dir == Path(tmpdir).resolve()
            assert watcher._runner is runner

    @pytest.mark.asyncio
    async def test_reload_rune_with_shortcuts(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            manifest_data = (
                '{"name": "test_rune", "version": "1.0.0", '
                '"description": "Test", "hooks": [], '
                '"entry_point": "main.py", '
                '"shortcuts": [{"key": "ctrl+k", "description": "Clear"}]}'
            )
            (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")
            (rune_dir / "main.py").write_text(
                "def rune_factory(api):\n"
                "    api.register_shortcut('ctrl+r', 'Reload')\n"
            )

            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("test_rune")

            shortcuts = runner.get_shortcuts()
            assert len(shortcuts) >= 1
            keys = {s.key for s in shortcuts}
            assert "ctrl+k" in keys

    @pytest.mark.asyncio
    async def test_reload_rune_warns_on_missing_manifest(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("nonexistent")

    @pytest.mark.asyncio
    async def test_reload_rune_warns_on_no_factory(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            manifest_data = (
                '{"name": "test_rune", "version": "1.0.0", '
                '"description": "Test", "hooks": []}'
            )
            (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")

            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("test_rune")

    @pytest.mark.asyncio
    async def test_reload_rune_reinvokes_factory(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            manifest_data = (
                '{"name": "test_rune", "version": "1.0.0", '
                '"description": "Test", "hooks": [], '
                '"entry_point": "main.py"}'
            )
            (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")
            (rune_dir / "main.py").write_text(
                "def rune_factory(api):\n"
                "    api.register_spell(__import__('mvge.runes.types', "
                "fromlist=['SpellDefinition'])."
                "SpellDefinition('hot_reload_spell', 'Hot reloaded'))\n"
            )

            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("test_rune")

            spells = runner.get_all_registered_spells()
            assert len(spells) == 1
            assert spells[0].name == "hot_reload_spell"


class TestRuneReloadHandler:
    @pytest.mark.asyncio
    async def test_schedule_reload_debounce(self) -> None:
        """Test that _schedule_reload debounces multiple calls."""
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            # Schedule multiple reloads quickly
            handler._schedule_reload("rune1")
            handler._schedule_reload("rune2")
            handler._schedule_reload("rune1")  # duplicate

            # Wait for debounce - first task
            await asyncio.sleep(0.05)
            # Wait for second task
            await asyncio.sleep(0.05)

            # Should have been called for each unique name
            assert callback.call_count >= 1
            called_names = {call[0][0] for call in callback.call_args_list}
            assert "rune1" in called_names
            assert "rune2" in called_names

    def test_find_rune_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, AsyncMock())

            rune_dir = ext_dir / "my_rune"
            rune_dir.mkdir()

            # File inside rune directory
            test_file = rune_dir / "main.py"
            test_file.write_text("test", encoding="utf-8")

            found = handler._find_rune_dir(str(test_file))
            assert found == "my_rune"

    def test_find_rune_dir_outside_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, AsyncMock())

            # File outside extensions dir - use absolute path outside
            outside_file = Path(tmpdir).parent / "other.py"
            found = handler._find_rune_dir(str(outside_file))
            assert found is None

    def test_find_rune_dir_resolves_symlinked_event_path(self) -> None:
        """Event paths that differ textually but resolve identically match.

        On Windows, ``Path.resolve()`` may return 8.3 short names
        (``RUNNER~1``) while watchdog reports long names (``runneradmin``).
        The handler must normalize incoming paths before comparing.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            link_dir = Path(tmpdir) / "link"
            try:
                link_dir.symlink_to(real_dir, target_is_directory=True)
            except OSError:
                pytest.skip("Symlink creation requires elevated privileges on Windows")
            handler = _RuneReloadHandler(link_dir, AsyncMock())

            rune_dir = real_dir / "my_rune"
            rune_dir.mkdir()
            test_file = rune_dir / "main.py"
            test_file.write_text("test", encoding="utf-8")

            # Event arrives via the unresolved symlink path.
            found = handler._find_rune_dir(str(link_dir / "my_rune" / "main.py"))
            assert found == "my_rune"

    @pytest.mark.asyncio
    async def test_on_modified(self) -> None:
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            # Create mock event
            event = MagicMock()
            event.is_directory = False
            event.src_path = str(rune_dir / "main.py")

            handler.on_modified(event)

            await asyncio.sleep(0.05)
            callback.assert_called_once_with("test_rune")

    @pytest.mark.asyncio
    async def test_on_created(self) -> None:
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            event = MagicMock()
            event.is_directory = False
            event.src_path = str(rune_dir / "new_file.py")

            handler.on_created(event)

            await asyncio.sleep(0.05)
            callback.assert_called_once_with("test_rune")

    @pytest.mark.asyncio
    async def test_on_deleted(self) -> None:
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()

            event = MagicMock()
            event.is_directory = False
            event.src_path = str(rune_dir / "deleted.py")

            handler.on_deleted(event)

            await asyncio.sleep(0.05)
            callback.assert_called_once_with("test_rune")

    def test_ignore_directory_events(self) -> None:
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            event = MagicMock()
            event.is_directory = True
            event.src_path = str(ext_dir / "test_rune")

            handler.on_modified(event)
            handler.on_created(event)
            handler.on_deleted(event)

            # Should not call callback for directory events
            callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignore_cache_and_bytecode_events(self) -> None:
        callback = AsyncMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            handler = _RuneReloadHandler(ext_dir, callback, debounce_seconds=0.01)

            rune_dir = ext_dir / "test_rune"
            pycache_dir = rune_dir / "__pycache__"
            pycache_dir.mkdir(parents=True)

            for path in [
                str(pycache_dir / "module.cpython-314.pyc"),
                str(rune_dir / "file.pyc"),
                str(rune_dir / ".hidden_file"),
            ]:
                event = MagicMock()
                event.is_directory = False
                event.src_path = path

                handler.on_modified(event)
                handler.on_created(event)
                handler.on_deleted(event)

            await asyncio.sleep(0.05)
            callback.assert_not_called()


class TestRuneWatcherStartStop:
    @pytest.mark.asyncio
    async def test_event_on_foreign_thread_schedules_reload(self) -> None:
        """Watchdog dispatches events on its own thread; scheduling must work there."""
        callback_hit = threading.Event()

        async def callback(name: str) -> None:
            callback_hit.set()

        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "test_rune"
            rune_dir.mkdir()
            (rune_dir / "main.py").write_text("x", encoding="utf-8")

            runner = MagicMock()
            watcher = RuneWatcher(ext_dir, runner)
            watcher._reload_rune = callback  # type: ignore[method-assign]
            await watcher.start()
            assert watcher._handler is not None

            thread_error: list[BaseException] = []

            def observer_side() -> None:
                """Replicate watchdog: fire the handler off-loop."""
                try:
                    event = MagicMock()
                    event.is_directory = False
                    event.src_path = str(rune_dir / "main.py")
                    watcher._handler.on_modified(event)
                except BaseException as exc:  # noqa: BLE001
                    thread_error.append(exc)

            thread = threading.Thread(target=observer_side)
            thread.start()
            thread.join(timeout=5)

            assert thread_error == [], (
                f"handler raised on observer thread: {thread_error!r}"
            )
            # The scheduled coroutine runs on OUR loop; yield so it can.
            for _ in range(100):
                if callback_hit.is_set():
                    break
                await asyncio.sleep(0.01)
            assert callback_hit.is_set(), "callback never ran"
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_start_creates_observer(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)

            watcher = RuneWatcher(ext_dir, runner)
            await watcher.start()

            assert watcher._observer is not None
            assert watcher._handler is not None
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)

            watcher = RuneWatcher(ext_dir, runner)
            await watcher.start()
            first_observer = watcher._observer
            await watcher.start()  # Should not create new observer
            assert watcher._observer is first_observer
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)

            watcher = RuneWatcher(ext_dir, runner)
            await watcher.start()
            await watcher.stop()
            assert watcher._observer is None
            assert watcher._handler is None

    @pytest.mark.asyncio
    async def test_reload_rune_async_factory(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "async_rune"
            rune_dir.mkdir()

            manifest_data = (
                '{"name": "async_rune", "version": "1.0.0", '
                '"description": "Async Test", "hooks": [], '
                '"entry_point": "main.py"}'
            )
            (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")
            (rune_dir / "main.py").write_text(
                "async def rune_factory(api):\n"
                "    api.register_shortcut('ctrl+a', 'Async Shortcut')\n"
            )

            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("async_rune")

            shortcuts = runner.get_shortcuts()
            assert any(s.key == "ctrl+a" for s in shortcuts)

    @pytest.mark.asyncio
    async def test_reload_rune_corrupt_manifest(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            rune_dir = ext_dir / "bad_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text("{invalid json", encoding="utf-8")

            watcher = RuneWatcher(ext_dir, runner)
            await watcher._reload_rune("bad_rune")

    @pytest.mark.asyncio
    async def test_schedule_reload_handles_callback_exception(self) -> None:
        async def failing_callback(name: str) -> None:
            raise RuntimeError("Reload blew up")

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = _RuneReloadHandler(
                Path(tmpdir),
                failing_callback,
                debounce_seconds=0.01,
                loop=asyncio.get_running_loop(),
            )
            handler._schedule_reload("test_rune")
            await asyncio.sleep(0.05)

    def test_schedule_reload_closed_loop(self) -> None:
        loop = asyncio.new_event_loop()
        loop.close()

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = _RuneReloadHandler(
                Path(tmpdir), AsyncMock(), debounce_seconds=0.01, loop=loop
            )
            handler._schedule_reload("test_rune")


class TestWatcherReloadCallbackMode:
    """The watcher as a dumb trigger: fire-once coalescing into Mvge.reload()."""

    @pytest.mark.asyncio
    async def test_fire_once_coalesces_burst_into_single_call(self) -> None:
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = _RuneReloadHandler(
                Path(tmpdir),
                cb,
                debounce_seconds=0.02,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            event = FileModifiedEvent(str(Path(tmpdir) / "rune-a" / "rune.py"))
            handler.on_modified(event)
            handler.on_created(event)
            handler.on_modified(event)
            await asyncio.sleep(0.2)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_fire_once_ignores_dotfiles(self) -> None:
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = _RuneReloadHandler(
                Path(tmpdir),
                cb,
                debounce_seconds=0.02,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            event = FileModifiedEvent(str(Path(tmpdir) / "rune-a" / ".#rune.py"))
            handler.on_modified(event)
            await asyncio.sleep(0.2)
        assert calls == []

    @pytest.mark.asyncio
    async def test_fire_once_fires_under_dotted_watch_root(self) -> None:
        """A dot directory *above* the watched root must not mute events.

        The agent config dir lives under ``~/.agents``; ignoring dot path
        parts of the watched root's own ancestors would silently disable
        config-dir reload triggers.
        """
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            dotted_root = Path(tmpdir) / ".agents" / "agents" / "demo"
            dotted_root.mkdir(parents=True)
            handler = _RuneReloadHandler(
                dotted_root,
                cb,
                debounce_seconds=0.02,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            event = FileModifiedEvent(str(dotted_root / "SYSTEM.md"))
            handler.on_modified(event)
            await asyncio.sleep(0.2)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_fire_once_still_ignores_dotfiles_inside_tree(self) -> None:
        """Dotfiles *inside* the watched tree stay ignored (no regression)."""
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            dotted_root = Path(tmpdir) / ".agents" / "agents" / "demo"
            dotted_root.mkdir(parents=True)
            handler = _RuneReloadHandler(
                dotted_root,
                cb,
                debounce_seconds=0.02,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            event = FileModifiedEvent(str(dotted_root / ".#SYSTEM.md"))
            handler.on_modified(event)
            await asyncio.sleep(0.2)
        assert calls == []

    @pytest.mark.asyncio
    async def test_fire_once_handles_callback_exception(self) -> None:
        async def failing() -> None:
            raise RuntimeError("reload blew up")

        with tempfile.TemporaryDirectory() as tmpdir:
            handler = _RuneReloadHandler(
                Path(tmpdir),
                failing,
                debounce_seconds=0.01,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            handler._schedule_reload("")
            await asyncio.sleep(0.05)


class TestWatcherOnMoved:
    """Atomic renames arrive as moved events; map on the destination path."""

    @pytest.mark.asyncio
    async def test_on_moved_uses_destination_path(self) -> None:
        seen: list[str] = []

        async def cb(name: str) -> None:
            seen.append(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            ext = Path(tmpdir)
            handler = _RuneReloadHandler(
                ext, cb, debounce_seconds=0.02, loop=asyncio.get_running_loop()
            )
            handler.on_moved(
                FileMovedEvent(
                    str(ext / "rune-a" / "rune.py"), str(ext / "rune-b" / "rune.py")
                )
            )
            await asyncio.sleep(0.2)
        assert seen == ["rune-b"]

    @pytest.mark.asyncio
    async def test_on_moved_ignores_dotfile_destination(self) -> None:
        seen: list[str] = []

        async def cb(name: str) -> None:
            seen.append(name)

        with tempfile.TemporaryDirectory() as tmpdir:
            ext = Path(tmpdir)
            handler = _RuneReloadHandler(
                ext, cb, debounce_seconds=0.02, loop=asyncio.get_running_loop()
            )
            handler.on_moved(
                FileMovedEvent(
                    str(ext / "rune-a" / "rune.py"), str(ext / "rune-a" / ".#rune.py")
                )
            )
            await asyncio.sleep(0.2)
        assert seen == []

    @pytest.mark.asyncio
    async def test_on_moved_in_fire_once_mode_triggers_callback(self) -> None:
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        with tempfile.TemporaryDirectory() as tmpdir:
            ext = Path(tmpdir)
            handler = _RuneReloadHandler(
                ext,
                cb,
                debounce_seconds=0.02,
                loop=asyncio.get_running_loop(),
                fire_once=True,
            )
            handler.on_moved(
                FileMovedEvent(
                    str(ext / "rune-a" / "rune.py"), str(ext / "rune-a" / "rune2.py")
                )
            )
            await asyncio.sleep(0.2)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_watcher_forwards_real_file_event_to_reload_callback(self) -> None:
        calls: list[bool] = []

        async def cb() -> None:
            calls.append(True)

        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            ext_dir = Path(tmpdir)
            watcher = RuneWatcher(ext_dir, runner, reload_callback=cb)
            await watcher.start()
            try:
                (ext_dir / "probe.txt").write_text("x", encoding="utf-8")
                for _ in range(100):
                    if calls:
                        break
                    await asyncio.sleep(0.05)
            finally:
                await watcher.stop()
        assert len(calls) == 1


class _WedgedObserver:
    """Observer stub whose thread never exits on its own.

    ``join()`` without a timeout hangs forever (the pre-fix failure mode);
    with a timeout it sleeps past it, so the watchdog can declare the
    thread wedged. ``is_alive()`` stays True throughout.
    """

    def stop(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        if timeout is None:
            threading.Event().wait()
        else:
            time.sleep(timeout + 0.2)

    def is_alive(self) -> bool:
        return True


class TestRuneWatcherStopTimeout:
    @pytest.mark.asyncio
    async def test_stop_times_out_instead_of_hanging_on_wedged_observer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wedged observer thread must bound stop(): TimeoutError, loudly,
        instead of hanging reload() forever."""
        monkeypatch.setattr(
            watcher_module, "_OBSERVER_STOP_TIMEOUT_SECONDS", 0.05, raising=False
        )
        runner = RuneRunner()
        watcher = RuneWatcher(tmp_path, runner)
        watcher._observer = _WedgedObserver()  # type: ignore[assignment]
        with pytest.raises(TimeoutError, match="did not stop"):
            await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_keeps_observer_for_retry_after_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a join timeout the watcher stays armed so the engine's
        retry loop can attempt the stop again."""
        monkeypatch.setattr(
            watcher_module, "_OBSERVER_STOP_TIMEOUT_SECONDS", 0.05, raising=False
        )
        runner = RuneRunner()
        watcher = RuneWatcher(tmp_path, runner)
        watcher._observer = _WedgedObserver()  # type: ignore[assignment]
        with pytest.raises(TimeoutError):
            await watcher.stop()
        assert watcher._observer is not None


class TestRuneWatcherWatchPath:
    def test_relative_dir_resolves_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A CWD-relative watch target keeps its anchor but is exposed and
        logged as an absolute path — no deceptive relative strings."""
        monkeypatch.chdir(tmp_path)
        runner = RuneRunner()
        watcher = RuneWatcher(Path("rel-ext"), runner)
        assert watcher.watch_path.is_absolute()
        assert watcher.watch_path == (tmp_path / "rel-ext").resolve()

    def test_absolute_dir_is_kept(self, tmp_path: Path) -> None:
        runner = RuneRunner()
        watcher = RuneWatcher(tmp_path, runner)
        assert watcher.watch_path == tmp_path.resolve()


@pytest.mark.asyncio
async def test_audit_appends_do_not_trigger_reload(tmp_path: Path) -> None:
    """The engine audit log lives inside the watched extensions root; a real
    RuneAuditLog.append_event() under that root must not fire the reload
    trigger. Otherwise every reload's own audit record schedules another
    reload — the audit storm (211 unsolicited reload_ok records in ~52s)."""
    from mvge.runes.rune_audit import RuneAuditLog

    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    audit = RuneAuditLog(ext_dir)

    calls = 0

    async def _callback() -> None:
        nonlocal calls
        calls += 1

    watcher = RuneWatcher(ext_dir, RuneRunner(), reload_callback=_callback)
    await watcher.start()
    try:
        audit.append_event({"op": "reload", "outcome": "ok"})
        # Wait well past the debounce window for any scheduled callback.
        await asyncio.sleep(1.2)
        assert calls == 0
    finally:
        await watcher.stop()


def test_is_ignored_covers_audit_files(tmp_path: Path) -> None:
    """Root-level audit.jsonl and rotated audit-*.jsonl are ignored; a
    nested audit.jsonl inside a rune dir still triggers that rune."""
    handler = _RuneReloadHandler(tmp_path, lambda: None, fire_once=True)
    assert handler._is_ignored(str(tmp_path / "audit.jsonl"))
    assert handler._is_ignored(str(tmp_path / "audit-20260922-120000.jsonl"))
    assert handler._is_ignored(str(tmp_path / "audit-20260922-120000-1.jsonl"))
    assert not handler._is_ignored(str(tmp_path / "some_rune" / "audit.jsonl"))
    assert not handler._is_ignored(str(tmp_path / "some_rune" / "rune.py"))


@pytest.mark.asyncio
async def test_stop_cancels_pending_debounce(tmp_path: Path) -> None:
    """A file event debounced but not yet fired when stop() runs must not
    reach the callback afterwards: otherwise a pre-teardown event fires
    Mvge.reload() on the retired instance after close()."""
    calls = 0

    async def _callback() -> None:
        nonlocal calls
        calls += 1

    watcher = RuneWatcher(tmp_path, RuneRunner(), reload_callback=_callback)
    await watcher.start()
    try:
        assert watcher._handler is not None
        # A file event arrives; the reload is debounced (0.5s), not fired.
        watcher._handler.on_modified(
            FileModifiedEvent(str(tmp_path / "some_rune" / "rune.py"))
        )
        await asyncio.sleep(0.1)
        assert calls == 0
    finally:
        await watcher.stop()
    # Past the debounce window: the cancelled burst must never fire.
    await asyncio.sleep(0.7)
    assert calls == 0
