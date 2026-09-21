"""Consumer-facing test support for Hermes plugins.

A plugin author needs two things a hand-rolled fake cannot give them: a plugin
context that works with no Hermes checkout present, and a way to find out that
the host's registrar signatures changed underneath them. Those pull in opposite
directions, so this module separates them.

:class:`RecordingPluginContext` records the registration calls a plugin makes
and needs no host at all. :meth:`RecordingPluginContext.check_against_host`
replays those recorded arguments through the real ``PluginContext`` signatures
wherever a host *is* importable, and reports what drifted.

That split is deliberate. A permissive fake accepts every future signature, and
a hand-written fake mirroring today's host is a frozen snapshot of one revision
-- both pass unchanged when the host gains a required parameter. Comparing a
fake's own signature to the real one catches nothing either, because ``**kwargs``
is compatible with every signature by construction. Binding the *recorded
arguments* is what catches it, along with removed registrars.

Renames need one extra step. Binding surfaces a renamed *keyword* on its own, as
an unexpected-keyword error, but it cannot see a renamed *positional* parameter
-- Python binds those by position, so the call still fits. For the registrars
the kit calls positionally, the replay therefore compares parameter names as
well, against the baseline in ``_POSITIONAL_PARAMS``.

Nothing here imports hermes-agent or a test framework at module import time.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "DEPLOYED_HERMES_REVISION",
    "Drift",
    "DriftReport",
    "RecordingPluginContext",
]

#: The hermes-agent revision this kit's contract lanes are pinned to.
#:
#: The CI workflow keeps its own literal -- ``actions/checkout`` consumes the
#: ``ref:`` before a Python toolchain exists, so the workflow cannot read this
#: value -- and a release-contract test asserts the two agree.
DEPLOYED_HERMES_REVISION = "f80f453ae0679347e38abc917c7f94f717bf96c5"

#: Parameter names the kit's registrars pass positionally, in order.
#:
#: These are the names a rename would change. A keyword call surfaces a rename
#: on its own as an unexpected-keyword error; a positional one does not, because
#: Python binds by position, so the replay needs a name baseline for these.
#: Registrars the kit calls entirely by keyword are absent and need no baseline.
_POSITIONAL_PARAMS: dict[str, tuple[str, ...]] = {
    "register_middleware": ("kind", "callback"),
    "register_hook": ("hook_name", "callback"),
    "register_memory_provider": ("provider",),
    "register_image_gen_provider": ("provider",),
    "register_video_gen_provider": ("provider",),
    "register_context_engine": ("engine",),
}

_ALL_REGISTRARS = (
    "register_tool",
    "register_command",
    "register_cli_command",
    "register_middleware",
    "register_hook",
    "register_skill",
    "register_memory_provider",
    "register_image_gen_provider",
    "register_video_gen_provider",
    "register_context_engine",
)


@dataclass(frozen=True)
class Drift:
    """One registrar whose recorded call no longer fits the host's signature."""

    registrar: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.registrar}: {self.detail}"


@dataclass(frozen=True)
class DriftReport:
    """The outcome of replaying recorded calls against a host.

    ``checked`` is the field that matters most: a report that could not run is
    not a report that found nothing. ``clean`` is never ``True`` unless the
    replay actually happened.
    """

    checked: bool
    drifts: tuple[Drift, ...] = ()
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.checked and not self.drifts

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.clean

    def __str__(self) -> str:
        if not self.checked:
            return f"unchecked: {self.detail}"
        if not self.drifts:
            return "clean"
        return "; ".join(str(d) for d in self.drifts)


def _positional_names(target: Callable[..., Any]) -> list[str] | None:
    """Parameter names a call can reach positionally, excluding ``self``."""
    try:
        params = list(inspect.signature(target).parameters.values())
    except (ValueError, KeyError, TypeError):
        return None
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return [p.name for p in params[1:] if p.kind in kinds]


