import hashlib
import json
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import Mock, patch

import numpy as np

from vendor.map import DreameMapVacuumMapManager, DreameVacuumMapDecoder
from vendor.types import MapData, MapDataPartial, MapFrameType, MapImageDimensions, Segment


class SavedMapUpdatesTest(unittest.TestCase):
    @staticmethod
    def saved(grid, names, version):
        data = MapData()
        data.map_id = 7
        data.saved_map = True
        data.empty_map = False
        data.version = version
        data.pixel_type = np.array(grid, dtype=np.uint8)
        w, h = data.pixel_type.shape
        data.dimensions = MapImageDimensions(0, 0, h, w, 50)
        data.segments = {}
        for key, name in names.items():
            room = Segment(key)
            room.name = name
            data.segments[key] = room
        return data

    @staticmethod
    def manager(version=3):
        manager = DreameMapVacuumMapManager(Mock())
        manager._capability = Mock(lidar_navigation=True, cleaning_sequence_v2=True, map_v2=version == 3)
        manager._selected_map_id = 7
        manager._map_data = MapData()
        manager._map_data.version = version
        manager._map_data.saved_map_id = 7
        manager.schedule_update = Mock()
        return manager

    @staticmethod
    def mock_decoder(stack):
        decode = stack.enter_context(patch.object(DreameVacuumMapDecoder, "decode_saved_map"))
        for method in ("set_capability", "set_floor_material", "set_carpet_cleanset", "set_segment_cleanset"):
            stack.enter_context(patch.object(DreameVacuumMapDecoder, method))
        return decode

    def test_cloud_revisions_of_selected_map(self):
        for version in (2, 3):
            with self.subTest(version=version), ExitStack() as stack:
                manager = self.manager(version)
                original = self.saved([[1, 1], [1, 1]], {1: "Room"}, version)
                manager._saved_map_data = {7: original}
                decode = self.mock_decoder(stack)

                for grid, names in (
                    ([[1, 1], [2, 2]], {1: "Room", 2: "New"}),  # split
                    ([[1, 2], [2, 2]], {1: "Room", 2: "New"}),  # same ids, new boundary
                    ([[1, 2], [2, 2]], {1: "Room", 2: "Office"}),  # rename
                    ([[2, 2], [2, 2]], {2: "Office"}),  # merge
                ):
                    incoming = self.saved(grid, names, version)
                    decode.return_value = incoming
                    payload = json.dumps({"curr_id": 7, "mapstr": [{"map": str(grid) + str(names)}]}).encode()
                    source = ("fixture", hashlib.md5(payload, usedforsecurity=False).hexdigest())
                    manager.set_map_list_object_name(*source)
                    manager._get_interim_file_data = Mock(return_value=payload)
                    DreameMapVacuumMapManager.request_map_list(manager)
                    self.assertIs(manager._saved_map_data[7], incoming)
                    self.assertTrue(manager.command_map_matches(manager.selected_map, source))

    def test_bad_snapshot_cannot_authorize_cached_map(self):
        manager = self.manager()
        complete = b'{"curr_id":7,"mapstr":[{"map":"fixture"}]}'
        old = self.saved([[1, 1], [1, 1]], {1: "Room"}, 3)
        manager._saved_map_data[7] = old
        bad = self.saved([[0, 0], [0, 0]], {}, 3)
        for data, decoded, checksum in (
            (b'broken', old, None),
            (b'{"curr_id":8,"mapstr":[{"map":"fixture"}]}', old, None),
            (complete, bad, None),
            (complete, deepcopy(old), "0" * 32),
        ):
            with self.subTest(data=data, checksum=checksum), ExitStack() as stack:
                source = ("list", checksum or hashlib.md5(data, usedforsecurity=False).hexdigest())
                manager.set_map_list_object_name(*source)
                manager._get_interim_file_data = Mock(return_value=data)
                self.mock_decoder(stack).return_value = decoded
                manager.request_map_list()
                self.assertIs(manager.selected_map, old)
                self.assertFalse(manager.command_map_matches(old, source))

    def test_inflight_old_list_cannot_authorize_even_if_source_changes_back(self):
        manager = self.manager()
        old = self.saved([[1, 1], [1, 1]], {1: "Room"}, 3)
        manager._saved_map_data[7] = old
        payload = b'{"curr_id":7,"mapstr":[{"map":"fixture"}]}'
        source = ("list", hashlib.md5(payload, usedforsecurity=False).hexdigest())
        manager.set_map_list_object_name(*source)

        def changed(_):
            manager.set_map_list_object_name("new-list", "0" * 32)
            manager.set_map_list_object_name(*source)
            return payload

        with ExitStack() as stack:
            self.mock_decoder(stack).return_value = deepcopy(old)
            manager._get_interim_file_data = Mock(side_effect=changed)
            manager.request_map_list()
            self.assertIs(manager.selected_map, old)
            self.assertFalse(manager.command_map_matches(old, source))
            self.assertTrue(manager._need_map_list_request)
            manager._get_interim_file_data = Mock(return_value=payload)
            manager.request_map_list()
            self.assertTrue(manager.command_map_matches(manager.selected_map, source))
        manager.selected_map.segments[1].name = "Different room"
        self.assertFalse(manager.command_map_matches(manager.selected_map, source))

    def test_external_plan_can_display_but_checksum_only_covers_list(self):
        manager = self.manager()
        payload = b'{"curr_id":7,"server":1,"mapstr":[{"rismobj":"external-plan"}]}'
        source = ("list", hashlib.md5(payload, usedforsecurity=False).hexdigest())
        manager.set_map_list_object_name(*source)
        files = {"list": payload, "external-plan": b"map"}
        manager._get_interim_file_data = Mock(side_effect=files.get)
        with ExitStack() as stack:
            self.mock_decoder(stack).return_value = self.saved([[1, 1], [1, 1]], {1: "Room"}, 3)
            manager.request_map_list()
        self.assertIsNotNone(manager.selected_map)
        self.assertFalse(manager.command_map_matches(manager.selected_map, source))

    def test_v3_current_frame_updates_saved_rooms_without_other_changes(self):
        for action, old_rooms, new_rooms in (
            ("split", {1: "Living room"}, {1: "Living room", 2: "Office"}),
            ("merge", {1: "Living room", 2: "Office"}, {2: "Office"}),
        ):
            with self.subTest(action=action):
                previous = MapData()
                previous.version = 3
                previous.map_id = 17
                previous.frame_id = 10
                previous.timestamp_ms = 1000
                previous.saved_map_id = 7
                previous.saved_map_status = 2
                previous.segments = {
                    room_id: Segment(room_id, custom_name=name) for room_id, name in old_rooms.items()
                }
                incoming = deepcopy(previous)
                incoming.segments = {
                    room_id: Segment(room_id, custom_name=name) for room_id, name in new_rooms.items()
                }
                # The room list alone must enter the v3 fallback.
                self.assertEqual(previous, incoming)
                self.assertNotEqual(previous.segments, incoming.segments)

                partial = MapDataPartial()
                partial.map_id = previous.map_id
                partial.frame_id = previous.frame_id
                partial.timestamp_ms = previous.timestamp_ms
                partial.frame_type = MapFrameType.I.value
                manager = DreameMapVacuumMapManager(Mock())
                manager._latest_map_id = manager._current_map_id = partial.map_id
                manager._current_frame_id = partial.frame_id
                manager._current_timestamp_ms = partial.timestamp_ms
                manager._map_data = previous
                manager._selected_map_id = previous.saved_map_id
                manager._change_callback = Mock()
                saved = MapData()
                saved.map_id = previous.saved_map_id
                saved.saved_map = True
                saved.segments = deepcopy(previous.segments)
                manager._saved_map_data[saved.map_id] = saved

                with patch.object(
                    DreameVacuumMapDecoder, "decode_map_data_from_partial", return_value=(incoming, None)
                ):
                    self.assertTrue(manager._add_map_data(partial))

                self.assertIs(manager.selected_map, saved)
                self.assertEqual(
                    {key: (room.id, room.name) for key, room in saved.segments.items()},
                    {key: (key, name) for key, name in new_rooms.items()},
                )
                for key in new_rooms.keys() - old_rooms.keys():
                    self.assertIsNot(saved.segments[key], incoming.segments[key])
                manager._change_callback.assert_any_call(True)
                self.assertEqual(manager._protocol.mock_calls, [])
