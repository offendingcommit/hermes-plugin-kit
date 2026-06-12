# hermes-plugin-kit

> A `@tool` decorator for [hermes-agent](https://github.com/NousResearch/hermes-agent) plugins — convention-correct LLM tool schemas, validation, logging, and the JSON envelope, baked in.

[![test](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml/badge.svg)](https://github.com/offendingcommit/hermes-plugin-kit/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.13%2B-blue)

`hermes-plugin-kit` is a tiny, dependency-free helper for authoring **tools** in
[hermes-agent](https://github.com/NousResearch/hermes-agent) plugins. Decorate a
handler with `@tool`, register it with `register_all`, and the LLM-facing schema,
argument validation, structured logging, and the JSON result envelope are all
generated for you — correctly, every time.

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
- **Logging** — `WARNING` on a rejected call (arguments truncated, secret-looking
  values redacted), `INFO` on success, under your plugin's own logger.
- **Envelope + safety** — return a plain `dict` (or raise); the kit encodes the JSON
  string, catches exceptions, and always returns `str` from an `(args, **kwargs)`
  handler.

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

## Development

Uses [uv](https://docs.astral.sh/uv/). Install it with `brew install uv` (macOS) or
`curl -LsSf https://astral.sh/uv/install.sh | sh`.

```bash
make install     # uv sync — create/sync the dev environment
make test        # uv run python -m unittest discover -s tests
make test-one T=tests.test_kit.SchemaConventionTests
make build       # uv build — wheel + sdist
```

CI runs `make test` on `actions/checkout@v6` + `astral-sh/setup-uv@v8.2.0` (Python 3.13).

## License

MIT
