import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from official_enumerate import parse_acl, parse_pmlr  # noqa: E402


ACL = """[pdf](https://aclanthology.org/2026.acl-long.1.pdf) [bib](/2026.acl-long.1.bib)

**[Example Medical Paper](/2026.acl-long.1/)**
[Ada Lovelace](/people/ada/) | [Alan Turing](/people/alan/)

We study medical factuality.

[pdf](https://aclanthology.org/2026.acl-long.2.pdf) [bib](/2026.acl-long.2.bib)
"""


PMLR = """Example Paper
Ada Lovelace, Alan Turing; *Proceedings of the 43rd International Conference on Machine Learning*, PMLR 306:1-21

\\[[abs](https://proceedings.mlr.press/v306/example.html)\\]\\[[Download PDF](https://example.org/paper.pdf)\\]
"""


class OfficialParserTest(unittest.TestCase):
    def test_acl_parser(self):
        rows = parse_acl(ACL, "acl_long", "ACL 2026", 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["acl_id"], "2026.acl-long.1")
        self.assertEqual(rows[0]["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(rows[0]["abstract"], "We study medical factuality.")

    def test_pmlr_parser(self):
        rows = parse_pmlr(PMLR, "icml", "ICML 2026", 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Example Paper")
        self.assertEqual(rows[0]["pages"], "1-21")


if __name__ == "__main__":
    unittest.main()
