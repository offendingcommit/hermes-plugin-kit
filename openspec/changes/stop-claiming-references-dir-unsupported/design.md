## Context

Evidence came from a fleet survey of 11 `state.db` stores across 5 pods over 8
days (1,681 plugin tool calls), reported by the memory-sync maintainer, plus
direct verification against two Hermes checkouts here.

The host-side capability is real and not new. `_plugin_skill_linked_files`
exists at `tools/skills_tool.py` in the pinned revision `f80f453` and at
`tools/skills_tool_plugin.py` on main after an upstream refactor. Support dirs
are enumerated in both.

## Goals / Non-Goals

**Goals:**

- Stop asserting a negative the kit cannot establish.
- Give an agent a reason to call the reference tool.
- Stop discarding examples.

**Non-Goals:**

- Removing `plugin_reference_tool`. It remains the right answer where the host
  genuinely does not serve files, and consumers depend on it.
- Changing registration behaviour. The kwarg is still dropped when the host
  does not take it, and the skill still registers.
- Any upstream Hermes change.

## Decisions

**Probe positively; treat "cannot tell" as unknown, never as absent.** The
check returns `True` or `None` — never `False`. The kit knows a handful of
private module paths, and those move; a name it cannot find is not evidence
about the host. This is the whole lesson of the defect, encoded in the return
type rather than left to a comment.

**Not a warning.** When the host serves the files, this is a debug detail.
When the kit cannot tell, it is informational and names the check an author
can run. A warning asserts something is wrong, and nothing is.

**Name files in the default description, not just the mechanism.** An agent
selects on description at call time. `references_dir` is known at build time,
so enumeration is free. Capped, with an overflow count, so a large directory
does not flood the tool list.

**Examples belong to the argument, not to its required-ness.** An example is
guidance about shape; whether a key is mandatory is a separate fact. Rendering
optional examples under their own heading keeps both legible.

## Risks / Trade-offs

- The probe reads private host names, which can move again — it already has
  once. That is why it is a candidate list and why failure means unknown. The
  cost of a stale probe is a slightly less specific log line, not a wrong
  claim.
- Naming files makes the description depend on directory contents, so adding a
  file changes the tool description. That is intended; a stale description is
  what caused the original problem.
- Neither the probe nor a recorded plugin context would have caught this: both
  reason about shape. Only reading the host's other tool did. Worth holding
  onto when the harness tempts a similar inference.

## Migration

None. Consumers already using `plugin_reference_tool` keep working; those who
adopted it because of the false warning may now find the host route sufficient,
but nothing forces that decision.

## Open Questions

- Whether the kit should expose the capability check publicly so a plugin can
  branch on it rather than always registering a reference tool. Deferred: one
  consumer's behaviour is not yet enough to design an API around.
