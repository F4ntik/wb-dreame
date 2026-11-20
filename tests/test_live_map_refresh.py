import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from robot import Robot
from vendor.map import DreameMapVacuumMapManager, PIID
from vendor.types import DreameVacuumProperty as P, MapData


class LiveMapRefreshTest(unittest.TestCase):
    def setUp(self):
        self.clock = 1_800_000_000.0
        clock = patch("vendor.map.time", SimpleNamespace(time=lambda: self.clock, monotonic=lambda: self.clock))
        clock.start()
        self.addCleanup(clock.stop)
        self.protocol = Mock(dreame_cloud=True, cloud=SimpleNamespace(connected=True))
        self.protocol.action.return_value = {"code": 0, "out": [{"piid": PIID(P.ROBOT_TIME), "value": 1_799_999_980_000}]}
        self.manager = DreameMapVacuumMapManager(self.protocol)
        self.manager._device_running = True
        self.manager._current_timestamp_ms = int((self.clock - 20) * 1000)
        self.manager._map_data = MapData()
        self.manager._map_data.empty_map = False
        self.manager._map_data.last_updated = self.clock - 20
        self.robot = Robot({}, ".")
        self.robot.device = SimpleNamespace(_map_manager=self.manager)

    def test_tabs_share_one_background_request_and_ack_does_not_schedule_retry(self):
        with patch.object(self.manager, "schedule_update") as schedule:
            for _ in range(10):
                self.robot.note_live_map_demand()
            self.protocol.action.assert_not_called()
            schedule.assert_not_called()
        self.manager.update()
        self.assertEqual(self.protocol.action.call_count, 1)
        siid, aiid, payload, retries = self.protocol.action.call_args.args
        self.assertEqual((siid, aiid, retries), (6, 1, 0))
        self.assertEqual(json.loads(payload[0]["value"]), {"req_type": 1, "frame_type": "I", "force_type": 1})

        self.clock += 3
        self.robot.note_live_map_demand()
        self.manager.update()
        self.assertEqual(self.protocol.action.call_count, 1)
        self.clock += 3
        self.manager.update()
        self.assertEqual(self.protocol.action.call_count, 2)
        self.clock += 10
        self.manager.update()
        self.assertEqual(self.protocol.action.call_count, 2)

    def test_fresh_frame_skips_requests_even_with_active_panel(self):
        self.manager._current_timestamp_ms = int(self.clock * 1000)
        self.robot.note_live_map_demand()
        self.manager.update()
        self.protocol.action.assert_not_called()

    def test_active_panel_bypasses_cached_status_and_a_pending_map_timestamp(self):
        self.manager._device_running = False
        self.protocol.cloud.connected = False
        self.manager._map_request_time = 1_799_999_980_000
        self.manager._need_map_request = True
        self.robot.note_live_map_demand()
        self.manager.update()
        self.protocol.action.assert_called_once()
        payload = self.protocol.action.call_args.args[2]
        self.assertNotIn("time", json.loads(payload[0]["value"]))

        self.clock += 10
        self.manager.update()
        self.assertEqual(self.protocol.action.call_count, 2)
        payload = self.protocol.action.call_args.args[2]
        self.assertEqual(json.loads(payload[0]["value"])["time"], self.manager._map_request_time)

    def test_explicit_new_map_has_priority_and_disconnected_manager_drops_demand(self):
        self.manager._need_new_map = True
        self.robot.note_live_map_demand()
        with patch.object(self.manager, "request_new_map") as request_new_map:
            self.manager.update()
            request_new_map.assert_called_once_with()
        self.protocol.action.assert_not_called()
        self.manager.disconnect()
        self.manager.update()
        self.protocol.action.assert_not_called()

    def test_expired_demand_and_reentrant_updates_do_not_request_another_frame(self):
        self.robot.note_live_map_demand()
        self.clock += 6
        self.manager.update()
        self.protocol.action.assert_not_called()

        def new_poll(*args):
            self.clock += 6
            self.robot.note_live_map_demand()
            self.manager.update()
            return self.protocol.action.return_value

        self.protocol.action.side_effect = new_poll
        self.robot.note_live_map_demand()
        self.manager.update()
        self.protocol.action.assert_called_once()
