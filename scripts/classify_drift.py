#!/usr/bin/env python3
"""Sort contract failures into already-known and genuinely new.

The drift lane launches knowingly red: the #99220 relay-egress break is out of
scope and stays failing. An unclassified report would therefore name the same
failure every run, and a channel that is always red trains people to stop
reading it -- so the next real break arrives invisible.

Matching is on a fingerprint of the failure text, not the test id alone. A
baselined test whose assertion or error has changed is a new break wearing a
known name.

Reads a unittest log on stdin or as a path; writes a GitHub-flavoured summary
to stdout. Exit status is 0 when nothing unrecognized appeared, 1 otherwise, so
a caller can branch on "is there new drift" rather than "did anything fail".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "known-hermes-drift.txt"

# unittest prints: "FAIL: <name> (<full.dotted.id>)", optionally a docstring
# line, then a dashed rule, then the body. The parenthesised value is already
# the full id, so it is taken verbatim rather than rebuilt from parts.
_BLOCK = re.compile(
    r"^(?:FAIL|ERROR): \S+ \((?P<test_id>[^)]+)\)\n"
    # [^\n] not . -- re.S is on for the body, and a dotall `.*` here
    # would run straight past the separator it is meant to stop at.
    r"(?:(?!-{10,})[^\n]*\n)*?"
    r"-{10,}\n"
    r"(?P<body>.*?)(?=\n={10,}|\n-{10,}\n|\Z)",
    re.M | re.S,
)


def load_baseline(path: Path = BASELINE) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        test_id, _, fingerprint = line.partition(" ")
        entries[test_id.strip()] = fingerprint.strip()
    return entries


#: Text that differs between machines and runs, and so must never reach a
#: fingerprint. A baseline is committed once and matched everywhere; if it
#: absorbed a home directory, a temp path, a port, or a PID, it would match on
#: the machine that produced it and nowhere else.
_VOLATILE = (
    # Windows and POSIX absolute paths, including UNC.
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"]*"), " path "),
    (re.compile(r"(?<![\w])/[^\s'\"]*"), " path "),
    # Hex object addresses and sha-like runs.
    (re.compile(r"0x[0-9a-fA-F]+"), " addr "),
    (re.compile(r"\b[0-9a-f]{7,}\b"), " hash "),
    # Ports, PIDs, line numbers, byte counts.
    (re.compile(r"\b\d{2,}\b"), " n "),
)


def normalize(text: str) -> str:
    """Strip everything that varies by machine or run."""
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def fingerprint(body: str) -> str:
    """A short digest of what went wrong, stable across machines.

    Uses the assertion or error text rather than the whole traceback, so a
    shifting line number does not read as a new break while a changed message
    does -- and normalizes the parts that differ per machine, so a baseline
    committed from a laptop still matches in CI.
    """
    for line in reversed(body.strip().splitlines()):
        line = line.strip()
        if not line or line.startswith(("File \"", "Traceback", "~", "^")):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", normalize(line).lower()).strip("-")
        return slug[:80] or "unknown"
    return "unknown"


def classify(log: str, baseline: dict[str, str]) -> tuple[list[str], list[str]]:
    known: list[str] = []
    new: list[str] = []
    for match in _BLOCK.finditer(log):
        test_id = match.group("test_id")
        print_id = test_id
        fp = fingerprint(match.group("body"))
        expected = baseline.get(test_id)
        if expected and expected in fp:
            known.append(f"{print_id} ({fp})")
        else:
            reason = "not in the baseline" if expected is None else (
                f"baselined as {expected!r} but failed as {fp!r}"
            )
            new.append(f"{print_id} — {reason}")
    return known, new


def main() -> int:
    log = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    revision = (sys.argv[2] if len(sys.argv) > 2 else "unknown").strip()
    known, new = classify(log, load_baseline())

    out = [f"## Hermes upstream drift\n", f"Exercised hermes-agent `{revision}`.\n"]
    if new:
        out.append(f"### New drift ({len(new)})\n")
        out.append("These are not in the known-drift baseline, or their failure changed.\n")
        out += [f"- `{n}`" for n in new]
        out.append("")
    if known:
        out.append(f"### Already known ({len(known)})\n")
        out += [f"- `{k}`" for k in known]
        out.append("")
    if not known and not new:
        out.append("No contract failures against upstream.\n")
    print("\n".join(out))
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main())
