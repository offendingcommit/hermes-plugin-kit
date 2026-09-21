## 1. Recording fake and drift replay (U2)

- [x] 1.1 Add the flat `hermes_plugin_kit/testing.py` module and re-export it
      from the package root, following the `observability.py` precedent.
      Verify: the module imports from the package root in a fresh interpreter.
- [x] 1.2 Write the failing drift tests first — added required parameter,
      renamed parameter, removed registrar — against simulated host shapes.
      Verify: each fails for its intended reason before any implementation.
- [x] 1.3 Implement the recording fake, carrying plugin name and config as
      first-class constructor arguments so `load_plugin_config` and the receipt
      identity resolve. Verify: the three drift tests pass and the unchanged-host
      case reports clean.
- [x] 1.4 Add the `references_dir`-unsupported mode and the missing-registrar
      mode, so both branches of the kit's capability probe are exercisable.
      Verify: the probe takes its retry branch and the unsupported-context error
      raises, each under its own test.
- [x] 1.5 Expose the deployed Hermes revision as a module constant, and keep
      every Hermes import lazy. Verify: importing the module pulls in neither
      hermes-agent nor a test framework.

## 2. Registration and schema assertions (U3)

- [ ] 2.1 Add the receipt assertion, pinned to the emitted log field order
      rather than the dataclass order. Verify: a permuted field order fails.
- [ ] 2.2 Add duplicate-detection and deterministic-ordering assertions.
      Verify: a duplicate tool name fails while the same name across a slash and
      a CLI command passes; out-of-order registration still yields sorted output.
- [ ] 2.3 Add the schema-convention assertion. Verify: a schema with flattened
      top-level arguments fails and a conforming one passes.
- [ ] 2.4 Add the drift report that names the registrar and parameter, shaped
      like the kit's existing structured receipts. Verify: the report names the
      added parameter against a drifted host and reports clean otherwise.

## 3. Public-surface reachability (U4)

- [ ] 3.1 Add a `justfile` recipe that builds, installs into a throwaway
      environment, and imports through the public path, plus the CI job that
      runs it. Verify: the recipe fails when the module is dropped from the
      packaging manifest, where `twine check` passes.
- [ ] 3.2 Add the harness names and `plugin_reference_tool` to `__all__`.
      Verify: `plugin_reference_tool` is present, closing the existing gap.
- [ ] 3.3 Add the guard asserting every documented public name is exported and
      importable. Verify: temporarily removing one name fails the guard.
