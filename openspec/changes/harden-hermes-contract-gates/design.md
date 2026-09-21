## Context

Four defects sit in the current wiring, and three of them look like success.

`.github/workflows/test.yml` triggers only on `push` and `pull_request`. The
drift job therefore runs on this repository's activity and never on Hermes'.
Job-level `continue-on-error: true` additionally makes it render green in the
checks list, so even when it does run and fail, the signal reads as a pass.

`justfile`'s `prepare-hermes-agent` clones the default branch with no ref
support, so the pinned revision is unreachable outside CI — `just test-contract`
can only ever test upstream main. Both contract recipes resolve to the same
directory, and the recipe swallows the pull error a detached checkout leaves
behind, so an upstream run after a pinned one silently re-exercises the pin.

And the pinned gate covers `tests.test_context_engine_contract` only. The full
suite is green at `f80f453` — verified by running it there — so widening the job
adds coverage rather than risk on the day it lands.

## Goals / Non-Goals

**Goals:**

- A blocking gate whose green means the whole contract holds at the pin.
- A drift lane that fires when Hermes moves, is honestly red, and distinguishes
  a new break from the known one.
- A local lane that can target the pin and never reports success for a run that
  checked nothing.

**Non-Goals:**

- Fixing the #99220 relay-egress break. It is the motivating evidence; making it
  visible is this change's job, and repairing it is a separate objective.
- Promoting the deployed pin past `f80f453`.
- A tracking-issue lifecycle for the drift lane. The requirement asks for a
  visible record, which a job summary plus an honestly-red workflow provides; an
  issue manager is stateful, needs a permission the workflow lacks, and is
  deferred until the lane has proven it fires.

## Decisions

**A separate scheduled workflow, not a repaired flag.** Fixing
`continue-on-error` would leave the lane triggering on the wrong event. A
workflow on `schedule` plus `workflow_dispatch`, outside required checks and
carrying no `continue-on-error`, fires on the right event and cannot mislead the
PR checks list — while still being non-blocking, since a workflow that is not a
required check cannot block a merge. Daily, so the worst-case detection lag is a
stated 24 hours rather than an unspecified hope.

**The workflow keeps a pin literal; a test enforces agreement.** The workflow
cannot read the harness constant: `actions/checkout` consumes the `ref:` before
`setup-uv` installs Python, so a dynamic read would mean reordering the job to
install a toolchain ahead of the host checkout. Test-enforced agreement gets the
same single-source-of-truth property through the route the release-contract
tests already own.

**Assert the executed-test count, not the exit status.** On Python 3.11 —
pinned in CI — `unittest` exits 0 both when every test skips and when zero tests
are collected; exit 5 arrives only in 3.12. So "the gate passed" and "the gate
ran" are indistinguishable from the status alone.

**The guard needs an intent signal.** A test outside `skipUnless` cannot tell an
intended contract run from an ordinary one. Without a signal it either reddens
the blocking job on every push — that job checks out no Hermes revision and
`just test` discovers both contract modules — or never fires. The contract
recipes set the signal; a bare `just test` stays green and claims no contract
coverage.

**Baseline on failure fingerprints, not test names.** A baselined test whose
assertion or error has changed is a new break wearing a known name. Matching on
names alone would file it as expected.

## Risks / Trade-offs

- A scheduled workflow on a public repository is auto-disabled after 60 days of
  inactivity, so the alarm can switch itself off in exactly the quiet period it
  guards. An activity trigger alongside the schedule mitigates it; it does not
  eliminate it, and that residue is accepted rather than solved here.
- The drift lane launches knowingly red, because #99220 stays out of scope. The
  baseline is what keeps that from training people to ignore it.
- Widening the blocking gate means any contract the job previously ignored can
  now fail the build. That is the point, and the suite is green at the pin
  today, but it does remove a hiding place.

## Migration

None. CI wiring and local recipes only; no consumer-visible API changes.

## Open Questions

- Whether the drift lane should eventually block once #99220 is resolved and the
  baseline is empty. Not decided here; the settled position is non-blocking.
