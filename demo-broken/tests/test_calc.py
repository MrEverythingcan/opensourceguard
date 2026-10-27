import unittest
from calc import divide

class T(unittest.TestCase):
    def test_div(self):
        self.assertEqual(divide(4, 2), 2)
