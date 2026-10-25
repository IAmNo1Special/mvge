from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_runner import RuneRunner

logger = logging.getLogger(__name__)

_OBSERVER_STOP_TIMEOUT_SECONDS = 5.0
"""Bound for the watchdog observer thread join in :meth:`RuneWatcher.stop`.

A stopped observer normally joins in milliseconds; a thread that is still
alive after this long is wedged, and ``stop()`` raises ``TimeoutError``
instead of hanging the caller (e.g. ``Mvge.reload()``) forever. The
watcher stays armed after the timeout so the engine's retry loop can
attempt the stop again.
"""


class _RuneReloadHandler(FileSystemEventHandler):
    """Debounces file-system events into reload callbacks.

    Two modes:

    - Per-rune mode (default): the callback receives each changed rune's
      name after debouncing, and the caller reloads that rune.
    - Trigger mode (``fire_once=True``): the callback takes no arguments
      and is invoked exactly once per debounced burst. The watcher is a
      dumb trigger here; the callback (``Mvge.reload()``) owns all reload
      semantics, including rune refresh.
    """

    def __init__(
        self,
        extensions_dir: Path,
        reload_callback: Any,
        debounce_seconds: float = 0.5,
        loop: asyncio.AbstractEventLoop | None = None,
        fire_once: bool = False,
    ) -> None:
        super().__init__()
        # Resolve to match RuneWatcher and the normalized event paths:
        # on Windows, resolve() may return 8.3 short names, and all
        # comparisons must use the same form.
        self._extensions_dir = Path(extensions_dir).expanduser().resolve()
        self._callback = reload_callback
        self._debounce_seconds = debounce_seconds
        self._pending: set[str] = set()
        self._debounce_future: Future[Any] | None = None
        # Watchdog dispatches events on its own thread; scheduling must go
        # through run_coroutine_threadsafe onto the loop the watcher was
        # started from. Without a loop there is nothing to schedule on.
        self._loop = loop
        self._fire_once = fire_once

    def _schedule_reload(self, rune_name: str) -> None:
        self._pending.add(rune_name)

        async def _debounced() -> None:
            await asyncio.sleep(self._debounce_seconds)
            pending = self._pending.copy()
            self._pending.clear()
            if self._fire_once:
                # Trigger mode: one call per burst, no rune-name dispatch.
                try:
                    result = self._callback()
                    if isinstance(result, Awaitable):
                        await result
                except Exception:
                    logger.exception("Reload callback failed")
                return
            for name in pending:
                try:
                    await self._callback(name)
                except Exception:
                    logger.exception("Failed to reload rune %s", name)

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug(
                    "No event loop available; dropping reload of %s", rune_name
                )
                return
        if loop.is_closed():
            return
        if self._debounce_future is not None and not self._debounce_future.done():
            self._debounce_future.cancel()
        try:
            self._debounce_future = asyncio.run_coroutine_threadsafe(_debounced(), loop)
        except RuntimeError:
            logger.debug("Event loop closed while scheduling reload of %s", rune_name)

    def cancel_pending(self) -> None:
        """Cancel a debounced reload that has not fired yet.

        Called on teardown so a file event that arrived before the stop
        cannot fire the callback afterwards — e.g. ``Mvge.reload()`` on a
        retired instance after ``Mvge.close()``.
        """
        future = self._debounce_future
        self._debounce_future = None
        self._pending.clear()
        if future is not None and not future.done():
            future.cancel()

    def _find_rune_dir(self, path: str) -> str | None:
        # Resolve the incoming path: the extensions dir is stored resolved,
        # but watchdog may report a textually different form for the same
        # location (e.g. Windows 8.3 short names like RUNNER~1 vs the long
        # name). Without this, relative_to fails and the event is dropped.
        src_path = Path(path).resolve()
        try:
            relative = src_path.relative_to(self._extensions_dir)
        except ValueError:
            return None
        parts = relative.parts
        if parts:
            return parts[0]
        return None

    def _is_ignored(self, path: str) -> bool:
        # Resolve for the same reason as _find_rune_dir: the watched root
        # is stored resolved, incoming event paths may not be.
        src = Path(path).resolve()
        # Judge only the path *inside* the watched tree: the watched root
        # itself may legitimately live under a dot directory (e.g. the
        # agent config dir under ``~/.agents``).
        try:
            parts = src.relative_to(self._extensions_dir).parts
        except ValueError:
            parts = src.parts
        # The engine's audit log lives at the root of the watched
        # extensions dir; its own appends and rotations must not fire
        # reloads, or every reload's audit record schedules another reload
        # (audit storm). Root-level only: a nested audit.jsonl belongs to
        # a rune and still triggers that rune.
        if len(parts) == 1:
            name = parts[0]
            if name == "audit.jsonl" or (
                name.startswith("audit-") and name.endswith(".jsonl")
            ):
                return True
        for part in parts:
            if part == "__pycache__" or (part.startswith(".") and part != "."):
                return True
        return src.suffix in (".pyc", ".pyo", ".pyd", ".swp", ".tmp")

    def _handle_file_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or self._is_ignored(str(event.src_path)):
            return
        if self._fire_once:
            self._schedule_reload("")
            return
        rune_name = self._find_rune_dir(str(event.src_path))
        if rune_name:
            self._schedule_reload(rune_name)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Atomic renames (os.replace) arrive as moved events: map on the
        # destination path, which is where the new content lives.
        dest = getattr(event, "dest_path", "") or ""
        if event.is_directory or not dest or self._is_ignored(str(dest)):
            return
        if self._fire_once:
            self._schedule_reload("")
            return
        rune_name = self._find_rune_dir(str(dest))
        if rune_name:
            self._schedule_reload(rune_name)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle_file_event(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle_file_event(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle_file_event(event)


class RuneWatcher:
    """Watches a directory tree for changes.

    Per-rune mode (default) reloads each changed rune through the runner.
    Pass ``reload_callback`` for trigger mode: the watcher fires the
    callback once per debounced burst of modified/created/deleted/moved
    events, and the callback — ``Mvge.reload()`` — owns all reload
    semantics. The watcher never reloads anything itself in trigger mode.
    """

    def __init__(
        self,
        extensions_dir: Path,
        runner: RuneRunner,
        reload_callback: Callable[[], Any] | None = None,
    ) -> None:
        # Resolve to absolute at construction: every later use — the
        # observer schedule, the event handler's base, the logs — sees
        # the real directory instead of a deceptive relative string.
        # This changes no anchoring semantics.
        self._extensions_dir = Path(extensions_dir).expanduser().resolve()
        self._runner = runner
        self._reload_callback = reload_callback
        self._observer: Any = None
        self._handler: _RuneReloadHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def watch_path(self) -> Path:
        """The absolute directory this watcher watches."""
        return self._extensions_dir

    async def _reload_rune(self, rune_name: str) -> None:
        rune_dir = self._extensions_dir / rune_name
        if not rune_dir.is_dir():
            logger.warning("Rune directory vanished: %s", rune_dir)
            return

        manifest = load_manifest(rune_dir)
        if manifest is None:
            logger.warning("Manifest invalid or missing after reload: %s", rune_dir)
            return

        factory = load_factory_from_manifest(manifest, rune_dir)
        if factory is None:
            logger.warning("Factory not found after reload: %s", rune_name)
            return

        for sc in manifest.shortcuts:
            self._runner.register_shortcut(sc, override=True)

        logger.info("Reloading rune: %s (%s)", rune_name, manifest.version)
        self._runner.clear_rune(rune_name)
        api = self._runner.create_api(rune_name=rune_name, override=True)
        result = factory(api)
        if isinstance(result, Awaitable):
            await result

    async def _fire_reload_callback(self) -> None:
        """Invoke the engine reload callback (trigger mode)."""
        if self._reload_callback is None:
            return
        result = self._reload_callback()
        if isinstance(result, Awaitable):
            await result

    async def start(self) -> None:
        if self._observer is not None:
            return

        self._loop = asyncio.get_running_loop()
        if self._reload_callback is not None:
            callback: Any = self._fire_reload_callback
            fire_once = True
        else:
            callback = self._reload_rune
            fire_once = False
        self._handler = _RuneReloadHandler(
            self._extensions_dir, callback, loop=self._loop, fire_once=fire_once
        )
        self._observer = Observer()
        self._observer.schedule(
            self._handler, str(self._extensions_dir), recursive=True
        )
        self._observer.start()
        logger.info("Rune watcher started for %s", self._extensions_dir)

    async def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=_OBSERVER_STOP_TIMEOUT_SECONDS)
            if self._observer.is_alive():
                # Wedged thread: fail loudly and stay armed so a retry can
                # attempt the stop again. Never hang the caller forever.
                # The pending debounce is left alone: the watcher is still
                # live, so the burst still belongs to it.
                raise TimeoutError(
                    f"Rune watcher for {self._extensions_dir} did not stop "
                    f"within {_OBSERVER_STOP_TIMEOUT_SECONDS}s; "
                    "observer thread still alive"
                )
            self._observer = None
            if self._handler is not None:
                # The observer is down: a debounced event must not fire
                # the retired callback after this returns.
                self._handler.cancel_pending()
                self._handler = None
            logger.info("Rune watcher stopped")
