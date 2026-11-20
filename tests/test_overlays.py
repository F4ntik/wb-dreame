import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import numpy as np

from overlays import MAX_PER_KIND, ROBOT_POSITION_MAX_AGE_MS, read_overlays
from vendor.types import Area, Line, MapData, MapFrameType, MapImageDimensions, MapPixelType, Path, PathType, Point

NOW = 1_800_000_000


def saved_plan():
    saved = MapData()
    saved.version = 3
    saved.saved_map = True
    saved.map_id = 7
    saved.dimensions = MapImageDimensions(-100, -100, 6, 6, 50)
    saved.charger_position = Point(0, 0)
    saved.timestamp_ms = NOW * 1000 - 30_000
    return saved


def current_frame(saved):
    current = MapData()
    current.version = saved.version
    current.saved_map_id = saved.map_id
    current.saved_map_status = 2
    current.dimensions = deepcopy(saved.dimensions)
    current.charger_position = Point(0, 0, 180)
    current.robot_position = Point(15, 25, 90)
    current.frame_id = 12
    current.frame_type = MapFrameType.I.value
    current.timestamp_ms = NOW * 1000
    return current


class OverlaysTest(unittest.TestCase):
    def test_saved_overlays_are_rotated_and_bounded(self):
        saved = saved_plan()
        valid = Area(0, 0, 40, 0, 40, 20, 0, 20, angle=90)
        hidden = deepcopy(valid)
        hidden.hidden = True
        saved.no_go_areas = [hidden, valid, Area(0, 0, 0, 0, 40, 20, 0, 20)]
        saved.no_mopping_areas = [Area(-100, -100, -70, -100, -70, -50, -100, -50)]
        saved.virtual_walls = [Line(0, 0, 200, 200), Line(0, 0, 201, 100), Line(0, 0, float("nan"), 100)]
        result = read_overlays(saved, None)
        self.assertEqual(result, [
            {"kind": "no_go", "points": [[30, -10], [30, 30], [10, 30], [10, -10]]},
            {"kind": "no_mop", "points": [[-100, -100], [-70, -100], [-70, -50], [-100, -50]]},
            {"kind": "wall", "points": [[0, 0], [200, 200]]},
        ])
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    @patch("overlays.time", return_value=NOW)
    def test_position_requires_verified_map_and_current_frame(self, _):
        saved = saved_plan()
        current = current_frame(saved)
        marker = [{"kind": "robot", "points": [[15, 25]]}]
        self.assertEqual(read_overlays(saved, current), marker)

        current.dimensions = MapImageDimensions(-50, -50, 4, 4, 50)
        self.assertEqual(read_overlays(saved, current), marker)
        current.charger_position = Point(3, -9)
        self.assertEqual(read_overlays(saved, current), marker)
        current.charger_position = Point(40, 40)
        self.assertEqual(read_overlays(saved, current), [])
        current.charger_position = saved.charger_position
        current.dimensions = MapImageDimensions(-50, -75, 4, 4, 50)
        self.assertEqual(read_overlays(saved, current), [])

        for field, value in (
            ("saved_map_id", 8), ("history_map", True), ("temporary_map", True),
            ("clean_log", True), ("restored_map", True), ("saved_map_status", 1),
            ("timestamp_ms", NOW * 1000 - ROBOT_POSITION_MAX_AGE_MS - 1),
            ("timestamp_ms", NOW * 1000 + 60_000),
            ("timestamp_ms", saved.timestamp_ms - 1),
            ("robot_position", Point(201, 25)), ("robot_position", Point(True, 25)),
        ):
            with self.subTest(field=field, value=value):
                invalid = current_frame(saved)
                setattr(invalid, field, value)
                self.assertEqual(read_overlays(saved, invalid), [])

        current = current_frame(saved)
        current.docked = True
        current.robot_position = deepcopy(current.charger_position)
        self.assertEqual(read_overlays(saved, current), [])

    def test_overlay_count_is_capped_and_history_is_not_a_saved_plan(self):
        saved = saved_plan()
        saved.virtual_walls = [Line(0, 0, 1, 1)] * (MAX_PER_KIND + 3)
        self.assertEqual(len(read_overlays(saved, None)), MAX_PER_KIND)
        saved.history_map = True
        self.assertEqual(read_overlays(saved, None), [])

    @patch("overlays.time", return_value=NOW)
    def test_live_track_requires_current_cleaning_and_never_joins_separate_passes(self, _):
        saved = saved_plan()
        frame = current_frame(saved)
        frame.path = [Path(x, 25, kind) for x, kind in [(10, PathType.SWEEP), (20, PathType.LINE),
                                                       (40, PathType.MOP), (45, PathType.LINE)]]
        def paths():
            return [entry["points"] for entry in read_overlays(saved, frame, cleaning=True) if entry["kind"] == "path"]
        self.assertEqual(json.loads(json.dumps(paths())), [[[10, 25], [20, 25]], [[40, 25], [45, 25]]])
        self.assertEqual([e["kind"] for e in read_overlays(saved, frame)], ["robot"])
        frame.docked = True
        self.assertEqual(paths(), [])
        frame.docked = False
        frame.timestamp_ms -= ROBOT_POSITION_MAX_AGE_MS + 1
        self.assertEqual(paths(), [])

    @patch("overlays.time", return_value=NOW)
    def test_live_coverage_uses_marked_cells_in_the_shared_floor(self, _):
        saved = saved_plan()
        saved.pixel_type = np.ones((6, 6), np.uint8)
        saved.pixel_type[2, 2] = MapPixelType.WALL
        frame = current_frame(saved)
        frame.dimensions = MapImageDimensions(-150, -150, 6, 6, 50)
        frame.pixel_type = np.ones((6, 6), np.uint8)
        frame.pixel_type[5, 3] = MapPixelType.UNKNOWN
        frame.cleaning_map_data = overlay = deepcopy(frame)
        overlay.history_map = overlay.cleaning_map = True  # The vendor decoder marks its decmap this way.
        overlay.has_cleaned_area = overlay.has_dirty_area = True
        overlay.pixel_type[:, 3] = MapPixelType.CLEAN_AREA
        overlay.pixel_type[4, 3] = MapPixelType.DIRTY_AREA
        overlay.pixel_type[4, 4] = MapPixelType.CLEAN_AREA

        def coverage(cleaning=True):
            return [item for item in read_overlays(saved, frame, cleaning=cleaning) if item["kind"] == "coverage"]

        self.assertEqual(coverage(), [{
            "kind": "coverage", "source": "frame", "rects": [[-100, 0, 100, 50], [50, 50, 50, 50]],
        }])
        self.assertEqual(coverage(False), [])
        frame.docked = True
        frame.path = [Path(10, 25, "W"), Path(20, 25, "L")]
        self.assertEqual(coverage(), [])
        paused = read_overlays(saved, frame, cleaning=True, station_pause=True)
        self.assertEqual([item["kind"] for item in paused], ["coverage", "path"])
        frame.docked = False
        with patch("overlays.MAX_COVERAGE_SPANS", 1):
            self.assertEqual(coverage(), [])
        overlay.has_cleaned_area = False
        self.assertEqual(coverage(), [])
        overlay.has_cleaned_area = True
        overlay.rotation = 90
        self.assertEqual(coverage(), [])
