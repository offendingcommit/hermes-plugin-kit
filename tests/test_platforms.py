from __future__ import annotations

import dataclasses
import logging
import subprocess
import sys
import textwrap
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import hermes_plugin_kit as hpk
from hermes_plugin_kit.testing import RecordingPluginContext, receipt_fields

# PlatformEntry fields every pinned Hermes has, mirroring register_platform's
# named parameters plus the two fields the host fills itself.
_BASE_ENTRY_FIELDS = (
    "name", "label", "adapter_factory", "check_fn", "validate_config",
    "required_env", "install_hint", "source", "plugin_name",
)


def _check() -> bool:
    return True


class _FakeRegistry:
    """Stand-in for the host's PlatformRegistry: concrete and deferred state."""

    def __init__(self) -> None:
        self.entries: dict[str, object] = {}
        self.deferred: dict[str, object] = {}

    @staticmethod
    def current_scope_key() -> str:
        return "<home>"

    def is_registered(self, name: str) -> bool:
        return name in self.entries or name in self.deferred

    def snapshot_registration(self, name: str, *, scope=None):
        return self.entries.get(name), self.deferred.get(name)


@contextmanager
def fake_platform_host(
    *,
    extra_fields=("emoji", "allowed_users_env", "trusted_inbound"),
    core=("telegram",),
):
    """Install a stand-in for the host's platform registry and base adapter."""
    gateway = types.ModuleType("gateway")
    registry_module = types.ModuleType("gateway.platform_registry")
    platforms_package = types.ModuleType("gateway.platforms")
    base_module = types.ModuleType("gateway.platforms.base")

    class BasePlatformAdapter:
        def __init__(self, config, platform=None) -> None:
            self.config = config

    entry_type = dataclasses.make_dataclass(
        "PlatformEntry",
        [(field, object, None) for field in (*_BASE_ENTRY_FIELDS, *extra_fields)],
    )
    registry = _FakeRegistry()
    registry_module.PlatformEntry = entry_type
    registry_module.PlatformRegistry = _FakeRegistry
    registry_module.platform_registry = registry
    registry_module.core_ships_platform = lambda name: name in core
    base_module.BasePlatformAdapter = BasePlatformAdapter
    gateway.platform_registry = registry_module
    gateway.platforms = platforms_package
    platforms_package.base = base_module
    with patch.dict(
        sys.modules,
        {
            "gateway": gateway,
            "gateway.platform_registry": registry_module,
            "gateway.platforms": platforms_package,
            "gateway.platforms.base": base_module,
        },
    ):
        yield types.SimpleNamespace(
            BasePlatformAdapter=BasePlatformAdapter,
            registry=registry,
            module=registry_module,
            entry_type=entry_type,
        )


def _module(**attrs) -> types.ModuleType:
    module = types.ModuleType("sample_plugin")
    for name, value in attrs.items():
        setattr(module, name, value)
    return module


@hpk.tool(toolset="sample", params={"q": hpk.str_arg("query", required=True)})
def sample_read_thread(args, **kwargs):
    """Read a thread."""
    return {"ok": True}


def _register(ctx, module, **kwargs):
    """Run register_plugin and return (summary, receipt message)."""
    logger = logging.getLogger("tests.platforms")
    with RecordingCaptureLogs(logger) as cap:
        summary = hpk.register_plugin(ctx, module, logger=logger, **kwargs)
    return summary, cap.message


