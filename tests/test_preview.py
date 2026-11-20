import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from bridge import Bridge
from config import DEFAULT, STATE, Refused, atomic, read, validate
from robot import Robot, check_writes_with, discovery, guard, permit
from vendor.device import DreameVacuumDevice
from vendor.types import DreameVacuumProperty as P


class PreviewTest(unittest.TestCase):
    def test_core_offline_import_and_shutdown(self):
        d = DreameVacuumDevice("test", None, None, username="test", password="fixture", country="eu",
                               prefer_cloud=True, account_type="dreame", device_id="fixture")
        try:
            self.assertIsNotNone(d._map_manager)
            with self.assertRaises(Refused):
                d._protocol.action(2, 1)
            with self.assertRaises(Refused):
                d._protocol.action_async(None, 2, 1)
        finally:
            d.disconnect()

    def test_guard_scope(self):
        guard("get_properties", [])
        guard("action", {"siid": 6, "aiid": 1})
        with permit((2, 1)):
            guard("action", {"siid": 2, "aiid": 1})
            with check_writes_with(lambda: False):
                guard("get_properties", [])
                with self.assertRaises(Refused):
                    guard("action", {"siid": 2, "aiid": 1})
        for method, data in (("action", {"siid": 2, "aiid": 1}), ("set_properties", [])):
            with self.assertRaises(Refused):
                guard(method, data)

    @patch("robot.DreameVacuumDreameHomeCloudProtocol")
    def test_discovery_accepts_exact_models_and_reports_only_unsupported_models(self, protocol):
        cloud = protocol.return_value
        cloud.login.return_value = True
        cloud.auth_key = "token-fixture"
        supported = [{"model": "dreame.vacuum." + model, "did": model}
                     for model in ("r2492a", "r2492b", "r2492j", "r9419a")]
        unsupported = {"model": "dreame.vacuum.r9419e", "did": "private-id", "customName": "Private name"}
        records = [unsupported, *supported]
        cloud.get_devices.return_value = {"page": {"records": records}}
        self.assertEqual(discovery("test", "fixture", "eu"), (supported, cloud.auth_key))
        records[:] = [unsupported]
        with self.assertRaises(Refused) as error:
            discovery("test", "fixture", "eu")
        self.assertEqual(str(error.exception), "В аккаунте нет поддерживаемых моделей. Модели в аккаунте — dreame.vacuum.r9419e")
        records.clear()
        with self.assertRaisesRegex(Refused, "нет устройств"):
            discovery("test", "fixture", "eu")

    def test_l40s_pro_ultra_remains_read_only(self):
        robot = Robot({"model": "dreame.vacuum.r9419a"}, ".")
        robot.device = Mock()
        with self.assertRaisesRegex(Refused, "только чтение"):
            robot.send("start", None)
        self.assertEqual(robot.device.mock_calls, [])

    def test_zero_battery_and_absent_battery_differ(self):
        values = {P.STATE: 6, P.TASK_STATUS: 0, P.BATTERY_LEVEL: 0, P.ERROR: 0}
        with TemporaryDirectory() as temp:
            robot = Robot({"model": "dreame.vacuum.r2492a", "did": "robot", "binding": "fixture"}, temp)
            proto = Mock()
            proto.cloud = SimpleNamespace(device_id="robot", auth_key=None)
            device = SimpleNamespace(property_mapping=DreameVacuumDevice.property_mapping, _protocol=proto,
                info=SimpleNamespace(model="dreame.vacuum.r2492a", firmware_version="test"),
                status=SimpleNamespace(current_map=None, selected_map=None))
            robot.device = device
            def sample():
                proto.get_properties.return_value = [
                    {**device.property_mapping[key], "code": 0, "value": value} for key, value in values.items()]
                return robot.observe()
            self.assertEqual(sample()["battery"], 0)
            del values[P.BATTERY_LEVEL]
            missing = sample()
            self.assertIsNone(missing["battery"])
            bridge = Bridge()
            with patch.object(bridge, "pub") as publish:
                bridge.snapshot(missing)
                publish.assert_any_call("/controls/level_battery/meta/error", "r")
                self.assertFalse(any(x.args[0] == "/controls/level_battery" for x in publish.call_args_list))
            del values[P.TASK_STATUS]
            with self.assertRaises(Refused):
                sample()

    def test_mqtt_read_error_preserves_value_and_clears_before_recovery(self):
        bridge = Bridge()
        battery = "/controls/level_battery"
        ready = {"state": "CHARGING", "fw": "test", "battery": 50, "map_id": None, "rooms": {}}
        with patch.object(bridge, "pub") as publish:
            bridge.snapshot(ready)
            publish.assert_any_call(battery, "50")
            publish.reset_mock()
            bridge.offline("Нет ответа Dreamehome")
            self.assertEqual([x for x in publish.call_args_list if x.args[0].startswith(battery)],
                             [call(battery + "/meta/error", "r")])
            self.assertEqual(publish.call_args_list[-2:],
                             [call("/meta/error", "r"), call("/controls/state_connection", "Нет ответа Dreamehome")])
            publish.reset_mock()
            bridge.snapshot({**ready, "battery": 51})
            self.assertEqual([x for x in publish.call_args_list if x.args[0].startswith(battery)],
                             [call(battery + "/meta/error", ""), call(battery, "51")])
            self.assertEqual(publish.call_args_list[-2:],
                             [call("/meta/error", ""), call("/controls/state_connection", "На связи")])

    def test_card_title_matches_binding_on_same_topic(self):
        for model, title in ((None, "Dreame"), ("dreame.vacuum.r2492j", "Dreame L40 Ultra"),
                             ("dreame.vacuum.r9419a", "Dreame L40s Pro Ultra")):
            account = {"model": model} if model else {}
            with (self.subTest(model=model), patch.dict("os.environ", {"CREDENTIALS_DIRECTORY": "."}),
                  patch("bridge.signal.signal"), patch("bridge.start_ui"), patch("bridge.Jobs"),
                  patch("bridge.lock_namespace"), patch("bridge.RetainedSession") as session,
                  patch("bridge.read", side_effect=[DEFAULT, account])):
                bridge = Bridge()
                session.return_value.claim.side_effect = lambda *_: bridge.stop()
                bridge.run()
                session.return_value.claim.assert_called_once_with(STATE, title)

    def test_config_and_private_atomic_file(self):
        self.assertEqual(validate(dict(DEFAULT)), DEFAULT)
        with self.assertRaises(Refused):
            validate({**DEFAULT, "poll_seconds": True})
        with TemporaryDirectory() as temp:
            f = Path(temp) / "account.json"
            atomic(f, {"v": 1})
            atomic(f, {"v": 2})
            self.assertEqual(read(f), {"v": 2})
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
