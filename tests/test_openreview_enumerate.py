import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from openreview_enumerate import classify_v1_note  # noqa: E402


def note(content, decision=None):
    replies = []
    if decision:
        replies.append(
            {
                "invitation": "Venue/Paper1/-/Decision",
                "content": {"decision": decision},
                "cdate": 1,
            }
        )
    return SimpleNamespace(content=content, details={"directReplies": replies})


class V1ClassificationTest(unittest.TestCase):
    def test_accept_decision_supplies_presentation(self):
        outcome, presentation, decision = classify_v1_note(
            note({"title": "Example"}, "Accept (Poster)"),
            "Venue/Conference",
        )
        self.assertEqual((outcome, presentation, decision), ("accepted", "poster", "Accept (Poster)"))

    def test_reject_is_not_accepted(self):
        outcome, presentation, decision = classify_v1_note(
            note({"title": "Example"}, "Reject"),
            "Venue/Conference",
        )
        self.assertEqual((outcome, presentation, decision), ("rejected", None, "Reject"))

    def test_matching_venueid_is_accepted_without_decision(self):
        outcome, presentation, decision = classify_v1_note(
            note({"title": "Example", "venue": "Venue Oral", "venueid": "Venue/Conference"}),
            "Venue/Conference",
        )
        self.assertEqual((outcome, presentation, decision), ("accepted", "oral", None))

    def test_workshop_venue_is_excluded(self):
        outcome, presentation, decision = classify_v1_note(
            note({"title": "Example", "venueid": "Venue/Workshop"}),
            "Venue/Conference",
        )
        self.assertEqual((outcome, presentation, decision), ("non_main", None, None))


if __name__ == "__main__":
    unittest.main()
