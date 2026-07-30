"""Contract tests against the *real* hermes-agent source.

These keep the kit relevant: they import the genuine ``PluginContext`` and tool
``registry`` from a hermes-agent checkout and prove that what the kit emits still
satisfies the runtime contract — most importantly that a kit-built schema, run
through the registry's real OpenAI-tool conversion, yields a function whose
``parameters`` are populated (the empty-``{}`` failure mode this kit exists to
prevent). The host-tool contract also sends the exact generated-image payload
through Hermes' real target parser, media extractor, and Telegram formatter,
mocking only the final Bot API client.

The whole module is skipped when hermes-agent is not importable, so the suite
stays green standalone and in public CI. Point it at a checkout with
``HERMES_AGENT_PATH`` (a CI job can ``actions/checkout`` NousResearch/hermes-agent
and set it); locally it discovers ``~/hermes-agent`` automatically.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import hermes_plugin_kit as hpk


def _import_real_hermes():
    """Return real plugin runtime APIs, failing when an explicit checkout is invalid."""
    candidates: list[Path] = []
    env_path = os.environ.get("HERMES_AGENT_PATH")
    if env_path:
        explicit_root = Path(env_path)
        if not (explicit_root / "hermes_cli" / "plugins.py").exists():
            raise FileNotFoundError(
                f"HERMES_AGENT_PATH has no hermes_cli/plugins.py: {explicit_root}"
            )
        sys.path.insert(0, str(explicit_root))
    candidates.append(Path.home() / "hermes-agent")
    candidates.append(Path.home() / ".hermes" / "hermes-agent")

    def _try():
        from hermes_cli.plugins import (  # type: ignore
            PluginContext,
            PluginManager,
            PluginManifest,
            VALID_HOOKS,
            resolve_plugin_command_result,
        )
        from hermes_cli.middleware import (  # type: ignore
            VALID_MIDDLEWARE,
            apply_llm_request_middleware,
            apply_tool_request_middleware,
            run_llm_execution_middleware,
            run_tool_execution_middleware,
        )
        from tools.registry import registry  # type: ignore

        # A stale checkout that predates plugin-owned skills is not the
        # lifecycle contract this suite is intended to certify.
        if not hasattr(PluginContext, "register_skill"):
            raise ImportError("hermes-agent PluginContext.register_skill is unavailable")
        if not hasattr(PluginManager, "find_plugin_skill"):
            raise ImportError("hermes-agent PluginManager.find_plugin_skill is unavailable")
        if not hasattr(PluginContext, "register_middleware"):
            raise ImportError(
                "hermes-agent PluginContext.register_middleware is unavailable"
            )

        return types.SimpleNamespace(
            apply_llm_request_middleware=apply_llm_request_middleware,
            apply_tool_request_middleware=apply_tool_request_middleware,
            PluginContext=PluginContext,
            PluginManager=PluginManager,
            PluginManifest=PluginManifest,
            VALID_HOOKS=set(VALID_HOOKS),
            VALID_MIDDLEWARE=set(VALID_MIDDLEWARE),
            resolve_plugin_command_result=resolve_plugin_command_result,
            run_llm_execution_middleware=run_llm_execution_middleware,
            run_tool_execution_middleware=run_tool_execution_middleware,
            registry=registry,
        )

    try:
        return _try()
    except Exception:
        if env_path:
            # An explicit contract checkout is authoritative in CI. Do not turn
            # import or layout drift into a misleading skipped-green build.
            raise

    for root in candidates:
        if not (root / "hermes_cli" / "plugins.py").exists():
            continue
        sys.path.insert(0, str(root))
        try:
            return _try()
        except Exception:
            continue
    return None


_REAL = _import_real_hermes()


class _RecordingCtx:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.calls.append(kwargs)


@hpk.tool(
    toolset="probe",
    requires_env=["PROBE_KEY"],
    params={
        "channel_id_or_url": hpk.str_arg(
            "A Discord channel id or URL", required=True, example="123456789012345678"
        ),
        "limit": hpk.int_arg("How many", minimum=1, maximum=100),
    },
)
def hpk_contract_probe(args, **kwargs):
    """Probe tool used only by the hermes contract tests."""
    return {"echo": args.get("channel_id_or_url")}


_SPEC = getattr(hpk_contract_probe, "_hpk_tool_spec")


@hpk.command(
    "hpk-contract-probe",
    description="Probe command registration.",
    args_hint="<value>",
)
async def hpk_command_contract_probe(raw_args):
    return f"command:{raw_args}"


@unittest.skipUnless(_REAL is not None, "hermes-agent source not importable")
class HermesContractTests(unittest.TestCase):
    """Validate the kit's output against genuine hermes-agent runtime APIs."""

    def _register_probe(self):
        reg = _REAL.registry
        reg.register(
            name=_SPEC["name"],
            toolset=_SPEC["toolset"],
            schema=_SPEC["schema"],
            handler=hpk_contract_probe,
            requires_env=_SPEC["requires_env"],
            description=_SPEC["schema"]["description"],
            emoji=_SPEC["emoji"],
        )
        self.addCleanup(reg.deregister, _SPEC["name"])
        return reg

    def test_kit_schema_survives_real_registry_conversion(self) -> None:
        # registry.get_definitions does the exact {**schema, "name": ...} spread the
        # model receives. The kit's parameters wrapper must survive it populated.
        reg = self._register_probe()
        defs = reg.get_definitions({_SPEC["name"]})
        fn = next(d["function"] for d in defs if d["function"]["name"] == _SPEC["name"])

        self.assertIn("parameters", fn, "model would receive a tool with no parameters")
        props = fn["parameters"]["properties"]
        self.assertTrue(props, "parameters.properties is empty — the empty-{} failure mode")
        self.assertIn("channel_id_or_url", props)
        self.assertEqual(fn["parameters"]["required"], ["channel_id_or_url"])

    def test_kit_handler_dispatches_through_real_registry(self) -> None:
        # The runtime calls handler(args, **kwargs) and expects a JSON string.
        reg = self._register_probe()

        ok = json.loads(reg.dispatch(_SPEC["name"], {"channel_id_or_url": "999"}))
        self.assertTrue(ok["success"])
        self.assertEqual(ok["data"]["echo"], "999")

        # A missing required arg fails in-band (no exception) with an instructive error.
        missing = json.loads(reg.dispatch(_SPEC["name"], {}))
        self.assertFalse(missing["success"])
        self.assertIn("channel_id_or_url", missing["error"])

    def test_register_all_binds_to_real_plugincontext_signature(self) -> None:
        # If hermes renames/removes a register_tool parameter the kit passes, this fails.
        ctx = _RecordingCtx()
        hpk.register_all(ctx, __name__)
        self.assertTrue(ctx.calls)
        sig = inspect.signature(_REAL.PluginContext.register_tool)
        for call in ctx.calls:
            try:
                sig.bind(None, **call)  # None stands in for self
            except TypeError as exc:  # pragma: no cover - failure path
                self.fail(f"register_all call does not match PluginContext.register_tool: {exc}")

    def test_lifecycle_registration_invokes_hook_and_resolves_qualified_skill(self) -> None:
        @hpk.hook("pre_llm_call")
        def contract_hook(**kwargs):
            return {"context": kwargs["message"]}

        module = types.ModuleType("contract_lifecycle_plugin")
        module.contract_hook = contract_hook
        manager = _REAL.PluginManager()
        manifest = _REAL.PluginManifest(name="contract-plugin")
        ctx = _REAL.PluginContext(manifest, manager)

        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("# Contract skill\n")
            with self.assertLogs("contract_lifecycle_plugin", level="INFO") as cap:
                summary = hpk.register_plugin(
                    ctx,
                    module,
                    skills=(hpk.plugin_skill("probe", path, "Contract probe"),),
                )
            self.assertEqual(summary.hooks, ("pre_llm_call",))
            self.assertEqual(len(cap.records), 1)
            self.assertIn(
                "plugin=contract-plugin; commands=<none>; "
                "cli_commands=<none>; tools=<none>; middlewares=<none>; "
                "hooks=pre_llm_call; skills=probe; "
                "skipped_optional_skills=<none>",
                cap.records[0].getMessage(),
            )
            self.assertEqual(
                manager.invoke_hook("pre_llm_call", message="gateway-shaped"),
                [{"context": "gateway-shaped"}],
            )
            self.assertEqual(manager.find_plugin_skill("contract-plugin:probe"), path)

    def test_command_registers_and_dispatches_through_real_plugin_context(self) -> None:
        manager = _REAL.PluginManager()
        manifest = _REAL.PluginManifest(name="contract-plugin")
        ctx = _REAL.PluginContext(manifest, manager)
        module = types.ModuleType("contract_command_plugin")
        module.hpk_command_contract_probe = hpk_command_contract_probe

        summary = hpk.register_plugin(ctx, module)

        self.assertEqual(summary.commands, ("hpk-contract-probe",))
        entry = manager._plugin_commands["hpk-contract-probe"]
        self.assertEqual(entry["description"], "Probe command registration.")
        self.assertEqual(entry["args_hint"], "<value>")
        result = entry["handler"]("exact raw args")
        self.assertEqual(
            _REAL.resolve_plugin_command_result(result),
            "command:exact raw args",
        )

    def test_cli_command_registers_and_dispatches_through_real_plugin_context(self) -> None:
        def setup_parser(parser):
            parser.add_argument("--scope", required=True)

        @hpk.command(
            "hpk-contract",
            type=hpk.CommandType.CLI,
            help="Run the plugin-kit contract probe",
            description="Exercise terminal CLI command registration.",
            setup_fn=setup_parser,
        )
        def cli_handler(args):
            return f"cli:{args.scope}"

        manager = _REAL.PluginManager()
        manifest = _REAL.PluginManifest(name="contract-plugin")
        ctx = _REAL.PluginContext(manifest, manager)
        module = types.ModuleType("contract_cli_command_plugin")
        module.cli_handler = cli_handler

        summary = hpk.register_plugin(ctx, module)

        self.assertEqual(summary.cli_commands, ("hpk-contract",))
        entry = manager._cli_commands["hpk-contract"]
        parser = argparse.ArgumentParser()
        entry["setup_fn"](parser)
        args = parser.parse_args(["--scope", "exact"])
        self.assertEqual(entry["handler_fn"](args), "cli:exact")

    def test_middleware_registers_and_runs_all_real_hermes_contracts(self) -> None:
        request_calls: list[tuple[str, dict]] = []
        execution_calls: list[tuple[str, dict]] = []

        @hpk.middleware(hpk.MiddlewareKind.TOOL_REQUEST)
        def rewrite_tool_request(**kwargs):
            request_calls.append(("tool_request", kwargs))
            return {
                "args": {**kwargs["args"], "rewritten": True},
                "source": "contract-test",
            }

        @hpk.middleware(hpk.MiddlewareKind.LLM_REQUEST)
        def rewrite_llm_request(**kwargs):
            request_calls.append(("llm_request", kwargs))
            return {
                "request": {**kwargs["request"], "rewritten": True},
                "source": "contract-test",
            }

        @hpk.middleware(hpk.MiddlewareKind.TOOL_EXECUTION)
        def wrap_tool_execution(**kwargs):
            execution_calls.append(("tool_execution", kwargs))
            result = kwargs["next_call"]({**kwargs["args"], "wrapped": True})
            return {"middleware": "tool", "result": result}

        @hpk.middleware(hpk.MiddlewareKind.LLM_EXECUTION)
        def wrap_llm_execution(**kwargs):
            execution_calls.append(("llm_execution", kwargs))
            result = kwargs["next_call"](
                {**kwargs["request"], "wrapped": True}
            )
            return {"middleware": "llm", "result": result}

        module = types.ModuleType("contract_middleware_plugin")
        module.rewrite_tool_request = rewrite_tool_request
        module.rewrite_llm_request = rewrite_llm_request
        module.wrap_tool_execution = wrap_tool_execution
        module.wrap_llm_execution = wrap_llm_execution

        manager = _REAL.PluginManager()
        manifest = _REAL.PluginManifest(name="contract-plugin")
        ctx = _REAL.PluginContext(manifest, manager)
        summary = hpk.register_plugin(ctx, module)

        expected_kinds = tuple(sorted(kind.value for kind in hpk.MiddlewareKind))
        self.assertEqual(summary.middlewares, expected_kinds)
        self.assertEqual(set(manager._middleware), set(_REAL.VALID_MIDDLEWARE))

        with patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            tool_request = _REAL.apply_tool_request_middleware(
                "contract_tool",
                {"value": "original"},
                session_id="session-1",
            )
            llm_request = _REAL.apply_llm_request_middleware(
                {"model": "contract-model"},
                session_id="session-1",
            )
            tool_execution = _REAL.run_tool_execution_middleware(
                "contract_tool",
                {"value": "effective"},
                lambda args: {"terminal": args},
                session_id="session-1",
            )
            llm_execution = _REAL.run_llm_execution_middleware(
                {"model": "contract-model"},
                lambda request: {"terminal": request},
                session_id="session-1",
            )

        self.assertTrue(tool_request.changed)
        self.assertEqual(
            tool_request.payload,
            {"value": "original", "rewritten": True},
        )
        self.assertEqual(tool_request.trace, [{"source": "contract-test"}])
        self.assertTrue(llm_request.changed)
        self.assertEqual(
            llm_request.payload,
            {"model": "contract-model", "rewritten": True},
        )
        self.assertEqual(
            tool_execution,
            {
                "middleware": "tool",
                "result": {
                    "terminal": {"value": "effective", "wrapped": True}
                },
            },
        )
        self.assertEqual(
            llm_execution,
            {
                "middleware": "llm",
                "result": {
                    "terminal": {
                        "model": "contract-model",
                        "wrapped": True,
                    }
                },
            },
        )
        self.assertEqual(
            [name for name, _kwargs in request_calls],
            ["tool_request", "llm_request"],
        )
        self.assertEqual(
            [name for name, _kwargs in execution_calls],
            ["tool_execution", "llm_execution"],
        )
        for _name, kwargs in request_calls + execution_calls:
            self.assertEqual(
                kwargs["middleware_schema_version"],
                "hermes.middleware.v1",
            )

    def test_lifecycle_calls_bind_to_real_plugincontext_signatures(self) -> None:
        command_sig = inspect.signature(_REAL.PluginContext.register_command)
        command_sig.bind(
            None,
            name="probe-command",
            handler=lambda raw_args: raw_args,
            description="Probe command.",
            args_hint="<value>",
        )

        cli_command_sig = inspect.signature(_REAL.PluginContext.register_cli_command)
        cli_command_sig.bind(
            None,
            name="probe",
            help="Run the probe",
            setup_fn=lambda parser: None,
            handler_fn=lambda args: args,
            description="Probe terminal command.",
        )

        hook_sig = inspect.signature(_REAL.PluginContext.register_hook)
        hook_sig.bind(None, "pre_llm_call", lambda **kwargs: None)

        middleware_sig = inspect.signature(_REAL.PluginContext.register_middleware)
        middleware_sig.bind(None, "tool_request", lambda **kwargs: None)

        skill_sig = inspect.signature(_REAL.PluginContext.register_skill)
        skill_sig.bind(
            None,
            name="probe",
            path=Path("SKILL.md"),
            description="Probe",
        )

    def test_host_tool_invocation_reaches_real_telegram_media_contract(self) -> None:
        """Exercise Hermes media parsing and Telegram formatting without network I/O."""
        import asyncio
        from tempfile import TemporaryDirectory

        from gateway import config as gateway_config  # type: ignore
        from gateway.config import Platform  # type: ignore
        from tools import interrupt as interrupt_module  # type: ignore

        telegram_config = types.SimpleNamespace(
            enabled=True,
            token="contract-token",
            extra={},
        )
        config = types.SimpleNamespace(
            platforms={Platform.TELEGRAM: telegram_config},
            get_home_channel=lambda _platform: None,
        )

        bot = types.SimpleNamespace(
            initialize=AsyncMock(),
            shutdown=AsyncMock(),
            send_message=AsyncMock(),
            send_photo=AsyncMock(
                return_value=types.SimpleNamespace(message_id=42)
            ),
            send_video=AsyncMock(),
            send_voice=AsyncMock(
                return_value=types.SimpleNamespace(message_id=43)
            ),
            send_audio=AsyncMock(),
            send_document=AsyncMock(),
        )
        bot_factory = Mock(return_value=bot)

        telegram_module = types.ModuleType("telegram")
        telegram_module.Bot = bot_factory
        telegram_constants = types.ModuleType("telegram.constants")
        telegram_constants.ParseMode = types.SimpleNamespace(
            HTML="HTML",
            MARKDOWN_V2="MarkdownV2",
        )
        telegram_module.constants = telegram_constants

        class TelegramAdapter:
            MAX_MESSAGE_LENGTH = 4096

            @staticmethod
            def format_message(message: str) -> str:
                return message

            @staticmethod
            def _message_thread_id_for_send(value: str) -> int | None:
                return None if value == "1" else int(value)

        telegram_adapter = types.ModuleType("plugins.platforms.telegram.adapter")
        telegram_adapter.TelegramAdapter = TelegramAdapter
        telegram_adapter.register = Mock()

        model_tools = types.ModuleType("model_tools")
        model_tools._run_async = asyncio.run
        mirror = types.ModuleType("gateway.mirror")
        mirror.mirror_to_session = Mock(return_value=False)
        session_context = types.ModuleType("gateway.session_context")
        session_context.get_session_env = Mock(return_value="")

        with TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "avatars" / "generated" / "contract-probe.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            voice_path = Path(tmp) / "voice-staging" / "contract-probe.ogg"
            voice_path.parent.mkdir(parents=True)
            voice_path.write_bytes(b"OggS" + b"\x00" * 32)
            args = {
                "action": "send",
                "target": "telegram:8670382527",
                "message": f"MEDIA:{image_path}",
            }

            with (
                patch.dict(
                    sys.modules,
                    {
                        "telegram": telegram_module,
                        "telegram.constants": telegram_constants,
                        "plugins.platforms.telegram.adapter": telegram_adapter,
                        "model_tools": model_tools,
                        "gateway.mirror": mirror,
                        "gateway.session_context": session_context,
                    },
                ),
                patch.dict(
                    os.environ,
                    {
                        "HERMES_MEDIA_DELIVERY_STRICT": "0",
                        "TELEGRAM_PROXY": "",
                    },
                    clear=False,
                ),
                patch.object(
                    gateway_config,
                    "load_gateway_config",
                    return_value=config,
                ),
                patch.object(
                    interrupt_module,
                    "is_interrupted",
                    return_value=False,
                ),
            ):
                result = json.loads(hpk.invoke_host_tool("send_message", args))
                voice_result = hpk.deliver_media(
                    hpk.MediaPayload(voice_path, hpk.MediaType.VOICE),
                    target="telegram:8670382527",
                )
                spoiler_result = hpk.deliver_media(
                    hpk.MediaPayload(image_path, spoiler=True),
                    target="telegram:-5372910000:42",
                )

            self.assertTrue(result.get("success"), result)
            self.assertEqual(result["platform"], "telegram")
            self.assertEqual(result["chat_id"], "8670382527")
            self.assertEqual(result["message_id"], "42")
            self.assertTrue(voice_result.success, voice_result)
            self.assertEqual(voice_result.media_type, hpk.MediaType.VOICE)
            self.assertEqual(voice_result.host_result["message_id"], "43")
            self.assertTrue(spoiler_result.success, spoiler_result)
            self.assertTrue(spoiler_result.spoiler)
            self.assertEqual(bot_factory.call_count, 3)
            self.assertTrue(
                all(item.kwargs == {"token": "contract-token"} for item in bot_factory.call_args_list)
            )
            bot.send_message.assert_not_awaited()
            self.assertEqual(bot.send_photo.await_count, 2)
            bot.send_voice.assert_awaited_once()
            photo_call = bot.send_photo.await_args_list[0]
            self.assertEqual(photo_call.kwargs["chat_id"], 8670382527)
            self.assertEqual(
                photo_call.kwargs["photo"].name,
                str(image_path.resolve()),
            )
            voice_call = bot.send_voice.await_args
            self.assertEqual(voice_call.kwargs["chat_id"], 8670382527)
            self.assertEqual(
                voice_call.kwargs["voice"].name,
                str(voice_path.resolve()),
            )
            spoiler_call = bot.send_photo.await_args_list[1]
            self.assertEqual(spoiler_call.kwargs["chat_id"], -5372910000)
            self.assertEqual(spoiler_call.kwargs["message_thread_id"], 42)
            self.assertTrue(spoiler_call.kwargs["has_spoiler"])
            bot.initialize.assert_awaited_once()
            bot.shutdown.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
