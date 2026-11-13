from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest

import hermes_plugin_kit as hpk


def _check() -> bool:
    return True


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


if __name__ == "__main__":
    unittest.main()
