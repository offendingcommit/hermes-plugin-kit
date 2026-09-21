"""One discovery policy for every contract lane.

The two contract modules used to resolve a Hermes checkout differently, and
both ways could report success without checking anything.

`test_hermes_contract` auto-discovered `~/hermes-agent`, so a bare `just test`
silently exercised whatever branch a developer happened to have checked out,
while CI — which checks out nothing — skipped the module entirely. The same
command meant two different things. `test_context_engine_contract` was env-only
and returned `None` into a silent skip.

Both are replaced by one policy:

- ``HERMES_AGENT_PATH`` is authoritative. A path that is not a checkout raises
  rather than falling back, because an operator who set it meant it.
- Nothing is auto-discovered. A checkout the caller did not name is never used.
- Absence is only *tolerated* when nobody asked for a contract run. When
  ``HERMES_CONTRACT_REQUIRED`` is set, absence is a failure.

That last rule is what lets the guard exist at all. A test outside
``skipUnless`` cannot otherwise tell an intended contract run from an ordinary
``just test``: without the signal it would either redden every push — the
blocking job checks out no Hermes revision and discovers both contract modules
— or never fire, leaving the honest-receipt requirement unenforced.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

#: Set by the contract recipes to say "a contract run was actually asked for".
CONTRACT_REQUIRED_ENV = "HERMES_CONTRACT_REQUIRED"

#: Set by the pin-targeted recipe to the revision the run claims to exercise.
EXPECTED_REVISION_ENV = "HERMES_EXPECTED_REVISION"

HOST_PATH_ENV = "HERMES_AGENT_PATH"


def contract_required() -> bool:
    return bool((os.environ.get(CONTRACT_REQUIRED_ENV) or "").strip())


def host_root() -> Path | None:
    """The checkout to test against, or ``None`` when none was named.

    Raises when a path was named and is not a Hermes checkout: turning that
    into a skip is how a misconfigured lane becomes a misleading green.
    """
    raw = (os.environ.get(HOST_PATH_ENV) or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not (root / "hermes_cli" / "plugins.py").exists():
        raise FileNotFoundError(
            f"{HOST_PATH_ENV} has no hermes_cli/plugins.py: {root}"
        )
    return root


def head_revision(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return None


def revision_mismatch(root: Path) -> str | None:
    """Describe a pin-targeted run pointed at the wrong revision, if it is.

    A stale ``HERMES_AGENT_PATH`` otherwise sails through the pin lane and
    produces a receipt naming a revision the run did not actually use — and CI
    inheriting that variable would go green against an incompatible host.
    """
    expected = (os.environ.get(EXPECTED_REVISION_ENV) or "").strip()
    if not expected:
        return None
    actual = head_revision(root)
    if actual == expected:
        return None
    return (
        f"{HOST_PATH_ENV} is at {actual or 'an unreadable revision'} but this lane "
        f"claims {expected}; refusing to report a revision the run did not use."
    )


def build_guard(module_resolved_host: object, module_name: str) -> type[unittest.TestCase]:
    """A test case that stays *outside* the module's ``skipUnless``.

    Every other test in a contract module is skipped when no host resolves,
    which on Python 3.11 exits 0 — indistinguishable from passing. This one
    cannot be skipped along with them, so an intended-but-unconfigured lane
    fails instead of vanishing.
    """

    class ContractLaneGuard(unittest.TestCase):
        def test_an_intended_contract_run_actually_resolved_a_host(self) -> None:
            if not contract_required():
                self.skipTest(
                    "no contract run was requested; "
                    f"set {CONTRACT_REQUIRED_ENV} to require one"
                )
            self.assertIsNotNone(
                module_resolved_host,
                f"{module_name}: a contract run was requested but no Hermes host "
                f"resolved. Set {HOST_PATH_ENV} to a checkout, or stop setting "
                f"{CONTRACT_REQUIRED_ENV}.",
            )

        def test_a_pin_targeted_run_is_at_the_revision_it_claims(self) -> None:
            if not contract_required():
                self.skipTest("no contract run was requested")
            root = host_root()
            if root is None:
                self.skipTest("covered by the host-resolution guard above")
            mismatch = revision_mismatch(root)
            self.assertIsNone(mismatch, mismatch)

    ContractLaneGuard.__qualname__ = f"{module_name}ContractLaneGuard"
    return ContractLaneGuard
