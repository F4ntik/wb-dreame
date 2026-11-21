import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bridge import Bridge
from config import Refused
from preferences import WRITE
from robot import guard
from vendor.types import DreameVacuumActionMapping as ACTIONS
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING
from webui import WebConflict


class SchedulesTest(unittest.TestCase):
    def setUp(self):
        self.values = {P.OFF_PEAK_CHARGING: '{"enable":false,"startTime":"22:00","endTime":"08:00"}',
                       P.SCHEDULE: "23-1-21:30-1000010-1-7-1-2-0-extra;24-3-09:00-0-0-9-1-2-9,2-vendor"}
        account = {"did": "fixture", "model": "dreame.vacuum.r9419a"}
        cloud = SimpleNamespace(device_id=account["did"])
        self.protocol = SimpleNamespace(cloud=cloud, device_cloud=cloud, dreame_cloud=True,
                                        get_properties=Mock(side_effect=self.read), set_property=Mock(side_effect=self.write),
                                        action=Mock(side_effect=self.action))
        device = SimpleNamespace(_protocol=self.protocol, device_connected=True, property_mapping=MAPPING,
                                 action_mapping=ACTIONS)
        robot = SimpleNamespace(account=account, device=device, identity=Mock())
        self.bridge = SimpleNamespace(cloud=robot, latest={"online": True}, halt=threading.Event(),
                                      io_lock=threading.Lock(), commands_enabled=Mock(return_value=True))

    def read(self, query, *, retry_count, timeout):
        self.assertEqual(retry_count, 0)
        self.bridge.cloud.device.device_connected = True
        return [{**item, "code": 0, "value": self.values[P(int(item["did"]))]} for item in query]

    def write(self, siid, piid, value, *, retry_count):
        self.assertIn((siid, piid), {(3, 3), (8, 2)})
        self.assertEqual(retry_count, 0)
        guard("set_properties", [{"did": "fixture", "siid": siid, "piid": piid, "value": value}])
        with self.assertRaises(Refused):
            guard("action", {"siid": 2, "aiid": 1})
        self.values[P.OFF_PEAK_CHARGING if siid == 3 else P.SCHEDULE] = value
        self.bridge.cloud.device.device_connected = False
        raise TimeoutError  # This reply was lost after the robot saved the setting.

    def action(self, siid, aiid, args, *, retry_count):
        self.assertEqual((siid, aiid, retry_count), (8, 1, 0))
        task_id = args[0]["value"]
        self.assertIs(type(task_id), str)
        self.assertEqual(args, [{"piid": 3, "value": task_id}])
        message = {"did": "fixture", "siid": siid, "aiid": aiid, "in": args}
        with self.assertRaises(Refused):
            guard("action", {**message, "did": "unbound"})
        with self.assertRaises(Refused):
            guard("action", {**message, "in": [{"piid": 3, "value": int(task_id)}]})
        with self.assertRaises(Refused):
            guard("action", {**message, "in": [{"piid": 3, "value": str(1 + int(task_id))}]})
        guard("action", message)
        with self.assertRaises(Refused):
            guard("action", message)
        self.values[P.SCHEDULE] = ";".join(record for record in self.values[P.SCHEDULE].split(";")
                                           if record.split("-", 1)[0] != str(task_id))
        raise TimeoutError

    def call(self, request=None):
        return Bridge.web_schedules(self.bridge, request)

    def change(self):
        return {**self.call()["charging"], "enabled": True, "start": "23:00"}

    def update_task(self, task_id=23, **fields):
        return {"kind": "cleaning", "operation": "update", "id": task_id,
                "revision": self.call()["cleaning"]["revision"], "enabled": False, "time": "08:45", **fields}

    def delete_task(self, task_id):
        return {"kind": "cleaning", "operation": "delete", "id": task_id,
                "revision": self.call()["cleaning"]["revision"]}

    def test_read_cleaning_and_unsupported_values(self):
        self.bridge.commands_enabled.return_value = False
        state = self.call()
        self.assertFalse(state["allow_commands"])
        self.assertFalse(state["charging"]["enabled"])
        self.assertEqual(state["cleaning"]["tasks"], [
            {"id": 24, "enabled": False, "invalid": True, "time": "09:00", "once": True, "days": 0,
             "weekdays": "0000000", "editable": False, "copyable": False},
            {"id": 23, "enabled": True, "invalid": False, "time": "21:30", "once": False, "days": 2,
             "weekdays": "1000010", "editable": True, "copyable": True},
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

    def test_no_charging_write_when_disabled_stale_or_invalid(self):
        request = self.change()
        self.bridge.commands_enabled.return_value = False
        with self.assertRaises(WebConflict):
            self.call(request)
        self.bridge.commands_enabled.return_value = True
        for bad in ({**request, "enabled": 1}, {**request, "end": "23:00"},
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
        for siid, piid, value in ((8, 2, "23-1-12:00"), (8, 2, ""), (3, 3, '{"enable":true}'),
                                  (3, 3, '{"enable":true,"startTime":"01:00","endTime":"01:00"}')):
            token = WRITE.set(("fixture", siid, piid, value))
            try:
                with self.assertRaises(Refused):
                    guard("set_properties", [{"did": "fixture", "siid": siid, "piid": piid, "value": value}])
            finally:
                WRITE.reset(token)

    def test_cleaning_edit_preserves_vendor_fields_and_confirms_once(self):
        # Sunday is the first mask bit and a Monday/Wednesday/Friday job uses 0101010.
        payload = self.update_task(weekdays="0101010")
        result = self.call(payload)["cleaning"]
        self.assertEqual(self.values[P.SCHEDULE],
                         "23-0-08:45-0101010-1-7-1-2-0-extra;24-3-09:00-0-0-9-1-2-9,2-vendor")
        self.assertEqual(result["tasks"][0]["weekdays"], "0101010")
        self.assertFalse(result["tasks"][0]["enabled"])
        self.assertNotEqual(result["revision"], payload["revision"])
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(payload)
        self.call({**payload, "revision": result["revision"]})
        self.protocol.set_property.assert_called_once()
        self.protocol.action.assert_not_called()

    def test_cleaning_update_refuses_invalid_and_stale_requests(self):
        payload = self.update_task()
        self.bridge.commands_enabled.return_value = False
        with self.assertRaises(WebConflict):
            self.call(payload)
        self.bridge.commands_enabled.return_value = True
        for bad in ({**payload, "operation": "add"}, {**payload, "id": True}, {**payload, "id": 24},
                    {**payload, "enabled": 1}, {**payload, "time": "29:00"}, {**payload, "rooms": [2]},
                    {**payload, "weekdays": None}, {**payload, "weekdays": "0000000"}):
            with self.subTest(bad=bad), self.assertRaises(Refused):
                self.call(bad)
        self.values[P.SCHEDULE] = self.values[P.SCHEDULE].replace("9,2-vendor", "9,3-vendor")
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(payload)
        self.bridge.cloud.account["model"] = "dreame.vacuum.r2492a"
        self.assertFalse(self.call()["cleaning"]["can_edit"])
        with self.assertRaisesRegex(Refused, "Dreamehome"):
            self.call(self.update_task())
        self.protocol.set_property.assert_not_called()
        self.protocol.action.assert_not_called()

    def test_one_shot_time_preserves_repeat_fields(self):
        self.values[P.SCHEDULE] = "23-0-08:45-0-0-7-1-2-0"
        with self.assertRaisesRegex(Refused, "Dreamehome"):
            self.call(self.update_task(weekdays="0101010"))
        self.call(self.update_task(time="08:46", enabled=True))
        self.assertEqual(self.values[P.SCHEDULE], "23-1-08:46-0-0-7-1-2-0")
        self.protocol.set_property.assert_called_once()

    def test_delete_targets_exact_task_and_checks_remaining_records(self):
        payload = self.delete_task(24)
        with self.assertRaises(Refused):
            guard("action", {"did": "fixture", "siid": 8, "aiid": 1, "in": [{"piid": 3, "value": "24"}]})
        result = self.call(payload)["cleaning"]
        self.assertEqual([task["id"] for task in result["tasks"]], [23])
        self.assertEqual(self.values[P.SCHEDULE], "23-1-21:30-1000010-1-7-1-2-0-extra")
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(payload)
        self.protocol.action.assert_called_once()
        self.protocol.set_property.assert_not_called()

    def test_create_disabled_copy_preserves_everything_and_prevents_overlapping_days(self):
        before = self.values[P.SCHEDULE]
        payload = {"kind": "cleaning", "operation": "clone", "id": 23, "time": "21:30", "weekdays": "0101100",
                   "revision": self.call()["cleaning"]["revision"]}
        for bad, message in (({**payload, "weekdays": "1100000"}, "уже есть"),
                             ({**payload, "id": 24}, "Dreamehome"),
                             ({**payload, "enabled": True}, "Проверьте поля")):
            with self.subTest(bad=bad), self.assertRaisesRegex(Refused, message):
                self.call(bad)
        self.protocol.set_property.assert_not_called()

        result = self.call(payload)["cleaning"]
        self.assertEqual(self.values[P.SCHEDULE], before + ";25-0-21:30-0101100-1-7-1-2-0-extra")
        copy = next(task for task in result["tasks"] if task["id"] == 25)
        self.assertFalse(copy["enabled"])
        self.assertTrue(copy["copyable"])
        with self.assertRaisesRegex(Refused, "изменилось"):
            self.call(payload)
        self.protocol.set_property.assert_called_once()
        self.protocol.action.assert_not_called()

    def test_delete_accepts_delayed_readback_without_a_second_action(self):
        normal_read = self.protocol.get_properties.side_effect
        starting_value = self.values[P.SCHEDULE]

        def delayed_read(query, **kwargs):
            rows = normal_read(query, **kwargs)
            if self.protocol.action.call_count and self.protocol.get_properties.call_count == 4:
                rows[0]["value"] = starting_value
            return rows

        self.protocol.get_properties.side_effect = delayed_read
        result = self.call(self.delete_task(23))["cleaning"]
        self.assertEqual([task["id"] for task in result["tasks"]], [24])
        self.assertEqual(self.protocol.get_properties.call_count, 5)
        self.protocol.action.assert_called_once()

    def test_no_success_when_robot_acknowledges_but_list_differs(self):
        def unexpected_write(*_args, **_kwargs):
            self.values[P.SCHEDULE] = "23-0-08:45-1000010-1-7-1-2-0-extra"
            return [{"code": 0}]

        self.protocol.set_property.side_effect = unexpected_write
        with self.assertRaisesRegex(Refused, "не подтвердил"):
            self.call(self.update_task())
        self.protocol.set_property.assert_called_once()
        self.protocol.action.assert_not_called()
