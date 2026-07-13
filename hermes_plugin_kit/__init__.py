"""hermes-plugin-kit — convention-correct tool registration for hermes-agent plugins.

Reach for ``@tool`` + ``register_all`` and every hermes tool convention is applied
for you, so the classes of bug that bite hand-written plugins cannot recur:

- **Schema convention** — arguments are nested under a ``parameters`` wrapper
  (``{name, description, parameters: {type, properties, required,
  additionalProperties}}``). A top-level ``properties`` is what makes a model
  receive empty ``{}`` arguments; the kit makes that impossible.
- **Self-documenting** — each required argument's name (and example) is appended
  to the tool description, the one field a model always sees.
- **Validation + instructive errors** — a missing/blank required argument returns
  an error that names the argument and its example, and logs a WARNING.
- **Logging** — DEBUG on invocation, WARNING on rejected/failed calls with
  tracebacks for exceptions, and INFO on success with elapsed time. Arguments
  are truncated and secret-looking values are recursively redacted.
- **Envelope + safety** — a handler returns a plain ``dict`` (or raises); the kit
  encodes the JSON string, wraps exceptions, and always returns ``str`` from an
  ``(args, **kwargs)`` signature, exactly as the registry requires.

Usage::

    from hermes_plugin_kit import tool, register_all, str_arg, int_arg

    @tool(
        toolset="messaging",
        requires_env=["DISCORD_BOT_TOKEN"],
        params={
            "thread_id_or_url": str_arg(
                "Discord thread link or numeric ID",
                required=True, example="123456789012345678",
            ),
            "limit": int_arg("Messages to return", minimum=1, maximum=100),
        },
    )
    def discord_read_thread(args, **kwargs):
        '''Read recent messages from a Discord thread the bot can already access.'''
        return {"messages": _read(args["thread_id_or_url"])}

    def register(ctx):
        register_all(ctx, __name__)
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "tool",
    "hook",
    "plugin_skill",
    "register_plugin",
    "PluginSkill",
    "RegistrationSummary",
    "register_all",
    "build_schema",
    "tool_name",
    "validate_tool_name",
    "arg",
    "str_arg",
    "int_arg",
    "bool_arg",
]

_SPEC_ATTR = "_hpk_tool_spec"
_HOOK_SPEC_ATTR = "_hpk_hook_spec"
_REDACT_HINTS = ("token", "secret", "password", "passwd", "api_key", "apikey", "auth")
_MAX_LOG_CHARS = 200
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_AGENT_LOOP_TOOL_NAMES = frozenset({"todo", "memory", "session_search", "delegate_task"})
_RESERVED_NAMESPACE_PREFIXES = ("memory_",)
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class PluginSkill:
    """Validated declaration for a plugin-owned, read-only Hermes skill."""

    name: str
    path: Path
    description: str
    optional: bool = False


@dataclass(frozen=True)
class RegistrationSummary:
    """Inventory of lifecycle surfaces registered by :func:`register_plugin`."""

    tools: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    skipped_optional_skills: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Tool naming
# ---------------------------------------------------------------------------

def validate_tool_name(name: str, *, namespace: str | None = None) -> str:
    """Validate a Hermes plugin tool name and return it unchanged.

    Hermes keeps tool names in one global registry. The core agent loop also
    intercepts a few names before registry dispatch, so plugin tools must avoid
    those names and should carry an explicit plugin/domain prefix.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("tool name is required")
    if not _TOOL_NAME_RE.fullmatch(name):
        raise ValueError(
            f"tool name {name!r} must match {_TOOL_NAME_RE.pattern!r}"
        )
    if name in _AGENT_LOOP_TOOL_NAMES:
        raise ValueError(
            f"tool name {name!r} is reserved by the Hermes agent loop"
        )
    if name.startswith(_RESERVED_NAMESPACE_PREFIXES):
        raise ValueError(
            f"tool name {name!r} uses a reserved Hermes core namespace; "
            "choose a plugin/domain namespace instead"
        )
    if namespace is not None:
        if not _TOOL_NAME_RE.fullmatch(namespace):
            raise ValueError(
                f"tool namespace {namespace!r} must match {_TOOL_NAME_RE.pattern!r}"
            )
        if namespace in _AGENT_LOOP_TOOL_NAMES:
            raise ValueError(
                f"tool namespace {namespace!r} is reserved by the Hermes agent loop"
            )
        expected = f"{namespace}_"
        if not name.startswith(expected):
            raise ValueError(
                f"tool name {name!r} must start with explicit namespace {expected!r}"
            )
    return name


