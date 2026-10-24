"""The drift baseline is committed once and matched everywhere.

A fingerprint that absorbed a home directory, a temp path, a port, or a PID
would match on the machine that produced it and nowhere else -- the baseline
would look correct locally and classify every known failure as new in CI. These
tests hold the normalization that prevents that.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "classify_drift", ROOT / "scripts" / "classify_drift.py"
)
classify_drift = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(classify_drift)

LOGS = ROOT / "tests" / "fixtures" / "contract-logs"

#: What a completed run with one baselined failure ends with. Without it a log
#: is not evidence that the suite ran at all.
_RAN_TRAILER = "\n" + "-" * 70 + "\nRan 17 tests in 0.601s\n\nFAILED (failures=1)\nEXIT=1\n"


class FingerprintStabilityTests(unittest.TestCase):
    """Same failure, different machine, same fingerprint."""

    def test_posix_and_windows_paths_normalize_alike(self) -> None:
        posix = classify_drift.fingerprint(
            "FileNotFoundError: No such file: /home/runner/work/hermes-agent/x.py"
        )
        mac = classify_drift.fingerprint(
            "FileNotFoundError: No such file: /Users/someone/hermes-agent/x.py"
        )
        windows = classify_drift.fingerprint(
            r"FileNotFoundError: No such file: C:\Users\someone\hermes-agent\x.py"
        )

        self.assertEqual(posix, mac)
        self.assertEqual(posix, windows)
        self.assertNotIn("runner", posix)
        self.assertNotIn("someone", posix)

    def test_temp_directories_do_not_reach_the_fingerprint(self) -> None:
        first = classify_drift.fingerprint(
            "AssertionError: /var/folders/v9/T/tmpab12cd/references != expected"
        )
        second = classify_drift.fingerprint(
            "AssertionError: /tmp/tmp99zzzz/references != expected"
        )

        self.assertEqual(first, second)
        self.assertNotIn("tmpab12cd", first)

    def test_ports_and_pids_do_not_reach_the_fingerprint(self) -> None:
        first = classify_drift.fingerprint(
            "ConnectionError: could not connect to 127.0.0.1:54321 (pid 88213)"
        )
        second = classify_drift.fingerprint(
            "ConnectionError: could not connect to 127.0.0.1:61000 (pid 9182)"
        )

        self.assertEqual(first, second)

    def test_small_numbers_survive_because_they_usually_carry_meaning(self) -> None:
        """Normalization stops at two digits on purpose.

        Ports, PIDs and byte counts are multi-digit and volatile. A small
        number in an assertion is usually part of the failure's identity --
        "expected 3, got 5" is a different break from "expected 3, got 7" --
        so flattening every digit would trade machine-noise for lost signal.
        """
        three = classify_drift.fingerprint("AssertionError: expected 3 items, got 5")
        seven = classify_drift.fingerprint("AssertionError: expected 3 items, got 7")

        self.assertNotEqual(three, seven)

    def test_a_genuinely_different_cause_still_differs(self) -> None:
        """Normalization must not flatten real differences into one bucket."""
        relay = classify_drift.fingerprint(
            "AssertionError: Refusing to send to 'telegram': relay routing unknown"
        )
        other = classify_drift.fingerprint(
            "AssertionError: schema parameters must be object-shaped"
        )

        self.assertNotEqual(relay, other)

    def test_a_line_number_shift_is_not_new_drift(self) -> None:
        body_a = 'File "x.py", line 807, in test\n    assert False\nAssertionError: boom'
        body_b = 'File "x.py", line 912, in test\n    assert False\nAssertionError: boom'

        self.assertEqual(
            classify_drift.fingerprint(body_a), classify_drift.fingerprint(body_b)
        )


class BaselineTests(unittest.TestCase):
    def test_the_committed_baseline_parses(self) -> None:
        entries = classify_drift.load_baseline()

        self.assertTrue(entries, "an empty baseline files every known failure as new")
        for test_id, fingerprint in entries.items():
            with self.subTest(test_id=test_id):
                self.assertTrue(fingerprint)
                self.assertNotIn("/", fingerprint, "a path in a baseline is machine-specific")

    def test_a_known_failure_classifies_as_known(self) -> None:
        test_id, fingerprint = next(iter(classify_drift.load_baseline().items()))
        log = (
            f"FAIL: t ({test_id})\n"
            + "-" * 70
            + "\nTraceback (most recent call last):\n"
            + f"AssertionError: ... {fingerprint.replace('-', ' ')} ...\n"
            + _RAN_TRAILER
        )

        known, new = classify_drift.classify(log, classify_drift.load_baseline())

        self.assertEqual(1, len(known), f"known={known} new={new}")
        self.assertEqual([], new)

    def test_the_same_test_failing_differently_is_new(self) -> None:
        test_id = next(iter(classify_drift.load_baseline()))
        log = (
            f"FAIL: t ({test_id})\n"
            + "-" * 70
            + "\nTraceback (most recent call last):\n"
            + "AssertionError: an entirely unrelated cause\n"
            + _RAN_TRAILER
        )

        known, new = classify_drift.classify(log, classify_drift.load_baseline())

        self.assertEqual([], known)
        self.assertEqual(1, len(new), "a baselined test failing differently is a new break")


class RunThatDidNotExecuteTests(unittest.TestCase):
    """A suite that never ran its checks is drift, not a clean report.

    The captured logs under ``tests/fixtures/contract-logs`` come from a real
    run where both contract modules died importing the host
    (``No module named 'ruamel'``). The tail-only one is exactly what the drift
    workflow handed the classifier while the lane printed ``tail -25``: the
    ``ERROR:`` headers were cut, no failure block parsed, and the report read
    "No contract failures against upstream."
    """

    def test_tail_of_an_import_failure_is_new_drift_naming_the_module(self) -> None:
        log = (LOGS / "import-failure-tail.log").read_text()

        known, new = classify_drift.classify(log, classify_drift.load_baseline())

        self.assertEqual([], known)
        self.assertTrue(new, "an import failure must never classify as clean")
        self.assertTrue(
            any("test_context_engine_contract" in entry for entry in new), new
        )

    def test_full_import_failure_log_names_both_modules(self) -> None:
        log = (LOGS / "import-failure-full.log").read_text()

        _, new = classify_drift.classify(log, classify_drift.load_baseline())

        joined = "\n".join(new)
        self.assertIn("test_hermes_contract", joined)
        self.assertIn("test_context_engine_contract", joined)

    def test_the_cli_exits_non_zero_on_the_tail_only_log(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "classify_drift.py"),
             str(LOGS / "import-failure-tail.log"), "0" * 40],
            capture_output=True, text=True, check=False,
        )

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertNotIn("No contract failures", result.stdout)

    def test_zero_tests_ran_is_new_drift_even_when_unittest_exits_zero(self) -> None:
        log = "\n" + "-" * 70 + "\nRan 0 tests in 0.000s\n\nOK\nEXIT=0\n"

        _, new = classify_drift.classify(log, {})

        self.assertTrue(new)

    def test_a_log_with_no_ran_line_is_new_drift(self) -> None:
        """The suite died before its summary, whatever the exit said."""
        log = "test_x (tests.test_hermes_contract.T.test_x) ... ok\nEXIT=0\n"

        _, new = classify_drift.classify(log, {})

        self.assertTrue(new)

    def test_a_log_with_no_exit_line_is_new_drift(self) -> None:
        log = "\n" + "-" * 70 + "\nRan 17 tests in 0.601s\n\nOK\n"

        _, new = classify_drift.classify(log, {})

        self.assertTrue(new)

    def test_a_non_zero_exit_with_no_parsed_failure_is_new_drift(self) -> None:
        log = "\n" + "-" * 70 + "\nRan 17 tests in 0.601s\n\nOK\nEXIT=0\nEXIT=1\n"

        _, new = classify_drift.classify(log, {})

        self.assertTrue(new)

    def test_a_clean_run_reports_no_drift(self) -> None:
        log = (
            "test_x (tests.test_hermes_contract.HermesContractTests.test_x) ... ok\n"
            "\n" + "-" * 70 + "\nRan 17 tests in 0.601s\n\nOK\nEXIT=0\nEXIT=0\n"
        )

        self.assertEqual(([], []), classify_drift.classify(log, {}))


if __name__ == "__main__":
    unittest.main()
