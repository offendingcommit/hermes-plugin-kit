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
- [x] 1.6 Catch a positionally renamed parameter by comparing names as well as
      arity. Binding alone misses it, because Python binds positionals by
      position. Verify: a host renaming `register_hook`'s first parameter is
      reported with both names, and an unrenamed positional host stays clean.

## 2. Registration and schema assertions (U3)

- [x] 2.1 Add the receipt assertion, pinned to the emitted log field order
      rather than the dataclass order. Verify: a permuted field order fails.
- [x] 2.2 Add duplicate-detection and deterministic-ordering assertions.
      Verify: a duplicate tool name fails while the same name across a slash and
      a CLI command passes; out-of-order registration still yields sorted output.
- [x] 2.3 Add the schema-convention assertion. Verify: a schema with flattened
      top-level arguments fails and a conforming one passes.
- [x] 2.4 Add the drift report that names the registrar and parameter, shaped
      like the kit's existing structured receipts. Verify: the report names the
      added parameter against a drifted host and reports clean otherwise.

## 3. Host-shape coverage the kit's own tests need (U3)

- [x] 3.1 Add a mode whose `register_skill` advertises `references_dir` through
      a permissive signature but rejects it at call time, reproducing the host
      shape that fools the kit's capability probe. Verify: `register_plugin`
      takes its documented retry branch and the skill still registers.
- [x] 3.2 Add a mode whose `register_skill` raises a `TypeError` unrelated to
      `references_dir`. Verify: the retry does not swallow it and the error
      propagates.

## 4. Migrate the kit's own fake onto the harness (U3)

- [x] 4.1 Replace `FakeCtx` and `FakePluginCtx` in `tests/test_kit.py` with the
      harness. Verify: the suite passes unchanged, with no loss of coverage.
- [x] 4.2 Replace the four bespoke host-shape classes — the strict positional
      host, the explicit-`references_dir` host, the fooled-probe host, and the
      broken host — with harness modes. Verify: the tests that own those
      branches still fail when their branch regresses.
- [x] 4.3 Record the line delta from the migration. Verify: the migration
      removes more lines than it adds, which is the success criterion's only
      in-repo evidence.

## 5. Public-surface reachability (U4)

- [x] 5.1 Add a `justfile` recipe that builds, installs into a throwaway
      environment, and imports every `__all__` name, plus the CI job that runs
      it after the metadata check. Verify: the recipe fails when the module is
      dropped from the packaging manifest, where `twine check` passes.
- [x] 5.2 Add `plugin_reference_tool` to `__all__`, closing the existing gap
      between what the kit documents and what it exports. Verify: it is
      importable from the package root and present in the export list.
- [x] 5.3 Add the guard asserting every name in the Surface Map's `Kit API`
      column is exported and importable. Verify: temporarily removing one name
      fails the guard.

## 6. Shipped checkout resolver (U9)

- [x] 6.1 Write the offline case first: no checkout, no network. Verify: it
      fails before implementation, then returns an explicit unchecked result
      naming the revision and the reason, within the timeout.
- [x] 6.2 Export the resolver, honoring an explicit `HERMES_AGENT_PATH` ahead of
      any fetch. Verify: a supplied path is used as-is and no fetch is attempted.
- [x] 6.3 Fetch at the pin when nothing resolves, into a reused cache, bounded
      by a timeout and never silently retried. Verify: the first call fetches a
      checkout whose HEAD is the pin and the replay then runs; the second call
      reuses the cache.
- [x] 6.4 Refuse or re-resolve a cache sitting at the wrong revision. Verify: a
      cache at an unrelated revision is never used as if it were the pin.
