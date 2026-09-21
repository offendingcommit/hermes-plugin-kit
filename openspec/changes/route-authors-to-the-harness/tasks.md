## 1. Route the skill to the harness (U8)

- [x] 1.1 Rewrite the Validation section's contract-test instruction to name
      the harness by import path and the pin by constant, replacing an
      instruction that names no mechanism. Verify: every name it gives resolves
      against the installed package, not a repository path.
- [x] 1.2 Document the lane in a Python-invocable form a consumer can wire into
      their own runner, and mark the `just` recipes as work in this repository.
      Verify: no instruction aimed at consumers requires `just`.
- [x] 1.3 Document provisioning — how a checkout is resolved, what to set when
      an environment cannot fetch, and that a refusal is a legible outcome
      rather than a failure to work around. Verify: the sandboxed case is
      described, not just the happy path.
- [x] 1.4 State the minimum kit revision beside every harness import path the
      skill names. Verify: a reader on an older pin learns the requirement
      before hitting an ImportError.
- [x] 1.5 Add the harness row to the Surface Map's `Kit API` column, so the
      reachability guard covers it. Verify: the guard's documented-name set
      grows to include the harness, and still passes.

## 2. Stop reference links rotting (U8)

- [x] 2.1 Extend `tests/test_skills.py` to resolve relative links in
      `references/*.md`, which it checks only in `SKILL.md` bodies today.
      Verify: a deliberately broken reference link fails the test, and the
      existing `SKILL.md` validation still passes.
- [x] 2.2 Repoint or remove reference links that are meaningless to a consumer
      — repository paths into `tests/`, which no install contains. Verify: the
      links that remain resolve, and none instructs a consumer to open a file
      their install does not have.
