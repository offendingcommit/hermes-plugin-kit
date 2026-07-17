# hermes-plugin-kit

Convention-correct helper library for registering `hermes-agent` plugin tools,
hooks, and skills.
This repository is an installable Python package, not a path-loaded runtime
plugin.

## Working Rules

- Keep `@tool` and `register_all` backward compatible. Use `@hook`,
  `plugin_skill`, and `register_plugin` for full plugin lifecycle registration.
- Use `invoke_host_tool` for host-managed capabilities such as `send_message`;
  do not assume every Hermes capability is registered in `tools.registry`.
  Nested host calls must remain visible to `pre_tool_call` and `post_tool_call`.
- Keep host invocation grounded in the real Hermes contract suite. For media
  delivery, exercise target parsing and platform formatting and mock only the
  final network client rather than replacing the host handler.
- Plugins must use `MediaPayload` + `deliver_media` for attachments. The kit
  owns Hermes media directives, task-local `origin` resolution, route redaction,
  and the typed result; consumers must not recreate those contracts.
- Use `tool_name(namespace, verb, noun)` for new tools and prefer explicit
  verbs such as `read`, `write`, and `patch`. Do not use Hermes agent-loop
  names (`memory`, `todo`, `session_search`, `delegate_task`) as plugin tools.
- Preserve the Hermes tool schema convention: arguments live under
  `function.parameters`, never as flattened top-level schema fields.
- Tool handlers must accept `(args, **kwargs)` and return JSON-compatible
  dictionaries unless deliberately returning an already-encoded string.
- Keep validation errors instructive for model-facing callers, including the
  missing argument name and example when available.
- Redact secret-looking values in logs and avoid logging full untrusted payloads.
- Use `uv` and the Makefile for local development:
  `make install`, `make test`, `make test-one T=tests.test_kit.SchemaConventionTests`,
  and `make build`.

## Release Notes

When changing conventions or exported helpers, update `README.md` examples and
tests together so consuming Hermes plugins have a reliable migration path.
