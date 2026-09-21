## Why

A plugin author cannot prove their plugin still satisfies the Hermes runtime
contract, because the kit ships no test support. Fourteen consumer plugin
repositories have each hand-rolled a fake plugin context in response; eight use
a strict positional `register_skill`, three a permissive `**kwargs` form, three
define none at all. None of them catches the failure they exist to catch: a
permissive fake accepts every future signature, and a hand-written positional
fake is a frozen snapshot of one host revision, so both pass unchanged when the
host adds a required parameter.

The kit already owns a working fake and a real-Hermes import seam, but they live
in `tests/`, which never reaches a consumer install — the wheel carries only
`hermes_plugin_kit/__init__.py` and `hermes_plugin_kit/observability.py`. The
repository states the principle in `skills/hermes-plugins/references/plugin-kit.md`
("a fake context alone can hide signature drift") and cannot deliver it.

## What Changes

- A public `hermes_plugin_kit.testing` module, importable from an installed
  wheel with no new runtime dependency.
- A recording fake plugin context that captures registration calls, plus a
  replay that binds those captured arguments against the real `PluginContext`
  signatures wherever a Hermes checkout is importable. Added, renamed, and
  removed registrar parameters each fail and name what drifted.
- Assertion helpers for the registration receipt's log field order, duplicate
  detection, deterministic ordering, and the schema-under-`function.parameters`
  convention, so a consumer asserts them without copying literals out of the
  kit's own tests.
- The deployed Hermes revision exposed as a module constant.
- A reachability guard that fails when a documented public name is missing from
  `__all__`, plus a build-install-import check that exercises the wheel rather
  than the source tree.

## Capabilities

### New Capabilities

- `plugin-test-harness`: the kit's consumer-facing test support — the recording
  fake, drift replay against the real host, registration assertions, the
  deployed-revision constant, and the guarantee that documented names are
  reachable from an installed package.

### Modified Capabilities

<!-- None. This change introduces the first capability in openspec/specs/. -->

## Impact

- `hermes_plugin_kit/testing.py` (new), re-exported from
  `hermes_plugin_kit/__init__.py` and added to `__all__`.
- `plugin_reference_tool` joins `__all__`; it is documented and tested today but
  absent from the public list.
- `pyproject.toml` stays unchanged: `packages` is an explicit list, so a flat
  module ships while a subpackage would be silently omitted from the wheel.
- New tests in `tests/test_testing_harness.py`; new `justfile` recipe and CI job
  for the installed-artifact check.
- No change to `deliver_media` or `invoke_host_tool`.
