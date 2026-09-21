## Context

The skill is distribution, not decoration. `README.md` documents installing it
by symlinking this checkout into an agent's skills directory, so what the skill
says is what an author — or a coding agent authoring a plugin — will do.

That creates a coupling the skill has to be honest about. The symlink points at
a moving checkout, while `AGENTS.md` requires consumers to pin the kit to an
immutable commit. A reader's skill is therefore always at least as new as their
kit, and can be much newer. An import path named without a minimum revision is
an `ImportError` waiting for whoever is furthest behind.

The audience also does not share this repository's tooling. `just` is the task
runner here; consumer plugin repositories predominantly use `make`. A lane
documented only as a `just` recipe reads as instructions in the repositories
that most need it and executes in none of them.

## Goals / Non-Goals

**Goals:**

- An author following the Validation section reaches a real drift check, or a
  refusal that names what to do about it.
- Every import path the skill names carries the revision that provides it.
- The instructions work regardless of task runner, OS, or network access.
- Reference-file links stop rotting unnoticed.

**Non-Goals:**

- Converting any consumer repository. The lane ends at documentation and the
  shipped entry points.
- Version-coupling the skill to the pinned kit revision. That is the real fix
  for the symlink/pin mismatch and is deferred; naming a minimum revision is
  the cheap mitigation, not a solution.

## Decisions

**Route by import path, never by repository path.** `references/plugin-kit.md`
currently links canonical sources as `../../../tests/test_kit.py`. Those
resolve inside a kit checkout and are meaningless to a consumer, whose install
contains no `tests/` at all — the sdist carries it, the wheel does not, and
`git+…@sha` installs neither. Anything the skill tells a consumer to use is
named as `hermes_plugin_kit.testing.…`.

**Document the Python entry point, not a `just` recipe.** The harness is
importable; a consumer wires it into whatever runner they already have. The
`just` recipes stay documented for work *in this repository* and are marked as
such, rather than presented as the lane.

**State the minimum revision beside every import path.** One line, next to the
name, so a reader on an older pin sees the requirement before the traceback.

**Extend the existing skills test rather than adding a new one.** It already
walks `SKILL.md` bodies and resolves relative links; reference files are the
gap, not the mechanism.

## Risks / Trade-offs

- A minimum-revision note is a manual claim. Nothing enforces that the number
  in the skill matches the release that actually introduced the harness, and
  the link test cannot check it. The deferred version-coupling work is what
  would; until then this is documentation discipline.
- Documenting provisioning invites an author to run a fetch inside a sandbox
  that forbids it. The harness already degrades to an explicit unchecked
  result, so the failure is legible — but the skill must say so plainly rather
  than presenting the fetch as unconditional.

## Migration

None. Documentation and a test only.

## Open Questions

None blocking. The symlink/pin coupling is recorded as deferred follow-up in
the plan rather than left implicit here.
