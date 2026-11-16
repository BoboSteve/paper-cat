import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from seed_import import load_bib, load_csv, load_ris  # noqa: E402


class SeedImportTest(unittest.TestCase):
    def test_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.csv"
            path.write_text("title,authors,year,url\nExample,Ada Lovelace;Alan Turing,2026,https://doi.org/10.1/x\n", encoding="utf-8")
            rows = load_csv(path)
        self.assertEqual(rows[0]["title"], "Example")

    def test_bib(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.bib"
            path.write_text("@article{x,\n title={Example Paper},\n author={Ada Lovelace and Alan Turing},\n year={2026}\n}\n", encoding="utf-8")
            rows = load_bib(path)
        self.assertEqual(rows[0]["title"], "Example Paper")

    def test_ris(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.ris"
            path.write_text("TY  - JOUR\nTI  - Example Paper\nAU  - Ada Lovelace\nPY  - 2026\nER  - \n", encoding="utf-8")
            rows = load_ris(path)
        self.assertEqual(rows[0]["authors"], ["Ada Lovelace"])


if __name__ == "__main__":
    unittest.main()
