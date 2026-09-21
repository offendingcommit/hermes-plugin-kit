## 1. Stop asserting the capability is missing

- [x] 1.1 Add a capability check that looks for the host's companion-file
      support across its known module locations, returning unknown rather than
      false when it cannot tell. Verify: it reports the capability present
      against both the pinned revision and upstream main, and unknown with no
      host importable.
- [x] 1.2 Replace the warning with what was actually observed, at a level that
      does not claim something is wrong. Verify: no run asserts the files go
      unserved, and the message points at the host's skills tool.
- [x] 1.3 Update the two existing assertions that pinned the old wording,
      preserving the behaviour they guard. Verify: the kwarg is still dropped
      and the skill still registers, with and without a host importable.

## 2. Give the reference tool a reason to be called

- [x] 2.1 Name the available files in the default description, capped with an
      overflow count, and say when to consult them. Verify: the filenames
      appear, and an explicitly supplied description is left untouched.

## 3. Stop discarding examples on optional arguments

- [x] 3.1 Surface optional examples under their own heading. Verify: an
      example on an optional key reaches the description without being
      labelled required, and required examples are unchanged.
