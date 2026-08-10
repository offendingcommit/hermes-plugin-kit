# Hermes Plugin Surface Checklist

Use this checklist when adding, renaming, removing, reviewing, or debugging Hermes plugin exposure.

## Required Inventory

- `__init__.py`: `register(ctx)`, direct `ctx.register_*` calls,
  `register_all`, `register_plugin`, and imported decorated declarations.
- `plugin.yaml`: supported `provides_*` fields, `requires_env`, description,
  version, kind, and plugin metadata.
- `tools.py` or equivalent: handler behavior and API/client calls.
- `schemas.py` or kit declarations: JSON schema names, descriptions, required
  fields, enums, defaults, toolsets, and environment gates.
- Lifecycle modules: `@command`, `@middleware`, `@hook`, direct registration,
  callback contracts, and failure tolerance.
- `skills/**/SKILL.md`: bundled Hermes skills that may be conditionally registered.
- `tests/**`: registration counts, manifest parity, schema/tool definition
  parity, lifecycle signatures, auth requirements, handler behavior, and privacy boundaries.
- `AGENTS.md` or repo docs: public surface inventory and validation commands.

## Surface Change Rules

- Keep toolset names consistent with repo preference. In `hermes-plugin-dsl`, new tools stay in the `dsl` toolset unless the user explicitly asks otherwise.
- When the repo uses `hermes-plugin-kit`, prefer `register_all` for tool-only
  plugins and `register_plugin` for commands, tools, middleware, hooks, and skills.
- Add read-only tools without write-auth requirements.
- Add mutating tools to the repo's write-auth set and audit hooks when applicable.
- Register hooks in code and declare them in `plugin.yaml`.
- Register commands in code and update tests/docs if commands are part of the declared surface.
- Register middleware in code, test its exact phase contract against Hermes,
  and do not invent unsupported manifest fields for it.
- Register bundled skills only through the repo's intended feature/config gates, and declare them in `plugin.yaml` if the manifest advertises skills.
- Update public surface counts in `AGENTS.md` when counts are listed.
- Add or maintain an exposure test that compares registered tool names with
  `plugin.yaml` `provides_tools`, then checks each tool has a matching schema
  name, non-empty description, object `parameters`, and callable handler.

## Common Failure Modes

- Handler exists but is missing from `_TOOLS` or `register(ctx)`.
- Registration exists but schema is missing or stale.
- Schema exists but `schema["name"]` disagrees with the registered tool name.
- Code registration changed but `plugin.yaml` still advertises the old surface.
- `plugin.yaml` advertises a tool/hook/skill that code no longer registers.
- A decorated kit declaration exists but the module passed to `register_all` or
  `register_plugin` does not expose it.
- Middleware was treated as an observational hook or omitted its single-use
  `next_call` contract.
- Mutating tool is usable without auth or is omitted from audit logging.
- Read-only tool incorrectly requires bot auth.
- Tests assert the old registration count but not the new contract.
- Docs say a hook exists after it was removed, or omit a hook that still runs.
- Runtime config enables/disables a plugin differently than the checked-in repo suggests.

## Runtime Truth

For questions about what Hermes actually exposes, inspect runtime state when available:

- Effective Hermes config for plugin repos, enabled toolsets, platform-specific config, and skill directories.
- The live tool list for the target platform, when the Hermes CLI/runtime is available.
- Rendered Kubernetes/Helm/Argo manifests for minilab or production deployments.
- Logs or pod files only after checking the effective config path that loads the plugin.

Checked-in docs are useful orientation, but runtime exposure is determined by the deployed plugin revision plus the active Hermes config.
