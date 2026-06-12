"""Contract tests against the *real* hermes-agent source.

These keep the kit relevant: they import the genuine ``PluginContext`` and tool
``registry`` from a hermes-agent checkout and prove that what the kit emits still
satisfies the runtime contract — most importantly that a kit-built schema, run
through the registry's real OpenAI-tool conversion, yields a function whose
``parameters`` are populated (the empty-``{}`` failure mode this kit exists to
prevent).

The whole module is skipped when hermes-agent is not importable, so the suite
stays green standalone and in public CI. Point it at a checkout with
``HERMES_AGENT_PATH`` (a CI job can ``actions/checkout`` NousResearch/hermes-agent
and set it); locally it discovers ``~/hermes-agent`` automatically.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import types
import unittest
from pathlib import Path

import hermes_plugin_kit as hpk


def _import_real_hermes():
    """Return a namespace with the real PluginContext / VALID_HOOKS / registry, or None."""
    candidates: list[Path] = []
    env_path = os.environ.get("HERMES_AGENT_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.home() / "hermes-agent")
    candidates.append(Path.home() / ".hermes" / "hermes-agent")

    def _try():
        from hermes_cli.plugins import PluginContext, VALID_HOOKS  # type: ignore
        from tools.registry import registry  # type: ignore

        return types.SimpleNamespace(
            PluginContext=PluginContext, VALID_HOOKS=set(VALID_HOOKS), registry=registry
        )

    try:
        return _try()
    except Exception:
        pass

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


if __name__ == "__main__":
    unittest.main()
