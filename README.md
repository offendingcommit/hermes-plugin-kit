# hermes-plugin-kit

> Lifecycle helpers for [hermes-agent](https://github.com/NousResearch/hermes-agent) plugins — convention-correct tools, hooks, skills, validation, and safe logging, baked in.

[![test](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml/badge.svg)](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

`hermes-plugin-kit` is a tiny, dependency-free helper for authoring plugins for
[hermes-agent](https://github.com/NousResearch/hermes-agent). Decorate a tool
with `@tool` or a lifecycle callback with `@hook`, then use `register_plugin` to
register tools, hooks, and plugin-owned skills together. Existing tool-only
plugins can keep using `register_all`; the LLM-facing schema,
argument validation, structured logging, and the JSON result envelope are all
generated for you — correctly, every time.

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
  recursively redacted. `register_all` also logs the registered tool inventory.
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

The package is consumed straight from Git (works great with [uv](https://docs.astral.sh/uv/)):

```bash
uv add git+https://github.com/offendingcommit/hermes-plugin-kit.git
# or
pip install git+https://github.com/offendingcommit/hermes-plugin-kit.git
```

With uv, pin it as a source in your plugin's `pyproject.toml`:

```toml
dependencies = ["hermes-plugin-kit"]

[tool.uv.sources]
hermes-plugin-kit = { git = "https://github.com/offendingcommit/hermes-plugin-kit.git", branch = "main" }
```

## Usage

`tools.py`:

```python
from hermes_plugin_kit import tool, tool_name, register_all, str_arg, int_arg

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
from hermes_plugin_kit import register_all
from . import tools

def register(ctx):
    register_all(ctx, tools.__name__)
```

That's it. `discord_read_thread` is registered with a `parameters`-wrapped schema, a
self-documenting description, required-argument validation, logging, and the JSON
envelope — none of which you had to write.

## Hooks and plugin skills

Use the lifecycle entrypoint when a plugin provides more than tools:

```python
from pathlib import Path
from hermes_plugin_kit import hook, plugin_skill, register_plugin

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

`@hook` forwards Hermes keyword arguments and return values unchanged. It logs
only the hook name, elapsed time, result type, and supplied `session_id` or
`task_id`; callback payloads and exception messages are never logged. Exceptions
are re-raised so Hermes retains its normal per-plugin isolation behavior.

`plugin_skill` validates the bare skill name, `SKILL.md` path, and description.
Required missing skills fail registration; optional missing skills warn and are
reported in the returned `RegistrationSummary`. Hermes supplies the plugin
namespace, so a declared `temporal-awareness` skill from plugin
`temporal-awareness` resolves as `temporal-awareness:temporal-awareness`.

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

The kit never logs handler result payloads. Keys containing `token`, `secret`,
`password`, `passwd`, `api_key`, `apikey`, or `auth` are replaced with `***` at
any nesting depth before arguments are logged.

## Development

Uses [uv](https://docs.astral.sh/uv/). Install it with `brew install uv` (macOS) or
`curl -LsSf https://astral.sh/uv/install.sh | sh`.

```bash
make install     # uv sync — create/sync the dev environment
make test        # uv run python -m unittest discover -s tests
make test-one T=tests.test_kit.SchemaConventionTests
make build       # uv build — wheel + sdist
```

CI runs `make test` on `actions/checkout@v6` + `astral-sh/setup-uv@v8.2.0` (Python 3.11).

## License

MIT
