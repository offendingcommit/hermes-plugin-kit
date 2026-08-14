from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from scripts.release_contract import (
    ReleaseContractError,
    classify_commit_messages,
    create_release_receipt,
    validate_conventional_history,
    validate_release_baseline,
    verify_pypi_release,
    verify_release_receipt,
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


class ReleaseReceiptTests(unittest.TestCase):
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
        package_info.unlink()

    def _create(self, dist: Path, output: Path, *, previous: str = "0.7.0") -> dict:
        return create_release_receipt(
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
            self.assertEqual(verify_release_receipt(root / "release-receipt.json", dist), receipt)

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

            with self.assertRaisesRegex(ReleaseContractError, "SHA-256 mismatch"):
                verify_release_receipt(receipt_path, dist)

    def test_major_receipt_is_never_automatic_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist, version="1.0.0")

            receipt = create_release_receipt(
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

            with self.assertRaisesRegex(ReleaseContractError, "expected version 0.8.0"):
                self._create(dist, root / "release-receipt.json")

    def test_pypi_inventory_and_downloaded_bytes_match_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            receipt_path = root / "release-receipt.json"
            receipt = self._create(dist, receipt_path)
            urls = []
            payloads = {}
            for artifact in receipt["artifacts"]:
                filename = artifact["filename"]
                url = f"https://files.pythonhosted.org/packages/release/{filename}"
                payloads[url] = (dist / filename).read_bytes()
                urls.append(
                    {
                        "filename": filename,
                        "digests": {"sha256": artifact["sha256"]},
                        "url": url,
                        "yanked": False,
                    }
                )
            metadata = {"info": {"version": self.VERSION}, "urls": urls}

            result = verify_pypi_release(
                receipt_path,
                fetch_json=lambda _url: metadata,
                fetch_bytes=payloads.__getitem__,
            )

            self.assertEqual(result, metadata)

    def test_pypi_download_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            receipt_path = root / "release-receipt.json"
            receipt = self._create(dist, receipt_path)
            urls = [
                {
                    "filename": artifact["filename"],
                    "digests": {"sha256": artifact["sha256"]},
                    "url": f"https://files.pythonhosted.org/packages/release/{artifact['filename']}",
                    "yanked": False,
                }
                for artifact in receipt["artifacts"]
            ]

            with self.assertRaisesRegex(
                ReleaseContractError, "downloaded PyPI SHA-256 mismatch"
            ):
                verify_pypi_release(
                    receipt_path,
                    fetch_json=lambda _url: {
                        "info": {"version": self.VERSION},
                        "urls": urls,
                    },
                    fetch_bytes=lambda _url: b"tampered",
                )


class ReleaseWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        cls.test_workflow = (ROOT / ".github/workflows/test.yml").read_text(
            encoding="utf-8"
        )
        cls.release_config = yaml.load(cls.workflow, Loader=yaml.BaseLoader)

    def test_release_is_materialized_without_push_or_vcs_release(self) -> None:
        self.assertIn("semantic-release --strict version --no-push --no-vcs-release", self.workflow)
        self.assertIn("release-source.bundle", self.workflow)
        self.assertIn("git push --atomic origin", self.workflow)

    def test_exact_release_source_is_tested_and_built_once(self) -> None:
        self.assertIn('test "$(git rev-parse HEAD)" = "$RELEASE_SHA"', self.workflow)
        self.assertEqual(self.workflow.count("make build"), 1)
        source_push = self.workflow.index("git push --atomic origin")
        self.assertLess(self.workflow.index("make test"), source_push)
        self.assertLess(self.workflow.index("make build"), source_push)

    def test_pypi_publish_precedes_discoverable_github_release_receipt(self) -> None:
        publish = self.workflow.index("pypa/gh-action-pypi-publish@")
        github_release = self.workflow.index("gh release create")
        self.assertLess(publish, github_release)
        self.assertIn("name: pypi", self.workflow)
        self.assertIn("id-token: write", self.workflow)
        self.assertNotIn("dispatches", self.workflow)
        github_release_job = self.release_config["jobs"]["github-release"]
        self.assertIn("verify-pypi", github_release_job["needs"])

    def test_pypi_is_verified_from_registry_before_release_is_discoverable(self) -> None:
        verify = self.release_config["jobs"]["verify-pypi"]
        self.assertIn("publish", verify["needs"])
        self.assertIn("verify-pypi", self.workflow)
        self.assertIn("downloaded bytes", self.workflow)

    def test_publishing_job_has_only_oidc_write_permission(self) -> None:
        publish = self.release_config["jobs"]["publish"]
        self.assertEqual(publish["permissions"], {"id-token": "write"})
        self.assertEqual(publish["environment"]["name"], "pypi")
        self.assertEqual(publish["needs"], "promote-source")

    def test_invalid_history_is_strict_and_activation_is_fail_closed(self) -> None:
        self.assertIn(
            "semantic-release --strict version --no-push --no-vcs-release",
            self.workflow,
        )
        self.assertIn("SEMANTIC_RELEASE_ENABLED", self.workflow)
        self.assertIn("validate-history", self.workflow)
        self.assertIn('"$version" = "$previous_version"', self.workflow)
        self.assertIn("Reject an already-published PyPI version", self.workflow)
        self.assertIn("404) ;;", self.workflow)

    def test_publish_retry_never_rebuilds_and_must_reverify_registry_bytes(self) -> None:
        publish = self.release_config["jobs"]["publish"]
        publish_action = publish["steps"][-1]
        self.assertEqual(publish_action["with"]["skip-existing"], "true")
        self.assertEqual(self.workflow.count("make build"), 1)
        self.assertIn("verify-pypi", self.release_config["jobs"])

    def test_actions_are_immutable_sha_pinned(self) -> None:
        action_lines = [
            line.strip() for line in self.workflow.splitlines() if "uses:" in line
        ]
        self.assertTrue(action_lines)
        for line in action_lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"uses: [^@]+@[0-9a-f]{40}\s+#\s+\S+")

    def test_test_workflow_actions_are_immutable_sha_pinned(self) -> None:
        action_lines = [
            line.strip() for line in self.test_workflow.splitlines() if "uses:" in line
        ]
        self.assertTrue(action_lines)
        for line in action_lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"uses: [^@]+@[0-9a-f]{40}\s+#\s+\S+")
        config = yaml.load(self.test_workflow, Loader=yaml.BaseLoader)
        self.assertEqual(config["permissions"], {"contents": "read"})

    def test_unsafe_pull_request_target_is_not_used(self) -> None:
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("pull_request_target", self.test_workflow)


if __name__ == "__main__":
    unittest.main()
