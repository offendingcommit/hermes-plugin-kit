## Why

The kit told plugin authors a host capability was missing that has existed all
along, and one of them built a workaround that then went unused.

`register_plugin` probes `register_skill`'s signature for a `references_dir`
keyword. When it is absent the kit warns that the directory "will not be
surfaced to the agent until the host adds support". Hermes serves skill
companion files through its *skills tool*, not through `register_skill` —
`_plugin_skill_linked_files` returns `references/`, `templates/`, `assets/`
and `scripts/` as `linked_files` in the same response as the skill body.
Verified present at both the pinned revision `f80f453` and upstream main; the
module moved during a refactor but the capability did not appear or disappear.

A signature probe cannot establish that a capability is missing. It can only
establish that one function does not take one argument. The kit asserted the
stronger claim, and a fleet survey traced the consequence: the warning fired
on every pod boot, a plugin built `plugin_reference_tool` to work around it,
and the resulting tools were called zero times in eight days across three
profiles while the real route sat available and unmentioned.

Two smaller defects share that theme of guidance never reaching the agent. The
reference tool's default description names no files, so an agent must spend a
speculative call to learn whether anything is worth reading. And an example
supplied for an *optional* argument is collected and then dropped, so a plugin
can ship good guidance no agent ever sees.

## What Changes

- The kit stops claiming the capability is missing. It checks whether the host
  serves companion files and, when it cannot tell, says what it observed
  instead of asserting absence. Not a warning either way: nothing is wrong.
- `plugin_reference_tool`'s default description names the available files and
  says when to consult them.
- Examples on optional arguments reach the description, under their own
  `Optional:` heading so required and optional stay distinguishable.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

<!-- None at the spec level: this corrects what the kit *claims* about a host
     and what it surfaces to an agent. The registration behaviour itself --
     the kwarg is dropped, the skill still registers -- is unchanged. -->

## Impact

- `hermes_plugin_kit/__init__.py` — capability probe, corrected log, default
  description, `_augment_description`.
- `tests/test_kit.py` — two existing assertions updated: the behaviour they
  guard is unchanged, only the claim in the log.
- `tests/test_testing_harness.py` — new coverage for all three.
- No host change required. That is the point: the capability already exists.