def _bind(
    target: Callable[..., Any],
    args: Sequence[Any],
    kwargs: dict[str, Any],
    expected_positional: Sequence[str] = (),
) -> str | None:
    """Return a drift description, or ``None`` when the call still fits.

    ``target`` is looked up on the class, so its first parameter is ``self``;
    ``None`` stands in for the instance. Binding raises before any function body
    runs, so this never invokes host code.

    Binding alone cannot see a renamed *positional* parameter: Python binds by
    position, so ``register_hook(event_name, callback)`` accepts a call written
    for ``register_hook(hook_name, callback)`` and reports clean. Keyword calls
    do surface a rename, as an unexpected-keyword error. So for the positional
    slots a call actually used, compare names as well as arity --
    ``expected_positional`` carries the names the kit's own registrar declares
    for those slots, which is the shape the plugin was written against.
    """
    try:
        inspect.signature(target).bind(None, *args, **kwargs)
    except TypeError as exc:
        return str(exc)
    except (ValueError, KeyError) as exc:  # unintrospectable builtin or C callable
        return f"signature could not be read ({exc}); this surface was not checked"

    if args and expected_positional:
        host_names = _positional_names(target)
        if host_names is None:
            return None
        renamed = [
            f"{was!r} is now {now!r} at position {i + 1}"
            for i, (was, now) in enumerate(zip(expected_positional, host_names))
            if was != now
        ]
        if renamed:
            return "positional parameter renamed: " + ", ".join(renamed)
    return None


