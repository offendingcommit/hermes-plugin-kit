## Why

The kit is gaining `@platform`, which registers gateway platforms through
`PluginContext.register_platform`. That registrar takes its options as
`**entry_kwargs`, so replaying a recorded call against its signature accepts
any option name. A Hermes release that removes or renames a `PlatformEntry`
field would leave the replay reporting clean while the plugin breaks at
registration. `PlatformEntry` went from 26 fields to 46 between two pinned
Hermes revisions, so this is the drift most likely to happen.

## What Changes

- The recording context records `register_platform` calls.
- The drift replay also compares each recorded platform option name with the
  host's `PlatformEntry` fields, read from the checkout, and reports a missing
  one as drift naming the option.
- A recorded platform call replayed without those field names is reported as
  not checked, never as clean.

## Capabilities

### Modified Capabilities

- `plugin-test-harness`: the replay requirement gains the platform-option
  scenarios.

## Impact

`hermes_plugin_kit/testing.py` (`RecordingPluginContext.check_against_host`
gains an optional `platform_entry_fields` keyword; `ResolvedHost` gains
`platform_entry_fields()`). Existing callers that pass only the host class keep
their behavior unless they record a platform call.
