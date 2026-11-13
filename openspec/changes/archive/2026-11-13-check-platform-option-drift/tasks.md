Implementation follows `docs/plans/2026-11-13-0900-feat-platform-annotation-plan.md`.

## 1. Record and check platform options

- [x] 1.1 Record `register_platform` calls on the recording context, returning
      a disposable handle. Verify: `tests/test_platforms.py`
      `PlatformRegistrationTests.test_registers_a_platform_alongside_a_tool`.
- [x] 1.2 Report a recorded option the host's `PlatformEntry` lacks as drift.
      Verify: `tests/test_testing_harness.py`
      `PlatformOptionDriftTests.test_host_without_a_recorded_option_reports_drift_naming_it`
      fails when the comparison is removed.
- [x] 1.3 Report a recorded platform call without field names as not checked.
      Verify: `PlatformOptionDriftTests.test_platform_call_without_field_names_is_reported_unchecked`
      fails when that branch is removed.
- [x] 1.4 Read the field names from a checkout. Verify:
      `ResolvedHostPlatformFieldsTests`.