class RecordingCaptureLogs:
    """Capture the one receipt line, without assertLogs' failure on silence."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.records: list[logging.LogRecord] = []

    def __enter__(self):
        handler = logging.Handler()
        handler.emit = self.records.append
        self._handler = handler
        self._level = self.logger.level
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc) -> None:
        self.logger.removeHandler(self._handler)
        self.logger.setLevel(self._level)

    @property
    def message(self) -> str:
        receipts = [
            r.getMessage() for r in self.records
            if "registered plugin lifecycle" in r.getMessage()
        ]
        return receipts[-1] if receipts else ""

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == logging.WARNING]


class PlatformAnnotationTests(unittest.TestCase):
    """Decoration-time behavior: what needs no Hermes host to judge."""

    def test_annotation_returns_the_same_class_carrying_its_spec(self) -> None:
        class WebChat:
            pass

        annotated = hpk.platform("web_chat", label="Web Chat", check=_check)(WebChat)

        self.assertIs(annotated, WebChat)
        spec = vars(WebChat)["_hpk_platform_spec"]
        self.assertEqual(spec["name"], "web_chat")
        self.assertEqual(spec["label"], "Web Chat")
        self.assertIs(spec["check_fn"], _check)
        self.assertEqual(spec["options"], {})
        self.assertNotIn("trusted_inbound", spec["options"])

    def test_name_not_already_in_host_form_is_rejected(self) -> None:
        for bad in ("Web_Chat", " web_chat ", ""):
            with self.subTest(name=bad):
                with self.assertRaisesRegex(ValueError, "lowercase"):
                    hpk.platform(bad, label="Web Chat", check=_check)

    def test_empty_label_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "label"):
            hpk.platform("web_chat", label="  ", check=_check)

    def test_non_callable_check_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            hpk.platform("web_chat", label="Web Chat", check="yes")

    def test_non_callable_validate_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            hpk.platform("web_chat", label="Web Chat", check=_check, validate=1)

    def test_annotating_a_function_is_rejected(self) -> None:
        decorate = hpk.platform("web_chat", label="Web Chat", check=_check)
        with self.assertRaisesRegex(TypeError, "class"):
            decorate(_check)

    def test_optional_option_that_was_not_passed_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "display_tier"):
            hpk.platform(
                "web_chat", label="Web Chat", check=_check,
                options={"emoji": "x"}, optional_options={"display_tier"},
            )

    def test_host_owned_options_are_rejected(self) -> None:
        for key in ("source", "plugin_name"):
            with self.subTest(option=key):
                with self.assertRaisesRegex(ValueError, "host owns"):
                    hpk.platform(
                        "web_chat", label="Web Chat", check=_check,
                        options={key: "x"},
                    )

    def test_option_duplicating_a_named_argument_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "install_hint"):
            hpk.platform(
                "web_chat", label="Web Chat", check=_check,
                options={"install_hint": "pip install x"},
            )

    def test_non_identifier_option_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identifier"):
            hpk.platform(
                "web_chat", label="Web Chat", check=_check,
                options={"not-a-name": 1},
            )

    def test_options_are_copied_not_aliased(self) -> None:
        options = {"emoji": "x"}

        @hpk.platform("web_chat", label="Web Chat", check=_check, options=options)
        class WebChat:
            pass

        options["emoji"] = "changed"
        self.assertEqual(vars(WebChat)["_hpk_platform_spec"]["options"], {"emoji": "x"})

    def test_annotating_does_not_import_hermes(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            import hermes_plugin_kit as hpk

            @hpk.platform("web_chat", label="Web Chat", check=lambda: True)
            class WebChat:
                pass

            leaked = sorted(m for m in sys.modules if m == "gateway" or m.startswith("gateway."))
            assert not leaked, leaked
            print("ok")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


class PlatformRegistrationTests(unittest.TestCase):
    """register_plugin discovers, checks and registers annotated platforms."""

    def _platform(self, host, name="web_chat", base=None, **kwargs):
        @hpk.platform(name, label="Web Chat", check=_check, **kwargs)
        class Adapter(base or host.BasePlatformAdapter):
            pass

        return Adapter

    def test_registers_a_platform_alongside_a_tool(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host, options={"emoji": "💬"})
            ctx = RecordingPluginContext()
            summary, receipt = _register(
                ctx, _module(WebChat=adapter, sample_read_thread=sample_read_thread)
            )

        self.assertEqual([t["name"] for t in ctx.tools], ["sample_read_thread"])
        self.assertEqual(len(ctx.platforms), 1)
        registered = ctx.platforms[0]
        self.assertEqual(registered["name"], "web_chat")
        self.assertEqual(registered["label"], "Web Chat")
        self.assertIs(registered["adapter_factory"], adapter)
        self.assertIs(registered["check_fn"], _check)
        self.assertEqual(registered["emoji"], "💬")
        self.assertNotIn("trusted_inbound", registered)
        self.assertEqual(summary.platforms, ("web_chat",))
        self.assertEqual(summary.replaced_platforms, ())
        self.assertTrue(
            receipt.endswith("platforms=web_chat; replaced_platforms=<none>"), receipt
        )

    def test_two_declarations_sharing_a_name_register_nothing(self) -> None:
        with fake_platform_host() as host:
            first = self._platform(host)
            second = self._platform(host)
            ctx = RecordingPluginContext()
            with self.assertRaisesRegex(ValueError, "web_chat"):
                _register(
                    ctx,
                    _module(A=first, B=second, sample_read_thread=sample_read_thread),
                )

        self.assertEqual(ctx.calls, [])

    def test_unknown_option_fails_before_anything_registers(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host, options={"display_tier": "minimal"})
            ctx = RecordingPluginContext()
            with self.assertRaisesRegex(ValueError, "display_tier"):
                _register(
                    ctx, _module(WebChat=adapter, sample_read_thread=sample_read_thread)
                )

        self.assertEqual(ctx.calls, [])

    def test_optional_unknown_option_is_dropped_and_named(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(
                host,
                options={"display_tier": "minimal", "emoji": "💬"},
                optional_options={"display_tier"},
            )
            ctx = RecordingPluginContext()
            logger = logging.getLogger("tests.platforms")
            with RecordingCaptureLogs(logger) as cap:
                hpk.register_plugin(ctx, _module(WebChat=adapter), logger=logger)

        self.assertNotIn("display_tier", ctx.platforms[0])
        self.assertEqual(ctx.platforms[0]["emoji"], "💬")
        self.assertTrue(any("display_tier" in w for w in cap.warnings()), cap.warnings())

    def test_class_not_subclassing_the_host_adapter_is_rejected(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host, base=object)
            ctx = RecordingPluginContext()
            with self.assertRaisesRegex(TypeError, "Adapter"):
                _register(
                    ctx, _module(WebChat=adapter, sample_read_thread=sample_read_thread)
                )

        self.assertEqual(ctx.calls, [])

    def test_trusted_inbound_on_a_core_platform_fails_in_preflight(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(
                host, name="telegram", options={"trusted_inbound": True}
            )
            ctx = RecordingPluginContext(config={"replace_platforms": ["telegram"]})
            with self.assertRaisesRegex(ValueError, "trusted_inbound"):
                _register(
                    ctx, _module(Tg=adapter, sample_read_thread=sample_read_thread)
                )

        self.assertEqual(ctx.calls, [])

    def test_host_refusal_raises_naming_the_platform(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host)
            ctx = RecordingPluginContext(refuse_platforms=("web_chat",))
            with self.assertRaisesRegex(RuntimeError, "web_chat"):
                _register(ctx, _module(WebChat=adapter))

    def test_host_refusal_rolls_back_platforms_already_registered(self) -> None:
        with fake_platform_host() as host:
            first = self._platform(host, name="aaa_chat")
            second = self._platform(host, name="zzz_chat")
            ctx = RecordingPluginContext(refuse_platforms=("zzz_chat",))
            with self.assertRaisesRegex(RuntimeError, "zzz_chat"):
                _register(
                    ctx,
                    _module(A=first, Z=second, sample_read_thread=sample_read_thread),
                )

        registered = [name for name, _, kwargs in ctx.calls]
        self.assertEqual(registered, ["register_platform", "register_platform"])
        self.assertEqual(ctx.platforms, [])
        self.assertEqual(ctx.tools, [])

    def test_context_without_register_platform(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host)
            ctx = RecordingPluginContext(missing_registrars=("register_platform",))
            with self.assertRaisesRegex(RuntimeError, "register_platform"):
                _register(ctx, _module(WebChat=adapter))
            summary, _ = _register(ctx, _module(sample_read_thread=sample_read_thread))

        self.assertEqual(summary.tools, ("sample_read_thread",))

    def test_missing_host_platform_module_refuses_registration(self) -> None:
        @hpk.platform("web_chat", label="Web Chat", check=_check)
        class Adapter:
            pass

        ctx = RecordingPluginContext()
        with patch.dict(sys.modules, {"gateway": None}):
            with self.assertRaisesRegex(RuntimeError, "platform"):
                _register(
                    ctx, _module(WebChat=Adapter, sample_read_thread=sample_read_thread)
                )

        self.assertEqual(ctx.calls, [])

    def test_iterable_holding_an_annotated_class_registers_it(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host)
            ctx = RecordingPluginContext()
            summary, _ = _register(ctx, [adapter, sample_read_thread])

        self.assertEqual(summary.platforms, ("web_chat",))
        self.assertEqual(summary.tools, ("sample_read_thread",))

    def test_unannotated_subclass_is_not_a_second_declaration(self) -> None:
        with fake_platform_host() as host:
            adapter = self._platform(host)

            class Variant(adapter):
                pass

            ctx = RecordingPluginContext()
            summary, _ = _register(ctx, _module(WebChat=adapter, Variant=Variant))

        self.assertEqual(summary.platforms, ("web_chat",))
        self.assertEqual(len(ctx.platforms), 1)
        self.assertIs(ctx.platforms[0]["adapter_factory"], adapter)

    def test_receipt_without_platforms_keeps_every_old_field_in_place(self) -> None:
        ctx = RecordingPluginContext()
        _, receipt = _register(ctx, _module(sample_read_thread=sample_read_thread))

        fields = list(receipt_fields(receipt))
        self.assertEqual(
            fields,
            [
                "commands", "cli_commands", "tools", "middlewares", "hooks",
                "skills", "skipped_optional_skills", "memory_providers",
                "image_gen_providers", "video_gen_providers", "capabilities",
                "context_engine", "context_engine_registration",
                "platforms", "replaced_platforms",
            ],
        )
        self.assertTrue(
            receipt.endswith("platforms=<none>; replaced_platforms=<none>"), receipt
        )


if __name__ == "__main__":
    unittest.main()
