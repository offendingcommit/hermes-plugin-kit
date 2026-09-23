from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import copy
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.release_contract import (
    REGISTRY_REPOSITORY,
    ReleaseContractError,
    classify_commit_messages,
    create_release_manifest,
    validate_conventional_history,
    validate_release_baseline,
    verify_final_release_receipt,
    verify_release_manifest,
)
from scripts.private_release import (
    BOOTSTRAP_TYPE, BUNDLE_TYPE, MANIFEST_TYPE, TITLE, Registry,
    fetch_release, make_layout, publish_release, stage_release,
)


ROOT = Path(__file__).resolve().parents[1]


class ConventionalReleaseIntentTests(unittest.TestCase):
    def test_fix_history_requests_patch_release(self) -> None:
        self.assertEqual(classify_commit_messages(["fix: keep schemas nested"]), "patch")

    def test_feature_history_requests_minor_release(self) -> None:
        self.assertEqual(
            classify_commit_messages(["fix: keep schemas nested", "feat: add hook helper"]),
            "minor",
        )

    def test_breaking_history_requests_major_release(self) -> None:
        self.assertEqual(
            classify_commit_messages(
                ["feat!: replace the registration receipt contract"]
            ),
            "major",
        )

    def test_docs_only_history_does_not_request_release(self) -> None:
        self.assertIsNone(classify_commit_messages(["docs: explain plugin ranges"]))

    def test_invalid_commit_fails_closed(self) -> None:
        with self.assertRaisesRegex(ReleaseContractError, "invalid conventional commit"):
            classify_commit_messages(["updated stuff"])


class ReleaseBaselineTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _repository(self, *, tag: bool) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name)
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "Release Contract")
        self._git(repo, "config", "user.email", "release-contract@example.invalid")
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "hermes-plugin-kit"\nversion = "0.7.0"\n',
            encoding="utf-8",
        )
        self._git(repo, "add", "pyproject.toml")
        self._git(repo, "commit", "-q", "-m", "chore: establish baseline")
        if tag:
            self._git(repo, "tag", "v0.7.0")
        return temporary

    def test_missing_baseline_tag_is_rejected(self) -> None:
        with self._repository(tag=False) as directory:
            with self.assertRaisesRegex(ReleaseContractError, "baseline tag v0.7.0"):
                validate_release_baseline(Path(directory))

    def test_existing_version_tag_must_be_clean_and_ancestral(self) -> None:
        with self._repository(tag=True) as directory:
            repo = Path(directory)
            (repo / "change.txt").write_text("feature\n", encoding="utf-8")
            self._git(repo, "add", "change.txt")
            self._git(repo, "commit", "-q", "-m", "feat: add a feature")

            baseline = validate_release_baseline(repo)
            self.assertEqual(baseline.version, "0.7.0")
            self.assertEqual(baseline.tag, "v0.7.0")
            self.assertEqual(len(baseline.source_sha), 40)

            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseContractError, "working tree is dirty"):
                validate_release_baseline(repo)

    def test_invalid_non_merge_commit_in_history_is_rejected(self) -> None:
        with self._repository(tag=True) as directory:
            repo = Path(directory)
            (repo / "change.txt").write_text("invalid\n", encoding="utf-8")
            self._git(repo, "add", "change.txt")
            self._git(repo, "commit", "-q", "-m", "updated stuff")

            with self.assertRaisesRegex(ReleaseContractError, "invalid conventional commit"):
                validate_conventional_history(repo, "v0.7.0")

    def test_valid_history_returns_strongest_release_intent(self) -> None:
        with self._repository(tag=True) as directory:
            repo = Path(directory)
            (repo / "change.txt").write_text("feature\n", encoding="utf-8")
            self._git(repo, "add", "change.txt")
            self._git(repo, "commit", "-q", "-m", "feat: add a feature")
            (repo / "change.txt").write_text("fix\n", encoding="utf-8")
            self._git(repo, "add", "change.txt")
            self._git(repo, "commit", "-q", "-m", "fix: correct the feature")

            self.assertEqual(validate_conventional_history(repo, "v0.7.0"), "minor")


