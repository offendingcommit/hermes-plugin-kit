# Official Hermes Agent References

Use these upstream references for Hermes runtime behavior. The official docs
describe the direct `PluginContext` API; `hermes-plugin-kit` is a consumer-side
adapter that removes repeated boilerplate around part of that API.

## Primary Documentation

- [Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
  covers manifests, schemas, handlers, `register(ctx)`, hooks, skills, testing,
  packaging, and installation for general plugins.
- [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
  covers extension types, discovery sources, enablement, capability routing,
  and the current `PluginContext` surface.
- [Event Hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)
  covers valid hook names, callback shapes, blocking and context injection,
  lifecycle timing, shell hooks, and gateway hooks.
- [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
  covers skill layout, namespacing, external directories, platform controls,
  and bundles.
- [CLI Commands Reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)
  covers `hermes plugins`, `hermes tools`, update, and runtime inspection commands.
- [Toolsets Reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference)
  explains how tool bundles control availability per platform, session, and task.
- [Adding Tools](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-tools)
  is for built-in core tools. Use it only when the request explicitly targets
  Hermes Agent core rather than a standalone plugin.
- [Hermes Agent repository guidance](https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md)
  records upstream contribution policy and the boundary between core and plugins.

When a website page and `main` source differ, inspect the official repository
page linked from the documentation and the exact revision the target runtime pins.

## Choose The Correct Extension Type

General plugins cover tools, lifecycle hooks, session slash commands, CLI
subcommands, bundled skills, message injection, and host-owned LLM access.

Specialized interfaces have separate discovery and activation contracts:

- Platform adapters for gateway channels.
- Memory providers.
- Context engines.
- Model providers.
- Image, video, browser, web-search, and secret-source providers.
- Desktop and dashboard extensions.

Do not route a specialized provider through `register_tool` merely because the
kit makes tools convenient.

## Runtime Authority Order

1. The deployed or pinned Hermes Agent source and effective configuration.
2. Official docs for the matching version.
3. `hermes-plugin-kit` contract tests against that source.
4. The plugin's checked-in docs and local fake-context tests.

For mutable runtime questions, verify plugin enablement, the loaded revision,
effective toolsets, platform-specific allowlists, and the actual tool inventory.
Discovery alone does not prove a plugin is enabled or exposed on a platform.

## Source Seams To Verify

From a Hermes Agent checkout, locate contracts instead of guessing paths from
memory:

```bash
rg -n "class PluginContext|def register_tool|def register_hook|def register_command|def register_skill" hermes_cli
rg -n "VALID_HOOKS|VALID_MIDDLEWARE|register_middleware" hermes_cli agent
rg -n "class ToolRegistry|def dispatch|def get_definitions" tools
rg -n "send_message|MEDIA:|audio_as_voice|as_document" tools gateway hermes_cli
```

The common high-value seams are the plugin context/manager, middleware
dispatcher, tool registry, agent-loop hook call sites, and gateway message
formatters. Paths move faster than the concepts, so search the pinned checkout.

## Reconcile Direct Docs With The Kit

Official examples usually hand-write schemas, catch exceptions, and return JSON
strings. With `@tool`, the kit builds the schema, validates required arguments,
wraps exceptions, logs safely, and encodes dict results. Preserve the upstream
wire contract without duplicating that boilerplate inside the handler.

Official `ctx.register_*` methods remain the source contract. The kit's
`tests/test_hermes_contract.py` should bind its generated calls to the real
signatures and exercise the real registry or gateway seam while mocking only
the final network client.
