# hermes-plugin-kit

> Lifecycle helpers for [hermes-agent](https://github.com/NousResearch/hermes-agent) plugins — convention-correct commands, tools, middleware, hooks, skills, validation, and safe logging, baked in.

[![test](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml/badge.svg)](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/hermes-plugin-kit)](https://pypi.org/project/hermes-plugin-kit/)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

`hermes-plugin-kit` is a tiny, dependency-free helper for authoring plugins for
[hermes-agent](https://github.com/NousResearch/hermes-agent). Decorate an
in-session slash command or terminal CLI subcommand with `@command`, a tool
with `@tool`, or a lifecycle callback with `@middleware` or `@hook`, then use
`register_plugin` to register commands, tools, middleware, hooks, plugin-owned
skills, and an optional Hermes context-engine instance together. Existing
tool-only plugins can keep using
`register_all` for backward compatibility, but new and migrated plugins should
use `register_plugin` so every surface and the lifecycle receipt share one
contract. The LLM-facing schema, argument validation, structured logging, and
the JSON result envelope are all generated for you — correctly, every time.

## Motivation

This began as a one-line fix. A hermes plugin had shipped its tool arguments at the
top level of the schema instead of under `parameters`; the model received a tool with
no arguments and couldn't call it until someone opened the plugin's source to find the
field names. The fix was trivial — but the same mistake was latent in every other
plugin that hand-rolls its schemas, envelopes, and logging.

Fixing them one at a time and hoping the next author remembers the rules doesn't scale.
So we pulled the conventions out into a single, reusable kit. Get them right once, here,
and every plugin that reaches for `@tool` inherits them — and the next person reading a
tool's logs can actually see what went wrong.

## Why it exists

Hermes turns each tool into an OpenAI-style function: `{"type": "function",
"function": {**schema, "name": ...}}`. That means a tool's arguments **must** live
under a `parameters` key. It's a small detail with an outsized failure mode, and
hand-written plugins keep tripping over the same things:

- **Empty `{}` arguments.** Put `properties` at the top level instead of under
  `parameters` and the model receives a tool with *no arguments* — it can't tell
  what to pass, and silently guesses wrong field names until it gives up.
- **`TypeError: unhashable type: 'slice'`.** Return a `dict` instead of a JSON
  string and the framework crashes downstream.
- **Silent failures.** A terse error with no log leaves operators staring at
  "invalid input" with no idea what the model actually sent.

These aren't exotic — they're the *default* mistakes when every plugin re-implements
the same boilerplate. `hermes-plugin-kit` makes them structurally impossible:

- **Schema convention** — arguments are always nested under `parameters`.
- **Self-documenting** — required argument names and examples are appended to the
  description, the one field a model reliably sees.
- **Validation + instructive errors** — a missing or blank required argument returns
  an error that *names the argument and its example*.
- **Explicit tool namespacing** — build names with `tool_name(namespace, verb, noun)`
  and reject Hermes agent-loop names such as `memory`.
- **Logging** — `DEBUG` when a tool is invoked, `WARNING` on rejected calls and
  exceptions (including tracebacks), and `INFO` on success with elapsed time and
  result mode. Arguments are truncated and nested secret-looking values are
  recursively redacted. `register_plugin` emits one exact lifecycle inventory;
  legacy `register_all` still logs its tool inventory.
- **Envelope + safety** — return a plain `dict` (or raise); the kit encodes the JSON
  string, catches exceptions, and always returns `str` from an `(args, **kwargs)`
  handler.
- **Host invocation** — call non-registry Hermes capabilities such as
  `send_message` without bypassing plugin guard and audit hooks.
- **Typed media delivery** — declare `MediaPayload` as `auto`, `voice`, or
  `document`; resolve task-local `origin` inside the kit; and receive a
  privacy-safe `MediaDeliveryResult` after the real Hermes-agent host send.
  These types encode Hermes-agent's `send_message` contract; they are not an
  OpenClaw compatibility layer.

## Who it's for

Anyone writing or maintaining a hermes-agent plugin who wants their tools to be
correct and debuggable without copy-pasting the same schema/envelope/logging
scaffolding into every file. It pairs naturally with the hermes plugin conventions
and adds nothing to your runtime footprint — pure standard library.

## Install

Install a published release from PyPI with [uv](https://docs.astral.sh/uv/) or pip:

```bash
uv add "hermes-plugin-kit>=0.7,<1"
# or: pip install "hermes-plugin-kit>=0.7,<1"
```

Consumers declare the narrowest truthful compatibility range in
`pyproject.toml`; adopting a newer kit API and raising that lower bound are one
change. Repository locks remain exact for reproducible local tests. Fleet
deployment independently selects one qualified wheel filename and SHA-256 for
every co-loaded plugin, so a movable branch is never a deployment identity.

```toml
dependencies = ["hermes-plugin-kit>=0.7,<1"]
```

## Usage

`tools.py`:

```python
from hermes_plugin_kit import tool, tool_name, register_plugin, str_arg, int_arg

@tool(
    toolset="messaging",
    namespace="discord",
    name=tool_name("discord", "read", "thread"),
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
    """Read recent messages from a Discord thread the bot can already access."""
    return {"messages": read_thread(args["thread_id_or_url"], args.get("limit", 100))}
```

`__init__.py`:

```python
from hermes_plugin_kit import register_plugin
from . import tools

def register(ctx):
    return register_plugin(ctx, tools)
```

That's it. `discord_read_thread` is registered with a `parameters`-wrapped
schema, a self-documenting description, required-argument validation, logging,
the JSON envelope, and the same exact registration receipt used by plugins with
hooks, commands, middleware, or skills.

Plugins that already own a Hermes function schema can adopt the same decorator
without rebuilding their schema from `params`:

```python
LEGACY_WRITE_SCHEMA = {
    "description": "Write one entry through the existing memory service.",
    "parameters": {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
        "additionalProperties": False,
    },
}

@tool(
    name="workspace_write_entry",
    toolset="memory-sync",
    schema=LEGACY_WRITE_SCHEMA,
    validate_required=False,
)
def workspace_write_entry(args, **kwargs):
    return legacy_service.write(args)
```

`schema` and `params` are mutually exclusive. The kit deep-copies and validates
a supplied schema, including its `parameters` shape and required-property
references. Required fields stay visible to the model. The default
`validate_required=True` keeps the kit's instructive missing-argument response;
set it to `False` only when an existing handler must retain its established
validation and error payload.

## Commands, middleware, hooks, and plugin skills

Use the lifecycle entrypoint when a plugin provides more than tools:

```python
import time
from pathlib import Path
from hermes_plugin_kit import (
    CommandType,
    MiddlewareKind,
    command,
    hook,
    middleware,
    plugin_skill,
    register_plugin,
)

@command("valdris-status", args_hint="<scope>")
def valdris_status(raw_args):
    """Show the current Valdris plugin status."""
    return build_status(raw_args)

def configure_valdris_cli(parser):
    parser.add_argument("--scope", default="all")

@command(
    "valdris",
    type=CommandType.CLI,
    help="Manage Valdris",
    setup_fn=configure_valdris_cli,
)
def valdris_cli(args):
    """Manage Valdris from the terminal."""
    return run_valdris_cli(scope=args.scope)

@middleware(MiddlewareKind.TOOL_REQUEST)
def normalize_tool_request(**kwargs):
    args = {**kwargs["args"]}
    args["workspace"] = normalize_workspace(args.get("workspace"))
    return {"args": args, "source": "valdris"}

@middleware(MiddlewareKind.TOOL_EXECUTION)
def measure_tool_execution(**kwargs):
    started = time.perf_counter()
    try:
        return kwargs["next_call"](kwargs["args"])
    finally:
        record_tool_latency(
            kwargs["tool_name"],
            time.perf_counter() - started,
        )

@hook("pre_llm_call")
def inject_context(**kwargs):
    return {"context": build_context(kwargs)}

SKILLS = (
    plugin_skill(
        "temporal-awareness",
        Path(__file__).with_name("SKILL.md"),
        "Calibrate responses against local time and message gaps.",
        optional=True,
    ),
)

def register(ctx):
    return register_plugin(ctx, __name__, skills=SKILLS)
```

For runtime-gated surfaces, pass only the active decorated declarations instead
of exposing a module full of inactive ones:

```python
def register(ctx):
    active = [inject_context]
    if authored_memory_enabled(ctx):
        active.append(workspace_write_entry)
    return register_plugin(
        ctx,
        active,
        skills=SKILLS,
        plugin_name="memory-sync",
        logger=logger,
    )
```

The second argument may be a module, a loaded module name, or an iterable of
decorated callables. Explicit `plugin_name` and `logger` values control the
single registration receipt; module registration keeps the existing manifest
and module-derived defaults. Duplicate detection and returned
`RegistrationSummary` inventories are identical for both declaration forms.

Hermes context engines use a singular native registration path:

```python
return register_plugin(
    ctx,
    (),
    context_engine=ContinuityEngine(config),
    plugin_name="continuity",
)
```

The kit preflights `ctx.register_context_engine`, the engine's non-empty
`name`, and the real `agent.context_engine.ContextEngine` type before mutating
any host registry. An affirmative host result is reported as `accepted`; older
hosts that return no result are truthfully reported as `declared/submitted`.
An explicit rejection—most commonly a second engine—fails registration before
tools, hooks, skills, or providers are registered.

Context-engine recovery operations are native engine tools. Declare their
schemas through `ContextEngine.get_tool_schemas()` and dispatch them through
`handle_tool_call()`. Do **not** duplicate them with `@tool`: ordinary plugin
registration would shadow Hermes' engine dispatch, which supplies the active
message context and other engine lifecycle state.

When a plugin exposes surfaces that must be enabled together, resolve a named
capability before calling `register_plugin` instead of making every deployment
copy the capability's members:

```python
from hermes_plugin_kit import resolve_capability_selection

selection = resolve_capability_selection(
    TOOLS_BY_NAME,
    enabled_capabilities=("image", "video"),
    capability_groups={
        "image": {"image_generate"},
        "video": {"video_generate", "video_status", "video_cancel"},
    },
)
return register_plugin(
    ctx,
    (TOOLS_BY_NAME[name] for name in selection.names),
    capabilities=selection.capabilities,
    plugin_name="media",
)
```

Capability selection and explicit-name selection are mutually exclusive.
Unknown capabilities, unknown explicit names, and capability groups that refer
to undeclared names fail before registration begins. The selected capability
names are included in the lifecycle receipt. `register_plugin` also preflights
the required context registrars and specialized providers before registering
any surface, so an unsupported host cannot leave a partially registered plugin.

`@command` requires a bare lowercase kebab-case name. Slash commands are the
backward-compatible default: the handler receives trailing command text
unchanged and may return `str | None` synchronously or asynchronously. The
optional `args_hint` is forwarded to Hermes for native command pickers.

Use `type=CommandType.CLI` (or `type="cli"`) for a terminal command such as
`hermes valdris`. Its synchronous handler receives the parsed
`argparse.Namespace`. `setup_fn` configures that command's argparse subparser;
omit it for a command with no command-specific arguments. `help` defaults to
the first line of the resolved description. CLI commands cannot use
`args_hint`, and slash commands cannot use `help` or `setup_fn`.

Command logs include only the command name, elapsed time, result type, and,
for slash commands, the argument character count. Raw slash text and parsed
CLI argument values are never logged.

`@middleware` changes runtime behavior rather than merely observing it. Request
middleware rewrites the effective payload before Hermes continues; execution
middleware wraps the actual tool or model call through the supplied
single-use `next_call`. The four current phases are:

- `MiddlewareKind.TOOL_REQUEST`: return `{"args": {...}}` to replace tool
  arguments before hooks, guardrails, approvals, and execution.
- `MiddlewareKind.TOOL_EXECUTION`: call `next_call(args)` to wrap the real tool
  execution and optionally transform its result.
- `MiddlewareKind.LLM_REQUEST`: return `{"request": {...}}` to replace provider
  request arguments before the model call.
- `MiddlewareKind.LLM_EXECUTION`: call `next_call(request)` to wrap the real
  model execution and optionally transform its result.

Middleware callbacks must be synchronous because Hermes does not await them.
Each execution callback must call `next_call` at most once. The decorator also
accepts a non-empty string kind for forward compatibility with future Hermes
phases. `register_plugin` rejects two callbacks for the same kind within one
plugin, which prevents registration order from silently deciding behavior.
Logs contain the kind, elapsed time, result type, and safe correlation IDs, but
never request payloads or exception messages.

`@hook` forwards Hermes keyword arguments and return values unchanged. It logs
only the hook name, elapsed time, result type, and supplied `session_id` or
`task_id`; callback payloads and exception messages are never logged. Exceptions
are re-raised so Hermes retains its normal per-plugin isolation behavior.

`plugin_skill` validates the bare skill name, `SKILL.md` path, and description.
Required missing skills fail registration; optional missing skills warn and are
reported in the returned `RegistrationSummary`. Hermes supplies the plugin
namespace, so a declared `temporal-awareness` skill from plugin
`temporal-awareness` resolves as `temporal-awareness:temporal-awareness`.

## Runtime configuration and registration receipts

Real Hermes `PluginManifest` objects do not carry profile runtime config. Use
the kit compatibility seam instead of reading `ctx.manifest.config` directly:

```python
import logging

from hermes_plugin_kit import configure_stderr_logging, load_plugin_config

logger = logging.getLogger("memory-sync")

def register(ctx):
    configure_stderr_logging(logger, env_var="MEMORY_SYNC_LOG_STDERR")
    config = load_plugin_config(ctx, "memory-sync")
    # Register the lifecycle-gated surface from config.
```

`load_plugin_config` accepts a non-empty `manifest.config` for tests and older
hosts. On current Hermes it reads `plugins.<name>` through
`load_config_readonly()` and returns a deep copy so plugin code cannot mutate
Hermes' cached configuration through nested values.

`configure_stderr_logging` installs one idempotent INFO handler only when its
operator-owned environment flag is enabled. This makes registration receipts
visible in container logs without forcing verbose plugin logging everywhere.
`register_plugin` emits exactly one stable INFO receipt through the public
`log_registration_summary(logger, plugin_name, summary)` helper. The receipt
uses the Hermes manifest name when available and lists the actual registered
command, tool, middleware, hook, and skill names, plus skipped optional skills.
Runtime-gated consumers should pass their active decorated declarations to
`register_plugin`; a truly custom registration path can call the same helper
with its own `RegistrationSummary` instead of inventing a second receipt
format.

## Tool names

Hermes uses one global tool registry, and the agent loop intercepts core names
before registry dispatch. Plugin tools should use an explicit domain namespace
and an action verb:

```python
name=tool_name("discord", "read", "thread")      # discord_read_thread
name=tool_name("workspace", "write", "diary")    # workspace_write_diary
name=tool_name("workspace", "patch", "text")     # workspace_patch_text
```

Do not register plugin tools with agent-loop names such as `memory`, `todo`,
`session_search`, or `delegate_task`. The kit also rejects the reserved
`memory_` prefix so plugin tools cannot be confused with Hermes' built-in
persistent memory tool.

## Argument specs

- `str_arg(description, *, required=False, example=None, enum=None, min_length=None, **extra)`
- `int_arg(description, *, required=False, example=None, minimum=None, maximum=None, **extra)`
- `bool_arg(description, *, required=False, example=None, **extra)`
- `arg(type, description, *, required=False, example=None, enum=None, **extra)` — generic

`required` and `example` are kit metadata (stripped from the emitted JSON Schema, used
for validation, error text, and the self-documenting description). Any other keyword
passes through verbatim into the JSON Schema for that property.

## Handler contract

A handler returns a `dict` (becomes the success `data`), or raises (becomes a tool
error), or returns a `str` as an escape hatch (treated as already-encoded JSON). It must
accept `(args, **kwargs)` — runtime keys like `task_id`/`session_id` arrive as kwargs.

## Session state helpers

The kit can read sessions and messages and append transcript rows through
Hermes' public `SessionDB` API. It imports Hermes only when a database is
opened, so the package keeps its zero-dependency runtime contract:

```python
from hermes_plugin_kit import (
    append_session_message,
    open_session_db,
    read_session,
    read_session_messages,
)

with open_session_db() as db:  # current Hermes profile's state.db
    session = read_session(db, session_id)
    messages = read_session_messages(db, session_id, limit=50, latest=True)
    row_id = append_session_message(db, session_id, "user", "Remember this")
```

`open_session_db(db_path)` constructs a Hermes `SessionDB` for that path and
closes it on exit. `open_session_db(db=existing_db)` borrows a caller-owned
handle and leaves it open. Supplying both is an error. Prefer the injected form
inside a running plugin when Hermes already owns the profile-scoped handle.

Opening a writable `SessionDB` can migrate its schema. Tests must therefore use
a generated database or a copy under temporary storage; never a developer's
live `~/.hermes/state.db`. Production helpers issue no raw SQL and delegate
ordering, pagination, structured message encoding, locking, and migration to
Hermes itself. An incompatible Hermes build raises
`SessionDBCompatibilityError` naming the missing contract.
Default-valued options added by newer Hermes releases are omitted when an
older public method signature does not accept them. Requesting a non-default
option that the running Hermes does not support still fails explicitly.

## Calling host-managed capabilities

Not every Hermes capability lives in `tools.registry`. In particular,
`send_message` is a host-managed runtime service, so calling
`registry.dispatch("send_message", ...)` from inside a plugin returns an unknown-tool
error. Use the kit's typed media seam instead:

```python
from hermes_plugin_kit import MediaPayload, MediaType, deliver_media

def deliver_voice_memo(path: str, **runtime_context):
    return deliver_media(
        MediaPayload(path, MediaType.VOICE),
        target="origin",
        **runtime_context,
    )
```

`MediaType.VOICE` accepts only `.ogg`/`.opus` and emits Hermes'
`[[audio_as_voice]]` directive. `MediaType.DOCUMENT` emits `[[as_document]]`;
`MediaType.AUTO` lets Hermes choose from the extension. `origin` resolves
through Hermes' task-local platform/chat/thread context inside the kit, so a
plugin never imports gateway internals or exposes raw group IDs to the model.
The returned `MediaDeliveryResult` carries success, media type, path, requested
route, a privacy-safe display route, spoiler state, and a redacted host result.

Telegram spoiler photos are available without patching Hermes core:

```python
deliver_media(
    MediaPayload("/opt/data/avatars/generated/reveal.png", spoiler=True),
    target="origin",
    **runtime_context,
)
```

`plugin_skill` reads and validates the referenced file immediately, then checks
it again during registration. Every required skill must contain closed YAML
frontmatter, a non-empty body, matching `name` and `description` values, and
well-shaped Hermes metadata. Optional missing files remain skippable; if an
optional file exists, it must satisfy the same contract.

```yaml
---
name: temporal-awareness
description: Calibrate responses against local time and message gaps.
platforms: [macos, linux]
metadata:
  hermes:
    tags: [Time, Context]
    requires_toolsets: [terminal]
---
```

The validator covers Hermes platform, conditional activation, config,
blueprint, environment-variable, and credential-file metadata shapes. Runtime
activation and setup behavior remain owned by Hermes Agent.

`plugin_skill` also accepts an optional `references_dir` for a companion
directory of reference files sibling to `SKILL.md` (Hermes' own convention
names these `references`, `templates`, `assets`, or `scripts`, but any
directory name is accepted) — as long as it's actually inside the skill's
own directory tree (any depth of subdirectory is fine; a path outside it,
including via a symlink, raises `ValueError` immediately, regardless of
`optional`). Without that check, `references_dir` would accept literally any
directory, and `plugin_reference_tool` (below) would then expose that entire
tree for reading. Existence, in contrast, follows the same required/optional
split as `SKILL.md`: a required (non-`optional`) skill whose `references_dir`
doesn't exist raises `NotADirectoryError` immediately from `plugin_skill`,
the directory counterpart to `SKILL.md`'s own `FileNotFoundError`. An
`optional` skill's `references_dir` existence isn't checked at declaration
time at all — it's carried as declared and checked once, during
`register_plugin`, which logs a warning and drops it if still missing
(checking it twice would silence that warning the second time):

```python
plugin_skill(
    "temporal-awareness",
    Path(__file__).with_name("SKILL.md"),
    "Calibrate responses against local time and message gaps.",
    references_dir=Path(__file__).with_name("references"),
)
```

**This is forward-compatible groundwork, not yet an effective capability.**
`register_plugin` only forwards `references_dir` to `ctx.register_skill` when
the host's own signature accepts that parameter (checked at registration
time via `inspect.signature`, so older hosts are never called with an
argument they don't understand). That check is a best-effort probe, not a
guarantee — a host reached through a generic `**kwargs` shape (a decorator
applied without `functools.wraps`, or a test double built with a bare
`Mock(spec=...)` instead of `create_autospec(...)`) can report acceptance it
doesn't actually have, so `register_plugin` also retries once without
`references_dir` if the host rejects it at call time, logging a warning
either way. As of this writing, no released Hermes Agent host reads or
serves plugin-skill companion files, so declaring `references_dir` today
does not make the directory agent-visible. Once a host adds support, plugins
that already declare `references_dir` start working with no further
kit-side change.

**`plugin_reference_tool` makes the directory agent-visible today, without
waiting on a host.** It builds an ordinary `@tool`-decorated function — no
`register_skill` involvement at all — that lists or reads files under a
skill's `references_dir`:

```python
reader = plugin_reference_tool(skill, toolset="temporal-awareness")
# tool name defaults to "<skill-name>_read_reference"; pass name=/description=
# to override either. Include `reader` in the plugin's own declarations
# passed to register_plugin, exactly like any other @tool function.
```

Called with no `file_path`, it returns every file under `references_dir`
(recursively, as relative POSIX paths). Called with `file_path` set to a
path relative to `references_dir`, it returns that file's content. It also
accepts the equivalent skill-relative `references/<path>` form commonly
authored in `SKILL.md` files. A
`file_path` that resolves outside `references_dir` — including through a
symlink, an absolute path, or a `../` chain — is rejected rather than
followed; a non-string `file_path` is rejected with a clean error instead of
an internal exception. `plugin_reference_tool` re-checks that
`references_dir` is scoped inside the skill's own directory even though
`plugin_skill` already enforces it, since `PluginSkill` is a public
dataclass a caller could construct directly, bypassing that factory. It is
not TOCTOU-safe against a `references_dir` writable by an untrusted process
at runtime — fine for the common case of a static directory shipped with
the plugin, insufficient if that assumption doesn't hold for a given
deployment.

## Subagents and specialized providers

Subagent lifecycle supervision is host-owned. Use the checked accessor instead
of importing delegation internals:

```python
from agent.subagent_lifecycle import SubagentLaunchRequest
from hermes_plugin_kit import get_subagent_lifecycle

service = get_subagent_lifecycle(ctx)
handle = service.launch(SubagentLaunchRequest(goal="Review this change."))
```

Memory, image-generation, and video-generation providers remain instances of
their Hermes ABCs. Pass them to `register_plugin`; the kit validates the common
identity seam and forwards each instance to the specialized context registry:

```python
return register_plugin(
    ctx,
    (),
    memory_providers=(MyMemoryProvider(),),
    image_gen_providers=(MyImageGenProvider(),),
    video_gen_providers=(MyVideoGenProvider(),),
)
```

Memory providers must run through Hermes' memory-provider discovery context.
Image and video providers run through the general `PluginContext`. The kit does
not decorate provider methods or replace the `MemoryProvider`,
`ImageGenProvider`, or `VideoGenProvider` contracts.

Context engines likewise remain instances of Hermes' `ContextEngine` ABC, but
are singular rather than a provider collection. Their native schemas and
`handle_tool_call` own recovery-tool dispatch.

The ordinary path remains Hermes' host-managed `send_message`. Because that
host contract does not currently expose Telegram's `has_spoiler`, only
`spoiler=True` uses the kit's narrow Telegram extension. The extension accepts
JPG, JPEG, PNG, and WebP photos, resolves the same Hermes current-chat/home
routes (including group topics), runs the normal Hermes `pre_tool_call` and
`post_tool_call` hooks, forwards `has_spoiler=True`, closes its one-shot Bot
client, and returns the same privacy-safe typed result. Voice, document,
non-Telegram, and unsupported-image requests are rejected rather than silently
losing spoiler intent. The Telegram token remains runtime-owned and is never a
model argument or result field.

Direct delivery and final response delivery are separate stages in Hermes. A
consumer that calls `deliver_media` must register the kit's matching Hermes
lifecycle hooks so a successful direct send cannot be followed by model-authored
text or a duplicate `MEDIA:` directive:

```python
from hermes_plugin_kit import (
    clear_media_delivery_state,
    transform_media_delivery_output,
)

def register(ctx):
    ctx.register_hook("transform_llm_output", transform_media_delivery_output)
    ctx.register_hook("on_session_end", clear_media_delivery_state)
```

Successful delivery arms a one-turn marker for the current Hermes session.
`transform_media_delivery_output` consumes it and returns Hermes' canonical
`NO_REPLY` response before the gateway sees the final text. Failed delivery does
not arm suppression, and `on_session_end` clears any unconsumed marker. These are
Hermes-agent lifecycle and response contracts; they are not OpenClaw shapes.

For non-media host calls, `invoke_host_tool` remains the lower-level seam.
`invoke_host_tool` resolves the supported direct host handler and wraps the nested
operation with Hermes `pre_tool_call` and `post_tool_call` hooks. A blocking hook
prevents the handler from running. If the guard API is unavailable, invocation is
refused rather than sending without policy checks. `send_message` is the currently
supported host tool; unknown names fail explicitly.

The upstream Hermes contract suite runs image and typed voice payloads through
the real `send_message` target parser, media extractor, and Telegram formatter.
It mocks only the final Bot API client and asserts that Hermes calls `send_photo`
and `send_voice` with the expected files, without separate text messages. It
also runs the kit-owned spoiler extension against Hermes' real config, session,
async bridge, and Telegram library shapes while mocking only Bot network calls.

## Agent skill

The repo owns a Hermes plugin authoring skill at
[`skills/hermes-plugins`](skills/hermes-plugins). It includes a kit API map,
surface checklist, and routed references to the official Hermes Agent docs.

To use this checkout as the canonical Codex skill:

```bash
ln -s "$(pwd)/skills/hermes-plugins" ~/.codex/skills/hermes-plugins
```

## Logging contract

The kit logs under the decorated handler's module logger, so each plugin can
control verbosity with normal Python logging configuration. Tool lifecycle logs
include:

- `DEBUG`: invocation with truncated, recursively redacted arguments and safe
  `session_id`/`task_id` context when supplied by Hermes.
- `WARNING`: required-argument rejection or a handler exception. Exceptions use
  `logger.exception`, preserving the traceback for runtime diagnosis.
- `INFO`: successful completion with `elapsed_ms` and whether the handler returned
  a dictionary-like result or an already-encoded string.
- `INFO`: a registration summary from `register_all`, including count and names.
- `INFO`: one stable lifecycle receipt from `register_plugin`, including the
  plugin name and actual command, tool, middleware, hook, skill, and skipped
  optional skill names, plus the context-engine name and registration state.

The kit never logs handler result payloads. Keys containing `token`, `secret`,
`password`, `passwd`, `api_key`, `apikey`, or `auth` are replaced with `***` at
any nesting depth before arguments are logged.

### Structured plugin lifecycle receipts

Plugins with asynchronous or multi-stage work can emit correlated, local JSON
receipts without logging full tool payloads:

```python
import logging

from hermes_plugin_kit import (
    ObservabilityEvent,
    credential_identity_hash,
    log_observability_event,
    new_correlation_id,
)

correlation_id = new_correlation_id()
log_observability_event(
    logging.getLogger(__name__),
    ObservabilityEvent(
        plugin="sirens",
        event="generation.retrieve",
        correlation_id=correlation_id,
        persona="DJ Doot",
        lane="dj-doot-k7",
        tool="siren_video_gen",
        request_id="request-123",
        provider="google",
        model="gemini-omni-flash-preview",
        credential_ref="secret/hermes-agent/google-api",
        credential_hash=credential_identity_hash(api_key),
        stage="provider_retrieve",
        status="failed",
        http_status=403,
        error_code="permission_denied",
        error_message="Provider access denied",
        elapsed_ms=1438.13,
        retry_classification="terminal",
        artifact_outcome="not_created",
    ),
)
```

The emitted mapping uses schema `hermes.plugin.observability.v1`. It supports
`persona`, `lane`, `tool`, `request_id`, `fingerprint`, `provider`, `model`,
`credential_ref`, `credential_hash`, `stage`, `status`, `http_status`,
`error_code`, `error_message`, `elapsed_ms`, `retry_classification`, and
`artifact_outcome`, plus bounded `attributes` for plugin-specific safe metadata.
Null fields are omitted. Failed statuses default to `WARNING`; other statuses
default to `INFO`, and callers may explicitly select a log level.

Receipts go only through the supplied Python logger—there is no exporter or
outbound telemetry. Secret-looking nested attribute keys and common inline
credential forms are forcibly redacted, strings and collections are bounded,
and control characters are escaped. `credential_identity_hash` accepts secret
material only to calculate a domain-separated, truncated SHA-256 identity; the
event must contain the returned identity or a safe secret reference, never the
credential itself.

## Agent skills

Repository-owned skills are consumable directly from [`skills/`](skills). To
make the Agent Plugins migration skill available to Codex while keeping this
repository as the source of truth:

```bash
ln -s "$(pwd)/skills/migrate-agent-plugin" ~/.codex/skills/migrate-agent-plugin
```

Remove or rename an existing destination before creating the link. The skill
includes its migration guide, client-extension rules, and validation checklist,
so the linked directory is self-contained.

## Development

Uses [uv](https://docs.astral.sh/uv/) and
[Just](https://github.com/casey/just). On macOS, install both with
`brew install uv just`; otherwise install them using their platform-specific
instructions.

```bash
just install      # uv sync — create/sync the dev environment
just test         # uv run python -m unittest discover -s tests
just test-one tests.test_kit.SchemaConventionTests
just test-release # release intent, artifact identity, and workflow contracts
just test-contract # real upstream Hermes contract
just build        # uv build — wheel + sdist
just check-dist   # validate wheel/sdist metadata with twine
```

CI pins every Action to an immutable commit and runs the unit, package metadata,
and real-Hermes contract lanes with read-only repository permissions.

## Releases

Merges to `main` use Python Semantic Release 10.6.x and Conventional Commits:
`fix` produces a patch, `feat` produces a minor, and `!` or a
`BREAKING CHANGE:` footer produces a major. Documentation, test, CI, and chore
commits do not release by themselves; an invalid commit in release history
fails closed. Major releases are published but their receipt is always
`manual_migration_required`, never an automatic promotion candidate.

The workflow first creates the version/CHANGELOG commit and tag locally, then
bundles that exact final source before executing tests. Fresh, separate jobs
restore the bundle for unit/public tests, the mutable upstream-Hermes contract,
and the one artifact build. The build job is gated on both test jobs and never
checks out or executes Hermes code. Every lane verifies the release SHA, tag,
parent, and clean tracked source before continuing.

The build produces the wheel and sdist once, validates their metadata, and
writes a prepublication manifest containing their filenames, sizes, and SHA-256
values. Only then may the protected source-promotion job atomically push that
tested commit and tag. A separate `pypi` environment publishes the uploaded
artifacts through OIDC Trusted Publishing; no password or API token is used.
After publication, the workflow downloads and hashes the registry files and
turns the manifest into the final receipt by adding each verified direct
`https://files.pythonhosted.org/` URL. The immutable GitHub Release uploads that
final receipt only after PyPI verification. A failed publish is retried from
the retained workflow artifact and must not rebuild it.

Release automation is deliberately disarmed unless the repository variable
`SEMANTIC_RELEASE_ENABLED` is exactly `true`. Set it only after all activation
prerequisites have been reviewed:

- a pending or existing PyPI Trusted Publisher is configured for
  `offendingcommit/hermes-plugin-kit`, workflow `release.yml`, and environment
  `pypi`;
- the protected GitHub environment is named exactly `pypi`;
- GitHub immutable releases are enabled for the repository; the workflow
  verifies the repository control with Administration-read permission after a
  release intent is materialized but before source promotion, and verifies
  `isImmutable` after publication. Non-releasing commits never enter a
  protected environment;
- a protected environment named exactly `source-promotion` contains variable
  `SOURCE_PROMOTION_APP_CLIENT_ID` and secret
  `SOURCE_PROMOTION_APP_PRIVATE_KEY` for a dedicated GitHub App installed only
  on this repository. The App has repository Administration read and Contents
  write permissions; no PAT is used;
- the `main` ruleset requires the ordinary test workflow and names that
  dedicated GitHub App as its sole source-promotion bypass actor. Generic
  Actions credentials and the default `GITHUB_TOKEN` must not bypass it;
- the existing `0.7.0` source baseline has a reviewed immutable `v0.7.0` tag.

This change does not create or mutate any of those external controls. At review
time immutable releases were disabled, the required environments/ruleset/App
were not configured, and the PyPI project did not yet exist; a pending Trusted
Publisher supports that first OIDC publication. Keep
`SEMANTIC_RELEASE_ENABLED` unset until the full checklist is configured and a
generic-token rejection plus dedicated-App promotion have been exercised in an
isolated validation. A missing switch, control, baseline tag, test, build,
metadata check, source identity, or artifact hash stops before publication. The
final release receipt is the discoverable boundary for downstream
qualification; polling and recovery from a missed notification belong to that
downstream system.

## License

MIT