class DistributionFixture:
    VERSION = "0.8.0"
    SOURCE_SHA = "a" * 40
    HERMES_SHA = "b" * 40

    def _write_dist(self, directory: Path, version: str = VERSION) -> None:
        dist_info = f"hermes_plugin_kit-{version}.dist-info"
        metadata = (
            "Metadata-Version: 2.4\n"
            "Name: hermes-plugin-kit\n"
            f"Version: {version}\n\n"
        )
        wheel = directory / f"hermes_plugin_kit-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(f"{dist_info}/METADATA", metadata)

        source_root = f"hermes_plugin_kit-{version}"
        package_info = directory / "PKG-INFO"
        package_info.write_text(metadata, encoding="utf-8")
        sdist = directory / f"{source_root}.tar.gz"
        with tarfile.open(sdist, "w:gz") as archive:
            archive.add(package_info, arcname=f"{source_root}/PKG-INFO")
            archive.add(
                package_info,
                arcname=f"{source_root}/hermes_plugin_kit.egg-info/PKG-INFO",
            )
        package_info.unlink()

    def _create(self, dist: Path, output: Path, *, previous: str = "0.7.0") -> dict:
        return create_release_manifest(
            previous_version=previous,
            version=self.VERSION,
            tag=f"v{self.VERSION}",
            source_sha=self.SOURCE_SHA,
            hermes_source_sha=self.HERMES_SHA,
            workflow="release.yml",
            run_id="12345",
            run_attempt=1,
            artifact_dir=dist,
            output_path=output,
        )


class ReleaseReceiptTests(DistributionFixture, unittest.TestCase):
    def test_receipt_binds_artifact_metadata_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)

            receipt = self._create(dist, root / "release-receipt.json")

            self.assertEqual(receipt["version"], self.VERSION)
            self.assertEqual(receipt["tag"], f"v{self.VERSION}")
            self.assertEqual(receipt["source_sha"], self.SOURCE_SHA)
            self.assertEqual(receipt["release_class"], "minor")
            self.assertEqual(
                receipt["promotion"],
                {"automatic_candidate": True, "state": "automatic_candidate"},
            )
            self.assertEqual(
                [artifact["filename"] for artifact in receipt["artifacts"]],
                [
                    f"hermes_plugin_kit-{self.VERSION}-py3-none-any.whl",
                    f"hermes_plugin_kit-{self.VERSION}.tar.gz",
                ],
            )
            for artifact in receipt["artifacts"]:
                expected = hashlib.sha256(
                    (dist / artifact["filename"]).read_bytes()
                ).hexdigest()
                self.assertEqual(artifact["sha256"], expected)
            self.assertEqual(
                receipt["evidence"]["hermes_contract"]["hermes_source_sha"],
                self.HERMES_SHA,
            )
            for gate in ("unit", "public_contract", "hermes_contract", "build_metadata"):
                self.assertEqual(receipt["evidence"][gate]["source_sha"], self.SOURCE_SHA)
                self.assertEqual(receipt["evidence"][gate]["result"], "passed")
                self.assertTrue(receipt["evidence"][gate]["command"])
            self.assertEqual(receipt["receipt_state"], "tested")
            self.assertEqual(
                verify_release_manifest(root / "release-receipt.json", dist), receipt
            )

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            receipt_path = root / "release-receipt.json"
            receipt = self._create(dist, receipt_path)
            artifact = dist / receipt["artifacts"][0]["filename"]
            artifact.write_bytes(artifact.read_bytes() + b"tampered")

            with self.assertRaises(ReleaseContractError):
                verify_release_manifest(receipt_path, dist)

    def test_major_receipt_is_never_automatic_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist, version="1.0.0")

            receipt = create_release_manifest(
                previous_version="0.7.0",
                version="1.0.0",
                tag="v1.0.0",
                source_sha=self.SOURCE_SHA,
                hermes_source_sha=self.HERMES_SHA,
                workflow="release.yml",
                run_id="12345",
                run_attempt=1,
                artifact_dir=dist,
                output_path=root / "release-receipt.json",
            )

            self.assertEqual(receipt["release_class"], "major")
            self.assertEqual(
                receipt["promotion"],
                {
                    "automatic_candidate": False,
                    "state": "manual_migration_required",
                },
            )

    def test_package_metadata_version_must_match_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist, version="0.7.1")

            for path in tuple(dist.iterdir()):
                path.rename(path.with_name(path.name.replace("0.7.1", self.VERSION)))
            with self.assertRaises(ReleaseContractError):
                self._create(dist, root / "release-receipt.json")



class ReleaseWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        cls.release_config = yaml.load(cls.workflow, Loader=yaml.BaseLoader)

    def test_immutable_control_requires_credentials_and_positive_api_evidence(self) -> None:
        step = next(
            step
            for step in self.release_config["jobs"]["immutable-release-control"]["steps"]
            if step.get("id") == "verify"
        )
        for token, enabled, api_status, admitted in (
            ("", "true", "0", False),
            ("test-credential", "false", "0", False),
            ("test-credential", "true", "17", False),
            ("test-credential", "true", "0", True),
        ):
            with self.subTest(token_present=bool(token), enabled=enabled, api_status=api_status):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    output = root / "output"
                    probe = root / "api-called"
                    result = subprocess.run(
                        [
                            "bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
                            'gh() { printf called > "$PROBE"; '
                            'printf "%s\\n" "$CONTROL_VALUE"; return "$CONTROL_STATUS"; }\n'
                            + step["run"],
                        ],
                        env={
                            "PATH": os.defpath,
                            "GH_TOKEN": token,
                            "GITHUB_REPOSITORY": "example/kit",
                            "GITHUB_OUTPUT": str(output),
                            "PROBE": str(probe),
                            "CONTROL_VALUE": enabled,
                            "CONTROL_STATUS": api_status,
                        },
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode == 0, admitted)
                    self.assertEqual(probe.exists(), bool(token))
                    self.assertEqual(
                        output.read_text() if output.exists() else "",
                        "enabled=true\n" if admitted else "",
                    )

    def test_missing_promotion_credential_stops_before_payload_or_git_operations(self) -> None:
        step = self.release_config["jobs"]["promote-source"]["steps"][-1]
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "payload-reached"
            result = subprocess.run(
                [
                    "bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
                    'sha256sum() { printf reached > "$PROBE"; return 1; }\n' + step["run"],
                ],
                cwd=directory,
                env={"PATH": os.defpath, "GH_TOKEN": "", "PROBE": str(probe)},
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(probe.exists())



    def test_packaging_manifest_covers_every_package_directory(self) -> None:
        """The trap this guards is a subpackage silently dropped from the wheel.

        `pyproject.toml` lists packages explicitly, so a new package directory
        is omitted from the build with no warning and `twine check` -- which
        reads metadata, never the archive -- passes on the result.

        This asserts the manifest against the source tree rather than against
        a built wheel. An earlier version inspected `dist/`, which made it
        depend on whatever happened to be lying there: it passed in CI, where
        `dist/` is absent and the test skipped, and failed locally against a
        stale artifact. Worse, mtime could not distinguish stale from wrong --
        the wheel that caught this was *newer* than the source and still built
        from a mutated tree. End-to-end proof belongs in `just check-install`,
        which builds, installs and imports; this is the fast, deterministic
        half.
        """
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = set(manifest["tool"]["setuptools"]["packages"])

        package_root = ROOT / "hermes_plugin_kit"
        on_disk = {"hermes_plugin_kit"} | {
            ".".join(child.relative_to(ROOT).parts)
            for child in package_root.rglob("*")
            if child.is_dir() and (child / "__init__.py").exists()
        }

        missing = sorted(on_disk - declared)
        self.assertEqual(
            [], missing,
            "package directories absent from pyproject's explicit packages list "
            "are dropped from the wheel silently; twine check will not notice",
        )


class MemoryRegistry(Registry):
    """Exercise production OCI logic with an in-memory remote transport."""

    def __init__(self, metadata=None):
        super().__init__(REGISTRY_REPOSITORY)
        self.metadata = {"visibility": "private", "repository": None} if metadata is None else metadata
        self.after_bootstrap = {"visibility": "private", "repository": None}
        self.tags = {}
        self.manifests = {}
        self.blobs = {}
        self.uploads = []
        self.copies = []

    def package_metadata(self):
        return self.metadata

    def _copy_layout(self, layout, tag):
        index = json.loads((layout / "index.json").read_bytes())
        descriptor = index["manifests"][0]
        digest = descriptor["digest"]
        raw = (layout / "blobs" / "sha256" / digest.split(":")[1]).read_bytes()
        manifest = json.loads(raw)
        self.manifests[digest] = raw
        for item in [manifest["config"], *manifest["layers"]]:
            self.blobs[item["digest"]] = (
                layout / "blobs" / "sha256" / item["digest"].split(":")[1]
            ).read_bytes()
        self.tags[tag] = digest
        self.uploads.append(manifest)
        if manifest["artifactType"] == BOOTSTRAP_TYPE:
            self.metadata = self.after_bootstrap

    def resolve(self, tag):
        return self.tags.get(tag)

    def manifest(self, reference):
        return self.manifests[reference.split("@", 1)[1]]

    def manifest_descriptor(self, reference):
        raw = self.manifest(reference)
        return {
            "mediaType": MANIFEST_TYPE, "digest": reference.split("@", 1)[1], "size": len(raw),
        }

    def blob(self, digest, destination):
        if digest not in self.blobs:
            raise ReleaseContractError("remote blob missing")
        destination.write_bytes(self.blobs[digest])

    def _run(self, args, **kwargs):
        if args[:2] != ["oras", "cp"]:
            raise AssertionError(f"unexpected transport operation: {args[:2]}")
        reference, target = args[-2:]
        self.tags[target.rsplit(":", 1)[1]] = reference.split("@", 1)[1]
        self.copies.append((reference, target))
        return b""

    def change_manifest(self, reference, mutate):
        manifest = json.loads(self.manifest(reference))
        mutate(manifest)
        raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        self.manifests[digest] = raw
        return f"{self.repository}@{digest}"


class PrivateArtifactTests(DistributionFixture, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self._write_dist(self.dist)
        repository = self.root / "source"
        repository.mkdir()
        (repository / "pyproject.toml").write_text(
            f'[project]\nname = "hermes-plugin-kit"\nversion = "{self.VERSION}"\n'
        )
        environment = {
            **os.environ, "GIT_AUTHOR_NAME": "Release Test", "GIT_AUTHOR_EMAIL": "release@example.invalid",
            "GIT_COMMITTER_NAME": "Release Test", "GIT_COMMITTER_EMAIL": "release@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        }

        def git(*args):
            return subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args],
                check=True, capture_output=True, text=True, env=environment,
            ).stdout.strip()

        git("init", "--quiet")
        git("add", "pyproject.toml")
        git("commit", "--quiet", "-m", "feat: add tested source")
        self.SOURCE_SHA = git("rev-parse", "HEAD")
        git("branch", "release-candidate")
        git("tag", f"v{self.VERSION}")
        self.bundle = self.root / "release-source.bundle"
        git("bundle", "create", str(self.bundle), "refs/heads/release-candidate", f"refs/tags/v{self.VERSION}")
        self.manifest_path = self.root / "release-manifest.json"
        self.manifest = self._create(self.dist, self.manifest_path)
        self.checksums = self.root / "release-payload.sha256"
        self._checksums()
        self.registry = MemoryRegistry()

    def _checksums(self):
        paths = ["release-source.bundle", "release-manifest.json"] + [
            f"dist/{artifact['filename']}" for artifact in self.manifest["artifacts"]
        ]
        self.checksums.write_text("".join(
            f"{hashlib.sha256((self.root / path).read_bytes()).hexdigest()}  {path}\n"
            for path in paths
        ))

    def _stage(self):
        return stage_release(
            self.registry, tag=f"candidate-12345-{self.SOURCE_SHA}",
            manifest=self.manifest_path, artifact_dir=self.dist, source_bundle=self.bundle,
            payload_checksums=self.checksums, output=self.root / "staged-release.json",
        )["bundle_reference"]

    def _publish(self, reference):
        return publish_release(
            self.registry, reference, self.root / "release-receipt.json", self.root / "publication.json",
        )

    def test_private_roundtrip_preserves_every_tested_byte_and_receipt_evidence(self):
        reference = self._stage()
        output = self.root / "fetched"
        fetch_release(self.registry, reference, output)
        paths = [
            "release-source.bundle", "release-manifest.json", "release-payload.sha256",
            *(f"dist/{item['filename']}" for item in self.manifest["artifacts"]),
        ]
        for name in paths:
            self.assertEqual((output / name).read_bytes(), (self.root / name).read_bytes())
        publication = self._publish(reference)
        receipt = verify_final_release_receipt(self.root / "release-receipt.json")
        self.assertEqual(receipt["registry_reference"], reference)
        self.assertEqual(receipt["evidence"], self.manifest["evidence"])
        self.assertEqual(receipt["source_sha"], self.SOURCE_SHA)
        self.assertEqual(self.registry.tags[f"v{self.VERSION}"], reference.split("@")[1])
        for item in receipt["payload"]:
            payload = (self.root / item["path"]).read_bytes()
            self.assertEqual(item["blob_digest"], "sha256:" + hashlib.sha256(payload).hexdigest())
            self.assertEqual(item["size"], len(payload))
        receipt_output = self.root / "consumer-receipt"
        fetch_release(self.registry, publication["receipt_reference"], receipt_output)
        self.assertEqual(
            (receipt_output / "release-receipt.json").read_bytes(),
            (self.root / "release-receipt.json").read_bytes(),
        )
        self.assertEqual(
            json.loads((self.root / "publication.json").read_bytes()), publication,
        )

    def test_retries_reuse_identical_digests_without_reuploading(self):
        reference = self._stage()
        first = self._publish(reference)
        receipt = (self.root / "release-receipt.json").read_bytes()
        upload_count, copy_count = len(self.registry.uploads), len(self.registry.copies)
        for path in self.dist.iterdir():
            os.utime(path, (1, 1))
        self.assertEqual(self._stage(), reference)
        self.assertEqual(self._publish(reference), first)
        self.assertEqual((self.root / "release-receipt.json").read_bytes(), receipt)
        self.assertEqual(len(self.registry.uploads), upload_count)
        self.assertEqual(len(self.registry.copies), copy_count)

    def test_existing_version_with_different_digest_is_never_replaced(self):
        reference = self._stage()
        previous = "sha256:" + "0" * 64
        self.registry.tags[f"v{self.VERSION}"] = previous
        with self.assertRaises(ReleaseContractError):
            self._publish(reference)
        self.assertEqual(self.registry.tags[f"v{self.VERSION}"], previous)
        self.assertEqual(self.registry.copies, [])
        self.assertFalse((self.root / "publication.json").exists())

    def test_existing_receipt_with_different_digest_is_never_replaced(self):
        reference = self._stage()
        previous = "sha256:" + "0" * 64
        self.registry.tags[f"v{self.VERSION}-receipt"] = previous
        with self.assertRaises(ReleaseContractError):
            self._publish(reference)
        self.assertEqual(self.registry.tags[f"v{self.VERSION}-receipt"], previous)
        self.assertFalse((self.root / "publication.json").exists())

    def test_existing_candidate_with_different_bytes_is_not_replaced(self):
        reference = self._stage()
        self.manifest["evidence"]["workflow"]["run_attempt"] = 2
        self.manifest_path.write_text(json.dumps(self.manifest))
        self._checksums()
        with self.assertRaises(ReleaseContractError):
            self._stage()
        self.assertEqual(
            self.registry.tags[f"candidate-12345-{self.SOURCE_SHA}"], reference.split("@")[1],
        )

    def test_private_guard_precedes_any_sensitive_upload(self):
        for metadata in (
            {"visibility": "public"}, {"visibility": "internal"}, {},
            {"visibility": "private", "repository": {"full_name": "offendingcommit/hermes-plugin-kit"}},
        ):
            with self.subTest(metadata=metadata):
                self.registry = MemoryRegistry(metadata)
                with self.assertRaises(ReleaseContractError):
                    self._stage()
                self.assertEqual(self.registry.uploads, [])

    def test_first_package_bootstrap_contains_no_payload_and_requires_positive_recheck(self):
        for after in (None, {"visibility": "public"}, {"visibility": "internal"}, {}):
            with self.subTest(after=after):
                self.registry = MemoryRegistry()
                self.registry.metadata = None
                self.registry.after_bootstrap = after
                with self.assertRaises(ReleaseContractError):
                    self._stage()
                self.assertEqual(len(self.registry.uploads), 1)
                bootstrap = self.registry.uploads[0]
                self.assertEqual(bootstrap["artifactType"], BOOTSTRAP_TYPE)
                self.assertEqual(bootstrap["layers"], [])
                self.assertEqual(set(self.registry.blobs.values()), {b"{}"})
        self.registry = MemoryRegistry()
        self.registry.metadata = None
        reference = self._stage()
        self.assertEqual(
            [item["artifactType"] for item in self.registry.uploads], [BOOTSTRAP_TYPE, BUNDLE_TYPE],
        )
        self.assertEqual(fetch_release(self.registry, reference, self.root / "bootstrap-result"), self.manifest)

    def test_read_only_fetch_never_bootstraps_a_missing_package(self):
        reference = self._stage()
        self.registry.metadata = None
        count = len(self.registry.uploads)
        with self.assertRaises(ReleaseContractError):
            fetch_release(self.registry, reference, self.root / "missing-package")
        self.assertEqual(len(self.registry.uploads), count)
        self.assertFalse((self.root / "missing-package").exists())

    def test_source_bundle_must_match_the_claimed_source_even_with_valid_checksums(self):
        self.manifest["source_sha"] = "c" * 40
        for gate in self.manifest["evidence"].values():
            gate["source_sha"] = "c" * 40
        self.manifest_path.write_text(json.dumps(self.manifest))
        self._checksums()
        with self.assertRaises(ReleaseContractError):
            self._stage()
        self.assertEqual(self.registry.uploads, [])

    def test_wrong_gate_or_workflow_source_cannot_be_staged(self):
        for gate in ("unit", "public_contract", "hermes_contract", "build_metadata", "workflow"):
            with self.subTest(gate=gate):
                altered = copy.deepcopy(self.manifest)
                altered["evidence"][gate]["source_sha"] = "c" * 40
                self.manifest_path.write_text(json.dumps(altered))
                self._checksums()
                with self.assertRaises(ReleaseContractError):
                    self._stage()
                self.assertEqual(self.registry.uploads, [])

    def test_unpassed_gate_cannot_be_staged(self):
        self.manifest["evidence"]["hermes_contract"]["result"] = "failed"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self._checksums()
        with self.assertRaises(ReleaseContractError):
            self._stage()
        self.assertEqual(self.registry.uploads, [])

    def test_checksum_inventory_must_be_exact_and_safe(self):
        original = self.checksums.read_text()
        for content in (
            original.split("\n", 1)[1],
            original + original.splitlines()[0] + "\n",
            original + "0" * 64 + "  ../secret\n",
        ):
            with self.subTest(content=content):
                self.checksums.write_text(content)
                with self.assertRaises(ReleaseContractError):
                    self._stage()
                self.assertEqual(self.registry.uploads, [])

    def test_fetch_rejects_tampered_missing_extra_or_unsafe_oci_content_before_output(self):
        reference = self._stage()
        original_manifests = copy.deepcopy(self.registry.manifests)
        original_blobs = copy.deepcopy(self.registry.blobs)
        cases = ("manifest-bytes", "blob-bytes", "missing-blob", "extra-layer",
                 "missing-layer", "traversal", "absolute", "duplicate", "wrong-size")
        for case in cases:
            with self.subTest(case=case):
                self.registry.manifests = copy.deepcopy(original_manifests)
                self.registry.blobs = copy.deepcopy(original_blobs)
                bad_reference = reference
                manifest = json.loads(self.registry.manifest(reference))
                if case == "manifest-bytes":
                    self.registry.manifests[reference.split("@")[1]] += b" "
                elif case == "blob-bytes":
                    digest = manifest["layers"][0]["digest"]
                    data = self.registry.blobs[digest]
                    self.registry.blobs[digest] = bytes([data[0] ^ 1]) + data[1:]
                elif case == "missing-blob":
                    del self.registry.blobs[manifest["layers"][0]["digest"]]
                else:
                    def mutate(value):
                        if case == "extra-layer":
                            value["layers"].append(copy.deepcopy(value["layers"][0]))
                        elif case == "missing-layer":
                            value["layers"].pop()
                        elif case == "duplicate":
                            value["layers"][1]["annotations"] = value["layers"][0]["annotations"]
                        elif case == "wrong-size":
                            value["layers"][0]["size"] += 1
                        else:
                            value["layers"][0]["annotations"][TITLE] = (
                                "../escape" if case == "traversal" else "/absolute"
                            )
                    bad_reference = self.registry.change_manifest(reference, mutate)
                output = self.root / f"rejected-{case}"
                with self.assertRaises(ReleaseContractError):
                    fetch_release(self.registry, bad_reference, output)
                self.assertFalse(output.exists())

    def test_fetch_rejects_output_symlinks_without_writing_the_target(self):
        reference = self._stage()
        output = self.root / "unsafe-output"
        output.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (output / "dist").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ReleaseContractError):
            fetch_release(self.registry, reference, output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_final_receipt_rejects_unpinned_reference_or_inconsistent_blob_identity(self):
        publication = self._publish(self._stage())
        path = self.root / "release-receipt.json"
        original = json.loads(path.read_bytes())
        for change in ("mutable-reference", "blob-digest", "source-evidence", "payload-path"):
            with self.subTest(change=change):
                receipt = copy.deepcopy(original)
                if change == "mutable-reference":
                    receipt["registry_reference"] = f"{REGISTRY_REPOSITORY}:v{self.VERSION}"
                elif change == "blob-digest":
                    receipt["artifacts"][0]["blob_digest"] = "sha256:" + "0" * 64
                elif change == "source-evidence":
                    receipt["evidence"]["workflow"]["source_sha"] = "c" * 40
                else:
                    receipt["payload"][0]["path"] = "../escape"
                path.write_text(json.dumps(receipt))
                with self.assertRaises(ReleaseContractError):
                    verify_final_release_receipt(path)
        self.assertNotEqual(publication["receipt_reference"], publication["bundle_reference"])

    def test_layout_identity_ignores_file_timestamps_and_input_order(self):
        paths = {f"dist/{path.name}": path for path in self.dist.iterdir()}
        first = make_layout(self.root / "layout-one", paths, BUNDLE_TYPE)
        for path in paths.values():
            os.utime(path, (1, 1))
        second = make_layout(self.root / "layout-two", dict(reversed(list(paths.items()))), BUNDLE_TYPE)
        self.assertEqual(first, second)

    def test_final_version_cannot_be_bound_after_package_becomes_public(self):
        reference = self._stage()
        self.registry.metadata = {"visibility": "public"}
        with self.assertRaises(ReleaseContractError):
            self._publish(reference)
        self.assertNotIn(f"v{self.VERSION}", self.registry.tags)
        self.assertEqual(self.registry.copies, [])

    def test_dist_extra_files_or_symlinks_never_reach_the_registry(self):
        extra = self.dist / "private-notes.txt"
        extra.write_text("not a distribution")
        with self.assertRaises(ReleaseContractError):
            self._stage()
        extra.unlink()
        wheel = next(self.dist.glob("*.whl"))
        saved = self.root / wheel.name
        wheel.rename(saved)
        wheel.symlink_to(saved)
        with self.assertRaises(ReleaseContractError):
            self._stage()
        self.assertEqual(self.registry.uploads, [])


class RegistryPrivacyAPITests(unittest.TestCase):
    def test_http_failures_and_unknown_visibility_do_not_authorize_an_upload(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                response = subprocess.CompletedProcess(
                    [], 1, f'HTTP/2.0 {status} Error\n\n{{"message":"unavailable"}}'.encode(), b"",
                )
                registry = Registry(REGISTRY_REPOSITORY)
                with patch("scripts.private_release.subprocess.run", return_value=response):
                    with patch.object(registry, "_copy_layout") as upload:
                        with self.assertRaises(ReleaseContractError):
                            registry.require_private(bootstrap=True)
                        upload.assert_not_called()

    def test_only_explicit_package_not_found_allows_metadata_bootstrap(self):
        registry = Registry(REGISTRY_REPOSITORY)
        owner = subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\n\n{"type":"User"}', b"")
        unavailable = subprocess.CompletedProcess([], 1, b"network error", b"")
        with patch("scripts.private_release.subprocess.run", side_effect=[owner, unavailable]):
            with patch.object(registry, "_copy_layout") as upload:
                with self.assertRaises(ReleaseContractError):
                    registry.require_private(bootstrap=True)
                upload.assert_not_called()

    def test_not_found_bootstraps_only_metadata_then_rechecks_real_package_api(self):
        registry = Registry(REGISTRY_REPOSITORY)
        owner = subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\n\n{"type":"User"}', b"")
        absent = subprocess.CompletedProcess([], 1, b'HTTP/2.0 404 Not Found\n\n{"message":"Not Found"}', b"")
        private = subprocess.CompletedProcess(
            [], 0, b'HTTP/2.0 200 OK\n\n{"name":"hermes-plugin-kit","package_type":"container",'
            b'"owner":{"login":"offendingcommit"},"visibility":"private","repository":null}', b"",
        )
        uploaded = []

        def inspect_bootstrap(layout, tag):
            index = json.loads((layout / "index.json").read_bytes())
            digest = index["manifests"][0]["digest"].split(":")[1]
            manifest = json.loads((layout / "blobs" / "sha256" / digest).read_bytes())
            uploaded.append(manifest)

        with patch("scripts.private_release.subprocess.run", side_effect=[owner, absent, owner, private]):
            with patch.object(registry, "_copy_layout", side_effect=inspect_bootstrap):
                registry.require_private(bootstrap=True)
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0]["artifactType"], BOOTSTRAP_TYPE)
        self.assertEqual(uploaded[0]["layers"], [])


if __name__ == "__main__":
    unittest.main()

