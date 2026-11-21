import base64
import json
import struct
import unittest
import zlib
from copy import deepcopy
from unittest.mock import patch
from xml.etree import ElementTree

import numpy as np

from history_map import history_position, iter_route_sections, render_history
from vendor.types import MapData, MapFrameType, MapImageDimensions, MapPixelType, ObstacleReason, Path, PathType, Point, Segment


def frame(pixels, *, left=-30, top=-20, step=10, rotation=0):
    data = MapData()
    data.map_id = 3
    data.history_map = True
    data.version = 3
    data.rotation = rotation
    data.pixel_type = np.array(pixels, dtype=np.uint8)
    width, height = data.pixel_type.shape
    data.dimensions = MapImageDimensions(top, left, height, width, step)
    return data


def paths(svg):
    return [el.attrib for el in ElementTree.fromstring(svg).findall(".//{*}path")]


def encoded_frame(map_id, pixels, metadata):
    width, height = pixels.shape
    header = struct.pack(
        "<hhB11h", map_id, 1, MapFrameType.I.value,
        105, 105, 0, 105, 105, 0, 10, width, height, 100, 100,
    )
    return base64.b64encode(zlib.compress(header + pixels.T.tobytes() + json.dumps(metadata).encode())).decode()


class HistoryMapTest(unittest.TestCase):
    def test_archive_uses_its_own_room_cells_and_only_explicit_non_conflicting_outcomes(self):
        archive = frame([[1, 102], [2, 3], [4, 5], [6, 7]], rotation=90)
        archive.completed_segments = [1, 4, 6, 9, False]
        archive.skipped_segments = [2, 4]
        archive.pending_segments = [6]
        archive.cleaned_segments = [5]
        archive.blocked_segments = {3: [ObstacleReason.BLOCKED_BY_DOOR]}
        archive.segments = {1: Segment(1, 0, 0, 0, 0, custom_name="Кухня & <холл>")}
        archive.path = [Path(-25, -15, "S"), Path(-15, -15, "L")]
        result = render_history(archive)
        self.assertEqual(result["room_statuses"], [
            {"id": 1, "name": "Кухня & <холл>", "status": "completed"},
            {"id": 2, "name": "Комната 2", "status": "skipped"},
            {"id": 3, "name": "Комната 3", "status": "blocked"},
            *[{"id": room, "name": f"Комната {room}", "status": "unknown"} for room in (4, 5, 6, 7)],
        ])
        svg = ElementTree.fromstring(result["svg"])
        self.assertNotIn("Кухня", result["svg"])
        layer = svg.find("{*}g[@id='room-statuses']")
        self.assertEqual({p.get("fill"): p.get("d") for p in layer}, {
            "#1E9A78": "M1 3h1v1h-1z", "#DEA044": "M1 2h1v1h-1zM0 3h1v1h-1z",
            "#DE8079": "M0 2h1v1h-1z",
        })
        self.assertTrue(result["has_path"])
        archive.completed_segments = archive.skipped_segments = archive.blocked_segments = None
        unavailable = render_history(archive)
        self.assertEqual(unavailable["room_statuses"], [])
        self.assertNotIn('id="room-statuses"', unavailable["svg"])

    def test_archive_grid_rotation_charger_and_image_percent_share_projection(self):
        archive = frame([[1, 0], [1, 255], [0, 2]], rotation=90)
        archive.charger_position = Point(-25.0, -15.0)
        result = render_history(archive)
        self.assertFalse(result["has_path"])
        self.assertFalse(result["has_coverage"])
        svg = ElementTree.fromstring(result["svg"])
        self.assertEqual(svg.get("viewBox"), "-3 -3 8 9")
        station = svg.find("{*}circle")
        self.assertEqual((station.get("cx"), station.get("cy")), ("1.400", "2.500"))
        self.assertEqual(history_position(archive, -25.0, -15.0), [55.0, 61.11111])
        self.assertIsNone(history_position(archive, -31, -15))
        self.assertIsNone(history_position(archive, float("nan"), -15))
        self.assertEqual(result["route_sections"], [])
        archive.path = [Path(-25.0, -15.0, "W"), Path(-15.0, -5.0, "L")]
        self.assertEqual(render_history(archive)["route_sections"], [{
            "mode": "W",
            "points": [
                [55.0, 61.11111],
                history_position(archive, -15.0, -5.0),
            ],
        }])

        other = frame([[4, 4, 0], [4, 255, 0]], left=5, top=10, step=50, rotation=0)
        other_svg = render_history(other)["svg"]
        self.assertEqual(ElementTree.fromstring(other_svg).get("viewBox"), "-3 -3 8 9")
        self.assertNotEqual(paths(result["svg"]), paths(other_svg))
        self.assertIsNone(history_position(other, -25.0, -15.0))

    def test_native_route_modes_and_breaks_do_not_invent_connections(self):
        archive = frame(np.ones((8, 10), np.uint8), left=0, top=0)
        archive.path = [
            Path(5, 5, PathType.SWEEP), Path(15, 5, PathType.LINE),
            Path(45, 25, PathType.SWEEP_AND_MOP), Path(45, 35, PathType.LINE),
            Path(105, 35, PathType.LINE), Path(55, 45, PathType.LINE), Path(65, 45, PathType.LINE),
            Path(25, 55, PathType.MOP), Path(35, 55, PathType.LINE),
            Path(75, 75, "M"), Path(75, 85, "L"),
            Path(15, 75, "S"), Path(25, 85, "L"),
        ]
        result = render_history(archive)
        traces = [p["d"] for p in paths(result["svg"]) if "stroke" in p]
        self.assertTrue(result["has_path"])
        self.assertEqual(traces, [
            "M0.500 9.400L1.500 9.400M1.500 2.400L2.500 1.400",
            "M4.500 7.400L4.500 6.400",
            "M5.500 5.400L6.500 5.400",
            "M2.500 4.400L3.500 4.400M7.500 2.400L7.500 1.400",
        ])
        sections = list(iter_route_sections(archive.path, project=lambda x, y: (x, y) if x < 80 else None))
        self.assertEqual([mode for mode, _ in sections], [
            PathType.SWEEP, PathType.SWEEP_AND_MOP, PathType.LINE, PathType.MOP, PathType.MOP, PathType.SWEEP,
        ])
        self.assertEqual(sections[2][1], [(55, 45), (65, 45)])
        self.assertEqual(result["route_sections"], [
            {"mode": "S", "points": [[25.0, 77.5], [32.14286, 77.5]]},
            {"mode": "W", "points": [[53.57143, 65.0], [53.57143, 58.75]]},
            {"mode": "L", "points": [[60.71429, 52.5], [67.85714, 52.5]]},
            {"mode": "M", "points": [[39.28571, 46.25], [46.42857, 46.25]]},
            {"mode": "M", "points": [[75.0, 33.75], [75.0, 27.5]]},
            {"mode": "S", "points": [[32.14286, 33.75], [39.28571, 27.5]]},
        ])
        with patch("history_map.MAX_PATH_POINTS", 2):
            limited = render_history(archive)
            self.assertFalse(limited["has_path"])
            self.assertEqual(limited["route_sections"], [])

    def test_coverage_uses_only_explicit_cells_on_compatible_archived_floor(self):
        archive = frame([[1, 1], [255, 1], [MapPixelType.UNKNOWN, 2]])
        archive.cleaned_area = 1200
        self.assertFalse(render_history(archive)["has_coverage"])
        overlay = deepcopy(archive)
        overlay.cleaning_map = True
        overlay.map_id = 21  # The embedded decmap can have its own header ID.
        overlay.has_cleaned_area = overlay.has_dirty_area = True
        overlay.pixel_type[0, 0] = MapPixelType.CLEAN_AREA
        overlay.pixel_type[1, 0] = MapPixelType.CLEAN_AREA  # On a wall: omit.
        overlay.pixel_type[2, 0] = MapPixelType.CLEAN_AREA  # Unknown floor: omit.
        overlay.pixel_type[2, 1] = MapPixelType.DIRTY_AREA
        archive.cleaning_map_data = overlay
        result = render_history(archive)
        self.assertTrue(result["has_coverage"])
        self.assertEqual(result["coverage_source"], "frame")
        marks = [p["d"] for p in paths(result["svg"]) if p.get("fill") in ("#20A58E", "#F0A347")]
        self.assertCountEqual(marks, ["M0 1h1v1h-1z", "M2 0h1v1h-1z"])
        overlay.dimensions.left += 10
        self.assertFalse(render_history(archive)["has_coverage"])

    def test_mop_track_estimate_is_separate_and_clipped_to_floor(self):
        archive = frame([[1, 1], [255, 1], [0, 2]], left=0, top=0)
        archive.path = [
            Path(5, 5, "S"), Path(8, 8, "L"),
            Path(5, 15, "W"), Path(15, 16, "L"),
            Path(18, 18, "M"), Path(25, 18, "L"),
        ]
        render = render_history(archive)
        self.assertFalse(render["has_coverage"])
        self.assertEqual(render["coverage_source"], "route")
        group = ElementTree.fromstring(render["svg"]).find(".//{*}g[@id='cleaning-coverage']")
        clip = group.find(".//{*}clipPath[@id='cleaning-floor']/{*}path")
        self.assertEqual(clip.get("d"), "M0 0h3v1h-3zM0 1h1v1h-1z")
        band = group.find("{*}g[@clip-path='url(#cleaning-floor)']")
        self.assertEqual(band.get("stroke-width"), "25.0000")
        self.assertEqual([el.get("d") for el in band], [
            "M0.500 0.400L1.500 0.300", "M1.800 0.100L2.500 0.100",
        ])
        self.assertEqual(len(render["route_sections"]), 3)

        without_mop = deepcopy(archive)
        without_mop.path = without_mop.path[:2]
        self.assertIsNone(render_history(without_mop)["coverage_source"])
        with patch("history_map.MAX_SVG_BYTES", len(render["svg"].encode()) - 1):
            limited = render_history(archive)
        self.assertIsNone(limited["coverage_source"])
        self.assertTrue(limited["has_path"])

        archive.pixel_type[2, 1] = MapPixelType.DIRTY_AREA
        archive.has_dirty_area = True
        explicit = render_history(archive)
        self.assertTrue(explicit["has_coverage"])
        self.assertEqual(explicit["coverage_source"], "frame")
        group = ElementTree.fromstring(explicit["svg"]).find(".//{*}g[@id='cleaning-coverage']")
        self.assertEqual([el.get("fill") for el in group], ["#F0A347"])

    def test_archived_base64_codec_and_native_tokens(self):
        from vendor.map import DreameVacuumMapDecoder

        cleaned = np.zeros((4, 3), dtype=np.uint8)
        cleaned[1, 1] = 1
        pixels = np.ones((4, 3), dtype=np.uint8)
        packed = encoded_frame(7, pixels, {
            "iscleanlog": True, "mra": 180, "timestamp_ms": 1_800_000_000_000,
            "tr": "W105,105L10,0S125,115L0,10", "decmap": encoded_frame(8, cleaned, {}),
            "combinecleanedarea": [[1], [True], [1.5]],
        })
        archive, _ = DreameVacuumMapDecoder.decode_map(packed, 3)
        self.assertIsNotNone(archive)
        archive.history_map = True  # Set by DreameMapVacuumMapManager.get_history_map.
        result = render_history(archive)
        self.assertTrue(result["has_path"])
        self.assertTrue(result["has_coverage"])
        self.assertEqual(archive.completed_segments, [1])
        self.assertEqual([p.path_type for p in archive.path], [
            PathType.SWEEP_AND_MOP, PathType.LINE, PathType.SWEEP, PathType.LINE,
        ])

    def test_limits_and_only_fixed_safe_svg_markup(self):
        archive = frame([[1, 0, 1], [2, 0, 2], [0, 1, 1]])
        archive.map_name = "</title><script>alert(1)</script>"
        archive.path = [Path(0, 0, "M<script>"), Path(0, 0, "L")]
        safe = render_history(archive)
        self.assertFalse(safe["has_path"])
        self.assertNotIn("alert", safe["svg"])
        self.assertNotIn("href", safe["svg"])
        self.assertLessEqual({el.tag.split("}")[-1] for el in ElementTree.fromstring(safe["svg"]).iter()},
                             {"svg", "title", "rect", "g", "path", "circle"})
        with patch("history_map.MAX_FLOOR_SPANS", 2):
            self.assertIsNone(render_history(archive))
        with patch("history_map.MAX_PATH_POINTS", 1):
            self.assertEqual(list(iter_route_sections([Path(1, 1, "S"), Path(2, 2, "L")])), [])
        archive.history_map = False
        self.assertIsNone(render_history(archive))
        archive.history_map = True
        archive.dimensions.width = 1025
        self.assertIsNone(render_history(archive))
