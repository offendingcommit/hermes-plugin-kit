# hermes-plugin-kit

Convention-correct tool registration for [hermes-agent](https://github.com/NousResearch/hermes-agent) plugins.

Reach for `@tool` + `register_all` and every hermes tool convention is applied for
you — so the bugs that bite hand-written plugins can't recur.

## Why

Hermes builds each LLM tool as `{"type": "function", "function": {**schema, "name": ...}}`,
so a tool's arguments must live under a `parameters` key. Flatten `properties` to the
top level and the model receives **empty `{}` arguments** — it can't tell what to pass.
Plugins also have to return JSON strings (never dicts), accept `(args, **kwargs)`, never
raise, and ideally log and self-document. That's a lot to get right by hand, every time.

This kit bakes all of it in:

- **Schema convention** — arguments nested under `parameters`, always.
- **Self-documenting** — required arg names + examples appended to the description
  (the one field a model always sees).
- **Validation + instructive errors** — a missing/blank required arg returns an error
  that names the arg and its example.
- **Logging** — `WARNING` on a rejected call (args truncated, secret-looking values
  redacted), `INFO` on success, under your plugin's logger namespace.
- **Envelope + safety** — return a plain `dict` (or raise); the kit encodes the JSON
  string, catches exceptions, and always returns `str`.

## Install

```bash
pip install hermes-plugin-kit
# or from source:
pip install git+https://github.com/offendingcommit/hermes-plugin-kit.git
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
from . import tools

def register(ctx):
    register_all(ctx, tools.__name__)
```

That's it. `discord_read_thread` is registered with a `parameters`-wrapped schema, a
self-documenting description, required-arg validation, logging, and the JSON envelope —
none of which you had to write.

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

## Validation

```bash
python3 -m unittest discover -s tests
```
