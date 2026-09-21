## Why

The gate that blocks covers a tenth of the contract, and the lane that would
warn about upstream drift cannot fire on drift at all.

The blocking job runs only `tests.test_context_engine_contract` — 110 lines of
an 853-line suite — against the deployed pin, even though the full suite is
green there. The upstream-drift job triggers on `push` and `pull_request`, so it
runs when this repository changes and never when Hermes moves, which is the only
event it exists to detect. Job-level `continue-on-error: true` compounds that by
rendering it green in the checks list rather than visibly amber.

Locally the picture is worse. `just test` auto-discovers `~/hermes-agent`, so it
silently exercises whatever branch a developer happens to have checked out,
while CI has no checkout and skips the module entirely — the same command
meaning two different things. And on Python 3.11, the version CI pins,
`unittest` exits 0 both when every test skips and when zero tests are collected,
so a contract lane that vanished is indistinguishable from one that passed.

Hermes #99220 is the live proof: it broke the kit's media contract against
upstream on 2026-09-21 and nothing reported it.

## What Changes

- The blocking pinned job runs the full contract suite instead of the
  context-engine module alone.
- The pin becomes one literal, test-enforced against the constant the harness
  ships, instead of two copies that agree with nothing.
- The upstream-drift lane moves to its own scheduled workflow: daily, outside
  required checks, no `continue-on-error`, so red is honestly red.
- Drift reporting names the revision and the failing tests, and separates
  failures already known from genuinely new ones.
- The local lane can target the pin, reports the revision and executed-test
  count, and refuses rather than reporting success when it checked nothing.
- Both contract modules resolve a host by one policy instead of two.

## Capabilities

### New Capabilities

<!-- None. This change alters CI wiring and local task definitions; it adds no
     capability-level requirement, which is why .openspec.yaml sets
     skip_specs: true rather than inventing one to satisfy validation. -->

### Modified Capabilities

<!-- None. -->

## Impact

- `.github/workflows/test.yml` — blocking job widened, drift job removed.
- `.github/workflows/hermes-drift.yml` — new scheduled lane.
- `tests/fixtures/known-hermes-drift.txt` — the baseline that keeps the known
  #99220 failure from drowning a new one.
- `justfile` — pin-aware and upstream contract recipes, separate checkout
  directories, an intent signal so a bare `just test` stays honest.
- `tests/test_hermes_contract.py`, `tests/test_context_engine_contract.py` —
  one discovery policy, plus a guard outside `skipUnless`.
- `tests/test_release_contract.py` — asserts the new workflow shape.
- No change to `deliver_media`. The #99220 break stays out of scope; this
  change makes it visible, not fixed.
