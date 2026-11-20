import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from config import Refused
from preferences import WRITE, apply_preference, cleangenius_value, offered, property_write_permitted
from vendor.types import DreameVacuumCleanGenius as G, DreameVacuumDeviceCapability
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as PROPERTY_MAPPING
from vendor.types import DreameVacuumState as S


class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.model = "dreame.vacuum.r9419a"
        self.did = "fixture"
        cloud = SimpleNamespace(device_id=self.did)
        self.protocol = SimpleNamespace(cloud=cloud, device_cloud=cloud, dreame_cloud=True,
                                        get_properties=Mock(side_effect=self.read),
                                        set_property=Mock(side_effect=self.write))
        self.values = {P.STATE: S.CHARGING_COMPLETED.value, P.TASK_STATUS: 0, P.ERROR: 0,
                       P.VOLUME: 20, P.DND_REDUCE_VOLUME: 0, P.DND_DISABLE_AUTO_EMPTY: 1,
                       P.DND: True, P.DND_TASK: json.dumps([{"id": 1, "en": True, "wk": 127,
                                                            "st": "22:00", "et": "08:00", "ss": 0}])}
        self.device = SimpleNamespace(info=SimpleNamespace(model=self.model), device_connected=True,
                                      property_mapping=PROPERTY_MAPPING, _protocol=self.protocol,
                                      status=SimpleNamespace(dnd=True, has_saved_map=False, has_temporary_map=False),
                                      get_property=self.values.get)
        self.device.capability = DreameVacuumDeviceCapability(self.device)
        self.device.capability.dnd_functions = self.device.capability.dnd_task = True
        self.robot = SimpleNamespace(account={"model": self.model, "did": self.did},
                                     device=self.device, identity=Mock())
        self.volume = {"key": "volume", "value": 35, "expected": 20}

    def with_cleangenius(self):
        cap = self.device.capability
        cap.cleangenius = cap.auto_switch_settings = cap.self_wash_base = True
        cap.cleangenius_auto = False
        self.device.status.cleangenius_list = {"off": G.OFF, "routine_cleaning": G.ROUTINE_CLEANING,
                                              "deep_cleaning": G.DEEP_CLEANING}
        self.values.update({P.AUTO_SWITCH_SETTINGS: '[{"k":"SmartHost","v":0},{"k":"Other","v":3}]',
                            P.CHARGING_STATUS: 3, P.SELF_WASH_BASE_STATUS: 0, P.FAULTS: ""})
        return {"key": "cleangenius", "value": 1, "expected": 0}

    def read(self, query, *, retry_count, timeout):
        self.assertEqual((retry_count, timeout), (0, 6))
        return [{**item, "code": 0, "value": self.values.get(P(int(item["did"])))} for item in query]

    def write(self, siid, piid, value, *, retry_count):
        self.assertEqual(retry_count, 0)
        params = {"did": self.did, "siid": siid, "piid": piid, "value": value}
        for method, candidate in (("action", [params]), ("set_properties", [params, params]),
                                  ("set_properties", [dict(params, did="other")]),
                                  ("set_properties", [dict(params, value=True)]),
                                  ("set_properties", [dict(params, piid=piid + 1)]),
                                  ("set_properties", [dict(params, value=value + 1 if type(value) is int else
                                                           '{"k":"Other","v":1}')])):
            self.assertFalse(property_write_permitted(method, candidate))
        self.assertTrue(property_write_permitted("set_properties", [params]))
        self.assertFalse(property_write_permitted("set_properties", [params]))
        prop = next(p for p, mapping in PROPERTY_MAPPING.items() if mapping == {"siid": siid, "piid": piid})
        self.values[prop] = value
        raise TimeoutError  # The second read must happen even when the write reply was lost.

    def test_only_supported_values_are_offered(self):
        self.assertEqual([(item["key"], item["value"]) for item in offered(self.device)],
                         [("volume", 20), ("dnd_reduce_volume", 0), ("dnd_disable_auto_empty", 1)])
        self.device.status.dnd = False
        self.values[P.VOLUME] = "20"
        self.assertEqual(offered(self.device), [])

    def test_disabled_malformed_conflict_and_noop_never_write(self):
        with self.assertRaises(Refused):
            apply_preference(self.robot, self.volume)
        self.protocol.get_properties.assert_not_called()
        for payload in ({**self.volume, "key": "camera"}, {**self.volume, "value": True},
                        {**self.volume, "expected": 21}, {**self.volume, "siid": 7}):
            with self.subTest(payload=payload), self.assertRaises(Refused):
                apply_preference(self.robot, payload, allowed=True)
        self.assertFalse(apply_preference(self.robot, {**self.volume, "value": 20}, allowed=True)["changed"])
        self.protocol.set_property.assert_not_called()

    def test_volume_changes_once_and_requires_readback_even_without_write_reply(self):
        result = apply_preference(self.robot, self.volume, allowed=True)
        self.assertEqual(result, {"key": "volume", "value": 35, "changed": True})
        self.assertEqual(self.protocol.get_properties.call_count, 2)
        self.protocol.set_property.assert_called_once_with(7, 1, 35, retry_count=0)
        self.assertFalse(property_write_permitted("set_properties", [{"did": self.did, "siid": 7, "piid": 1, "value": 35}]))

    def test_dnd_flags_require_fresh_enabled_schedule(self):
        request = {"key": "dnd_reduce_volume", "value": 1, "expected": 0}
        valid = self.values[P.DND_TASK]
        for raw in ("[]", '[{"en":true}]', valid.replace("true", "false"), [valid]):
            self.values[P.DND_TASK] = raw
            with self.subTest(raw=raw), self.assertRaises(Refused):
                apply_preference(self.robot, request, allowed=True)
        self.protocol.set_property.assert_not_called()
        self.values[P.DND_TASK] = valid
        self.assertEqual(apply_preference(self.robot, request, allowed=True)["value"], 1)
        self.protocol.set_property.assert_called_once_with(28, 16, 1, retry_count=0)

    def test_did_change_during_initial_read_and_robot_busy_reject_before_write(self):
        self.values[P.STATE] = S.SWEEPING.value
        with self.assertRaises(Refused):
            apply_preference(self.robot, self.volume, allowed=True)
        self.values[P.STATE] = S.IDLE.value

        def switched(query, **kwargs):
            self.robot.account["did"] = "other"
            return self.read(query, **kwargs)

        self.protocol.get_properties.side_effect = switched
        with self.assertRaises(Refused):
            apply_preference(self.robot, self.volume, allowed=True)
        self.protocol.set_property.assert_not_called()

    def test_inconsistent_readback_returns_refusal_without_retry(self):
        def after_write(siid, piid, value, *, retry_count):
            self.values[P.STATE] = S.SWEEPING.value
            self.write(siid, piid, value, retry_count=retry_count)

        self.protocol.set_property.side_effect = after_write
        with self.assertRaisesRegex(Refused, "Не удалось подтвердить"):
            apply_preference(self.robot, self.volume, allowed=True)
        self.protocol.set_property.assert_called_once()
        self.assertEqual(self.protocol.get_properties.call_count, 2)

    def test_cleangenius_choices_follow_vendor_options_and_fresh_value(self):
        request = self.with_cleangenius()
        self.device.status.cleangenius_list.pop("routine_cleaning")
        current = {P.AUTO_SWITCH_SETTINGS: '{"k":"SmartHost","v":2}'}
        self.assertEqual(offered(self.device, current=current)[-1],
                         {"key": "cleangenius", "label": "CleanGenius", "value": 2, "min": 0, "max": 2,
                          "choices": [{"value": 0, "label": "Выкл."}, {"value": 2, "label": "Глубокая уборка"}]})
        self.assertFalse(any(item["key"] == "cleangenius"
                             for item in offered(self.device, current={P.AUTO_SWITCH_SETTINGS: None})))
        with self.assertRaises(Refused):
            apply_preference(self.robot, request, allowed=True)
        self.device.capability.cleangenius = False
        self.assertFalse(any(item["key"] == "cleangenius" for item in offered(self.device)))
        with self.assertRaises(Refused):
            apply_preference(self.robot, {**request, "value": 2}, allowed=True)
        self.protocol.get_properties.assert_not_called()
        self.protocol.set_property.assert_not_called()

    def test_cleangenius_parses_only_unambiguous_vendor_values(self):
        self.assertEqual(cleangenius_value('[{"k":"Other","v":6},{"k":"SmartHost","v":0}]'), 0)
        self.assertEqual(cleangenius_value('{"k":"SmartHost","v":2}'), 2)
        for raw in (None, "[]", '{"k":"SmartHost","v":true}', '{"k":"SmartHost","v":-1}',
                    '{"k":"SmartHost","v":3}', '{"k":"SmartHost","v":"1"}', '{"k":"SmartHost","v":0,"v":1}',
                    '[{"k":"SmartHost","v":0},{"k":"SmartHost","v":1}]', '[{"k":"SmartHost","v":0},null]'):
            with self.subTest(raw=raw):
                self.assertIsNone(cleangenius_value(raw))

    def test_string_write_token_is_limited_to_known_switches_and_mappings(self):
        for siid, piid, value, allowed in ((4, 50, '{"k":"SmartHost","v":1}', True),
                                          (4, 50, '{"k":"CleanRoute","v":4}', True),
                                          (4, 50, '{"k":"Other","v":1}', False),
                                          (4, 50, '{"k":"SmartHost","v":3}', False),
                                          (7, 1, '{"k":"SmartHost","v":1}', False)):
            with self.subTest(siid=siid, piid=piid, value=value):
                token = WRITE.set((self.did, siid, piid, value))
                try:
                    params = [{"did": self.did, "siid": siid, "piid": piid, "value": value}]
                    self.assertEqual(property_write_permitted("set_properties", params), allowed)
                    self.assertFalse(property_write_permitted("set_properties", params))
                finally:
                    WRITE.reset(token)

    def test_cleangenius_write_uses_only_smart_host_and_confirms_matching_readback(self):
        request = self.with_cleangenius()
        self.values.update({P.STATE: S.DRYING.value, P.CHARGING_STATUS: 1, P.SELF_WASH_BASE_STATUS: 2,
                            P.ERROR: 68, P.FAULTS: "68"})

        def normalized_write(siid, piid, value, **kwargs):
            self.values.update({P.ERROR: 0, P.FAULTS: ""})
            self.write(siid, piid, value, **kwargs)

        self.protocol.set_property.side_effect = normalized_write
        volume_noop = {**self.volume, "value": self.volume["expected"]}
        self.assertFalse(apply_preference(self.robot, volume_noop, allowed=True)["changed"])
        self.assertEqual(apply_preference(self.robot, request, allowed=True),
                         {"key": "cleangenius", "value": 1, "changed": True})
        self.protocol.set_property.assert_called_once_with(4, 50, '{"k":"SmartHost","v":1}', retry_count=0)
        self.assertFalse(property_write_permitted("set_properties",
                         [{"did": self.did, "siid": 4, "piid": 50, "value": '{"k":"SmartHost","v":1}'}]))
        self.assertEqual(self.protocol.get_properties.call_count, 3)

    def test_cleangenius_drying_requires_confirmed_charge_base_task_and_no_faults(self):
        request = self.with_cleangenius()
        drying = {P.STATE: S.DRYING.value, P.CHARGING_STATUS: 1, P.SELF_WASH_BASE_STATUS: 2}
        for prop, value in ((P.CHARGING_STATUS, 2), (P.CHARGING_STATUS, True), (P.CHARGING_STATUS, None),
                            (P.SELF_WASH_BASE_STATUS, 0), (P.SELF_WASH_BASE_STATUS, 1),
                            (P.TASK_STATUS, 1), (P.ERROR, 7), (P.FAULTS, "68,7")):
            self.values.update({**drying, P.TASK_STATUS: 0, P.ERROR: 0, P.FAULTS: "", prop: value})
            with self.subTest(prop=prop, value=value), self.assertRaises(Refused):
                apply_preference(self.robot, request, allowed=True)
        self.protocol.set_property.assert_not_called()

    def test_cleangenius_checks_fresh_expected_value_and_never_retries_an_unconfirmed_write(self):
        request = self.with_cleangenius()
        with self.assertRaisesRegex(Refused, "Значение уже изменилось"):
            apply_preference(self.robot, {**request, "expected": 2}, allowed=True)
        self.assertFalse(apply_preference(self.robot, {**request, "value": 0}, allowed=True)["changed"])
        self.protocol.set_property.assert_not_called()
        self.protocol.set_property.side_effect = TimeoutError
        with self.assertRaisesRegex(Refused, "Не удалось подтвердить"):
            apply_preference(self.robot, request, allowed=True)
        self.protocol.set_property.assert_called_once()
