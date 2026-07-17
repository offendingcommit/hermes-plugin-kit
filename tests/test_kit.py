from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
