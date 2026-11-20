import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from bridge import Bridge
from config import Refused
from panel import interactive_map
from room_names import RoomNames
from test_panel import sample_map
from vendor.types import MapImageDimensions, Segment


class RoomNamesTest(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.directory = Path(state.name)
        self.names = RoomNames(self.directory, "device-a")
        self.saved = sample_map()
        self.saved.segments[2] = Segment(2, 0, 0, 4, 4, custom_name="Коридор")
        self.plan = interactive_map(self.saved)

    def test_rename_and_reset_are_local_persistent_and_detect_stale_editors(self):
        bridge = Bridge()
        bridge.room_names = self.names
        bridge.latest.update(online=True, map=self.plan)
        state = bridge.web_state()
        first = state["room_names"]["1"]
        request = {"key": first["key"], "expected": None, "name": " Кухня Павла "}
        self.assertEqual(bridge.web_room_name(request), {"status": "saved"})
        self.assertEqual(self.names.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(RoomNames(self.directory, "device-a").for_map(self.plan)["1"]["alias"], "Кухня Павла")
        self.assertIsNone(RoomNames(self.directory, "device-b").for_map(self.plan)["1"]["alias"])
        new = bridge.web_state()
        self.assertEqual(state["map"], new["map"])
        self.assertEqual(new["room_names"]["1"]["alias"], "Кухня Павла")
        with self.assertRaisesRegex(Refused, "Название уже изменилось"):
            bridge.web_room_name({**request, "name": "Из другого браузера"})
        bridge.web_room_name({**request, "expected": "Кухня Павла", "name": None})
        self.assertIsNone(bridge.web_state()["room_names"]["1"]["alias"])

    def test_another_room_change_and_map_padding_keep_unchanged_room_name(self):
        initial = self.names.for_map(self.plan)
        self.names.save(self.plan, {"key": initial["1"]["key"], "expected": None, "name": "Кухня"})
        self.saved.segments[2].custom_name = "Холл"
        self.saved.pixel_type[3, 1] = 0
        self.saved.pixel_type = np.pad(self.saved.pixel_type, ((1, 0), (0, 0)))
        self.saved.dimensions = MapImageDimensions(-20, -40, 3, 6, 10)
        changed = interactive_map(self.saved, self.plan)
        self.assertNotEqual(self.plan["revision"], changed["revision"])
        names = self.names.for_map(changed)
        self.assertEqual(names["1"], {**initial["1"], "alias": "Кухня"})
        self.assertNotEqual(names["2"]["key"], initial["2"]["key"])

    def test_original_name_map_id_and_geometry_each_protect_against_alias_reuse(self):
        first = self.names.for_map(self.plan)["1"]
        request = {"key": first["key"], "expected": None, "name": "Кухня"}
        self.names.save(self.plan, request)
        pixels = self.saved.pixel_type.copy()
        pixels[0, 0] = 2
        changes = ({"map_id": 43}, {"segments": {1: Segment(1, 0, 0, 4, 4, custom_name="Спальня")}},
                   {"pixel_type": pixels})
        for change in changes:
            with self.subTest(change=next(iter(change))):
                saved = sample_map()
                for key, value in change.items():
                    setattr(saved, key, value)
                plan = interactive_map(saved)
                self.assertIsNone(self.names.for_map(plan)["1"]["alias"])
                with self.assertRaisesRegex(Refused, "Комната на карте изменилась"):
                    self.names.save(plan, {**request, "name": None})

    def test_invalid_or_corrupt_names_do_not_overwrite_the_file(self):
        key = self.names.for_map(self.plan)["1"]["key"]
        for name in (" ", "а\nб", "\nа", "x" * 49, {}, 12):
            with self.subTest(name=name), self.assertRaises(Refused):
                self.names.save(self.plan, {"key": key, "expected": None, "name": name})
        self.assertFalse(self.names.path.exists())
        contents = json.dumps({key: 42})
        self.names.path.write_text(contents, encoding="utf-8")
        self.assertIsNone(self.names.for_map(self.plan)["1"]["alias"])
        with self.assertRaises(Refused):
            self.names.save(self.plan, {"key": key, "expected": None, "name": "Кухня"})
        self.assertEqual(self.names.path.read_text(encoding="utf-8"), contents)
