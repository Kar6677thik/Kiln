"""Who turned the GIL back on? Names the module whose import re-enabled it (free-threaded builds).

WHY THIS MODULE EXISTS
    On a free-threaded build (3.15t) there is no GIL, until you import a C extension that
    hasn't declared `Py_mod_gil`. Then CPython quietly turns the GIL on for the whole
    process, prints one RuntimeWarning, and carries on, now slow again
    (docs/concepts/free-threading.md). The warning names the extension, but not who imported
    it, and it is easy to miss in a busy log. This module watches every import and reports
    the innermost module that flipped the GIL, with the chain of imports that led there.

HOW IT FITS
    master/arbiter.py (before preloading) and worker/main.py (before importing the app)
        ──GilWatch(on_event).install()── only if sysconfig Py_GIL_DISABLED == 1
    every import ──GilWatchFinder──▶ wrapped exec_module: is_gil_enabled() before/after
        flipped ──▶ on_event(GilReenabled(module, import_chain, origin)) ──▶ `gil_reenabled`
    warnings.showwarning wrapper ──▶ CPython's own "GIL ... enabled" RuntimeWarning, as a
        cross-check (gate G4.3)

    The same delegate-and-wrap pattern as K3's gcwatch layer B
    (docs/python/import-hooks-meta-path.md).

READ FIRST
    docs/concepts/free-threading.md, docs/concepts/c-extensions.md,
    docs/python/import-hooks-meta-path.md

MILESTONE
    Introduced in K4.
"""

from __future__ import annotations

import importlib.abc
import logging
import os
import sys
import sysconfig
import threading
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

HINT = "this extension did not declare Py_mod_gil; set PYTHON_GIL=0 to force (unsafe) or upgrade it"


def free_threaded_build() -> bool:
    """Whether this interpreter was built without the GIL.

    Why:
        On a normal build the GIL is always on, so there is nothing to watch.

    Returns:
        True on a free-threaded ("t") build.
    """
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def gil_enabled() -> bool:
    """Whether the GIL is on right now.

    Why:
        sys._is_gil_enabled exists from 3.13; before that the GIL is always on.

    Returns:
        The current state.
    """
    probe = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else bool(probe())


@dataclass(frozen=True, slots=True)
class GilReenabled:
    """One sighting: importing `module` switched the GIL on.

    Why:
        The log line needs the culprit, how we got there, and where its file is.
    """

    module: str  # the innermost module whose import flipped the GIL
    import_chain: tuple[str, ...]  # outermost → innermost, e.g. ("app", "helpers", "ext")
    origin: str  # the module's file (the .so)
    forced_off: bool  # PYTHON_GIL=0: the GIL stayed off although the module asked for it
    warning: str  # CPython's own RuntimeWarning text, if it was seen (gate G4.3)


