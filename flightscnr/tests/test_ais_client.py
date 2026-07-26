"""Tests for aisstream.io AIS client helpers."""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utilities.ais_client import (  # noqa: E402
    AisClient,
    Ship,
    _trim_ais,
    bounding_box,
)


class TestAisHelpers(unittest.TestCase):
    def test_trim_ais(self):
        self.assertEqual(_trim_ais("EXAMPLE NAME   "), "EXAMPLE NAME")
        self.assertEqual(_trim_ais("SHIP@@@@"), "SHIP")
        self.assertEqual(_trim_ais(""), "")

    def test_bounding_box_around_home(self):
        box = bounding_box(37.62, -122.37, 10.0)
        sw, ne = box
        self.assertLess(sw[0], 37.62)
        self.assertGreater(ne[0], 37.62)
        self.assertLess(sw[1], -122.37)
        self.assertGreater(ne[1], -122.37)

    def test_ingest_position_and_static(self):
        client = AisClient()
        pos = {
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": 211234560,
                "ShipName": "EXAMPLE NAME   ",
                "latitude": 38.81,
                "longitude": 0.12,
            },
            "Message": {
                "PositionReport": {
                    "UserID": 211234560,
                    "Latitude": 38.81,
                    "Longitude": 0.12,
                    "Cog": 187.4,
                    "Sog": 6.2,
                    "TrueHeading": 186,
                    "NavigationalStatus": 0,
                }
            },
        }
        client._ingest(json.dumps(pos))
        ships = client.snapshot()
        self.assertEqual(len(ships), 1)
        self.assertEqual(ships[0].mmsi, 211234560)
        self.assertEqual(ships[0].name, "EXAMPLE NAME")
        self.assertAlmostEqual(ships[0].lat, 38.81)
        self.assertAlmostEqual(ships[0].sog_kt, 6.2)

        static = {
            "MessageType": "ShipStaticData",
            "MetaData": {"MMSI": 211234560, "latitude": 38.81, "longitude": 0.12},
            "Message": {
                "ShipStaticData": {
                    "Name": "EXAMPLE NAME",
                    "Type": 70,
                    "Destination": "DENIA@@@@",
                    "Dimension": {"A": 100, "B": 20, "C": 8, "D": 8},
                }
            },
        }
        client._ingest(json.dumps(static))
        ships = client.snapshot()
        self.assertEqual(ships[0].ship_type, 70)
        self.assertEqual(ships[0].dest, "DENIA")
        self.assertEqual(ships[0].length_m, 120)
        self.assertEqual(ships[0].beam_m, 16)

    def test_ship_to_dict_nan(self):
        d = Ship(mmsi=1, lat=1.0, lon=2.0).to_dict()
        self.assertIsNone(d["sog_kt"])
        self.assertEqual(d["data_source"], "aisstream")
        self.assertTrue(math.isnan(float("nan")))

    def test_vessel_to_radar_entry(self):
        from utilities.ais_client import vessel_to_radar_entry

        entry = vessel_to_radar_entry(
            {
                "mmsi": 366123456,
                "name": "TEST SHIP",
                "destination": "SFO",
                "lat": 37.8,
                "lon": -122.4,
                "sog_kt": 12.5,
                "cog_deg": 90.0,
                "heading_deg": 91.0,
                "nav_status": 0,
                "ship_type": 70,
                "length_m": 180,
                "beam_m": 28,
            }
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["kind"], "vessel")
        self.assertEqual(entry["mmsi"], 366123456)
        self.assertEqual(entry["flag_iso2"], "us")
        self.assertEqual(entry["plane"], "Cargo")
        self.assertEqual(entry["plane_latitude"], 37.8)
        self.assertEqual(entry["heading"], 91)


if __name__ == "__main__":
    unittest.main()
