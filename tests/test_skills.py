from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "skills"


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

            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", body):
                if "://" in target:
                    continue
                resolved = skill_file.parent / target.split("#", 1)[0]
                self.assertTrue(resolved.is_file(), f"Missing {target} from {skill_file}")


if __name__ == "__main__":
    unittest.main()
