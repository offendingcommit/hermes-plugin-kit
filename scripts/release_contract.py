#!/usr/bin/env python3
"""Build and verify hermes-plugin-kit release manifests and receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


PACKAGE_NAME = "hermes-plugin-kit"
TAG_PREFIX = "v"
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REGISTRY_REPOSITORY = "ghcr.io/offendingcommit/hermes-plugin-kit"
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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
        package_info = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and len(PurePosixPath(member.name).parts) == 2
            and PurePosixPath(member.name).name == "PKG-INFO"
        ]
        if len(package_info) != 1:
            raise ReleaseContractError(
                f"{path.name} must contain exactly one top-level PKG-INFO file"
            )
        extracted = archive.extractfile(package_info[0])
        if extracted is None:
            raise ReleaseContractError(f"cannot read metadata from {path.name}")
        metadata = Parser().parsestr(extracted.read().decode("utf-8"))
    return metadata.get("Name", ""), metadata.get("Version", "")


def _artifact_records(artifact_dir: Path, version: str) -> list[dict[str, object]]:
    if artifact_dir.is_symlink():
        raise ReleaseContractError("artifact directory must not be a symlink")
    artifact_dir = artifact_dir.resolve()
    wheels = sorted(artifact_dir.glob("*.whl"))
    sdists = sorted(artifact_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseContractError("dist must contain exactly one wheel and one sdist")
    if {path.name for path in artifact_dir.iterdir()} != {
        path.name for path in (*wheels, *sdists)
    }:
        raise ReleaseContractError("dist contains unexpected files")
    if {path.name for path in (*wheels, *sdists)} != set(artifact_filenames(version)):
        raise ReleaseContractError("dist filenames do not match the release version")

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
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise ReleaseContractError(f"{label} must be a lowercase 40-character Git SHA")


def create_release_manifest(
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
    """Write the schema-2 manifest for already-tested private distributions."""

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
        "schema_version": 2,
        "receipt_state": "tested",
        "package": PACKAGE_NAME,
        "previous_version": previous_version,
        "version": version,
        "tag": tag,
        "source_sha": source_sha,
        "release_class": release_class,
        "promotion": promotion,
        "registry_repository": REGISTRY_REPOSITORY,
        "artifacts": _artifact_records(artifact_dir, version),
        "evidence": {
            "unit": {**passed, "command": "just test"},
            "public_contract": {
                **passed,
                "command": "just test-release (included in just test)",
            },
            "hermes_contract": {
                **passed,
                "command": "just test-contract-pinned",
                "hermes_source_sha": hermes_source_sha,
            },
            "build_metadata": {
                **passed,
                "command": "just build && just check-dist",
            },
            "workflow": {
                "name": workflow,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "source_sha": source_sha,
            },
        },
    }
    validate_release_identity(receipt, state="tested")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def artifact_filenames(version: str) -> tuple[str, str]:
    """The project ships exactly one universal wheel and one source archive."""

    SemVer.parse(version)
    return (
        f"hermes_plugin_kit-{version}-py3-none-any.whl",
        f"hermes_plugin_kit-{version}.tar.gz",
    )


def read_json(path: Path) -> dict[str, object]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseContractError(f"cannot read release JSON: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseContractError("release JSON must be an object")
    return value


def validate_registry_reference(reference: str) -> str:
    prefix = f"{REGISTRY_REPOSITORY}@"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ReleaseContractError("registry reference must name the private repository")
    digest = reference[len(prefix):]
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseContractError("registry reference must pin a SHA-256 digest")
    return digest


def validate_release_identity(receipt: dict[str, object], *, state: str) -> None:
    """Share source, gate, version, and promotion checks across both documents."""

    if receipt.get("schema_version") != 2:
        raise ReleaseContractError("unsupported release receipt schema")
    if receipt.get("receipt_state") != state:
        raise ReleaseContractError(f"expected a {state} release receipt")
    if receipt.get("package") != PACKAGE_NAME:
        raise ReleaseContractError("release receipt package mismatch")
    if receipt.get("registry_repository") != REGISTRY_REPOSITORY:
        raise ReleaseContractError("release receipt registry mismatch")
    expected_keys = {
        "schema_version", "receipt_state", "package", "previous_version", "version",
        "tag", "source_sha", "release_class", "promotion", "registry_repository",
        "artifacts", "evidence",
    }
    if state == "published":
        expected_keys |= {"registry_reference", "payload"}
    if set(receipt) != expected_keys:
        raise ReleaseContractError("release receipt contains unexpected or missing fields")
    version, previous_version = receipt.get("version"), receipt.get("previous_version")
    source_sha = receipt.get("source_sha")
    if not all(isinstance(value, str) for value in (version, previous_version, source_sha)):
        raise ReleaseContractError("release receipt identity is incomplete")
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
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "unit", "public_contract", "hermes_contract", "build_metadata", "workflow",
    }:
        raise ReleaseContractError("release receipt evidence is incomplete")
    commands = {
        "unit": "just test",
        "public_contract": "just test-release (included in just test)",
        "hermes_contract": "just test-contract-pinned",
        "build_metadata": "just build && just check-dist",
    }
    for gate, command in commands.items():
        item = evidence.get(gate)
        if (
            not isinstance(item, dict)
            or item.get("status") != "passed"
            or item.get("result") != "passed"
            or item.get("command") != command
        ):
            raise ReleaseContractError(f"release receipt gate {gate} did not pass")
        if item.get("source_sha") != source_sha:
            raise ReleaseContractError(f"release receipt gate {gate} used another source")
    _validate_sha(evidence["hermes_contract"].get("hermes_source_sha"), "hermes_source_sha")
    workflow = evidence.get("workflow")
    if (
        not isinstance(workflow, dict)
        or workflow.get("name") != "release.yml"
        or not isinstance(workflow.get("run_id"), str)
        or not re.fullmatch(r"[1-9][0-9]*", workflow["run_id"])
        or type(workflow.get("run_attempt")) is not int
        or workflow["run_attempt"] < 1
        or workflow.get("source_sha") != source_sha
    ):
        raise ReleaseContractError("workflow run evidence is incomplete or used another source")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise ReleaseContractError("release receipt must name one wheel and one sdist")
    filenames = set()
    for artifact in artifacts:
        _validate_file_record(artifact, name_key="filename", published=state == "published")
        filenames.add(artifact["filename"])
    if filenames != set(artifact_filenames(version)):
        raise ReleaseContractError("release receipt artifact filenames mismatch")


def _validate_file_record(record, *, name_key: str, published: bool) -> None:
    expected_keys = {name_key, "sha256", "size"}
    if published:
        expected_keys.add("blob_digest")
    if (
        not isinstance(record, dict)
        or set(record) != expected_keys
        or not isinstance(record.get(name_key), str)
        or not isinstance(record.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        or type(record.get("size")) is not int
        or record["size"] < 1
    ):
        raise ReleaseContractError("release file identity is incomplete")
    if published and record["blob_digest"] != f"sha256:{record['sha256']}":
        raise ReleaseContractError("release file blob digest mismatch")


def verify_release_manifest(receipt_path: Path, artifact_dir: Path) -> dict[str, object]:
    """Recompute the tested artifact metadata and bytes instead of trusting it."""

    receipt = read_json(receipt_path)
    validate_release_identity(receipt, state="tested")
    expected_artifacts = _artifact_records(artifact_dir, receipt["version"])
    actual_by_name = {item["filename"]: item for item in receipt["artifacts"]}
    for expected in expected_artifacts:
        actual = actual_by_name[expected["filename"]]
        if actual["sha256"] != expected["sha256"]:
            raise ReleaseContractError(f"SHA-256 mismatch for {expected['filename']}")
        if actual["size"] != expected["size"]:
            raise ReleaseContractError(f"size mismatch for {expected['filename']}")
    return receipt


def verify_final_release_receipt(receipt_path: Path) -> dict[str, object]:
    """Validate a schema-2 private OCI receipt without network access."""

    receipt = read_json(receipt_path)
    validate_release_identity(receipt, state="published")
    validate_registry_reference(receipt.get("registry_reference"))
    payload = receipt.get("payload")
    expected_paths = {
        "release-manifest.json", "release-source.bundle", "release-payload.sha256",
        *(f"dist/{name}" for name in artifact_filenames(receipt["version"])),
    }
    if not isinstance(payload, list) or len(payload) != len(expected_paths):
        raise ReleaseContractError("final release payload inventory is incomplete")
    for item in payload:
        _validate_file_record(item, name_key="path", published=True)
    by_path = {item["path"]: item for item in payload}
    if set(by_path) != expected_paths:
        raise ReleaseContractError("final release payload paths mismatch")
    for artifact in receipt["artifacts"]:
        item = by_path[f"dist/{artifact['filename']}"]
        if any(item[key] != artifact[key] for key in ("sha256", "size", "blob_digest")):
            raise ReleaseContractError("final release artifact and payload disagree")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser("validate-baseline")
    baseline.add_argument("--repository", type=Path, default=Path.cwd())

    history = subparsers.add_parser("validate-history")
    history.add_argument("--repository", type=Path, default=Path.cwd())
    history.add_argument("--baseline-tag", required=True)

    create = subparsers.add_parser("create-manifest")
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

    verify = subparsers.add_parser("verify-manifest")
    verify.add_argument("--receipt", required=True, type=Path)
    verify.add_argument("--artifact-dir", required=True, type=Path)

    final = subparsers.add_parser("verify-final-receipt")
    final.add_argument("--receipt", required=True, type=Path)
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
    if args.command == "create-manifest":
        create_release_manifest(
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
    if args.command == "verify-manifest":
        verify_release_manifest(args.receipt, args.artifact_dir)
        return 0
    if args.command == "verify-final-receipt":
        verify_final_release_receipt(args.receipt)
        return 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseContractError as error:
        raise SystemExit(f"release contract failed: {error}") from error
