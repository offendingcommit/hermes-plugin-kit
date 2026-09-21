## Why

The repository's own skill tells an author to do something they cannot do.

`skills/hermes-plugins/SKILL.md` instructs: "When compatibility matters, run
contract tests against the real pinned Hermes Agent checkout." It names no
mechanism, no pin, and no import path — and until the harness shipped, no
consumer could carry it out at all. `references/plugin-kit.md` states the
principle in the same breath ("a fake context alone can hide signature drift")
while its Surface Map lists no way to act on it.

The harness now exists, but nothing points at it. The skill is the only channel
through which an author — human or coding agent — learns it is there, so a
harness nobody is routed to is a harness nobody adopts.

Two things make the routing non-obvious. The skill is read from a symlinked
checkout while consumers pin the kit to an immutable commit, so the skill can
name an import path that a reader's pinned kit predates — an `ImportError` for
following the instructions. And consumer repositories predominantly drive tests
with `make`, so a recipe documented only as `just test-contract` is unusable in
the repositories that most need it.

## What Changes

- The Validation section names the harness by import path, names the pin by
  constant, and states the minimum kit revision that provides both.
- The lane is documented in a form a consumer can actually invoke, not as a
  `just` recipe they do not have.
- Provisioning is documented: how a checkout is resolved, and what to set when
  an environment cannot fetch one.
- The Surface Map gains a row for the harness, so the guard that checks
  documented names against `__all__` covers it.
- `tests/test_skills.py` validates relative links in `references/*.md`, which
  it does not do today — only `SKILL.md` bodies are checked, so reference-file
  links rot silently.

## Capabilities

### New Capabilities

<!-- None. This change updates repo-owned documentation and a link test; it
     adds no capability-level requirement, which is why .openspec.yaml sets
     skip_specs: true rather than inventing one. -->

### Modified Capabilities

<!-- None. -->

## Impact

- `skills/hermes-plugins/SKILL.md` — Validation section rewritten.
- `skills/hermes-plugins/references/plugin-kit.md` — Surface Map row, harness
  routing, minimum revision.
- `tests/test_skills.py` — reference-file link validation.
- No production code changes. `AGENTS.md` already binds `plugin-kit.md` to
  public-API changes; this change honors that rule rather than adding a new one.
