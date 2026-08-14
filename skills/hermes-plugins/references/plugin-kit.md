# hermes-plugin-kit Reference

Use this reference when a general Hermes plugin consumes or changes
`hermes-plugin-kit`. The kit is an installable helper package, not a
path-loaded Hermes plugin and not an upstream Hermes API.

## Canonical Local Sources

- Public guide and examples: [`README.md`](../../../README.md)
- Maintainer rules: [`AGENTS.md`](../../../AGENTS.md)
- Exported API and behavior: [`hermes_plugin_kit/__init__.py`](../../../hermes_plugin_kit/__init__.py)
- Unit contracts: [`tests/test_kit.py`](../../../tests/test_kit.py)
- Structured observability contracts:
  [`tests/test_observability.py`](../../../tests/test_observability.py)
- Real Hermes compatibility contracts:
  [`tests/test_hermes_contract.py`](../../../tests/test_hermes_contract.py)
- Package and Python requirements: [`pyproject.toml`](../../../pyproject.toml)

Read the source and tests when exact signatures matter. This page is routing
guidance, not a second implementation specification.

## Surface Map

| Plugin need | Kit API | Registration | Important contract |
| --- | --- | --- | --- |
| LLM tool | `@tool`, `tool_name`, argument helpers | `register_all` or `register_plugin` | Handler accepts `(args, **kwargs)` and may return a dict, raise, or deliberately return an encoded string. |
| Session slash command | `@command` | `register_plugin` | Name is bare lowercase kebab-case; handler receives raw trailing text and may be sync or async. |
| Request or execution middleware | `@middleware`, `MiddlewareKind` | `register_plugin` | Callback is synchronous; request phases replace payloads, execution phases call single-use `next_call`. |
| Lifecycle hook | `@hook` | `register_plugin` | Hermes kwargs and return values pass through; exceptions are re-raised for Hermes isolation. |
| Plugin-owned skill | `plugin_skill` | `register_plugin(..., skills=...)` | Hermes adds the plugin namespace; missing required skills fail, optional skills warn and skip. |
| Context engine | Hermes `ContextEngine` instance | `register_plugin(..., context_engine=...)` | Singular native engine registration; schemas and recovery dispatch stay in `get_tool_schemas()` / `handle_tool_call()`, never duplicated with `@tool`. |
| Host-managed call | `invoke_host_tool` | None | Use for supported non-registry capabilities such as `send_message`; pre/post-tool hooks remain active. |
| Local media delivery | `MediaPayload`, `MediaType`, `deliver_media` | Consumer registers suppression hooks | File must be absolute, present, and non-empty; `origin` resolves from task-local Hermes context. |
| Correlated lifecycle receipt | `ObservabilityEvent`, `log_observability_event`, `new_correlation_id`, `credential_identity_hash` | None | Emits bounded, redacted JSON through the supplied local logger; consumers provide domain stages and never place credentials in event fields. |

`RegistrationSummary` reports commands, tools, middleware, hooks, skills,
skipped optional skills, and the declared context engine plus its truthful host
registration state.

## What The Kit Owns

- Tool naming and the `function.parameters` schema shape.
- Required-argument metadata, validation, and model-facing error text.
- JSON success and error envelopes around kit-decorated tool handlers.
- Redacted lifecycle logging and registration inventories.
- A versioned, correlated lifecycle-event shape with bounded local JSON logging.
- Duplicate lifecycle declaration checks before registration.
- Guarded host invocation for supported host-managed tools.
- Typed Hermes media directives, origin resolution, privacy-safe results, and
  the narrow Telegram spoiler-photo extension.

## What It Does Not Own

Use the direct Hermes API or the specialized upstream plugin interface for:

- `ctx.register_cli_command`, `ctx.dispatch_tool`, `ctx.inject_message`, or
  `ctx.llm.complete*`.
- Gateway platform adapters.
- Memory, model, image, video, browser, web-search, secret source, desktop, or
  dashboard provider interfaces. The one supported context-engine seam is the
  singular typed `register_plugin` adapter; the kit does not abstract engine
  policy, schemas, or tool dispatch.
- Plugin discovery, enablement, platform toolset selection, or core agent-loop behavior.

Do not add a kit abstraction merely to hide one direct `PluginContext` call.
Expand the kit when there is a repeated convention or a failure mode worth
making structurally impossible.

## Consumer Migration

1. Prove the current registration and dependency state from code and
   `pyproject.toml`, not README claims.
2. Add `hermes-plugin-kit` through the consumer's package workflow, then refresh
   its lockfile with the repo-native install command.
3. Wrap existing handlers without changing business behavior.
4. Use `register_all` for tool-only migration or `register_plugin` when adopting
   commands, middleware, hooks, or plugin skills.
5. Keep `plugin.yaml`, auth gates, toolsets, docs, and registration tests in parity.
6. Run the consumer suite and a real Hermes contract test when runtime APIs matter.

For a context-engine consumer, pass exactly one real `ContextEngine` instance.
Keep recovery operations on the native engine schema/handler path; decorating
the same operations with `@tool` shadows the active-context-aware dispatch.

## Failure Traps

- A top-level JSON Schema `properties` field makes the model see empty arguments.
- Hermes tool names share a global registry. Use `tool_name(namespace, verb, noun)`
  and avoid core agent-loop names and the reserved `memory_` prefix.
- Plain Hermes handlers return encoded strings. Kit-decorated handlers may return
  dictionaries because the wrapper encodes them. Do not double-encode by habit.
- `send_message` is host-managed, not guaranteed to be registry-backed.
- A successful `deliver_media` call needs
  `transform_media_delivery_output` registered as `transform_llm_output` and
  `clear_media_delivery_state` registered as `on_session_end`, or the gateway
  can emit duplicate final output.
- The kit follows the real Hermes contract tests. A fake context alone can hide
  signature drift.

## Validation

In this repository:

```bash
make install
make test
make build
```

In a consumer, run its native suite plus registration and manifest-parity tests.
Point contract tests at a real checkout with `HERMES_AGENT_PATH` when automatic
checkout discovery is not appropriate.
