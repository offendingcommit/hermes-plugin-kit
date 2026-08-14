from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import hermes_plugin_kit as hpk


@contextmanager
def fake_context_engine_host():
    """Install the minimum real-type import seam used by registration preflight."""
    agent_module = types.ModuleType("agent")
    context_engine_module = types.ModuleType("agent.context_engine")

    class ContextEngine:
        pass

    context_engine_module.ContextEngine = ContextEngine
    agent_module.context_engine = context_engine_module
    with patch.dict(
        sys.modules,
        {"agent": agent_module, "agent.context_engine": context_engine_module},
    ):
        yield ContextEngine


class FakeCtx:
    def __init__(self) -> None:
        self.tools: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


class FakePluginCtx(FakeCtx):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[dict] = []
        self.cli_commands: list[dict] = []
        self.middlewares: list[tuple[str, object]] = []
        self.hooks: list[tuple[str, object]] = []
        self.skills: list[dict] = []
        self.image_gen_providers: list[object] = []
        self.video_gen_providers: list[object] = []
        self.memory_providers: list[object] = []
        self.context_engines: list[object] = []
        self.context_engine_result = True
        self.subagent_lifecycle = types.SimpleNamespace(
            launch=lambda request: request,
            status=lambda handle: handle,
            wait=lambda handle, **kwargs: handle,
            cancel=lambda handle, **kwargs: handle,
            result=lambda handle: handle,
            reconnect=lambda handle: handle,
        )

    def register_command(self, **kwargs) -> None:
        self.commands.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli_commands.append(kwargs)

    def register_middleware(self, kind, callback) -> None:
        self.middlewares.append((kind, callback))

    def register_hook(self, hook_name, callback) -> None:
        self.hooks.append((hook_name, callback))

    def register_skill(self, **kwargs) -> None:
        self.skills.append(kwargs)

    def register_image_gen_provider(self, provider) -> None:
        self.image_gen_providers.append(provider)

    def register_video_gen_provider(self, provider) -> None:
        self.video_gen_providers.append(provider)

    def register_memory_provider(self, provider) -> None:
        self.memory_providers.append(provider)

    def register_context_engine(self, engine):
        self.context_engines.append(engine)
        return self.context_engine_result


