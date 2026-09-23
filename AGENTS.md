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
- Consumer deployments must pin this package's private OCI bundle/receipt
  digests and verified wheel SHA-256, never a moving branch or registry tag.
  Source-based development pins an immutable commit. Profiles that install
  multiple plugins into one Python environment must use one qualified kit
  revision for every consumer.
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

The source repository stays public; release wheels, sdists, and receipts stay
private in `ghcr.io/offendingcommit/hermes-plugin-kit`. Source visibility never
authorizes public artifact publication. Public-repository Actions artifacts
are not private distribution: upload only public source/test evidence there.
GitHub Releases are immutable metadata only, with private digest references
and zero assets, including receipts.

Package privacy is independent of source access. Keep the GHCR package
unlinked, with its own explicit ACL; never grant this public repository Actions
access or add `org.opencontainers.image.source`. Do not use the built-in
`GITHUB_TOKEN` for package access: associating a private package with a public
repository can expose it to fork workflows. Registry steps use step-scoped
`GH_TOKEN` from protected environment secret `PRIVATE_ARTIFACTS_TOKEN`; writers
use the main-only `private-artifacts` environment, and source-promotion fetch
uses a read-capable secret in its own main-only environment.

Require a positive package API `visibility=private` check before any sensitive
upload. An absent package may receive metadata-only bootstrap content, then
must be rechecked. API errors and public/internal visibility fail closed.
Use ORAS with stdin/temporary authentication; preserve deterministic OCI
identities, digest-pinned retrieval, exact tested bytes, both test gates,
metadata checks, and downloaded-byte verification. Discovery tags cannot
replace digest identity; retries must refuse conflicting existing tags.

Source authentication uses `SOURCE_PROMOTION_TOKEN` from the main-only
`source-promotion` environment. A suitable PAT or existing GitHub credential
is valid; a particular token-minting mechanism is not a release invariant.
Keep the Administration-read immutability check, tested-source identity,
atomic guarded push, and immutable metadata-only GitHub Release control intact.
Never expose the promotion credential to source-testing/build or registry
steps, silently fall back to the default Actions token, or change branch-policy
bypasses as part of credential wiring. Environment restrictions narrow token
availability, not underlying permissions.

Keep `SEMANTIC_RELEASE_ENABLED=false` until the private cutover is reviewed.
The tagged `v0.9.0` publication failed before publishing: never retag, rebuild,
or retry its old PyPI workflow. The next reviewed normal release is the cutover.