class RecordingPluginContext:
    """A plugin context that records what a plugin registers.

    Usable with no Hermes checkout present. Pass ``strict_against`` to bind each
    call against a host as it is made, or call :meth:`check_against_host`
    afterwards to replay everything at once.

    The shape knobs exist so both branches of the kit's own capability probe are
    reachable from a test: ``supports_references_dir`` controls whether
    ``register_skill`` advertises that keyword, and ``missing_registrars`` omits
    a registrar entirely, which is how a host that predates a surface behaves.
    """

    def __init__(
        self,
        *,
        name: str = "test-plugin",
        config: dict[str, Any] | None = None,
        supports_references_dir: bool = True,
        missing_registrars: Iterable[str] = (),
        strict_against: type | None = None,
        context_engine_result: bool = True,
    ) -> None:
        unknown = sorted(set(missing_registrars) - set(_ALL_REGISTRARS))
        if unknown:
            raise ValueError(
                f"unknown registrar(s) in missing_registrars: {', '.join(unknown)}; "
                f"expected any of {', '.join(_ALL_REGISTRARS)}"
            )

        self.manifest = SimpleNamespace(name=name, config=dict(config or {}))
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        self.tools: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.cli_commands: list[dict[str, Any]] = []
        self.middlewares: list[tuple[Any, Any]] = []
        self.hooks: list[tuple[str, Any]] = []
        self.skills: list[dict[str, Any]] = []
        self.memory_providers: list[Any] = []
        self.image_gen_providers: list[Any] = []
        self.video_gen_providers: list[Any] = []
        self.context_engines: list[Any] = []

        self.context_engine_result = context_engine_result
        self.subagent_lifecycle = SimpleNamespace(
            launch=lambda request: request,
            status=lambda handle: handle,
            wait=lambda handle, **kwargs: handle,
            cancel=lambda handle, **kwargs: handle,
            result=lambda handle: handle,
            reconnect=lambda handle: handle,
        )

        self._strict_against = strict_against
        self._install_registrars(
            supports_references_dir=supports_references_dir,
            missing=set(missing_registrars),
        )

    # -- recording -----------------------------------------------------------

    def _record(self, registrar: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """Capture one call, and bind it now when running strict."""
        self.calls.append((registrar, args, kwargs))
        if self._strict_against is None:
            return
        target = getattr(self._strict_against, registrar, None)
        if target is None:
            raise AttributeError(
                f"host {self._strict_against.__name__} does not expose {registrar}()"
            )
        detail = _bind(target, args, kwargs, _POSITIONAL_PARAMS.get(registrar, ()))
        if detail is not None:
            raise TypeError(f"{registrar}: {detail}")

    def _install_registrars(self, *, supports_references_dir: bool, missing: set[str]) -> None:
        """Bind registrars per instance, so an omitted one is genuinely absent.

        `register_plugin` detects host support with ``callable(getattr(ctx, name, None))``,
        so leaving the attribute unset is what makes a missing surface look missing.
        """

        def register_tool(
            name, toolset, schema, handler, requires_env=None, description="", emoji=""
        ) -> None:
            kwargs = dict(
                name=name, toolset=toolset, schema=schema, handler=handler,
                requires_env=requires_env, description=description, emoji=emoji,
            )
            self._record("register_tool", (), kwargs)
            self.tools.append(kwargs)

        def register_command(**kwargs) -> None:
            self._record("register_command", (), kwargs)
            self.commands.append(kwargs)

        def register_cli_command(**kwargs) -> None:
            self._record("register_cli_command", (), kwargs)
            self.cli_commands.append(kwargs)

        def register_middleware(kind, callback) -> None:
            self._record("register_middleware", (kind, callback), {})
            self.middlewares.append((kind, callback))

        def register_hook(hook_name, callback) -> None:
            self._record("register_hook", (hook_name, callback), {})
            self.hooks.append((hook_name, callback))

        def register_skill_with_references(name, path, description="", references_dir=None) -> None:
            kwargs = dict(name=name, path=path, description=description)
            if references_dir is not None:
                kwargs["references_dir"] = references_dir
            self._record("register_skill", (), kwargs)
            self.skills.append(kwargs)

        def register_skill_only(name, path, description="") -> None:
            kwargs = dict(name=name, path=path, description=description)
            self._record("register_skill", (), kwargs)
            self.skills.append(kwargs)

        def register_memory_provider(provider) -> None:
            self._record("register_memory_provider", (provider,), {})
            self.memory_providers.append(provider)

        def register_image_gen_provider(provider) -> None:
            self._record("register_image_gen_provider", (provider,), {})
            self.image_gen_providers.append(provider)

        def register_video_gen_provider(provider) -> None:
            self._record("register_video_gen_provider", (provider,), {})
            self.video_gen_providers.append(provider)

        def register_context_engine(engine):
            self._record("register_context_engine", (engine,), {})
            self.context_engines.append(engine)
            return self.context_engine_result

        implementations: dict[str, Callable[..., Any]] = {
            "register_tool": register_tool,
            "register_command": register_command,
            "register_cli_command": register_cli_command,
            "register_middleware": register_middleware,
            "register_hook": register_hook,
            "register_skill": (
                register_skill_with_references
                if supports_references_dir
                else register_skill_only
            ),
            "register_memory_provider": register_memory_provider,
            "register_image_gen_provider": register_image_gen_provider,
            "register_video_gen_provider": register_video_gen_provider,
            "register_context_engine": register_context_engine,
        }

        for registrar, impl in implementations.items():
            if registrar not in missing:
                setattr(self, registrar, impl)

    # -- checking ------------------------------------------------------------

    def check_against_host(self, host: type | None) -> DriftReport:
        """Replay every recorded call through ``host``'s signatures.

        Pass the real ``PluginContext`` class. ``None`` means no host was
        available, which produces an explicitly unchecked report rather than a
        clean one -- not having looked is not the same as having found nothing.
        """
        if host is None:
            return DriftReport(
                checked=False,
                detail=(
                    "no hermes-agent checkout was available, so no drift check ran. "
                    f"Provision hermes-agent at {DEPLOYED_HERMES_REVISION} and point "
                    "HERMES_AGENT_PATH at it to enable the check."
                ),
            )

        drifts: list[Drift] = []
        for registrar, args, kwargs in self.calls:
            target = getattr(host, registrar, None)
            if target is None:
                drifts.append(
                    Drift(registrar, f"host {host.__name__} does not expose {registrar}()")
                )
                continue
            detail = _bind(target, args, kwargs, _POSITIONAL_PARAMS.get(registrar, ()))
            if detail is not None:
                drifts.append(Drift(registrar, detail))

        return DriftReport(checked=True, drifts=tuple(drifts))
