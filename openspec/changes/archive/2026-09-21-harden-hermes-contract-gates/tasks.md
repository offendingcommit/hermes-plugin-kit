## 1. Pin-aware local lane with one discovery policy (U5)

- [x] 1.1 Teach the checkout preparation step to check out a requested ref.
      Verify: the pinned revision is reachable locally, where today only the
      default branch is.
- [x] 1.2 Give the pin recipe and the upstream recipe separate checkout
      directories. Verify: running upstream after pinned exercises upstream, not
      the pin left behind by the previous run.
- [x] 1.3 Consolidate both contract modules onto one discovery policy — an
      explicit path is authoritative and re-raises, absence is explicit. Verify:
      the same bad path produces the same refusal from each module.
- [x] 1.4 For any pin-targeted run, compare the resolved checkout's HEAD against
      the pin and refuse on mismatch. Verify: a stale path naming a different
      revision is refused with expected and actual, rather than receipting the
      wrong host.
- [x] 1.5 Emit a receipt naming the revision exercised and the executed-test
      count, capturing the exit status directly rather than through a pipe.
      Verify: the receipt names both, and a filtered pipeline cannot supply the
      pass/fail signal.
- [x] 1.6 Add a guard outside each module's `skipUnless`, firing only when the
      contract recipes set an intent signal. Verify: a bare `just test` with no
      host stays green, while an invoked contract lane that cannot resolve a
      host fails.
- [x] 1.7 Make the context-engine recipe require an explicit checkout path.
      Verify: with the variable unset the recipe refuses instead of skipping.
- [x] 1.8 Prove the all-skipped and zero-collected cases fail. Verify: both are
      reported as failures, which is the pair Python 3.11 exits 0 on.

## 2. Blocking gate and scheduled drift lane (U6)

- [x] 2.1 Point the blocking pinned job at the full contract suite. Verify: it
      passes at the pin and the suite it runs is the whole module, not the
      context-engine one.
- [x] 2.2 Collapse the two pin literals to one. Verify: the workflow names the
      pin exactly once.
- [x] 2.3 Move the drift lane into its own workflow on a daily schedule plus
      manual dispatch, outside required checks, with no `continue-on-error`.
      Verify: a manual dispatch runs, and a failure is visibly red without
      affecting any required check.
- [x] 2.4 Report the upstream revision and failing tests to the run summary, on
      a step that runs even after the contract step fails. Verify: a failing
      dispatch still produces the summary, rather than skipping the step that
      reports the failure.
- [x] 2.5 Add the known-drift baseline and classify failures against it by
      fingerprint. Verify: the #99220 media failure is labelled known, and an
      unrecognized failure appears under its own heading.
- [x] 2.6 Add an activity trigger alongside the schedule. Verify: the workflow
      has a trigger other than the clock, so 60 days of quiet cannot silently
      disable the alarm.

## 3. Workflow shape held by the release contract (U7)

- [x] 3.1 Assert the blocking job selects the full contract suite. Verify:
      reducing it back to the context-engine module fails the test.
- [x] 3.2 Assert the workflow pin equals the harness constant. Verify: editing
      either one alone fails the test.
- [x] 3.3 Assert the drift workflow keeps its schedule trigger, its
      report-on-failure condition, and its baseline file. Verify: removing any
      of the three fails the test.
- [x] 3.4 Assert the built wheel contains the harness module. Verify: dropping
      it from the packaging manifest fails this test where `twine check` passes.
- [x] 3.5 Confirm the existing SHA-pinning, just-only, and
      `pull_request_target` assertions still hold for the new workflow. Verify:
      the release-contract suite passes with both workflows present.
