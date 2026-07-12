from __future__ import annotations

import json
import unittest

import hermes_plugin_kit as hpk


class FakeCtx:
    def __init__(self) -> None:
        self.tools: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


@hpk.tool(
    toolset="messaging",
    namespace="sample",
    name=hpk.tool_name("sample", "read", "thread"),
    requires_env=["DISCORD_BOT_TOKEN"],
    emoji="🧵",
    params={
        "thread_id_or_url": hpk.str_arg(
            "Discord thread link or numeric ID", required=True, example="123456789012345678"
        ),
        "limit": hpk.int_arg("Messages to return", minimum=1, maximum=100),
    },
)
def sample_read(args, **kwargs):
    """Read recent messages from a thread the bot can already access."""
    return {"thread": args["thread_id_or_url"], "kwargs": sorted(kwargs)}


@hpk.tool(toolset="x", params={"q": hpk.str_arg("query", required=True)})
def sample_boom(args, **kwargs):
    """Always explodes."""
    raise RuntimeError("kaboom")


class SchemaConventionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = getattr(sample_read, "_hpk_tool_spec")["schema"]

    def test_arguments_live_under_parameters_not_top_level(self) -> None:
        self.assertEqual(self.schema["name"], "sample_read_thread")
        self.assertIn("parameters", self.schema)
        self.assertNotIn("properties", self.schema)  # never at the top level
        params = self.schema["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertFalse(params["additionalProperties"])
        self.assertIn("thread_id_or_url", params["properties"])
        self.assertEqual(params["required"], ["thread_id_or_url"])

    def test_kit_metadata_stripped_from_emitted_schema(self) -> None:
        prop = self.schema["parameters"]["properties"]["thread_id_or_url"]
        self.assertNotIn("_required", prop)
        self.assertNotIn("_example", prop)
        self.assertEqual(prop["type"], "string")

    def test_description_self_documents_required_arg_and_example(self) -> None:
        self.assertIn("thread_id_or_url", self.schema["description"])
        self.assertIn("123456789012345678", self.schema["description"])


class HandlerBehaviorTests(unittest.TestCase):
    def test_success_envelope_and_tolerates_runtime_kwargs(self) -> None:
        with self.assertLogs(level="DEBUG") as cap:
            out = json.loads(sample_read({"thread_id_or_url": "999"}, task_id="t", session_id="s"))
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["thread"], "999")
        self.assertEqual(out["data"]["kwargs"], ["session_id", "task_id"])
        joined = "\n".join(cap.output)
        self.assertIn("sample_read_thread: invoked", joined)
        self.assertIn('"session_id": "s"', joined)
        self.assertRegex(joined, r"elapsed_ms=\d+\.\d{2}")
        self.assertIn("result=dict", joined)

    def test_missing_required_returns_instructive_error_and_warns(self) -> None:
        with self.assertLogs(level="WARNING") as cap:
            out = json.loads(sample_read({}))
        self.assertFalse(out["success"])
        self.assertIn("thread_id_or_url is required", out["error"])
        self.assertIn("123456789012345678", out["error"])
        self.assertTrue(any("missing thread_id_or_url" in line for line in cap.output))

    def test_blank_string_counts_as_missing(self) -> None:
        out = json.loads(sample_read({"thread_id_or_url": "   "}))
        self.assertFalse(out["success"])

    def test_exception_caught_in_band(self) -> None:
        with self.assertLogs(level="WARNING") as cap:
            out = json.loads(sample_boom({"q": "x"}))
        self.assertFalse(out["success"])
        self.assertIn("sample_boom failed", out["error"])
        joined = "\n".join(cap.output)
        self.assertIn("Traceback (most recent call last)", joined)
        self.assertIn("RuntimeError: kaboom", joined)
        self.assertRegex(joined, r"elapsed_ms=\d+\.\d{2}")

    def test_reserved_agent_loop_tool_name_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):

            @hpk.tool(toolset="x", name="memory")
            def reserved(args, **kwargs):
                """Reserved."""
                return {}

    def test_reserved_core_namespace_prefix_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved Hermes core namespace"):
            hpk.tool_name("memory", "write", "entry")

    def test_explicit_namespace_must_match_tool_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "must start with explicit namespace"):

            @hpk.tool(toolset="x", namespace="discord", name="thread_read")
            def wrong_namespace(args, **kwargs):
                """Wrong namespace."""
                return {}

    def test_secret_looking_values_redacted_in_logs(self) -> None:
        @hpk.tool(toolset="x", params={"id": hpk.str_arg("id", required=True)})
        def needs_id(args, **kwargs):
            """Needs id."""
            return {}

        with self.assertLogs(level="WARNING") as cap:
            needs_id({"api_token": "supersecret"})  # missing id -> args get logged
        joined = "\n".join(cap.output)
        self.assertIn("***", joined)
        self.assertNotIn("supersecret", joined)

    def test_nested_secret_looking_values_redacted_in_logs(self) -> None:
        @hpk.tool(toolset="x")
        def nested(args, **kwargs):
            """Accept nested configuration."""
            return {}

        with self.assertLogs(level="DEBUG") as cap:
            nested(
                {
                    "config": {
                        "api_key": "nested-secret",
                        "headers": [{"authorization": "Bearer hidden"}],
                    }
                }
            )
        joined = "\n".join(cap.output)
        self.assertNotIn("nested-secret", joined)
        self.assertNotIn("Bearer hidden", joined)
        self.assertGreaterEqual(joined.count("***"), 2)

    def test_string_return_is_passthrough(self) -> None:
        @hpk.tool(toolset="x")
        def already_json(args, **kwargs):
            """Returns its own JSON."""
            return '{"raw": true}'

        with self.assertLogs(level="INFO") as cap:
            self.assertEqual(already_json({}), '{"raw": true}')
        self.assertIn("result=encoded_string", "\n".join(cap.output))


class RegisterAllTests(unittest.TestCase):
    def test_registers_every_decorated_tool_with_convention(self) -> None:
        ctx = FakeCtx()
        with self.assertLogs(level="INFO") as cap:
            count = hpk.register_all(ctx, __name__)
        self.assertGreaterEqual(count, 2)
        by_name = {tool["name"]: tool for tool in ctx.tools}
        self.assertIn("sample_read_thread", by_name)
        sample = by_name["sample_read_thread"]
        self.assertEqual(sample["toolset"], "messaging")
        self.assertEqual(sample["requires_env"], ["DISCORD_BOT_TOKEN"])
        self.assertEqual(sample["emoji"], "🧵")
        self.assertIn("parameters", sample["schema"])
        self.assertTrue(callable(sample["handler"]))
        joined = "\n".join(cap.output)
        self.assertIn(f"registered {count} tool(s)", joined)
        self.assertIn("sample_read_thread", joined)

    def test_description_requires_a_docstring(self) -> None:
        with self.assertRaises(ValueError):

            @hpk.tool(toolset="x")
            def no_doc(args, **kwargs):
                return {}


if __name__ == "__main__":
    unittest.main()
