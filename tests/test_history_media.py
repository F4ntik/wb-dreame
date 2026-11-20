import base64
import json
import logging
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from history_media import camera_status, obstacle_photo, obstacles
from vendor.types import (
    DreameVacuumAIProperty as AI,
    DreameVacuumProperty as P,
    DreameVacuumPropertyMapping,
    DreameVacuumStreamStatus as STREAM,
    MapData, Obstacle, ObstaclePictureStatus, ObstacleType,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mMwAgAANAAzYwte4gAAAABJRU5ErkJggg==")
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5"
    "PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAB//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AMX//2Q=="
)


class HistoryMediaTest(unittest.TestCase):
    def setUp(self):
        self.photo = Obstacle(125.5, -480, ObstacleType.WIRE, 94, "demo-obstacle", "/fixture/blob", "fixture-key",
                              picture_status=ObstaclePictureStatus.UPLOADED)
        self.laser = Obstacle(101, -200, ObstacleType.OBSTACLE, 95)
        self.map = MapData()
        self.map.history_map = True
        self.map.map_id, self.map.frame_id, self.map.timestamp_ms, self.map.last_updated = 42, 3, 123456, 1234.5
        self.map.obstacles = {"1": self.laser, "7": self.photo}
        self.map.laser_obstacles = {"2": self.laser}
        self.cloud = SimpleNamespace(logged_in=True, get_batch_device_datas=Mock(return_value={
            "prop.s_ai_config": json.dumps({"privacyAuthed": True})
        }))
        mapping = DreameVacuumPropertyMapping[P.AI_OBSTACLE_DETECTION]
        self.protocol = SimpleNamespace(dreame_cloud=True, cloud=self.cloud, device_cloud=self.cloud,
                                        get_properties=Mock(return_value=[{**mapping, "code": 0,
                                            "value": AI.AI_OBSTACLE_PICTURE.value | AI.AI_OBSTACLE_DETECTION.value}]))
        self.manager = SimpleNamespace(_protocol=self.protocol, get_obstacle_image=Mock(return_value=(PNG, self.photo)))
        self.device = SimpleNamespace(capability=SimpleNamespace(camera_streaming=True, local_storage=False),
                                      status=SimpleNamespace(stream_status=STREAM.IDLE, ai_obstacle_picture=True,
                                          ai_obstacle_image_upload=False, ai_policy_accepted=True),
                                      property_mapping=DreameVacuumPropertyMapping, _protocol=self.protocol,
                                      _map_manager=self.manager)

    def photo_id(self):
        return next(item["id"] for item in obstacles(self.map, self.device) if item["photo_available"])

    def test_markers_and_status_are_cache_only_and_do_not_export_credentials(self):
        markers = obstacles(self.map, self.device)
        self.assertEqual(len({item["id"] for item in markers}), 3)
        item = next(marker for marker in markers if marker["photo_available"])
        self.assertEqual((item["label"], item["x"], item["y"]), ("Провод", 125.5, -480))
        self.assertEqual(set(item), {"id", "kind", "label", "x", "y", "photo_available"})
        self.assertRegex(item["id"], "^[0-9a-f]{32}$")
        self.assertNotIn("fixture", json.dumps(markers))
        self.assertEqual(camera_status(self.device), {"camera_supported": True, "stream_status": "idle",
            "photo_capture": True, "photo_upload": False, "privacy_confirmed": None, "live_available": False})
        self.protocol.get_properties.assert_not_called()
        self.cloud.get_batch_device_datas.assert_not_called()
        self.manager.get_obstacle_image.assert_not_called()

    def test_read_needs_explicit_privacy_and_the_fresh_view_setting(self):
        public_id = self.photo_id()
        for raw in ("{}", '{"privacyAuthed":"true"}', '{"privacyAuthed":true,"aiPrivacyAuthed":false}'):
            self.cloud.get_batch_device_datas.return_value = {"prop.s_ai_config": raw}
            with self.subTest(raw=raw):
                self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.cloud.get_batch_device_datas.reset_mock()
        self.protocol.get_properties.return_value[0]["value"] = AI.AI_OBSTACLE_IMAGE_UPLOAD.value
        self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.cloud.get_batch_device_datas.assert_not_called()
        self.manager.get_obstacle_image.assert_not_called()

    def test_upload_off_does_not_prevent_reading_an_existing_permitted_picture(self):
        public_id = self.photo_id()
        self.assertEqual(obstacle_photo(self.map, self.device, public_id), (PNG, "image/png"))
        self.manager.get_obstacle_image.assert_called_once()
        args = self.manager.get_obstacle_image.call_args.args
        self.assertIs(args[0], self.map)
        self.assertEqual(args[1], "7")
        self.assertEqual(self.protocol.get_properties.call_args.kwargs, {"retry_count": 0, "timeout": 6})
        self.cloud.get_batch_device_datas.assert_called_once_with(["prop.s_ai_config"])
        self.manager.get_obstacle_image.return_value = (None, None)
        with self.assertRaisesRegex(LookupError, "не вернул фотографию"):
            obstacle_photo(self.map, self.device, public_id)

    def test_marker_does_not_resolve_on_live_other_archived_or_changed_map(self):
        public_id = self.photo_id()
        other = deepcopy(self.map)
        other.last_updated += 1800
        self.assertIsNone(obstacle_photo(other, self.device, public_id))
        self.assertIsNone(obstacle_photo(self.map, self.device, "7"))
        self.map.history_map = False
        self.assertEqual(obstacles(self.map, self.device), [])
        self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.map.history_map = True
        self.photo.file_name = "/fixture/other"
        self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.manager.get_obstacle_image.assert_not_called()
        self.protocol.get_properties.assert_not_called()

    def test_same_history_record_decoded_again_retains_marker_id(self):
        public_id = self.photo_id()
        decoded_again = deepcopy(self.map)
        self.assertIsNot(decoded_again, self.map)
        self.manager.get_obstacle_image.return_value = (PNG, decoded_again.obstacles["7"])
        self.assertEqual(obstacle_photo(decoded_again, self.device, public_id), (PNG, "image/png"))
        self.assertIs(self.manager.get_obstacle_image.call_args.args[0], decoded_again)

    def test_filters_vendor_media_logs_and_rejects_wrong_target_and_non_raster(self):
        public_id = self.photo_id()
        logger = logging.getLogger("vendor.protocol")

        def fetch(*_):
            logger.warning("fixture-private-url")
            return b"<html>fixture</html>", self.photo

        self.manager.get_obstacle_image.side_effect = fetch
        with self.assertLogs("vendor.protocol", level="WARNING") as records:
            self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
            logger.warning("outside photo call")
        self.assertEqual(records.output, ["WARNING:vendor.protocol:outside photo call"])
        self.manager.get_obstacle_image.side_effect = None
        self.manager.get_obstacle_image.return_value = (PNG, self.laser)
        self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.manager.get_obstacle_image.return_value = (JPEG, self.photo)
        self.assertEqual(obstacle_photo(self.map, self.device, public_id), (JPEG, "image/jpeg"))

    def test_metadata_changed_during_privacy_read_refuses_fetch(self):
        public_id = self.photo_id()

        def changed(_):
            self.photo.file_name = "/fixture/new"
            return {"prop.s_ai_config": '{"privacyAuthed":true}'}

        self.cloud.get_batch_device_datas.side_effect = changed
        self.assertIsNone(obstacle_photo(self.map, self.device, public_id))
        self.manager.get_obstacle_image.assert_not_called()