def tool_name(namespace: str, verb: str, noun: str) -> str:
    """Build and validate a namespaced tool name such as ``discord_read_thread``."""
    name = "_".join(part.strip("_") for part in (namespace, verb, noun) if part)
    return validate_tool_name(name, namespace=namespace)


# ---------------------------------------------------------------------------
# Argument specs
# ---------------------------------------------------------------------------

def arg(
    type: str,
    description: str,
    *,
    required: bool = False,
    example: Any = None,
    enum: list | None = None,
    **extra: Any,
) -> dict:
    """A single argument spec. ``required``/``example`` are kit metadata, stripped
    out of the emitted JSON Schema; everything else passes through verbatim."""
    spec: dict[str, Any] = {"type": type, "description": description}
    if enum:
        spec["enum"] = enum
    spec.update(extra)
    spec["_required"] = required
    if example is not None:
        spec["_example"] = example
    return spec


def str_arg(description, *, required=False, example=None, enum=None, min_length=None, **extra):
    if min_length is not None:
        extra["minLength"] = min_length
    return arg("string", description, required=required, example=example, enum=enum, **extra)


def int_arg(description, *, required=False, example=None, minimum=None, maximum=None, **extra):
    if minimum is not None:
        extra["minimum"] = minimum
    if maximum is not None:
        extra["maximum"] = maximum
    return arg("integer", description, required=required, example=example, **extra)


def bool_arg(description, *, required=False, example=None, **extra):
    return arg("boolean", description, required=required, example=example, **extra)


# ---------------------------------------------------------------------------
# Schema building
# ---------------------------------------------------------------------------

def _split_params(params: dict | None) -> tuple[dict, list, dict]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    examples: dict[str, Any] = {}
    for key, spec in (params or {}).items():
        spec = dict(spec)
        if spec.pop("_required", False):
            required.append(key)
        example = spec.pop("_example", None)
        if example is not None:
            examples[key] = example
        properties[key] = spec
    return properties, required, examples


def _augment_description(description: str, required: list, examples: dict) -> str:
    if not required:
        return description
    bits = [
        f"`{key}`" + (f" (e.g. {examples[key]!r})" if key in examples else "")
        for key in required
    ]
    return f"{description} Required: {', '.join(bits)}."


def build_schema(name: str, description: str, params: dict | None) -> dict:
    """Build a hermes-convention tool schema: arguments nested under ``parameters``."""
    validate_tool_name(name)
    properties, required, examples = _split_params(params)
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {
        "name": name,
        "description": _augment_description(description, required, examples),
        "parameters": parameters,
    }


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _redacted_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "***"
                if any(hint in str(key).lower() for hint in _REDACT_HINTS)
                else _redacted_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redacted_value(item) for item in value]
    return value


def _redacted_args(args: dict) -> dict:
    return _redacted_value(args or {})


