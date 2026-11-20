import json
import unittest
from datetime import datetime
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from robot import dock_position
from vendor.device import DreameVacuumDevice
from vendor.types import DreameVacuumChargingStatus as Charging


class DisplayStatusTest(unittest.TestCase):
    def test_station_work_and_movement(self):
        self.assertEqual(dock_position("DRYING", Charging.CHARGING), "docked")
        self.assertIsNone(dock_position("DRYING", None))
        self.assertEqual(dock_position("SWEEPING", Charging.CHARGING_COMPLETED), "away")
        self.assertEqual(dock_position("RETURNING_TO_WASH", None), "returning")
        self.assertIsNone(dock_position("IDLE", Charging.NOT_CHARGING))

    def test_history_recovers_even_when_completion_event_was_missed(self):
        status = SimpleNamespace(_cleaning_history=[], _cleaning_history_attrs=None)
        cloud = Mock()
        device = SimpleNamespace(_history_refresh_lock=Lock(), _next_history_refresh=0, status=status,
                                 _protocol=SimpleNamespace(cloud=cloud), property_mapping={}, capability=SimpleNamespace(cleangenius=False))
        cloud.get_device_event.side_effect = [[{"history": "100"}], None, [{"value": "invalid"}, {"history": "200"}, {"history":"100"}]]
        make = lambda value, mapping: SimpleNamespace(date=datetime.fromtimestamp(value), cleanup_method=None)
        with patch("vendor.device.DIID", return_value="2.1"), patch("vendor.device.CleaningHistory", side_effect=make), patch("vendor.device.time.monotonic", side_effect=[0, 20, 301, 365]):
            self.assertTrue(DreameVacuumDevice.refresh_cleaning_records(device))
            self.assertFalse(DreameVacuumDevice.refresh_cleaning_records(device))
            self.assertFalse(DreameVacuumDevice.refresh_cleaning_records(device))
            self.assertEqual(int(status._cleaning_history[0].date.timestamp()), 100)
            self.assertTrue(DreameVacuumDevice.refresh_cleaning_records(device))
            self.assertEqual([int(r.date.timestamp()) for r in status._cleaning_history], [200, 100])
        self.assertEqual(cloud.get_device_event.call_count, 3)