class _GilWatchedLoader:
    """A loader that records the GIL state around the real loader's exec_module.

    Why:
        Every module, Python or C, is executed by some loader; wrapping that one call lets
        us compare the GIL state before and after, and forward everything else unchanged.
    """

    def __init__(self, inner: Any, watch: GilWatch, name: str, origin: str) -> None:  # noqa: ANN401  # WHY: any loader
        """Remember the real loader.

        Args:
            inner: the original loader.
            watch: the GilWatch to report to.
            name: the module's name.
            origin: its file path.
        """
        self._inner = inner
        self._watch = watch
        self._name = name
        self._origin = origin

    def __getattr__(self, attribute: str) -> Any:  # noqa: ANN401  # WHY: forwards anything
        """Forward every other attribute to the real loader.

        Args:
            attribute: the attribute name.

        Returns:
            The real loader's attribute.
        """
        return getattr(self._inner, attribute)

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Forward module creation. An extension's PyInit_* runs here, so watch it too.

        Why:
            For a C extension the GIL decision is taken while its module object is created
            (reading its Py_mod_gil slot), which may happen here rather than in exec_module.

        Args:
            spec: the module spec.

        Returns:
            What the real loader returns.
        """
        frame = self._watch._enter(self._name)
        try:
            module: ModuleType | None = self._inner.create_module(spec)
        finally:
            self._watch._leave(frame, self._name, self._origin)
        return module

    def exec_module(self, module: ModuleType) -> None:
        """Run the real exec_module, recording the GIL state around it.

        Args:
            module: the module being executed.
        """
        frame = self._watch._enter(self._name)
        try:
            self._inner.exec_module(module)
        finally:
            self._watch._leave(frame, self._name, self._origin)


class GilWatchFinder(importlib.abc.MetaPathFinder):
    """At position 0 of sys.meta_path: finds nothing itself, wraps what the others find.

    Why:
        Seeing every import first is the only way to watch imports we didn't write.
    """

    def __init__(self, watch: GilWatch) -> None:
        """Remember the watch.

        Args:
            watch: the GilWatch to report to.
        """
        self._watch = watch

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        """Delegate to the remaining finders, then wrap the loader.

        Why:
            We never locate modules ourselves; we only observe the real finders' result.

        Args:
            fullname: the module's full name.
            path: the parent package's __path__, or None.
            target: a module being reloaded, or None.

        Returns:
            The spec with a wrapped loader, or None.
        """
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec: ModuleSpec | None = finder.find_spec(fullname, path, target)
            if spec is None:
                continue
            if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                origin = spec.origin or ""
                spec.loader = _GilWatchedLoader(spec.loader, self._watch, fullname, origin)  # type: ignore[assignment]
            return spec
        return None


class GilWatch:
    """Install the finder and the warnings hook; report each GIL flip once, by module.

    Why:
        One object owns its patches, so uninstall() restores exactly what it changed.
    """

    def __init__(self, on_event: Callable[[GilReenabled], None]) -> None:
        """Configure the watch; nothing is installed yet.

        Why:
            Separate creation and installation, so tests can inspect it first.

        Args:
            on_event: called with every GilReenabled.
        """
        self._on_event = on_event
        self._finder: GilWatchFinder | None = None
        self._original_showwarning: Callable[..., None] | None = None
        self._local = threading.local()
        self._warnings: list[str] = []
        self.events: list[GilReenabled] = []
        self.installed = False

    def _stack(self) -> list[dict[str, object]]:
        """This thread's stack of imports in progress (imports can run in several threads).

        Returns:
            The stack.
        """
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _enter(self, name: str) -> dict[str, object]:
        """An import (or module creation) of `name` starts.

        Args:
            name: the module.

        Returns:
            The stack frame for _leave.
        """
        frame: dict[str, object] = {"name": name, "before": gil_enabled(), "child": False}
        self._stack().append(frame)
        return frame

    def _leave(self, frame: dict[str, object], name: str, origin: str) -> None:
        """An import of `name` ended: blame it if the GIL flipped and no inner import did.

        Why:
            Importing a package imports its submodules; all of them see "off before, on
            after". Only the innermost one whose *children* didn't flip it is the culprit.

        Args:
            frame: the frame from _enter.
            name: the module.
            origin: its file.
        """
        stack = self._stack()
        chain = tuple(str(f["name"]) for f in stack)
        stack.pop()
        flipped = (not frame["before"]) and gil_enabled()
        warned = self._warning_about(name)
        forced = not gil_enabled() and bool(warned)
        if (flipped or forced) and not frame["child"]:
            event = GilReenabled(name, chain, origin, forced, warned)
            self.events.append(event)
            self._on_event(event)
        if (flipped or forced) and stack:
            stack[-1]["child"] = True

    def _warning_about(self, name: str) -> str:
        """CPython's own RuntimeWarning text for this module, if one was emitted.

        Args:
            name: the module.

        Returns:
            The warning text, or "".
        """
        for text in self._warnings:
            if name in text:
                return text
        return ""

    def _showwarning(self, message: Warning | str, category: type[Warning], *args: Any) -> None:  # noqa: ANN401
        """warnings.showwarning replacement: remember GIL warnings, then show as usual.

        Why:
            warnings.catch_warnings can't wrap a whole process lifetime; replacing the
            display function can (K4 §5.8).

        Args:
            message: the warning.
            category: its class.
            *args: filename, lineno, file, line (passed through).
        """
        text = str(message)
        if "GIL" in text:
            self._warnings.append(text)
        if self._original_showwarning is not None:
            self._original_showwarning(message, category, *args)

    def install(self) -> bool:
        """Install the finder and the warnings hook, on free-threaded builds only.

        Why:
            On a GIL build the GIL can't be "re-enabled", so watching costs and finds nothing.

        Returns:
            True if installed.
        """
        if self.installed or not free_threaded_build():
            return False
        self._finder = GilWatchFinder(self)
        sys.meta_path.insert(0, self._finder)
        self._original_showwarning = warnings.showwarning
        warnings.showwarning = self._showwarning  # type: ignore[assignment]  # WHY: same call shape, *args
        self.installed = True
        return True

    def uninstall(self) -> None:
        """Restore sys.meta_path and warnings.showwarning.

        Why:
            Tests must leave process-wide state as they found it.
        """
        if not self.installed:
            return
        if self._finder in sys.meta_path:
            sys.meta_path.remove(self._finder)
        if self._original_showwarning is not None:
            warnings.showwarning = self._original_showwarning
        self.installed = False


def forced_off() -> bool:
    """Whether PYTHON_GIL=0 is forcing the GIL off.

    Returns:
        True if the variable is set to "0".
    """
    return os.environ.get("PYTHON_GIL") == "0"


def log_event(logger: logging.Logger, event: GilReenabled) -> None:
    """Log a sighting as `gil_reenabled` (or `gil_forced_off` under PYTHON_GIL=0), WARNING.

    Why:
        One format for the master and every worker (spec/06 Â§2.6).

    Args:
        logger: where to log.
        event: the sighting.
    """
    # PY: imported here so the classes above stay importable without Kiln (like gcwatch).
    from kiln import logsetup

    name = "gil_forced_off" if event.forced_off else "gil_reenabled"
    logsetup.event(
        logger,
        logging.WARNING,
        name,
        module=event.module,
        detected_after_import_of=event.import_chain[0] if event.import_chain else event.module,
        import_chain=">".join(event.import_chain),
        origin=event.origin,
        hint=HINT if not event.forced_off else "running without the GIL on purpose (unsafe)",
    )


def install_for_process(logger: logging.Logger) -> GilWatch | None:
    """Install a watch that logs every sighting, if this is a free-threaded build.

    Why:
        The master and the workers call this as early as possible, before any app import.

    Args:
        logger: where to log.

    Returns:
        The watch, or None on a GIL build.
    """
    global _ACTIVE  # WHY: one watch per process; a forked child inherits it
    if _ACTIVE is not None:
        return _ACTIVE
    watch = GilWatch(on_event=lambda event: log_event(logger, event))
    if not watch.install():
        return None
    _ACTIVE = watch
    return watch


# LAB: the watch installed in this process, if any (code-style §6 exception: a forked worker
# inherits its master's watch, and installing a second one would report every flip twice).
_ACTIVE: GilWatch | None = None
