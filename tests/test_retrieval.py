import unittest

from opensourceguard.retrieval import CodeDocument, HybridRetriever


class RetrievalTests(unittest.TestCase):
    def test_retrieves_symbol_and_path(self):
        r = HybridRetriever([
            CodeDocument("parser.py", "def parse_csv(text):\n    return []", symbol="parse_csv", language="python"),
            CodeDocument("docs/readme.md", "CSV usage", symbol="<module>", language="markdown"),
        ])
        result = r.search("empty csv parse_csv")
        self.assertEqual(result[0]["path"], "parser.py")
        self.assertEqual(result[0]["confidence"], "retrieved")


if __name__ == "__main__":
    unittest.main()