class SessionDBHelperTests(unittest.TestCase):
    def test_injected_db_is_delegated_to_and_remains_open(self) -> None:
        db = Mock()
        db.get_session.return_value = {"id": "s1"}
        db.list_sessions_rich.return_value = [{"id": "s1"}]
        db.get_messages.return_value = [{"id": 7, "content": "hello"}]
        db.append_message.return_value = 8

        with hpk.open_session_db(db=db) as opened:
            self.assertIs(opened, db)
            self.assertEqual(hpk.read_session(opened, "s1"), {"id": "s1"})
            self.assertEqual(hpk.list_sessions(opened, limit=1), [{"id": "s1"}])
            self.assertEqual(
                hpk.read_session_messages(opened, "s1", limit=1),
                [{"id": 7, "content": "hello"}],
            )
            self.assertEqual(
                hpk.append_session_message(
                    opened,
                    "s1",
                    "assistant",
                    "done",
                    tool_calls=[{"id": "call-1"}],
                ),
                8,
            )

        db.close.assert_not_called()
        db.get_messages.assert_called_once_with(
            "s1",
            include_inactive=False,
            limit=1,
            offset=0,
            latest=False,
            after_id=None,
        )
        db.append_message.assert_called_once_with(
            "s1", "assistant", "done", tool_calls=[{"id": "call-1"}]
        )

    def test_db_and_path_are_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            with hpk.open_session_db("state.db", db=Mock()):
                pass

    def test_missing_public_method_has_instructive_error(self) -> None:
        with self.assertRaisesRegex(
            hpk.SessionDBCompatibilityError, "required public method get_session"
        ):
            hpk.read_session(object(), "s1")

    def test_incompatible_public_signature_has_instructive_error(self) -> None:
        db = types.SimpleNamespace(get_messages=lambda session_id: [])
        with self.assertRaisesRegex(
            hpk.SessionDBCompatibilityError,
            r"get_messages\(\) does not accept requested argument limit=1",
        ):
            hpk.read_session_messages(db, "s1", limit=1)

    def test_older_public_signature_ignores_default_newer_options(self) -> None:
        calls = []

        def get_messages(session_id, include_inactive=False, limit=None, offset=0):
            calls.append((session_id, include_inactive, limit, offset))
            return []

        db = types.SimpleNamespace(get_messages=get_messages)
        self.assertEqual(hpk.read_session_messages(db, "s1"), [])
        self.assertEqual(calls, [("s1", False, None, 0)])
        with self.assertRaisesRegex(
            hpk.SessionDBCompatibilityError, "does not accept requested argument latest=True"
        ):
            hpk.read_session_messages(db, "s1", latest=True)

    def test_missing_hermes_import_has_instructive_error(self) -> None:
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "hermes_state":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaisesRegex(
                hpk.SessionDBCompatibilityError, "SessionDB is unavailable"
            ):
                with hpk.open_session_db("state.db"):
                    pass


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_manifest_config_remains_compatible_without_loading_host_config(self) -> None:
        ctx = types.SimpleNamespace(
            manifest=types.SimpleNamespace(config={"mode": "community"})
        )
        loader = Mock(side_effect=AssertionError("loader should not run"))

        result = hpk.load_plugin_config(
            ctx,
            "memory-sync",
            config_loader=loader,
        )

        self.assertEqual(result, {"mode": "community"})
        loader.assert_not_called()

    def test_real_plugin_context_reads_named_effective_config(self) -> None:
        ctx = types.SimpleNamespace(manifest=types.SimpleNamespace())
        effective = {
            "plugins": {
                "enabled": ["memory-sync"],
                "memory-sync": {"authored_memory": {"enabled": True}},
            }
        }

        result = hpk.load_plugin_config(
            ctx,
            "memory-sync",
            config_loader=lambda: effective,
        )

        self.assertEqual(result, {"authored_memory": {"enabled": True}})
        self.assertIsNot(result, effective["plugins"]["memory-sync"])
        self.assertIsNot(
            result["authored_memory"],
            effective["plugins"]["memory-sync"]["authored_memory"],
        )

    def test_stderr_logging_is_operator_gated_and_idempotent(self) -> None:
        logger = logging.getLogger("hpk-runtime-compatibility-test")
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        try:
            with patch.dict(
                hpk.os.environ,
                {"MEMORY_SYNC_LOG_STDERR": "true"},
                clear=False,
            ):
                first = hpk.configure_stderr_logging(
                    logger,
                    env_var="MEMORY_SYNC_LOG_STDERR",
                )
                second = hpk.configure_stderr_logging(
                    logger,
                    env_var="MEMORY_SYNC_LOG_STDERR",
                )

            self.assertIsNotNone(first)
            self.assertIs(first, second)
            self.assertEqual(logger.handlers, [first])
            self.assertEqual(logger.level, logging.INFO)
        finally:
            logger.handlers.clear()


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
    def test_prebuilt_schema_is_copied_and_legacy_required_validation_can_be_disabled(self) -> None:
        source_schema = {
            "description": "Write a legacy memory entry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Entry body.",
                    }
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        }

        @hpk.tool(
            toolset="x",
            name="legacy_write_entry",
            schema=source_schema,
            validate_required=False,
        )
        def legacy(args, **kwargs):
            """Write through a handler with its own validation contract."""
            if not args.get("content"):
                return '{"success": false, "error": "legacy content error"}'
            return {"content": args["content"]}

        emitted = getattr(legacy, "_hpk_tool_spec")["schema"]
        source_schema["parameters"]["properties"]["content"]["description"] = "mutated"

        self.assertEqual(
            emitted["parameters"]["properties"]["content"]["description"],
            "Entry body.",
        )
        self.assertNotIn("name", emitted)
        self.assertEqual(emitted["parameters"]["required"], ["content"])
        self.assertEqual(
            json.loads(legacy({})),
            {"success": False, "error": "legacy content error"},
        )

    def test_prebuilt_schema_uses_required_validation_by_default(self) -> None:
        handler = Mock(return_value={"unexpected": True})

        decorated = hpk.tool(
            toolset="x",
            name="schema_validated_tool",
            schema={
                "description": "Validate a supplied schema.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        )(handler)

        result = json.loads(decorated({}))

        self.assertFalse(result["success"])
        self.assertIn("query is required", result["error"])
        handler.assert_not_called()

    def test_prebuilt_schema_and_params_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema and params"):

            @hpk.tool(
                toolset="x",
                params={"query": hpk.str_arg("Query.")},
                schema={
                    "description": "Invalid mixed declaration.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            )
            def mixed(args, **kwargs):
                """Invalid mixed declaration."""
                return {}

    def test_prebuilt_schema_rejects_invalid_hermes_shapes(self) -> None:
        invalid_schemas = (
            {
                "description": "Arguments are incorrectly flattened.",
                "type": "object",
                "properties": {},
            },
            {
                "description": "Required references an unknown property.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": ["missing"],
                },
            },
            {
                "name": "different_name",
                "description": "Name disagrees with the decorated tool.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        )

        for supplied in invalid_schemas:
            with self.subTest(schema=supplied), self.assertRaises(ValueError):

                @hpk.tool(
                    toolset="x",
                    name="schema_shape_probe",
                    schema=supplied,
                )
                def invalid(args, **kwargs):
                    """Invalid prebuilt schema."""
                    return {}

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


class MiddlewareBehaviorTests(unittest.TestCase):
    def test_known_kinds_forward_kwargs_and_return_values(self) -> None:
        expected = {
            hpk.MiddlewareKind.TOOL_REQUEST,
            hpk.MiddlewareKind.TOOL_EXECUTION,
            hpk.MiddlewareKind.LLM_REQUEST,
            hpk.MiddlewareKind.LLM_EXECUTION,
        }
        self.assertEqual(set(hpk.MiddlewareKind), expected)

        for kind in expected:
            marker = object()

            @hpk.middleware(kind)
            def callback(**kwargs):
                self.assertIs(kwargs["payload"], marker)
                return marker

            with self.subTest(kind=kind), self.assertLogs(level="DEBUG") as cap:
                self.assertIs(
                    callback(payload=marker, session_id="session-1"),
                    marker,
                )
            spec = getattr(callback, "_hpk_middleware_spec")
            self.assertEqual(spec["kind"], kind.value)
            joined = "\n".join(cap.output)
            self.assertIn(f"{kind.value} middleware: invoked", joined)
            self.assertIn("session-1", joined)
            self.assertNotIn(repr(marker), joined)
            self.assertRegex(joined, r"elapsed_ms=\d+\.\d{2}")

    def test_accepts_future_string_kind(self) -> None:
        @hpk.middleware("  future_request  ")
        def callback(**kwargs):
            return kwargs

        self.assertEqual(
            getattr(callback, "_hpk_middleware_spec"),
            {"kind": "future_request"},
        )

    def test_rejects_missing_kind_and_async_callback(self) -> None:
        for kind in ("", "   ", None):
            with self.subTest(kind=kind), self.assertRaisesRegex(
                ValueError, "middleware kind"
            ):
                hpk.middleware(kind)

        async def async_callback(**kwargs):
            return kwargs

        with self.assertRaisesRegex(TypeError, "must be synchronous"):
            hpk.middleware(hpk.MiddlewareKind.TOOL_REQUEST)(async_callback)

    def test_reraises_without_logging_payload_or_exception_message(self) -> None:
        @hpk.middleware(hpk.MiddlewareKind.TOOL_REQUEST)
        def callback(**kwargs):
            raise RuntimeError("private middleware failure")

        with self.assertLogs(level="WARNING") as cap:
            with self.assertRaisesRegex(RuntimeError, "private middleware failure"):
                callback(args={"token": "private-token"}, task_id="task-1")
        joined = "\n".join(cap.output)
        self.assertIn("error_type=RuntimeError", joined)
        self.assertIn("task-1", joined)
        self.assertNotIn("private middleware failure", joined)
        self.assertNotIn("private-token", joined)


class CommandBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def test_forwards_raw_args_and_uses_docstring_description(self) -> None:
        @hpk.command("valdris-status", args_hint="  <scope>  ")
        def status(raw_args):
            """  Show Valdris status.  """
            return f"status:{raw_args}"

        spec = getattr(status, "_hpk_command_spec")
        self.assertEqual(spec["name"], "valdris-status")
        self.assertEqual(spec["description"], "Show Valdris status.")
        self.assertEqual(spec["args_hint"], "<scope>")

        raw_args = "  exact input --keep-spacing  "
        with self.assertLogs(level="DEBUG") as cap:
            self.assertEqual(status(raw_args), f"status:{raw_args}")
        joined = "\n".join(cap.output)
        self.assertIn("valdris-status: invoked", joined)
        self.assertIn(f"args_chars={len(raw_args)}", joined)
        self.assertNotIn(raw_args, joined)
        self.assertRegex(joined, r"elapsed_ms=\d+\.\d{2}")
        self.assertIn("result=str", joined)

    async def test_async_handler_remains_async_and_returns_none(self) -> None:
        @hpk.command("valdris-sync", description="Synchronize Valdris.")
        async def sync(raw_args):
            self.assertEqual(raw_args, "apply")
            return None

        self.assertTrue(inspect.iscoroutinefunction(sync))
        with self.assertLogs(level="INFO") as cap:
            self.assertIsNone(await sync("apply"))
        self.assertIn("result=NoneType", "\n".join(cap.output))

    def test_reraises_without_logging_args_or_exception_message(self) -> None:
        @hpk.command("valdris-fail", description="Fail safely.")
        def fail(raw_args):
            raise RuntimeError("private command failure")

        with self.assertLogs(level="WARNING") as cap:
            with self.assertRaisesRegex(RuntimeError, "private command failure"):
                fail("private command arguments")
        joined = "\n".join(cap.output)
        self.assertIn("error_type=RuntimeError", joined)
        self.assertNotIn("private command failure", joined)
        self.assertNotIn("private command arguments", joined)

    def test_rejects_invalid_names(self) -> None:
        for name in (
            "",
            "/valdris-status",
            "Valdris",
            "valdris status",
            "valdris_status",
            "9valdris",
            "valdris-",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "command name"
            ):
                hpk.command(name)

    def test_requires_description_or_docstring(self) -> None:
        with self.assertRaisesRegex(ValueError, "description is required"):

            @hpk.command("valdris-empty")
            def empty(raw_args):
                return raw_args

    def test_description_argument_overrides_docstring(self) -> None:
        @hpk.command("valdris-help", description="  Explicit description.  ")
        def help_command(raw_args):
            """Ignored description."""
            return raw_args

        spec = getattr(help_command, "_hpk_command_spec")
        self.assertEqual(spec["description"], "Explicit description.")

    def test_blank_description_falls_back_to_docstring(self) -> None:
        @hpk.command("valdris-help", description="   ")
        def help_command(raw_args):
            """Show Valdris help."""
            return raw_args

        spec = getattr(help_command, "_hpk_command_spec")
        self.assertEqual(spec["description"], "Show Valdris help.")

    def test_description_and_args_hint_must_be_strings(self) -> None:
        with self.assertRaisesRegex(TypeError, "description"):
            hpk.command("valdris-help", description=object())
        with self.assertRaisesRegex(TypeError, "args_hint"):
            hpk.command("valdris-help", description="Help.", args_hint=object())

    def test_cli_command_wraps_sync_handler_and_derives_help(self) -> None:
        def setup_parser(parser):
            parser.add_argument("--scope")

        @hpk.command(
            "valdris",
            type=hpk.CommandType.CLI,
            setup_fn=setup_parser,
        )
        def valdris(args):
            """Manage Valdris state.

            Supports status and repair operations.
            """
            return f"scope:{args.scope}"

        spec = getattr(valdris, "_hpk_command_spec")
        self.assertEqual(spec["type"], "cli")
        self.assertEqual(spec["help"], "Manage Valdris state.")
        self.assertIs(spec["setup_fn"], setup_parser)

        args = types.SimpleNamespace(scope="private-value")
        with self.assertLogs(level="INFO") as cap:
            self.assertEqual(valdris(args), "scope:private-value")
        joined = "\n".join(cap.output)
        self.assertIn("valdris: invoked; type=cli", joined)
        self.assertNotIn("private-value", joined)

    def test_cli_command_reraises_without_logging_namespace_or_error(self) -> None:
        @hpk.command("valdris", type="cli", description="Manage Valdris.")
        def valdris(args):
            raise RuntimeError("private CLI failure")

        args = argparse.Namespace(token="private-value")
        with self.assertLogs(level="WARNING") as cap:
            with self.assertRaisesRegex(RuntimeError, "private CLI failure"):
                valdris(args)
        joined = "\n".join(cap.output)
        self.assertIn("error_type=RuntimeError", joined)
        self.assertNotIn("private CLI failure", joined)
        self.assertNotIn("private-value", joined)

    def test_cli_command_supports_no_argument_setup(self) -> None:
        @hpk.command(
            "valdris-status",
            type="cli",
            help="Show Valdris status",
            description="Show the current Valdris status.",
        )
        def status(args):
            return args

        spec = getattr(status, "_hpk_command_spec")
        parser = Mock()
        self.assertIsNone(spec["setup_fn"](parser))
        parser.assert_not_called()
        self.assertEqual(spec["help"], "Show Valdris status")

    def test_cli_command_rejects_async_handler_and_slash_only_options(self) -> None:
        with self.assertRaisesRegex(TypeError, "CLI command handler must be synchronous"):

            @hpk.command("valdris", type="cli", description="Manage Valdris.")
            async def valdris(args):
                return args

        async def setup_parser(parser):
            return None

        with self.assertRaisesRegex(TypeError, "setup_fn must be synchronous"):
            hpk.command(
                "valdris",
                type="cli",
                description="Manage Valdris.",
                setup_fn=setup_parser,
            )

        with self.assertRaisesRegex(ValueError, "args_hint is only valid"):
            hpk.command(
                "valdris",
                type="cli",
                description="Manage Valdris.",
                args_hint="<scope>",
            )

        with self.assertRaisesRegex(ValueError, "help is only valid"):
            hpk.command(
                "valdris",
                type="slash",
                description="Manage Valdris.",
                help="Manage Valdris",
            )

        with self.assertRaisesRegex(ValueError, "setup_fn is only valid"):
            hpk.command(
                "valdris",
                type="slash",
                description="Manage Valdris.",
                setup_fn=lambda parser: None,
            )

    def test_command_rejects_invalid_type_and_cli_options(self) -> None:
        with self.assertRaisesRegex(ValueError, "command type"):
            hpk.command("valdris", type="terminal", description="Manage Valdris.")
        with self.assertRaisesRegex(TypeError, "command help"):
            hpk.command("valdris", type="cli", description="Manage Valdris.", help=1)
        with self.assertRaisesRegex(TypeError, "setup_fn"):
            hpk.command(
                "valdris",
                type="cli",
                description="Manage Valdris.",
                setup_fn="not-callable",
            )


class HostToolInvocationTests(unittest.TestCase):
    def _runtime_modules(self, *, block_message=None, result='{"success": true}'):
        plugins = types.ModuleType("hermes_cli.plugins")
        plugins.resolve_pre_tool_block = Mock(return_value=block_message)
        plugins.has_hook = Mock(return_value=True)
        plugins.invoke_hook = Mock(return_value=[])

        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli.plugins = plugins

        send_message = types.ModuleType("tools.send_message_tool")
        send_message.send_message_tool = Mock(return_value=result)
        tools_package = types.ModuleType("tools")
        tools_package.__path__ = []
        tools_package.send_message_tool = send_message

        modules = {
            "hermes_cli": hermes_cli,
            "hermes_cli.plugins": plugins,
            "tools": tools_package,
            "tools.send_message_tool": send_message,
        }
        return modules, plugins, send_message.send_message_tool

    def test_invokes_non_registry_host_tool_through_plugin_hooks(self) -> None:
        modules, plugins, handler = self._runtime_modules()
        args = {
            "action": "send",
            "target": "telegram:8670382527",
            "message": "MEDIA:/opt/data/avatars/generated/portrait.png",
        }

        with patch.dict(sys.modules, modules):
            result = hpk.invoke_host_tool(
                "send_message",
                args,
                session_id="session-1",
                task_id="task-1",
            )

        self.assertEqual(json.loads(result), {"success": True})
        plugins.resolve_pre_tool_block.assert_called_once_with(
            "send_message",
            args,
            task_id="task-1",
            session_id="session-1",
            tool_call_id="",
            turn_id="",
            api_request_id="",
        )
        handler.assert_called_once_with(args, session_id="session-1", task_id="task-1")
        post_call = plugins.invoke_hook.call_args
        self.assertEqual(post_call.args, ("post_tool_call",))
        self.assertEqual(post_call.kwargs["tool_name"], "send_message")
        self.assertEqual(post_call.kwargs["status"], "success")

    def test_blocked_host_tool_does_not_reach_handler(self) -> None:
        modules, _plugins, handler = self._runtime_modules(
            block_message="Outbound messaging is guarded"
        )

        with patch.dict(sys.modules, modules):
            result = hpk.invoke_host_tool(
                "send_message",
                {"action": "send", "target": "telegram", "message": "hello"},
            )

        self.assertEqual(
            json.loads(result),
            {"error": "Outbound messaging is guarded"},
        )
        handler.assert_not_called()

    def test_rejects_unknown_host_tool(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported host tool"):
            hpk.invoke_host_tool("not_a_host_tool", {})


class MediaDeliveryContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        hpk.clear_media_delivery_state(session_id="session-1")
        hpk.clear_media_delivery_state(session_id="session-2")

    def test_voice_payload_has_typed_hermes_directive(self) -> None:
        payload = hpk.MediaPayload("/opt/data/voice-staging/memo.ogg", hpk.MediaType.VOICE)
        self.assertEqual(
            payload.to_message(),
            "[[audio_as_voice]]\nMEDIA:/opt/data/voice-staging/memo.ogg",
        )

    def test_voice_payload_rejects_non_voice_container(self) -> None:
        with self.assertRaisesRegex(ValueError, "ogg or opus"):
            hpk.MediaPayload("/opt/data/voice-staging/memo.mp3", hpk.MediaType.VOICE)

    def test_spoiler_image_keeps_standard_hermes_media_directive(self) -> None:
        payload = hpk.MediaPayload(
            "/opt/data/avatars/generated/portrait.png",
            hpk.MediaType.AUTO,
            spoiler=True,
        )

        self.assertTrue(payload.spoiler)
        self.assertEqual(
            payload.to_message(),
            "MEDIA:/opt/data/avatars/generated/portrait.png",
        )

    def test_spoiler_rejects_non_image_media(self) -> None:
        for path, media_type in (
            ("/opt/data/voice-staging/memo.ogg", hpk.MediaType.VOICE),
            ("/opt/data/report.pdf", hpk.MediaType.DOCUMENT),
            ("/opt/data/video.mp4", hpk.MediaType.AUTO),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError, "spoiler media must be an image"
            ):
                hpk.MediaPayload(path, media_type, spoiler=True)

    def test_origin_target_uses_task_local_gateway_route(self) -> None:
        session_context = types.ModuleType("gateway.session_context")
        values = {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_CHAT_ID": "-5372910000",
            "HERMES_SESSION_THREAD_ID": "42",
        }
        session_context.get_session_env = lambda name, default="": values.get(name, default)
        gateway = types.ModuleType("gateway")
        gateway.__path__ = []
        gateway.session_context = session_context

        with patch.dict(
            sys.modules,
            {"gateway": gateway, "gateway.session_context": session_context},
        ):
            target = hpk.resolve_delivery_target("origin")

        self.assertEqual(target.requested, "origin")
        self.assertEqual(target.host_target, "telegram:-5372910000:42")
        self.assertEqual(target.display, "telegram:-…0000:42")

    def test_origin_target_preserves_telegram_dm_route(self) -> None:
        session_context = types.ModuleType("gateway.session_context")
        values = {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_CHAT_ID": "8670382527",
            "HERMES_SESSION_THREAD_ID": "",
        }
        session_context.get_session_env = lambda name, default="": values.get(name, default)
        gateway = types.ModuleType("gateway")
        gateway.__path__ = []
        gateway.session_context = session_context

        with patch.dict(
            sys.modules,
            {"gateway": gateway, "gateway.session_context": session_context},
        ):
            target = hpk.resolve_delivery_target("origin")

        self.assertEqual(target.host_target, "telegram:8670382527")
        self.assertEqual(target.display, "telegram:…2527")

    def test_origin_target_preserves_telegram_group_route(self) -> None:
        session_context = types.ModuleType("gateway.session_context")
        values = {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_CHAT_ID": "-5372910000",
            "HERMES_SESSION_THREAD_ID": "",
        }
        session_context.get_session_env = lambda name, default="": values.get(name, default)
        gateway = types.ModuleType("gateway")
        gateway.__path__ = []
        gateway.session_context = session_context

        with patch.dict(
            sys.modules,
            {"gateway": gateway, "gateway.session_context": session_context},
        ):
            target = hpk.resolve_delivery_target("origin")

        self.assertEqual(target.host_target, "telegram:-5372910000")
        self.assertEqual(target.display, "telegram:-…0000")

    def test_deliver_media_invokes_typed_host_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            payload = hpk.MediaPayload(path, hpk.MediaType.VOICE)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps(
                    {"success": True, "platform": "telegram", "chat_id": "8670382527", "message_id": "9"}
                ),
            ) as invoke:
                result = hpk.deliver_media(
                    payload,
                    target="telegram:8670382527",
                    session_id="session-1",
                )

        invoke.assert_called_once_with(
            "send_message",
            {
                "action": "send",
                "target": "telegram:8670382527",
                "message": f"[[audio_as_voice]]\nMEDIA:{path}",
            },
            session_id="session-1",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.requested_target, "telegram:…2527")
        self.assertEqual(result.display_target, "telegram:…2527")
        self.assertEqual(result.host_result["chat_id"], "…2527")
        self.assertEqual(result.as_dict()["media_type"], "voice")

    def test_spoiler_delivery_uses_guarded_telegram_transport(self) -> None:
        plugins = types.ModuleType("hermes_cli.plugins")
        plugins.resolve_pre_tool_block = Mock(return_value=None)
        plugins.has_hook = Mock(return_value=True)
        plugins.invoke_hook = Mock(return_value=[])
        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli.plugins = plugins

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portrait.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            payload = hpk.MediaPayload(path, spoiler=True)
            with (
                patch.dict(
                    sys.modules,
                    {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
                ),
                patch.object(
                    hpk,
                    "_deliver_telegram_spoiler",
                    return_value={
                        "success": True,
                        "platform": "telegram",
                        "chat_id": "8670382527",
                        "message_id": "9",
                    },
                ) as spoiler_send,
                patch.object(hpk, "invoke_host_tool") as host_send,
            ):
                result = hpk.deliver_media(
                    payload,
                    target="telegram:8670382527",
                    session_id="session-1",
                    task_id="task-1",
                )

        host_send.assert_not_called()
        spoiler_send.assert_called_once()
        self.assertEqual(spoiler_send.call_args.args[0], payload)
        self.assertEqual(
            spoiler_send.call_args.args[1].host_target,
            "telegram:8670382527",
        )
        guard_args = plugins.resolve_pre_tool_block.call_args.args[1]
        self.assertEqual(guard_args["target"], "telegram:8670382527")
        self.assertEqual(guard_args["message"], f"MEDIA:{path}")
        self.assertEqual(guard_args["media_options"], {"spoiler": True})
        self.assertTrue(result.success)
        self.assertTrue(result.spoiler)
        self.assertEqual(result.host_result["chat_id"], "…2527")
        self.assertEqual(
            plugins.invoke_hook.call_args.kwargs["status"],
            "success",
        )

    def test_spoiler_delivery_respects_hermes_pre_tool_guard(self) -> None:
        plugins = types.ModuleType("hermes_cli.plugins")
        plugins.resolve_pre_tool_block = Mock(return_value="Outbound media blocked")
        plugins.has_hook = Mock(return_value=True)
        plugins.invoke_hook = Mock(return_value=[])
        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli.plugins = plugins

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portrait.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            with (
                patch.dict(
                    sys.modules,
                    {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
                ),
                patch.object(hpk, "_deliver_telegram_spoiler") as spoiler_send,
            ):
                result = hpk.deliver_media(
                    hpk.MediaPayload(path, spoiler=True),
                    target="telegram:-5372910000",
                    session_id="session-1",
                )

        spoiler_send.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.host_result["error"], "Outbound media blocked")
        self.assertIsNone(
            hpk.transform_media_delivery_output(
                response_text="delivery failed",
                session_id="session-1",
            )
        )

    def test_spoiler_transport_forwards_group_topic_and_closes_bot(self) -> None:
        bot = types.SimpleNamespace(
            initialize=AsyncMock(),
            send_photo=AsyncMock(
                return_value=types.SimpleNamespace(message_id=42)
            ),
            shutdown=AsyncMock(),
        )
        telegram = types.ModuleType("telegram")
        telegram.Bot = Mock(return_value=bot)
        telegram_ids = types.ModuleType(
            "plugins.platforms.telegram.telegram_ids"
        )
        telegram_ids.normalize_telegram_chat_id = lambda value: int(value)
        adapter = types.ModuleType("plugins.platforms.telegram.adapter")
        adapter.TelegramAdapter = types.SimpleNamespace(
            _message_thread_id_for_send=lambda value: int(value)
        )
        gateway_config = types.ModuleType("gateway.config")
        platform = types.SimpleNamespace(TELEGRAM="telegram")
        gateway_config.Platform = platform
        gateway_config.load_gateway_config = Mock(
            return_value=types.SimpleNamespace(
                platforms={
                    "telegram": types.SimpleNamespace(
                        enabled=True,
                        token="contract-token",
                        extra={},
                    )
                },
                get_home_channel=lambda _platform: None,
            )
        )
        gateway = types.ModuleType("gateway")
        gateway.__path__ = []
        gateway.config = gateway_config
        plugins = types.ModuleType("plugins")
        plugins.__path__ = []
        platforms = types.ModuleType("plugins.platforms")
        platforms.__path__ = []
        telegram_package = types.ModuleType("plugins.platforms.telegram")
        telegram_package.__path__ = []
        model_tools = types.ModuleType("model_tools")
        model_tools._run_async = asyncio.run

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portrait.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            payload = hpk.MediaPayload(path, caption="A reveal", spoiler=True)
            resolved = hpk.ResolvedDeliveryTarget(
                requested="origin",
                host_target="telegram:-5372910000:42",
                display="telegram:-…0000:42",
            )
            with patch.dict(
                sys.modules,
                {
                    "telegram": telegram,
                    "gateway": gateway,
                    "gateway.config": gateway_config,
                    "plugins": plugins,
                    "plugins.platforms": platforms,
                    "plugins.platforms.telegram": telegram_package,
                    "plugins.platforms.telegram.adapter": adapter,
                    "plugins.platforms.telegram.telegram_ids": telegram_ids,
                    "model_tools": model_tools,
                },
            ):
                result = hpk._deliver_telegram_spoiler(payload, resolved)

        self.assertEqual(result["message_id"], "42")
        telegram.Bot.assert_called_once_with(token="contract-token")
        bot.initialize.assert_awaited_once()
        bot.shutdown.assert_awaited_once()
        photo_call = bot.send_photo.await_args.kwargs
        self.assertEqual(photo_call["chat_id"], -5372910000)
        self.assertEqual(photo_call["message_thread_id"], 42)
        self.assertEqual(photo_call["caption"], "A reveal")
        self.assertTrue(photo_call["has_spoiler"])

    def test_successful_delivery_suppresses_the_same_turn_final_response_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps({"success": True, "message_id": "9"}),
            ):
                hpk.deliver_media(
                    hpk.MediaPayload(path, hpk.MediaType.VOICE),
                    target="telegram:8670382527",
                    session_id="session-1",
                )

        self.assertEqual(
            hpk.transform_media_delivery_output(
                response_text=f"[[audio_as_voice]]\nMEDIA:{path}",
                session_id="session-1",
                platform="telegram",
            ),
            "NO_REPLY",
        )
        self.assertIsNone(
            hpk.transform_media_delivery_output(
                response_text="unrelated next turn",
                session_id="session-1",
                platform="telegram",
            )
        )

    def test_successful_delivery_does_not_suppress_another_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps({"success": True}),
            ):
                hpk.deliver_media(
                    hpk.MediaPayload(path, hpk.MediaType.VOICE),
                    target="telegram:-5372910000",
                    session_id="session-1",
                )

        self.assertIsNone(
            hpk.transform_media_delivery_output(
                response_text="keep this",
                session_id="session-2",
                platform="telegram",
            )
        )

    def test_failed_delivery_does_not_suppress_final_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps({"success": False, "error": "offline"}),
            ):
                hpk.deliver_media(
                    hpk.MediaPayload(path, hpk.MediaType.VOICE),
                    target="telegram:8670382527",
                    session_id="session-1",
                )

        self.assertIsNone(
            hpk.transform_media_delivery_output(
                response_text="delivery failed",
                session_id="session-1",
                platform="telegram",
            )
        )

    def test_session_end_clears_unconsumed_delivery_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps({"success": True}),
            ):
                hpk.deliver_media(
                    hpk.MediaPayload(path, hpk.MediaType.VOICE),
                    target="telegram:8670382527",
                    session_id="session-1",
                )

        hpk.clear_media_delivery_state(session_id="session-1")
        self.assertIsNone(
            hpk.transform_media_delivery_output(
                response_text="next turn",
                session_id="session-1",
                platform="telegram",
            )
        )

    def test_delivery_result_redacts_raw_route_from_host_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.ogg"
            path.write_bytes(b"OggS" + b"\x00" * 16)
            with patch.object(
                hpk,
                "invoke_host_tool",
                return_value=json.dumps(
                    {"error": "send to telegram:-5372910000 failed for -5372910000"}
                ),
            ):
                result = hpk.deliver_media(
                    hpk.MediaPayload(path, hpk.MediaType.VOICE),
                    target="telegram:-5372910000",
                )

        self.assertFalse(result.success)
        encoded = json.dumps(result.as_dict(), ensure_ascii=False)
        self.assertNotIn("5372910000", encoded.replace("…0000", ""))
        self.assertIn("telegram:-…0000", encoded)


class CapabilitySelectionTests(unittest.TestCase):
    def test_expands_selected_capabilities_atomically(self) -> None:
        selection = hpk.resolve_capability_selection(
            {"image", "video_gen", "video_status", "video_stitch"},
            enabled_capabilities=("image", "video"),
            capability_groups={
                "image": {"image"},
                "video": {"video_gen", "video_status", "video_stitch"},
            },
        )

        self.assertEqual(selection.capabilities, ("image", "video"))
        self.assertEqual(
            selection.names,
            ("image", "video_gen", "video_status", "video_stitch"),
        )

    def test_rejects_capabilities_and_explicit_names_together(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            hpk.resolve_capability_selection(
                {"image"},
                enabled_capabilities=("image",),
                enabled_names=("image",),
                capability_groups={"image": {"image"}},
            )

    def test_rejects_unknown_capabilities_and_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown capabilities: voice"):
            hpk.resolve_capability_selection(
                {"image"},
                enabled_capabilities=("voice",),
                capability_groups={"image": {"image"}},
            )
        with self.assertRaisesRegex(ValueError, "unknown names: missing"):
            hpk.resolve_capability_selection(
                {"image"},
                enabled_names=("missing",),
                capability_groups={"image": {"image"}},
            )

    def test_rejects_invalid_capability_group_members_before_selection(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "capability 'video' references unknown names: video_status"
        ):
            hpk.resolve_capability_selection(
                {"video_gen"},
                enabled_capabilities=("video",),
                capability_groups={"video": {"video_gen", "video_status"}},
            )


class RegisterPluginTests(unittest.TestCase):
    def _module(self, **attrs):
        module = types.ModuleType("sample_plugin")
        for name, value in attrs.items():
            setattr(module, name, value)
        return module

    def test_registration_summary_preserves_positional_middleware_argument(self) -> None:
        summary = hpk.RegistrationSummary(
            (),
            (),
            (),
            (),
            (),
            ("tool_request",),
        )

        self.assertEqual(summary.middlewares, ("tool_request",))
        self.assertEqual(summary.cli_commands, ())

    def test_registers_all_lifecycle_surfaces_with_summary(self) -> None:
        @hpk.command(
            "valdris-status",
            description="Show Valdris status.",
            args_hint="<scope>",
        )
        def command_handler(raw_args):
            return raw_args

        def setup_cli(parser):
            parser.add_argument("--scope")

        @hpk.command(
            "valdris",
            type="cli",
            description="Manage Valdris from the terminal.",
            help="Manage Valdris",
            setup_fn=setup_cli,
        )
        def cli_command_handler(args):
            return args

        @hpk.hook("pre_llm_call")
        def callback(**kwargs):
            return kwargs

        @hpk.middleware(hpk.MiddlewareKind.TOOL_REQUEST)
        def request_middleware(**kwargs):
            return {"args": kwargs["args"]}

        with tempfile.TemporaryDirectory() as tmp:
            skill_path = Path(tmp) / "SKILL.md"
            skill_path.write_text(
                "---\nname: temporal-awareness\n"
                "description: Use local timing context.\n---\n# Skill\n"
            )
            skill = hpk.plugin_skill(
                "temporal-awareness", skill_path, "Use local timing context."
            )
            ctx = FakePluginCtx()
            module = self._module(
                callback=callback,
                cli_command_handler=cli_command_handler,
                command_handler=command_handler,
                request_middleware=request_middleware,
                sample_read=sample_read,
            )
            with self.assertLogs(level="INFO") as cap:
                summary = hpk.register_plugin(ctx, module, skills=(skill,))

        self.assertEqual(summary.commands, ("valdris-status",))
        self.assertEqual(summary.cli_commands, ("valdris",))
        self.assertEqual(summary.tools, ("sample_read_thread",))
        self.assertEqual(summary.middlewares, ("tool_request",))
        self.assertEqual(summary.hooks, ("pre_llm_call",))
        self.assertEqual(summary.skills, ("temporal-awareness",))
        self.assertEqual(summary.skipped_optional_skills, ())
        self.assertEqual(
            ctx.commands,
            [
                {
                    "name": "valdris-status",
                    "handler": command_handler,
                    "description": "Show Valdris status.",
                    "args_hint": "<scope>",
                }
            ],
        )
        self.assertEqual(
            ctx.cli_commands,
            [
                {
                    "name": "valdris",
                    "help": "Manage Valdris",
                    "setup_fn": setup_cli,
                    "handler_fn": cli_command_handler,
                    "description": "Manage Valdris from the terminal.",
                }
            ],
        )
        self.assertEqual(
            ctx.middlewares,
            [("tool_request", request_middleware)],
        )
        self.assertEqual(ctx.hooks, [("pre_llm_call", callback)])
        self.assertEqual(ctx.skills[0]["name"], "temporal-awareness")
        self.assertIn("commands=valdris-status", "\n".join(cap.output))
        self.assertIn("cli_commands=valdris", "\n".join(cap.output))
        self.assertIn("tools=sample_read_thread", "\n".join(cap.output))
        self.assertIn("middlewares=tool_request", "\n".join(cap.output))
        self.assertIn("hooks=pre_llm_call", "\n".join(cap.output))
        self.assertIn("skills=temporal-awareness", "\n".join(cap.output))

    def test_plugin_skill_validates_frontmatter_and_matches_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_path = Path(tmp) / "SKILL.md"
            skill_path.write_text(
                "---\n"
                "name: temporal-awareness\n"
                "description: Use local timing context.\n"
                "platforms: [macos, linux]\n"
                "metadata:\n"
                "  hermes:\n"
                "    tags: [Time, Context]\n"
                "    requires_toolsets: [terminal]\n"
                "required_environment_variables:\n"
                "  - name: TIME_API_KEY\n"
                "    prompt: Time API key\n"
                "---\n"
                "# Temporal awareness\n"
            )

            skill = hpk.plugin_skill(
                "temporal-awareness", skill_path, "Use local timing context."
            )

        self.assertEqual(skill.name, "temporal-awareness")

    def test_plugin_skill_rejects_invalid_or_drifting_frontmatter(self) -> None:
        invalid_documents = {
            "missing": "# Skill\n",
            "name": "---\nname: other\ndescription: Description\n---\n# Skill\n",
            "description": "---\nname: sample\ndescription: Other\n---\n# Skill\n",
            "platforms": (
                "---\nname: sample\ndescription: Description\n"
                "platforms: [plan9]\n---\n# Skill\n"
            ),
            "hermes": (
                "---\nname: sample\ndescription: Description\n"
                "metadata:\n  hermes:\n    requires_tools: terminal\n"
                "---\n# Skill\n"
            ),
            "empty-body": "---\nname: sample\ndescription: Description\n---\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            for label, document in invalid_documents.items():
                with self.subTest(label=label):
                    path.write_text(document)
                    with self.assertRaises(ValueError):
                        hpk.plugin_skill("sample", path, "Description")

    def test_registers_specialized_providers_without_decorating_them(self) -> None:
        image_provider = types.SimpleNamespace(name="image", generate=lambda prompt: prompt)
        video_provider = types.SimpleNamespace(name="video", generate=lambda prompt: prompt)
        memory_provider = types.SimpleNamespace(name="memory")
        ctx = FakePluginCtx()

        summary = hpk.register_plugin(
            ctx,
            self._module(),
            memory_providers=(memory_provider,),
            image_gen_providers=(image_provider,),
            video_gen_providers=(video_provider,),
        )

        self.assertEqual(ctx.memory_providers, [memory_provider])
        self.assertEqual(ctx.image_gen_providers, [image_provider])
        self.assertEqual(ctx.video_gen_providers, [video_provider])
        self.assertEqual(summary.memory_providers, ("memory",))
        self.assertEqual(summary.image_gen_providers, ("image",))
        self.assertEqual(summary.video_gen_providers, ("video",))

    def test_registers_one_typed_context_engine_with_accepted_receipt(self) -> None:
        with fake_context_engine_host() as ContextEngine:
            engine = ContextEngine()
            engine.name = "continuity"
            ctx = FakePluginCtx()

            with self.assertLogs(level="INFO") as cap:
                summary = hpk.register_plugin(
                    ctx, self._module(), context_engine=engine
                )

        self.assertEqual(ctx.context_engines, [engine])
        self.assertEqual(summary.context_engine, "continuity")
        self.assertEqual(summary.context_engine_registration, "accepted")
        receipt = "\n".join(cap.output)
        self.assertIn("context_engine=continuity", receipt)
        self.assertIn("context_engine_registration=accepted", receipt)

    def test_legacy_context_engine_registrar_is_reported_as_submitted(self) -> None:
        with fake_context_engine_host() as ContextEngine:
            engine = ContextEngine()
            engine.name = "continuity"
            ctx = FakePluginCtx()
            ctx.context_engine_result = None

            summary = hpk.register_plugin(ctx, self._module(), context_engine=engine)

        self.assertEqual(summary.context_engine, "continuity")
        self.assertEqual(summary.context_engine_registration, "declared/submitted")

    def test_omitting_context_engine_preserves_existing_behavior(self) -> None:
        ctx = FakePluginCtx()

        summary = hpk.register_plugin(ctx, self._module())

        self.assertEqual(ctx.context_engines, [])
        self.assertIsNone(summary.context_engine)
        self.assertIsNone(summary.context_engine_registration)

    def test_context_engine_preflight_finishes_before_host_mutation(self) -> None:
        @hpk.tool(toolset="sample", name="sample_context_engine_preflight")
        def sample_tool(args, **kwargs):
            """Sample tool."""
            return {}

        cases = (
            ("missing registrar", object(), RuntimeError, "register_context_engine"),
            ("blank name", types.SimpleNamespace(name=" "), ValueError, "non-empty name"),
            (
                "wrong type",
                types.SimpleNamespace(name="continuity"),
                TypeError,
                "ContextEngine",
            ),
        )
        with fake_context_engine_host():
            for label, engine, error_type, message in cases:
                with self.subTest(label=label):
                    ctx = FakePluginCtx()
                    if label == "missing registrar":
                        ctx.register_context_engine = None
                    with self.assertRaisesRegex(error_type, message):
                        hpk.register_plugin(
                            ctx,
                            self._module(sample_tool=sample_tool),
                            context_engine=engine,
                        )
                    self.assertEqual(ctx.tools, [])
                    self.assertEqual(ctx.context_engines, [])

    def test_rejected_second_context_engine_fails_before_other_mutation(self) -> None:
        @hpk.hook("pre_llm_call")
        def sample_hook(**kwargs):
            return kwargs

        with fake_context_engine_host() as ContextEngine:
            engine = ContextEngine()
            engine.name = "continuity"
            ctx = FakePluginCtx()
            ctx.context_engine_result = False

            with self.assertRaisesRegex(RuntimeError, "only one context engine"):
                hpk.register_plugin(
                    ctx,
                    self._module(sample_hook=sample_hook),
                    context_engine=engine,
                )

        self.assertEqual(ctx.context_engines, [engine])
        self.assertEqual(ctx.hooks, [])

    def test_preflights_provider_support_before_registering_tools(self) -> None:
        @hpk.tool(toolset="sample", name="sample_tool")
        def sample_tool(args, **kwargs):
            """Sample tool."""
            return {}

        ctx = FakePluginCtx()
        ctx.register_video_gen_provider = None
        video_provider = types.SimpleNamespace(
            name="video", generate=lambda prompt: prompt
        )

        with self.assertRaisesRegex(RuntimeError, "does not support video generation"):
            hpk.register_plugin(
                ctx,
                self._module(sample_tool=sample_tool),
                video_gen_providers=(video_provider,),
            )

        self.assertEqual(ctx.tools, [])

    def test_get_subagent_lifecycle_requires_the_public_service_contract(self) -> None:
        ctx = FakePluginCtx()
        self.assertIs(hpk.get_subagent_lifecycle(ctx), ctx.subagent_lifecycle)

        ctx.subagent_lifecycle = types.SimpleNamespace(launch=lambda request: request)
        with self.assertRaises(RuntimeError):
            hpk.get_subagent_lifecycle(ctx)

    def test_logs_one_stable_registration_receipt_with_actual_names(self) -> None:
        logger = logging.getLogger("registration-receipt-test")
        summary = hpk.RegistrationSummary(
            commands=("valdris-status",),
            tools=("sample_read_thread",),
            middlewares=("tool_request",),
            hooks=("pre_llm_call",),
            skills=("temporal-awareness",),
            skipped_optional_skills=("missing-optional",),
            capabilities=("image", "video"),
        )

        with self.assertLogs(logger, level="INFO") as cap:
            hpk.log_registration_summary(logger, "sample-plugin", summary)

        self.assertEqual(len(cap.records), 1)
        self.assertEqual(
            cap.records[0].getMessage(),
            "hermes_plugin_kit: registered plugin lifecycle; "
            "plugin=sample-plugin; commands=valdris-status; "
            "cli_commands=<none>; tools=sample_read_thread; "
            "middlewares=tool_request; "
            "hooks=pre_llm_call; skills=temporal-awareness; "
            "skipped_optional_skills=missing-optional; "
            "memory_providers=<none>; "
            "image_gen_providers=<none>; video_gen_providers=<none>; "
            "capabilities=image,video; context_engine=<none>; "
            "context_engine_registration=<none>",
        )

    def test_register_plugin_reports_selected_capabilities(self) -> None:
        summary = hpk.register_plugin(
            FakePluginCtx(),
            self._module(),
            capabilities=("video", "image"),
        )

        self.assertEqual(summary.capabilities, ("image", "video"))

    def test_register_plugin_uses_public_registration_summary_logger(self) -> None:
        ctx = FakePluginCtx()
        ctx.manifest = types.SimpleNamespace(name="sample-plugin")
        module = self._module()

        with patch.object(hpk, "log_registration_summary") as log_summary:
            summary = hpk.register_plugin(ctx, module)

        log_summary.assert_called_once_with(
            logging.getLogger("sample_plugin"),
            "sample-plugin",
            summary,
        )

    def test_registers_only_active_decorated_callables_from_iterable(self) -> None:
        @hpk.tool(toolset="sample", name="sample_active")
        def active_tool(args, **kwargs):
            """Active tool."""
            return {}

        @hpk.tool(toolset="sample", name="sample_inactive")
        def inactive_tool(args, **kwargs):
            """Inactive tool."""
            return {}

        @hpk.hook("pre_llm_call")
        def active_hook(**kwargs):
            return kwargs

        ctx = FakePluginCtx()
        logger = logging.getLogger("active-declarations-test")
        with patch.object(hpk, "log_registration_summary") as log_summary:
            summary = hpk.register_plugin(
                ctx,
                (active_hook, active_tool),
                plugin_name="active-plugin",
                logger=logger,
            )

        self.assertEqual(summary.tools, ("sample_active",))
        self.assertEqual(summary.hooks, ("pre_llm_call",))
        self.assertNotIn("sample_inactive", summary.tools)
        log_summary.assert_called_once_with(logger, "active-plugin", summary)

    def test_iterable_duplicate_detection_matches_module_registration(self) -> None:
        @hpk.tool(toolset="sample", name="sample_duplicate_iterable")
        def first(args, **kwargs):
            """First duplicate tool."""
            return {}

        @hpk.tool(toolset="sample", name="sample_duplicate_iterable")
        def second(args, **kwargs):
            """Second duplicate tool."""
            return {}

        with self.assertRaisesRegex(ValueError, "duplicate tool"):
            hpk.register_plugin(FakePluginCtx(), (second, first))

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

        with self.assertRaises(FileNotFoundError):
            hpk.plugin_skill("required", "/missing/SKILL.md", "Required")

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

    def test_rejects_duplicate_middleware_kinds_before_registration(self) -> None:
        @hpk.middleware(hpk.MiddlewareKind.TOOL_EXECUTION)
        def first(**kwargs):
            return kwargs["next_call"](kwargs["args"])

        @hpk.middleware("tool_execution")
        def second(**kwargs):
            return kwargs["next_call"](kwargs["args"])

        ctx = FakePluginCtx()
        with self.assertRaisesRegex(ValueError, "duplicate middleware"):
            hpk.register_plugin(ctx, self._module(first=first, second=second))
        self.assertEqual(ctx.middlewares, [])
        self.assertEqual(ctx.tools, [])

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

    def test_rejects_duplicate_command_names_before_registration(self) -> None:
        @hpk.command("valdris-status", description="First status.")
        def first(raw_args):
            return raw_args

        @hpk.command("valdris-status", description="Second status.")
        def second(raw_args):
            return raw_args

        ctx = FakePluginCtx()
        with self.assertRaisesRegex(ValueError, "duplicate command"):
            hpk.register_plugin(ctx, self._module(first=first, second=second))
        self.assertEqual(ctx.commands, [])
        self.assertEqual(ctx.tools, [])

    def test_rejects_duplicate_cli_command_names_before_registration(self) -> None:
        @hpk.command("valdris", type="cli", description="First CLI command.")
        def first(args):
            return args

        @hpk.command("valdris", type=hpk.CommandType.CLI, description="Second CLI command.")
        def second(args):
            return args

        ctx = FakePluginCtx()
        with self.assertRaisesRegex(ValueError, "duplicate CLI command"):
            hpk.register_plugin(ctx, self._module(first=first, second=second))
        self.assertEqual(ctx.cli_commands, [])
        self.assertEqual(ctx.tools, [])

    def test_allows_same_name_on_slash_and_cli_surfaces(self) -> None:
        @hpk.command("valdris", type="slash", description="Slash command.")
        def slash(raw_args):
            return raw_args

        @hpk.command("valdris", type="cli", description="CLI command.")
        def cli(args):
            return args

        ctx = FakePluginCtx()
        summary = hpk.register_plugin(ctx, self._module(slash=slash, cli=cli))

        self.assertEqual(summary.commands, ("valdris",))
        self.assertEqual(summary.cli_commands, ("valdris",))
        self.assertEqual([entry["name"] for entry in ctx.commands], ["valdris"])
        self.assertEqual([entry["name"] for entry in ctx.cli_commands], ["valdris"])

    def test_rejects_duplicate_skill_names(self) -> None:
        skills = (
            hpk.plugin_skill("same", "one/SKILL.md", "First", optional=True),
            hpk.plugin_skill("same", "two/SKILL.md", "Second", optional=True),
        )
        with self.assertRaisesRegex(ValueError, "duplicate skill"):
            hpk.register_plugin(FakePluginCtx(), self._module(), skills=skills)


if __name__ == "__main__":
    unittest.main()
