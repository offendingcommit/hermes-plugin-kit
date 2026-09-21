from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "skills"

_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def _relative_link_targets(text: str) -> list[str]:
    """Link targets that should resolve on disk, ignoring URLs and anchors."""
    targets = []
    for target in _LINK.findall(text):
        if "://" in target or target.startswith("#") or target.startswith("mailto:"):
            continue
        targets.append(target)
    return targets


class SkillDistributionTests(unittest.TestCase):
    def test_skills_are_self_contained_and_named_for_their_directory(self) -> None:
        skill_files = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(skill_files, "Expected at least one consumable skill")

        for skill_file in skill_files:
            text = skill_file.read_text()
            self.assertTrue(text.startswith("---\n"), skill_file)
            _, frontmatter, body = text.split("---", 2)
            metadata = yaml.safe_load(frontmatter)
            self.assertEqual(skill_file.parent.name, metadata["name"])
            self.assertTrue(metadata["description"])

            for target in _relative_link_targets(body):
                resolved = skill_file.parent / target.split("#", 1)[0]
                self.assertTrue(resolved.is_file(), f"Missing {target} from {skill_file}")


class SkillReferenceLinkTests(unittest.TestCase):
    """Reference files were never link-checked, so their links rotted unseen.

    The suite validated `SKILL.md` bodies only. Everything under `references/`
    — which is where the detailed routing lives, and the most likely place to
    name a path — went unchecked.
    """

    def _reference_files(self) -> list[Path]:
        return sorted(SKILLS.glob("*/references/*.md"))

    def test_there_are_reference_files_to_check(self) -> None:
        # A glob that silently matched nothing would make every assertion below
        # vacuous, and this test would be the only sign.
        self.assertTrue(self._reference_files(), "no skill reference files found")

    def test_relative_links_in_reference_files_resolve(self) -> None:
        for reference in self._reference_files():
            for target in _relative_link_targets(reference.read_text()):
                with self.subTest(reference=reference.name, target=target):
                    resolved = (reference.parent / target.split("#", 1)[0]).resolve()
                    self.assertTrue(
                        resolved.is_file(), f"Missing {target} from {reference}"
                    )

    def test_reference_links_stay_inside_the_repository(self) -> None:
        """A link escaping the repo resolves only on the machine that wrote it."""
        root = ROOT.resolve()
        for reference in self._reference_files():
            for target in _relative_link_targets(reference.read_text()):
                with self.subTest(reference=reference.name, target=target):
                    resolved = (reference.parent / target.split("#", 1)[0]).resolve()
                    self.assertTrue(
                        resolved.is_relative_to(root),
                        f"{target} in {reference} escapes the repository",
                    )


if __name__ == "__main__":
    unittest.main()
