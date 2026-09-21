"""Contracts for the consumer-facing test harness.

The harness exists to catch one defect a hand-rolled fake cannot: the host
changing a registrar's signature underneath a plugin. These tests hold it to
that, then to the surrounding promises — usable with no host present, honest
about not having checked, and reachable from an installed package.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import hermes_plugin_kit as hpk
from hermes_plugin_kit import testing as hpk_testing


class HostV1:
    """The host shape the plugin was written against."""

    def register_tool(
        self, name, toolset, schema, handler, requires_env=None, description="", emoji=""
    ): ...

    def register_skill(self, name, path, description=""): ...


class HostAddedRequired:
    """Host gained a required parameter."""

    def register_tool(
        self,
        name,
        toolset,
        schema,
        handler,
        plugin_name,
        requires_env=None,
        description="",
        emoji="",
    ): ...

    def register_skill(self, name, path, description=""): ...


class HostRenamed:
    """Host renamed a parameter the plugin passes."""

    def register_tool(
        self, name, toolset, schema, handler, requires_env=None, help="", emoji=""
    ): ...

    def register_skill(self, name, path, description=""): ...


class HostMissingRegistrar:
    """Host no longer exposes a surface the plugin registers."""

    def register_tool(
        self, name, toolset, schema, handler, requires_env=None, description="", emoji=""
    ): ...


def _register_a_tool(ctx) -> None:
    """The registration call a plugin makes through the kit."""
    ctx.register_tool(
        name="probe_read_note",
        toolset="probe",
        schema={"type": "function", "function": {"name": "probe_read_note"}},
        handler=lambda args, **kwargs: {"ok": True},
        requires_env=None,
        description="Read a note.",
        emoji="*",
    )


def _register_a_skill(ctx) -> None:
    ctx.register_skill(name="probe-skill", path="skills/probe/SKILL.md", description="A probe skill.")


class DriftReplayTests(unittest.TestCase):
    """Replaying recorded calls through the real signature is the whole point."""

    def test_added_required_parameter_is_reported_by_name(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        _register_a_tool(ctx)

        report = ctx.check_against_host(HostAddedRequired)

        self.assertFalse(report.clean, report)
        self.assertEqual(["register_tool"], [d.registrar for d in report.drifts])
        self.assertIn("plugin_name", report.drifts[0].detail)

    def test_renamed_parameter_is_reported_as_unexpected(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        _register_a_tool(ctx)

        report = ctx.check_against_host(HostRenamed)

        self.assertFalse(report.clean, report)
        self.assertIn("description", report.drifts[0].detail)

    def test_positionally_renamed_parameter_is_reported(self) -> None:
        """Binding alone cannot see this: Python binds positionals by position."""

        class HostRenamedPositional:
            # Same arity as the real host, first parameter renamed.
            def register_hook(self, event_name, callback): ...

        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        ctx.register_hook("pre_tool_call", lambda *a, **k: None)

        report = ctx.check_against_host(HostRenamedPositional)

        self.assertFalse(report.clean, report)
        self.assertEqual(["register_hook"], [d.registrar for d in report.drifts])
        self.assertIn("hook_name", report.drifts[0].detail)
        self.assertIn("event_name", report.drifts[0].detail)

    def test_unrenamed_positional_host_stays_clean(self) -> None:
        class HostPositionalV1:
            def register_hook(self, hook_name, callback): ...

        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        ctx.register_hook("pre_tool_call", lambda *a, **k: None)

        self.assertTrue(ctx.check_against_host(HostPositionalV1).clean)

    def test_removed_registrar_is_reported(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        _register_a_skill(ctx)

        report = ctx.check_against_host(HostMissingRegistrar)

        self.assertFalse(report.clean, report)
        self.assertEqual(["register_skill"], [d.registrar for d in report.drifts])

    def test_unchanged_host_is_clean_and_preserves_the_recording(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        _register_a_tool(ctx)
        _register_a_skill(ctx)

        report = ctx.check_against_host(HostV1)

        self.assertTrue(report.clean, report)
        self.assertEqual((), report.drifts)
        self.assertEqual(["probe_read_note"], [t["name"] for t in ctx.tools])
        self.assertEqual(["probe-skill"], [s["name"] for s in ctx.skills])

    def test_strict_mode_fails_at_call_time_rather_than_on_replay(self) -> None:
        """A consumer calling the fake directly is invisible to a lane-side check."""
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin", strict_against=HostAddedRequired)

        with self.assertRaises(TypeError) as caught:
            _register_a_tool(ctx)

        self.assertIn("plugin_name", str(caught.exception))


class AbsentHostTests(unittest.TestCase):
    """An unchecked run must not read as a checked one."""

    def test_no_host_reports_unchecked_and_names_the_required_revision(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        _register_a_tool(ctx)

        report = ctx.check_against_host(None)

        self.assertFalse(report.checked)
        self.assertFalse(report.clean)
        self.assertIn(hpk_testing.DEPLOYED_HERMES_REVISION, report.detail)

    def test_deployed_revision_is_a_full_commit_sha(self) -> None:
        self.assertRegex(hpk_testing.DEPLOYED_HERMES_REVISION, r"^[0-9a-f]{40}$")


class HostShapeModeTests(unittest.TestCase):
    """Both branches of the kit's own capability probe must be reachable."""

    def setUp(self) -> None:
        # plugin_skill validates both paths against the filesystem.
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "SKILL.md").write_text(
            "---\nname: probe-skill\ndescription: A probe skill.\n---\n\n# probe\n"
        )
        (root / "references").mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.skill_path = str(root / "SKILL.md")
        self.references_dir = str(root / "references")

    def _skill(self, *, with_references: bool):
        return hpk.plugin_skill(
            name="probe-skill",
            path=self.skill_path,
            description="A probe skill.",
            references_dir=self.references_dir if with_references else None,
        )

    def test_references_dir_unsupported_host_still_registers_the_skill(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin", supports_references_dir=False
        )

        summary = hpk.register_plugin(
            ctx, [], skills=[self._skill(with_references=True)], plugin_name="probe-plugin"
        )

        self.assertEqual(("probe-skill",), summary.skills)
        self.assertNotIn("references_dir", ctx.skills[0])

    def test_references_dir_supported_host_receives_it(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin", supports_references_dir=True
        )

        hpk.register_plugin(
            ctx, [], skills=[self._skill(with_references=True)], plugin_name="probe-plugin"
        )

        self.assertEqual(self.references_dir, str(ctx.skills[0]["references_dir"]))

    def test_lying_probe_host_still_registers_the_skill(self) -> None:
        """A permissive signature can report support the host does not have.

        The kit probes `register_skill` for a `references_dir` keyword and
        retries without it on a TypeError naming that keyword. A `**kwargs`
        host satisfies the probe and can still reject the call, so the retry
        branch needs a host shape that lies.
        """
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin", references_dir_probe_lies=True
        )

        summary = hpk.register_plugin(
            ctx, [], skills=[self._skill(with_references=True)], plugin_name="probe-plugin"
        )

        self.assertEqual(("probe-skill",), summary.skills)
        self.assertNotIn("references_dir", ctx.skills[0])

    def test_unrelated_type_error_is_not_swallowed_by_the_retry(self) -> None:
        """The retry must be narrow: only a references_dir rejection."""
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin",
            register_skill_error=TypeError(
                "register_skill() missing 1 required positional argument"
            ),
        )

        with self.assertRaises(TypeError) as caught:
            hpk.register_plugin(
                ctx, [], skills=[self._skill(with_references=True)], plugin_name="probe-plugin"
            )

        self.assertIn("missing 1 required positional argument", str(caught.exception))

    def test_missing_registrar_surfaces_the_unsupported_context_error(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin", missing_registrars=("register_skill",)
        )
        with self.assertRaises(RuntimeError) as caught:
            hpk.register_plugin(
                ctx, [], skills=[self._skill(with_references=False)], plugin_name="probe-plugin"
            )

        self.assertIn("register_skill", str(caught.exception))


