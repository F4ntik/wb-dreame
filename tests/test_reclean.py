import json
import unittest
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from threading import Event, Lock
from time import monotonic
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from bridge import Bridge
from config import Refused
from jobs import Jobs
from robot import Robot, guard
from vendor.device import DreameVacuumDevice
from vendor.types import CleanupMethod, DreameVacuumStatus as DS, MapPixelType, ObstacleReason
from webui import WebConflict


class RecleanTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.jobs = Jobs(self.directory)
        self.plan = {"id": 7, "revision": "a" * 64, "rooms": {"2": "Кухня"}}
        self.report = NS(date=datetime.now().replace(microsecond=0), object_name="archive", key="fixture",
                         blocked_segments={2: [ObstacleReason.BLOCKED_BY_DOOR]}, multiple_cleaning_time="123456",
                         cleanup_method=CleanupMethod.DEFAULT_MODE, mopping_mode=0, second_mopping=0)
        rooms = {2: NS(unique_id="room-id")}
        self.archive = NS(history_map=True, empty_map=False, map_id=32, saved_map_id=7, frame_id=3, timestamp_ms=42,
                          multiple_cleaning_time=None, blocked_segments=self.report.blocked_segments.copy(),
                          segments=rooms, cleaning_map_data=None, cleaned_segments=None, dos=None)
        self.robot = Robot({"model": "dreame.vacuum.r9419a", "did": "fixture"}, self.directory)
        self.device = self.robot.device = NS(info=NS(model=self.robot.account["model"]),
            status=NS(_cleaning_history=[self.report], current_map=NS(map_id=37, saved_map_id=7), has_temporary_map=False),
            capability=NS(auto_recleaning=True, cleangenius_auto=False, customized_cleaning=True),
            property_mapping=DreameVacuumDevice.property_mapping, action_mapping=DreameVacuumDevice.action_mapping,
            _map_manager=NS(get_history_map=Mock(return_value=self.archive)),
            refresh_cleaning_records=Mock(return_value=True),
            _protocol=NS(cloud=NS(device_id="fixture"), action=Mock(side_effect=self.action)))
        self.robot._command_map_key = Mock(return_value="current-map")
        self.robot.saved_map = Mock(return_value=NS(segments=rooms))
        self.robot.observe = Mock(side_effect=self.observe)
        self.request = {"request_id": "reclean-1", "action": "reclean", "timestamp": int(self.report.date.timestamp()),
                        "map_id": self.plan["id"], "revision": self.plan["revision"]}
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.jobs, self.bridge.cloud = self.jobs, self.robot
        self.bridge.latest = {"online": True, "map": self.plan}
        self.bridge.commands_enabled = Mock(return_value=True)
        self.bridge.io_lock, self.bridge.halt, self.bridge.refresh = Lock(), Event(), Event()
        self.bridge.display_map = Mock(return_value=(self.plan, None))
        map_patch = patch("bridge.interactive_map", return_value=self.plan)
        map_patch.start()
        self.addCleanup(map_patch.stop)

    def observe(self, *, for_command):
        self.assertIs(for_command, True)
        return {"command_time": monotonic(), "state": "CHARGING_COMPLETED", "task": "COMPLETED",
                "battery": 95, "error": 0, "command_map": True, "command_map_key": "current-map", "map_id": 7,
                "rooms": self.plan["rooms"], "saved_map": self.robot.saved_map(),
                "command_settings": {"mode": "both", "min_battery": 15, "cleangenius": False, "customized": False,
                                     "max_suction": False, "mop_installed": True, "water": 2, "suction": 1,
                                     "verified_wetness": True}}

    def action(self, siid, aiid, parameters, *, retry_count):
        self.assertEqual((siid, aiid, retry_count), (4, 1, 0))
        rpc = {"did": "fixture", "siid": siid, "aiid": aiid, "in": parameters}
        guard("action", rpc)
        with self.assertRaises(Refused):
            guard("action", rpc | {"in": []})
        with self.assertRaises(Refused):
            guard("action", rpc | {"did": "other"})
        self.assertEqual(self.jobs.lookup(self.request)["status"], "unknown")
        return [{"code": 0}]

    def test_preview_is_read_only_and_vendor_blocked_rooms_send_once(self):
        with self.assertRaisesRegex(Refused, "Проверьте задание заново"):
            self.bridge.web_job(self.request, preview=False)
        preview = self.bridge.web_job(self.request, preview=True)
        self.assertEqual(preview["targets"], {"rooms": [2], "dirty_areas": False})
        self.assertEqual(preview["request"], self.request)
        self.assertIsNone(self.jobs.last)
        self.device._protocol.action.assert_not_called()
        self.assertEqual(self.bridge.web_job(self.request, preview=False)["status"], "acknowledged")
        params = self.device._protocol.action.call_args.args[2]
        self.assertEqual(params[0]["value"], DS.SEGMENT_CLEANING.value)
        self.assertEqual(json.loads(params[1]["value"]), {"selects": [[2, 1, 1, 2, 1]], "timestamp": "123456"})
        self.assertEqual(self.device.refresh_cleaning_records.call_count, 3)
        self.device.refresh_cleaning_records.assert_called_with(force=True, strict=True)
        self.assertEqual(self.device._map_manager.get_history_map.call_count, 3)
        self.assertEqual(self.bridge.web_job(self.request, preview=False)["status"], "acknowledged")
        self.bridge.jobs = Jobs(self.directory)
        with self.assertRaisesRegex(Refused, "уже запрашивалась"):
            self.bridge.web_job(self.request | {"request_id": "new-id"}, preview=True)
        self.device._protocol.action.assert_called_once()

    def test_native_dirty_area_changes_in_the_same_archive_invalidate_confirmation(self):
        self.report.cleanup_method = CleanupMethod.CLEANGENIUS
        self.report.second_mopping = 2
        self.report.blocked_segments = self.archive.blocked_segments = None
        self.archive.cleaned_segments = [2]
        pixels = np.array([[MapPixelType.DIRTY_AREA, 0]], dtype=np.uint8)
        self.archive.cleaning_map_data = NS(has_dirty_area=True, pixel_type=pixels,
                                           dimensions=NS(left=0, top=0, width=1, height=2, grid_size=50))
        preview = self.bridge.web_job(self.request, preview=True)
        self.assertEqual(preview["targets"], {"rooms": [], "dirty_areas": True})
        pixels[0, :] = [0, MapPixelType.DIRTY_AREA]
        with self.assertRaisesRegex(Refused, "условия доуборки изменились"):
            self.bridge.web_job(self.request, preview=False)
        self.assertIsNone(self.jobs.last)
        self.device._protocol.action.assert_not_called()
        self.bridge.web_job(self.request, preview=True)
        self.assertEqual(self.bridge.web_job(self.request, preview=False)["status"], "acknowledged")
        params = self.device._protocol.action.call_args.args[2]
        self.assertEqual(params[0]["value"], DS.CLEANING.value)
        self.assertEqual(json.loads(params[1]["value"]), {
            "CleanArea": [2], "BigArea": [], "MopAgain": 1, "timestamp": "123456"})

    def test_does_not_use_cached_report_when_cloud_fails_or_latest_report_changes(self):
        self.bridge.web_job(self.request, preview=True)
        self.device.refresh_cleaning_records.return_value = False
        with self.assertRaisesRegex(Refused, "Не удалось обновить"):
            self.bridge.web_job(self.request, preview=False)
        self.device.refresh_cleaning_records.return_value = True
        self.report.date += timedelta(seconds=1)
        with self.assertRaisesRegex(Refused, "другой последний отчёт"):
            self.bridge.web_job(self.request, preview=False)
        self.assertIsNone(self.jobs.last)
        self.device._protocol.action.assert_not_called()

    def test_missing_evidence_invalid_map_offline_and_operator_lockout(self):
        self.archive.blocked_segments = self.report.blocked_segments = None
        self.archive.cleaned_segments = [2]  # Vendor CleanArea alone does not establish a dirty area.
        with self.assertRaisesRegex(Refused, "не подтвердил пропущенные"):
            self.bridge.web_job(self.request, preview=True)
        self.archive.saved_map_id = 8
        with self.assertRaisesRegex(Refused, "другой карте"):
            self.bridge.web_job(self.request, preview=True)
        self.archive.saved_map_id = None
        with self.assertRaisesRegex(Refused, "другой карте"):
            self.bridge.web_job(self.request, preview=True)
        self.bridge.latest["online"] = False
        with self.assertRaisesRegex(WebConflict, "Нет свежих"):
            self.bridge.web_job(self.request, preview=True)
        self.bridge.latest["online"] = True
        self.bridge.commands_enabled.return_value = False
        with self.assertRaisesRegex(WebConflict, "Запуск выключен"):
            self.bridge.web_job(self.request, preview=False)
        self.assertIsNone(self.jobs.last)
        self.device._protocol.action.assert_not_called()

    def test_timeout_is_durable_and_other_payload_keys_are_refused(self):
        with self.assertRaises(Refused):
            self.bridge.web_job(self.request | {"rooms": [2]}, preview=True)
        self.bridge.web_job(self.request, preview=True)
        self.device._protocol.action.side_effect = TimeoutError
        self.assertEqual(self.bridge.web_job(self.request, preview=False)["status"], "unknown")
        self.bridge.jobs = Jobs(self.directory)
        self.assertEqual(self.bridge.web_job(self.request, preview=False)["status"], "unknown")
        with self.assertRaisesRegex(Refused, "уже запрашивалась"):
            self.bridge.web_job(self.request | {"request_id": "new-id"}, preview=False)
        self.device._protocol.action.assert_called_once()
