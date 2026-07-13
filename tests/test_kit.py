from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path

import hermes_plugin_kit as hpk


class FakeCtx:
    def __init__(self) -> None:
        self.tools: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


class FakePluginCtx(FakeCtx):
    def __init__(self) -> None:
        super().__init__()
        self.hooks: list[tuple[str, object]] = []
        self.skills: list[dict] = []

    def register_hook(self, hook_name, callback) -> None:
        self.hooks.append((hook_name, callback))

    def register_skill(self, **kwargs) -> None:
        self.skills.append(kwargs)


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


class HookBehaviorTests(unittest.TestCase):
    def test_forwards_kwargs_and_return_value_exactly(self) -> None:
        marker = object()

        @hpk.hook("pre_llm_call")
        def callback(**kwargs):
            self.assertIs(kwargs["payload"], marker)
            return marker

        with self.assertLogs(level="DEBUG") as cap:
            self.assertIs(callback(payload=marker, session_id="session-1"), marker)
        joined = "\n".join(cap.output)
        self.assertIn("pre_llm_call: invoked", joined)
        self.assertIn("session-1", joined)
        self.assertNotIn(repr(marker), joined)
        self.assertRegex(joined, r"elapsed_ms=\d+\.\d{2}")

    def test_reraises_and_does_not_log_payload_or_exception_message(self) -> None:
        @hpk.hook("pre_llm_call")
        def callback(**kwargs):
            raise RuntimeError("private exception text")

        with self.assertLogs(level="WARNING") as cap:
            with self.assertRaisesRegex(RuntimeError, "private exception text"):
                callback(message="private message text", task_id="task-1")
        joined = "\n".join(cap.output)
        self.assertIn("RuntimeError", joined)
        self.assertIn("task-1", joined)
        self.assertNotIn("private exception text", joined)
        self.assertNotIn("private message text", joined)

    def test_hook_name_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "hook name"):
            hpk.hook("")


class RegisterPluginTests(unittest.TestCase):
    def _module(self, **attrs):
        module = types.ModuleType("sample_plugin")
        for name, value in attrs.items():
            setattr(module, name, value)
        return module

    def test_registers_tools_hooks_and_skills_with_summary(self) -> None:
        @hpk.hook("pre_llm_call")
        def callback(**kwargs):
            return kwargs

        with tempfile.TemporaryDirectory() as tmp:
            skill_path = Path(tmp) / "SKILL.md"
            skill_path.write_text("# Skill\n")
            skill = hpk.plugin_skill(
                "temporal-awareness", skill_path, "Use local timing context."
            )
            ctx = FakePluginCtx()
            module = self._module(callback=callback, sample_read=sample_read)
            with self.assertLogs(level="INFO") as cap:
                summary = hpk.register_plugin(ctx, module, skills=(skill,))

        self.assertEqual(summary.tools, ("sample_read_thread",))
        self.assertEqual(summary.hooks, ("pre_llm_call",))
        self.assertEqual(summary.skills, ("temporal-awareness",))
        self.assertEqual(summary.skipped_optional_skills, ())
        self.assertEqual(ctx.hooks, [("pre_llm_call", callback)])
        self.assertEqual(ctx.skills[0]["name"], "temporal-awareness")
        self.assertIn("tools=sample_read_thread", "\n".join(cap.output))
        self.assertIn("hooks=pre_llm_call", "\n".join(cap.output))
        self.assertIn("skills=temporal-awareness", "\n".join(cap.output))

    def test_missing_optional_skill_is_skipped_with_warning(self) -> None:
        ctx = FakePluginCtx()
        skill = hpk.plugin_skill("optional", "/missing/SKILL.md", "Optional", optional=True)
        with self.assertLogs(level="WARNING"):
            summary = hpk.register_plugin(ctx, self._module(), skills=(skill,))
        self.assertEqual(summary.skipped_optional_skills, ("optional",))
        self.assertEqual(ctx.skills, [])

    def test_missing_required_skill_raises(self) -> None:
        @hpk.hook("pre_llm_call")
        def callback(**kwargs):
            return kwargs

        ctx = FakePluginCtx()
        skill = hpk.plugin_skill("required", "/missing/SKILL.md", "Required")
        with self.assertRaises(FileNotFoundError):
            hpk.register_plugin(
                ctx,
                self._module(callback=callback, sample_read=sample_read),
                skills=(skill,),
            )
        self.assertEqual(ctx.tools, [])
        self.assertEqual(ctx.hooks, [])
        self.assertEqual(ctx.skills, [])

    def test_validates_skill_name_path_and_description(self) -> None:
        for args in [
            ("bad:name", "SKILL.md", "Description"),
            ("good", "README.md", "Description"),
            ("good", "SKILL.md", ""),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                hpk.plugin_skill(*args)

    def test_rejects_duplicate_hook_names(self) -> None:
        @hpk.hook("pre_llm_call")
        def first(**kwargs):
            return None

        @hpk.hook("pre_llm_call")
        def second(**kwargs):
            return None

        with self.assertRaisesRegex(ValueError, "duplicate hook"):
            hpk.register_plugin(
                FakePluginCtx(), self._module(first=first, second=second)
            )

    def test_rejects_duplicate_tool_names(self) -> None:
        @hpk.tool(toolset="sample", name="sample_duplicate")
        def first(args, **kwargs):
            """First duplicate tool."""
            return {}

        @hpk.tool(toolset="sample", name="sample_duplicate")
        def second(args, **kwargs):
            """Second duplicate tool."""
            return {}

        ctx = FakePluginCtx()
        with self.assertRaisesRegex(ValueError, "duplicate tool"):
            hpk.register_plugin(ctx, self._module(first=first, second=second))
        self.assertEqual(ctx.tools, [])

    def test_rejects_duplicate_skill_names(self) -> None:
        skills = (
            hpk.plugin_skill("same", "one/SKILL.md", "First", optional=True),
            hpk.plugin_skill("same", "two/SKILL.md", "Second", optional=True),
        )
        with self.assertRaisesRegex(ValueError, "duplicate skill"):
            hpk.register_plugin(FakePluginCtx(), self._module(), skills=skills)


if __name__ == "__main__":
    unittest.main()
