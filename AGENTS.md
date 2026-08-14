# hermes-plugin-kit

Convention-correct helper library for registering `hermes-agent` plugin
commands, tools, middleware, hooks, and skills.
This repository is an installable Python package, not a path-loaded runtime
plugin.

## Working Rules

- Keep `@tool`, slash-default `@command`, and `register_all` backward
  compatible. Use `@command(type="cli")` for terminal subcommands and
  `@command(type="slash")` for explicit in-session commands; use
  `@middleware`, `@hook`, `plugin_skill`, and `register_plugin` for full
  plugin lifecycle registration.
- Use `load_plugin_config` for effective `plugins.<name>` runtime settings;
  current Hermes `PluginManifest` objects do not expose profile config. Use
  `configure_stderr_logging` for operator-gated registration receipts instead
  of rebuilding per-plugin stderr handlers.
- Keep lifecycle registration receipts centralized in
  `log_registration_summary`; preserve its stable field order and actual
  command, tool, middleware, hook, skill, and skipped optional skill names.
  `register_plugin` must emit exactly one receipt through that helper.
- Use `@tool(schema=...)` when a consumer already owns a valid Hermes function
  schema; do not translate it through a second argument-spec format. Keep
  `schema` and `params` exclusive, deep-copy supplied schemas, and preserve
  schema-required fields even when `validate_required=False` delegates
  missing-argument errors to a legacy handler.
- Runtime-gated consumers should pass their active decorated callables to
  `register_plugin` with an explicit receipt identity. Iterable registration
  must retain module registration's duplicate checks, deterministic ordering,
  skills, and `RegistrationSummary` contract.
- Use `resolve_capability_selection` when several runtime-gated surfaces form
  one authorization unit. Capability membership belongs to the plugin, not
  deployment configuration; explicit-name selection remains mutually exclusive
  for narrow legacy surfaces. Registration preflight must finish before the
  first `ctx.register_*` mutation, and receipts must name selected capabilities.
- Consumer plugins must pin this package to an immutable commit, not a moving
  branch. Profiles that install multiple plugins into one Python environment
  must keep every consumer on the same kit revision.
- Use `invoke_host_tool` for host-managed capabilities such as `send_message`;
  do not assume every Hermes capability is registered in `tools.registry`.
  Nested host calls must remain visible to `pre_tool_call` and `post_tool_call`.
- Keep host invocation grounded in the real Hermes contract suite. For media
  delivery, exercise target parsing and platform formatting and mock only the
  final network client rather than replacing the host handler.
- Plugins must use `MediaPayload` + `deliver_media` for attachments. The kit
  owns Hermes media directives, task-local `origin` resolution, route redaction,
  the typed result, successful-send final-response suppression, and the narrow
  Telegram `spoiler=True` photo extension. Spoiler delivery must retain Hermes
  pre/post-tool hooks, route privacy, topic forwarding, and explicit Bot client
  shutdown; normal media must remain on host-managed `send_message`. Consumers
  must register `transform_media_delivery_output` as Hermes'
  `transform_llm_output` hook and `clear_media_delivery_state` as
  `on_session_end`; they must not recreate those contracts or substitute
  OpenClaw response shapes.
- Use `tool_name(namespace, verb, noun)` for new tools and prefer explicit
  verbs such as `read`, `write`, and `patch`. Do not use Hermes agent-loop
  names (`memory`, `todo`, `session_search`, `delegate_task`) as plugin tools.
- Preserve the Hermes tool schema convention: arguments live under
  `function.parameters`, never as flattened top-level schema fields.
- Tool handlers must accept `(args, **kwargs)` and return JSON-compatible
  dictionaries unless deliberately returning an already-encoded string.
- Keep validation errors instructive for model-facing callers, including the
  missing argument name and example when available.
- Keep stateful Hermes provider ABCs as provider instances: register memory,
  image-generation, and video-generation providers through their specialized
  contexts instead of decorating provider methods as general plugin surfaces.
- Register at most one real Hermes `ContextEngine` through `register_plugin`.
  Finish every registrar, identity, type, declaration, and provider preflight
  before submitting it or mutating another host registry. Keep engine schemas
  and recovery dispatch on `get_tool_schemas` / `handle_tool_call`; do not
  duplicate native engine tools through `@tool`.
- Redact secret-looking values in logs and avoid logging full untrusted payloads.
- Use `uv` and the `justfile` for local development:
  `just install`, `just test`,
  `just test-one tests.test_kit.SchemaConventionTests`, and `just build`.

## Release Notes

When changing conventions or exported helpers, update `README.md` examples and
tests together so consuming Hermes plugins have a reliable migration path.
Keep `skills/hermes-plugins/references/plugin-kit.md` aligned with public API
and contract changes so the repo-owned authoring skill does not teach stale
behavior.
