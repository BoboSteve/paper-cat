import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from startlux_runtime import validate_service
from jev_rank import reusable, sha256


class RuntimeTest(unittest.TestCase):
    def test_llama_rejects_causal_reading(self):
        value = {"status": "ok", "kind": "instant", "model": "StartLux-Test",
                 "prompt_attention": "bidirectional", "engine": {"name": "llamacpp", "bidir_mode": "server"}}
        with patch("startlux_runtime.settings", return_value={"backend": "llamacpp"}):
            self.assertEqual(validate_service(value), value)
            value["engine"]["bidir_mode"] = "off"
            with self.assertRaises(ValueError):
                validate_service(value)

    def test_rerank_when_evidence_prompt_or_service_changes(self):
        row = {"title": "Test", "abstract": "Evidence"}
        prior = {"status": "ok", "input_sha256": sha256(json.dumps(row, sort_keys=True)),
                 "prompt_sha256": "p1", "service_sha256": "s1"}
        self.assertTrue(reusable(prior, row, "p1", "s1"))
        self.assertFalse(reusable(prior, row, "p2", "s1"))
        self.assertFalse(reusable(prior, row, "p1", "s2"))
        self.assertFalse(reusable(prior, {**row, "abstract": "Changed"}, "p1", "s1"))


if __name__ == "__main__":
    unittest.main()
