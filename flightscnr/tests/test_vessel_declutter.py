"""Tests for AIS vessel radar declutter helpers."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from display.round_touch import vessel_declutter as vd  # noqa: E402


class TestVesselDeclutter(unittest.TestCase):
    def test_display_name_skips_mmsi(self):
        self.assertEqual(vd.display_name({"name": "REDWOOD CITY"}), "REDWOOD CITY")
        self.assertEqual(vd.display_name({"callsign": "MMSI 366999999"}), "")
        self.assertEqual(vd.display_name({"name": "MMSI 1", "callsign": "SONOMA"}), "SONOMA")

    def test_truncate(self):
        self.assertEqual(vd.truncate_name("ABC", 14), "ABC")
        self.assertTrue(vd.truncate_name("HANDY INCLUSIVITY", 14).endswith("…"))
        self.assertLessEqual(len(vd.truncate_name("HANDY INCLUSIVITY", 14)), 14)

    def test_parked_detection(self):
        self.assertTrue(vd.is_parked({"kind": "vessel", "stationary": True}))
        self.assertTrue(vd.is_parked({"kind": "vessel", "nav_status_name": "At Anchor"}))
        self.assertTrue(vd.is_parked({"kind": "vessel", "sog_kt": 0.1}))
        self.assertFalse(vd.is_parked({"kind": "vessel", "sog_kt": 8.0}))
        self.assertFalse(vd.is_parked({"kind": "aircraft", "sog_kt": 0}))

    def test_hide_parked(self):
        parked = {"kind": "vessel", "stationary": True, "name": "X"}
        moving = {"kind": "vessel", "sog_kt": 10, "name": "Y"}
        with mock.patch.object(vd, "hide_parked_enabled", return_value=True):
            self.assertFalse(vd.should_show_on_radar(parked))
            self.assertTrue(vd.should_show_on_radar(moving))
        with mock.patch.object(vd, "hide_parked_enabled", return_value=False):
            self.assertTrue(vd.should_show_on_radar(parked))

    def test_density_modes(self):
        parked = {"kind": "vessel", "stationary": True, "name": "PARK"}
        moving = {"kind": "vessel", "sog_kt": 12, "name": "GO"}
        with mock.patch.object(vd, "density_mode", return_value="icons_only"):
            self.assertFalse(vd.should_label(parked))
            self.assertFalse(vd.should_label(moving))
        with mock.patch.object(vd, "density_mode", return_value="moving_only"):
            self.assertFalse(vd.should_label(parked))
            self.assertTrue(vd.should_label(moving))
        with mock.patch.object(vd, "density_mode", return_value="all_labels"):
            self.assertTrue(vd.should_label(parked))
            self.assertTrue(vd.should_label(moving))


if __name__ == "__main__":
    unittest.main()
