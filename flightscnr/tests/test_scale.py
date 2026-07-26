import unittest

from display.round_touch import scale


class TestScaleSnapping(unittest.TestCase):
    def test_index_for_value_mi(self):
        self.assertEqual(scale.index_for_value(30, "mi"), 6)
        self.assertEqual(scale.index_for_value(4, "mi"), 1)
        self.assertEqual(scale.index_for_value(7, "mi"), 3)

    def test_format_display_value_mi(self):
        self.assertEqual(scale.format_display_value(1, "mi"), "3")

    def test_index_for_value_km(self):
        self.assertEqual(scale.index_for_value(48, "km"), 6)


if __name__ == "__main__":
    unittest.main()
