---
name: hermes-plugins
description: Build, modify, migrate, review, and debug Hermes Agent plugin repositories. Use for plugin.yaml manifests, PluginContext or register(ctx), hermes-plugin-kit adoption, tools, commands, middleware, hooks, bundled skills, guarded host calls, media delivery, registration tests, and runtime toolset exposure. Do not use for Hermes core tools or specialized provider plugins unless the request explicitly targets those upstream surfaces.
license: MIT
metadata:
  audience: developers
  keywords: hermes-agent, plugins, hermes-plugin-kit, PluginContext
---

# Hermes Plugins

Work from the Hermes runtime contract outward. `hermes-plugin-kit` removes
repeated lifecycle boilerplate, but it does not replace Hermes Agent's plugin
API or make every extension type a general plugin.

## Load The Right Reference

- Read [references/plugin-kit.md](references/plugin-kit.md) before adopting,
  upgrading, or debugging `hermes-plugin-kit`.
- Read [references/official-hermes-agent.md](references/official-hermes-agent.md)
  for upstream plugin types, hooks, discovery, enablement, toolsets, or runtime behavior.
- Read [references/surface-checklist.md](references/surface-checklist.md) when
  adding, removing, renaming, or auditing a declared plugin surface.

If these references disagree with the pinned or deployed Hermes source, the
source and effective runtime configuration win.

## First Pass

1. Read the repo's `AGENTS.md` or equivalent local guidance before editing.
2. Classify the extension: general plugin, platform, memory provider, context
   engine, model provider, or another specialized backend. Do not force a
   specialized interface through the kit.
3. Inspect `plugin.yaml`, `__init__.py`, and the actual `register(ctx)` path.
4. Check `pyproject.toml` and its lockfile to prove whether the kit is installed.
5. Inspect registration, parity, handler, privacy, and upstream-contract tests.
6. For live exposure, verify enabled plugins, effective toolsets, platform
   allowlists, and the loaded tool inventory instead of trusting docs.

## Add Or Change A Surface

Update every declaration in one change set:

- Handler and schema or kit declaration.
- `register(ctx)` wiring, whether direct or through `register_plugin`.
- `plugin.yaml` exposure, requirements, version, and metadata.
- Registration, schema, auth, privacy, and manifest-parity tests.
- `AGENTS.md`, README, and runtime configuration when they enumerate the surface.

Treat `plugin.yaml` as a runtime/discovery contract, not generated documentation. If it disagrees with `register(ctx)`, the change is incomplete.

## Kit Boundaries

- Prefer `@tool` plus `register_all` for an existing tool-only plugin.
- Prefer `@command`, `@tool`, `@middleware`, `@hook`, `plugin_skill`, and
  `register_plugin` for a full lifecycle plugin.
- Use direct `PluginContext` APIs for surfaces the kit does not wrap.
- Use `invoke_host_tool` or `deliver_media` for supported host-managed calls.
  Do not dispatch `send_message` through `tools.registry`.
- Keep the kit dependency and lockfile current with the repo-native install command.

## Auth, Privacy, And Runtime Boundaries

Keep read-only tools usable without write credentials. Put mutations behind the
plugin's established auth and audit mechanism, then assert that in tests.

For Discord/community-facing plugins, check privacy boundaries before widening any response or memory surface. Prefer transcript/runtime evidence over plausible docs when exposing user, channel, memory, or bot-write behavior.

For catalog or content plugins, keep public summaries from leaking raw prompts, raw descriptions, raw lyrics, aligned lyric data, private memory, or internal classifier notes unless the user explicitly requests a permitted raw surface and the repo already supports it.

## Validation

Run the plugin repository's own install/test commands before committing —
whatever its task runner is. (`just install` / `just test` / `just build` are
this kit's own recipes, not something a consumer repository has.)

Also run focused registration, manifest-parity, hook or middleware, and local
import tests for the surface touched.

### Checking your plugin against the real host

A fake plugin context cannot tell you the host changed underneath you. A
permissive one accepts every future signature, and a hand-written one is a
frozen snapshot of whichever Hermes revision you wrote it against — both keep
passing when Hermes adds a required parameter.

Use the kit's harness instead. It records what your plugin registers and
replays those calls against the real `PluginContext` signatures:

```python
from hermes_plugin_kit.testing import (
    DEPLOYED_HERMES_REVISION,
    RecordingPluginContext,
    resolve_hermes_checkout,
)

ctx = RecordingPluginContext(name="your-plugin", config={})
register(ctx)                     # your plugin's own registration entry point

host = resolve_hermes_checkout()  # honors HERMES_AGENT_PATH; fetches at the pin otherwise
report = ctx.check_against_host(host.plugin_context_class())
assert report.clean, report       # `checked` is False when no host was available
```

Requires **hermes-plugin-kit 0.9.0 or newer** — earlier pins have no
`hermes_plugin_kit.testing` module, and this skill tracks a moving checkout
while your plugin pins the kit to a commit, so check before you follow this.

This is plain Python. Wire it into `make`, `pytest`, `unittest`, or whatever
the repository already uses; it does not need this kit's task runner.

**When no checkout is available** — a sandboxed runner, no network, no `git` —
`resolve_hermes_checkout()` returns an unavailable result naming
`DEPLOYED_HERMES_REVISION`, and `check_against_host(None)` gives a report with
`checked == False`. That is the honest outcome, not a failure to route around:
treat an unchecked report as "not verified", never as "verified clean". Set
`HERMES_AGENT_PATH` to a local checkout to run the check offline, or
`HERMES_PLUGIN_KIT_CACHE` to control where a fetched one is stored.

`check_registration(ctx)` additionally reports duplicate names, non-deterministic
ordering, and tool schemas whose arguments are flattened beside `parameters`
instead of inside it.

For tool surfaces, prefer a test that asserts every registered tool has:
matching `plugin.yaml` exposure, matching schema `name`, non-empty schema
`description`, object-shaped schema `parameters`, and a callable handler.

Before finalizing, re-run a quick inventory:

```bash
rg -n "register_tool|register_hook|register_command|register_skill|provides_tools|provides_hooks|provides_skills|requires_env" __init__.py plugin.yaml tests
```

## Shipping

If the user asks to ship, commit after validation.

When the plugin is deployed through an immutable infra/GitOps snapshot, treat
the source change and snapshot promotion as one shipping objective with
separate proof gates:

- Merge the plugin change to the snapshot's declared branch before pinning it.
  Do not promote an open-PR commit that could become unreachable after a squash
  merge or branch deletion.
- From the current infra base, resolve a candidate snapshot, confirm the target
  plugin advances to the merged commit without regressing other pins, run the
  repository's full snapshot qualification, and open or update the infra PR.
- Keep source, promotion, and runtime claims distinct. A merged plugin PR is not
  deployed; a merged snapshot PR is not live until GitOps reconciles and the
  effective workload reports the promoted snapshot and plugin revision.

Read the infra repository's local guidance for its policy file, validation
command, and rollout proof instead of assuming a universal snapshot format.
