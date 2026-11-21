import json
import os
import tempfile
import unittest
from copy import deepcopy
from functools import partial
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import cli
from bridge import Bridge
from config import DEFAULT, Refused
from panel import interactive_map
from room_names import RoomNames, confed_save, confed_view, read_room_catalog, room_aliases
from test_panel import sample_map
from vendor.types import MapImageDimensions, Segment


class RoomNamesTest(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.directory = Path(state.name)
        self.catalog = self.directory / "rooms.json"
        self.bridge = Bridge()
        self.bridge.room_names = RoomNames("device-a", catalog=self.catalog)
        self.saved = sample_map()
        self.saved.segments[2] = Segment(2, 0, 0, 4, 4, custom_name="Коридор")
        self.config = {**DEFAULT, "allow_commands": True, "profiles": [{
            "id": "hall", "name": "Коридор", "map_id": 42, "repeats": 2, "kind": "rooms", "rooms": [2]}]}
        if os.name == "nt":
            writer = patch("room_names.atomic", lambda path, value: path.write_text(json.dumps(value), encoding="utf-8"))
            writer.start()
            self.addCleanup(writer.stop)

    def snapshot(self):
        self.bridge.snapshot({"state": "CHARGING", "fw": "test", "battery": 50, "map_id": self.saved.map_id,
                              "rooms": {}, "saved_map": self.saved})
        return read_room_catalog(self.catalog)

    def through_cli(self, action, payload):
        stdout = BytesIO()
        with (patch("sys.argv", ["wb-dreame", action]),
              patch("sys.stdin", SimpleNamespace(buffer=BytesIO(json.dumps(payload).encode()))),
              patch("sys.stdout", SimpleNamespace(buffer=stdout)),
              patch("cli.CONFIG", self.directory / "config.json"),
              patch("room_names.room_aliases", side_effect=partial(room_aliases, directory=self.directory)),
              patch("room_names.read_room_catalog", side_effect=partial(read_room_catalog, path=self.catalog))):
            cli.main()
        return json.loads(stdout.getvalue())

    def test_confed_round_trip_keeps_profiles_and_inactive_aliases_and_can_reset(self):
        rooms = self.snapshot()
        key, other = rooms[0]["key"], "f" * 64
        legacy = self.directory / "room-names.json"
        aliases = {key: "Кухня Павла", other: "Старая карта"}
        legacy.write_text(json.dumps(aliases), encoding="utf-8")
        config = self.directory / "config.json"
        config.write_text(json.dumps(self.config), encoding="utf-8")
        form = self.through_cli("confed-tojson", self.config)
        self.assertEqual(form["rooms"][0]["name"], "Кухня Павла")
        form["poll_seconds"] = 70
        form["rooms"][0]["name"] = ""
        form["rooms"][1]["name"] = " Прихожая "
        result = self.through_cli("confed-fromjson", form)
        self.assertEqual(result, {**self.config, "poll_seconds": 70,
                                  "room_names": {other: "Старая карта", rooms[1]["key"]: "Прихожая"}})
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8")), aliases)
        view = self.through_cli("confed-tojson", result)
        self.assertEqual([row["name"] for row in view["rooms"]], ["", "Прихожая"])
        self.bridge.room_names = RoomNames("device-a", room_aliases(result, self.directory))
        web = self.bridge.web_state()
        self.assertEqual(web["room_names"]["2"]["alias"], "Прихожая")
        self.assertEqual(web["map"]["rooms"]["2"], "Коридор")

    def test_navigation_round_trip_preserves_legacy_config_and_rejects_unknown_layout(self):
        original = self.config.copy()
        form = self.through_cli("confed-tojson", original)
        self.assertEqual(form["panel_navigation"], "auto")
        for placement in ("left", "top", "auto"):
            form["panel_navigation"] = placement
            path = self.directory / "config.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            self.config = self.through_cli("confed-fromjson", form)
            form = self.through_cli("confed-tojson", self.config)
            self.assertEqual(form["panel_navigation"], placement)
        self.assertEqual(self.config, original)
        with self.assertRaises(Refused):
            confed_save({**form, "panel_navigation": "unknown"}, self.config, [], {})

    def test_new_room_discovery_and_stale_edit_rejection(self):
        self.saved.segments.pop(2)
        before = self.snapshot()
        self.assertEqual(len(before), 1)
        pending = confed_view(self.config, before, {})
        pending["rooms"][0]["name"] = "Кухня"
        self.saved.segments[2] = Segment(2, 0, 0, 4, 4, custom_name="Коридор")
        after = self.snapshot()
        self.assertEqual(len(after), 2)
        self.assertEqual(before[0], after[0])
        with self.assertRaisesRegex(Refused, "Список комнат изменился"):
            confed_save(pending, self.config, after, {})
        current = confed_view(self.config, after, {})
        with self.assertRaisesRegex(Refused, "Настройки уже изменились"):
            confed_save(current, {**self.config, "poll_seconds": 90}, after, {})
        self.bridge.offline("Нет ответа")
        self.assertEqual(read_room_catalog(self.catalog), [])

    def test_another_room_change_and_map_padding_keep_unchanged_room_name(self):
        self.saved.segments[2].custom_name = "Холл"
        first = interactive_map(self.saved)
        initial = self.bridge.room_names.for_map(first)
        self.saved.segments[2].custom_name = "Коридор"
        self.saved.pixel_type[3, 1] = 0
        self.saved.pixel_type = np.pad(self.saved.pixel_type, ((1, 0), (0, 0)))
        self.saved.dimensions = MapImageDimensions(-20, -40, 3, 6, 10)
        changed = interactive_map(self.saved, first)
        self.assertNotEqual(first["revision"], changed["revision"])
        aliases = {initial["1"]["key"]: "Кухня"}
        names = RoomNames("device-a", aliases).for_map(changed)
        self.assertEqual(names["1"], {**initial["1"], "alias": "Кухня"})
        self.assertNotEqual(names["2"]["key"], initial["2"]["key"])

    def test_reused_room_id_and_another_robot_do_not_inherit_alias(self):
        rooms = self.snapshot()
        aliases = {rooms[0]["key"]: "Кухня"}
        self.assertIsNone(RoomNames("device-b", aliases).for_map(interactive_map(self.saved))["1"]["alias"])
        form = confed_view(self.config, rooms, aliases)
        pixels = self.saved.pixel_type.copy()
        pixels[0, 0] = 2
        changes = ({"map_id": 43}, {"segments": {**self.saved.segments, 1: Segment(1, 0, 0, 4, 4, custom_name="Спальня")}},
                   {"pixel_type": pixels})
        for change in changes:
            with self.subTest(change=next(iter(change))):
                old = {key: getattr(self.saved, key) for key in change}
                for key, value in change.items():
                    setattr(self.saved, key, value)
                changed = self.snapshot()
                self.assertIsNone(RoomNames("device-a", aliases).for_map(interactive_map(self.saved))["1"]["alias"])
                with self.assertRaisesRegex(Refused, "Комната на карте изменилась"):
                    confed_save(form, self.config, changed, aliases)
                for key, value in old.items():
                    setattr(self.saved, key, value)

    def test_invalid_edits_and_corrupt_legacy_file_are_refused(self):
        rooms = self.snapshot()
        form = confed_view(self.config, rooms, {})
        for name in (" ", "а\nб", "\nа", "x" * 49, {}, 12):
            with self.subTest(name=name), self.assertRaises(Refused):
                request = deepcopy(form)
                request["rooms"][0]["name"] = name
                confed_save(request, self.config, rooms, {})
        form["rooms"][0]["key"] = rooms[1]["key"]
        with self.assertRaises(Refused):
            confed_save(form, self.config, rooms, {})
        legacy = self.directory / "room-names.json"
        contents = json.dumps({rooms[0]["key"]: 42})
        legacy.write_text(contents, encoding="utf-8")
        with self.assertRaises(Refused):
            room_aliases(self.config, self.directory)
        (self.directory / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        with self.assertLogs(level="WARNING"):
            available = self.through_cli("confed-tojson", self.config)
            self.assertEqual(available["rooms"], [])
            available["allow_commands"] = False
            self.assertEqual(self.through_cli("confed-fromjson", available), {**self.config, "allow_commands": False})
        self.assertEqual(legacy.read_text(encoding="utf-8"), contents)
