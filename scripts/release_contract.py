#!/usr/bin/env python3
"""Build and verify the immutable hermes-plugin-kit release receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import time
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PACKAGE_NAME = "hermes-plugin-kit"
TAG_PREFIX = "v"
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ReleaseContractError(ValueError):
    """Raised when release identity or evidence is incomplete or inconsistent."""


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = SEMVER_PATTERN.fullmatch(value)
        if match is None:
            raise ReleaseContractError(f"invalid stable SemVer: {value!r}")
        return cls(*(int(match[name]) for name in ("major", "minor", "patch")))


@dataclass(frozen=True)
class ReleaseBaseline:
    version: str
    tag: str
    source_sha: str


def _run_git(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ReleaseContractError(detail)
    return result.stdout.strip()


def _project_version(repository: Path) -> str:
    with (repository / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    if project.get("name") != PACKAGE_NAME:
        raise ReleaseContractError(f"expected project name {PACKAGE_NAME!r}")
    version = project.get("version")
    if not isinstance(version, str):
        raise ReleaseContractError("project.version must be a string")
    SemVer.parse(version)
    return version


def validate_release_baseline(repository: Path) -> ReleaseBaseline:
    """Require a clean checkout whose declared version has an ancestral tag."""

    repository = repository.resolve()
    if _run_git(repository, "status", "--porcelain"):
        raise ReleaseContractError("working tree is dirty")
    version = _project_version(repository)
    tag = f"{TAG_PREFIX}{version}"
    tag_sha = _run_git(repository, "rev-list", "-n", "1", tag, check=False)
    if not SHA_PATTERN.fullmatch(tag_sha):
        raise ReleaseContractError(
            f"baseline tag {tag} is missing; seed and review it before enabling releases"
        )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tag_sha, "HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise ReleaseContractError(f"baseline tag {tag} is not an ancestor of HEAD")
    return ReleaseBaseline(version=version, tag=tag, source_sha=tag_sha)


def _synthetic_commit(message: str):
    from git import Actor, Repo
    from git.objects.commit import Commit

    actor = Actor("Release Contract", "release-contract@example.invalid")
    return Commit(
        repo=Repo(),
        binsha=Commit.NULL_BIN_SHA,
        message=message,
        author=actor,
        authored_date=0,
        committer=actor,
        committed_date=0,
        parents=[],
    )


def _classify_commits(commits: Iterable[object]) -> str | None:
    from semantic_release.commit_parser.conventional import ConventionalCommitParser
    from semantic_release.commit_parser.token import ParseError
    from semantic_release.enums import LevelBump

    parser = ConventionalCommitParser()
    strongest = LevelBump.NO_RELEASE
    for commit in commits:
        results = tuple(parser.parse(commit))
        if not results or any(isinstance(result, ParseError) for result in results):
            message = str(getattr(commit, "message", ""))
            subject = message.splitlines()[0] if message else "<empty>"
            raise ReleaseContractError(
                f"invalid conventional commit in release history: {subject!r}"
            )
        strongest = max(strongest, *(result.bump for result in results))
    return {
        LevelBump.NO_RELEASE: None,
        LevelBump.PATCH: "patch",
        LevelBump.MINOR: "minor",
        LevelBump.MAJOR: "major",
    }[strongest]


def classify_commit_messages(messages: Iterable[str]) -> str | None:
    """Exercise PSR's configured conventional parser and return the strongest bump."""

    return _classify_commits(_synthetic_commit(message) for message in messages)


def validate_conventional_history(repository: Path, baseline_tag: str) -> str | None:
    """Reject non-merge commits PSR cannot parse between the baseline and HEAD."""

    from git import Repo

    repository = repository.resolve()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", baseline_tag):
        raise ReleaseContractError(f"invalid baseline tag: {baseline_tag!r}")
    repo = Repo(repository)
    try:
        commits = [
            commit
            for commit in repo.iter_commits(f"{baseline_tag}..HEAD")
            if len(commit.parents) <= 1
        ]
    except Exception as error:
        raise ReleaseContractError(
            f"cannot inspect conventional history from {baseline_tag}: {error}"
        ) from error
    return _classify_commits(reversed(commits))


