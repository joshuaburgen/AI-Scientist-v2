"""Exercise launcher PDF selection without loading the GPU research stack."""
import ast
import os
from pathlib import Path
import re
import tempfile
import unittest


class ReviewPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "launch_scientist_bfts.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "find_pdf_path_for_review")
        namespace = {"os": os, "osp": os.path, "re": re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        cls.select_pdf = staticmethod(namespace[function.name])

    def test_no_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self.select_pdf(tmp))

    def test_plain_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.touch()
            self.assertEqual(self.select_pdf(tmp), str(pdf))

    def test_latest_reflection_then_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["paper.pdf", "reflection_2.pdf", "reflection_10.pdf"]:
                (Path(tmp) / name).touch()
            self.assertEqual(Path(self.select_pdf(tmp)).name, "reflection_10.pdf")
            (Path(tmp) / "reflection_final.pdf").touch()
            self.assertEqual(Path(self.select_pdf(tmp)).name, "reflection_final.pdf")
