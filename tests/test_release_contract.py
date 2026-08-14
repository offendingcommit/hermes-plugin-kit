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
    create_release_manifest,
    finalize_release_receipt,
    validate_conventional_history,
    validate_release_baseline,
    verify_final_release_receipt,
    verify_pypi_release,
    verify_release_manifest,
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

    def _pypi_release(self, receipt: dict, dist: Path) -> tuple[dict, dict[str, bytes]]:
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
        return {"info": {"version": self.VERSION}, "urls": urls}, payloads

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
            self.assertEqual(receipt["evidence"]["unit"]["command"], "just test")
            self.assertEqual(
                receipt["evidence"]["public_contract"]["command"],
                "just test-release (included in just test)",
            )
            self.assertEqual(
                receipt["evidence"]["hermes_contract"]["command"],
                "just test-contract",
            )
            self.assertEqual(
                receipt["evidence"]["build_metadata"]["command"],
                "just build && just check-dist",
            )
            self.assertEqual(receipt["receipt_state"], "prepublication")
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

            with self.assertRaisesRegex(ReleaseContractError, "SHA-256 mismatch"):
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
            metadata, payloads = self._pypi_release(receipt, dist)

            result = verify_pypi_release(
                receipt_path,
                fetch_json=lambda _url: metadata,
                fetch_bytes=payloads.__getitem__,
            )

            self.assertEqual(result, metadata)

    def test_verified_pypi_urls_round_trip_into_final_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            manifest_path = root / "release-manifest.json"
            manifest = self._create(dist, manifest_path)
            metadata, payloads = self._pypi_release(manifest, dist)
            receipt_path = root / "release-receipt.json"

            receipt = finalize_release_receipt(
                manifest_path,
                receipt_path,
                fetch_json=lambda _url: metadata,
                fetch_bytes=payloads.__getitem__,
            )

            self.assertEqual(receipt["receipt_state"], "published")
            self.assertEqual(
                receipt,
                json.loads(receipt_path.read_text(encoding="utf-8")),
            )
            verified = verify_final_release_receipt(receipt_path)
            self.assertEqual(verified, receipt)
            for artifact in receipt["artifacts"]:
                self.assertEqual(artifact["size"], len(payloads[artifact["url"]]))
                self.assertTrue(
                    artifact["url"].startswith("https://files.pythonhosted.org/")
                )

    def test_yanked_pypi_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            manifest_path = root / "release-manifest.json"
            manifest = self._create(dist, manifest_path)
            metadata, payloads = self._pypi_release(manifest, dist)
            metadata["urls"][0]["yanked"] = True

            with self.assertRaisesRegex(ReleaseContractError, "is yanked"):
                verify_pypi_release(
                    manifest_path,
                    fetch_json=lambda _url: metadata,
                    fetch_bytes=payloads.__getitem__,
                )

    def test_pypi_metadata_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            manifest_path = root / "release-manifest.json"
            manifest = self._create(dist, manifest_path)
            metadata, payloads = self._pypi_release(manifest, dist)
            metadata["urls"][0]["digests"]["sha256"] = "0" * 64

            with self.assertRaisesRegex(ReleaseContractError, "PyPI SHA-256 mismatch"):
                verify_pypi_release(
                    manifest_path,
                    fetch_json=lambda _url: metadata,
                    fetch_bytes=payloads.__getitem__,
                )

    def test_non_pythonhosted_artifact_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            self._write_dist(dist)
            manifest_path = root / "release-manifest.json"
            manifest = self._create(dist, manifest_path)
            metadata, payloads = self._pypi_release(manifest, dist)
            original_url = metadata["urls"][0]["url"]
            invalid_url = original_url.replace(
                "files.pythonhosted.org", "downloads.example.invalid"
            )
            metadata["urls"][0]["url"] = invalid_url
            payloads[invalid_url] = payloads[original_url]

            with self.assertRaisesRegex(ReleaseContractError, "unexpected PyPI artifact URL"):
                verify_pypi_release(
                    manifest_path,
                    fetch_json=lambda _url: metadata,
                    fetch_bytes=payloads.__getitem__,
                )

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
        cls.justfile = (ROOT / "justfile").read_text(encoding="utf-8")
        cls.release_config = yaml.load(cls.workflow, Loader=yaml.BaseLoader)

    def test_release_is_materialized_without_push_or_vcs_release(self) -> None:
        self.assertIn("semantic-release --strict version --no-push --no-vcs-release", self.workflow)
        self.assertIn("release-source.bundle", self.workflow)
        self.assertIn("git push --atomic origin", self.workflow)

    def test_materialize_attaches_exact_trigger_sha_to_main_before_release(self) -> None:
        steps = self.release_config["jobs"]["materialize"]["steps"]
        checkout_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"] == "Check out the triggering main revision"
        )
        intent_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"] == "Validate the tagged baseline and strict release intent"
        )
        attach_steps = [
            (index, step)
            for index, step in enumerate(steps)
            if step["name"] == "Attach the exact trigger to local main"
        ]

        self.assertEqual(len(attach_steps), 1)
        attach_index, attach = attach_steps[0]
        self.assertLess(checkout_index, attach_index)
        self.assertLess(attach_index, intent_index)
        self.assertEqual(attach["env"]["EXPECTED_SHA"], "${{ github.sha }}")
        self.assertEqual(attach["run"], "just release-attach-trigger")
        self.assertIn("release-attach-trigger:", self.justfile)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', self.justfile)
        self.assertEqual(
            self.justfile.count(
                'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'
            ),
            2,
        )
        self.assertIn(
            'git switch --force-create main "$EXPECTED_SHA"', self.justfile
        )
        self.assertIn('test "$(git branch --show-current)" = "main"', self.justfile)

    def test_exact_release_source_is_tested_and_built_once_in_isolated_jobs(self) -> None:
        jobs = self.release_config["jobs"]
        for job_name in ("unit-public-tests", "hermes-contract", "build"):
            with self.subTest(job=job_name):
                job_text = yaml.safe_dump(jobs[job_name])
                self.assertIn("actions/checkout@", job_text)
                self.assertIn("release-source.bundle", job_text)
                self.assertIn('git rev-parse HEAD)" = "$RELEASE_SHA"', job_text)
                self.assertIn("git status --porcelain", job_text)
        build = jobs["build"]
        self.assertEqual(
            set(build["needs"]),
            {"materialize", "unit-public-tests", "hermes-contract"},
        )
        build_text = yaml.safe_dump(build)
        hermes_text = yaml.safe_dump(jobs["hermes-contract"])
        self.assertIn("just test-contract", hermes_text)
        self.assertNotIn("just test-contract", build_text)
        self.assertNotIn(".hermes-agent", build_text)
        self.assertIn("just build", build_text)
        self.assertEqual(self.workflow.count("just build"), 1)
        source_push = self.workflow.index("git push --atomic origin")
        self.assertLess(self.workflow.index("just test"), source_push)
        self.assertLess(self.workflow.index("just build"), source_push)

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
        self.assertIn("Verify PyPI bytes", self.workflow)
        self.assertIn("finalize-receipt", self.workflow)
        github_release = yaml.safe_dump(self.release_config["jobs"]["github-release"])
        self.assertIn("release-receipt.json", github_release)
        self.assertNotIn("release-manifest.json", github_release)

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
        immutable_control = yaml.safe_dump(
            self.release_config["jobs"]["immutable-release-control"]
        )
        self.assertIn("immutable-releases", immutable_control)
        self.assertIn("permission-administration: read", immutable_control)
        self.assertIn("isImmutable", self.workflow)
        self.assertIn("validate-history", self.workflow)
        self.assertIn('"$version" = "$previous_version"', self.workflow)
        self.assertIn("Reject an already-published PyPI version", self.workflow)
        self.assertIn("404) ;;", self.workflow)
        self.assertIn("--connect-timeout", self.workflow)
        self.assertIn("--max-time", self.workflow)

    def test_source_promotion_uses_protected_github_app_identity(self) -> None:
        promote = self.release_config["jobs"]["promote-source"]
        promote_text = yaml.safe_dump(promote)
        self.assertEqual(promote["environment"], "source-promotion")
        self.assertIn(
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            promote_text,
        )
        self.assertIn("SOURCE_PROMOTION_APP_CLIENT_ID", promote_text)
        self.assertIn("SOURCE_PROMOTION_APP_PRIVATE_KEY", promote_text)
        self.assertNotIn("github.token", promote_text)
        self.assertIn("persist-credentials: 'false'", promote_text)
        self.assertNotIn("personal_access_token", promote_text.lower())

    def test_no_release_intent_never_enters_a_protected_environment(self) -> None:
        jobs = self.release_config["jobs"]
        materialize = jobs["materialize"]
        immutable_control = jobs["immutable-release-control"]
        promote = jobs["promote-source"]

        self.assertEqual(materialize["needs"], "activation")
        self.assertEqual(
            materialize["if"], "needs.activation.outputs.enabled == 'true'"
        )
        self.assertEqual(immutable_control["needs"], "materialize")
        self.assertEqual(
            immutable_control["if"],
            "needs.materialize.outputs.released == 'true'",
        )
        self.assertIn("immutable-release-control", promote["needs"])
        self.assertIn("build", promote["needs"])
        self.assertEqual(
            promote["if"], "needs.materialize.outputs.released == 'true'"
        )

    def test_every_checkout_discards_automatic_credentials(self) -> None:
        checkout_count = self.workflow.count("uses: actions/checkout@")
        self.assertGreater(checkout_count, 0)
        self.assertEqual(
            checkout_count, self.workflow.count("persist-credentials: false")
        )

    def test_publish_retry_never_rebuilds_and_must_reverify_registry_bytes(self) -> None:
        publish = self.release_config["jobs"]["publish"]
        publish_action = publish["steps"][-1]
        self.assertEqual(publish_action["with"]["skip-existing"], "true")
        self.assertEqual(self.workflow.count("just build"), 1)
        self.assertIn("verify-pypi", self.release_config["jobs"])

    def test_source_promotion_resumes_after_an_ambiguous_success(self) -> None:
        promote = self.release_config["jobs"]["promote-source"]
        push = promote["steps"][-1]["run"]

        self.assertIn(
            '[ "$remote_main" = "$RELEASE_SHA" ] && '
            '[ "$remote_tag" = "$RELEASE_SHA" ]',
            push,
        )
        self.assertIn(
            '[ "$remote_main" = "$TRIGGER_SHA" ] && [ -z "$remote_tag" ]',
            push,
        )
        self.assertIn("if ! git push --atomic origin", push)
        self.assertGreaterEqual(
            push.count('test "$remote_main" = "$RELEASE_SHA"'),
            1,
        )
        self.assertGreaterEqual(
            push.count('test "$remote_tag" = "$RELEASE_SHA"'),
            1,
        )

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

    def test_workflows_install_and_use_only_just(self) -> None:
        setup_just = (
            "extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3"
        )
        workflows = {
            "release": self.workflow,
            "test": self.test_workflow,
        }
        for workflow_name, workflow in workflows.items():
            config = yaml.load(workflow, Loader=yaml.BaseLoader)
            for job_name, job in config["jobs"].items():
                steps = job.get("steps", [])
                just_steps = [
                    index
                    for index, step in enumerate(steps)
                    if any(
                        line.strip() == "just" or line.strip().startswith("just ")
                        for line in step.get("run", "").splitlines()
                    )
                ]
                if not just_steps:
                    continue
                setup_steps = [
                    index
                    for index, step in enumerate(steps)
                    if step.get("uses") == setup_just
                    and step.get("with", {}).get("just-version") == "1.58.0"
                ]
                with self.subTest(workflow=workflow_name, job=job_name):
                    self.assertTrue(
                        any(index < just_steps[0] for index in setup_steps),
                        "Just-using jobs must install the pinned version first",
                    )

            with self.subTest(workflow=workflow_name):
                self.assertNotRegex(workflow, r"(?m)^\s+make(?:\s|$)")
                self.assertNotIn("run: make", workflow)

    def test_justfile_is_the_only_repository_task_runner(self) -> None:
        self.assertTrue((ROOT / "justfile").is_file())
        self.assertFalse((ROOT / "Makefile").exists())

    def test_unsafe_pull_request_target_is_not_used(self) -> None:
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("pull_request_target", self.test_workflow)


if __name__ == "__main__":
    unittest.main()
