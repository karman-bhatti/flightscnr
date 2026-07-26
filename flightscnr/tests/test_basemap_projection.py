"""Aircraft screen positions must match the tile basemap at high zoom."""

from __future__ import annotations

import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBasemapProjection(unittest.TestCase):
    def setUp(self):
        import display.round_touch.settings as settings
        import display.round_touch.theme as theme
        from display.round_touch import scale

        self.settings = settings
        settings._facing_preview = None
        settings._state = dict(settings._defaults)
        settings._state["facing_deg"] = 0.0
        theme.set_framebuffer_side(720)
        scale.select(0)  # 2 mi — where flat-earth vs Mercator divergence is obvious

    def tearDown(self):
        self.settings.set_facing_preview(None)

    def test_geo_matches_basemap_when_map_enabled(self):
        from display.round_touch import geo, map_bg

        home = [37.62, -122.37]
        # ~500 m east of home
        cos_lat = math.cos(math.radians(home[0]))
        lon = home[1] + 0.5 / (111.320 * cos_lat)
        lat = home[0]

        with patch("display.round_touch.geo.LOCATION_HOME", home), patch.object(
            map_bg, "_enabled", return_value=True
        ):
            flat_would = geo.enu_to_screen(*geo.local_offset_km(lat, lon)[:2])
            screen = geo.lat_lon_to_screen(lat, lon)
            merc = map_bg.lat_lon_to_basemap_screen(
                lat, lon, center_lat=home[0], center_lon=home[1]
            )

        self.assertEqual(screen, merc)
        # Regression: flat-earth was ~11 px off at 500 m on the 2 mi scale.
        self.assertNotEqual(screen, flat_would)

    def test_basemap_roundtrip(self):
        from display.round_touch import geo, map_bg

        home = [37.62, -122.37]
        target = (37.625, -122.365)

        with patch("display.round_touch.geo.LOCATION_HOME", home), patch.object(
            map_bg, "_enabled", return_value=True
        ):
            x, y = geo.lat_lon_to_screen(*target)
            lat, lon = geo.screen_to_lat_lon(x, y)

        self.assertAlmostEqual(lat, target[0], places=4)
        self.assertAlmostEqual(lon, target[1], places=4)


if __name__ == "__main__":
    unittest.main()
