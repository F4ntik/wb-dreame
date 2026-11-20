import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bridge import Bridge
from config import Refused
from preferences import WRITE
from robot import guard
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING
from webui import WebConflict


class SchedulesTest(unittest.TestCase):
    def setUp(self):
        self.values = {P.OFF_PEAK_CHARGING: '{"enable":false,"startTime":"22:00","endTime":"08:00"}',
                       P.SCHEDULE: "23-1-21:30-1000010-1-7-1-2-0;24-3-09:00-0-0-9-1-2-9,2-vendor"}
        account = {"did": "fixture", "model": "dreame.vacuum.r9419a"}
        cloud = SimpleNamespace(device_id=account["did"])
        self.protocol = SimpleNamespace(cloud=cloud, device_cloud=cloud, dreame_cloud=True,
                                        get_properties=Mock(side_effect=self.read), set_property=Mock(side_effect=self.write))
        device = SimpleNamespace(_protocol=self.protocol, device_connected=True, property_mapping=MAPPING)
        robot = SimpleNamespace(account=account, device=device, identity=Mock())
        self.bridge = SimpleNamespace(cloud=robot, latest={"online": True}, halt=threading.Event(),
                                      io_lock=threading.Lock(), commands_enabled=Mock(return_value=True))

    def read(self, query, *, retry_count, timeout):
        self.assertEqual(retry_count, 0)
        return [{**item, "code": 0, "value": self.values[P(int(item["did"]))]} for item in query]

    def write(self, siid, piid, value, *, retry_count):
        self.assertEqual((siid, piid, retry_count), (3, 3, 0))
        guard("set_properties", [{"did": "fixture", "siid": siid, "piid": piid, "value": value}])
        with self.assertRaises(Refused):
            guard("action", {"siid": 2, "aiid": 1})
        self.values[P.OFF_PEAK_CHARGING] = value
        raise TimeoutError  # This reply was lost after the robot saved the setting.

    def call(self, request=None):
        return Bridge.web_schedules(self.bridge, request)

    def change(self):
        return {**self.call()["charging"], "enabled": True, "start": "23:00"}

    def test_read_only_cleaning_and_unsupported_values(self):
        self.bridge.commands_enabled.return_value = False
        state = self.call()
        self.assertFalse(state["allow_commands"])
        self.assertFalse(state["charging"]["enabled"])
        self.assertEqual(state["cleaning"]["tasks"], [
            {"id": 24, "enabled": False, "invalid": True, "time": "09:00", "once": True, "days": 0},
            {"id": 23, "enabled": True, "invalid": False, "time": "21:30", "once": False, "days": 2},
        ])
        self.values[P.SCHEDULE] = ""
        self.assertEqual(self.call()["cleaning"]["tasks"], [])
        self.values[P.SCHEDULE] = "23-2-29:00"
        self.values[P.OFF_PEAK_CHARGING] = ""
        state = self.call()
        self.assertIn("reason", state["cleaning"])
        self.assertIn("reason", state["charging"])
        self.protocol.set_property.assert_not_called()

    def test_night_interval_write_confirmed_after_lost_reply_and_cannot_replay(self):
        request = self.change()
        result = self.call(request)["charging"]
        self.assertTrue(result["enabled"])
        self.assertEqual((result["start"], result["end"]), ("23:00", "08:00"))
        self.assertNotEqual(result["revision"], request["revision"])
        self.assertEqual(json.loads(self.values[P.OFF_PEAK_CHARGING]),
                         {"enable": True, "startTime": "23:00", "endTime": "08:00"})
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(request)
        self.call(result)
        self.protocol.set_property.assert_called_once()

    def test_no_write_when_disabled_stale_invalid_or_attempted_cleaning_edit(self):
        request = self.change()
        self.bridge.commands_enabled.return_value = False
        with self.assertRaises(WebConflict):
            self.call(request)
        self.bridge.commands_enabled.return_value = True
        for bad in ({**request, "enabled": 1}, {**request, "end": "23:00"}, {**request, "kind": "cleaning"},
                    {**request, "start": "24:00"}, {"id": 23, "enabled": False}):
            with self.subTest(bad=bad), self.assertRaises(Refused):
                self.call(bad)
        self.values[P.OFF_PEAK_CHARGING] = self.values[P.OFF_PEAK_CHARGING].replace("22:00", "21:00")
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(request)
        self.protocol.set_property.assert_not_called()

    def test_config_revoked_between_read_and_write(self):
        request = self.change()
        self.bridge.commands_enabled.side_effect = [True, False]
        with self.assertRaisesRegex(Refused, "не подтвердил"):
            self.call(request)
        self.assertEqual(json.loads(self.values[P.OFF_PEAK_CHARGING])["enable"], False)
        self.protocol.set_property.assert_called_once()

    def test_guard_rejects_opaque_and_malformed_writes_even_with_token(self):
        for siid, piid, value in ((8, 2, self.values[P.SCHEDULE]), (3, 3, '{"enable":true}'),
                                  (3, 3, '{"enable":true,"startTime":"01:00","endTime":"01:00"}')):
            token = WRITE.set(("fixture", siid, piid, value))
            try:
                with self.assertRaises(Refused):
                    guard("set_properties", [{"did": "fixture", "siid": siid, "piid": piid, "value": value}])
            finally:
                WRITE.reset(token)
