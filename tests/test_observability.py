from __future__ import annotations

import json
import logging
import unittest

from hermes_plugin_kit import (
    ObservabilityEvent,
    credential_identity_hash,
    log_observability_event,
    new_correlation_id,
)


class ObservabilityTests(unittest.TestCase):
    def test_serializes_video_lifecycle_fields_in_stable_shape(self) -> None:
        event = ObservabilityEvent(
            plugin="sirens",
            event="generation.retrieve",
            correlation_id="corr-123",
            persona="DJ Doot",
            lane="dj-doot-k7",
            tool="siren_video_gen",
            request_id="request-123",
            fingerprint="abc123",
            provider="google",
            model="gemini-omni-flash-preview",
            credential_ref="secret/hermes-agent/google-api",
            credential_hash="sha256:0123456789abcdef",
            stage="provider_retrieve",
            status="failed",
            http_status=403,
            error_code="permission_denied",
            error_message="Provider access denied",
            elapsed_ms=1438.126,
            retry_classification="terminal",
            artifact_outcome="not_created",
        )

        self.assertEqual(
            event.as_dict(),
            {
                "schema": "hermes.plugin.observability.v1",
                "event": "generation.retrieve",
                "correlation_id": "corr-123",
                "plugin": "sirens",
                "persona": "DJ Doot",
                "lane": "dj-doot-k7",
                "tool": "siren_video_gen",
                "request_id": "request-123",
                "fingerprint": "abc123",
                "provider": "google",
                "model": "gemini-omni-flash-preview",
                "credential_ref": "secret/hermes-agent/google-api",
                "credential_hash": "sha256:0123456789abcdef",
                "stage": "provider_retrieve",
                "status": "failed",
                "http_status": 403,
                "error_code": "permission_denied",
                "error_message": "Provider access denied",
                "elapsed_ms": 1438.13,
                "retry_classification": "terminal",
                "artifact_outcome": "not_created",
            },
        )

    def test_generates_opaque_correlation_ids(self) -> None:
        first = new_correlation_id()
        second = new_correlation_id()

        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        self.assertRegex(
            ObservabilityEvent(plugin="sirens", event="submitted").correlation_id,
            r"^[0-9a-f]{32}$",
        )

    def test_credential_hash_is_stable_without_exposing_secret(self) -> None:
        secret = "AIzaThisIsOnlyTestMaterial123456789"

        identity = credential_identity_hash(secret)

        self.assertEqual(identity, credential_identity_hash(secret.encode()))
        self.assertRegex(identity, r"^sha256:[0-9a-f]{16}$")
        self.assertNotIn(secret, identity)
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            credential_identity_hash("")

    def test_redacts_nested_and_inline_secrets_before_logging(self) -> None:
        logger = logging.getLogger("hpk-observability-redaction-test")
        google_key = "AIzaThisIsOnlyTestMaterial123456789"
        event = ObservabilityEvent(
            plugin="sirens",
            event="generation.failed",
            status="failed",
            error_message=f"authorization: Bearer hidden-token api_key={google_key}",
            attributes={
                "headers": {"Authorization": "Bearer nested-hidden"},
                "apiKey": google_key,
                "safe": ["retained", {"password": "also-hidden"}],
            },
        )

        with self.assertLogs(logger, level="WARNING") as captured:
            payload = log_observability_event(logger, event)

        output = "\n".join(captured.output)
        self.assertNotIn(google_key, output)
        self.assertNotIn("hidden-token", output)
        self.assertNotIn("nested-hidden", output)
        self.assertNotIn("also-hidden", output)
        self.assertEqual(payload["attributes"]["apiKey"], "***")
        self.assertEqual(payload["attributes"]["headers"]["Authorization"], "***")
        self.assertIn("retained", output)

    def test_logs_compact_json_locally_at_status_derived_level(self) -> None:
        logger = logging.getLogger("hpk-observability-level-test")
        event = ObservabilityEvent(
            plugin="sirens",
            event="generation.completed",
            correlation_id="corr-456",
            status="completed",
        )

        with self.assertLogs(logger, level="INFO") as captured:
            returned = log_observability_event(logger, event)

        message = captured.records[0].getMessage()
        self.assertEqual(captured.records[0].levelno, logging.INFO)
        self.assertTrue(message.startswith("hermes_plugin_observability "))
        encoded = message.removeprefix("hermes_plugin_observability ")
        self.assertEqual(json.loads(encoded), returned)

    def test_bounds_fields_collections_and_attribute_payloads(self) -> None:
        event = ObservabilityEvent(
            plugin="sirens",
            event="generation.debug",
            error_message="x" * 1000,
            attributes={
                "items": list(range(100)),
                "large": "y" * 2000,
            },
        )

        payload = event.as_dict()

        self.assertLessEqual(len(payload["error_message"]), 512)
        self.assertLessEqual(
            len(json.dumps(payload["attributes"], ensure_ascii=False)),
            1024,
        )
        self.assertLessEqual(len(payload["attributes"]["large"]), 256)
        self.assertEqual(payload["attributes"]["items"][-1], "<76 more>")

    def test_rejects_invalid_event_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "plugin must"):
            ObservabilityEvent(plugin="", event="submitted")
        with self.assertRaisesRegex(ValueError, "http_status"):
            ObservabilityEvent(plugin="sirens", event="submitted", http_status=42)
        with self.assertRaisesRegex(ValueError, "elapsed_ms"):
            ObservabilityEvent(plugin="sirens", event="submitted", elapsed_ms=-1)
        with self.assertRaisesRegex(TypeError, "attributes"):
            ObservabilityEvent(
                plugin="sirens",
                event="submitted",
                attributes=[],  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
