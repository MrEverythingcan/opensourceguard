import sys
import tempfile
import unittest
from pathlib import Path

from opensourceguard.verification import VerificationEngine


class VerificationTests(unittest.TestCase):
    def test_isolated_candidate_passes_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "test.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual(1,1)\n")
            before = (repo / "test.py").read_text()
            result = VerificationEngine(timeout_seconds=10).verify(repo, [[sys.executable, "-m", "unittest", "test.py"]])
            self.assertEqual(result["verification_status"], "verified")
            self.assertEqual((repo / "test.py").read_text(), before)


if __name__ == "__main__":
    unittest.main()
