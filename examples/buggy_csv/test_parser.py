import unittest

from parser import parse_csv


class ParserTests(unittest.TestCase):
    def test_empty_csv_returns_empty_list(self):
        self.assertEqual(parse_csv(""), [])

    def test_one_row_keeps_legacy_shape(self):
        self.assertEqual(parse_csv("a,b"), ["a", "b"])

    def test_multiple_rows_are_preserved(self):
        self.assertEqual(parse_csv("a,b\nc,d"), [["a", "b"], ["c", "d"]])


if __name__ == "__main__":
    unittest.main()
