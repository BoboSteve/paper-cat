import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from jev_rank import prompt_for  # noqa: E402


class JevPromptTest(unittest.TestCase):
    def test_profile_is_included(self):
        prompt = prompt_for("medical trust", "Prefer method donors and failure warnings.")
        self.assertIn("medical trust", prompt)
        self.assertIn("Prefer method donors and failure warnings.", prompt)


if __name__ == "__main__":
    unittest.main()