def _truncate(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_LOG_CHARS else text[:_MAX_LOG_CHARS] + "…"


def _safe_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return only Hermes correlation identifiers that are safe to log."""
    return {
        key: kwargs[key]
        for key in ("session_id", "task_id")
        if kwargs.get(key) is not None
    }


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------

def hook(name: str) -> Callable:
    """Mark and instrument a Hermes lifecycle hook callback.

    The wrapper preserves Hermes' callback contract: keyword arguments and the
    return value pass through unchanged, and exceptions are re-raised for the
    plugin manager to isolate. Logs contain correlation identifiers only, never
    message payloads or exception messages.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("hook name is required")
    hook_name = name.strip()

    def decorate(fn: Callable) -> Callable:
        log = logging.getLogger(fn.__module__ or "hermes_plugin_kit")

        @functools.wraps(fn)
        def wrapper(**kwargs: Any) -> Any:
            started = time.perf_counter()
            context = _truncate(_safe_context(kwargs))
            log.debug(
                "%s: invoked; context=%s",
                hook_name,
                context,
            )
            try:
                result = fn(**kwargs)
            except Exception as exc:
                log.warning(
                    "%s: callback raised; elapsed_ms=%.2f; error_type=%s; context=%s",
                    hook_name,
                    (time.perf_counter() - started) * 1000,
                    type(exc).__name__,
                    context,
                )
                raise
            log.info(
                "%s: ok; elapsed_ms=%.2f; result=%s; context=%s",
                hook_name,
                (time.perf_counter() - started) * 1000,
                type(result).__name__,
                context,
            )
            return result

        setattr(wrapper, _HOOK_SPEC_ATTR, {"name": hook_name})
        return wrapper

    return decorate


def plugin_skill(
    name: str,
    path: str | Path,
    description: str,
    optional: bool = False,
) -> PluginSkill:
    """Declare a plugin-owned ``SKILL.md`` for :func:`register_plugin`."""
    if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
        raise ValueError("skill name must match [a-zA-Z0-9_-]+ and contain no namespace")
    skill_path = Path(path)
    if skill_path.name != "SKILL.md":
        raise ValueError("skill path must point to SKILL.md")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("skill description is required")
    return PluginSkill(name, skill_path, description.strip(), bool(optional))

def tool(
    *,
    toolset: str,
    params: dict | None = None,
    name: str | None = None,
    namespace: str | None = None,
    description: str | None = None,
    requires_env: list | None = None,
    emoji: str = "",
) -> Callable:
    """Register-ready hermes tool from a plain handler.

    The wrapped handler receives ``(args, **kwargs)`` and returns a ``dict``
    (becomes the success ``data``) or raises (becomes a tool error). It may also
    return a ``str`` as an escape hatch (treated as already-encoded JSON).
    """

    def decorate(fn: Callable) -> Callable:
        tool_name = validate_tool_name(name or fn.__name__, namespace=namespace)
        doc = (description or inspect.getdoc(fn) or "").strip()
        if not doc:
            raise ValueError(
                f"@tool {tool_name!r}: a description is required (docstring or description=)."
            )
        schema = build_schema(tool_name, doc, params)
        required = list(schema["parameters"].get("required", []))
        examples = {
            key: (params or {}).get(key, {}).get("_example")
            for key in required
        }
        log = logging.getLogger(fn.__module__ or "hermes_plugin_kit")

        @functools.wraps(fn)
        def wrapper(args: dict, **kwargs: Any) -> str:
            args = args or {}
            started = time.perf_counter()
            safe_args = _truncate(_redacted_args(args))
            context = _safe_context(kwargs)
            log.debug(
                "%s: invoked; args=%s; context=%s",
                tool_name,
                safe_args,
                _truncate(context),
            )
            for key in required:
                value = args.get(key)
                if value is None or (isinstance(value, str) and not value.strip()):
                    example = examples.get(key)
                    message = f"{key} is required" + (
                        f" (e.g. {example!r})" if example is not None else ""
                    )
                    log.warning(
                        "%s: rejected call, missing %s; elapsed_ms=%.2f; args=%s",
                        tool_name,
                        key,
                        (time.perf_counter() - started) * 1000,
                        safe_args,
                    )
                    return json.dumps({"success": False, "error": message}, ensure_ascii=False)
            try:
                result = fn(args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — tool errors stay in-band
                log.exception(
                    "%s: handler raised; elapsed_ms=%.2f; error=%s",
                    tool_name,
                    (time.perf_counter() - started) * 1000,
                    exc,
                )
                return json.dumps(
                    {"success": False, "error": f"{tool_name} failed: {exc}"},
                    ensure_ascii=False,
                )
            if isinstance(result, str):
                log.info(
                    "%s: ok; elapsed_ms=%.2f; result=encoded_string",
                    tool_name,
                    (time.perf_counter() - started) * 1000,
                )
                return result
            log.info(
                "%s: ok; elapsed_ms=%.2f; result=%s",
                tool_name,
                (time.perf_counter() - started) * 1000,
                type(result).__name__,
            )
            return json.dumps({"success": True, "data": result}, ensure_ascii=False)

        setattr(
            wrapper,
            _SPEC_ATTR,
            {
                "name": tool_name,
                "toolset": toolset,
                "schema": schema,
                "requires_env": requires_env,
                "emoji": emoji,
            },
        )
        return wrapper

    return decorate


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(ctx: Any, module: Any) -> int:
    """Register every ``@tool`` defined in *module* with *ctx*.

    *module* may be a module object or its ``__name__`` (e.g. pass ``__name__``
    from the plugin's ``tools`` module). Returns the number of tools registered.
    """
    if isinstance(module, str):
        module = sys.modules[module]
    log = logging.getLogger(getattr(module, "__name__", "hermes_plugin_kit"))
    count = 0
    seen: set[str] = set()
    for _, obj in inspect.getmembers(module):
        spec = getattr(obj, _SPEC_ATTR, None)
        if not spec or spec["name"] in seen:
            continue
        seen.add(spec["name"])
        _register_tool(ctx, obj, spec)
        count += 1
    log.info(
        "hermes_plugin_kit: registered %d tool(s); names=%s",
        count,
        ",".join(sorted(seen)) or "<none>",
    )
    return count


def _register_tool(ctx: Any, handler: Callable, spec: dict[str, Any]) -> None:
    """Bind one kit-decorated tool to the current Hermes plugin context."""
    ctx.register_tool(
        name=spec["name"],
        toolset=spec["toolset"],
        schema=spec["schema"],
        handler=handler,
        requires_env=spec["requires_env"],
        description=spec["schema"]["description"],
        emoji=spec["emoji"],
    )


def register_plugin(
    ctx: Any,
    module: Any,
    skills: tuple[PluginSkill, ...] | list[PluginSkill] = (),
) -> RegistrationSummary:
    """Register decorated tools, hooks, and declared skills from *module*.

    Unlike the backward-compatible :func:`register_all`, this lifecycle-level
    entrypoint rejects distinct declarations that share a public name. Missing
    optional skills are warned and skipped; missing required skills fail fast.
    """
    if isinstance(module, str):
        module = sys.modules[module]
    log = logging.getLogger(getattr(module, "__name__", "hermes_plugin_kit"))

    tools: dict[str, Callable] = {}
    hooks: dict[str, Callable] = {}
    for _, obj in inspect.getmembers(module):
        tool_spec = getattr(obj, _SPEC_ATTR, None)
        if tool_spec:
            existing = tools.get(tool_spec["name"])
            if existing is not None and existing is not obj:
                raise ValueError(f"duplicate tool name: {tool_spec['name']}")
            tools[tool_spec["name"]] = obj

        hook_spec = getattr(obj, _HOOK_SPEC_ATTR, None)
        if hook_spec:
            existing = hooks.get(hook_spec["name"])
            if existing is not None and existing is not obj:
                raise ValueError(f"duplicate hook name: {hook_spec['name']}")
            hooks[hook_spec["name"]] = obj

    declared_skills: dict[str, PluginSkill] = {}
    for skill in skills:
        if not isinstance(skill, PluginSkill):
            raise TypeError("skills must contain plugin_skill() declarations")
        if skill.name in declared_skills:
            raise ValueError(f"duplicate skill name: {skill.name}")
        declared_skills[skill.name] = skill

    registered_tools: list[str] = []
    for name in sorted(tools):
        obj = tools[name]
        spec = getattr(obj, _SPEC_ATTR)
        _register_tool(ctx, obj, spec)
        registered_tools.append(name)

    registered_hooks: list[str] = []
    for name in sorted(hooks):
        ctx.register_hook(name, hooks[name])
        registered_hooks.append(name)

    registered_skills: list[str] = []
    skipped_skills: list[str] = []
    for name in sorted(declared_skills):
        skill = declared_skills[name]
        if not skill.path.is_file():
            if skill.optional:
                log.warning(
                    "hermes_plugin_kit: optional skill missing; name=%s; path=%s",
                    skill.name,
                    skill.path,
                )
                skipped_skills.append(name)
                continue
            raise FileNotFoundError(f"SKILL.md not found at {skill.path}")
        ctx.register_skill(
            name=skill.name,
            path=skill.path,
            description=skill.description,
        )
        registered_skills.append(name)

    summary = RegistrationSummary(
        tools=tuple(registered_tools),
        hooks=tuple(registered_hooks),
        skills=tuple(registered_skills),
        skipped_optional_skills=tuple(skipped_skills),
    )
    log.info(
        "hermes_plugin_kit: registered plugin lifecycle; tools=%s; hooks=%s; "
        "skills=%s; skipped_optional_skills=%s",
        ",".join(summary.tools) or "<none>",
        ",".join(summary.hooks) or "<none>",
        ",".join(summary.skills) or "<none>",
        ",".join(summary.skipped_optional_skills) or "<none>",
    )
    return summary