class ManifestTests(unittest.TestCase):
    """The single largest gap between the kit's own fake and a consumer's need."""

    def test_plugin_config_is_readable_through_load_plugin_config(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(
            name="probe-plugin", config={"threads_enabled": True}
        )

        self.assertEqual(
            {"threads_enabled": True}, hpk.load_plugin_config(ctx, "probe-plugin")
        )

    def test_receipt_identity_comes_from_the_manifest_name(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")

        with self.assertLogs("hermes_plugin_kit", level="INFO") as captured:
            hpk.register_plugin(ctx, [], plugin_name=None)

        self.assertIn("plugin=probe-plugin", "\n".join(captured.output))


class ReceiptFieldTests(unittest.TestCase):
    """The receipt's log order is the external contract, not the dataclass order."""

    def test_field_order_matches_the_emitted_receipt(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        with self.assertLogs("hermes_plugin_kit", level="INFO") as captured:
            hpk.register_plugin(ctx, [], plugin_name="probe-plugin")

        fields = hpk_testing.receipt_fields(captured.records[-1].getMessage())

        self.assertEqual(list(hpk_testing.RECEIPT_FIELD_ORDER), list(fields))

    def test_empty_surfaces_read_as_no_values(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        with self.assertLogs("hermes_plugin_kit", level="INFO") as captured:
            hpk.register_plugin(ctx, [], plugin_name="probe-plugin")

        fields = hpk_testing.receipt_fields(captured.records[-1].getMessage())

        self.assertEqual((), fields["tools"])

    def test_registered_names_appear_under_their_field(self) -> None:
        # Captured at the root, not under "hermes_plugin_kit": the kit logs the
        # receipt through the decorated handler's own module logger, so the
        # logger name depends on where the plugin's tools are defined.
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")

        @hpk.tool(name="probe_read_note", toolset="probe", description="Read a note.")
        def probe_read_note(args, **kwargs):
            return {"ok": True}

        with self.assertLogs(level="INFO") as captured:
            hpk.register_plugin(ctx, [probe_read_note], plugin_name="probe-plugin")

        fields = hpk_testing.receipt_fields(captured.records[-1].getMessage())

        self.assertEqual(("probe_read_note",), fields["tools"])
        self.assertEqual((), fields["hooks"])

    def test_declared_order_is_the_log_order_not_the_dataclass_order(self) -> None:
        """These genuinely differ; pinning the wrong one silently passes."""
        import dataclasses

        dataclass_order = [f.name for f in dataclasses.fields(hpk.RegistrationSummary)]

        self.assertNotEqual(dataclass_order, list(hpk_testing.RECEIPT_FIELD_ORDER))
        self.assertEqual(
            set(dataclass_order), set(hpk_testing.RECEIPT_FIELD_ORDER),
            "the two orders must hold the same fields, only sequenced differently",
        )


class RegistrationCheckTests(unittest.TestCase):
    """Contracts a consumer would otherwise copy out of the kit's own tests."""

    def _tool(self, name):
        @hpk.tool(name=name, toolset="probe", description=f"{name}.")
        def handler(args, **kwargs):
            return {"ok": True}

        return handler

    def test_sorted_registration_reports_clean(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        hpk.register_plugin(
            ctx, [self._tool("probe_zulu"), self._tool("probe_alpha")],
            plugin_name="probe-plugin",
        )

        result = hpk_testing.check_registration(ctx)

        self.assertTrue(result.ok, result)
        self.assertEqual((), result.problems)

    def test_unsorted_recording_is_reported(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        ctx.register_tool(
            name="probe_zulu", toolset="probe", schema={}, handler=None,
            requires_env=None, description="z", emoji="",
        )
        ctx.register_tool(
            name="probe_alpha", toolset="probe", schema={}, handler=None,
            requires_env=None, description="a", emoji="",
        )

        result = hpk_testing.check_registration(ctx)

        self.assertFalse(result.ok, result)
        self.assertTrue(any("order" in p for p in result.problems), result.problems)

    def test_duplicate_tool_name_is_reported(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        for _ in range(2):
            ctx.register_tool(
                name="probe_same", toolset="probe", schema={}, handler=None,
                requires_env=None, description="d", emoji="",
            )

        result = hpk_testing.check_registration(ctx)

        self.assertFalse(result.ok, result)
        self.assertTrue(any("duplicate" in p for p in result.problems), result.problems)

    def test_same_name_across_slash_and_cli_is_allowed(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        ctx.register_command(name="probe", handler=None, description="d")
        ctx.register_cli_command(name="probe", handler=None, description="d")

        self.assertTrue(hpk_testing.check_registration(ctx).ok)

    def test_flattened_tool_schema_is_reported(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        ctx.register_tool(
            name="probe_flat", toolset="probe",
            schema={"name": "probe_flat", "properties": {"a": {"type": "string"}}},
            handler=None, requires_env=None, description="d", emoji="",
        )

        result = hpk_testing.check_registration(ctx)

        self.assertFalse(result.ok, result)
        self.assertTrue(
            any("function.parameters" in p for p in result.problems), result.problems
        )

    def test_conforming_schema_passes(self) -> None:
        ctx = hpk_testing.RecordingPluginContext(name="probe-plugin")
        hpk.register_plugin(ctx, [self._tool("probe_ok")], plugin_name="probe-plugin")

        self.assertTrue(hpk_testing.check_registration(ctx).ok)


class ImportHygieneTests(unittest.TestCase):
    """The module ships in the wheel and must cost nothing until it is used."""

    def test_import_pulls_in_neither_hermes_nor_a_test_framework(self) -> None:
        probe = (
            "import sys; import hermes_plugin_kit.testing as t; "
            "leaked = sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'hermes_cli', 'hermes_state', 'agent', 'tools', 'pytest'}); "
            "print(','.join(leaked))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )

        self.assertEqual("", result.stdout.strip(), f"leaked imports: {result.stdout!r}")


if __name__ == "__main__":
    unittest.main()
