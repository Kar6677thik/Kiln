"""Who re-enabled the garbage collector? A detector in three layers (plus gdb by hand).

WHY THIS MODULE EXISTS
    Instagram disabled CPython's GC to keep copy-on-write sharing, and then found memory
    being copied anyway: a library (msgpack) quietly called gc.enable() again. They found it
    with a debugger. The GC switch is *global interpreter state*: any code in the process,
    Python or C, can flip it, and nothing announces it (docs/concepts/global-interpreter-state.md).
    No single hook sees every way it can happen, so this module combines three:

    A  replace gc.enable with a recording wrapper.
       Catches Python (and Cython) callers, with the exact stack. Misses C calling PyGC_Enable().
    B  a sys.meta_path finder comparing gc.isenabled() before/after each import.
       Catches any flip during an import, even from C. Misses flips after import.
    C  poll gc.isenabled(), plus a gc.callbacks sentinel for automatic collections.
       Catches runtime flips from anywhere. Misses *who* did it (it only knows when).
    D  gdb with `break PyGC_Enable` (by hand): catches everything.

HOW IT FITS
    master/arbiter.py and worker/main.py ──GcWatch(...).install()── when labs.gc_watch = true
    GcWatch ──on_event(GcEvent)──▶ the caller logs `gc_reenabled` (WARNING)
    worker heartbeat thread / master tick ──watch.poll()── layer C(1)

    CONSTRAINT (K7): stdlib only, Python 3.13 syntax, and loadable *by file path* with no other
    Kiln module imported: K7 copies it into standalone scripts.

READ FIRST
    docs/concepts/global-interpreter-state.md, docs/python/monkeypatching.md,
    docs/python/import-hooks-meta-path.md, docs/python/traceback-module.md,
    docs/python/gc-module.md

MILESTONE
    Introduced in K3 (layers A–C). K7 adds the GC pause histogram.
"""

from __future__ import annotations

import gc
import importlib.abc
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any, Literal

Layer = Literal["shim", "import_flip", "poll", "collection_while_disabled"]
STACK_FRAMES = 8  # how many frames of a stack an event keeps


@dataclass(frozen=True, slots=True)
class GcEvent:
    """One sighting of the GC being switched on (or collecting) when we didn't expect it.

    Why:
        Each layer knows different things (an exact stack, a module name, only a time), so
        one record type with optional fields lets the caller log them all the same way.
    """

    layer: Layer
    t_mono: float
    module: str | None  # layer B: the module whose import flipped the GC
    stack: tuple[str, ...]  # layer A: the caller (exact); layer C(2): the allocation site
    job_id: str | None  # layer C: filled in by the worker (which job was running)
    note: str


def _stack(skip: int) -> tuple[str, ...]:
    """The current call stack as short text lines, innermost last.

    Why:
        A stack turned into text can be logged and compared; frames inside this module are
        noise, so `skip` drops them.

    Args:
        skip: how many innermost frames to drop.

    Returns:
        Up to STACK_FRAMES lines like "pkg/mod.py:12 in func".
    """
    frames = traceback.extract_stack()[: -skip or None]
    return tuple(f"{f.filename}:{f.lineno} in {f.name}" for f in frames[-STACK_FRAMES:])


class _WatchedLoader:
    """A loader that runs the real loader's exec_module and reports a GC flip during it.

    Why:
        This is the "delegate-and-wrap" pattern (docs/python/import-hooks-meta-path.md).
        We can't change how a C extension calls PyGC_Enable(), but every module, C or Python,
        is executed by some loader's exec_module(). Wrapping that call lets us compare
        gc.isenabled() before and after. Every other attribute is forwarded unchanged, so
        the import system sees the original loader's behaviour.
    """

    def __init__(self, inner: Any, watch: GcWatch, name: str) -> None:  # noqa: ANN401  # WHY: any loader object
        """Remember the real loader.

        Args:
            inner: the original loader.
            watch: the GcWatch to report to.
            name: the module being loaded.
        """
        self._inner = inner
        self._watch = watch
        self._name = name

    def __getattr__(self, attribute: str) -> Any:  # noqa: ANN401  # WHY: forwards anything
        """Forward every attribute we don't define to the real loader.

        Args:
            attribute: the attribute name.

        Returns:
            The real loader's attribute.
        """
        return getattr(self._inner, attribute)

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Forward module creation (extension modules create their own module object).

        Args:
            spec: the module spec.

        Returns:
            What the real loader returns.
        """
        result: ModuleType | None = self._inner.create_module(spec)
        return result

    def exec_module(self, module: ModuleType) -> None:
        """Run the real exec_module, recording the GC state around it.

        Why:
            Imports nest (module a imports b while a is executing). A flip is blamed on the
            *innermost* import whose before/after shows it, so each frame on our stack
            remembers whether a child already took the blame.

        Args:
            module: the module being executed.
        """
        frame = {"before": gc.isenabled(), "child_flipped": False}
        self._watch._import_stack.append(frame)
        try:
            self._inner.exec_module(module)
        finally:
            self._watch._import_stack.pop()
            after = gc.isenabled()
            flipped = after and not frame["before"]
            if flipped and not frame["child_flipped"]:
                self._watch._emit("import_flip", module=self._name, note="GC enabled during import")
            if flipped and self._watch._import_stack:
                self._watch._import_stack[-1]["child_flipped"] = True


class _ImportFlipFinder(importlib.abc.MetaPathFinder):
    """A meta-path finder that finds nothing itself: it asks the others and wraps the loader.

    Why:
        Sitting at position 0 of sys.meta_path, it sees every import first. It delegates the
        real search to the finders after it, then wraps the loader of whatever they found.
    """

    def __init__(self, watch: GcWatch) -> None:
        """Remember the watch.

        Args:
            watch: the GcWatch to report to.
        """
        self._watch = watch

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Find the module with the remaining finders and wrap its loader.

        Why:
            We never find modules ourselves; we only wrap what the real finders found.

        Args:
            fullname: the module's full name.
            path: the parent package's __path__, or None for top-level modules.
            target: a module being reloaded, or None.

        Returns:
            The spec with a wrapped loader, or None to let the import system continue.
        """
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec: ModuleSpec | None = finder.find_spec(fullname, path, target)
            if spec is None:
                continue
            # WHY: only wrap loaders with exec_module (the modern protocol); leave the rest alone.
            if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                spec.loader = _WatchedLoader(spec.loader, self._watch, fullname)  # type: ignore[assignment]
            return spec
        return None


