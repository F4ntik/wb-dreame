import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge import Bridge
from cleaning_preferences import apply, offered
from config import Refused
from preferences import auto_switch_value, property_write_permitted
from robot import Robot
from vendor.types import DreameVacuumAutoSwitchProperty as A, DreameVacuumDeviceCapability
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING
from vendor.types import DreameVacuumState as S


class CleaningPreferencesTest(unittest.TestCase):
    def setUp(self):
        model = "dreame.vacuum.r9419a"
        did = "fixture"
        cloud = SimpleNamespace(device_id=did)
        self.protocol = SimpleNamespace(cloud=cloud, device_cloud=cloud, dreame_cloud=True,
                                        get_properties=Mock(side_effect=self.read),
                                        set_property=Mock(side_effect=self.write))
        self.values = {
            P.STATE: S.DRYING.value, P.TASK_STATUS: 0, P.ERROR: 68, P.FAULTS: "68", P.CHARGING_STATUS: 1,
            P.SELF_WASH_BASE_STATUS: 2, P.CLEANING_MODE: 0, P.SUCTION_LEVEL: 2, P.WETNESS_LEVEL: 20,
            P.DRAINAGE_STATUS: 0,
            P.WATER_TANK: 10, P.MOP_PAD_INSTALLED: 0, P.AUTO_MOUNT_MOP: 1, P.MOP_IN_STATION: 0,
            P.CUSTOMIZED_CLEANING: 0, P.AUTO_WATER_REFILLING: 0, P.MOP_WASH_LEVEL: 1,
            P.SELF_CLEAN: 1, P.SMART_MOP_WASHING: 0, P.CARPET_BOOST: 1, P.CARPET_CLEANING: 2,
            P.CARPET_RECOGNITION: 1, P.CHILD_LOCK: 0, P.RESUME_CLEANING: 1,
        }
        self.switches = {"SmartHost": 0, "CleanRoute": 1, "SuctionMax": 0, "SuperWash": 0, "BackWashType": 3}
        self.set_switches()
        self.cached = dict(self.values)
        self.cap = DreameVacuumDeviceCapability(None)
        for flag in ("self_wash_base", "mop_pad_lifting", "mop_pad_unmounting", "auto_switch_settings",
                     "cleangenius", "cleaning_route", "mopping_after_sweeping", "wetness", "wetness_level",
                     "self_clean_frequency", "smart_mop_washing", "ultra_clean_mode", "max_suction_power",
                     "carpet_recognition", "customized_cleaning", "drainage", "water_check"):
            setattr(self.cap, flag, True)
        self.device = SimpleNamespace(info=SimpleNamespace(model=model), device_connected=True,
                                      property_mapping=MAPPING, _protocol=self.protocol, capability=self.cap,
                                      status=SimpleNamespace(has_saved_map=True, has_temporary_map=False),
                                      get_property=self.get_cached)
        self.robot = SimpleNamespace(account={"model": model, "did": did}, device=self.device, identity=Mock())

    def get_cached(self, prop):
        if isinstance(prop, A):
            return auto_switch_value(self.cached.get(P.AUTO_SWITCH_SETTINGS), prop)
        return self.cached.get(prop)

    def set_switches(self, **changes):
        self.switches.update(changes)
        self.values[P.AUTO_SWITCH_SETTINGS] = json.dumps([{"k": k, "v": v} for k, v in self.switches.items()])

    def read(self, query, *, retry_count, timeout):
        self.assertEqual((retry_count, timeout), (0, 6))
        properties = [P(int(item["did"])) for item in query]
        self.assertLessEqual(len(query), 10 if P.AUTO_SWITCH_SETTINGS in properties else 15)
        return [{**item, "code": 0, "value": self.values.get(P(int(item["did"])))} for item in query]

    def write(self, siid, piid, value, *, retry_count):
        self.assertEqual(retry_count, 0)
        params = {"did": self.robot.account["did"], "siid": siid, "piid": piid, "value": value}
        self.assertFalse(property_write_permitted("action", [params]))
        self.assertFalse(property_write_permitted("set_properties", [dict(params, did="other")]))
        self.assertTrue(property_write_permitted("set_properties", [params]))
        self.assertFalse(property_write_permitted("set_properties", [params]))
        target = next(prop for prop, mapping in MAPPING.items() if mapping == {"siid": siid, "piid": piid})
        if target is P.AUTO_SWITCH_SETTINGS:
            entry = json.loads(value)
            self.set_switches(**{entry["k"]: entry["v"]})
        else:
            self.values[target] = value
        raise TimeoutError

    def update(self, key, expected, value):
        return apply(self.robot, {"key": key, "value": value, "expected": expected})

    def item(self, key):
        return next(item for item in offered(self.device) if item["key"] == key)

    def observing_robot(self):
        self.protocol.cloud.auth_key = None
        self.device.info.firmware_version = "test"
        self.device.status.current_map = self.device.status.selected_map = None
        self.device.status.cleaning_mode = None
        robot = Robot(self.robot.account, ".")
        robot.device = self.device
        return robot

    def test_supported_dock_settings_send_only_their_property_once(self):
        for key, expected, value, target, wire in (
            ("suction_level", 2, 1, P.SUCTION_LEVEL, 1),
            ("water_volume", 2, 3, P.WETNESS_LEVEL, 27),
            ("cleaning_route", 1, 4, P.AUTO_SWITCH_SETTINGS, '{"k":"CleanRoute","v":4}'),
            ("mop_wash_level", 1, 2, P.MOP_WASH_LEVEL, 2),
            ("carpet_boost", 1, 0, P.CARPET_BOOST, 0),
            ("resume_cleaning", 1, 0, P.RESUME_CLEANING, 0),
            ("child_lock", 0, 1, P.CHILD_LOCK, 1),
            ("auto_water_refilling", 0, 1, P.AUTO_WATER_REFILLING, 1),
        ):
            with self.subTest(key=key):
                self.protocol.set_property.reset_mock()
                self.protocol.get_properties.reset_mock()
                self.assertEqual(self.item(key)["value"], expected)
                self.assertEqual(self.update(key, expected, value), {"key": key, "value": value, "changed": True})
                self.protocol.set_property.assert_called_once_with(MAPPING[target]["siid"], MAPPING[target]["piid"],
                                                                 wire, retry_count=0)
                self.assertGreaterEqual(self.protocol.get_properties.call_count, 2)

    def test_mode_and_legacy_water_each_preserve_unrelated_packed_bits(self):
        self.cap.wetness = self.cap.wetness_level = False
        self.values[P.CLEANING_MODE] = self.cached[P.CLEANING_MODE] = 0x020818
        self.assertEqual(self.item("cleaning_mode")["value"], 2)
        self.assertTrue(self.update("cleaning_mode", 2, 1)["changed"])
        self.assertEqual(self.values[P.CLEANING_MODE], 0x020819)
        self.assertTrue(self.update("water_volume", 2, 1)["changed"])
        self.assertEqual(self.values[P.CLEANING_MODE], 0x010819)

    def test_settings_reject_fresh_conflicts_without_a_write(self):
        self.set_switches(SmartHost=1)
        with self.assertRaisesRegex(Refused, "CleanGenius"):
            self.update("suction_level", 2, 1)
        self.set_switches(SmartHost=0)
        with self.assertRaisesRegex(Refused, "вариант"):
            self.update("cleaning_route", 1, 3)
        self.values[P.SMART_MOP_WASHING] = 1
        with self.assertRaisesRegex(Refused, "промывку"):
            self.update("mop_wash_level", 1, 2)
        self.cap.water_check = False
        with self.assertRaisesRegex(Refused, "поддержку"):
            self.update("auto_water_refilling", 0, 1)
        self.values[P.CLEANING_MODE] = 21 << 8
        with self.assertRaisesRegex(Refused, "вариант"):
            self.update("water_volume", 2, 3)
        self.values[P.CARPET_RECOGNITION] = 0
        with self.assertRaisesRegex(Refused, "ковров"):
            self.update("carpet_boost", 1, 0)
        self.values[P.DRAINAGE_STATUS] = 1
        with self.assertRaisesRegex(Refused, "заняты"):
            self.update("child_lock", 0, 1)
        self.values[P.DRAINAGE_STATUS] = 0
        self.values[P.FAULTS] = "68,43"
        with self.assertRaisesRegex(Refused, "об ошибке"):
            self.update("child_lock", 0, 1)
        self.protocol.set_property.assert_not_called()

    def test_changed_wash_interval_blocks_packed_mode_write(self):
        self.values[P.CLEANING_MODE] = self.cached[P.CLEANING_MODE] = 0x020818

        def changed_interval(query, **kwargs):
            if [item["did"] for item in query] == [str(P.CLEANING_MODE.value)]:
                self.values[P.CLEANING_MODE] = 0x020918
            return self.read(query, **kwargs)

        self.protocol.get_properties.side_effect = changed_interval
        with self.assertRaisesRegex(Refused, "изменились"):
            self.update("cleaning_mode", 2, 1)
        self.protocol.set_property.assert_not_called()

    def test_stale_expected_and_uncertain_write_are_not_retried(self):
        self.values[P.SUCTION_LEVEL] = 3
        with self.assertRaisesRegex(Refused, "уже изменилось"):
            self.update("suction_level", 2, 1)
        self.assertFalse(self.update("child_lock", 1, 0)["changed"])
        self.protocol.set_property.assert_not_called()
        self.protocol.set_property.side_effect = TimeoutError
        with self.assertRaisesRegex(Refused, "Не удалось подтвердить"):
            self.update("child_lock", 0, 1)
        self.protocol.set_property.assert_called_once()

    def test_panel_refreshes_from_conflict_and_readback_without_reloading_vendor_cache(self):
        robot = self.observing_robot()
        bridge = Bridge()
        bridge.cloud = robot
        bridge.commands_enabled = lambda: True
        bridge.latest["online"] = True

        def shown(rows):
            return next(row["value"] for row in rows["preferences"] if row["key"] == "suction_level")

        self.assertEqual(shown(robot.preference_state(poll={})), 2)
        self.values[P.SUCTION_LEVEL] = 3
        payload = {"key": "suction_level", "value": 1, "expected": 2}
        conflict = bridge.web_preference(payload)
        self.assertEqual((conflict["status"], conflict["value"], shown(conflict)), ("conflict", 3, 3))
        reads = self.protocol.get_properties.call_count
        self.assertEqual(reads, 2)
        self.assertEqual(shown(bridge.web_state()), 3)
        self.assertEqual(shown(robot.preference_state(poll={})), 3)
        self.assertEqual(self.protocol.get_properties.call_count, reads)
        self.protocol.set_property.assert_not_called()

        result = bridge.web_preference({**payload, "expected": 3})
        self.assertEqual((result["status"], result["changed"], shown(result)), ("confirmed", True, 1))
        self.assertEqual(shown(bridge.web_state()), 1)
        self.assertEqual(shown(robot.preference_state(poll={})), 1)
        self.protocol.set_property.assert_called_once()

        self.values[P.SUCTION_LEVEL] = 2  # Dreamehome restores the unchanged vendor cache value.
        for cached in (2, 1, 2):
            self.cached[P.SUCTION_LEVEL] = cached
            bridge.snapshot(robot.observe())
            self.assertEqual(shown(bridge.web_state()), 2)
        self.assertEqual(self.cached[P.SUCTION_LEVEL], 2)
        self.protocol.set_property.assert_called_once()

        bridge.web_preference(payload)
        self.values[P.SUCTION_LEVEL] = 3
        self.assertEqual(shown(robot.observe()), 3)
        self.assertEqual(shown(robot.observe()), 3)
        self.assertEqual(self.cached[P.SUCTION_LEVEL], 2)
        self.cached[P.SUCTION_LEVEL] = 1  # A delayed vendor update carries the earlier write.
        self.assertEqual(shown(robot.observe()), 3)
        self.assertEqual(self.protocol.set_property.call_count, 2)

    @patch("robot.monotonic", return_value=100)
    def test_preference_recheck_keeps_partial_poll_and_backs_off_unavailable_values(self, now):
        robot = self.observing_robot()
        self.device.property_mapping = {**MAPPING, P.VOLUME: {"siid": 99, "piid": 1}}
        self.values[P.CHILD_LOCK] = True
        robot.preference_state(readings={P.SUCTION_LEVEL: 1, P.CHILD_LOCK: 1, P.VOLUME: 55})
        self.values.pop(P.SUCTION_LEVEL)

        def read_partial(query, **kwargs):
            return [row for row in self.read(query, **kwargs) if row["did"] != str(P.SUCTION_LEVEL.value)]

        def readings():
            return {entry["key"]: entry["value"] for entry in robot.observe()["preferences"]}

        self.protocol.get_properties.side_effect = read_partial
        self.assertEqual(readings()["child_lock"], 1)
        self.assertEqual(readings()["suction_level"], 1)
        self.assertNotIn("suction_level", readings())
        queries = [[P(int(item["did"])) for item in call.args[0]] for call in self.protocol.get_properties.call_args_list]
        self.assertEqual(sum(P.SUCTION_LEVEL in props for props in queries), 3)
        self.assertFalse(any(P.VOLUME in props for props in queries))

        self.protocol.get_properties.reset_mock()
        self.assertNotIn("suction_level", readings())
        self.protocol.get_properties.assert_called_once()
        self.values[P.SUCTION_LEVEL] = 3
        now.return_value += 301
        self.protocol.get_properties.side_effect = self.read
        self.assertEqual(readings()["suction_level"], 3)
        self.protocol.set_property.assert_not_called()

    def test_preference_recheck_batches_without_reading_any_property_twice(self):
        robot = self.observing_robot()
        self.cap.cleangenius = False  # The route still shares the bulk register.
        robot.preference_state(readings=self.values)
        robot.observe()
        queries = [[P(int(item["did"])) for item in call.args[0]] for call in self.protocol.get_properties.call_args_list]
        requested = [prop for query in queries for prop in query]
        self.assertEqual(len(queries), 3)
        self.assertEqual(len(requested), len(set(requested)))
        self.assertEqual(set(requested), set(self.values) | {P.BATTERY_LEVEL})
        self.assertEqual(queries[1][0], P.AUTO_SWITCH_SETTINGS)
        self.assertEqual(len(queries[1]), 10)
        self.assertLessEqual(len(queries[2]), 15)

    def test_read_only_state_is_explained_and_auto_charging_uses_wire_two(self):
        self.set_switches(SmartHost=2)
        self.cached.update(self.values)
        for key in ("cleaning_mode", "suction_level", "water_volume", "cleaning_route", "mop_wash_level"):
            self.assertIn("CleanGenius", self.item(key)["disabled_reason"])
        self.assertNotIn("disabled_reason", self.item("auto_water_refilling"))
        self.cap.auto_charging = True
        self.values[P.RESUME_CLEANING] = self.cached[P.RESUME_CLEANING] = 0
        self.assertTrue(self.update("resume_cleaning", 0, 1)["changed"])
        self.protocol.set_property.assert_called_once_with(4, 11, 2, retry_count=0)
