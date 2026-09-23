#!/usr/bin/env python3
"""Stage, fetch, and publish exact tested bytes in an independently private GHCR package.

Requires Python 3.11+, git, gh, ORAS 1.3+, and GH_TOKEN (a classic package PAT).
Credentials are passed to ORAS on stdin and kept only in a temporary auth directory.
No operation links the package to the public source repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Sequence

if __package__:
    from .release_contract import (
        DIGEST_PATTERN, REGISTRY_REPOSITORY, ReleaseContractError, artifact_filenames,
        validate_registry_reference, verify_final_release_receipt,
        verify_release_manifest,
    )
else:
    from release_contract import (
        DIGEST_PATTERN, REGISTRY_REPOSITORY, ReleaseContractError, artifact_filenames,
        validate_registry_reference, verify_final_release_receipt,
        verify_release_manifest,
    )

MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
EMPTY_TYPE = "application/vnd.oci.empty.v1+json"
FILE_TYPE = "application/octet-stream"
BUNDLE_TYPE = "application/vnd.hermes-plugin-kit.release.v2"
RECEIPT_TYPE = "application/vnd.hermes-plugin-kit.receipt.v2"
BOOTSTRAP_TYPE = "application/vnd.hermes-plugin-kit.private-bootstrap.v1"
TITLE = "org.opencontainers.image.title"
STATIC_PATHS = {"release-manifest.json", "release-source.bundle", "release-payload.sha256"}
TAG_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _object(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        result = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise ReleaseContractError("invalid registry JSON") from error
    if not isinstance(result, dict):
        raise ReleaseContractError("registry JSON must be an object")
    return result


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _descriptor(data: bytes, media_type: str) -> dict:
    return {"mediaType": media_type, "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}


def _check_descriptor(value: object, *, media_type: str, titled: bool = False) -> dict:
    keys = {"mediaType", "digest", "size"} | ({"annotations"} if titled else set())
    if (
        not isinstance(value, dict) or set(value) != keys
        or value.get("mediaType") != media_type
        or not isinstance(value.get("digest"), str)
        or not DIGEST_PATTERN.fullmatch(value["digest"])
        or type(value.get("size")) is not int or value["size"] < 1
    ):
        raise ReleaseContractError("invalid OCI descriptor")
    if titled and (
        not isinstance(value["annotations"], dict)
        or set(value["annotations"]) != {TITLE}
        or not isinstance(value["annotations"][TITLE], str)
    ):
        raise ReleaseContractError("invalid OCI path annotation")
    return value


def _check_file(path: Path, descriptor: dict) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReleaseContractError("missing or unsafe OCI blob")
    if path.stat().st_size != descriptor["size"]:
        raise ReleaseContractError("OCI blob size mismatch")
    if "sha256:" + _sha256(path) != descriptor["digest"]:
        raise ReleaseContractError("OCI blob SHA-256 mismatch")


def make_layout(directory: Path, paths: dict[str, Path], artifact_type: str) -> str:
    """Write deterministic OCI bytes, with no timestamps, compression, or archives."""

    blobs = directory / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    config = _descriptor(b"{}", EMPTY_TYPE)
    (blobs / config["digest"].split(":")[1]).write_bytes(b"{}")
    layers = []
    for name, path in sorted(paths.items()):
        digest = _sha256(path)
        target = blobs / digest
        shutil.copyfile(path, target)
        descriptor = {
            "mediaType": FILE_TYPE, "digest": "sha256:" + digest,
            "size": path.stat().st_size, "annotations": {TITLE: name},
        }
        _check_file(target, descriptor)
        layers.append(descriptor)
    manifest = _json_bytes({
        "schemaVersion": 2, "mediaType": MANIFEST_TYPE, "artifactType": artifact_type,
        "config": config, "layers": layers,
    })
    descriptor = _descriptor(manifest, MANIFEST_TYPE)
    (blobs / descriptor["digest"].split(":")[1]).write_bytes(manifest)
    (directory / "oci-layout").write_bytes(_json_bytes({"imageLayoutVersion": "1.0.0"}))
    (directory / "index.json").write_bytes(_json_bytes({
        "schemaVersion": 2, "manifests": [{
            **descriptor, "annotations": {"org.opencontainers.image.ref.name": "payload"},
        }],
    }))
    return descriptor["digest"]


class Registry:
    """The only remote transport: authenticated gh metadata and ORAS OCI operations."""

    def __init__(self, repository: str):
        if repository != REGISTRY_REPOSITORY:
            raise ReleaseContractError("only the fixed private registry repository is allowed")
        self.repository = repository
        self._temporary = None
        self.auth = None

    def __enter__(self):
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            raise ReleaseContractError("GH_TOKEN must contain the protected private-package credential")
        self._temporary = tempfile.TemporaryDirectory(prefix="private-release-auth-")
        self.auth = Path(self._temporary.name) / "config.json"
        try:
            # GHCR accepts the credential owner's login; unlike a workflow actor this
            # also works when a maintainer supplies the independent package PAT.
            _, user = self._api("user")
            login = user.get("login")
            if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login):
                raise ReleaseContractError("package credential owner is unknown")
            self._run([
                "oras", "login", "ghcr.io", "--username", login, "--password-stdin",
                "--registry-config", str(self.auth),
            ], input=(token + "\n").encode())
        except BaseException:
            self._temporary.cleanup()
            raise
        return self

    def __exit__(self, *_):
        self._temporary.cleanup()

    @staticmethod
    def _run(args: list[str], *, input: bytes | None = None) -> bytes:
        result = subprocess.run(args, input=input, capture_output=True, check=False)
        if result.returncode:
            # Tool output may contain credentials or private server response data.
            raise ReleaseContractError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
        return result.stdout

    @staticmethod
    def _api(endpoint: str, *, allow_missing: bool = False) -> tuple[int, dict]:
        result = subprocess.run([
            "gh", "api", "--hostname", "github.com", "--include",
            "-H", "Accept: application/vnd.github+json",
            "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint,
        ], capture_output=True, check=False)
        response = result.stdout.replace(b"\r\n", b"\n")
        headers, separator, body = response.partition(b"\n\n")
        match = re.match(rb"HTTP/\S+ (\d{3})\b", headers)
        if not separator or not match:
            raise ReleaseContractError("package API returned no verifiable HTTP status")
        status = int(match[1])
        if status == 404 and allow_missing:
            return status, {}
        if status != 200 or result.returncode:
            raise ReleaseContractError(f"package API failed closed (HTTP {status})")
        return status, _object(body)

    def package_metadata(self) -> dict | None:
        _, owner = self._api("users/offendingcommit")
        owner_type = owner.get("type")
        if owner_type not in {"User", "Organization"}:
            raise ReleaseContractError("package owner type is unknown")
        prefix = "users" if owner_type == "User" else "orgs"
        status, metadata = self._api(
            f"{prefix}/offendingcommit/packages/container/hermes-plugin-kit", allow_missing=True,
        )
        if status == 404:
            return None
        if (
            metadata.get("name") != "hermes-plugin-kit"
            or metadata.get("package_type") != "container"
            or not isinstance(metadata.get("owner"), dict)
            or not isinstance(metadata["owner"].get("login"), str)
            or metadata["owner"].get("login", "").lower() != "offendingcommit"
        ):
            raise ReleaseContractError("package API returned another package identity")
        return metadata

    def require_private(self, *, bootstrap: bool = False) -> None:
        metadata = self.package_metadata()
        if metadata is None and bootstrap:
            # A missing/inaccessible package never authorizes sensitive uploads.
            # The first write contains only {} and public format identifiers. GHCR
            # defaults new packages to private; positive API evidence is still required.
            with tempfile.TemporaryDirectory(prefix="private-bootstrap-") as directory:
                layout = Path(directory) / "layout"
                make_layout(layout, {}, BOOTSTRAP_TYPE)
                self._copy_layout(layout, "private-bootstrap")
            metadata = self.package_metadata()
        if metadata is None or metadata.get("visibility") != "private":
            raise ReleaseContractError("package visibility must be positively verified as private")
        if metadata.get("repository") is not None:
            raise ReleaseContractError("private package must not be linked to a source repository")

    def _copy_layout(self, layout: Path, tag: str) -> None:
        self._run([
            "oras", "cp", "--from-oci-layout", "--to-registry-config", str(self.auth),
            "--no-tty", f"{layout}:payload", f"{self.repository}:{tag}",
        ])

    def resolve(self, tag: str) -> str | None:
        if not TAG_PATTERN.fullmatch(tag):
            raise ReleaseContractError("invalid OCI discovery tag")
        tags = self._run([
            "oras", "repo", "tags", "--registry-config", str(self.auth), self.repository,
        ]).decode().splitlines()
        if tag not in tags:
            return None
        descriptor = self.manifest_descriptor(f"{self.repository}:{tag}")
        return descriptor["digest"]

    def manifest_descriptor(self, reference: str) -> dict:
        value = _object(self._run([
            "oras", "manifest", "fetch", "--descriptor", "--registry-config", str(self.auth), reference,
        ]))
        return _check_descriptor(value, media_type=MANIFEST_TYPE)

    def manifest(self, reference: str) -> bytes:
        return self._run([
            "oras", "manifest", "fetch", "--registry-config", str(self.auth), reference,
        ])

    def blob(self, digest: str, destination: Path) -> None:
        self._run([
            "oras", "blob", "fetch", "--registry-config", str(self.auth), "--output", str(destination),
            f"{self.repository}@{digest}",
        ])

    def upload(self, layout: Path, tag: str, digest: str) -> None:
        self.require_private()
        existing = self.resolve(tag)
        if existing is not None and existing != digest:
            raise ReleaseContractError(f"refusing to replace a different OCI digest at {tag}")
        if existing is None:
            self._copy_layout(layout, tag)
        if self.resolve(tag) != digest:
            raise ReleaseContractError("uploaded OCI discovery tag does not match the tested digest")

    def bind(self, reference: str, tag: str) -> None:
        digest = validate_registry_reference(reference)
        self.require_private()
        existing = self.resolve(tag)
        if existing is not None and existing != digest:
            raise ReleaseContractError(f"refusing to replace a different OCI digest at {tag}")
        if existing is None:
            self._run([
                "oras", "cp", "--from-registry-config", str(self.auth),
                "--to-registry-config", str(self.auth), "--no-tty",
                reference, f"{self.repository}:{tag}",
            ])
        if self.resolve(tag) != digest:
            raise ReleaseContractError("published OCI tag does not match the tested digest")


def _git(repository: Path, *args: str) -> str:
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    result = subprocess.run([
        "git", "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args,
    ], env=environment, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ReleaseContractError(f"source bundle git {args[0]} verification failed")
    return result.stdout.strip()


def _verify_source(bundle: Path, manifest: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="release-source-check-") as directory:
        repository = Path(directory)
        _git(repository, "init", "--bare", "--quiet")
        _git(repository, "bundle", "verify", str(bundle.resolve()))
        heads = _git(repository, "bundle", "list-heads", str(bundle.resolve())).splitlines()
        refs = [line.split(" ", 1)[1] for line in heads if " " in line]
        expected_refs = {"refs/heads/release-candidate", f"refs/tags/{manifest['tag']}"}
        if len(refs) != 2 or set(refs) != expected_refs:
            raise ReleaseContractError("source bundle refs do not match the release identity")
        _git(repository, "fetch", "--quiet", "--no-tags", str(bundle.resolve()), *(
            f"{ref}:{ref}" for ref in sorted(expected_refs)
        ))
        for ref in expected_refs:
            if _git(repository, "rev-parse", f"{ref}^{{commit}}") != manifest["source_sha"]:
                raise ReleaseContractError("source bundle used another source SHA")
        project = tomllib.loads(_git(repository, "show", f"{manifest['source_sha']}:pyproject.toml"))
        if project.get("project", {}).get("version") != manifest["version"]:
            raise ReleaseContractError("source bundle project version mismatch")


def verify_payload(root: Path) -> dict:
    manifest = verify_release_manifest(root / "release-manifest.json", root / "dist")
    expected = {
        "release-source.bundle", "release-manifest.json",
        *(f"dist/{name}" for name in artifact_filenames(manifest["version"])),
    }
    checksums = {}
    try:
        lines = (root / "release-payload.sha256").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseContractError("cannot read release payload checksums") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)", line)
        if not match or match[2] not in expected or match[2] in checksums:
            raise ReleaseContractError("unexpected, unsafe, or duplicate payload checksum path")
        checksums[match[2]] = match[1]
    if set(checksums) != expected:
        raise ReleaseContractError("payload checksum inventory is incomplete")
    for name, digest in checksums.items():
        path = root / name
        if not path.is_file() or path.is_symlink() or _sha256(path) != digest:
            raise ReleaseContractError(f"payload SHA-256 mismatch: {name}")
    _verify_source(root / "release-source.bundle", manifest)
    return manifest


def _safe_layer_name(name: str) -> bool:
    return name in STATIC_PATHS or name == "release-receipt.json" or bool(re.fullmatch(
        r"dist/hermes_plugin_kit-(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-py3-none-any\.whl|\.tar\.gz)", name,
    ))


def _download(registry: Registry, reference: str, root: Path) -> tuple[dict, dict[str, dict]]:
    digest = validate_registry_reference(reference)
    registry.require_private()
    descriptor = registry.manifest_descriptor(reference)
    raw = registry.manifest(reference)
    if len(raw) > 1024 * 1024 or descriptor != _descriptor(raw, MANIFEST_TYPE) or descriptor["digest"] != digest:
        raise ReleaseContractError("OCI manifest digest or size mismatch")
    manifest = _object(raw)
    if (
        set(manifest) != {"schemaVersion", "mediaType", "artifactType", "config", "layers"}
        or manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != MANIFEST_TYPE
        or manifest.get("artifactType") not in {BUNDLE_TYPE, RECEIPT_TYPE}
    ):
        raise ReleaseContractError("unsupported OCI release manifest")
    config = _check_descriptor(manifest.get("config"), media_type=EMPTY_TYPE)
    if config != _descriptor(b"{}", EMPTY_TYPE):
        raise ReleaseContractError("unexpected OCI release config")
    layers = manifest.get("layers")
    count = 5 if manifest["artifactType"] == BUNDLE_TYPE else 1
    if not isinstance(layers, list) or len(layers) != count:
        raise ReleaseContractError("OCI layer inventory is incomplete or contains extra files")
    paths = {}
    for layer in layers:
        _check_descriptor(layer, media_type=FILE_TYPE, titled=True)
        name = layer["annotations"][TITLE]
        if not _safe_layer_name(name) or name in paths:
            raise ReleaseContractError("unsafe or duplicate OCI payload path")
        paths[name] = layer
    if manifest["artifactType"] == RECEIPT_TYPE:
        if set(paths) != {"release-receipt.json"}:
            raise ReleaseContractError("receipt OCI artifact has unexpected paths")
    elif not STATIC_PATHS.issubset(paths) or "release-receipt.json" in paths:
        raise ReleaseContractError("bundle OCI artifact has unexpected paths")
    # Never ask ORAS to extract archives or interpret untrusted layer names.
    root.mkdir(parents=True)
    config_path = root / "config"
    registry.blob(config["digest"], config_path)
    _check_file(config_path, config)
    config_path.unlink()
    for name, layer in paths.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        registry.blob(layer["digest"], target)
        _check_file(target, layer)
    if manifest["artifactType"] == BUNDLE_TYPE:
        release = verify_payload(root)
        if set(paths) != STATIC_PATHS | {f"dist/{name}" for name in artifact_filenames(release["version"])}:
            raise ReleaseContractError("OCI paths do not match the tested manifest")
    else:
        release = verify_final_release_receipt(root / "release-receipt.json")
    return release, paths


def _install(root: Path, paths: dict[str, dict], output: Path) -> None:
    if output.is_symlink():
        raise ReleaseContractError("output directory must not be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    for name in paths:
        target = output / name
        if target.parent.is_symlink() or target.is_symlink():
            raise ReleaseContractError("refusing an unsafe output path")
        if target.exists() and (not target.is_file() or _sha256(target) != _sha256(root / name)):
            raise ReleaseContractError(f"refusing to overwrite different bytes: {name}")
    for name in paths:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(root / name, target)


def fetch_release(registry: Registry, reference: str, output_dir: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="private-release-fetch-") as directory:
        root = Path(directory) / "payload"
        release, paths = _download(registry, reference, root)
        _install(root, paths, output_dir)
        return release


def stage_release(
    registry: Registry, *, tag: str, manifest: Path, artifact_dir: Path,
    source_bundle: Path, payload_checksums: Path, output: Path,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="private-release-stage-") as directory:
        root = Path(directory)
        payload = root / "payload"
        (payload / "dist").mkdir(parents=True)
        inputs = {
            "release-manifest.json": manifest, "release-source.bundle": source_bundle,
            "release-payload.sha256": payload_checksums,
        }
        if artifact_dir.is_symlink():
            raise ReleaseContractError("artifact directory must not be a symlink")
        inputs.update({f"dist/{path.name}": path for path in artifact_dir.iterdir()})
        for name, path in inputs.items():
            if not path.is_file() or path.is_symlink() or not _safe_layer_name(name):
                raise ReleaseContractError("stage input contains an unexpected or unsafe file")
            shutil.copyfile(path, payload / name)
        release = verify_payload(payload)
        expected_tag = f"candidate-{release['evidence']['workflow']['run_id']}-{release['source_sha']}"
        if tag != expected_tag:
            raise ReleaseContractError("candidate tag does not match the tested run and source")
        layout = root / "layout"
        digest = make_layout(layout, {name: payload / name for name in inputs}, BUNDLE_TYPE)
        registry.require_private(bootstrap=True)
        registry.upload(layout, tag, digest)
        reference = f"{registry.repository}@{digest}"
        _, paths = _download(registry, reference, root / "verified")
        for name in paths:
            if _sha256(root / "verified" / name) != _sha256(payload / name):
                raise ReleaseContractError("staged bytes differ from the tested payload")
        result = {"bundle_reference": reference}
        output.write_bytes(_json_bytes(result))
        return result


def publish_release(registry: Registry, reference: str, output: Path, references: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="private-release-publish-") as directory:
        root = Path(directory)
        payload = root / "payload"
        release, paths = _download(registry, reference, payload)
        if release["receipt_state"] != "tested":
            raise ReleaseContractError("publication requires a tested bundle, not a receipt")
        registry.bind(reference, release["tag"])
        # Discovery binds the same OCI manifest, never a rebuilt/repacked distribution.
        confirmed, confirmed_paths = _download(registry, reference, root / "confirmed")
        if confirmed != release or confirmed_paths != paths:
            raise ReleaseContractError("registry changed the published release payload")
        receipt = {**release, "receipt_state": "published", "registry_reference": reference}
        receipt["artifacts"] = [
            {**item, "blob_digest": paths[f"dist/{item['filename']}"]["digest"]}
            for item in release["artifacts"]
        ]
        receipt["payload"] = [
            {"path": name, "sha256": layer["digest"].split(":")[1],
             "size": layer["size"], "blob_digest": layer["digest"]}
            for name, layer in sorted(paths.items())
        ]
        receipt_path = root / "release-receipt.json"
        receipt_path.write_bytes(_json_bytes(receipt))
        verify_final_release_receipt(receipt_path)
        layout = root / "receipt-layout"
        digest = make_layout(layout, {"release-receipt.json": receipt_path}, RECEIPT_TYPE)
        registry.upload(layout, release["tag"] + "-receipt", digest)
        receipt_reference = f"{registry.repository}@{digest}"
        _download(registry, receipt_reference, root / "verified-receipt")
        if _sha256(root / "verified-receipt" / "release-receipt.json") != _sha256(receipt_path):
            raise ReleaseContractError("registry changed the published receipt bytes")
        # No receipt self-reference: its OCI digest belongs only in the publication index.
        result = {"bundle_reference": reference, "receipt_reference": receipt_reference}
        output.write_bytes(receipt_path.read_bytes())
        references.write_bytes(_json_bytes(result))
        return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-private")
    check.add_argument("--repository", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--repository", required=True)
    stage.add_argument("--tag", required=True)
    stage.add_argument("--manifest", required=True, type=Path)
    stage.add_argument("--artifact-dir", required=True, type=Path)
    stage.add_argument("--source-bundle", required=True, type=Path)
    stage.add_argument("--payload-checksums", required=True, type=Path)
    stage.add_argument("--output", required=True, type=Path)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--reference", required=True)
    fetch.add_argument("--output-dir", required=True, type=Path)
    publish = commands.add_parser("publish")
    publish.add_argument("--reference", required=True)
    publish.add_argument("--output", required=True, type=Path)
    publish.add_argument("--references", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command in {"fetch", "publish"}:
        validate_registry_reference(args.reference)
        repository = REGISTRY_REPOSITORY
    else:
        repository = args.repository
    with Registry(repository) as registry:
        if args.command == "check-private":
            registry.require_private(bootstrap=True)
        elif args.command == "stage":
            stage_release(
                registry, tag=args.tag, manifest=args.manifest, artifact_dir=args.artifact_dir,
                source_bundle=args.source_bundle, payload_checksums=args.payload_checksums, output=args.output,
            )
        elif args.command == "fetch":
            fetch_release(registry, args.reference, args.output_dir)
        else:
            publish_release(registry, args.reference, args.output, args.references)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseContractError, OSError, ValueError) as error:
        raise SystemExit(f"private release failed: {error}") from error