class GcWatch:
    """Install layers A, B and C(2); call poll() regularly for layer C(1).

    Why:
        One object owns the patches it makes, so uninstall() can undo exactly them (tests
        rely on `gc.enable is original` afterwards).
    """

    def __init__(
        self, *, expect_disabled: bool, on_event: Callable[[GcEvent], None], note: str = ""
    ) -> None:
        """Configure the watch; nothing is installed yet.

        Why:
            Creating and installing are separate so tests can inspect a watch before use.

        Args:
            expect_disabled: True if the GC should be off (gc_mode disabled, or the parent
                under freeze). False under threshold0 or in a freeze child: then only the
                shim reports, as information.
            on_event: called with every GcEvent.
            note: extra text added to shim events (e.g. "harmless under threshold0").
        """
        self.expect_disabled = expect_disabled
        self._on_event = on_event
        self._note = note
        self._original_enable: Callable[[], None] | None = None
        self._finder: _ImportFlipFinder | None = None
        self._import_stack: list[dict[str, bool]] = []
        self._poll_reported = False
        self._sentinel_reported = False
        self.installed = False

    def _emit(
        self,
        layer: Layer,
        *,
        module: str | None = None,
        stack: tuple[str, ...] = (),
        note: str = "",
    ) -> None:
        """Build a GcEvent and hand it to the callback.

        Args:
            layer: which layer saw it.
            module: the module (layer B).
            stack: a stack (layers A and C(2)).
            note: free text.
        """
        self._on_event(GcEvent(layer, time.monotonic(), module, stack, None, note))

    def _shim_enable(self) -> None:
        """Layer A: the replacement gc.enable: record who called it, then really enable.

        Why:
            Code that looks up gc.enable at call time (Python, and Cython-compiled modules)
            reaches this wrapper and leaves an exact stack.
        """
        note = "gc.enable() called" + (f"; {self._note}" if self._note else "")
        self._emit("shim", stack=_stack(skip=1), note=note)
        if self._original_enable is not None:
            self._original_enable()

    def _collection_sentinel(self, phase: str, info: dict[str, int]) -> None:
        """Layer C(2): a gc.callbacks hook that flags automatic collections we didn't expect.

        Why:
            gc.callbacks fires around every collection. If one *starts* while we expected the
            GC to be off but it is on, an automatic collection happened (an explicit
            gc.collect() while disabled runs with isenabled() False and is not flagged).
            The stack here is wherever the allocation that triggered it happened, not the
            culprit; the note says so.

        Args:
            phase: "start" or "stop".
            info: generation and counts (unused).
        """
        if (
            phase == "start"
            and self.expect_disabled
            and gc.isenabled()
            and not self._sentinel_reported
        ):
            # WHY: once per flip; every later automatic collection would repeat the same news.
            self._sentinel_reported = True
            self._emit(
                "collection_while_disabled",
                stack=_stack(skip=1),
                note=f"automatic gen-{info.get('generation')} collection; "
                "stack = allocation site, not culprit",
            )

    def install(self) -> None:
        """Install layers A, B and C(2). Calling it twice does nothing the second time.

        Why:
            Install as early as possible: before the app and its libraries are imported, so
            layer B sees their imports and layer A catches their calls.
        """
        if self.installed:
            return
        self._original_enable = gc.enable
        # PY: monkeypatching: we replace the module attribute; anyone looking up gc.enable
        # from now on gets our wrapper (docs/python/monkeypatching.md).
        gc.enable = self._shim_enable
        self._finder = _ImportFlipFinder(self)
        sys.meta_path.insert(0, self._finder)
        gc.callbacks.append(self._collection_sentinel)
        self.installed = True

    def uninstall(self) -> None:
        """Undo everything install() did.

        Why:
            Tests (and K7 scripts) must leave gc.enable, sys.meta_path and gc.callbacks
            exactly as they found them: these are process-wide.
        """
        if not self.installed:
            return
        if self._original_enable is not None:
            gc.enable = self._original_enable
        if self._finder in sys.meta_path:
            sys.meta_path.remove(self._finder)
        if self._collection_sentinel in gc.callbacks:
            gc.callbacks.remove(self._collection_sentinel)
        self.installed = False

    def poll(self) -> GcEvent | None:
        """Layer C(1): is the GC on although we expected it off? Reported once per flip.

        Why:
            Catches flips from any source after import, including C code; it only tells
            *when* (between two polls), so the worker adds the job that was running.

        Returns:
            The event if a new flip was seen, else None.
        """
        if not self.expect_disabled:
            return None
        if gc.isenabled() and not self._poll_reported:
            self._poll_reported = True
            event = GcEvent(
                "poll", time.monotonic(), None, (), None, "GC found enabled by the poll"
            )
            self._on_event(event)
            return event
        if not gc.isenabled():
            self._poll_reported = False
            self._sentinel_reported = False  # WHY: the GC is off again; the next flip is news
        return None
