# hermes-plugin-kit

Convention-correct helper library for registering `hermes-agent` plugin tools.
This repository is an installable Python package, not a path-loaded runtime
plugin.

## Working Rules

- Keep the public API centered on `@tool`, `register_all`, and the argument
  helpers exported from `hermes_plugin_kit`.
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
