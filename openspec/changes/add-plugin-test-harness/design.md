## Context

The kit's own fake plugin context and its real-Hermes import seam already exist
and work, in `tests/test_kit.py` and `tests/test_hermes_contract.py`. Nothing
about them is wrong; they are simply unreachable. Consumers install the kit via
`git+…@sha`, and the built wheel contains only `hermes_plugin_kit/__init__.py`
and `hermes_plugin_kit/observability.py`.

Two constraints shape everything below. `pyproject.toml` declares
`packages = ["hermes_plugin_kit"]` as an explicit list, so a new subpackage is
dropped from the wheel with no warning and `twine check` — which validates
metadata, not archive contents — does not notice. And a consumer's machine
generally cannot import hermes-agent at all, so any mechanism that needs the
real class at construction time is unavailable exactly where the harness is
meant to be used.

## Goals / Non-Goals

**Goals:**

- A fake a consumer can import and use with no host present.
- A drift check that fails when the host's registrar signatures change, and
  names what changed.
- Registration assertions that do not require copying literals out of the kit's
  own test suite.
- Proof that documented public names survive the trip into a wheel.

**Non-Goals:**

- Converting any consumer repository's existing fake. Adoption is each
  consumer's own call.
- Changing `deliver_media` or `invoke_host_tool` behavior.
- Exposing the harness to the Hermes runtime agent loop. It is dev-time
  support, invoked from a consumer's test runner.

## Decisions

**Record-and-replay, not a "stricter" fake.** Probing five candidates settled
this. A permissive `**kwargs` fake and a hand-written positional fake mirroring
today's host both pass when the host gains a required parameter — the second is
a frozen snapshot, not a stronger contract. Comparing the fake's own signature
to the real one also catches nothing, because `**kwargs` is compatible with
every signature by construction. `create_autospec` does catch it, but needs the
real class importable, which rules it out as the shipped mechanism. Binding
recorded call arguments through the real signature catches added, renamed, and
removed parameters, and needs the host only at check time. Rejected: shipping
selectable strict and permissive fakes, which would have shipped two shapes that
both miss the defect.

**The replay lives in the fake, not only in the kit's contract lane.** A
consumer calling the fake directly is invisible to a lane-side check.

**A flat module, not a subpackage.** Forced by the explicit `packages` list
above; it also matches the existing `observability.py` precedent. A wheel
membership assertion guards it, since `twine check` cannot.

**Receipt assertions pin the log field order.** The `RegistrationSummary`
dataclass order and the order `log_registration_summary` emits genuinely
differ, and the logged string is the contract `AGENTS.md` pins.

**Composable pieces, not one entry point.** The fake, the revision constant,
the drift check, and the assertions are separately usable, so an author can take
only registration parity without the whole lane.

## Risks / Trade-offs

- A registrar attached dynamically rather than declared on the class has no
  signature to bind against. Treat that as "could not check this surface"
  rather than clean; partial coverage is the realistic outcome, since the one
  registrar with trailing `**kwargs` still binds its required portion.
- The replay proves nothing about host→plugin *callback* drift. If Hermes
  changes how it invokes a registered hook, binding outbound registration calls
  is silent on it. Only the real dispatchers catch that, which is why the
  contract lane stays mandatory rather than being replaced by this harness.
- Adding names to `__all__` grows the public surface and makes this a minor
  release.

## Migration

None. The module is additive; no existing export changes shape. Consumers adopt
it when they choose, and their current fakes keep working untouched.

## Open Questions

- Whether the revision constant should be owned here or read from infra's
  deployed-snapshot receipt. Proceeding with local ownership, because a
  constant that ships in the wheel is the only form a consumer can read.
- Whether the assertion helpers should stay framework-neutral by returning
  structured results rather than raising. Proceeding with neutral results, since
  the kit is stdlib `unittest` but consumers may not be.