def _release_class(previous_version: str, version: str) -> str:
    previous = SemVer.parse(previous_version)
    current = SemVer.parse(version)
    if current.major == previous.major + 1 and current.minor == current.patch == 0:
        return "major"
    if (
        current.major == previous.major
        and current.minor == previous.minor + 1
        and current.patch == 0
    ):
        return "minor"
    if (
        current.major == previous.major
        and current.minor == previous.minor
        and current.patch == previous.patch + 1
    ):
        return "patch"
    raise ReleaseContractError(
        f"release {previous_version} -> {version} is not one SemVer bump"
    )


def _metadata_from_wheel(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ReleaseContractError(f"{path.name} must contain exactly one METADATA file")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
    return metadata.get("Name", ""), metadata.get("Version", "")


def _metadata_from_sdist(path: Path) -> tuple[str, str]:
    with tarfile.open(path, mode="r:gz") as archive:
        package_info = [member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")]
        if len(package_info) != 1:
            raise ReleaseContractError(f"{path.name} must contain exactly one PKG-INFO file")
        extracted = archive.extractfile(package_info[0])
        if extracted is None:
            raise ReleaseContractError(f"cannot read metadata from {path.name}")
        metadata = Parser().parsestr(extracted.read().decode("utf-8"))
    return metadata.get("Name", ""), metadata.get("Version", "")


def _artifact_records(artifact_dir: Path, version: str) -> list[dict[str, object]]:
    artifact_dir = artifact_dir.resolve()
    wheels = sorted(artifact_dir.glob("*.whl"))
    sdists = sorted(artifact_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseContractError("dist must contain exactly one wheel and one sdist")

    records: list[dict[str, object]] = []
    for path, metadata_reader in ((wheels[0], _metadata_from_wheel), (sdists[0], _metadata_from_sdist)):
        if not path.is_file() or path.is_symlink():
            raise ReleaseContractError(f"release artifact must be a regular file: {path.name}")
        name, metadata_version = metadata_reader(path)
        if name.lower().replace("_", "-") != PACKAGE_NAME:
            raise ReleaseContractError(f"unexpected package name in {path.name}: {name!r}")
        if metadata_version != version:
            raise ReleaseContractError(
                f"{path.name} metadata has version {metadata_version!r}; expected version {version}"
            )
        records.append(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    return sorted(records, key=lambda item: str(item["filename"]))


def _validate_sha(value: str, label: str) -> None:
    if not SHA_PATTERN.fullmatch(value):
        raise ReleaseContractError(f"{label} must be a lowercase 40-character Git SHA")


def create_release_receipt(
    *,
    previous_version: str,
    version: str,
    tag: str,
    source_sha: str,
    hermes_source_sha: str,
    workflow: str,
    run_id: str,
    run_attempt: int,
    artifact_dir: Path,
    output_path: Path,
) -> dict[str, object]:
    """Write the canonical receipt for the already-tested distribution files."""

    release_class = _release_class(previous_version, version)
    expected_tag = f"{TAG_PREFIX}{version}"
    if tag != expected_tag:
        raise ReleaseContractError(f"tag {tag!r} does not match version {version!r}")
    _validate_sha(source_sha, "source_sha")
    _validate_sha(hermes_source_sha, "hermes_source_sha")
    if not workflow or not run_id or run_attempt < 1:
        raise ReleaseContractError("workflow run evidence is incomplete")

    promotion = (
        {"automatic_candidate": False, "state": "manual_migration_required"}
        if release_class == "major"
        else {"automatic_candidate": True, "state": "automatic_candidate"}
    )
    passed = {"result": "passed", "source_sha": source_sha, "status": "passed"}
    receipt: dict[str, object] = {
        "schema_version": 1,
        "package": PACKAGE_NAME,
        "previous_version": previous_version,
        "version": version,
        "tag": tag,
        "source_sha": source_sha,
        "release_class": release_class,
        "promotion": promotion,
        "registry_url": f"https://pypi.org/project/{PACKAGE_NAME}/{version}/",
        "artifacts": _artifact_records(artifact_dir, version),
        "evidence": {
            "unit": {**passed, "command": "make test"},
            "public_contract": {
                **passed,
                "command": "make test-release (included in make test)",
            },
            "hermes_contract": {
                **passed,
                "command": "make test-contract",
                "hermes_source_sha": hermes_source_sha,
            },
            "build_metadata": {
                **passed,
                "command": "make build && make check-dist",
            },
            "workflow": {
                "name": workflow,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "source_sha": source_sha,
            },
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def verify_release_receipt(receipt_path: Path, artifact_dir: Path) -> dict[str, object]:
    """Recompute identity and hashes instead of trusting receipt assertions."""

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseContractError(f"cannot read release receipt: {error}") from error
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise ReleaseContractError("unsupported release receipt schema")
    if receipt.get("package") != PACKAGE_NAME:
        raise ReleaseContractError("release receipt package mismatch")

    version = receipt.get("version")
    previous_version = receipt.get("previous_version")
    source_sha = receipt.get("source_sha")
    if not all(isinstance(value, str) for value in (version, previous_version, source_sha)):
        raise ReleaseContractError("release receipt identity is incomplete")
    assert isinstance(version, str)
    assert isinstance(previous_version, str)
    assert isinstance(source_sha, str)
    _validate_sha(source_sha, "source_sha")
    release_class = _release_class(previous_version, version)
    if receipt.get("tag") != f"{TAG_PREFIX}{version}":
        raise ReleaseContractError("release receipt tag mismatch")
    if receipt.get("release_class") != release_class:
        raise ReleaseContractError("release receipt class mismatch")

    expected_promotion = (
        {"automatic_candidate": False, "state": "manual_migration_required"}
        if release_class == "major"
        else {"automatic_candidate": True, "state": "automatic_candidate"}
    )
    if receipt.get("promotion") != expected_promotion:
        raise ReleaseContractError("release receipt promotion state mismatch")

    expected_artifacts = _artifact_records(artifact_dir, version)
    actual_artifacts = receipt.get("artifacts")
    if not isinstance(actual_artifacts, list):
        raise ReleaseContractError("release receipt artifacts are missing")
    actual_by_name = {
        item.get("filename"): item
        for item in actual_artifacts
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    for expected in expected_artifacts:
        actual = actual_by_name.get(expected["filename"])
        if actual is None:
            raise ReleaseContractError(f"missing artifact receipt for {expected['filename']}")
        if actual.get("sha256") != expected["sha256"]:
            raise ReleaseContractError(f"SHA-256 mismatch for {expected['filename']}")
        if actual.get("size") != expected["size"]:
            raise ReleaseContractError(f"size mismatch for {expected['filename']}")
    if (
        len(actual_artifacts) != len(expected_artifacts)
        or len(actual_by_name) != len(expected_artifacts)
    ):
        raise ReleaseContractError("release receipt contains unexpected artifacts")

    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict):
        raise ReleaseContractError("release receipt evidence is missing")
    for gate in ("unit", "public_contract", "hermes_contract", "build_metadata"):
        item = evidence.get(gate)
        if (
            not isinstance(item, dict)
            or item.get("status") != "passed"
            or item.get("result") != "passed"
            or not isinstance(item.get("command"), str)
        ):
            raise ReleaseContractError(f"release receipt gate {gate} did not pass")
        if item.get("source_sha") != source_sha:
            raise ReleaseContractError(f"release receipt gate {gate} used another source")
    hermes_sha = evidence["hermes_contract"].get("hermes_source_sha")
    if not isinstance(hermes_sha, str):
        raise ReleaseContractError("Hermes contract source SHA is missing")
    _validate_sha(hermes_sha, "hermes_source_sha")
    return receipt


def _fetch_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "hermes-plugin-kit-release-verifier/1"})
    with urlopen(request, timeout=30) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ReleaseContractError("PyPI returned a non-object release response")
    return value


def _fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "hermes-plugin-kit-release-verifier/1"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def verify_pypi_release(
    receipt_path: Path,
    *,
    fetch_json: Callable[[str], dict[str, object]] = _fetch_json,
    fetch_bytes: Callable[[str], bytes] = _fetch_bytes,
) -> dict[str, object]:
    """Verify PyPI exposes the exact receipt filenames and bytes."""

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseContractError(f"cannot read release receipt: {error}") from error
    if not isinstance(receipt, dict):
        raise ReleaseContractError("release receipt must be an object")
    version = receipt.get("version")
    artifacts = receipt.get("artifacts")
    if not isinstance(version, str) or not isinstance(artifacts, list):
        raise ReleaseContractError("release receipt identity is incomplete")
    SemVer.parse(version)
    expected = {
        item["filename"]: item["sha256"]
        for item in artifacts
        if isinstance(item, dict)
        and isinstance(item.get("filename"), str)
        and isinstance(item.get("sha256"), str)
    }
    if len(expected) != 2:
        raise ReleaseContractError("release receipt must name one wheel and one sdist")

    metadata_url = f"https://pypi.org/pypi/{PACKAGE_NAME}/{version}/json"
    try:
        metadata = fetch_json(metadata_url)
    except Exception as error:
        raise ReleaseContractError(f"cannot fetch PyPI release metadata: {error}") from error
    info = metadata.get("info")
    urls = metadata.get("urls")
    if not isinstance(info, dict) or info.get("version") != version:
        raise ReleaseContractError("PyPI release version does not match the receipt")
    if not isinstance(urls, list):
        raise ReleaseContractError("PyPI release file inventory is missing")
    published = {
        item.get("filename"): item
        for item in urls
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    if set(published) != set(expected):
        raise ReleaseContractError("PyPI filenames do not match the release receipt")

    for filename, expected_sha in expected.items():
        item = published[filename]
        digests = item.get("digests")
        url = item.get("url")
        if item.get("yanked") is True:
            raise ReleaseContractError(f"PyPI artifact is yanked: {filename}")
        if not isinstance(digests, dict) or digests.get("sha256") != expected_sha:
            raise ReleaseContractError(f"PyPI SHA-256 mismatch for {filename}")
        if not isinstance(url, str):
            raise ReleaseContractError(f"PyPI artifact URL is missing for {filename}")
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "files.pythonhosted.org":
            raise ReleaseContractError(f"unexpected PyPI artifact URL for {filename}")
        try:
            published_bytes = fetch_bytes(url)
        except Exception as error:
            raise ReleaseContractError(f"cannot download PyPI artifact {filename}: {error}") from error
        if hashlib.sha256(published_bytes).hexdigest() != expected_sha:
            raise ReleaseContractError(f"downloaded PyPI SHA-256 mismatch for {filename}")
    return metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser("validate-baseline")
    baseline.add_argument("--repository", type=Path, default=Path.cwd())

    history = subparsers.add_parser("validate-history")
    history.add_argument("--repository", type=Path, default=Path.cwd())
    history.add_argument("--baseline-tag", required=True)

    create = subparsers.add_parser("create-receipt")
    create.add_argument("--previous-version", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--tag", required=True)
    create.add_argument("--source-sha", required=True)
    create.add_argument("--hermes-source-sha", required=True)
    create.add_argument("--workflow", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--run-attempt", required=True, type=int)
    create.add_argument("--artifact-dir", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify-receipt")
    verify.add_argument("--receipt", required=True, type=Path)
    verify.add_argument("--artifact-dir", required=True, type=Path)

    pypi = subparsers.add_parser("verify-pypi")
    pypi.add_argument("--receipt", required=True, type=Path)
    pypi.add_argument("--attempts", type=int, default=12)
    pypi.add_argument("--delay-seconds", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-baseline":
        baseline = validate_release_baseline(args.repository)
        print(json.dumps(baseline.__dict__, sort_keys=True))
        return 0
    if args.command == "validate-history":
        release_class = validate_conventional_history(
            args.repository, args.baseline_tag
        )
        print(json.dumps({"release_class": release_class}, sort_keys=True))
        return 0
    if args.command == "create-receipt":
        create_release_receipt(
            previous_version=args.previous_version,
            version=args.version,
            tag=args.tag,
            source_sha=args.source_sha,
            hermes_source_sha=args.hermes_source_sha,
            workflow=args.workflow,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            artifact_dir=args.artifact_dir,
            output_path=args.output,
        )
        return 0
    if args.command == "verify-receipt":
        verify_release_receipt(args.receipt, args.artifact_dir)
        return 0
    if args.attempts < 1 or args.delay_seconds < 0:
        raise ReleaseContractError("PyPI retry settings must be non-negative")
    for attempt in range(1, args.attempts + 1):
        try:
            verify_pypi_release(args.receipt)
            return 0
        except ReleaseContractError:
            if attempt == args.attempts:
                raise
            time.sleep(args.delay_seconds)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseContractError as error:
        raise SystemExit(f"release contract failed: {error}") from error
