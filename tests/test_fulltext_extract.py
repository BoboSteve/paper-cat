import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from common import locate_quote, split_pages  # noqa: E402
from fulltext_verify import extract_pages, write_pages  # noqa: E402

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

LEFT = ("Our skill library stores typed contracts. The contract alone falls below the prose baseline, "
        "which shows that templates matter for invocation.")
RIGHT = "Right column text about unrelated experiments on ALFWorld with three seeds and paired tests."


@unittest.skipIf(pymupdf is None, "PyMuPDF not installed")
class FullTextExtractTest(unittest.TestCase):
    def make_pdf(self, path):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 290, 400), LEFT, fontsize=10)
        page.insert_textbox(pymupdf.Rect(310, 50, 550, 400), RIGHT, fontsize=10)
        document.new_page().insert_text((50, 72), "Second page conclusion sentence for testing.", fontsize=10)
        document.save(str(path))

    def test_two_column_page_keeps_sentences_and_page_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            self.make_pdf(pdf)
            pages, extractor = extract_pages(pdf, timeout=30)
            text = write_pages(pages, Path(directory) / "paper.txt")
            reread = split_pages((Path(directory) / "paper.txt").read_text(encoding="utf-8"))
        self.assertEqual(extractor, "pymupdf")
        self.assertEqual(len(reread), 2)
        self.assertIn("===== PAGE 2 =====", text)
        hit = locate_quote("The contract alone falls below the prose baseline, which shows that templates matter", reread)
        self.assertEqual((hit["found"], hit["page"]), (True, 1))
        self.assertEqual(locate_quote("Second page conclusion sentence for testing.", reread)["page"], 2)


if __name__ == "__main__":
    unittest.main()
