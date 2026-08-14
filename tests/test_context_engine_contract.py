"""Focused contract for Amber's exact deployed Hermes context-engine seam."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import hermes_plugin_kit as hpk


def _import_deployed_host():
    root_value = os.environ.get("HERMES_AGENT_PATH")
    if not root_value:
        return None
    root = Path(root_value)
    if not (root / "hermes_cli" / "plugins.py").exists():
        raise FileNotFoundError(
            f"HERMES_AGENT_PATH has no hermes_cli/plugins.py: {root}"
        )
    sys.path.insert(0, str(root))
    from agent.context_engine import ContextEngine  # type: ignore
    from hermes_cli.plugins import (  # type: ignore
        PluginContext,
        PluginManager,
        PluginManifest,
    )

    return ContextEngine, PluginContext, PluginManager, PluginManifest


_HOST = _import_deployed_host()


@unittest.skipUnless(_HOST is not None, "exact Hermes host source not configured")
class DeployedContextEngineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = os.environ["HERMES_AGENT_PATH"]
        commit = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(f"deployed hermes-agent context-engine contract commit: {commit}")

    def test_registers_singleton_and_deep_copies_without_tool_duplication(self) -> None:
        ContextEngine, PluginContext, PluginManager, PluginManifest = _HOST

        class ContractEngine(ContextEngine):
            @property
            def name(self):
                return "continuity-contract"

            def update_from_response(self, usage):
                self.last_total_tokens = usage.get("total_tokens", 0)

            def should_compress(self, prompt_tokens=None):
                return False

            def compress(self, messages, **kwargs):
                return messages

            def get_tool_schemas(self):
                return [
                    {
                        "name": "continuity_recover",
                        "description": "Recover bounded context.",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ]

            def handle_tool_call(self, name, args, **kwargs):
                return json.dumps({"name": name})

        manager = PluginManager()
        manifest = PluginManifest(name="contract-plugin")
        ctx = PluginContext(manifest, manager)
        engine = ContractEngine()

        summary = hpk.register_plugin(ctx, (), context_engine=engine)

        self.assertIs(manager._context_engine, engine)
        self.assertEqual(summary.context_engine, "continuity-contract")
        self.assertEqual(summary.context_engine_registration, "accepted")
        activated = copy.deepcopy(manager._context_engine)
        self.assertIsNot(activated, engine)
        self.assertEqual(activated.name, engine.name)
        self.assertNotIn("continuity_recover", manager._plugin_tool_names)

        with self.assertRaisesRegex(RuntimeError, "context engine.*registered"):
            hpk.register_plugin(ctx, (), context_engine=ContractEngine())
        self.assertIs(manager._context_engine, engine)


if __name__ == "__main__":
    unittest.main()
