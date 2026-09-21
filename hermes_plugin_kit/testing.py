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
import os
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "DEPLOYED_HERMES_REVISION",
    "RECEIPT_FIELD_ORDER",
    "CheckResult",
    "ResolvedHost",
    "Drift",
    "DriftReport",
    "RecordingPluginContext",
    "check_registration",
    "receipt_fields",
    "resolve_hermes_checkout",
]

#: Receipt fields in the order ``log_registration_summary`` emits them.
#:
#: This is deliberately not ``RegistrationSummary``'s dataclass field order --
#: the two genuinely differ, and the logged string is the contract AGENTS.md
#: pins. Asserting the dataclass order instead would pass while the external
#: contract drifted.
RECEIPT_FIELD_ORDER: tuple[str, ...] = (
    "commands",
    "cli_commands",
    "tools",
    "middlewares",
    "hooks",
    "skills",
    "skipped_optional_skills",
    "memory_providers",
    "image_gen_providers",
    "video_gen_providers",
    "capabilities",
    "context_engine",
    "context_engine_registration",
)

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

    The shape knobs exist so every branch of the kit's own capability probe is
    reachable from a test:

    - ``supports_references_dir`` -- whether ``register_skill`` advertises that
      keyword at all.
    - ``references_dir_probe_lies`` -- a permissive signature that satisfies the
      probe and then rejects the keyword at call time. This is the shape that
      fools a signature check, and the only way to reach the documented retry.
    - ``register_skill_error`` -- raise this from ``register_skill``. Used to
      prove the retry stays narrow and does not swallow an unrelated error.
    - ``missing_registrars`` -- omit a registrar entirely, which is how a host
      predating a surface behaves.
    """

    def __init__(
        self,
        *,
        name: str = "test-plugin",
        config: dict[str, Any] | None = None,
        supports_references_dir: bool = True,
        references_dir_probe_lies: bool = False,
        register_skill_error: BaseException | None = None,
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
            references_dir_probe_lies=references_dir_probe_lies,
            register_skill_error=register_skill_error,
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

    def _install_registrars(
        self,
        *,
        supports_references_dir: bool,
        references_dir_probe_lies: bool,
        register_skill_error: BaseException | None,
        missing: set[str],
    ) -> None:
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

        def register_skill_lying_probe(**kwargs) -> None:
            # A permissive signature satisfies the kit's keyword probe, so the
            # probe reports support this host does not have. Rejecting at call
            # time is what drives the documented retry.
            if "references_dir" in kwargs:
                raise TypeError(
                    "register_skill() got an unexpected keyword argument 'references_dir'"
                )
            self._record("register_skill", (), kwargs)
            self.skills.append(kwargs)

        def register_skill_raising(**kwargs) -> None:
            raise register_skill_error

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

        def _pick_skill_registrar() -> Callable[..., Any]:
            # Order matters: a raising host outranks the probe shapes, since a
            # test asking for an error wants it regardless of the keyword.
            if register_skill_error is not None:
                return register_skill_raising
            if references_dir_probe_lies:
                return register_skill_lying_probe
            if supports_references_dir:
                return register_skill_with_references
            return register_skill_only

        implementations: dict[str, Callable[..., Any]] = {
            "register_tool": register_tool,
            "register_command": register_command,
            "register_cli_command": register_cli_command,
            "register_middleware": register_middleware,
            "register_hook": register_hook,
            "register_skill": _pick_skill_registrar(),
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


@dataclass(frozen=True)
class CheckResult:
    """A framework-neutral verdict: the consumer decides how to assert it.

    The kit is stdlib ``unittest``, but consumers may not be, so these helpers
    return a result rather than raising -- ``assert result.ok, result`` works
    under any runner, and ``problems`` says what to fix.
    """

    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.ok

    def __str__(self) -> str:
        return "ok" if self.ok else "; ".join(self.problems)


def receipt_fields(message: str) -> dict[str, tuple[str, ...]]:
    """Parse one registration receipt into its fields, in emitted order.

    Lets a consumer assert what was registered without copying the kit's format
    string into their own test. ``<none>`` becomes an empty tuple, so an absent
    surface and an empty one read the same way.
    """
    fields: dict[str, tuple[str, ...]] = {}
    for chunk in message.split("; "):
        key, sep, value = chunk.partition("=")
        key = key.strip()
        if not sep or key not in RECEIPT_FIELD_ORDER:
            continue
        value = value.strip()
        fields[key] = () if value == "<none>" else tuple(value.split(","))
    return {k: fields[k] for k in RECEIPT_FIELD_ORDER if k in fields}


def _schema_problems(name: str, schema: Any) -> list[str]:
    """A tool's arguments belong under ``parameters``, never flattened beside it.

    The kit hands ``register_tool`` the inner function schema --
    ``{name, description, parameters}`` -- and Hermes' registry wraps it so the
    arguments land at ``function.parameters`` in the tool it exposes. So a
    top-level ``properties`` here is the defect: it flattens the arguments and
    they never reach ``function.parameters`` after conversion.
    """
    if not isinstance(schema, dict) or not schema:
        return []
    problems: list[str] = []
    if "properties" in schema:
        problems.append(
            f"tool {name!r} declares arguments beside `parameters` instead of inside "
            f"it, so they would not reach function.parameters once Hermes converts "
            f"the schema"
        )
    parameters = schema.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        problems.append(f"tool {name!r} has a non-object `parameters`")
    elif isinstance(parameters, dict) and parameters.get("type") not in (None, "object"):
        problems.append(
            f"tool {name!r} has parameters.type {parameters.get('type')!r}; "
            f"Hermes expects 'object'"
        )
    return problems


def check_registration(ctx: RecordingPluginContext) -> CheckResult:
    """Check what a plugin registered against the kit's standing conventions.

    Covers deterministic ordering, duplicate names, and the
    arguments-under-``function.parameters`` schema shape -- the contracts a
    consumer would otherwise copy out of the kit's own test suite.

    A name used for both a slash command and a CLI command is allowed; the kit
    permits that pairing deliberately, so the duplicate check is per surface.
    """
    problems: list[str] = []

    named_surfaces = {
        "tools": [t.get("name") for t in ctx.tools],
        "commands": [c.get("name") for c in ctx.commands],
        "cli_commands": [c.get("name") for c in ctx.cli_commands],
        "skills": [s.get("name") for s in ctx.skills],
        "hooks": [h[0] for h in ctx.hooks],
        "middlewares": [str(m[0]) for m in ctx.middlewares],
    }

    for surface, names in named_surfaces.items():
        present = [n for n in names if n is not None]
        duplicates = sorted({n for n in present if present.count(n) > 1})
        if duplicates:
            problems.append(f"duplicate {surface} name(s): {', '.join(duplicates)}")
        if present != sorted(present):
            problems.append(
                f"{surface} were recorded out of order ({', '.join(present)}); "
                f"register_plugin sorts every registrar, so this indicates a "
                f"registration path that bypassed it"
            )

    for tool in ctx.tools:
        problems.extend(_schema_problems(tool.get("name"), tool.get("schema")))

    return CheckResult(tuple(problems))


#: How long a fetch may run before it is abandoned, in seconds.
#:
#: A test-support library must not hang a consumer's suite. When this elapses
#: the result is an ordinary unavailable :class:`ResolvedHost`, not an exception.
FETCH_TIMEOUT_SECONDS = 300

_HERMES_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"


@dataclass(frozen=True)
class ResolvedHost:
    """Where a Hermes checkout is, or why there isn't one.

    ``available`` false is a normal outcome, not an error: offline, sandboxed,
    and air-gapped runs all land here, and the caller turns it into the
    "unchecked" drift report rather than a failure.
    """

    path: Path | None
    revision: str | None
    detail: str

    @property
    def available(self) -> bool:
        return self.path is not None

    def plugin_context_class(self) -> type | None:
        """Import the real ``PluginContext`` from this checkout, or ``None``.

        Returns ``None`` when no checkout resolved, which is exactly what
        :meth:`RecordingPluginContext.check_against_host` treats as unchecked --
        so a consumer can pass this straight through.
        """
        if self.path is None:
            return None
        root = str(self.path)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from hermes_cli.plugins import PluginContext  # type: ignore
        except Exception:  # pragma: no cover - depends on the checkout's shape
            return None
        return PluginContext


def _git(args: list[str], *, cwd: Path | None = None, timeout: int) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def _head_revision(path: Path, *, timeout: int) -> str | None:
    try:
        return _git(["rev-parse", "HEAD"], cwd=path, timeout=timeout)
    except Exception:
        return None


def _looks_like_hermes(path: Path) -> bool:
    return (path / "hermes_cli" / "plugins.py").exists()


def _default_fetcher(url: str, revision: str, destination: Path, *, timeout: int) -> None:
    """Fetch exactly one revision, shallow, into ``destination``."""
    destination.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", str(destination)], timeout=timeout)
    _git(["remote", "add", "origin", url], cwd=destination, timeout=timeout)
    _git(["fetch", "-q", "--depth", "1", "origin", revision], cwd=destination, timeout=timeout)
    _git(["checkout", "-q", "FETCH_HEAD"], cwd=destination, timeout=timeout)


def resolve_hermes_checkout(
    *,
    revision: str = DEPLOYED_HERMES_REVISION,
    cache_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = FETCH_TIMEOUT_SECONDS,
    fetcher: Callable[..., None] | None = None,
    url: str = _HERMES_REPO_URL,
) -> ResolvedHost:
    """Find a Hermes checkout the drift replay can use, fetching one if needed.

    Without this the replay only ever runs inside this repository: a consumer's
    machine has no Hermes checkout, so recording always happens and checking
    never does.

    Resolution order, and why:

    1. ``HERMES_AGENT_PATH`` always wins. An operator who pointed at a checkout
       meant it, and a library should not second-guess that or fetch over it.
    2. A cache already at ``revision`` is reused, so the fetch happens at most
       once per machine.
    3. Otherwise fetch. A cache sitting at some *other* revision is never
       returned as if it were the pin -- that would receipt the wrong host.

    Any failure returns an unavailable result naming the revision and the
    reason. It never raises and never hangs past ``timeout``.
    """
    environ = os.environ if env is None else env
    supplied = (environ.get("HERMES_AGENT_PATH") or "").strip()
    if supplied:
        path = Path(supplied)
        if not _looks_like_hermes(path):
            return ResolvedHost(
                None, None,
                f"HERMES_AGENT_PATH={supplied} has no hermes_cli/plugins.py, so it is "
                f"not a Hermes checkout. Point it at one, or unset it to let the "
                f"harness fetch {revision}.",
            )
        return ResolvedHost(path, _head_revision(path, timeout=timeout), f"supplied: {supplied}")

    cache = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    if _looks_like_hermes(cache):
        head = _head_revision(cache, timeout=timeout)
        if head == revision:
            return ResolvedHost(cache, head, f"cached at {revision}")

    fetch = fetcher if fetcher is not None else _default_fetcher
    try:
        fetch(url, revision, cache, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ResolvedHost(
            None, None,
            f"fetching hermes-agent at {revision} exceeded {timeout}s and was "
            f"abandoned; set HERMES_AGENT_PATH to a local checkout to skip the fetch.",
        )
    except Exception as exc:
        return ResolvedHost(
            None, None,
            f"could not obtain hermes-agent at {revision}: {exc}. "
            f"Set HERMES_AGENT_PATH to a local checkout to run the drift check offline.",
        )

    head = _head_revision(cache, timeout=timeout)
    if not _looks_like_hermes(cache) or head != revision:
        return ResolvedHost(
            None, None,
            f"fetch completed but the checkout is not at {revision} (found {head}); "
            f"refusing to report a revision the replay did not actually use.",
        )
    return ResolvedHost(cache, head, f"fetched {revision}")


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "hermes-plugin-kit" / "hermes-agent"
