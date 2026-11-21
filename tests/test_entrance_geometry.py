from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from config import Refused, read
from entrance import Entrance
from panel import interactive_map
from vendor.types import Area, Line, MapData, MapImageDimensions, Point, Segment


class EntranceGeometryTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "entrance.json"
        self.store = Entrance(self.path)
        self.zones = [[185, 235, 462, 589], [810, 170, 1190, 485]]
        self.robot = "dreame-1"
        self.map = MapData()
        self.map.map_id, self.map.version, self.map.saved_map = 7, 3, True
        self.map.dimensions = MapImageDimensions(0, 0, 40, 60, 50)
        self.map.charger_position = Point(1200, 610, 90)
        self.map.segments = {1: Segment(1, 0, 0, 2999, 1999, custom_name="Прихожая")}
        self.map.pixel_type = np.zeros((60, 40), dtype=np.uint8)
        self.map.pixel_type[1:-1, 1:-1] = 1

    def save(self, source=None):
        plan = interactive_map(self.map if source is None else source)
        expected = self.store.view(self.robot, plan)["version"]
        saved = self.store.save(self.robot, plan, plan, {
            "version": expected, "map_id": plan["id"], "revision": plan["revision"], "zones": self.zones,
        })
        self.assertEqual(saved["status"], "ready")
        return plan, saved

    def test_room_rename_split_and_small_remote_edit_keep_the_saved_coordinates(self):
        plan, saved = self.save()
        reference = self.path.read_bytes()
        updated = deepcopy(self.map)
        updated.segments[1].custom_name = "Коридор"
        updated.segments[2] = Segment(2, 0, 0, 550, 1000, custom_name="Тамбур")
        updated.pixel_type[1:11, 1:20] = 2
        updated.pixel_type[58, 1:16] = 0
        changed = interactive_map(updated, plan)
        self.assertNotEqual(changed["revision"], plan["revision"])
        self.assertEqual(changed["geometry_key"], plan["geometry_key"])
        self.assertEqual(Entrance(self.path).view(self.robot, changed), saved)
        self.assertEqual(self.path.read_bytes(), reference)

        updated.pixel_type[57, 1:16] = 0
        self.assertEqual(self.store.view(self.robot, interactive_map(updated, changed)), {
            "version": saved["version"], "status": "stale",
        })

    def test_rejects_local_outline_change_and_floor_move_with_equal_area(self):
        self.map.pixel_type[18, 6] = 255
        plan, saved = self.save()
        changed = deepcopy(self.map)
        changed.pixel_type[2, 6] = 0  # Adjacent to the first selection.
        self.assertEqual(self.store.view(self.robot, interactive_map(changed, plan))["status"], "stale")
        changed = deepcopy(self.map)
        changed.pixel_type[18, 6], changed.pixel_type[19, 6] = 1, 255
        altered_plan = interactive_map(changed, plan)
        self.assertEqual(altered_plan["footprint"], plan["footprint"])
        self.assertEqual(altered_plan["room_area_m2"], plan["room_area_m2"])
        self.assertEqual(self.store.view(self.robot, altered_plan), {"version": saved["version"], "status": "stale"})

    def test_rejects_major_floor_changes_even_inside_an_unchanged_outline(self):
        plan, saved = self.save()
        changed = deepcopy(self.map)
        changed.pixel_type[57, 1:16] = 255
        harmless = interactive_map(changed, plan)
        self.assertEqual(self.store.view(self.robot, harmless), saved)
        changed.pixel_type[58, 1:16] = 255
        altered = interactive_map(changed, harmless)
        self.assertEqual(altered["footprint"], plan["footprint"])
        self.assertEqual(self.store.view(self.robot, altered)["status"], "stale")

    def test_rejects_new_binding_map_axis_boundary_dock_and_restrictions(self):
        plan, saved = self.save()
        self.assertEqual(self.store.view("other-dreame", plan)["status"], "stale")
        no_mop = Area(1800, 1500, 1900, 1500, 1900, 1600, 1800, 1600, 0)
        for changed in (
            {"map_id": 8}, {"rotation": 90}, {"charger_position": Point(1200, 610, 180)},
            {"charger_position": Point(1250, 610, 90)}, {"charger_position": None},
            {"dimensions": MapImageDimensions(0, 50, 40, 60, 50)},
            {"dimensions": MapImageDimensions(0, 0, 40, 60, 55)},
            {"no_go_areas": [no_mop]}, {"no_mopping_areas": [no_mop]},
            {"virtual_walls": [Line(1800, 1500, 1900, 1600)]}, {"dirty": True}, {"restored_map": True},
        ):
            with self.subTest(changed=changed):
                draft = deepcopy(self.map)
                for field, value in changed.items():
                    setattr(draft, field, value)
                self.assertEqual(self.store.view(self.robot, interactive_map(draft, plan)), {
                    "version": saved["version"], "status": "stale",
                })

    def test_legacy_geometry_requires_the_exact_revision_and_unknown_map_cannot_be_saved(self):
        plan = interactive_map(self.map)
        legacy = {k: v for k, v in plan.items() if k != "geometry_key"}
        data = {"version": None, "map_id": legacy["id"], "revision": legacy["revision"], "zones": self.zones}
        previous = self.store.save(self.robot, legacy, legacy, data)
        self.assertNotIn("geometry", read(self.path))
        self.assertEqual(self.store.view(self.robot, plan), previous)
        self.assertEqual(self.store.view(self.robot, {**plan, "revision": "new"})["status"], "stale")

        self.map.charger_position = None
        unknown = interactive_map(self.map, plan)
        with self.assertRaisesRegex(Refused, "выберите участок у входа заново"):
            self.store.save(self.robot, unknown, unknown, {
                **data, "version": previous["version"], "revision": unknown["revision"],
            })
        self.assertEqual(self.store.view(self.robot, plan), previous)


if __name__ == "__main__":
    unittest.main()
